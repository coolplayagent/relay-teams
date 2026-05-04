# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.agents.orchestration.harnesses.control_harness import (
    TaskControlHarness,
)


class TestTaskControlHarnessImport:
    """Verify that TaskControlHarness is importable from task_execution_service."""

    def test_import_succeeds(self) -> None:
        assert TaskControlHarness is not None

    def test_has_expected_constructor_params(self) -> None:
        import inspect

        sig = inspect.signature(TaskControlHarness.__init__)
        param_names = set(sig.parameters.keys())
        assert "task_repo" in param_names
        assert "agent_repo" in param_names
        assert "run_runtime_repo" in param_names
        assert "event_bus" in param_names

    def test_optional_params_accept_none(self) -> None:
        import inspect

        sig = inspect.signature(TaskControlHarness.__init__)
        assert sig.parameters["wakeup_repo"].default is None
        assert sig.parameters["artifact_repo"].default is None
