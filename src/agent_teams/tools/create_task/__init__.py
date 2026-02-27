from agent_teams.tools.registry.models import ToolSpec
from agent_teams.tools.create_task.mount import mount

TOOL_SPEC = ToolSpec(name='create_task', mount=mount)
