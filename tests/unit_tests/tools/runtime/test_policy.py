# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.sessions.runs.enums import ApprovalMode
from agent_teams.tools.runtime import ToolApprovalPolicy


def test_default_policy_requires_high_risk_tools() -> None:
    policy = ToolApprovalPolicy()
    assert policy.requires_approval("shell")
    assert policy.requires_approval("write")
    assert policy.requires_approval("write_stage_doc")
    assert not policy.requires_approval("read")


def test_yolo_policy_disables_approval_for_all_tools() -> None:
    policy = ToolApprovalPolicy(approval_mode=ApprovalMode.YOLO)

    assert not policy.requires_approval("shell")
    assert not policy.requires_approval("write")
    assert not policy.requires_approval("create_tasks")
