from agent_teams.tools.registry.models import ToolSpec
from agent_teams.tools.emit_event.mount import mount

TOOL_SPEC = ToolSpec(name='emit_event', mount=mount)
