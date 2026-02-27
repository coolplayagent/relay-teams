from agent_teams.tools.registry.models import ToolSpec
from agent_teams.tools.query_task.mount import mount

TOOL_SPEC = ToolSpec(name='query_task', mount=mount)
