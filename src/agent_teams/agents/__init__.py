# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
    from agent_teams.agents.instances.enums import InstanceStatus
    from agent_teams.agents.execution.subagent_runner import (
        SubAgentRequest,
        SubAgentRunner,
    )
    from agent_teams.agents.instances.ids import InstanceId, new_instance_id
    from agent_teams.agents.instances.models import (
        AgentRuntimeRecord,
        SubAgentInstance,
        create_subagent_instance,
    )
    from agent_teams.agents.orchestration.meta_agent import MetaAgent

__all__ = [
    "AgentInstanceRepository",
    "AgentRuntimeRecord",
    "InstanceId",
    "InstanceStatus",
    "MetaAgent",
    "SubAgentRequest",
    "SubAgentRunner",
    "SubAgentInstance",
    "create_subagent_instance",
    "new_instance_id",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "AgentInstanceRepository": (
        "agent_teams.agents.instances.instance_repository",
        "AgentInstanceRepository",
    ),
    "AgentRuntimeRecord": ("agent_teams.agents.instances.models", "AgentRuntimeRecord"),
    "InstanceId": ("agent_teams.agents.instances.ids", "InstanceId"),
    "InstanceStatus": ("agent_teams.agents.instances.enums", "InstanceStatus"),
    "MetaAgent": ("agent_teams.agents.orchestration.meta_agent", "MetaAgent"),
    "SubAgentRequest": (
        "agent_teams.agents.execution.subagent_runner",
        "SubAgentRequest",
    ),
    "SubAgentRunner": (
        "agent_teams.agents.execution.subagent_runner",
        "SubAgentRunner",
    ),
    "SubAgentInstance": ("agent_teams.agents.instances.models", "SubAgentInstance"),
    "create_subagent_instance": (
        "agent_teams.agents.instances.models",
        "create_subagent_instance",
    ),
    "new_instance_id": ("agent_teams.agents.instances.ids", "new_instance_id"),
}


def __getattr__(name: str) -> object:
    module_info = _LAZY_IMPORTS.get(name)
    if module_info is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = module_info
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
