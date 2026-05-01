# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from relay_teams.validation import normalize_identifier_tuple


def _normalize_text_tuple(value: object, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = (value,)
    elif isinstance(value, (list, tuple, set)):
        items = tuple(value)
    else:
        raise TypeError(f"{field_name} must be a string or sequence of strings")
    normalized: list[str] = []
    for item in items:
        text = str(item).strip()
        if text:
            normalized.append(text)
    return tuple(normalized)


class RoleContractPreconditionType(str, Enum):
    TASK_HAS_SPEC = "task_has_spec"
    TASK_HAS_ACCEPTANCE_CRITERIA = "task_has_acceptance_criteria"
    DEPENDENCIES_COMPLETED = "dependencies_completed"
    DEPENDENCY_ROLE_COMPLETED = "dependency_role_completed"


class RoleContractPostconditionType(str, Enum):
    VERIFICATION_COMMANDS_CONFIGURED = "verification_commands_configured"
    RESULT_MENTIONS_ACCEPTANCE_CRITERIA = "result_mentions_acceptance_criteria"
    RESULT_MENTIONS_EVIDENCE_EXPECTATIONS = "result_mentions_evidence_expectations"
    HANDOFF_PRESENT = "handoff_present"


class RoleContractInvariantType(str, Enum):
    MUST_HAVE_TOOLS = "must_have_tools"
    MUST_NOT_HAVE_TOOLS = "must_not_have_tools"
    MUST_HAVE_MCP_SERVERS = "must_have_mcp_servers"
    MUST_NOT_HAVE_MCP_SERVERS = "must_not_have_mcp_servers"
    MUST_HAVE_SKILLS = "must_have_skills"
    MUST_NOT_HAVE_SKILLS = "must_not_have_skills"


class RoleContractPrecondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition: RoleContractPreconditionType
    description: str = ""
    role_ids: tuple[str, ...] = ()

    @field_validator("description", mode="before")
    @classmethod
    def _normalize_description(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("role_ids", mode="before")
    @classmethod
    def _normalize_role_ids(cls, value: object) -> tuple[str, ...]:
        return normalize_identifier_tuple(value, field_name="role_ids") or ()


class RoleContractPostcondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    guarantee: RoleContractPostconditionType
    description: str = ""

    @field_validator("description", mode="before")
    @classmethod
    def _normalize_description(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()


class RoleContractInvariant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invariant: RoleContractInvariantType
    description: str = ""
    tools: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()

    @field_validator("description", mode="before")
    @classmethod
    def _normalize_description(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("tools", "mcp_servers", "skills", mode="before")
    @classmethod
    def _normalize_capability_refs(cls, value: object) -> tuple[str, ...]:
        return _normalize_text_tuple(value, field_name="contract capability refs")


class RoleContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(default="1", min_length=1)
    preconditions: tuple[RoleContractPrecondition, ...] = ()
    postconditions: tuple[RoleContractPostcondition, ...] = ()
    invariants: tuple[RoleContractInvariant, ...] = ()

    @field_validator("version", mode="before")
    @classmethod
    def _normalize_version(cls, value: object) -> str:
        if value is None:
            return "1"
        normalized = str(value).strip()
        return normalized or "1"


def is_empty_role_contract(contract: RoleContract) -> bool:
    return contract == RoleContract()


def role_contract_invariant_failures(
    *,
    contract: RoleContract,
    tools: tuple[str, ...],
    mcp_servers: tuple[str, ...],
    skills: tuple[str, ...],
) -> tuple[str, ...]:
    failures: list[str] = []
    tool_set = set(tools)
    mcp_server_set = set(mcp_servers)
    skill_set = set(skills)
    for invariant in contract.invariants:
        if invariant.invariant == RoleContractInvariantType.MUST_HAVE_TOOLS:
            failures.extend(
                _missing_reference_failures(
                    label="tools",
                    values=invariant.tools,
                    available=tool_set,
                    invariant=invariant.invariant.value,
                )
            )
        elif invariant.invariant == RoleContractInvariantType.MUST_NOT_HAVE_TOOLS:
            failures.extend(
                _forbidden_reference_failures(
                    label="tools",
                    values=invariant.tools,
                    available=tool_set,
                    invariant=invariant.invariant.value,
                )
            )
        elif invariant.invariant == RoleContractInvariantType.MUST_HAVE_MCP_SERVERS:
            failures.extend(
                _missing_reference_failures(
                    label="mcp_servers",
                    values=invariant.mcp_servers,
                    available=mcp_server_set,
                    invariant=invariant.invariant.value,
                )
            )
        elif invariant.invariant == RoleContractInvariantType.MUST_NOT_HAVE_MCP_SERVERS:
            failures.extend(
                _forbidden_reference_failures(
                    label="mcp_servers",
                    values=invariant.mcp_servers,
                    available=mcp_server_set,
                    invariant=invariant.invariant.value,
                )
            )
        elif invariant.invariant == RoleContractInvariantType.MUST_HAVE_SKILLS:
            failures.extend(
                _missing_reference_failures(
                    label="skills",
                    values=invariant.skills,
                    available=skill_set,
                    invariant=invariant.invariant.value,
                )
            )
        elif invariant.invariant == RoleContractInvariantType.MUST_NOT_HAVE_SKILLS:
            failures.extend(
                _forbidden_reference_failures(
                    label="skills",
                    values=invariant.skills,
                    available=skill_set,
                    invariant=invariant.invariant.value,
                )
            )
    return tuple(failures)


def build_role_contract_prompt(contract: RoleContract) -> str:
    if is_empty_role_contract(contract):
        return ""
    lines = ["## Role Contract", f"- Version: {contract.version}"]
    if contract.preconditions:
        lines.append("- Preconditions:")
        for precondition in contract.preconditions:
            suffix = _format_contract_description(precondition.description)
            lines.append(f"  - {precondition.condition.value}{suffix}")
            if precondition.role_ids:
                lines.append(f"    - Role IDs: {', '.join(precondition.role_ids)}")
    if contract.postconditions:
        lines.append("- Postconditions:")
        for postcondition in contract.postconditions:
            suffix = _format_contract_description(postcondition.description)
            lines.append(f"  - {postcondition.guarantee.value}{suffix}")
    if contract.invariants:
        lines.append("- Invariants:")
        for invariant in contract.invariants:
            suffix = _format_contract_description(invariant.description)
            lines.append(f"  - {invariant.invariant.value}{suffix}")
            if invariant.tools:
                lines.append(f"    - Tools: {', '.join(invariant.tools)}")
            if invariant.mcp_servers:
                lines.append(f"    - MCP Servers: {', '.join(invariant.mcp_servers)}")
            if invariant.skills:
                lines.append(f"    - Skills: {', '.join(invariant.skills)}")
    return "\n".join(lines)


def _missing_reference_failures(
    *,
    label: str,
    values: tuple[str, ...],
    available: set[str],
    invariant: str,
) -> tuple[str, ...]:
    if "*" in available:
        return ()
    return tuple(
        f"{invariant}: missing {label} '{value}'"
        for value in values
        if value not in available
    )


def _forbidden_reference_failures(
    *,
    label: str,
    values: tuple[str, ...],
    available: set[str],
    invariant: str,
) -> tuple[str, ...]:
    if "*" in available:
        return tuple(
            f"{invariant}: forbidden {label} '{value}' is present" for value in values
        )
    return tuple(
        f"{invariant}: forbidden {label} '{value}' is present"
        for value in values
        if value in available
    )


def _format_contract_description(description: str) -> str:
    if not description:
        return ""
    return f" - {description}"
