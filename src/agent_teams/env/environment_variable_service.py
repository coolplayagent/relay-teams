# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Sequence
import ctypes
from ctypes import wintypes
from contextlib import AbstractContextManager
from pathlib import Path
import re
import sys
from typing import Protocol, cast

from agent_teams.env.environment_variable_models import (
    EnvironmentVariableCatalog,
    EnvironmentVariableRecord,
    EnvironmentVariableSaveRequest,
    EnvironmentVariableScope,
    EnvironmentVariableValueKind,
)
from agent_teams.env.runtime_env import get_app_env_file_path, load_env_file

try:
    import winreg as _winreg
except ImportError:  # pragma: no cover - only exercised on non-Windows systems.
    _winreg = None


_EXPANDABLE_VALUE_PATTERN = re.compile(r"%[^%]+%")
_SYSTEM_ENV_REGISTRY_PATH = (
    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
)
_USER_ENV_REGISTRY_PATH = r"Environment"
_HWND_BROADCAST = 0xFFFF
_WM_SETTINGCHANGE = 0x001A
_SMTO_ABORTIFHUNG = 0x0002


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


class WinRegModuleProtocol(Protocol):
    HKEY_LOCAL_MACHINE: int
    HKEY_CURRENT_USER: int
    KEY_READ: int
    KEY_SET_VALUE: int
    KEY_QUERY_VALUE: int
    REG_EXPAND_SZ: int
    REG_SZ: int

    def OpenKey(
        self,
        key: int,
        sub_key: str,
        reserved: int = 0,
        access: int = 0,
    ) -> AbstractContextManager[object]: ...

    def EnumValue(self, key: object, index: int) -> tuple[str, object, int]: ...

    def QueryValueEx(self, key: object, value_name: str) -> tuple[object, int]: ...

    def SetValueEx(
        self,
        key: object,
        value_name: str,
        reserved: int,
        value_type: int,
        value: str,
    ) -> None: ...

    def DeleteValue(self, key: object, value_name: str) -> None: ...


class EnvironmentVariableService:
    def __init__(
        self,
        *,
        backend: EnvironmentVariableBackend | None = None,
        app_env_file_path: Path | None = None,
    ) -> None:
        self._backend: EnvironmentVariableBackend = (
            WindowsRegistryEnvironmentVariableBackend() if backend is None else backend
        )
        self._app_env_file_path: Path = (
            get_app_env_file_path()
            if app_env_file_path is None
            else app_env_file_path.expanduser().resolve()
        )

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
        app_records[normalized_key] = EnvironmentVariableRecord(
            key=normalized_key,
            value=request.value,
            scope=EnvironmentVariableScope.APP,
            value_kind=value_kind,
        )
        if is_rename and source_existing is not None:
            app_records.pop(source_key, None)
        self._write_app_records(app_records)
        return EnvironmentVariableRecord(
            key=normalized_key,
            value=request.value,
            scope=EnvironmentVariableScope.APP,
            value_kind=value_kind,
        )

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
        existing = app_records.get(normalized_key)
        if existing is None:
            raise ValueError(f"Environment variable not found in app: {normalized_key}")
        app_records.pop(normalized_key, None)
        self._write_app_records(app_records)

    def _sort_records(
        self,
        records: Sequence[EnvironmentVariableRecord],
    ) -> list[EnvironmentVariableRecord]:
        return sorted(records, key=lambda record: record.key.upper())

    def _load_app_records(self) -> tuple[EnvironmentVariableRecord, ...]:
        values = load_env_file(self._app_env_file_path)
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


