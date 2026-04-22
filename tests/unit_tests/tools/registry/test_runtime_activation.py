# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.tools.registry.runtime_activation import (
    apply_tool_activation,
    build_initial_active_tools,
    validate_activation_request,
)


def test_build_initial_active_tools_keeps_implicit_tool_search_when_authorized() -> (
    None
):
    assert build_initial_active_tools(
        ("read", "activate_tools", "tool_search", "write")
    ) == (
        "tool_search",
        "activate_tools",
    )


def test_validate_activation_request_separates_active_deferred_and_unknown() -> None:
    result = validate_activation_request(
        authorized_tools=("tool_search", "activate_tools", "read", "write"),
        active_tools=("tool_search", "activate_tools"),
        requested_tool_names=("read", "tool_search", "unknown_tool", "read"),
    )

    assert result.active == ("tool_search", "activate_tools")
    assert result.deferred == ("read", "write")
    assert result.activatable == ("read",)
    assert result.already_active == ("tool_search",)
    assert result.unknown_or_unauthorized == ("unknown_tool",)


def test_apply_tool_activation_is_idempotent_and_respects_max_active_limit() -> None:
    result = apply_tool_activation(
        authorized_tools=("tool_search", "activate_tools", "read", "write"),
        active_tools=("tool_search", "activate_tools"),
        requested_tool_names=("read", "write", "tool_search"),
        max_active_tools=3,
    )

    assert result.activated == ("read",)
    assert result.already_active == ("tool_search",)
    assert result.rejected_due_to_limit == ("write",)
    assert result.active_tools == ("tool_search", "activate_tools", "read")
    assert result.deferred_tools == ("write",)
