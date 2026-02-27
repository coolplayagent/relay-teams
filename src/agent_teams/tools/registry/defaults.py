from agent_teams.tools.assign_task import TOOL_SPEC as ASSIGN_TASK
from agent_teams.tools.communicate import TOOL_SPEC as COMMUNICATE
from agent_teams.tools.create_subagent import TOOL_SPEC as CREATE_SUBAGENT
from agent_teams.tools.create_task import TOOL_SPEC as CREATE_TASK
from agent_teams.tools.emit_event import TOOL_SPEC as EMIT_EVENT
from agent_teams.tools.glob import TOOL_SPEC as GLOB
from agent_teams.tools.grep import TOOL_SPEC as GREP
from agent_teams.tools.list_tasks import TOOL_SPEC as LIST_TASKS
from agent_teams.tools.manage_state import TOOL_SPEC as MANAGE_STATE
from agent_teams.tools.query_task import TOOL_SPEC as QUERY_TASK
from agent_teams.tools.read import TOOL_SPEC as READ
from agent_teams.tools.registry.registry import ToolRegistry
from agent_teams.tools.verify_task import TOOL_SPEC as VERIFY_TASK
from agent_teams.tools.write import TOOL_SPEC as WRITE


def build_default_registry() -> ToolRegistry:
    return ToolRegistry(
        (
            CREATE_TASK,
            ASSIGN_TASK,
            QUERY_TASK,
            VERIFY_TASK,
            LIST_TASKS,
            CREATE_SUBAGENT,
            MANAGE_STATE,
            EMIT_EVENT,
            GLOB,
            GREP,
            READ,
            WRITE,
            COMMUNICATE,
        )
    )
