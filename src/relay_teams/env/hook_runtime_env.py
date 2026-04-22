# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar, Token
from typing import Dict, Optional

_CURRENT_TOOL_HOOK_RUNTIME_ENV: ContextVar[Optional[Dict[str, str]]] = ContextVar(
    "current_tool_hook_runtime_env",
    default=None,
)


def set_tool_hook_runtime_env(
    env: Optional[Mapping[str, str]],
) -> Token[Optional[Dict[str, str]]]:
    resolved = None if env is None else dict(env.items())
    return _CURRENT_TOOL_HOOK_RUNTIME_ENV.set(resolved)


def reset_tool_hook_runtime_env(token: Token[Optional[Dict[str, str]]]) -> None:
    _CURRENT_TOOL_HOOK_RUNTIME_ENV.reset(token)


def get_tool_hook_runtime_env() -> Dict[str, str]:
    current = _CURRENT_TOOL_HOOK_RUNTIME_ENV.get()
    return {} if current is None else dict(current.items())


def merge_tool_hook_runtime_env(
    base_env: Optional[Mapping[str, str]],
) -> Optional[Dict[str, str]]:
    current = _CURRENT_TOOL_HOOK_RUNTIME_ENV.get()
    if base_env is None and current is None:
        return None
    merged: Dict[str, str] = {}
    if base_env is not None:
        merged.update(base_env)
    if current is not None:
        merged.update(current)
    return merged
