# 09 — Feature: Approvals & Policy

**Contracts:** `Policy` Protocol, `ApprovalDecision`, checkpoint constants ·
**Built-in:** `RiskyActionApprovalPolicy`

## Summary

Approvals are central to the runtime, not a callback afterthought. The runtime
emits an approval event, marks the session `waiting`, accepts a decision, and
resumes — for dangerous local tools, cloud-agent tool confirmation, MCP tool
confirmation, workspace writes, command execution, deployment actions, and
data-exfiltration-sensitive operations.

## Approval flow

```text
provider/runtime emits approval.requested
session status becomes waiting when needed
application returns ApprovalDecision
runtime/provider resumes or denies the action
approval.approved or approval.denied is emitted
```

```python
await runtime.agents.approve(
    provider="claude-code",
    approval_id="approval_123",
    decision=ApprovalDecision(allow=True, reason="Safe to run tests."),
)
```

## ApprovalDecision

Supports: `allow`, `deny`, optional `reason`, and optional modified arguments or
policy metadata.

## Policy protocol and checkpoints

A minimal `Policy` Protocol with checkpoint constants gates the loop. The legacy
`approval_policy` callable is wrapped as one implementation; declarative rule
engines stay P2. Checkpoints include:

- `before_tool_call`
- `before_command`
- `before_workspace_write`
- `before_artifact_export`
- `before_mcp_call`
- `before_final_output`
- `before_scheduled_run` (workspace-agent schedules — see [08](08-workspace-agents.md))
- `before_work_claim` (inbound environment work — see [14](14-environment-workers.md))

At each checkpoint a policy may **allow**, **deny**, or **require approval**. The
built-in `RiskyActionApprovalPolicy` maps the `coding_agent` profile's
`approval_policy="risky_actions"` setting onto these gates.

## Profiles

`coding_agent` sets `approval_policy="risky_actions"` by default (see
[13](13-configuration.md)). Pluggable policies range from simple callbacks to the
future declarative engine.

## Requirements

| ID | Priority | Requirement |
|---|---|---|
| P0-R16 | P0 | A minimal `Policy` Protocol with checkpoint constants exists; the legacy `approval_policy` callable is wrapped as one implementation. |
| P1-R8 | P1 | Approval events can pause and resume local or provider sessions. |
| P2-R8 | P2 | Declarative permission/approval/data-access policy engine on top of the P0 `Policy` Protocol. |

## Hard constraints

- Approvals are part of the runtime, not an opaque callback.
- Centralize the approval event and decision types — don't fork per-provider
  approval shapes.
- Errors raise `ApprovalError` (under `AgentRuntimeError`).

## Status & references

`Policy` Protocol, checkpoints, `ApprovalDecision`, and `RiskyActionApprovalPolicy`
shipped. MCP approval pausing is wired through the high-level loop. **Pending
(Horizon 1):** approval-channel integration at workspace checkpoints — wire
`before_command` / `before_workspace_write` to the approval event/decision flow
the way MCP approvals already are. PRD §10.6, §12 (P0-R16/P1-R8/P2-R8), §17.

→ Next: [10 — Structured output](10-structured-output.md)
