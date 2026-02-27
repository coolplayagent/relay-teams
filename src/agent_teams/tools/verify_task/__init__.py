from agent_teams.tools.registry.models import ToolSpec
from agent_teams.tools.verify_task.mount import mount

TOOL_SPEC = ToolSpec(name='verify_task', mount=mount)
