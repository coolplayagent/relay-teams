from __future__ import annotations

from pathlib import Path

import pytest

_UNIT_TESTS_ROOT = Path(__file__).resolve().parent


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    if not config.pluginmanager.hasplugin("timeout"):
        raise pytest.UsageError(
            "pytest-timeout is required for unit-test timeout enforcement"
        )
    timeout_marker = pytest.mark.timeout(1)
    for item in items:
        item_path = Path(str(item.fspath)).resolve()
        if _UNIT_TESTS_ROOT not in item_path.parents:
            continue
        if item.get_closest_marker("timeout") is not None:
            continue
        item.add_marker(timeout_marker)
