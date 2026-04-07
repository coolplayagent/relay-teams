# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from json import dumps, loads
from pathlib import Path
from typing import cast

try:
    import keyring
except Exception:  # pragma: no cover - import availability depends on environment
    keyring = None

from relay_teams.secrets.secret_models import (
    SecretCoordinate,
    SecretIndexDocument,
    SecretIndexEntry,
)
from pydantic import JsonValue

_KEYRING_SERVICE_NAME = "agent-teams"
_SECRETS_FILE_NAME = "secrets.json"
LOGGER = logging.getLogger(__name__)


class AppSecretStore:
    def get_secret(
        self,
        config_dir: Path,
        *,
        namespace: str,
        owner_id: str,
        field_name: str,
    ) -> str | None:
        coordinate = _normalize_coordinate(
            namespace=namespace,
            owner_id=owner_id,
            field_name=field_name,
        )
        index = self._load_index(config_dir)
        entry = _find_entry(index, coordinate)
        if entry is not None and entry.storage == "file":
            return entry.value
        if not self.has_usable_keyring_backend():
            return None
        return self._get_from_keyring(config_dir, coordinate)

    def set_secret(
        self,
        config_dir: Path,
        *,
        namespace: str,
        owner_id: str,
        field_name: str,
        value: str | None,
    ) -> None:
        coordinate = _normalize_coordinate(
            namespace=namespace,
            owner_id=owner_id,
            field_name=field_name,
        )
        if value is None:
            self.delete_secret(
                config_dir,
                namespace=coordinate.namespace,
                owner_id=coordinate.owner_id,
                field_name=coordinate.field_name,
            )
            return

        index = self._load_index(config_dir)
        if self.has_usable_keyring_backend():
            if self._try_set_in_keyring(config_dir, coordinate, value):
                index = _upsert_entry(
                    index,
                    SecretIndexEntry(
                        namespace=coordinate.namespace,
                        owner_id=coordinate.owner_id,
                        field_name=coordinate.field_name,
                        storage="keyring",
                    ),
                )
                self._save_index(config_dir, index)
                return
            index = _drop_entry(index, coordinate)
            index = _upsert_entry(
                index,
                SecretIndexEntry(
                    namespace=coordinate.namespace,
                    owner_id=coordinate.owner_id,
                    field_name=coordinate.field_name,
                    storage="file",
                    value=value,
                ),
            )
            self._save_index(config_dir, index)
            return

        index = _upsert_entry(
            index,
            SecretIndexEntry(
                namespace=coordinate.namespace,
                owner_id=coordinate.owner_id,
                field_name=coordinate.field_name,
                storage="file",
                value=value,
            ),
        )
        self._save_index(config_dir, index)

    def delete_secret(
        self,
        config_dir: Path,
        *,
        namespace: str,
        owner_id: str,
        field_name: str,
    ) -> None:
        coordinate = _normalize_coordinate(
            namespace=namespace,
            owner_id=owner_id,
            field_name=field_name,
        )
        index = self._load_index(config_dir)
        index = SecretIndexDocument(
            version=index.version,
            entries=tuple(
                entry for entry in index.entries if entry.coordinate() != coordinate
            ),
        )
        self._save_index(config_dir, index)
        if not self.has_usable_keyring_backend():
            return
        self._delete_from_keyring(config_dir, coordinate)

    def list_owner_fields(
        self,
        config_dir: Path,
        *,
        namespace: str,
        owner_id: str,
    ) -> tuple[str, ...]:
        normalized_namespace = namespace.strip()
        normalized_owner_id = owner_id.strip()
        index = self._load_index(config_dir)
        return tuple(
            entry.field_name
            for entry in index.entries
            if entry.namespace == normalized_namespace
            and entry.owner_id == normalized_owner_id
        )

    def get_owner_secrets(
        self,
        config_dir: Path,
        *,
        namespace: str,
        owner_id: str,
    ) -> dict[str, str]:
        secrets_by_field: dict[str, str] = {}
        for field_name in self.list_owner_fields(
            config_dir,
            namespace=namespace,
            owner_id=owner_id,
        ):
            value = self.get_secret(
                config_dir,
                namespace=namespace,
                owner_id=owner_id,
                field_name=field_name,
            )
            if value is None:
                continue
            secrets_by_field[field_name] = value
        return secrets_by_field

    def delete_owner(
        self,
        config_dir: Path,
        *,
        namespace: str,
        owner_id: str,
    ) -> None:
        for field_name in self.list_owner_fields(
            config_dir,
            namespace=namespace,
            owner_id=owner_id,
        ):
            self.delete_secret(
                config_dir,
                namespace=namespace,
                owner_id=owner_id,
                field_name=field_name,
            )

    def rename_owner(
        self,
        config_dir: Path,
        *,
        namespace: str,
        from_owner_id: str,
        to_owner_id: str,
    ) -> None:
        normalized_namespace = namespace.strip()
        normalized_from_owner_id = from_owner_id.strip()
        normalized_to_owner_id = to_owner_id.strip()
        if normalized_from_owner_id == normalized_to_owner_id:
            return
        index = self._load_index(config_dir)
        next_entries: list[SecretIndexEntry] = []
        pending_moves: list[SecretIndexEntry] = []
        for entry in index.entries:
            if (
                entry.namespace == normalized_namespace
                and entry.owner_id == normalized_from_owner_id
            ):
                pending_moves.append(entry)
                continue
            next_entries.append(entry)
        for entry in pending_moves:
            coordinate = SecretCoordinate(
                namespace=entry.namespace,
                owner_id=entry.owner_id,
                field_name=entry.field_name,
            )
            next_coordinate = SecretCoordinate(
                namespace=entry.namespace,
                owner_id=normalized_to_owner_id,
                field_name=entry.field_name,
            )
            if entry.storage == "keyring":
                value = self._get_from_keyring(config_dir, coordinate)
                if value is None:
                    LOGGER.warning(
                        "Preserving keyring secret mapping during owner rename because the secret value could not be read",
                        extra={
                            "namespace": entry.namespace,
                            "owner_id": entry.owner_id,
                            "field_name": entry.field_name,
                        },
                    )
                    next_entries.append(entry)
                    continue
                if self.has_usable_keyring_backend() and self._try_set_in_keyring(
                    config_dir, next_coordinate, value
                ):
                    next_entries.append(
                        SecretIndexEntry(
                            namespace=next_coordinate.namespace,
                            owner_id=next_coordinate.owner_id,
                            field_name=next_coordinate.field_name,
                            storage="keyring",
                        )
                    )
                else:
                    next_entries.append(
                        SecretIndexEntry(
                            namespace=next_coordinate.namespace,
                            owner_id=next_coordinate.owner_id,
                            field_name=next_coordinate.field_name,
                            storage="file",
                            value=value,
                        )
                    )
                if self.has_usable_keyring_backend():
                    self._delete_from_keyring(config_dir, coordinate)
                continue
            next_entries.append(
                SecretIndexEntry(
                    namespace=next_coordinate.namespace,
                    owner_id=next_coordinate.owner_id,
                    field_name=next_coordinate.field_name,
                    storage="file",
                    value=entry.value,
                )
            )
        self._save_index(
            config_dir,
            SecretIndexDocument(version=index.version, entries=tuple(next_entries)),
        )

    def migrate_legacy_secret(
        self,
        config_dir: Path,
        *,
        namespace: str,
        owner_id: str,
        field_name: str,
        value: str | None,
    ) -> bool:
        if value is None:
            return False
        existing = self.get_secret(
            config_dir,
            namespace=namespace,
            owner_id=owner_id,
            field_name=field_name,
        )
        if existing is not None:
            return False
        self.set_secret(
            config_dir,
            namespace=namespace,
            owner_id=owner_id,
            field_name=field_name,
            value=value,
        )
        return True

    def has_usable_keyring_backend(self) -> bool:
        backend = self._get_keyring_backend()
        if backend is None:
            return False
        try:
            return float(getattr(backend, "priority", 0.0)) > 0
        except (TypeError, ValueError):
            return False

    def secrets_file_path(self, config_dir: Path) -> Path:
        return config_dir.expanduser().resolve() / _SECRETS_FILE_NAME

    def _load_index(self, config_dir: Path) -> SecretIndexDocument:
        secrets_file = self.secrets_file_path(config_dir)
        if not secrets_file.exists() or not secrets_file.is_file():
            return SecretIndexDocument()
        try:
            payload = loads(secrets_file.read_text(encoding="utf-8"))
        except Exception:
            return SecretIndexDocument()
        try:
            return SecretIndexDocument.model_validate(payload)
        except Exception:
            return SecretIndexDocument()

    def _save_index(self, config_dir: Path, index: SecretIndexDocument) -> None:
        secrets_file = self.secrets_file_path(config_dir)
        secrets_file.parent.mkdir(parents=True, exist_ok=True)
        secrets_file.write_text(
            dumps(index.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _build_account_name(
        self,
        config_dir: Path,
        coordinate: SecretCoordinate,
    ) -> str:
        resolved_dir = config_dir.expanduser().resolve()
        return (
            f"{resolved_dir}::{coordinate.namespace}::"
            f"{coordinate.owner_id}::{coordinate.field_name}"
        )

    def _get_keyring_backend(self) -> object | None:
        if keyring is None:
            return None
        try:
            backend = keyring.get_keyring()
        except Exception:
            return None
        if backend is None:
            return None
        return backend

    def _get_from_keyring(
        self,
        config_dir: Path,
        coordinate: SecretCoordinate,
    ) -> str | None:
        if keyring is None:
            return None
        try:
            return keyring.get_password(
                _KEYRING_SERVICE_NAME,
                self._build_account_name(config_dir, coordinate),
            )
        except Exception:
            return None

    def _set_in_keyring(
        self,
        config_dir: Path,
        coordinate: SecretCoordinate,
        value: str,
    ) -> None:
        if keyring is None:
            raise RuntimeError("System keyring backend is unavailable.")
        try:
            keyring.set_password(
                _KEYRING_SERVICE_NAME,
                self._build_account_name(config_dir, coordinate),
                value,
            )
        except Exception as exc:
            raise RuntimeError(
                "Failed to persist secrets to the system keyring."
            ) from exc

    def _try_set_in_keyring(
        self,
        config_dir: Path,
        coordinate: SecretCoordinate,
        value: str,
    ) -> bool:
        try:
            self._set_in_keyring(config_dir, coordinate, value)
        except RuntimeError:
            payload = {
                "config_dir": str(config_dir.expanduser().resolve()),
                "namespace": coordinate.namespace,
                "owner_id": coordinate.owner_id,
                "field_name": coordinate.field_name,
            }
            try:
                from relay_teams.logger import log_event

                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="secret_store.keyring_write_failed",
                    message="Falling back to file-backed secret storage after keyring write failure",
                    payload=cast(dict[str, JsonValue], payload),
                )
            except Exception:
                LOGGER.warning(
                    "Falling back to file-backed secret storage after keyring write failure: %s",
                    payload,
                )
            return False
        return True

    def _delete_from_keyring(
        self,
        config_dir: Path,
        coordinate: SecretCoordinate,
    ) -> None:
        if keyring is None:
            return
        try:
            keyring.delete_password(
                _KEYRING_SERVICE_NAME,
                self._build_account_name(config_dir, coordinate),
            )
        except Exception:
            return


_SECRET_STORE = AppSecretStore()


def get_secret_store() -> AppSecretStore:
    return _SECRET_STORE


def _normalize_coordinate(
    *,
    namespace: str,
    owner_id: str,
    field_name: str,
) -> SecretCoordinate:
    normalized_namespace = namespace.strip()
    normalized_owner_id = owner_id.strip()
    normalized_field_name = field_name.strip()
    return SecretCoordinate(
        namespace=normalized_namespace,
        owner_id=normalized_owner_id,
        field_name=normalized_field_name,
    )


def _find_entry(
    index: SecretIndexDocument,
    coordinate: SecretCoordinate,
) -> SecretIndexEntry | None:
    for entry in index.entries:
        if entry.coordinate() == coordinate:
            return entry
    return None


def _upsert_entry(
    index: SecretIndexDocument,
    next_entry: SecretIndexEntry,
) -> SecretIndexDocument:
    entries: list[SecretIndexEntry] = []
    replaced = False
    for entry in index.entries:
        if entry.coordinate() == next_entry.coordinate():
            if not replaced:
                entries.append(next_entry)
                replaced = True
            continue
        entries.append(entry)
    if not replaced:
        entries.append(next_entry)
    entries.sort(key=lambda item: (item.namespace, item.owner_id, item.field_name))
    return SecretIndexDocument(version=index.version, entries=tuple(entries))


def _drop_entry(
    index: SecretIndexDocument,
    coordinate: SecretCoordinate,
) -> SecretIndexDocument:
    return SecretIndexDocument(
        version=index.version,
        entries=tuple(
            entry for entry in index.entries if entry.coordinate() != coordinate
        ),
    )
