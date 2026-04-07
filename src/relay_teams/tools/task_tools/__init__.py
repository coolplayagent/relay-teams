from __future__ import annotations

from relay_teams.tools.task_tools.create_tasks import register as register_create_tasks
from relay_teams.tools.task_tools.create_temporary_role import (
    register as register_create_temporary_role,
)
from relay_teams.tools.task_tools.dispatch_task import (
    register as register_dispatch_task,
)
from relay_teams.tools.task_tools.list_available_roles import (
    register as register_list_available_roles,
)
from relay_teams.tools.task_tools.list_delegated_tasks import (
    register as register_list_delegated_tasks,
)
from relay_teams.tools.task_tools.update_task import register as register_update_task

TOOLS = {
    "create_tasks": register_create_tasks,
    "create_temporary_role": register_create_temporary_role,
    "update_task": register_update_task,
    "list_available_roles": register_list_available_roles,
    "list_delegated_tasks": register_list_delegated_tasks,
    "dispatch_task": register_dispatch_task,
}
