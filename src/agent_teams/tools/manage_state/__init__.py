from agent_teams.tools.registry.models import ToolSpec
from agent_teams.tools.manage_state.mount import mount

TOOL_SPEC = ToolSpec(name='manage_state', mount=mount)
