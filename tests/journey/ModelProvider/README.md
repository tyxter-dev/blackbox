# ModelProvider Journeys

These tests exercise the `ModelProvider` framework as user-facing journeys.
They intentionally avoid deterministic assertions against model text. Each
journey makes real provider calls when credentials are available and prints a
structured report for LLM review.

Run with:

```bash
pytest -m journey_model_provider -s tests/journey/ModelProvider
```

## Covered Journeys

The suite covers these user-facing goals:

- `direct_model_turn`: a user makes one direct model call and receives text plus provider state.
- `streaming_response_for_live_ui`: a user streams deltas into a live UI while still receiving final usage, metadata, and provider state.
- `provider_state_continuation_for_follow_up_context`: a user continues a provider-native conversation without manually replaying all prior context.
- `chat_compatibility_for_existing_chat_app`: a user with an existing chat-message-shaped app can call the ModelProvider layer.
- `high_level_runtime_local_tool_loop`: a user exposes local Python tools and lets `AgentRuntime` call tools, feed results back, and produce a final answer.
- `dynamic_tool_session_for_one_off_app_tool`: a user registers a temporary tool for one run without leaving it in the global registry.
- `provider_native_structured_output_for_supported_providers`: a user asks providers with native support to return validated structured output.
- `posthoc_structured_output_with_retry`: a user asks for structured output through the runtime validation and retry path.
- `request_controls_usage_and_cost_metadata`: a user sets provider request controls and receives normalized usage and estimated cost metadata.
- `hosted_web_search_recent_stock_news`: a user builds a news app that uses provider-integrated web search to return recent stock news.
- `openai_file_search_private_knowledge_base`: a user connects a private OpenAI vector store and asks file search to ground an answer.
- `provider_native_remote_mcp_server`: a user connects a provider-native remote MCP server and expects MCP events and result metadata.
- `openai_tool_search_catalog`: a user exposes a searchable provider-side tool catalog and lets OpenAI select relevant tools.
- `openai_code_interpreter_analysis`: a user delegates computation to OpenAI hosted code interpreter.
- `openai_image_generation_artifact`: a user asks OpenAI hosted image generation to produce an artifact and report artifact metadata.

Provider credentials are read from the standard environment variables:
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` or `GEMINI_API_KEY`,
and `XAI_API_KEY`.

Optional hosted-tool journeys require extra inputs:

- `OPENAI_VECTOR_STORE_ID` for OpenAI file search.
- `JOURNEY_MCP_SERVER_URL` for provider-native remote MCP.
- `RUN_OPENAI_CODE_INTERPRETER_JOURNEY=1` for OpenAI code interpreter.
- `RUN_OPENAI_IMAGE_GENERATION_JOURNEY=1` for OpenAI image generation.
- `RUN_OPENAI_TOOL_SEARCH_JOURNEY=1` for OpenAI tool search.

Use `MODEL_PROVIDER_JOURNEY_PROVIDERS=openai,google` to limit the provider set.
