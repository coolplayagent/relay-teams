# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from relay_teams.agents.orchestration.harnesses.control_harness import (
    AuditContext,
    ResolvedPolicyContext,
    TaskControlHarness,
)
from relay_teams.agents.tasks.enums import TaskSpecStrictness
from relay_teams.tools.runtime.guardrails import RuntimeGuardrailPolicy
from relay_teams.tools.runtime.policy import ToolApprovalPolicy


class TestAuditContext:
    def test_construction(self) -> None:
        ctx = AuditContext(
            trace_id="tr_1",
            run_id="run_1",
            task_id="task_1",
            session_id="sess_1",
            instance_id="inst_1",
            role_id="Crafter",
        )
        assert ctx.task_id == "task_1"
        assert ctx.role_id == "Crafter"

    def test_frozen(self) -> None:
        ctx = AuditContext()
        with pytest.raises(Exception):
            ctx.task_id = "other"  # type: ignore[misc]


class TestResolvedPolicyContext:
    def test_construction(self) -> None:
        ctx = ResolvedPolicyContext(
            guardrail_policy=RuntimeGuardrailPolicy(),
            approval_policy=ToolApprovalPolicy(),
            strictness=TaskSpecStrictness.HIGH,
        )
        assert ctx.strictness == TaskSpecStrictness.HIGH

    def test_default_strictness(self) -> None:
        ctx = ResolvedPolicyContext(
            guardrail_policy=RuntimeGuardrailPolicy(),
            approval_policy=ToolApprovalPolicy(),
        )
        assert ctx.strictness == TaskSpecStrictness.MEDIUM


class TestTaskControlHarness:
    def test_constructor(self) -> None:
        from pathlib import Path

        from relay_teams.agents.tasks.task_repository import TaskRepository
        from relay_teams.sessions.runs.event_log import EventLog

        repo = TaskRepository(Path(":memory:"))
        event_log = EventLog(Path(":memory:"))
        harness = TaskControlHarness(
            task_repo=repo,
            agent_repo=None,  # type: ignore[arg-type]
            run_runtime_repo=None,  # type: ignore[arg-type]
            event_bus=event_log,
        )
        assert harness is not None
