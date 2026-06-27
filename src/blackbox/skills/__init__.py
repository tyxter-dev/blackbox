from blackbox.skills.compiler import (
    CompositePolicy,
    SkillPermissionPolicy,
    compile_skills,
    compose_policies,
)
from blackbox.skills.specs import SkillExpansion, SkillInput, SkillSpec
from blackbox.skills.staging import ClaudeCodeSkillStager, SkillStager

__all__ = [
    "ClaudeCodeSkillStager",
    "CompositePolicy",
    "SkillExpansion",
    "SkillInput",
    "SkillPermissionPolicy",
    "SkillSpec",
    "SkillStager",
    "compile_skills",
    "compose_policies",
    "validate_skill_spec",
]


def __getattr__(name: str) -> object:
    if name == "validate_skill_spec":
        from blackbox.skills.validation import validate_skill_spec

        return validate_skill_spec
    raise AttributeError(name)
