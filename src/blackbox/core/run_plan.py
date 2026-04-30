"""Compatibility re-exports for run planning contracts.

New code should import from :mod:`blackbox.planning.run_plan`.
"""

from blackbox.planning.run_plan import (
    DataSourceRef,
    DynamicToolLoadingSpec,
    ResolvedHostedTool,
    ResolvedMCPToolset,
    ResolvedRunSpec,
    ResolvedTool,
    resolved_hosted_tools,
)
from blackbox.tools.routing import ResolvedToolPlan

__all__ = [
    "DataSourceRef",
    "DynamicToolLoadingSpec",
    "ResolvedHostedTool",
    "ResolvedMCPToolset",
    "ResolvedRunSpec",
    "ResolvedTool",
    "ResolvedToolPlan",
    "resolved_hosted_tools",
]
