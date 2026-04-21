from __future__ import annotations

from relay_teams.tools.todo_tools.todo_read import register as register_todo_read
from relay_teams.tools.todo_tools.todo_write import register as register_todo_write

TOOLS = {
    "todo_read": register_todo_read,
    "todo_write": register_todo_write,
}
