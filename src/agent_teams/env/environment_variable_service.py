# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Sequence
import ctypes
from ctypes import wintypes
from contextlib import AbstractContextManager
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
    ) -> None:
        self._backend: EnvironmentVariableBackend = (
            WindowsRegistryEnvironmentVariableBackend() if backend is None else backend
        )

    def list_environment_variables(self) -> EnvironmentVariableCatalog:
        system_records = self._sort_records(
            self._backend.list_values(EnvironmentVariableScope.SYSTEM)
        )
        user_records = self._sort_records(
            self._backend.list_values(EnvironmentVariableScope.USER)
        )
        return EnvironmentVariableCatalog(
            system=tuple(system_records),
            user=tuple(user_records),
        )

    def save_environment_variable(
        self,
        *,
        scope: EnvironmentVariableScope,
        key: str,
        request: EnvironmentVariableSaveRequest,
    ) -> EnvironmentVariableRecord:
        normalized_key = _normalize_key(key)
        source_key = (
            normalized_key
            if request.source_key is None
            else _normalize_key(request.source_key)
        )
        target_existing = self._backend.get_value(scope, normalized_key)
        source_existing = self._backend.get_value(scope, source_key)
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
        self._backend.set_value(
            scope,
            normalized_key,
            request.value,
            value_kind=value_kind,
        )
        if is_rename and source_existing is not None:
            self._backend.delete_value(scope, source_key)
        self._backend.broadcast_change()
        return EnvironmentVariableRecord(
            key=normalized_key,
            value=request.value,
            scope=scope,
            value_kind=value_kind,
        )

    def delete_environment_variable(
        self,
        *,
        scope: EnvironmentVariableScope,
        key: str,
    ) -> None:
        normalized_key = _normalize_key(key)
        existing = self._backend.get_value(scope, normalized_key)
        if existing is None:
            raise ValueError(
                f"Environment variable not found in {scope.value}: {normalized_key}"
            )
        self._backend.delete_value(scope, normalized_key)
        self._backend.broadcast_change()

    def _sort_records(
        self,
        records: Sequence[EnvironmentVariableRecord],
    ) -> list[EnvironmentVariableRecord]:
        return sorted(records, key=lambda record: record.key.upper())


class WindowsRegistryEnvironmentVariableBackend:
    def list_values(
        self,
        scope: EnvironmentVariableScope,
    ) -> Sequence[EnvironmentVariableRecord]:
        registry_module = _require_winreg()
        root_key, sub_key = _resolve_registry_location(scope)
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
                    key, value, raw_type = registry_module.EnumValue(
                        registry_key, index
                    )
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
                        scope=scope,
                        value_kind=value_kind,
                    )
                )
        return tuple(values)

    def get_value(
        self,
        scope: EnvironmentVariableScope,
        key: str,
    ) -> EnvironmentVariableRecord | None:
        registry_module = _require_winreg()
        root_key, sub_key = _resolve_registry_location(scope)
        try:
            with registry_module.OpenKey(
                root_key,
                sub_key,
                0,
                registry_module.KEY_READ,
            ) as registry_key:
                value, raw_type = registry_module.QueryValueEx(registry_key, key)
        except FileNotFoundError:
            return None
        if not isinstance(value, str):
            return None
        value_kind = _registry_type_to_value_kind(raw_type)
        if value_kind is None:
            return None
        return EnvironmentVariableRecord(
            key=key,
            value=value,
            scope=scope,
            value_kind=value_kind,
        )

    def set_value(
        self,
        scope: EnvironmentVariableScope,
        key: str,
        value: str,
        *,
        value_kind: EnvironmentVariableValueKind,
    ) -> None:
        registry_module = _require_winreg()
        root_key, sub_key = _resolve_registry_location(scope)
        raw_type = _value_kind_to_registry_type(value_kind)
        access = registry_module.KEY_SET_VALUE | registry_module.KEY_QUERY_VALUE
        with registry_module.OpenKey(root_key, sub_key, 0, access) as registry_key:
            registry_module.SetValueEx(registry_key, key, 0, raw_type, value)

    def delete_value(self, scope: EnvironmentVariableScope, key: str) -> None:
        registry_module = _require_winreg()
        root_key, sub_key = _resolve_registry_location(scope)
        with registry_module.OpenKey(
            root_key,
            sub_key,
            0,
            registry_module.KEY_SET_VALUE,
        ) as registry_key:
            registry_module.DeleteValue(registry_key, key)

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
    if scope == EnvironmentVariableScope.SYSTEM:
        return registry_module.HKEY_LOCAL_MACHINE, _SYSTEM_ENV_REGISTRY_PATH
    return registry_module.HKEY_CURRENT_USER, _USER_ENV_REGISTRY_PATH


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
