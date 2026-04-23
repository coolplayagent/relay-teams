from __future__ import annotations

from pathlib import Path

import pytest

_UNIT_TESTS_ROOT = Path(__file__).resolve().parent
_FRONTEND_TESTS_ROOT = _UNIT_TESTS_ROOT / "frontend"
_SESSIONS_TESTS_ROOT = _UNIT_TESTS_ROOT / "sessions"
_SERVER_INTERFACE_TESTS_ROOT = _UNIT_TESTS_ROOT / "interfaces" / "server"
_EVALS_WORKSPACE_TESTS_ROOT = _UNIT_TESTS_ROOT / "relay_teams_evals" / "workspace"
_BACKGROUND_TASK_TESTS_ROOT = (
    _UNIT_TESTS_ROOT / "sessions" / "runs" / "background_tasks"
)


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    if not config.pluginmanager.hasplugin("timeout"):
        raise pytest.UsageError(
            "pytest-timeout is required for unit-test timeout enforcement"
        )
    default_timeout_marker = pytest.mark.timeout(1)
    slow_timeout_marker = pytest.mark.timeout(10)
    for item in items:
        item_path = Path(str(item.fspath)).resolve()
        if _UNIT_TESTS_ROOT not in item_path.parents:
            continue
        if item.get_closest_marker("timeout") is not None:
            continue
        if (
            _FRONTEND_TESTS_ROOT in item_path.parents
            or _SESSIONS_TESTS_ROOT in item_path.parents
            or _SERVER_INTERFACE_TESTS_ROOT in item_path.parents
            or _EVALS_WORKSPACE_TESTS_ROOT in item_path.parents
            or _BACKGROUND_TASK_TESTS_ROOT in item_path.parents
        ):
            item.add_marker(slow_timeout_marker)
            continue
        item.add_marker(default_timeout_marker)
