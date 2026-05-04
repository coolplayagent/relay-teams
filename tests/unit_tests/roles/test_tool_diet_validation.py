# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.roles.tool_diet_validation import (
    should_reject,
    validate_tool_diet,
)
from relay_teams.roles.tool_diet_policy import (
    ToolDietFinding,
    ToolDietPolicy,
    ToolDietReport,
    ToolDietSeverity,
)


class TestToolDietValidation:
    def test_validate_empty_role_passes(self) -> None:
        report = validate_tool_diet(
            policy=ToolDietPolicy(),
            tool_count=0,
            objective="test",
            role_id="test-role",
        )
        assert isinstance(report, ToolDietReport)

    def test_should_reject_empty_report(self) -> None:
        report = ToolDietReport(
            tool_count=0,
            max_tools=10,
            objective_length=10,
            findings=(),
        )
        assert should_reject(report) is False

    def test_should_reject_with_error_finding(self) -> None:
        report = ToolDietReport(
            tool_count=15,
            max_tools=10,
            objective_length=50,
            findings=(
                ToolDietFinding(
                    code="tool_count_exceeded",
                    severity=ToolDietSeverity.ERROR,
                    message="Too many tools",
                ),
            ),
        )
        assert should_reject(report) is True

    def test_should_not_reject_with_only_warnings(self) -> None:
        report = ToolDietReport(
            tool_count=8,
            max_tools=10,
            objective_length=50,
            findings=(
                ToolDietFinding(
                    code="tool_count_warning",
                    severity=ToolDietSeverity.WARNING,
                    message="Approaching limit",
                ),
            ),
        )
        assert should_reject(report) is False
