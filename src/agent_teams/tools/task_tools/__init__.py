from __future__ import annotations

from agent_teams.tools.task_tools.create_tasks import register as register_create_tasks
from agent_teams.tools.task_tools.dispatch_task import (
    register as register_dispatch_task,
)
from agent_teams.tools.task_tools.list_run_tasks import (
    register as register_list_run_tasks,
)
from agent_teams.tools.task_tools.update_task import register as register_update_task

TOOLS = {
    "create_tasks": register_create_tasks,
    "update_task": register_update_task,
    "list_run_tasks": register_list_run_tasks,
    "dispatch_task": register_dispatch_task,
}
