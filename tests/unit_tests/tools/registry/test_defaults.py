# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from agent_teams.tools.registry import build_default_registry


def test_registry_rejects_unknown_tools() -> None:
    registry = build_default_registry()
    with pytest.raises(ValueError):
        registry.validate_known(("read", "unknown_tool"))


def test_registry_contains_only_role_mounted_tools() -> None:
    registry = build_default_registry()
    assert registry.list_names() == (
        "create_workflow_graph",
        "dispatch_tasks",
        "glob",
        "grep",
        "list_available_roles",
        "list_available_workflows",
        "read",
        "read_stage_input",
        "shell",
        "write",
        "write_stage_doc",
    )
