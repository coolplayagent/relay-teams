# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol, Tuple
from uuid import uuid4

from relay_teams.gateway.xiaoluban.account_repository import XiaolubanAccountRepository
from relay_teams.gateway.xiaoluban.client import XiaolubanClient
from relay_teams.gateway.xiaoluban.models import (
    DEFAULT_XIAOLUBAN_BASE_URL,
    XiaolubanAccountCreateInput,
    XiaolubanAccountRecord,
    XiaolubanAccountStatus,
    XiaolubanAccountUpdateInput,
    XiaolubanSecretStatus,
)
from relay_teams.gateway.xiaoluban.secret_store import (
    XiaolubanSecretStore,
    get_xiaoluban_secret_store,
)
from relay_teams.validation import require_force_delete


class WorkspaceLookup(Protocol):
    def get_workspace(self, workspace_id: str) -> object: ...


class XiaolubanGatewayService:
    def __init__(
        self,
        *,
        config_dir: Path,
        repository: XiaolubanAccountRepository,
        secret_store: Optional[XiaolubanSecretStore] = None,
        client: Optional[XiaolubanClient] = None,
        workspace_lookup: Optional[WorkspaceLookup] = None,
    ) -> None:
        self._config_dir = config_dir
        self._repository = repository
        self._secret_store = (
            get_xiaoluban_secret_store() if secret_store is None else secret_store
        )
        self._client = XiaolubanClient() if client is None else client
        self._workspace_lookup = workspace_lookup

    def list_accounts(self) -> Tuple[XiaolubanAccountRecord, ...]:
        return tuple(
            self._with_secret_status(item) for item in self._repository.list_accounts()
        )

    def get_account(self, account_id: str) -> XiaolubanAccountRecord:
        return self._with_secret_status(self._repository.get_account(account_id))

    def create_account(
        self,
        request: XiaolubanAccountCreateInput,
    ) -> XiaolubanAccountRecord:
        self._validate_notification_workspaces(request.notification_workspace_ids)
        normalized_token = _validate_token(request.token)
        derived_uid = derive_uid_from_token(normalized_token)
        now = datetime.now(tz=timezone.utc)
        record = XiaolubanAccountRecord(
            account_id=f"xlb_{uuid4().hex[:12]}",
            display_name=request.display_name,
            base_url=_normalize_base_url(request.base_url),
            status=(
                XiaolubanAccountStatus.ENABLED
                if request.enabled
                else XiaolubanAccountStatus.DISABLED
            ),
            derived_uid=derived_uid,
            notification_workspace_ids=request.notification_workspace_ids,
            notification_receiver=request.notification_receiver,
            created_at=now,
            updated_at=now,
        )
        self._secret_store.set_token(
            self._config_dir, record.account_id, normalized_token
        )
        saved = self._repository.upsert_account(record)
        return self._with_secret_status(saved)

    def update_account(
        self,
        account_id: str,
        request: XiaolubanAccountUpdateInput,
    ) -> XiaolubanAccountRecord:
        existing = self._repository.get_account(account_id)
        notification_workspace_ids = (
            existing.notification_workspace_ids
            if request.notification_workspace_ids is None
            else request.notification_workspace_ids
        )
        if request.notification_workspace_ids is not None:
            self._validate_notification_workspaces(request.notification_workspace_ids)
        token = None
        derived_uid = existing.derived_uid
        if request.token is not None:
            token = _validate_token(request.token)
            derived_uid = derive_uid_from_token(token)
        updated = existing.model_copy(
            update={
                "display_name": request.display_name or existing.display_name,
                "base_url": (
                    _normalize_base_url(request.base_url)
                    if request.base_url is not None
                    else existing.base_url
                ),
                "status": (
                    existing.status
                    if request.enabled is None
                    else (
                        XiaolubanAccountStatus.ENABLED
                        if request.enabled
                        else XiaolubanAccountStatus.DISABLED
                    )
                ),
                "derived_uid": derived_uid,
                "notification_workspace_ids": notification_workspace_ids,
                "notification_receiver": (
                    request.notification_receiver
                    if "notification_receiver" in request.model_fields_set
                    else existing.notification_receiver
                ),
                "updated_at": datetime.now(tz=timezone.utc),
            }
        )
        if token is not None:
            self._secret_store.set_token(self._config_dir, account_id, token)
        saved = self._repository.upsert_account(updated)
        return self._with_secret_status(saved)

    def set_account_enabled(
        self,
        account_id: str,
        enabled: bool,
    ) -> XiaolubanAccountRecord:
        return self.update_account(
            account_id,
            XiaolubanAccountUpdateInput(enabled=enabled),
        )

    def delete_account(self, account_id: str, *, force: bool = False) -> None:
        account = self._repository.get_account(account_id)
        if account.status == XiaolubanAccountStatus.ENABLED:
            require_force_delete(
                force,
                message="Cannot delete enabled Xiaoluban account without force",
            )
        self._secret_store.delete_token(self._config_dir, account_id)
        self._repository.delete_account(account_id)

    def send_text_message(
        self,
        *,
        account_id: str,
        text: str,
        receiver_uid: Optional[str] = None,
    ) -> str:
        account = self._repository.get_account(account_id)
        if account.status != XiaolubanAccountStatus.ENABLED:
            raise RuntimeError("xiaoluban_account_disabled")
        token = self._secret_store.get_token(self._config_dir, account_id)
        if token is None:
            raise RuntimeError("missing_xiaoluban_token")
        response = self._client.send_text_message(
            text=text,
            receiver_uid=(
                receiver_uid or account.notification_receiver or account.derived_uid
            ).strip()
            or account.derived_uid,
            auth_token=token,
            base_url=account.base_url,
        )
        return response.message_id

    def has_usable_credentials(self, account_id: str) -> bool:
        try:
            account = self._repository.get_account(account_id)
        except KeyError:
            return False
        return (
            account.status == XiaolubanAccountStatus.ENABLED
            and self._secret_store.get_token(self._config_dir, account_id) is not None
        )

    def _with_secret_status(
        self,
        account: XiaolubanAccountRecord,
    ) -> XiaolubanAccountRecord:
        return account.model_copy(
            update={
                "secret_status": XiaolubanSecretStatus(
                    token_configured=(
                        self._secret_store.get_token(
                            self._config_dir, account.account_id
                        )
                        is not None
                    )
                )
            }
        )

    def _validate_notification_workspaces(self, workspace_ids: tuple[str, ...]) -> None:
        if self._workspace_lookup is None:
            return
        for workspace_id in workspace_ids:
            try:
                _ = self._workspace_lookup.get_workspace(workspace_id)
            except KeyError as exc:
                raise ValueError(
                    f"Unknown notification workspace: {workspace_id}"
                ) from exc


def derive_uid_from_token(token: str) -> str:
    normalized = _validate_token(token)
    prefix, _separator, _suffix = normalized.partition("_")
    return prefix


def _normalize_base_url(value: Optional[str]) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return DEFAULT_XIAOLUBAN_BASE_URL
    return normalized


def _validate_token(token: str) -> str:
    normalized = str(token).strip()
    if not normalized:
        raise ValueError("token must not be empty")
    if normalized.startswith("p_"):
        raise ValueError("token must be a personal Xiaoluban token")
    prefix, separator, suffix = normalized.partition("_")
    if not separator or not prefix or len(suffix) != 32:
        raise ValueError("token format is invalid")
    return normalized


__all__ = ["XiaolubanGatewayService", "derive_uid_from_token"]
