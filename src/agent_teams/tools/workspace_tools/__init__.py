from __future__ import annotations

from agent_teams.tools.workspace_tools.edit import register as register_edit
from agent_teams.tools.workspace_tools.glob import register as register_glob
from agent_teams.tools.workspace_tools.grep import register as register_grep
from agent_teams.tools.workspace_tools.read import register as register_read
from agent_teams.tools.workspace_tools.shell import register as register_shell
from agent_teams.tools.workspace_tools.write import register as register_write

TOOLS = {
    "edit": register_edit,
    "glob": register_glob,
    "grep": register_grep,
    "read": register_read,
    "write": register_write,
    "shell": register_shell,
}
