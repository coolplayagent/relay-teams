# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable, Sequence
import os
import re
from pathlib import Path
from typing import Protocol

from agent_teams.env.environment_variable_models import (
    EnvironmentVariableCatalog,
    EnvironmentVariableRecord,
    EnvironmentVariableSaveRequest,
    EnvironmentVariableScope,
    EnvironmentVariableValueKind,
)
from agent_teams.env.runtime_env import (
    get_app_env_file_path,
    load_env_file,
    load_secret_env_vars,
    sync_app_env_to_process_env,
)
from agent_teams.secrets import get_secret_store, is_sensitive_env_key

_EXPANDABLE_VALUE_PATTERN = re.compile(r"%[^%]+%")


class EnvironmentVariableBackend(Protocol):
    def list_values(
        self,
        scope: EnvironmentVariableScope,
    ) -> Sequence[EnvironmentVariableRecord]: ...

    def get_value(
        self,
        scope: EnvironmentVariableScope,
        key: str,
    ) -> EnvironmentVariableRecord | None: ...

    def set_value(
        self,
        scope: EnvironmentVariableScope,
        key: str,
        value: str,
        *,
        value_kind: EnvironmentVariableValueKind,
    ) -> None: ...

    def delete_value(self, scope: EnvironmentVariableScope, key: str) -> None: ...

    def broadcast_change(self) -> None: ...


class EnvironmentVariableService:
    def __init__(
        self,
        *,
        backend: EnvironmentVariableBackend | None = None,
        app_env_file_path: Path | None = None,
        on_app_env_changed: Callable[[frozenset[str]], None] | None = None,
    ) -> None:
        self._backend: EnvironmentVariableBackend = (
            ProcessEnvironmentVariableBackend() if backend is None else backend
        )
        self._app_env_file_path: Path = (
            get_app_env_file_path()
            if app_env_file_path is None
            else app_env_file_path.expanduser().resolve()
        )
        self._secret_store = get_secret_store()
        self._on_app_env_changed = on_app_env_changed or (lambda _changed_keys: None)

    def list_environment_variables(self) -> EnvironmentVariableCatalog:
        system_records = self._sort_records(
            self._backend.list_values(EnvironmentVariableScope.SYSTEM)
        )
        app_records = self._sort_records(self._load_app_records())
        return EnvironmentVariableCatalog(
            system=tuple(system_records),
            app=tuple(app_records),
        )

    def save_environment_variable(
        self,
        *,
        scope: EnvironmentVariableScope,
        key: str,
        request: EnvironmentVariableSaveRequest,
    ) -> EnvironmentVariableRecord:
        if scope == EnvironmentVariableScope.SYSTEM:
            raise ValueError("System environment variables are read-only.")
        normalized_key = _normalize_key(key)
        source_key = (
            normalized_key
            if request.source_key is None
            else _normalize_key(request.source_key)
        )
        app_records = self._build_app_record_map()
        plaintext_records = self._build_plaintext_app_record_map()
        target_existing = app_records.get(normalized_key)
        source_existing = app_records.get(source_key)
        is_rename = source_key != normalized_key

        if request.source_key is not None and source_existing is None:
            raise ValueError(f"Environment variable not found: {source_key}")
        if is_rename and target_existing is not None:
            raise ValueError(
                f"Environment variable already exists in {scope.value}: {normalized_key}"
            )

        value_kind = _resolve_value_kind(
            value=request.value,
            source_existing=source_existing,
            target_existing=target_existing,
        )
        next_record = EnvironmentVariableRecord(
            key=normalized_key,
            value=request.value,
            scope=EnvironmentVariableScope.APP,
            value_kind=value_kind,
        )
        if source_existing is not None:
            if is_sensitive_env_key(source_key):
                self._delete_secret_record(source_key)
            else:
                plaintext_records.pop(source_key, None)

        if is_sensitive_env_key(normalized_key):
            plaintext_records.pop(normalized_key, None)
            self._set_secret_record(next_record)
        else:
            self._delete_secret_record(normalized_key)
            plaintext_records[normalized_key] = next_record

        self._write_app_records(plaintext_records)
        changed_keys = {normalized_key}
        if source_existing is not None:
            changed_keys.add(source_key)
        self._on_app_env_changed(frozenset(changed_keys))
        return next_record

    def delete_environment_variable(
        self,
        *,
        scope: EnvironmentVariableScope,
        key: str,
    ) -> None:
        if scope == EnvironmentVariableScope.SYSTEM:
            raise ValueError("System environment variables are read-only.")
        normalized_key = _normalize_key(key)
        app_records = self._build_app_record_map()
        plaintext_records = self._build_plaintext_app_record_map()
        existing = app_records.get(normalized_key)
        if existing is None:
            raise ValueError(f"Environment variable not found in app: {normalized_key}")
        if is_sensitive_env_key(normalized_key):
            self._delete_secret_record(normalized_key)
        else:
            plaintext_records.pop(normalized_key, None)
        self._write_app_records(plaintext_records)
        self._on_app_env_changed(frozenset((normalized_key,)))

    def _sort_records(
        self,
        records: Sequence[EnvironmentVariableRecord],
    ) -> list[EnvironmentVariableRecord]:
        return sorted(records, key=lambda record: record.key.upper())

    def _load_app_records(self) -> tuple[EnvironmentVariableRecord, ...]:
        values = load_env_file(self._app_env_file_path)
        values.update(load_secret_env_vars(self._app_env_file_path.parent))
        records = [
            EnvironmentVariableRecord(
                key=key,
                value=value,
                scope=EnvironmentVariableScope.APP,
                value_kind=(
                    EnvironmentVariableValueKind.EXPANDABLE
                    if _EXPANDABLE_VALUE_PATTERN.search(value)
                    else EnvironmentVariableValueKind.STRING
                ),
            )
            for key, value in values.items()
        ]
        return tuple(records)

    def _build_app_record_map(self) -> dict[str, EnvironmentVariableRecord]:
        return {record.key: record for record in self._load_app_records()}

    def _build_plaintext_app_record_map(self) -> dict[str, EnvironmentVariableRecord]:
        values = load_env_file(self._app_env_file_path)
        return {
            key: EnvironmentVariableRecord(
                key=key,
                value=value,
                scope=EnvironmentVariableScope.APP,
                value_kind=(
                    EnvironmentVariableValueKind.EXPANDABLE
                    if _EXPANDABLE_VALUE_PATTERN.search(value)
                    else EnvironmentVariableValueKind.STRING
                ),
            )
            for key, value in values.items()
        }

    def _write_app_records(
        self,
        records_by_key: dict[str, EnvironmentVariableRecord],
    ) -> None:
        env_file_path = self._app_env_file_path
        written_keys: set[str] = set()
        output_lines: list[str] = []
        existing_lines: list[str] = []
        if env_file_path.exists() and env_file_path.is_file():
            existing_lines = env_file_path.read_text(encoding="utf-8").splitlines()

        for raw_line in existing_lines:
            stripped_line = raw_line.strip()
            if (
                not stripped_line
                or stripped_line.startswith("#")
                or "=" not in raw_line
            ):
                output_lines.append(raw_line)
                continue
            raw_key, _raw_value = raw_line.split("=", 1)
            normalized_key = raw_key.strip()
            record = records_by_key.get(normalized_key)
            if record is None:
                continue
            if normalized_key in written_keys:
                continue
            output_lines.append(
                f"{normalized_key}={_serialize_env_value(record.value)}"
            )
            written_keys.add(normalized_key)

        for key in sorted(records_by_key):
            if key in written_keys:
                continue
            output_lines.append(
                f"{key}={_serialize_env_value(records_by_key[key].value)}"
            )

        env_file_path.parent.mkdir(parents=True, exist_ok=True)
        serialized = "\n".join(output_lines)
        if serialized:
            serialized = f"{serialized}\n"
        env_file_path.write_text(serialized, encoding="utf-8")
        sync_app_env_to_process_env(env_file_path)

    def _set_secret_record(self, record: EnvironmentVariableRecord) -> None:
        self._secret_store.set_secret(
            self._app_env_file_path.parent,
            namespace="app_env",
            owner_id="app",
            field_name=record.key,
            value=record.value,
        )

    def _delete_secret_record(self, key: str) -> None:
        self._secret_store.delete_secret(
            self._app_env_file_path.parent,
            namespace="app_env",
            owner_id="app",
            field_name=key,
        )


