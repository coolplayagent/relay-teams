from agent_teams.tools.registry.models import ToolSpec
from agent_teams.tools.create_subagent.mount import mount

TOOL_SPEC = ToolSpec(name='create_subagent', mount=mount)
