from agent_teams.tools.materialize_code_shards_from_design.mount import mount
from agent_teams.tools.registry.models import ToolSpec

TOOL_SPEC = ToolSpec(name='materialize_code_shards_from_design', mount=mount)
