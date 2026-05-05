# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar, Token

_CURRENT_TOOL_HOOK_RUNTIME_ENV: ContextVar[dict[str, str] | None] = ContextVar(
    "current_tool_hook_runtime_env",
    default=None,
)


def set_tool_hook_runtime_env(
    env: Mapping[str, str] | None,
) -> Token[dict[str, str] | None]:
    resolved = None if env is None else dict(env.items())
    return _CURRENT_TOOL_HOOK_RUNTIME_ENV.set(resolved)


def reset_tool_hook_runtime_env(token: Token[dict[str, str] | None]) -> None:
    _CURRENT_TOOL_HOOK_RUNTIME_ENV.reset(token)


def get_tool_hook_runtime_env() -> dict[str, str]:
    current = _CURRENT_TOOL_HOOK_RUNTIME_ENV.get()
    return {} if current is None else dict(current.items())


def merge_tool_hook_runtime_env(
    base_env: Mapping[str, str] | None,
) -> dict[str, str] | None:
    current = _CURRENT_TOOL_HOOK_RUNTIME_ENV.get()
    if base_env is None and current is None:
        return None
    merged: dict[str, str] = {}
    if base_env is not None:
        merged.update(base_env)
    if current is not None:
        merged.update(current)
    return merged
