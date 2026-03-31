# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from agent_teams.tools.registry import build_default_registry


def test_registry_rejects_unknown_tools() -> None:
    registry = build_default_registry()
    with pytest.raises(ValueError):
        registry.validate_known(("read", "unknown_tool"))


def test_registry_contains_registered_local_tools() -> None:
    registry = build_default_registry()
    assert registry.list_names() == (
        "capture_screen",
        "click_at",
        "create_tasks",
        "create_temporary_role",
        "dispatch_task",
        "double_click_at",
        "drag_between",
        "edit",
        "exec_command",
        "focus_window",
        "glob",
        "grep",
        "hotkey",
        "im_send",
        "launch_app",
        "list_available_roles",
        "list_delegated_tasks",
        "list_exec_sessions",
        "list_windows",
        "read",
        "resize_exec_session",
        "scroll_view",
        "terminate_exec_session",
        "type_text",
        "update_task",
        "wait_for_window",
        "webfetch",
        "websearch",
        "write",
        "write_stdin",
    )


def test_registry_hides_im_send_from_manual_role_configuration() -> None:
    registry = build_default_registry()

    assert "im_send" not in registry.list_configurable_names()


def test_default_registry_maps_legacy_tool_aliases_for_runtime_resolution() -> None:
    registry = build_default_registry()

    assert registry.resolve_known(("shell",), strict=False) == ("exec_command",)
    assert registry.resolve_known(("write_tmp",), strict=False) == ("write",)


def test_default_registry_rejects_legacy_tool_aliases_for_explicit_validation() -> None:
    registry = build_default_registry()

    with pytest.raises(ValueError, match="Unknown tools"):
        registry.validate_known(("shell",))
    with pytest.raises(ValueError, match="Unknown tools"):
        registry.validate_known(("write_tmp",))
