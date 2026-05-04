# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.roles.tool_diet_policy import ToolDietPolicy, ToolDietSeverity
from relay_teams.roles.tool_diet_validation import (
    has_warnings,
    should_reject,
    validate_tool_diet,
)


def _policy() -> ToolDietPolicy:
    return ToolDietPolicy()


class TestToolDietValidation:
    def test_zero_tools_ok(self) -> None:
        report = validate_tool_diet(
            policy=_policy(),
            tool_count=0,
            objective="Do something specific",
            verification_acceptance_criteria_count=1,
        )
        assert report.tool_count == 0
        assert not report.findings

    def test_warning_threshold(self) -> None:
        report = validate_tool_diet(
            policy=_policy(),
            tool_count=7,
            objective="Do something specific",
        )
        warnings = [
            f for f in report.findings if f.severity == ToolDietSeverity.WARNING
        ]
        assert any(f.code == "tool_count_warning" for f in warnings)

    def test_exceeds_max_is_warning(self) -> None:
        report = validate_tool_diet(
            policy=_policy(),
            tool_count=11,
            objective="Do something specific",
        )
        warnings = [
            f for f in report.findings if f.severity == ToolDietSeverity.WARNING
        ]
        assert any(f.code == "tool_count_exceeded" for f in warnings)
        assert should_reject(report) is False

    def test_broad_objective_warning(self) -> None:
        report = validate_tool_diet(
            policy=_policy(),
            tool_count=3,
            objective="Handle everything for the user",
        )
        warnings = [
            f for f in report.findings if f.severity == ToolDietSeverity.WARNING
        ]
        assert any(f.code == "objective_too_broad" for f in warnings)

    def test_short_objective_warning(self) -> None:
        report = validate_tool_diet(
            policy=_policy(),
            tool_count=3,
            objective="short",
        )
        warnings = [
            f for f in report.findings if f.severity == ToolDietSeverity.WARNING
        ]
        assert any(f.code == "objective_too_short" for f in warnings)

    def test_long_objective_warning(self) -> None:
        report = validate_tool_diet(
            policy=_policy(),
            tool_count=3,
            objective="x" * 600,
        )
        warnings = [
            f for f in report.findings if f.severity == ToolDietSeverity.WARNING
        ]
        assert any(f.code == "objective_too_long" for f in warnings)

    def test_insufficient_verification_warning(self) -> None:
        report = validate_tool_diet(
            policy=_policy(),
            tool_count=3,
            objective="Do something specific with enough detail",
        )
        warnings = [
            f for f in report.findings if f.severity == ToolDietSeverity.WARNING
        ]
        assert any(f.code == "insufficient_verification" for f in warnings)

    def test_multiple_issues_combine(self) -> None:
        report = validate_tool_diet(
            policy=_policy(),
            tool_count=8,
            objective="Do everything",
        )
        codes = {f.code for f in report.findings}
        assert "tool_count_warning" in codes
        assert "objective_too_broad" in codes
        assert "insufficient_verification" in codes

    def test_empty_role_id_no_crash(self) -> None:
        report = validate_tool_diet(
            policy=_policy(),
            tool_count=11,
            objective="x" * 600,
            role_id="",
        )
        warnings = [
            f for f in report.findings if f.severity == ToolDietSeverity.WARNING
        ]
        assert len(warnings) >= 1
        role_findings = [
            f
            for f in report.findings
            if f.code in ("tool_count_exceeded", "objective_too_long")
        ]
        for f in role_findings:
            assert "(unknown)" in f.message

    def test_has_warnings(self) -> None:
        report = validate_tool_diet(
            policy=_policy(),
            tool_count=7,
            objective="Do something specific",
        )
        assert has_warnings(report) is True

    def test_no_warnings(self) -> None:
        report = validate_tool_diet(
            policy=_policy(),
            tool_count=3,
            objective="Do something specific and well-defined for the task",
            verification_acceptance_criteria_count=1,
        )
        assert has_warnings(report) is False
        assert should_reject(report) is False
