# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_ALWAYS_ACTIVE_TOOLS: tuple[str, ...] = (
    "tool_search",
    "activate_tools",
)


class ActivationValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requested: tuple[str, ...] = ()
    authorized: tuple[str, ...] = ()
    active: tuple[str, ...] = ()
    deferred: tuple[str, ...] = ()
    already_active: tuple[str, ...] = ()
    unknown_or_unauthorized: tuple[str, ...] = ()
    activatable: tuple[str, ...] = ()


class ActivationApplyResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requested: tuple[str, ...] = ()
    activated: tuple[str, ...] = ()
    already_active: tuple[str, ...] = ()
    unknown_or_unauthorized: tuple[str, ...] = ()
    rejected_due_to_limit: tuple[str, ...] = ()
    active_tools: tuple[str, ...] = ()
    deferred_tools: tuple[str, ...] = ()
    max_active_tools: int | None = Field(default=None, ge=1)


def build_initial_active_tools(
    authorized_tools: tuple[str, ...],
    *,
    always_active_tools: tuple[str, ...] = DEFAULT_ALWAYS_ACTIVE_TOOLS,
) -> tuple[str, ...]:
    authorized_set = set(authorized_tools)
    return _dedupe_names(
        tuple(name for name in always_active_tools if name in authorized_set)
    )


def validate_activation_request(
    *,
    authorized_tools: tuple[str, ...],
    active_tools: tuple[str, ...],
    requested_tool_names: tuple[str, ...],
) -> ActivationValidationResult:
    authorized = _dedupe_names(authorized_tools)
    active = tuple(name for name in _dedupe_names(active_tools) if name in authorized)
    authorized_set = set(authorized)
    active_set = set(active)
    requested = _dedupe_names(requested_tool_names)

    already_active = tuple(name for name in requested if name in active_set)
    unknown_or_unauthorized = tuple(
        name for name in requested if name not in authorized_set
    )
    activatable = tuple(
        name for name in requested if name in authorized_set and name not in active_set
    )
    deferred = tuple(name for name in authorized if name not in active_set)

    return ActivationValidationResult(
        requested=requested,
        authorized=authorized,
        active=active,
        deferred=deferred,
        already_active=already_active,
        unknown_or_unauthorized=unknown_or_unauthorized,
        activatable=activatable,
    )


def apply_tool_activation(
    *,
    authorized_tools: tuple[str, ...],
    active_tools: tuple[str, ...],
    requested_tool_names: tuple[str, ...],
    max_active_tools: int | None = None,
) -> ActivationApplyResult:
    validation = validate_activation_request(
        authorized_tools=authorized_tools,
        active_tools=active_tools,
        requested_tool_names=requested_tool_names,
    )
    next_active = list(validation.active)
    activated: list[str] = []
    rejected_due_to_limit: list[str] = []

    for name in validation.activatable:
        if max_active_tools is not None and len(next_active) >= max_active_tools:
            rejected_due_to_limit.append(name)
            continue
        next_active.append(name)
        activated.append(name)

    active_result = tuple(next_active)
    return ActivationApplyResult(
        requested=validation.requested,
        activated=tuple(activated),
        already_active=validation.already_active,
        unknown_or_unauthorized=validation.unknown_or_unauthorized,
        rejected_due_to_limit=tuple(rejected_due_to_limit),
        active_tools=active_result,
        deferred_tools=tuple(
            name for name in validation.authorized if name not in set(active_result)
        ),
        max_active_tools=max_active_tools,
    )


def _dedupe_names(names: tuple[str, ...]) -> tuple[str, ...]:
    deduplicated: list[str] = []
    seen: set[str] = set()
    for raw_name in names:
        name = str(raw_name).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        deduplicated.append(name)
    return tuple(deduplicated)
