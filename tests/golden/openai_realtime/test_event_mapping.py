from __future__ import annotations

from blackbox.core.events import EventTypes
from blackbox.core.media import MediaRef
from blackbox.core.raw import RawEnvelope
from blackbox.realtime.providers.openai_realtime import map_openai_realtime_event


def test_openai_realtime_maps_text_audio_tool_and_completion_events() -> None:
    text = map_openai_realtime_event(
        {
            "type": "response.output_text.delta",
            "response_id": "resp_1",
            "item_id": "item_1",
            "delta": "hi",
        }
    )[0]
    audio = map_openai_realtime_event(
        {
            "type": "response.output_audio.delta",
            "response_id": "resp_1",
            "item_id": "item_1",
            "delta": "YWJj",
        }
    )[0]
    args = map_openai_realtime_event(
        {
            "type": "response.function_call_arguments.delta",
            "response_id": "resp_1",
            "item_id": "call_item",
            "call_id": "call_1",
            "delta": "{\"q\"",
        }
    )[0]
    done = map_openai_realtime_event(
        {
            "type": "response.done",
            "response": {
                "id": "resp_1",
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "id": "item_call",
                        "call_id": "call_1",
                        "name": "lookup",
                        "arguments": "{\"q\":\"x\"}",
                    }
                ],
            },
        }
    )

    assert text.type == EventTypes.REALTIME_OUTPUT_TEXT_DELTA
    assert text.data["delta"] == "hi"
    assert isinstance(text.raw, RawEnvelope)
    assert audio.type == EventTypes.REALTIME_OUTPUT_AUDIO_DELTA
    assert isinstance(audio.data["media"], MediaRef)
    assert audio.data["media"].bytes_count == 3
    assert args.type == EventTypes.REALTIME_TOOL_ARGUMENTS_DELTA
    assert [event.type for event in done] == [
        EventTypes.TOOL_CALL_REQUESTED,
        EventTypes.REALTIME_RESPONSE_COMPLETED,
    ]
    assert done[0].data["arguments"] == {"q": "x"}


def test_openai_realtime_maps_session_audio_and_error_events() -> None:
    mapped = [
        map_openai_realtime_event({"type": "session.created", "session": {"id": "sess_1"}})[0],
        map_openai_realtime_event({"type": "session.updated", "session": {"voice": "alloy"}})[0],
        map_openai_realtime_event(
            {"type": "input_audio_buffer.speech_started", "audio_start_ms": 10}
        )[0],
        map_openai_realtime_event(
            {"type": "input_audio_buffer.speech_stopped", "audio_end_ms": 20}
        )[0],
        map_openai_realtime_event(
            {"type": "input_audio_buffer.committed", "item_id": "item_audio"}
        )[0],
        map_openai_realtime_event(
            {
                "type": "conversation.item.truncated",
                "item_id": "item_1",
                "content_index": 0,
                "audio_end_ms": 100,
            }
        )[0],
        map_openai_realtime_event({"type": "error", "error": {"message": "bad"}})[0],
    ]

    assert [event.type for event in mapped] == [
        EventTypes.REALTIME_SESSION_CONNECTED,
        EventTypes.REALTIME_SESSION_UPDATED,
        EventTypes.REALTIME_INPUT_SPEECH_STARTED,
        EventTypes.REALTIME_INPUT_SPEECH_STOPPED,
        EventTypes.REALTIME_INPUT_AUDIO_COMMITTED,
        EventTypes.REALTIME_OUTPUT_TRUNCATED,
        EventTypes.REALTIME_SESSION_FAILED,
    ]
