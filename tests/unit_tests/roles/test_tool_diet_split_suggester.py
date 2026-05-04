# -*- coding: utf-8 -*-
from __future__ import annotations


from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.tool_diet_policy import ToolDietPolicy
from relay_teams.roles.tool_diet_split_suggester import (
    RoleSplitCandidate,
    TaskSplitSuggestion,
    suggest_task_split,
)
from relay_teams.roles.tool_diet_validation import validate_tool_diet


def _make_role(tools: tuple[str, ...] = ()) -> RoleDefinition:
    return RoleDefinition(
        role_id="test_role",
        name="Test Role",
        description="Test role for tool diet split suggestion tests.",
        version="1.0.0",
        tools=tools,
        system_prompt="You are a test role.",
    )


class TestTaskSplitSuggestion:
    def test_model_construction(self) -> None:
        suggestion = TaskSplitSuggestion(
            original_role_id="r1",
            original_tool_count=12,
            suggested_splits=(
                RoleSplitCandidate(
                    suggested_objective="Read-only",
                    suggested_tools=("read",),
                    rationale="Read tools",
                ),
            ),
            reason="tool_count_exceeded",
        )
        assert suggestion.original_role_id == "r1"

    def test_no_split_when_clean(self) -> None:
        role = _make_role(tools=("read", "write"))
        report = validate_tool_diet(
            policy=ToolDietPolicy(),
            tool_count=2,
            objective="Build a feature with tests.",
        )
        result = suggest_task_split(role=role, report=report)
        assert result is None

    def test_split_suggested_when_findings(self) -> None:
        role = _make_role(tools=tuple(f"tool_{i}" for i in range(12)))
        report = validate_tool_diet(
            policy=ToolDietPolicy(max_tools_per_role=5),
            tool_count=12,
            objective="Short",
        )
        result = suggest_task_split(
            role=role,
            report=report,
            policy_max_tools=5,
        )
        assert result is not None
        assert result.original_tool_count == 12
        assert len(result.suggested_splits) >= 2
