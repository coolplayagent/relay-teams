# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.tools.auto_harness_tools.disable_tool import (
    register as register_auto_harness_disable_tool,
)
from relay_teams.tools.auto_harness_tools.enable_tool import (
    register as register_auto_harness_enable_tool,
)
from relay_teams.tools.auto_harness_tools.synthesize_tool import (
    register as register_auto_harness_synthesize_tool,
)
from relay_teams.tools.auto_harness_tools.upgrade_tool import (
    register as register_auto_harness_upgrade_tool,
)
from relay_teams.tools.generated_tools import (
    AUTO_HARNESS_DISABLE_TOOL,
    AUTO_HARNESS_ENABLE_TOOL,
    AUTO_HARNESS_SYNTHESIZE_TOOL,
    AUTO_HARNESS_UPGRADE_TOOL,
)

TOOLS = {
    AUTO_HARNESS_SYNTHESIZE_TOOL: register_auto_harness_synthesize_tool,
    AUTO_HARNESS_ENABLE_TOOL: register_auto_harness_enable_tool,
    AUTO_HARNESS_DISABLE_TOOL: register_auto_harness_disable_tool,
    AUTO_HARNESS_UPGRADE_TOOL: register_auto_harness_upgrade_tool,
}

__all__ = [
    "TOOLS",
    "register_auto_harness_disable_tool",
    "register_auto_harness_enable_tool",
    "register_auto_harness_synthesize_tool",
    "register_auto_harness_upgrade_tool",
]
