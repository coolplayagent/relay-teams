# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from relay_teams.tools.registry import build_default_registry


def test_registry_rejects_unknown_tools() -> None:
    registry = build_default_registry()
    with pytest.raises(ValueError):
        registry.validate_known(("read", "unknown_tool"))


def test_registry_contains_registered_local_tools() -> None:
    registry = build_default_registry()
    assert registry.list_names() == (
        "ask_question",
        "capture_screen",
        "click_at",
        "create_monitor",
        "create_tasks",
        "create_temporary_role",
        "dispatch_task",
        "double_click_at",
        "drag_between",
        "edit",
        "focus_window",
        "glob",
        "grep",
        "hotkey",
        "im_send",
        "launch_app",
        "list_available_roles",
        "list_background_tasks",
        "list_delegated_tasks",
        "list_monitors",
        "list_windows",
        "notebook_edit",
        "office_read_markdown",
        "read",
        "scroll_view",
        "shell",
        "spawn_subagent",
        "stop_background_task",
        "stop_monitor",
        "todo_read",
        "todo_write",
        "type_text",
        "update_task",
        "wait_background_task",
        "wait_for_window",
        "webfetch",
        "websearch",
        "write",
        "write_tmp",
    )


def test_registry_hides_im_send_from_manual_role_configuration() -> None:
    registry = build_default_registry()

    assert "im_send" not in registry.list_configurable_names()


def test_default_registry_ignores_unknown_tools_for_runtime_resolution() -> None:
    registry = build_default_registry()

    assert registry.resolve_known(("shell",), strict=False) == ("shell",)
    assert registry.resolve_known(("unknown_tool",), strict=False) == ()
    assert registry.resolve_known(("missing_background_tool",), strict=False) == ()
    assert registry.resolve_known(("deprecated_writer",), strict=False) == ()


def test_default_registry_rejects_unknown_tools_for_explicit_validation() -> None:
    registry = build_default_registry()

    registry.validate_known(("shell",))
    registry.validate_known(("list_background_tasks",))
    registry.validate_known(("notebook_edit",))
    registry.validate_known(("office_read_markdown",))
    with pytest.raises(ValueError, match="Unknown tools"):
        registry.validate_known(("unknown_tool",))
    with pytest.raises(ValueError, match="Unknown tools"):
        registry.validate_known(("missing_background_tool",))
    with pytest.raises(ValueError, match="Unknown tools"):
        registry.validate_known(("deprecated_writer",))
