from __future__ import annotations

from agent_runtime.core.events import EventTypes


def test_realtime_event_types_are_registered() -> None:
    expected = {
        "REALTIME_SESSION_CONNECTING": "realtime.session.connecting",
        "REALTIME_SESSION_CONNECTED": "realtime.session.connected",
        "REALTIME_SESSION_UPDATED": "realtime.session.updated",
        "REALTIME_SESSION_CLOSED": "realtime.session.closed",
        "REALTIME_SESSION_FAILED": "realtime.session.failed",
        "REALTIME_TRANSPORT_RECONNECTING": "realtime.transport.reconnecting",
        "REALTIME_TRANSPORT_RECONNECTED": "realtime.transport.reconnected",
        "REALTIME_INPUT_ITEM_CREATED": "realtime.input.item.created",
        "REALTIME_INPUT_TEXT_SUBMITTED": "realtime.input.text.submitted",
        "REALTIME_INPUT_AUDIO_DELTA": "realtime.input.audio.delta",
        "REALTIME_INPUT_AUDIO_COMMITTED": "realtime.input.audio.committed",
        "REALTIME_INPUT_SPEECH_STARTED": "realtime.input.speech.started",
        "REALTIME_INPUT_SPEECH_STOPPED": "realtime.input.speech.stopped",
        "REALTIME_INPUT_TRANSCRIPT_DELTA": "realtime.input.transcript.delta",
        "REALTIME_INPUT_TRANSCRIPT_COMPLETED": "realtime.input.transcript.completed",
        "REALTIME_INPUT_IMAGE_ADDED": "realtime.input.image.added",
        "REALTIME_INPUT_VIDEO_FRAME_ADDED": "realtime.input.video_frame.added",
        "REALTIME_RESPONSE_CREATED": "realtime.response.created",
        "REALTIME_RESPONSE_COMPLETED": "realtime.response.completed",
        "REALTIME_RESPONSE_CANCELLED": "realtime.response.cancelled",
        "REALTIME_RESPONSE_FAILED": "realtime.response.failed",
        "REALTIME_OUTPUT_ITEM_CREATED": "realtime.output.item.created",
        "REALTIME_OUTPUT_ITEM_COMPLETED": "realtime.output.item.completed",
        "REALTIME_OUTPUT_TEXT_DELTA": "realtime.output.text.delta",
        "REALTIME_OUTPUT_TEXT_DONE": "realtime.output.text.done",
        "REALTIME_OUTPUT_AUDIO_DELTA": "realtime.output.audio.delta",
        "REALTIME_OUTPUT_AUDIO_DONE": "realtime.output.audio.done",
        "REALTIME_OUTPUT_AUDIO_TRANSCRIPT_DELTA": "realtime.output.audio_transcript.delta",
        "REALTIME_OUTPUT_AUDIO_TRANSCRIPT_DONE": "realtime.output.audio_transcript.done",
        "REALTIME_TURN_STARTED": "realtime.turn.started",
        "REALTIME_TURN_COMPLETED": "realtime.turn.completed",
        "REALTIME_INTERRUPTION_DETECTED": "realtime.interruption.detected",
        "REALTIME_OUTPUT_TRUNCATED": "realtime.output.truncated",
        "REALTIME_HISTORY_UPDATED": "realtime.history.updated",
        "REALTIME_USAGE_REPORTED": "realtime.usage.reported",
        "REALTIME_TOOL_ARGUMENTS_DELTA": "realtime.tool.arguments.delta",
    }
    for name, value in expected.items():
        assert getattr(EventTypes, name) == value
