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
        "list_delegated_tasks",
        "list_windows",
        "read",
        "scroll_view",
        "shell",
        "shell_background_list",
        "shell_background_read",
        "shell_background_resize",
        "shell_background_start",
        "shell_background_stop",
        "shell_background_wait",
        "shell_background_write",
        "type_text",
        "update_task",
        "wait_for_window",
        "webfetch",
        "websearch",
        "write",
        "write_tmp",
    )


def test_registry_hides_im_send_from_manual_role_configuration() -> None:
    registry = build_default_registry()

    assert "im_send" not in registry.list_configurable_names()
