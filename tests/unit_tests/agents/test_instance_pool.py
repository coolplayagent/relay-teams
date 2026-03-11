# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.agents.enums import InstanceStatus
from agent_teams.agents.management.instance_pool import InstancePool
from agent_teams.workspace import (
    build_instance_conversation_id,
    build_instance_workspace_id,
)


def test_instance_lifecycle() -> None:
    pool = InstancePool()
    instance = pool.create_subagent("generalist")
    assert instance.status == InstanceStatus.IDLE

    pool.mark_running(instance.instance_id)
    assert pool.get(instance.instance_id).status == InstanceStatus.RUNNING

    pool.mark_timeout(instance.instance_id)
    assert pool.get(instance.instance_id).status == InstanceStatus.TIMEOUT


def test_create_subagent_with_session_uses_instance_scoped_ids() -> None:
    pool = InstancePool()
    instance = pool.create_subagent("generalist", session_id="session-1")

    assert instance.workspace_id == build_instance_workspace_id(
        "session-1",
        "generalist",
        instance.instance_id,
    )
    assert instance.conversation_id == build_instance_conversation_id(
        "session-1",
        "generalist",
        instance.instance_id,
    )
