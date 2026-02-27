from agent_teams.tools.registry.models import ToolSpec
from agent_teams.tools.grep.mount import mount

TOOL_SPEC = ToolSpec(name='grep', mount=mount)
