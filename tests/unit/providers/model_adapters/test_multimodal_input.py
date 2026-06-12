"""Content-part mapping into provider-native multimodal input."""
from __future__ import annotations

import pytest

from blackbox.core.content import (
    AudioPart,
    ContentItem,
    FilePart,
    ImagePart,
    ProviderNativePart,
    TextPart,
    ToolResultPart,
)
from blackbox.core.errors import UnsupportedFeatureError
from blackbox.core.media import MediaRef
from blackbox.providers.base import TurnRequest
from blackbox.providers.model_adapters._multimodal import (
    content_item_to_anthropic,
    content_item_to_gemini,
    content_item_to_openai_responses,
)
from blackbox.providers.model_adapters.anthropic_messages.provider import _compose_messages
from blackbox.providers.model_adapters.gemini_generate_content.provider import _compose_contents
from blackbox.providers.model_adapters.openai_responses.provider import _coerce_input

URL_IMAGE = ImagePart(media=MediaRef.from_url("https://cdn.test/cat.png", mime_type="image/png"))
B64_IMAGE = ImagePart(media=MediaRef.from_bytes(b"fakebytes", mime_type="image/jpeg"), detail="high")
URL_FILE = FilePart(media=MediaRef.from_url("https://cdn.test/spec.pdf", mime_type="application/pdf"))
B64_FILE = FilePart(
    media=MediaRef.from_bytes(b"%PDF-1.7", mime_type="application/pdf"),
    filename="invoice.pdf",
)
UPLOADED_FILE = FilePart(
    media=MediaRef(source="provider_file", mime_type="application/pdf", provider_file_id="file_abc"),
)


def _item(*parts) -> ContentItem:
    return ContentItem(role="user", parts=list(parts))


# --- OpenAI Responses -------------------------------------------------------

def test_openai_maps_text_image_and_file_parts() -> None:
    message = content_item_to_openai_responses(
        _item(TextPart(text="what is this?"), URL_IMAGE, B64_FILE)
    )
    assert message["role"] == "user"
    text, image, file = message["content"]
    assert text == {"type": "input_text", "text": "what is this?"}
    assert image == {"type": "input_image", "detail": "auto",
                     "image_url": "https://cdn.test/cat.png"}
    assert file["type"] == "input_file"
    assert file["filename"] == "invoice.pdf"
    assert file["file_data"].startswith("data:application/pdf;base64,")


def test_openai_maps_inline_image_to_data_uri_and_file_id() -> None:
    message = content_item_to_openai_responses(_item(B64_IMAGE, UPLOADED_FILE))
    image, file = message["content"]
    assert image["detail"] == "high"
    assert image["image_url"].startswith("data:image/jpeg;base64,")
    assert file == {"type": "input_file", "file_id": "file_abc"}


def test_openai_assistant_text_uses_output_text() -> None:
    message = content_item_to_openai_responses(
        ContentItem(role="assistant", parts=[TextPart(text="earlier reply")])
    )
    assert message["content"] == [{"type": "output_text", "text": "earlier reply"}]


def test_openai_rejects_audio_and_tool_result_parts() -> None:
    with pytest.raises(UnsupportedFeatureError):
        content_item_to_openai_responses(_item(AudioPart.from_bytes(b"x", mime_type="audio/wav")))
    with pytest.raises(UnsupportedFeatureError):
        content_item_to_openai_responses(_item(ToolResultPart(call_id="c", content="r")))


def test_openai_coerce_input_converts_content_items_in_place() -> None:
    converted = _coerce_input([_item(TextPart(text="hi"), URL_IMAGE)])
    assert converted == [{
        "role": "user",
        "content": [
            {"type": "input_text", "text": "hi"},
            {"type": "input_image", "detail": "auto", "image_url": "https://cdn.test/cat.png"},
        ],
    }]


# --- Anthropic Messages -----------------------------------------------------

def test_anthropic_maps_url_and_base64_sources() -> None:
    turn = content_item_to_anthropic(_item(TextPart(text="read this"), URL_IMAGE, B64_FILE))
    text, image, document = turn["content"]
    assert text == {"type": "text", "text": "read this"}
    assert image == {"type": "image", "source": {"type": "url", "url": "https://cdn.test/cat.png"}}
    assert document["type"] == "document"
    assert document["source"]["type"] == "base64"
    assert document["source"]["media_type"] == "application/pdf"


def test_anthropic_maps_provider_file_id_source() -> None:
    turn = content_item_to_anthropic(_item(UPLOADED_FILE))
    assert turn["content"][0]["source"] == {"type": "file", "file_id": "file_abc"}


def test_anthropic_rejects_system_role_items() -> None:
    with pytest.raises(UnsupportedFeatureError):
        content_item_to_anthropic(ContentItem(role="system", parts=[TextPart(text="be brief")]))


def test_anthropic_compose_messages_appends_content_item_turn() -> None:
    request = TurnRequest(model="claude-test", input=[_item(TextPart(text="hi"), URL_IMAGE)])
    messages = _compose_messages(request)
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"][1]["type"] == "image"


# --- Gemini GenerateContent -------------------------------------------------

def test_gemini_maps_inline_and_file_data_parts() -> None:
    entry = content_item_to_gemini(_item(TextPart(text="describe"), B64_IMAGE, URL_FILE))
    assert entry["role"] == "user"
    text, image, file = entry["parts"]
    assert text == {"text": "describe"}
    assert image["inline_data"]["mime_type"] == "image/jpeg"
    assert file == {"file_data": {"file_uri": "https://cdn.test/spec.pdf",
                                  "mime_type": "application/pdf"}}


def test_gemini_maps_audio_and_assistant_role() -> None:
    audio = AudioPart.from_bytes(b"pcmpcm", mime_type="audio/wav")
    entry = content_item_to_gemini(_item(audio))
    assert entry["parts"][0]["inline_data"]["mime_type"] == "audio/wav"
    model_entry = content_item_to_gemini(
        ContentItem(role="assistant", parts=[TextPart(text="earlier")])
    )
    assert model_entry == {"role": "model", "parts": [{"text": "earlier"}]}


def test_gemini_compose_contents_appends_content_item_entry() -> None:
    request = TurnRequest(model="gemini-test", input=[_item(TextPart(text="hi"), B64_IMAGE)])
    contents = _compose_contents(request)
    assert contents[-1]["role"] == "user"
    assert "inline_data" in contents[-1]["parts"][1]


# --- shared media resolution ------------------------------------------------

def test_artifact_only_media_raises_for_all_providers() -> None:
    artifact_image = ImagePart(
        media=MediaRef.from_artifact("artifact_9", mime_type="image/png")
    )
    for mapper in (
        content_item_to_openai_responses,
        content_item_to_anthropic,
        content_item_to_gemini,
    ):
        with pytest.raises(UnsupportedFeatureError, match="artifact"):
            mapper(_item(artifact_image))


def test_provider_native_part_passes_through_everywhere() -> None:
    native = ProviderNativePart(provider="any", value={"type": "custom", "x": 1})
    assert content_item_to_openai_responses(_item(native))["content"] == [{"type": "custom", "x": 1}]
    assert content_item_to_anthropic(_item(native))["content"] == [{"type": "custom", "x": 1}]
    assert content_item_to_gemini(_item(native))["parts"] == [{"type": "custom", "x": 1}]
