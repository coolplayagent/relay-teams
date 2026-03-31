# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.tools.runtime import ToolApprovalPolicy


def test_default_policy_requires_high_risk_tools() -> None:
    policy = ToolApprovalPolicy()
    assert policy.requires_approval("exec_command")
    assert policy.requires_approval("edit")
    assert policy.requires_approval("write")
    assert policy.requires_approval("write_tmp")
    assert policy.requires_approval("webfetch")
    assert policy.requires_approval("websearch")
    assert not policy.requires_approval("read")


def test_yolo_policy_disables_approval_for_all_tools() -> None:
    policy = ToolApprovalPolicy(yolo=True)

    assert not policy.requires_approval("exec_command")
    assert not policy.requires_approval("edit")
    assert not policy.requires_approval("write")
    assert not policy.requires_approval("write_tmp")
    assert not policy.requires_approval("create_tasks")
