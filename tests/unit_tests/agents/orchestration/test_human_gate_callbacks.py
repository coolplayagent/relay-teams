# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import MagicMock


from relay_teams.agents.orchestration.human_gate import GateManager


class TestGateManagerCallbacks:
    def test_resolve_gate_fires_callback(self) -> None:
        gm = GateManager()
        gm.open_gate(
            run_id="r-1",
            task_id="t-1",
            instance_id="i-1",
            role_id="role-1",
            summary="needs approval",
        )
        cb = MagicMock()
        gm.add_resolved_callback(cb)
        gm.resolve_gate(
            run_id="r-1",
            task_id="t-1",
            action="approve",
            feedback="looks good",
        )
        cb.assert_called_once_with("r-1", "t-1", "approve", "looks good")

    def test_resolve_gate_no_callbacks(self) -> None:
        gm = GateManager()
        gm.open_gate(
            run_id="r-2",
            task_id="t-2",
            instance_id="i-2",
            role_id="role-2",
            summary="needs approval",
        )
        gm.resolve_gate(
            run_id="r-2",
            task_id="t-2",
            action="approve",
        )

    def test_add_multiple_callbacks(self) -> None:
        gm = GateManager()
        gm.open_gate(
            run_id="r-3",
            task_id="t-3",
            instance_id="i-3",
            role_id="role-3",
            summary="needs approval",
        )
        cb1 = MagicMock()
        cb2 = MagicMock()
        gm.add_resolved_callback(cb1)
        gm.add_resolved_callback(cb2)
        gm.resolve_gate(
            run_id="r-3",
            task_id="t-3",
            action="approve",
            feedback="done",
        )
        cb1.assert_called_once()
        cb2.assert_called_once()

    def test_callback_exception_swallowed(self) -> None:
        gm = GateManager()
        gm.open_gate(
            run_id="r-4",
            task_id="t-4",
            instance_id="i-4",
            role_id="role-4",
            summary="needs approval",
        )
        bad_cb = MagicMock(side_effect=RuntimeError("boom"))
        gm.add_resolved_callback(bad_cb)
        # Should not raise despite callback exception
        gm.resolve_gate(
            run_id="r-4",
            task_id="t-4",
            action="revise",
        )
        bad_cb.assert_called_once()
