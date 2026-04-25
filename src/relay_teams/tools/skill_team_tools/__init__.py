# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.tools.skill_team_tools.activate_skill_roles import (
    register as register_activate_skill_roles,
)
from relay_teams.tools.skill_team_tools.list_skill_roles import (
    register as register_list_skill_roles,
)

TOOLS = {
    "activate_skill_roles": register_activate_skill_roles,
    "list_skill_roles": register_list_skill_roles,
}

__all__ = [
    "TOOLS",
    "register_activate_skill_roles",
    "register_list_skill_roles",
]
