# ModelProvider Workflow Validation

This plan validates common single-call ModelProvider workflows with runnable
examples instead of assertion-oriented tests.

## Workflows

| Workflow | Example | Success signal |
|---|---|---|
| Classification / option choice | `examples/model_provider_classification.py` | Returns a validated `RequestClassification` object with one intent, one queue, confidence, and rationale. |
| Multi-form structured output | `examples/model_provider_form_fill.py` | Returns a validated nested `FormFillResult` object that can be persisted into several app forms. |
| Knowledge retrieval for drawer assistants | `examples/model_provider_knowledge_drawer.py` | Creates a temporary OpenAI vector store, queries it through `FileSearch`, returns a validated `SupportDrawerAnswer`, and prints hosted-tool/source metadata. |

## How To Run

Install live OpenAI and validation dependencies:

```bash
pip install -e .[openai,validate]
```

Set credentials and, optionally, a model:

```bash
export OPENAI_API_KEY=...
export OPENAI_EXAMPLE_MODEL=gpt-4o-mini
```

PowerShell:

```powershell
$env:OPENAI_API_KEY = "..."
$env:OPENAI_EXAMPLE_MODEL = "gpt-4o-mini"
```

Run each workflow:

```bash
python examples/model_provider_classification.py
python examples/model_provider_form_fill.py
python examples/model_provider_knowledge_drawer.py
```

## Library Support Being Exercised

- `OutputSpec(strategy="provider_native", fallback="posthoc_parse")` gives a
  concise path for provider-native schemas with a runtime fallback.
- `FileSearch(vector_store_ids=[...], include_results=True)` exposes
  provider-hosted retrieval without registering fake local tools.
- `temporary_openai_vector_store(...)` creates a self-contained OpenAI vector
  store fixture from inline documents, waits for ingestion, and cleans up the
  store plus uploaded files.
- Result metadata and provider state expose validation attempts, usage,
  hosted-tool events, and source references for review.

## Review Checklist

- The examples should fail fast with a clear message when `OPENAI_API_KEY` or
  optional dependencies are missing.
- Each example should be readable as application code, not test scaffolding.
- The vector-store example should not require pre-provisioned external files or
  a manually created `OPENAI_VECTOR_STORE_ID`.
- The printed result should be enough for a human or LLM reviewer to judge the
  workflow without stepping through the debugger.
