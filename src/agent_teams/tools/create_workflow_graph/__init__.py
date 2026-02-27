from agent_teams.tools.create_workflow_graph.mount import mount
from agent_teams.tools.registry.models import ToolSpec

TOOL_SPEC = ToolSpec(name='create_workflow_graph', mount=mount)
