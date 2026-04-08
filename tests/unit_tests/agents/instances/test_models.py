# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.agents.instances.models import create_subagent_instance
from relay_teams.workspace import build_instance_conversation_id


def test_create_subagent_instance_requires_conversation_without_session() -> None:
    with pytest.raises(
        ValueError,
        match="conversation_id is required when session_id is not provided",
    ):
        _ = create_subagent_instance("generalist", workspace_id="workspace-1")


def test_create_subagent_instance_with_session_uses_explicit_workspace_id() -> None:
    instance = create_subagent_instance(
        "generalist",
        session_id="session-1",
        workspace_id="workspace-1",
    )

    assert instance.status == InstanceStatus.IDLE
    assert instance.workspace_id == "workspace-1"
    assert instance.conversation_id == build_instance_conversation_id(
        "session-1",
        "generalist",
        instance.instance_id,
    )