class ProcessEnvironmentVariableBackend:
    def list_values(
        self,
        scope: EnvironmentVariableScope,
    ) -> Sequence[EnvironmentVariableRecord]:
        if scope != EnvironmentVariableScope.SYSTEM:
            return ()
        return tuple(
            EnvironmentVariableRecord(
                key=key,
                value=value,
                scope=EnvironmentVariableScope.SYSTEM,
                value_kind=(
                    EnvironmentVariableValueKind.EXPANDABLE
                    if _EXPANDABLE_VALUE_PATTERN.search(value)
                    else EnvironmentVariableValueKind.STRING
                ),
            )
            for key, value in os.environ.items()
        )

    def get_value(
        self,
        scope: EnvironmentVariableScope,
        key: str,
    ) -> EnvironmentVariableRecord | None:
        if scope != EnvironmentVariableScope.SYSTEM:
            return None
        value = os.environ.get(key)
        if value is None:
            return None
        return EnvironmentVariableRecord(
            key=key,
            value=value,
            scope=EnvironmentVariableScope.SYSTEM,
            value_kind=(
                EnvironmentVariableValueKind.EXPANDABLE
                if _EXPANDABLE_VALUE_PATTERN.search(value)
                else EnvironmentVariableValueKind.STRING
            ),
        )

    def set_value(
        self,
        scope: EnvironmentVariableScope,
        key: str,
        value: str,
        *,
        value_kind: EnvironmentVariableValueKind,
    ) -> None:
        _ = (scope, key, value, value_kind)
        raise PermissionError("System environment variables are read-only.")

    def delete_value(self, scope: EnvironmentVariableScope, key: str) -> None:
        _ = (scope, key)
        raise PermissionError("System environment variables are read-only.")

    def broadcast_change(self) -> None:
        return None


def _normalize_key(key: str) -> str:
    normalized = key.strip()
    if not normalized:
        raise ValueError("Environment variable key cannot be empty.")
    if "=" in normalized:
        raise ValueError("Environment variable key cannot contain '='.")
    if "\x00" in normalized:
        raise ValueError("Environment variable key cannot contain NUL bytes.")
    return normalized


def _resolve_value_kind(
    *,
    value: str,
    source_existing: EnvironmentVariableRecord | None,
    target_existing: EnvironmentVariableRecord | None,
) -> EnvironmentVariableValueKind:
    if source_existing is not None:
        return source_existing.value_kind
    if target_existing is not None:
        return target_existing.value_kind
    if _EXPANDABLE_VALUE_PATTERN.search(value):
        return EnvironmentVariableValueKind.EXPANDABLE
    return EnvironmentVariableValueKind.STRING


def _serialize_env_value(value: str) -> str:
    if any(character.isspace() for character in value) or "#" in value:
        escaped_value = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped_value}"'
    return value
