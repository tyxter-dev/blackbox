from __future__ import annotations

import asyncio
import inspect
import tempfile
import time
from collections.abc import AsyncIterator, Iterable, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from blackbox.tools.hosted.specs import FileSearch


@dataclass(slots=True, frozen=True)
class OpenAIVectorStoreDocument:
    """Small text document to seed an OpenAI vector store.

    The helper writes each document to a temporary file, uploads it through the
    OpenAI Files API, attaches it to a vector store, and waits until the store is
    searchable by the Responses ``file_search`` tool.
    """

    filename: str
    text: str


@dataclass(slots=True)
class OpenAIVectorStoreHandle:
    """OpenAI vector store plus uploaded file IDs created by integration helpers."""

    id: str
    client: Any = field(repr=False, compare=False)
    name: str | None = None
    file_ids: list[str] = field(default_factory=list)

    async def delete(self, *, delete_files: bool = True) -> None:
        """Delete the vector store and, by default, the uploaded source files."""

        errors: list[str] = []
        try:
            vector_stores = _vector_stores_resource(self.client)
            await _maybe_await(vector_stores.delete(self.id))
        except Exception as exc:  # pragma: no cover - depends on provider SDK/runtime failures.
            errors.append(f"vector store {self.id}: {exc}")

        if delete_files:
            files = getattr(self.client, "files", None)
            delete = getattr(files, "delete", None)
            if delete is None:
                errors.append("files.delete is not available on the OpenAI client")
            else:
                for file_id in self.file_ids:
                    try:
                        await _maybe_await(delete(file_id))
                    except Exception as exc:  # pragma: no cover - provider cleanup best effort.
                        errors.append(f"file {file_id}: {exc}")

        if errors:
            raise RuntimeError(
                "Failed to clean up OpenAI vector store resources: " + "; ".join(errors)
            )


async def create_openai_vector_store(
    *,
    client: Any,
    name: str,
    documents: Mapping[str, str] | Iterable[OpenAIVectorStoreDocument | tuple[str, str]],
    poll_interval: float = 1.0,
    ingestion_timeout: float = 120.0,
) -> OpenAIVectorStoreHandle:
    """Create and populate an OpenAI vector store from in-memory text.

    This is intended for examples, demos, and small support-assistant fixtures
    where requiring pre-provisioned vector-store IDs would make the workflow
    harder to run. For production ingestion, use your app's storage pipeline and
    pass the resulting IDs to :class:`blackbox.FileSearch`.
    """

    normalized_documents = _normalize_documents(documents)
    if not normalized_documents:
        raise ValueError("At least one document is required.")

    vector_stores = _vector_stores_resource(client)
    store = await _maybe_await(vector_stores.create(name=name))
    store_id = _object_id(store, object_name="vector store")
    file_ids: list[str] = []
    handle = OpenAIVectorStoreHandle(
        id=store_id,
        client=client,
        name=_attr(store, "name") or name,
        file_ids=file_ids,
    )

    try:
        with tempfile.TemporaryDirectory(prefix="blackbox-openai-vs-") as temp_dir:
            root = Path(temp_dir)
            for document in normalized_documents:
                filename = _safe_filename(document.filename)
                path = root / filename
                path.write_text(document.text, encoding="utf-8")
                uploaded_file = await _upload_openai_file(client, path)
                file_id = _object_id(uploaded_file, object_name="file")
                file_ids.append(file_id)
                await _attach_file_to_vector_store(
                    vector_stores,
                    vector_store_id=store_id,
                    file_id=file_id,
                    poll_interval=poll_interval,
                    ingestion_timeout=ingestion_timeout,
                )

        await _wait_for_vector_store(
            vector_stores,
            vector_store_id=store_id,
            poll_interval=poll_interval,
            ingestion_timeout=ingestion_timeout,
        )
    except Exception:
        with suppress(Exception):
            await handle.delete(delete_files=True)
        raise
    return handle


@asynccontextmanager
async def temporary_openai_vector_store(
    *,
    client: Any,
    name: str,
    documents: Mapping[str, str] | Iterable[OpenAIVectorStoreDocument | tuple[str, str]],
    poll_interval: float = 1.0,
    ingestion_timeout: float = 120.0,
    delete_files: bool = True,
) -> AsyncIterator[OpenAIVectorStoreHandle]:
    """Yield a populated OpenAI vector store and clean it up on exit."""

    handle = await create_openai_vector_store(
        client=client,
        name=name,
        documents=documents,
        poll_interval=poll_interval,
        ingestion_timeout=ingestion_timeout,
    )
    try:
        yield handle
    finally:
        await handle.delete(delete_files=delete_files)


