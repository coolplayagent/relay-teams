from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.secrets import AppSecretStore

_UNIT_TESTS_ROOT = Path(__file__).resolve().parent


class _UnitTestSecretStore(AppSecretStore):
    def has_usable_keyring_backend(self) -> bool:
        return False


@pytest.fixture(autouse=True)
def _disable_system_keyring_for_unit_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    import relay_teams.secrets.secret_store as secret_store

    monkeypatch.setattr(secret_store, "_SECRET_STORE", _UnitTestSecretStore())


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
