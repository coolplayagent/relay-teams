from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_teams.tools.feishu_tools.context import FeishuToolContextResolver
    from agent_teams.tools.feishu_tools.service import FeishuToolService

__all__ = ["FeishuToolContextResolver", "FeishuToolService"]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "FeishuToolContextResolver": (
        "agent_teams.tools.feishu_tools.context",
        "FeishuToolContextResolver",
    ),
    "FeishuToolService": (
        "agent_teams.tools.feishu_tools.service",
        "FeishuToolService",
    ),
}


def __getattr__(name: str) -> object:
    module_info = _LAZY_IMPORTS.get(name)
    if module_info is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = module_info
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
