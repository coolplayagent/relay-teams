from __future__ import annotations

from relay_teams.tools.orchestration_tools.create_tasks import (
    register as register_create_tasks,
)
from relay_teams.tools.orchestration_tools.create_temporary_role import (
    register as register_create_temporary_role,
)
from relay_teams.tools.orchestration_tools.dispatch_task import (
    register as register_dispatch_task,
)
from relay_teams.tools.orchestration_tools.list_available_roles import (
    register as register_list_available_roles,
)
from relay_teams.tools.orchestration_tools.list_delegated_tasks import (
    register as register_list_delegated_tasks,
)
from relay_teams.tools.orchestration_tools.update_task import (
    register as register_update_task,
)

TOOLS = {
    "orch_create_tasks": register_create_tasks,
    "orch_create_temporary_role": register_create_temporary_role,
    "orch_update_task": register_update_task,
    "orch_list_available_roles": register_list_available_roles,
    "orch_list_delegated_tasks": register_list_delegated_tasks,
    "orch_dispatch_task": register_dispatch_task,
}

__all__ = [
    "TOOLS",
]
