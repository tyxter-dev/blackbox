from __future__ import annotations

from typing import Literal

TransportKind = Literal["websocket", "webrtc", "sip", "provider_sdk"]
ToolMode = Literal["manual", "auto", "disabled"]
