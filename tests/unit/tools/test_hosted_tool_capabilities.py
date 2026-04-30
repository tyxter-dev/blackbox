from __future__ import annotations

import pytest

from blackbox.core.capabilities import (
    HostedToolSupport,
    ModelCapabilities,
    validate_hosted_tools,
)
from blackbox.core.errors import UnsupportedFeatureError
from blackbox.hosted_tools import HostedToolRaw, Shell, WebSearch


def test_validate_hosted_tools_rejects_provider_without_hosted_support() -> None:
    with pytest.raises(UnsupportedFeatureError):
        validate_hosted_tools(
            ModelCapabilities(supports_hosted_tools=False),
            [WebSearch()],
            provider="test",
        )


def test_validate_hosted_tools_rejects_unsupported_typed_spec() -> None:
    with pytest.raises(UnsupportedFeatureError):
        validate_hosted_tools(
            ModelCapabilities(
                supports_hosted_tools=True,
                hosted_tools={
                    "web_search": HostedToolSupport("web_search", True, True, True, False)
                },
            ),
            [Shell(execution="local")],
            provider="test",
        )


def test_validate_hosted_tools_allows_raw_escape_hatch() -> None:
    validate_hosted_tools(
        ModelCapabilities(supports_hosted_tools=True),
        [HostedToolRaw({"type": "future_tool"})],
        provider="test",
    )
