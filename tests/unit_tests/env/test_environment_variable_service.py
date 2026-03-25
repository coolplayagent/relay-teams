# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

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


def test_list_environment_variables_sorts_each_scope(tmp_path: Path) -> None:
    backend = _FakeEnvironmentVariableBackend()
    app_env_file_path = tmp_path / ".agent-teams" / ".env"
    app_env_file_path.parent.mkdir(parents=True)
    app_env_file_path.write_text("BETA=2\nALPHA=1\n", encoding="utf-8")
    service = EnvironmentVariableService(
        backend=backend,
        app_env_file_path=app_env_file_path,
    )

    payload = service.list_environment_variables()

    assert [record.key for record in payload.system] == ["ComSpec", "Path"]
    assert [record.key for record in payload.app] == ["ALPHA", "BETA"]


def test_save_environment_variable_rejects_system_scope_mutation(
    tmp_path: Path,
) -> None:
    backend = _FakeEnvironmentVariableBackend()
    service = EnvironmentVariableService(
        backend=backend,
        app_env_file_path=tmp_path / ".agent-teams" / ".env",
    )

    with pytest.raises(ValueError, match="read-only"):
        service.save_environment_variable(
            scope=EnvironmentVariableScope.SYSTEM,
            key="SystemPath",
            request=EnvironmentVariableSaveRequest(
                source_key="Path",
                value=r"%SystemRoot%\\System32;C:\\Tools",
            ),
        )


def test_save_environment_variable_renames_and_preserves_value_kind(
    tmp_path: Path,
) -> None:
    backend = _FakeEnvironmentVariableBackend()
    app_env_file_path = tmp_path / ".agent-teams" / ".env"
    app_env_file_path.parent.mkdir(parents=True)
    app_env_file_path.write_text(
        "Path=%SystemRoot%\\\\system32\\\\cmd.exe\n", encoding="utf-8"
    )
    service = EnvironmentVariableService(
        backend=backend,
        app_env_file_path=app_env_file_path,
    )

    saved = service.save_environment_variable(
        scope=EnvironmentVariableScope.APP,
        key="SystemPath",
        request=EnvironmentVariableSaveRequest(
            source_key="Path",
            value=r"%SystemRoot%\\System32;C:\\Tools",
        ),
    )

    assert saved.key == "SystemPath"
    assert saved.scope == EnvironmentVariableScope.APP
    assert saved.value_kind == EnvironmentVariableValueKind.EXPANDABLE
    assert (
        "Path=%SystemRoot%\\\\system32\\\\cmd.exe"
        not in app_env_file_path.read_text(encoding="utf-8")
    )
    persisted = service.list_environment_variables().app
    assert [record.key for record in persisted] == ["SystemPath"]
    assert persisted[0].value_kind == EnvironmentVariableValueKind.EXPANDABLE
    assert backend.broadcast_count == 0


def test_delete_environment_variable_requires_existing_key(tmp_path: Path) -> None:
    backend = _FakeEnvironmentVariableBackend()
    service = EnvironmentVariableService(
        backend=backend,
        app_env_file_path=tmp_path / ".agent-teams" / ".env",
    )

    with pytest.raises(ValueError, match="not found"):
        service.delete_environment_variable(
            scope=EnvironmentVariableScope.APP,
            key="MISSING",
        )


def test_delete_environment_variable_rejects_system_scope(tmp_path: Path) -> None:
    backend = _FakeEnvironmentVariableBackend()
    service = EnvironmentVariableService(
        backend=backend,
        app_env_file_path=tmp_path / ".agent-teams" / ".env",
    )

    with pytest.raises(ValueError, match="read-only"):
        service.delete_environment_variable(
            scope=EnvironmentVariableScope.SYSTEM,
            key="Path",
        )


def test_save_environment_variable_rejects_rename_to_existing_key(
    tmp_path: Path,
) -> None:
    backend = _FakeEnvironmentVariableBackend()
    app_env_file_path = tmp_path / ".agent-teams" / ".env"
    app_env_file_path.parent.mkdir(parents=True)
    app_env_file_path.write_text("BETA=2\nALPHA=1\n", encoding="utf-8")
    service = EnvironmentVariableService(
        backend=backend,
        app_env_file_path=app_env_file_path,
    )

    with pytest.raises(ValueError, match="already exists"):
        service.save_environment_variable(
            scope=EnvironmentVariableScope.APP,
            key="BETA",
            request=EnvironmentVariableSaveRequest(
                source_key="ALPHA",
                value="1",
            ),
        )
