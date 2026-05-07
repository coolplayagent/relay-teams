# -*- coding: utf-8 -*-
from __future__ import annotations

import fnmatch

from pydantic import ValidationError

from relay_teams.hooks.hook_models import (
    HookEventName,
    HookHandlerConfig,
    HookHandlerType,
    HookMatcherGroup,
    HooksConfig,
)

CLAUDE_TOOL_MATCHER_ALIASES: dict[str, str] = {
    "Bash": "shell",
    "Edit": "edit",
    "Glob": "glob",
    "Grep": "grep",
    "NotebookEdit": "notebook_edit",
    "Read": "read",
    "TodoWrite": "todo_write",
    "WebFetch": "webfetch",
    "WebSearch": "websearch",
    "Write": "write",
}

TOOL_EVENTS = frozenset(
    {
        HookEventName.PRE_TOOL_USE,
        HookEventName.PERMISSION_REQUEST,
        HookEventName.PERMISSION_DENIED,
        HookEventName.POST_TOOL_USE,
        HookEventName.POST_TOOL_USE_FAILURE,
    }
)
MATCHER_UNSUPPORTED_EVENTS = frozenset(
    {
        HookEventName.USER_PROMPT_SUBMIT,
        HookEventName.STOP,
        HookEventName.TASK_CREATED,
        HookEventName.TASK_COMPLETED,
    }
)
COMMAND_ONLY_EVENTS = frozenset({HookEventName.SESSION_START})
COMMAND_HTTP_ONLY_EVENTS = frozenset(
    {
        HookEventName.SESSION_END,
        HookEventName.STOP_FAILURE,
        HookEventName.SUBAGENT_START,
        HookEventName.INSTRUCTIONS_LOADED,
        HookEventName.NOTIFICATION,
        HookEventName.PRE_COMPACT,
        HookEventName.POST_COMPACT,
    }
)
_EMPTY_GROUP_ERROR = "hook matcher group must contain at least one handler"


def normalize_hooks_payload(payload: object, *, tolerant: bool = False) -> object:
    if not isinstance(payload, dict):
        return payload
    next_payload = dict(payload)
    raw_hooks = payload.get("hooks")
    if not isinstance(raw_hooks, dict):
        return next_payload
    normalized_hooks: dict[object, object] = {}
    for event_name, raw_groups in raw_hooks.items():
        if not isinstance(event_name, str) or not isinstance(raw_groups, list):
            normalized_hooks[event_name] = raw_groups
            continue
        normalized_groups: list[object] = []
        for raw_group in raw_groups:
            if not isinstance(raw_group, dict):
                normalized_groups.append(raw_group)
                continue
            try:
                normalized_groups.extend(
                    _normalize_hook_group(raw_group, raw_event_name=event_name)
                )
            except ValueError:
                if not tolerant:
                    raise
                continue
        normalized_hooks[event_name] = normalized_groups
    next_payload["hooks"] = normalized_hooks
    return next_payload


def parse_tolerant_hooks_payload(payload: object) -> HooksConfig:
    normalized_payload = normalize_hooks_payload(payload, tolerant=True)
    if not isinstance(normalized_payload, dict):
        return HooksConfig()
    raw_hooks = normalized_payload.get("hooks")
    if not isinstance(raw_hooks, dict):
        return HooksConfig()
    next_hooks: dict[HookEventName, tuple[HookMatcherGroup, ...]] = {}
    for raw_event_name, raw_groups in raw_hooks.items():
        if not isinstance(raw_event_name, str) or not isinstance(raw_groups, list):
            continue
        for raw_group in raw_groups:
            try:
                config = HooksConfig.model_validate(
                    {"hooks": {raw_event_name: [raw_group]}}
                )
                validate_hook_event_capabilities(config=config)
            except ValidationError:
                continue
            except ValueError as exc:
                if str(exc) != _EMPTY_GROUP_ERROR:
                    continue
                try:
                    config = HooksConfig.model_validate(
                        {"hooks": {raw_event_name: [raw_group]}}
                    )
                except ValidationError:
                    continue
            for event_name, groups in config.hooks.items():
                existing_groups = next_hooks.get(event_name, ())
                next_hooks[event_name] = (*existing_groups, *groups)
    return HooksConfig(hooks=next_hooks)


def validate_hook_event_capabilities(*, config: HooksConfig) -> None:
    for event_name, groups in config.hooks.items():
        for group in groups:
            if not group.hooks:
                raise ValueError("hook matcher group must contain at least one handler")
            matcher = group.matcher.strip() or "*"
            if event_name in MATCHER_UNSUPPORTED_EVENTS and matcher != "*":
                raise ValueError(
                    f"Matcher is not supported for {event_name.value} hooks"
                )
            for handler in group.hooks:
                _validate_handler_event_compatibility(
                    event_name=event_name,
                    handler=handler,
                )


