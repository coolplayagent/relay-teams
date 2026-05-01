# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from relay_teams.roles.role_contracts import (
    RoleContract,
    RoleContractInvariant,
    RoleContractInvariantType,
    RoleContractPostcondition,
    RoleContractPostconditionType,
    RoleContractPrecondition,
    RoleContractPreconditionType,
    build_role_contract_prompt,
    role_contract_invariant_failures,
)


def test_role_contract_models_normalize_text_and_reject_invalid_refs() -> None:
    precondition = RoleContractPrecondition.model_validate(
        {
            "condition": "dependency_role_completed",
            "description": None,
            "role_ids": [" Crafter "],
        }
    )
    postcondition = RoleContractPostcondition.model_validate(
        {"guarantee": "handoff_present", "description": None}
    )
    invariant = RoleContractInvariant.model_validate(
        {
            "invariant": "must_have_tools",
            "description": None,
            "tools": " edit ",
            "mcp_servers": [" filesystem ", ""],
            "skills": {" time "},
        }
    )
    contract = RoleContract.model_validate({"version": None})

    assert precondition.description == ""
    assert precondition.role_ids == ("Crafter",)
    assert postcondition.description == ""
    assert invariant.description == ""
    assert invariant.tools == ("edit",)
    assert invariant.mcp_servers == ("filesystem",)
    assert invariant.skills == ("time",)
    assert contract.version == "1"

    with pytest.raises(TypeError, match="contract capability refs"):
        RoleContractInvariant.model_validate(
            {"invariant": "must_have_tools", "tools": object()}
        )


def test_role_contract_invariant_failures_cover_all_capability_types() -> None:
    contract = RoleContract(
        invariants=(
            RoleContractInvariant(
                invariant=RoleContractInvariantType.MUST_HAVE_TOOLS,
                tools=("read", "edit"),
            ),
            RoleContractInvariant(
                invariant=RoleContractInvariantType.MUST_NOT_HAVE_TOOLS,
                tools=("shell", "write"),
            ),
            RoleContractInvariant(
                invariant=RoleContractInvariantType.MUST_HAVE_MCP_SERVERS,
                mcp_servers=("filesystem", "database"),
            ),
            RoleContractInvariant(
                invariant=RoleContractInvariantType.MUST_NOT_HAVE_MCP_SERVERS,
                mcp_servers=("browser", "docs"),
            ),
            RoleContractInvariant(
                invariant=RoleContractInvariantType.MUST_HAVE_SKILLS,
                skills=("time", "pdf"),
            ),
            RoleContractInvariant(
                invariant=RoleContractInvariantType.MUST_NOT_HAVE_SKILLS,
                skills=("network", "search"),
            ),
        )
    )

    failures = role_contract_invariant_failures(
        contract=contract,
        tools=("read", "shell"),
        mcp_servers=("filesystem", "browser"),
        skills=("time", "network"),
    )

    assert failures == (
        "must_have_tools: missing tools 'edit'",
        "must_not_have_tools: forbidden tools 'shell' is present",
        "must_have_mcp_servers: missing mcp_servers 'database'",
        "must_not_have_mcp_servers: forbidden mcp_servers 'browser' is present",
        "must_have_skills: missing skills 'pdf'",
        "must_not_have_skills: forbidden skills 'network' is present",
    )


def test_role_contract_invariant_failures_treat_wildcards_as_all_capabilities() -> None:
    contract = RoleContract(
        invariants=(
            RoleContractInvariant(
                invariant=RoleContractInvariantType.MUST_HAVE_TOOLS,
                tools=("edit",),
            ),
            RoleContractInvariant(
                invariant=RoleContractInvariantType.MUST_NOT_HAVE_TOOLS,
                tools=("shell",),
            ),
        )
    )

    assert role_contract_invariant_failures(
        contract=contract,
        tools=("*",),
        mcp_servers=(),
        skills=(),
    ) == ("must_not_have_tools: forbidden tools 'shell' is present",)


def test_build_role_contract_prompt_renders_all_sections() -> None:
    prompt = build_role_contract_prompt(
        RoleContract(
            version="2",
            preconditions=(
                RoleContractPrecondition(
                    condition=RoleContractPreconditionType.DEPENDENCY_ROLE_COMPLETED,
                    description="after craft",
                    role_ids=("Crafter",),
                ),
            ),
            postconditions=(
                RoleContractPostcondition(
                    guarantee=RoleContractPostconditionType.HANDOFF_PRESENT,
                    description="handoff required",
                ),
            ),
            invariants=(
                RoleContractInvariant(
                    invariant=RoleContractInvariantType.MUST_HAVE_TOOLS,
                    description="read access",
                    tools=("read",),
                    mcp_servers=("filesystem",),
                    skills=("time",),
                ),
            ),
        )
    )

    assert prompt.splitlines() == [
        "## Role Contract",
        "- Version: 2",
        "- Preconditions:",
        "  - dependency_role_completed - after craft",
        "    - Role IDs: Crafter",
        "- Postconditions:",
        "  - handoff_present - handoff required",
        "- Invariants:",
        "  - must_have_tools - read access",
        "    - Tools: read",
        "    - MCP Servers: filesystem",
        "    - Skills: time",
    ]
    assert build_role_contract_prompt(RoleContract()) == ""
