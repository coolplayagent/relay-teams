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
        "create_tasks",
        "dispatch_task",
        "edit",
        "feishu_send",
        "glob",
        "grep",
        "list_delegated_tasks",
        "read",
        "shell",
        "update_task",
        "webfetch",
        "websearch",
        "write",
        "write_tmp",
    )