def filter_tolerant_hook_groups(*, config: HooksConfig) -> HooksConfig:
    next_hooks: dict[HookEventName, tuple[HookMatcherGroup, ...]] = {}
    for event_name, groups in config.hooks.items():
        valid_groups: list[HookMatcherGroup] = []
        for group in groups:
            try:
                validate_hook_event_capabilities(
                    config=HooksConfig(hooks={event_name: (group,)})
                )
            except ValueError:
                continue
            valid_groups.append(group)
        if valid_groups:
            next_hooks[event_name] = tuple(valid_groups)
    return HooksConfig(hooks=next_hooks)


def _validate_handler_event_compatibility(
    *,
    event_name: HookEventName,
    handler: HookHandlerConfig,
) -> None:
    if handler.run_async and handler.type != HookHandlerType.COMMAND:
        raise ValueError("async hooks are only supported for command handlers")
    if handler.if_rule and event_name not in TOOL_EVENTS:
        raise ValueError(
            f"Hook handler 'if' is only supported for tool events, not {event_name.value}"
        )
    if event_name in COMMAND_ONLY_EVENTS and handler.type != HookHandlerType.COMMAND:
        raise ValueError(f"{event_name.value} only supports command hook handlers")
    if event_name in COMMAND_HTTP_ONLY_EVENTS and handler.type not in {
        HookHandlerType.COMMAND,
        HookHandlerType.HTTP,
    }:
        raise ValueError(
            f"{event_name.value} only supports command and http hook handlers"
        )


def _normalize_hook_group(
    raw_group: dict[str, object],
    *,
    raw_event_name: str = "",
) -> list[object]:
    group = dict(raw_group)
    is_tool_event = _is_tool_event_name(raw_event_name)
    raw_handlers = group.get("hooks")
    handlers = raw_handlers
    if isinstance(raw_handlers, list):
        next_handlers: list[object] = []
        for raw_handler in raw_handlers:
            if isinstance(raw_handler, dict):
                next_handlers.append(dict(raw_handler))
            else:
                next_handlers.append(raw_handler)
        handlers = next_handlers
    legacy_if = str(group.get("if_condition") or "").strip()
    if (
        legacy_if
        and isinstance(handlers, list)
        and handlers
        and all(
            isinstance(handler, dict)
            and "if" not in handler
            and "if_rule" not in handler
            for handler in handlers
        )
    ):
        for handler in handlers:
            if isinstance(handler, dict):
                handler["if"] = legacy_if
        group.pop("if_condition", None)
    elif not legacy_if:
        group.pop("if_condition", None)
    group["hooks"] = handlers
    raw_tool_names = group.get("tool_names", ())
    matcher = str(group.get("matcher") or "").strip()
    if is_tool_event and matcher:
        matcher = _normalize_tool_matcher(matcher)
        if not matcher:
            raise ValueError("tool hook matcher must contain at least one pattern")
        group["matcher"] = matcher
    tool_name_values = (
        raw_tool_names if isinstance(raw_tool_names, (list, tuple)) else ()
    )
    tool_names = tuple(
        dict.fromkeys(
            _normalize_tool_matcher(str(value).strip())
            if is_tool_event
            else str(value).strip()
            for value in tool_name_values
            if str(value).strip()
        )
    )
    if is_tool_event and any(not tool_name for tool_name in tool_names):
        raise ValueError("tool hook matcher must contain at least one pattern")
    if not tool_names:
        group.pop("tool_names", None)
        return [group]
    if matcher and matcher != "*":
        matching_tool_names = [
            tool_name
            for tool_name in tool_names
            if fnmatch.fnmatchcase(tool_name, matcher)
        ]
        if not matching_tool_names:
            return [group]
        group.pop("tool_names", None)
        return [group | {"matcher": tool_name} for tool_name in matching_tool_names]
    group.pop("tool_names", None)
    return [group | {"matcher": tool_name} for tool_name in tool_names]


def _is_tool_event_name(raw_event_name: str) -> bool:
    try:
        return HookEventName(raw_event_name) in TOOL_EVENTS
    except ValueError:
        return False


def _normalize_tool_matcher(matcher: str) -> str:
    return "|".join(
        _normalize_tool_matcher_part(part.strip())
        for part in matcher.split("|")
        if part.strip()
    )


def _normalize_tool_matcher_part(part: str) -> str:
    if _is_glob_matcher(part):
        return part
    return CLAUDE_TOOL_MATCHER_ALIASES.get(part, part)


def _is_glob_matcher(value: str) -> bool:
    return any(char in value for char in "*?[]")
