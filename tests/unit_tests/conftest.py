from __future__ import annotations

from os import environ
from pathlib import Path

import pytest

from relay_teams.secrets import AppSecretStore

_UNIT_TESTS_ROOT = Path(__file__).resolve().parent
_DEFAULT_UNIT_TEST_TIMEOUT_SECONDS = 1.0
_UNIT_TEST_TIMEOUT_ENV = "RELAY_TEAMS_UNIT_TEST_TIMEOUT_SECONDS"


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
    timeout_marker = pytest.mark.timeout(_unit_test_timeout_seconds())
    for item in items:
        item_path = Path(str(item.fspath)).resolve()
        if _UNIT_TESTS_ROOT not in item_path.parents:
            continue
        if item.get_closest_marker("timeout") is not None:
            continue
        item.add_marker(timeout_marker)


def _unit_test_timeout_seconds() -> float:
    raw_timeout = environ.get(_UNIT_TEST_TIMEOUT_ENV)
    if raw_timeout is None:
        return _DEFAULT_UNIT_TEST_TIMEOUT_SECONDS
    try:
        timeout_seconds = float(raw_timeout)
    except ValueError as exc:
        raise pytest.UsageError(
            f"{_UNIT_TEST_TIMEOUT_ENV} must be a positive number"
        ) from exc
    if timeout_seconds <= 0:
        raise pytest.UsageError(f"{_UNIT_TEST_TIMEOUT_ENV} must be a positive number")
    return timeout_seconds
