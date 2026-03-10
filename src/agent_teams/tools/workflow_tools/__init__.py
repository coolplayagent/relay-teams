from __future__ import annotations

from agent_teams.tools.workflow_tools.create_workflow_graph import (
    register as register_create_workflow_graph,
)
from agent_teams.tools.workflow_tools.dispatch_tasks import (
    register as register_dispatch_tasks,
)
from agent_teams.tools.workflow_tools.list_available_roles import (
    register as register_list_available_roles,
)
from agent_teams.tools.workflow_tools.list_available_workflows import (
    register as register_list_available_workflows,
)

TOOLS = {
    "list_available_roles": register_list_available_roles,
    "list_available_workflows": register_list_available_workflows,
    "create_workflow_graph": register_create_workflow_graph,
    "dispatch_tasks": register_dispatch_tasks,
}
