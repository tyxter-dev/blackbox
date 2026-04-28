"""Compatibility re-exports for the runtime agent loop.

New code should import from :mod:`agent_runtime.runtime.agent_loop`.
"""

from agent_runtime.runtime.agent_loop import AgentLoop, ApprovalPolicy

__all__ = ["AgentLoop", "ApprovalPolicy"]
