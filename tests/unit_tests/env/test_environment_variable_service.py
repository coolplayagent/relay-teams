# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from agent_teams.env.environment_variable_models import (
    EnvironmentVariableRecord,
    EnvironmentVariableSaveRequest,
    EnvironmentVariableScope,
    EnvironmentVariableValueKind,
)
from agent_teams.env.environment_variable_service import EnvironmentVariableService


class _FakeEnvironmentVariableBackend:
    def __init__(self) -> None:
        self._values: dict[
            EnvironmentVariableScope, dict[str, EnvironmentVariableRecord]
        ] = {
            EnvironmentVariableScope.SYSTEM: {
                "Path": EnvironmentVariableRecord(
                    key="Path",
                    value=r"C:\\Windows\\System32",
                    scope=EnvironmentVariableScope.SYSTEM,
                    value_kind=EnvironmentVariableValueKind.EXPANDABLE,
                ),
                "ComSpec": EnvironmentVariableRecord(
                    key="ComSpec",
                    value=r"%SystemRoot%\\system32\\cmd.exe",
                    scope=EnvironmentVariableScope.SYSTEM,
                    value_kind=EnvironmentVariableValueKind.EXPANDABLE,
                ),
            },
            EnvironmentVariableScope.USER: {
                "BETA": EnvironmentVariableRecord(
                    key="BETA",
                    value="2",
                    scope=EnvironmentVariableScope.USER,
                    value_kind=EnvironmentVariableValueKind.STRING,
                ),
                "ALPHA": EnvironmentVariableRecord(
                    key="ALPHA",
                    value="1",
                    scope=EnvironmentVariableScope.USER,
                    value_kind=EnvironmentVariableValueKind.STRING,
                ),
            },
        }
        self.broadcast_count = 0

    def list_values(
        self,
        scope: EnvironmentVariableScope,
    ) -> tuple[EnvironmentVariableRecord, ...]:
        return tuple(self._values[scope].values())

    def get_value(
        self,
        scope: EnvironmentVariableScope,
        key: str,
    ) -> EnvironmentVariableRecord | None:
        return self._values[scope].get(key)

    def set_value(
        self,
        scope: EnvironmentVariableScope,
        key: str,
        value: str,
        *,
        value_kind: EnvironmentVariableValueKind,
    ) -> None:
        self._values[scope][key] = EnvironmentVariableRecord(
            key=key,
            value=value,
            scope=scope,
            value_kind=value_kind,
        )

    def delete_value(self, scope: EnvironmentVariableScope, key: str) -> None:
        del self._values[scope][key]

    def broadcast_change(self) -> None:
        self.broadcast_count += 1


def test_list_environment_variables_sorts_each_scope() -> None:
    backend = _FakeEnvironmentVariableBackend()
    service = EnvironmentVariableService(backend=backend)

    payload = service.list_environment_variables()

    assert [record.key for record in payload.system] == ["ComSpec", "Path"]
    assert [record.key for record in payload.user] == ["ALPHA", "BETA"]


def test_save_environment_variable_renames_and_preserves_value_kind() -> None:
    backend = _FakeEnvironmentVariableBackend()
    service = EnvironmentVariableService(backend=backend)

    saved = service.save_environment_variable(
        scope=EnvironmentVariableScope.SYSTEM,
        key="SystemPath",
        request=EnvironmentVariableSaveRequest(
            source_key="Path",
            value=r"%SystemRoot%\\System32;C:\\Tools",
        ),
    )

    assert saved.key == "SystemPath"
    assert saved.scope == EnvironmentVariableScope.SYSTEM
    assert saved.value_kind == EnvironmentVariableValueKind.EXPANDABLE
    assert backend.get_value(EnvironmentVariableScope.SYSTEM, "Path") is None
    persisted = backend.get_value(EnvironmentVariableScope.SYSTEM, "SystemPath")
    assert persisted is not None
    assert persisted.value_kind == EnvironmentVariableValueKind.EXPANDABLE
    assert backend.broadcast_count == 1


def test_delete_environment_variable_requires_existing_key() -> None:
    backend = _FakeEnvironmentVariableBackend()
    service = EnvironmentVariableService(backend=backend)

    with pytest.raises(ValueError, match="not found"):
        service.delete_environment_variable(
            scope=EnvironmentVariableScope.USER,
            key="MISSING",
        )


def test_save_environment_variable_rejects_rename_to_existing_key() -> None:
    backend = _FakeEnvironmentVariableBackend()
    service = EnvironmentVariableService(backend=backend)

    with pytest.raises(ValueError, match="already exists"):
        service.save_environment_variable(
            scope=EnvironmentVariableScope.USER,
            key="BETA",
            request=EnvironmentVariableSaveRequest(
                source_key="ALPHA",
                value="1",
            ),
        )
