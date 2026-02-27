from agent_teams.tools.assign_task import TOOL_SPEC as ASSIGN_TASK
from agent_teams.tools.communicate import TOOL_SPEC as COMMUNICATE
from agent_teams.tools.create_subagent import TOOL_SPEC as CREATE_SUBAGENT
from agent_teams.tools.create_task import TOOL_SPEC as CREATE_TASK
from agent_teams.tools.create_workflow_graph import TOOL_SPEC as CREATE_WORKFLOW_GRAPH
from agent_teams.tools.dispatch_ready_tasks import TOOL_SPEC as DISPATCH_READY_TASKS
from agent_teams.tools.emit_event import TOOL_SPEC as EMIT_EVENT
from agent_teams.tools.get_workflow_status import TOOL_SPEC as GET_WORKFLOW_STATUS
from agent_teams.tools.glob import TOOL_SPEC as GLOB
from agent_teams.tools.grep import TOOL_SPEC as GREP
from agent_teams.tools.list_tasks import TOOL_SPEC as LIST_TASKS
from agent_teams.tools.manage_state import TOOL_SPEC as MANAGE_STATE
from agent_teams.tools.materialize_code_shards_from_design import TOOL_SPEC as MATERIALIZE_CODE_SHARDS_FROM_DESIGN
from agent_teams.tools.query_task import TOOL_SPEC as QUERY_TASK
from agent_teams.tools.read import TOOL_SPEC as READ
from agent_teams.tools.read_stage_input import TOOL_SPEC as READ_STAGE_INPUT
from agent_teams.tools.registry.registry import ToolRegistry
from agent_teams.tools.shell import TOOL_SPEC as SHELL
from agent_teams.tools.verify_task import TOOL_SPEC as VERIFY_TASK
from agent_teams.tools.write import TOOL_SPEC as WRITE
from agent_teams.tools.write_stage_doc import TOOL_SPEC as WRITE_STAGE_DOC


def build_default_registry() -> ToolRegistry:
    return ToolRegistry(
        (
            CREATE_TASK,
            CREATE_WORKFLOW_GRAPH,
            MATERIALIZE_CODE_SHARDS_FROM_DESIGN,
            DISPATCH_READY_TASKS,
            GET_WORKFLOW_STATUS,
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
            READ_STAGE_INPUT,
            WRITE_STAGE_DOC,
            SHELL,
        )
    )