@asynccontextmanager
async def temporary_openai_file_search(
    *,
    name: str,
    documents: Mapping[str, str] | Iterable[OpenAIVectorStoreDocument | tuple[str, str]],
    client: Any | None = None,
    api_key: str | None = None,
    max_num_results: int | None = None,
    include_results: bool = True,
    poll_interval: float = 1.0,
    ingestion_timeout: float = 120.0,
    delete_files: bool = True,
) -> AsyncIterator[FileSearch]:
    """Yield a ``FileSearch`` spec backed by a temporary OpenAI vector store.

    This is the shortest path for examples and demos that need retrieval over a
    few inline documents. It creates an OpenAI client when one is not supplied,
    uploads the documents, waits for ingestion, yields a ready-to-use
    ``FileSearch`` spec, and cleans up the vector store plus uploaded files.
    """

    resolved_client, owns_client = _openai_client(client=client, api_key=api_key)
    try:
        async with temporary_openai_vector_store(
            client=resolved_client,
            name=name,
            documents=documents,
            poll_interval=poll_interval,
            ingestion_timeout=ingestion_timeout,
            delete_files=delete_files,
        ) as vector_store:
            yield FileSearch(
                vector_store_ids=[vector_store.id],
                max_num_results=max_num_results,
                include_results=include_results,
            )
    finally:
        if owns_client:
            close = getattr(resolved_client, "close", None)
            if callable(close):
                await _maybe_await(close())


def _normalize_documents(
    documents: Mapping[str, str] | Iterable[OpenAIVectorStoreDocument | tuple[str, str]],
) -> list[OpenAIVectorStoreDocument]:
    if isinstance(documents, Mapping):
        return [
            OpenAIVectorStoreDocument(filename=filename, text=text)
            for filename, text in documents.items()
        ]

    normalized: list[OpenAIVectorStoreDocument] = []
    for item in documents:
        if isinstance(item, OpenAIVectorStoreDocument):
            normalized.append(item)
            continue
        filename, text = item
        normalized.append(OpenAIVectorStoreDocument(filename=filename, text=text))
    return normalized


def _safe_filename(filename: str) -> str:
    path = Path(filename)
    if path.name != filename or "/" in filename or "\\" in filename or filename in {"", ".", ".."}:
        raise ValueError(f"OpenAI vector store document filename must be local: {filename!r}")
    return filename


async def _upload_openai_file(client: Any, path: Path) -> Any:
    files = getattr(client, "files", None)
    create = getattr(files, "create", None)
    if create is None:
        raise RuntimeError("files.create is not available on the OpenAI client.")
    with path.open("rb") as file:
        return await _maybe_await(create(file=file, purpose="assistants"))


async def _attach_file_to_vector_store(
    vector_stores: Any,
    *,
    vector_store_id: str,
    file_id: str,
    poll_interval: float,
    ingestion_timeout: float,
) -> None:
    files = getattr(vector_stores, "files", None)
    if files is None:
        raise RuntimeError("vector_stores.files is not available on the OpenAI client.")

    create_and_poll = getattr(files, "create_and_poll", None)
    if create_and_poll is not None:
        await _maybe_await(create_and_poll(vector_store_id=vector_store_id, file_id=file_id))
        return

    create = getattr(files, "create", None)
    if create is None:
        raise RuntimeError("vector_stores.files.create is not available on the OpenAI client.")
    await _maybe_await(create(vector_store_id=vector_store_id, file_id=file_id))
    await _wait_for_vector_store(
        vector_stores,
        vector_store_id=vector_store_id,
        poll_interval=poll_interval,
        ingestion_timeout=ingestion_timeout,
    )


async def _wait_for_vector_store(
    vector_stores: Any,
    *,
    vector_store_id: str,
    poll_interval: float,
    ingestion_timeout: float,
) -> None:
    retrieve = getattr(vector_stores, "retrieve", None)
    if retrieve is None:
        return

    deadline = time.monotonic() + ingestion_timeout
    while True:
        store = await _maybe_await(retrieve(vector_store_id))
        status = _attr(store, "status")
        file_counts = _attr(store, "file_counts")
        in_progress = _int_attr(file_counts, "in_progress")
        failed = _int_attr(file_counts, "failed")
        if status == "completed" and in_progress == 0:
            return
        if status in {"failed", "expired", "cancelled"}:
            raise RuntimeError(f"OpenAI vector store {vector_store_id} ended with status {status}.")
        if failed > 0 and in_progress == 0:
            raise RuntimeError(
                f"OpenAI vector store {vector_store_id} has {failed} failed file(s)."
            )
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Timed out waiting for OpenAI vector store {vector_store_id} to finish ingestion."
            )
        await asyncio.sleep(poll_interval)


def _vector_stores_resource(client: Any) -> Any:
    resource = getattr(client, "vector_stores", None)
    if resource is not None:
        return resource
    beta = getattr(client, "beta", None)
    resource = getattr(beta, "vector_stores", None)
    if resource is not None:
        return resource
    raise RuntimeError("vector_stores is not available on the OpenAI client.")


def _openai_client(*, client: Any | None, api_key: str | None) -> tuple[Any, bool]:
    if client is not None:
        return client, False
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:
        raise RuntimeError(
            "OpenAI vector store helpers require the openai package. "
            "Install with `pip install -e .[openai]`."
        ) from exc
    if api_key is not None:
        return AsyncOpenAI(api_key=api_key), True
    return AsyncOpenAI(), True


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _object_id(value: Any, *, object_name: str) -> str:
    object_id = _attr(value, "id")
    if not isinstance(object_id, str) or not object_id:
        raise RuntimeError(f"OpenAI {object_name} response did not include an id.")
    return object_id


def _int_attr(value: Any, name: str) -> int:
    item = _attr(value, name)
    return item if isinstance(item, int) else 0


def _attr(value: Any, name: str) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)
