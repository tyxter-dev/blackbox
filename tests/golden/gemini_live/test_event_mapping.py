from __future__ import annotations

from agent_runtime.core.events import EventTypes
from agent_runtime.core.media import MediaRef
from agent_runtime.core.state import ProviderState
from agent_runtime.realtime.providers.gemini_live import map_gemini_live_message


def test_gemini_live_maps_core_server_messages() -> None:
    setup = map_gemini_live_message({"setupComplete": {}, "sessionId": "sess_1"})[0]
    text = map_gemini_live_message(
        {
            "serverContent": {
                "responseId": "resp_1",
                "itemId": "item_1",
                "modelTurn": {"parts": [{"text": "hello"}]},
            }
        }
    )[0]
    audio = map_gemini_live_message(
        {
            "serverContent": {
                "responseId": "resp_1",
                "itemId": "item_1",
                "modelTurn": {
                    "parts": [{"inlineData": {"mimeType": "audio/pcm", "data": "YWJj"}}]
                },
            }
        }
    )[0]
    done = map_gemini_live_message(
        {"serverContent": {"turnId": "turn_1", "turnComplete": True}}
    )
    interrupted = map_gemini_live_message(
        {"serverContent": {"responseId": "resp_1", "itemId": "item_1", "interrupted": True}}
    )

    assert setup.type == EventTypes.REALTIME_SESSION_CONNECTED
    assert text.type == EventTypes.REALTIME_OUTPUT_TEXT_DELTA
    assert audio.type == EventTypes.REALTIME_OUTPUT_AUDIO_DELTA
    assert isinstance(audio.data["media"], MediaRef)
    assert [event.type for event in done] == [
        EventTypes.REALTIME_TURN_COMPLETED,
        EventTypes.REALTIME_RESPONSE_COMPLETED,
    ]
    assert [event.type for event in interrupted] == [
        EventTypes.REALTIME_INTERRUPTION_DETECTED,
        EventTypes.REALTIME_RESPONSE_CANCELLED,
    ]


def test_gemini_live_maps_tools_transcripts_usage_resume_and_goaway() -> None:
    tool = map_gemini_live_message(
        {"toolCall": {"functionCalls": [{"id": "call_1", "name": "lookup", "args": {"q": "x"}}]}}
    )[0]
    cancelled = map_gemini_live_message(
        {"toolCallCancellation": {"ids": ["call_1"]}}
    )[0]
    input_transcript = map_gemini_live_message(
        {"inputTranscription": {"text": "hi", "finished": True, "itemId": "in_1"}}
    )[0]
    output_transcript = map_gemini_live_message(
        {"outputTranscription": {"text": "out", "finished": True, "itemId": "out_1"}}
    )[0]
    usage = map_gemini_live_message({"usageMetadata": {"totalTokenCount": 12}})[0]
    resume = map_gemini_live_message(
        {"sessionResumptionUpdate": {"newHandle": "handle_1"}}
    )[0]
    goaway = map_gemini_live_message({"goAway": {"reason": "soon"}})[0]

    assert tool.type == EventTypes.TOOL_CALL_REQUESTED
    assert cancelled.type == EventTypes.TOOL_CALL_FAILED
    assert input_transcript.type == EventTypes.REALTIME_INPUT_TRANSCRIPT_COMPLETED
    assert output_transcript.type == EventTypes.REALTIME_OUTPUT_AUDIO_TRANSCRIPT_DONE
    assert usage.type == EventTypes.REALTIME_USAGE_REPORTED
    assert resume.type == EventTypes.REALTIME_HISTORY_UPDATED
    assert isinstance(resume.data["provider_state"], ProviderState)
    assert goaway.type == EventTypes.REALTIME_TRANSPORT_RECONNECTING
