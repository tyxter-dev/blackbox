# SKILLS — portable skill packs (`SkillSpec`)

Design for gh #1 ("Add portable skill pack support on top of Toolsets and
WorkspaceAgentSpec"). Scope: the provider-agnostic `SkillSpec` compiler for the
in-house model-runtime path, plus the Claude Code agent-provider staging path
that reuses the `setting_sources` support landed in Slice 34 (gh #2).

Status: **implemented** in Slice 35 for gh #1. The provider-agnostic
`SkillSpec` loader/compiler, `runtime.run(..., skills=[...])` wiring,
workspace-agent bridge, validation checks, and Claude Code staging path are
landed. File references are historical anchors from the design phase; line
numbers in this doc are approximate and reflect the tree at the time of
writing.

---

## 1. The product gap

`blackbox` already has a skill *artifact* but no skill *execution*:

- `SkillBundleRef` (`workspace_agents/spec.py:38`) is just `name + source +
  version + metadata`.
- The package format (`workspace_agents/package.py`) embeds `skills/<name>/`
  bundles on disk and resolves their paths on load.
- But at run time the only thing that happens to a skill is
  `WorkspaceAgentSpec.to_agent_spec()` writing `metadata["skills"] = [name,
  ...]` (`spec.py:119`). **Nothing reads the bundle. `SKILL.md` is never
  parsed. There is no progressive disclosure, no `Skill` tool, no
  compilation.** `runtime.run` has no `skills=` parameter, and
  `run_workspace_agent` (`workspace_agents/runtime.py`) drops skills entirely.

A skill is conceptually a reusable bundle of: instructions, the tools it needs,
the MCP servers it needs, workspace requirements, permissions/policy, example
invocations, and (optionally) an output schema. Today none of that *activates*.

### Two activation paths, one artifact

A staged skill has to be *activated*, and there are two execution paths through
the runtime:

1. **In-house path** (`LocalAgentProvider` / `runtime.run` over model
   adapters): no CLI to delegate to, so `blackbox` must *compile* the bundle
   into primitives it already owns — prompt fragments, local tool refs, hosted
   tools, MCP toolsets, output spec, policy, workspace — and inject them before
   the loop runs.

2. **Claude Code path** (`ClaudeCodeAgentProvider`): the CLI *already has* a
   native skills engine (`.claude/skills/`, the `Skill` tool, progressive
   disclosure). Activation = stage the bundle into the workspace
   `.claude/skills/<name>/` and set `setting_sources=["project"]` (Slice 34).
   The CLI does the rest.

These differ in *who activates the skill*. They share one on-disk format and
one goal, but the hooks are different: `runtime.run`/`runtime.stream` compile
skills into run primitives, while Claude Code staging happens during
agent-session preparation (and through `run_workspace_agent` when a
`WorkspaceAgentSpec` lowers to an agent provider).

---

## 2. The on-disk artifact: `SKILL.md`

A skill bundle is a directory (already the package format's `skills/<name>/`).
Its entry point is `SKILL.md` — the file the example and tests already create
and assert (`examples/diet_coach.py:266`, `tests/.../test_package.py`,
`tests/e2e/.../test_diet_coach_example.py:36`) but which **nothing parses
today**. We formalize it as YAML frontmatter + Markdown body, matching the
Claude Code skill convention so the same bundle works on both paths:

```markdown
---
name: review-pr
description: Review a pull request for correctness and style.
version: 0.2.0
# --- what the skill needs (all optional) ---
tools: [get_diff, post_comment]          # local tool names (must resolve in the registry)
hosted_tools:                            # HostedToolSpec specs (tools/hosted/specs.py)
  - kind: web_search
mcp_servers: [github]                    # package-declared MCP names/refs, or inline specs
workspace: { kind: git }                 # WorkspaceSpec requirement (kind + constraints)
context_flag: review-pr                  # gate for progressive disclosure (see §4)
# --- governance ---
policy: risky_actions                    # "risky_actions" | "allow_all" | policy ref
permissions:                             # ToolPermission-shaped entries
  - { ref: post_comment, scopes: [write], approval: { mode: always } }
# --- output contract (optional) ---
output:
  schema: mypkg.reports.ReviewReport     # dotted import path, or inline JSON Schema
  strategy: finalizer_tool
# --- discovery / docs ---
examples:
  - "Review PR #1421"
  - "Is this diff safe to merge?"
---

Detailed reviewer instructions go here. On the in-house path this body is the
*disclosed* fragment text (shown only when the skill is active — see §4). On the
Claude Code path this whole file is staged verbatim and the CLI's own
progressive disclosure decides when to surface the body.
```

Frontmatter is intentionally a **superset of the Claude Code skill frontmatter**
(which only standardizes `name`/`description` + a few keys). Unknown keys are
preserved into `metadata` so a richer SKILL.md still round-trips.

Supporting files (`scripts/`, `references/`, etc.) live alongside `SKILL.md` in
the bundle and are copied verbatim by the package format and staged verbatim by
the Claude Code stager.

---

## 3. The `SkillSpec` data model

New package `src/blackbox/skills/` (a data + compiler layer, **not** a facade —
mirrors how `workspace_agents` is a schema layer). It gets a package README with
the Belongs Here / Does Not Belong Here / File Map sections per repo convention.

`SkillBundleRef` (`workspace_agents/spec.py`) **stays** as the lightweight
on-disk *reference* (name + source). `SkillSpec` is the *loaded, parsed,
compilable* form. `WorkspaceAgentSpec.skills` keeps holding `SkillBundleRef`s for
backward compatibility; the bridge resolves them to `SkillSpec`s at run time.

```python
@dataclass(slots=True, frozen=True)
class SkillSpec:
    name: str
    description: str = ""
    instructions: str = ""                      # SKILL.md body
    version: str | None = None
    source: str | None = None                   # bundle dir (for supporting files / staging)
    tools: tuple[str, ...] = ()                  # local tool names
    hosted_tools: tuple[HostedToolSpec, ...] = ()
    mcp_servers: tuple[MCPServerSpec | str, ...] = ()  # inline specs or refs/names
    workspace: WorkspaceSpec | None = None       # workspace requirement
    context_flag: str | None = None              # progressive-disclosure gate
    permissions: tuple[ToolPermission, ...] = ()
    policy: str | None = None                    # "risky_actions" | "allow_all" | policy ref
    output: OutputSpec | None = None
    examples: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_directory(cls, path: str | Path) -> "SkillSpec": ...   # reads SKILL.md
    @classmethod
    def from_skill_md(cls, text: str, *, source: str | None = None) -> "SkillSpec": ...
    @classmethod
    def from_bundle_ref(cls, ref: SkillBundleRef) -> "SkillSpec": ... # bridges existing refs

    def to_markdown(self) -> str: ...            # export-as-docs (acceptance criterion)
```

Parsing reuses the optional `pyyaml`/inline parser already in the dependency
posture (frontmatter is small; a tiny in-house parser avoids a new hard dep —
decide in §10).

---

## 4. The compiler

A single function compiles a list of skills into the existing `runtime.run`
primitives. It returns a `SkillExpansion` value object; the runtime merges it
into the run parameters before tool exposure and before
`_resolved_run_spec` / `prompt_composer.build`.

```python
@dataclass(slots=True)
class SkillExpansion:
    local_tools: list[str]                  # registry tool names to add to `tools=`
    hosted_tools: list[HostedToolSpec]
    mcp_toolsets: list[MCPToolset]
    prompt_fragments: list[PromptFragment]  # the disclosed instruction bodies
    context_flags: list[str]
    prompt_mode: PromptMode | None          # usually "tool_aware" when skills exist
    output_spec: OutputSpec | None
    policy: Policy | None                   # merged approval policy
    workspace: WorkspaceSpec | None         # strongest workspace requirement
    tool_permissions: list[ToolPermission]

def compile_skills(
    skills: Sequence[SkillSpec],
    *,
    registry: ToolRegistry,
    mcp_servers: Mapping[str, MCPServerSpec] | None = None,
) -> SkillExpansion: ...
```

### What each `SkillSpec` field compiles into

| SkillSpec field | Target primitive | `runtime.run` channel |
| --- | --- | --- |
| `instructions` | `PromptFragment` (see disclosure below) | per-run `available_prompt_fragments` |
| `tools` | local tool refs resolved against `ToolRegistry` | merged into effective `tools=` before exposure |
| `hosted_tools` | `HostedToolSpec` list | `hosted_tools=` |
| `mcp_servers` | `MCPToolset` per server | merged into `mcp_toolsets` (`main.py:~831`) |
| `output` | `OutputSpec` | `output_spec=` (only if caller didn't set one) |
| `policy` / `permissions` | `Policy` impl | `policy=` (composed with caller policy) |
| `workspace` | `WorkspaceSpec` | `workspace=` (requirement; conflict → error) |
| `context_flag` | flag string | `context_flags=` |
| `examples` | discovery metadata | `metadata` + `to_markdown()` |

### Progressive disclosure on the in-house path

The analog of Claude Code's native disclosure is `PromptFragment` +
`FragmentSelector` (`planning/prompts.py:36,98`). `skills=[...]` means the
listed skills are active for this run, so the compiler adds each skill's
`context_flag` (default `skill:<name>`) to `context_flags` and upgrades the
default prompt mode to `tool_aware` unless the caller explicitly supplied
`PromptSpec(mode="none" | "base")` or `prompt_mode`. For each active skill we
emit:

- A short **always-on** fragment (the skill's `description`) at
  `placement="system"`, unconditional — "the `review-pr` skill is active for
  this run."
- A **disclosed** fragment carrying the full `instructions` body, gated by
  `applies_to=FragmentSelector(context_flags={skill.context_flag})` and/or
  `FragmentSelector(tools=set(skill.tools))`, at `placement="tool_guidance"`.
  It only enters the prompt when the skill's flag is set or its tools are
  active. This gives the same "details appear only when relevant" behavior
  without a CLI, and it is **parity-checked**: `validate_prompt_tool_parity`
  (`planning/prompts.py:540`) ensures a fragment never references a tool absent
  from `plan.effective_tool_ids`.

`conflict_group=f"skill:{name}"` + `priority` lets a caller-provided fragment
override a skill's default guidance. Skill fragments are passed through
`ResolvedRunSpec.available_prompt_fragments` for the current run; they are not
registered into the runtime-global `PromptFragmentRegistry`.

### Hook point (exact)

In `stream`, `_run_single`, and `plan_run` (`runtime/main.py`), consume a new
`skills` kwarg from `RuntimeConfig`, normalize paths/refs/specs into
`SkillSpec`s, and compile before tool exposure and before `_resolved_run_spec`:

```python
skills = _normalize_skills(self._consume_config_value(config_values, "skills", skills))
if skills:
    expansion = compile_skills(
        skills,
        registry=effective_tool_session.registry if effective_tool_session else self.tools.registry,
        mcp_servers=_workspace_agent_mcp_index(...),
    )
    effective_tools.extend(expansion.local_tools)
    mcp_toolsets = [*mcp_toolsets, *expansion.mcp_toolsets]
    effective_hosted_tools = _merge_hosted(effective_hosted_tools, expansion.hosted_tools)
    context_flags  = [*(context_flags or []), *expansion.context_flags]
    policy         = _compose_policies(policy, expansion.policy)
    workspace      = _require_workspace(workspace, expansion.workspace)
    output_spec    = output_spec or expansion.output_spec
    prompt_spec    = _skill_prompt_spec(prompt, prompt_mode, expansion.prompt_mode)
    skill_fragments = expansion.prompt_fragments
```

Then `_resolved_run_spec(..., prompt_fragments=skill_fragments)` appends those
fragments to the plan's `available_prompt_fragments`. This reuses the existing
tool exposure, `mcp_toolsets` loop (`main.py:~831`), output negotiation, and
prompt composition. `skills=` is consumed via `_consume_config_value`, so it
also flows through `RuntimeConfig.overrides` (unknown overrides already survive
into `**kwargs`).

Implementation detail: skill workspace requirements must be merged before a
workspace is opened. Local tool refs can be validated later, after any caller
`toolsets` have been registered into the per-run `ToolSession`, but still
before provider tool exposure. That keeps a skill able to require a workspace
and also refer to tools supplied by the caller for the same run.

---

## 5. Stagers

```python
class SkillStager(Protocol):
    async def prepare(self, skills: Sequence[SkillSpec], *, workspace, provider) -> None: ...
```

### In-house path

The in-house model-runtime path has no stager. It uses `compile_skills(...)`
from §4 directly and keeps everything in process.

### `ClaudeCodeSkillStager` (Claude Code agent-provider path)

The CLI owns disclosure and the `Skill` tool, so this stager does **not** emit
prompt fragments. Instead `prepare()`:

1. Resolves each skill's bundle (`SkillSpec.source` → the `skills/<name>/`
   directory; or materializes `SKILL.md` from `instructions` when source-less).
2. Stages every bundle file into the workspace via
   `WorkspaceProvider.write_file(ws, f".claude/skills/{name}/SKILL.md", ...)`
   (`workspaces/provider.py:81`; parents auto-created by concrete providers)
   plus text supporting files. If a bundle contains binary files, Slice D must
   either reject them with a typed error or add a binary workspace-write
   primitive before claiming support.
3. Ensures `setting_sources` includes `"project"` in the agent
   `permissions`/`task.extra` so the CLI discovers the staged `.claude/`
   (Slice 34, `claude_code.py` `_build_options`). This is the *only* reason
   Slice 34 was a prerequisite.

For a Claude Code session, blackbox still reads enough of each `SkillSpec` to
enforce workspace requirements and policy metadata. The CLI owns the prompt
disclosure and `Skill` tool behavior after staging.

### Stager selection

The model-runtime path uses `compile_skills(...)` directly. Agent-provider
surfaces use a stager selected from the resolved `AgentProvider`: Claude Code
gets `ClaudeCodeSkillStager`; providers without native skill support either get
the compiler path if they lower to `LocalAgentProvider`, or raise
`UnsupportedFeatureError` rather than silently dropping skills.

---

## 6. Public API surface (no new facade)

```python
result = await runtime.run(
    input="Review this pull request",
    skills=[SkillSpec.from_directory("./skills/review_pr")],
    workspace=workspace,
)
```

- `skills=` accepts `SkillSpec`, a bundle path `str`/`Path`, or a
  `SkillBundleRef` (normalized via `from_directory` / `from_bundle_ref`).
- Carries through `RuntimeConfig(overrides={"skills": [...]})` and
  `WorkflowProfile` defaults for free (§4 hook uses `_consume_config_value`).
- `runtime.plan_run(..., skills=[...])` returns a `ResolvedRunSpec` with
  selected/skipped skill fragments in `plan.prompt`; `runtime.prompts.build(...)`
  returns the `PromptBundle` directly for dry-run inspection.
- `run_workspace_agent` (`workspace_agents/runtime.py`) is extended to resolve
  `spec.skills` (`SkillBundleRef` → `SkillSpec`). If `spec.agent_provider` is
  unset, it passes `skills=` through to the model-runtime path; if
  `spec.agent_provider` is Claude Code, it stages `.claude/skills/` before
  starting the agent session. Other agent providers must fail loudly until they
  advertise native skill support.

No new top-level facade is added; `skills` is a kwarg on the existing
high-level path, consistent with the gh #1 constraint.

---

## 7. Permissions enforcement (acceptance criterion)

A skill's `policy`/`permissions` MUST flow through the **existing** policy
layers, not a new one:

- `policy: "risky_actions"` → `RiskyActionApprovalPolicy`
  (`runtime/config.py:196`).
- `permissions: [{ref, scopes, approval}]` → `ToolPermission` declarations.
  A policy adapter returns `require_approval` for matching checkpoints when
  `ApprovalRequirement.requires_approval_for(scope)` says so
  (`workspace_agents/permissions.py`, `PolicyDecision.require_approval()`).
- Multiple skills + a caller policy compose via `_compose_policies` (deny wins,
  then require_approval, then allow).
- MCP skills route through `MCPToolset` → existing `MCPTrustEvaluator` /
  `MCPServerTrustPolicy` (unchanged; a skill can't bypass trust).
- Workspace writes by a skill's tools hit `before_workspace_write` like any
  other — including the Claude Code stager's own staging step, which should be
  exempt (it's runtime-controlled, pre-session) or run under a dedicated
  checkpoint.

This is purely additive wiring into `policy=`/`approval_policy=`; the loop's
approval machinery is untouched.

---

## 8. Validation

Extend `workspace_agents/validation.py` `_check_skills` (`validation.py:301`)
and add SkillSpec-level validation:

- **Preserve the cross-platform source check** (also gh-relevant): the old guard
  `skill.source and Path(skill.source).is_absolute() and not
  Path(skill.source).exists()` silently passed relative sources and
  mis-classified Windows paths on POSIX. The regression is pinned by
  `test_missing_absolute_skill_source_is_warning`. Keep existence checks on
  local path-like sources, including Windows absolute paths, while treating
  URL/registry coordinates as external references rather than false local
  paths.
- New checks on `SkillSpec`: `SKILL.md` present and parseable; `name`
  non-empty and unique; `tools` resolvable in the registry (or deferred);
  `output.schema` importable; `policy`/`permissions` reference real refs.
- Typed `ValidationIssue` codes (e.g. `missing_skill_md`,
  `unparseable_skill_md`, `unresolved_skill_tool`, plus the existing
  `duplicate_skill_name` / `missing_skill_source`).

---

## 9. Evals & deterministic replay (acceptance criterion)

- Compilation is pure and deterministic: same `SkillSpec` → same local tool
  refs, same MCP/hosted tool specs, same fragments, same `prompt_fingerprint`
  (`PromptBundle.prompt_fingerprint`, `planning/prompts.py:213`). Skills
  therefore replay deterministically through the existing observability
  replay/diff path.
- `SkillSpec` is serializable (frontmatter round-trips), so an eval case can
  pin a skill by content hash and the replay reproduces the exact compiled
  prompt + tool surface.
- Skills can be attached to `eval_run`-profile runs unchanged.

---

## 10. Decisions confirmed during implementation

1. **YAML dependency.** The core keeps zero hard dependencies. `SKILL.md`
   frontmatter uses PyYAML when installed and falls back to a small in-house
   parser for the documented subset.
2. **Package home.** `src/blackbox/skills/` is the new data/compiler package.
3. **`SkillBundleRef` vs `SkillSpec` relationship.** Both remain:
   `SkillBundleRef` is the lightweight package pointer, `SkillSpec` is the
   loaded/compiled form.
4. **Claude Code staging exemption.** Staging is runtime-controlled setup:
   `run_workspace_agent` resolves the workspace, writes `.claude/skills/...`,
   and passes a `WorkspaceRef` into the provider run so session execution uses
   the staged project settings.

---

## 11. Suggested slice plan

1. **Slice A — SkillSpec + parser.** `src/blackbox/skills/` package,
   `SkillSpec`, `from_directory`/`from_skill_md`/`from_bundle_ref`,
   `to_markdown`, frontmatter parser, unit tests. No runtime wiring yet.
2. **Slice B — Compiler + in-house path.** `compile_skills`,
   the `runtime.run(skills=[...])` hook, progressive
   disclosure via fragments, `plan_run` inspection. Tests via
   `ScriptedModelProvider`.
3. **Slice C — Permissions + validation.** Policy composition, `_check_skills`
   SkillSpec validation codes, and coverage that preserves the cross-platform
   source classifier.
4. **Slice D — Claude Code stager.** Staging via `write_file` +
   `setting_sources`, stager selection, `run_workspace_agent` skill
   forwarding. Offline tests with the fake SDK + a local workspace.
5. **Slice E — Docs/evals.** FEATURES/VALIDATION/ROADMAP updates, an example
   skill, eval/replay coverage.

Each slice: one commit, three green gates, deferred items logged — per repo
convention.
