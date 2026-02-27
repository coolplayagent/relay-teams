from agent_teams.tools.get_workflow_status.mount import mount
from agent_teams.tools.registry.models import ToolSpec

TOOL_SPEC = ToolSpec(name='get_workflow_status', mount=mount)
