# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest
from pydantic import ValidationError

from relay_teams.roles.tool_diet_policy import (
    ToolDietFinding,
    ToolDietPolicy,
    ToolDietSeverity,
)


class TestToolDietPolicyModel:
    def test_default_values(self) -> None:
        policy = ToolDietPolicy()
        assert policy.max_tools_per_role == 10
        assert policy.max_tools_warning_threshold == 7
        assert policy.min_verification_fields == 1
        assert policy.max_objective_length == 500
        assert policy.min_objective_length == 10
        assert len(policy.broad_objective_keywords) > 0

    def test_frozen(self) -> None:
        policy = ToolDietPolicy()
        with pytest.raises(ValidationError):
            policy.max_tools_per_role = 99  # type: ignore[misc]

    def test_custom_values(self) -> None:
        policy = ToolDietPolicy(max_tools_per_role=20, max_tools_warning_threshold=15)
        assert policy.max_tools_per_role == 20
        assert policy.max_tools_warning_threshold == 15

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ToolDietPolicy(unknown_field=42)  # type: ignore[call-arg]


class TestToolDietFinding:
    def test_construction(self) -> None:
        f = ToolDietFinding(
            code="test",
            severity=ToolDietSeverity.WARNING,
            message="test message",
        )
        assert f.code == "test"
        assert f.severity == ToolDietSeverity.WARNING
        assert f.detail == {}

    def test_frozen(self) -> None:
        f = ToolDietFinding(
            code="test",
            severity=ToolDietSeverity.OK,
            message="test",
        )
        with pytest.raises(ValidationError):
            f.code = "other"  # type: ignore[misc]


class TestToolDietSeverity:
    def test_values(self) -> None:
        assert ToolDietSeverity.OK == "ok"
        assert ToolDietSeverity.WARNING == "warning"
        assert ToolDietSeverity.ERROR == "error"
