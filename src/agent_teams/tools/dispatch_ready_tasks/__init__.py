from agent_teams.tools.dispatch_ready_tasks.mount import mount
from agent_teams.tools.registry.models import ToolSpec

TOOL_SPEC = ToolSpec(name='dispatch_ready_tasks', mount=mount)
