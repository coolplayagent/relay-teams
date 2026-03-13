# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.agents.enums import InstanceStatus
from agent_teams.agents.models import create_subagent_instance
from agent_teams.workspace import (
    build_instance_conversation_id,
    build_instance_workspace_id,
)


def test_create_subagent_instance_defaults_to_idle() -> None:
    instance = create_subagent_instance("generalist")

    assert instance.status == InstanceStatus.IDLE
    assert instance.workspace_id == instance.instance_id
    assert instance.conversation_id == instance.instance_id


def test_create_subagent_instance_with_session_uses_instance_scoped_ids() -> None:
    instance = create_subagent_instance("generalist", session_id="session-1")

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