class WindowsRegistryEnvironmentVariableBackend:
    def list_values(
        self,
        scope: EnvironmentVariableScope,
    ) -> Sequence[EnvironmentVariableRecord]:
        registry_module = _require_winreg()
        if scope != EnvironmentVariableScope.SYSTEM:
            return ()
        system_values = _read_registry_values(
            registry_module,
            registry_module.HKEY_LOCAL_MACHINE,
            _SYSTEM_ENV_REGISTRY_PATH,
        )
        user_values = _read_registry_values(
            registry_module,
            registry_module.HKEY_CURRENT_USER,
            _USER_ENV_REGISTRY_PATH,
        )
        merged_values: dict[str, EnvironmentVariableRecord] = {
            record.key: record for record in system_values
        }
        for record in user_values:
            merged_values[record.key] = EnvironmentVariableRecord(
                key=record.key,
                value=record.value,
                scope=EnvironmentVariableScope.SYSTEM,
                value_kind=record.value_kind,
            )
        return tuple(merged_values.values())

    def get_value(
        self,
        scope: EnvironmentVariableScope,
        key: str,
    ) -> EnvironmentVariableRecord | None:
        if scope != EnvironmentVariableScope.SYSTEM:
            return None
        for record in self.list_values(scope):
            if record.key == key:
                return record
        return None

    def set_value(
        self,
        scope: EnvironmentVariableScope,
        key: str,
        value: str,
        *,
        value_kind: EnvironmentVariableValueKind,
    ) -> None:
        raise PermissionError("System environment variables are read-only.")

    def delete_value(self, scope: EnvironmentVariableScope, key: str) -> None:
        raise PermissionError("System environment variables are read-only.")

    def broadcast_change(self) -> None:
        _ = _require_winreg()
        send_message_timeout = ctypes.windll.user32.SendMessageTimeoutW
        send_message_timeout.argtypes = (
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPCWSTR,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.POINTER(wintypes.DWORD),
        )
        send_message_timeout.restype = wintypes.LPARAM
        result = wintypes.DWORD(0)
        send_message_timeout(
            _HWND_BROADCAST,
            _WM_SETTINGCHANGE,
            0,
            "Environment",
            _SMTO_ABORTIFHUNG,
            5000,
            ctypes.byref(result),
        )


def _read_registry_values(
    registry_module: WinRegModuleProtocol,
    root_key: int,
    sub_key: str,
) -> tuple[EnvironmentVariableRecord, ...]:
    values: list[EnvironmentVariableRecord] = []
    with registry_module.OpenKey(
        root_key,
        sub_key,
        0,
        registry_module.KEY_READ,
    ) as registry_key:
        index = 0
        while True:
            try:
                key, value, raw_type = registry_module.EnumValue(registry_key, index)
            except OSError:
                break
            index += 1
            if not isinstance(value, str):
                continue
            value_kind = _registry_type_to_value_kind(raw_type)
            if value_kind is None:
                continue
            values.append(
                EnvironmentVariableRecord(
                    key=key,
                    value=value,
                    scope=EnvironmentVariableScope.SYSTEM,
                    value_kind=value_kind,
                )
            )
    return tuple(values)


def _normalize_key(key: str) -> str:
    normalized = key.strip()
    if not normalized:
        raise ValueError("Environment variable key cannot be empty.")
    if "=" in normalized:
        raise ValueError("Environment variable key cannot contain '='.")
    if "\x00" in normalized:
        raise ValueError("Environment variable key cannot contain NUL bytes.")
    return normalized


def _require_winreg() -> WinRegModuleProtocol:
    if sys.platform != "win32" or _winreg is None:
        raise RuntimeError(
            "Environment variable settings are only supported on Windows."
        )
    return cast(WinRegModuleProtocol, _winreg)


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


def _resolve_registry_location(
    scope: EnvironmentVariableScope,
) -> tuple[int, str]:
    registry_module = _require_winreg()
    if scope != EnvironmentVariableScope.SYSTEM:
        raise ValueError(f"Unsupported registry scope: {scope.value}")
    return registry_module.HKEY_LOCAL_MACHINE, _SYSTEM_ENV_REGISTRY_PATH


def _registry_type_to_value_kind(
    raw_type: int,
) -> EnvironmentVariableValueKind | None:
    registry_module = _require_winreg()
    if raw_type == registry_module.REG_EXPAND_SZ:
        return EnvironmentVariableValueKind.EXPANDABLE
    if raw_type == registry_module.REG_SZ:
        return EnvironmentVariableValueKind.STRING
    return None


def _value_kind_to_registry_type(value_kind: EnvironmentVariableValueKind) -> int:
    registry_module = _require_winreg()
    if value_kind == EnvironmentVariableValueKind.EXPANDABLE:
        return registry_module.REG_EXPAND_SZ
    return registry_module.REG_SZ


def _serialize_env_value(value: str) -> str:
    if any(character.isspace() for character in value) or "#" in value:
        escaped_value = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped_value}"'
    return value
