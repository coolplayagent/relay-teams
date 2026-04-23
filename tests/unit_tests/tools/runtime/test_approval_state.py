# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from relay_teams.tools.runtime.approval_state import ToolApprovalManager


def test_tool_approval_manager_open_list_resolve() -> None:
    manager = ToolApprovalManager()
    manager.open_approval(
        run_id="run-1",
        tool_call_id="toolcall-1",
        instance_id="inst-1",
        role_id="spec_coder",
        tool_name="write",
        args_preview='{"path":"a.txt"}',
        risk_level="high",
    )
    approvals = manager.list_open_approvals(run_id="run-1")
    assert len(approvals) == 1
    assert approvals[0]["tool_call_id"] == "toolcall-1"

    manager.resolve_approval(
        run_id="run-1",
        tool_call_id="toolcall-1",
        action="approve",
    )
    action, feedback = manager.wait_for_approval(
        run_id="run-1",
        tool_call_id="toolcall-1",
        timeout=0.1,
    )
    assert action == "approve"
    assert feedback == ""


def test_tool_approval_manager_missing_entry() -> None:
    manager = ToolApprovalManager()
    with pytest.raises(KeyError):
        manager.resolve_approval(
            run_id="run-1",
            tool_call_id="missing",
            action="deny",
        )
