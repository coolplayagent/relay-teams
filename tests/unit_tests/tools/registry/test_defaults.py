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
        "focus_window",
        "glob",
        "grep",
        "hotkey",
        "im_send",
        "launch_app",
        "list_available_roles",
        "list_background_tasks",
        "list_delegated_tasks",
        "list_windows",
        "read",
        "scroll_view",
        "shell",
        "stop_background_task",
        "type_text",
        "update_task",
        "wait_background_task",
        "wait_for_window",
        "webfetch",
        "websearch",
        "write",
    )


def test_registry_hides_im_send_from_manual_role_configuration() -> None:
    registry = build_default_registry()

    assert "im_send" not in registry.list_configurable_names()


def test_default_registry_ignores_legacy_tool_aliases_for_runtime_resolution() -> None:
    registry = build_default_registry()

    assert registry.resolve_known(("shell",), strict=False) == ("shell",)
    assert registry.resolve_known(("exec_command",), strict=False) == ()
    assert registry.resolve_known(("terminate_background_task",), strict=False) == ()
    assert registry.resolve_known(("write_tmp",), strict=False) == ()


def test_default_registry_rejects_legacy_tool_aliases_for_explicit_validation() -> None:
    registry = build_default_registry()

    registry.validate_known(("shell",))
    registry.validate_known(("list_background_tasks",))
    with pytest.raises(ValueError, match="Unknown tools"):
        registry.validate_known(("exec_command",))
    with pytest.raises(ValueError, match="Unknown tools"):
        registry.validate_known(("terminate_background_task",))
    with pytest.raises(ValueError, match="Unknown tools"):
        registry.validate_known(("write_tmp",))
