# skills

`skills` owns portable skill-pack loading and compilation. A skill pack is a
directory with a `SKILL.md` entry point plus optional supporting files.

## Belongs Here

- `SkillSpec`, the loaded and parsed form of a skill bundle.
- `SKILL.md` frontmatter/body parsing and deterministic markdown export.
- Compilation of active skills into existing runtime primitives: prompt
  fragments, local tool refs, hosted tools, MCP toolsets, output specs,
  workspace requirements, and approval policies.
- Provider-specific staging helpers for native skill engines such as Claude
  Code.
- Skill-level validation helpers used by workspace-agent validation.

## Does Not Belong Here

- A new `AgentRuntime` facade.
- Tool execution, MCP transport, or workspace backend implementation.
- Provider adapter request/event mapping.
- Application-specific skill content.

## File Map

- `specs.py`: `SkillSpec`, `SkillExpansion`, coercion helpers, and markdown
  import/export.
- `frontmatter.py`: dependency-light `SKILL.md` frontmatter parser and writer.
- `compiler.py`: `compile_skills(...)` and skill policy composition.
- `staging.py`: `ClaudeCodeSkillStager` and staging protocol.
- `validation.py`: typed `ValidationIssue` generation for skill specs.
- `__init__.py`: public package exports.
