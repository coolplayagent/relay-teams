# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, Optional, Protocol, Tuple, cast
from uuid import uuid4

from relay_teams.gateway.gateway_models import GatewayChannelType
from relay_teams.gateway.gateway_session_service import GatewaySessionService
from relay_teams.gateway.session_ingress_service import (
    GatewaySessionIngressBusyPolicy,
    GatewaySessionIngressRequest,
    GatewaySessionIngressService,
)
from relay_teams.gateway.xiaoluban.account_repository import XiaolubanAccountRepository
from relay_teams.gateway.xiaoluban.client import XiaolubanClient
from relay_teams.gateway.xiaoluban.models import (
    DEFAULT_XIAOLUBAN_BASE_URL,
    XIAOLUBAN_PLATFORM,
    XiaolubanAccountCreateInput,
    XiaolubanAccountRecord,
    XiaolubanAccountStatus,
    XiaolubanAccountUpdateInput,
    XiaolubanImConfig,
    XiaolubanImConfigUpdateInput,
    XiaolubanInboundMessage,
    XiaolubanSecretStatus,
)
from relay_teams.gateway.xiaoluban.notification_format import (
    format_xiaoluban_notification_text,
)
from relay_teams.gateway.xiaoluban.secret_store import (
    XiaolubanSecretStore,
    get_xiaoluban_secret_store,
)
from relay_teams.logger import get_logger, log_event
from relay_teams.media import content_parts_from_text
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.run_models import (
    IntentInput,
    RuntimePromptConversationContext,
)
from relay_teams.sessions.runs.run_service import SessionRunService
from relay_teams.sessions.runs.terminal_payload import (
    extract_terminal_error,
    extract_terminal_output,
    parse_terminal_payload_json,
)
from relay_teams.validation import require_force_delete

LOGGER = get_logger(__name__)
_IM_REPLY_POLL_INTERVAL_SECONDS = 2.0
_IM_TERMINAL_SUPPRESSION_TTL_SECONDS = 24 * 60 * 60


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
        gateway_session_service: Optional[GatewaySessionService] = None,
        run_service: Optional[SessionRunService] = None,
        event_log: Optional[EventLog] = None,
        session_ingress_service: Optional[GatewaySessionIngressService] = None,
    ) -> None:
        self._config_dir = config_dir
        self._repository = repository
        self._secret_store = (
            get_xiaoluban_secret_store() if secret_store is None else secret_store
        )
        self._client = XiaolubanClient() if client is None else client
        self._workspace_lookup = workspace_lookup
        self._gateway_session_service = gateway_session_service
        self._run_service = run_service
        self._event_log = event_log
        self._session_ingress_service = session_ingress_service
        self._im_terminal_suppressed_run_ids: dict[str, float] = {}
        self._im_terminal_suppression_lock = threading.Lock()
        self._pending_im_replies: dict[str, _IMReplyContext] = {}
        self._pending_im_replies_lock = threading.Lock()
        self._im_poller_started = False

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
        if "im_config" in request.model_fields_set:
            im_config = self._resolve_im_config(
                request.im_config,
                requires_workspace_id=True,
            )
        else:
            im_config = _default_im_config()
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
            im_config=im_config,
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
        im_config = existing.im_config
        if "im_config" in request.model_fields_set:
            im_config = self._resolve_im_config(
                request.im_config,
                requires_workspace_id=True,
            )
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
                "im_config": im_config,
                "updated_at": datetime.now(tz=timezone.utc),
            }
        )
        if token is not None:
            self._secret_store.set_token(self._config_dir, account_id, token)
        saved = self._repository.upsert_account(updated)
        return self._with_secret_status(saved)

    def update_im_config(
        self,
        account_id: str,
        request: XiaolubanImConfigUpdateInput,
    ) -> XiaolubanAccountRecord:
        existing = self._repository.get_account(account_id)
        workspace_id = request.workspace_id
        if workspace_id is None:
            raise ValueError("workspace_id is required for Xiaoluban IM")
        self._validate_im_workspace(workspace_id)
        im_config = existing.im_config.model_copy(
            update={
                "workspace_id": workspace_id,
            }
        )
        saved = self._repository.upsert_account(
            existing.model_copy(
                update={
                    "im_config": im_config,
                    "updated_at": datetime.now(tz=timezone.utc),
                }
            )
        )
        return self._with_secret_status(saved)

    def handle_im_inbound(
        self,
        *,
        account_id: str,
        message: XiaolubanInboundMessage,
    ) -> None:
        try:
            self._handle_im_inbound(account_id, message)
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="gateway.xiaoluban.im_inbound.failed",
                message="Failed to handle Xiaoluban IM inbound message",
                payload={
                    "account_id": account_id,
                    "error": str(exc),
                },
            )
            try:
                workspace_id = ""
                session_id = message.session_id
                try:
                    account = self._repository.get_account(account_id)
                    workspace_id = str(account.im_config.workspace_id or "")
                except KeyError as lookup_exc:
                    log_event(
                        LOGGER,
                        logging.WARNING,
                        event="gateway.xiaoluban.im_inbound.account_lookup_failed",
                        message="Falling back to empty workspace_id after account lookup failure",
                        payload={
                            "account_id": account_id,
                            "error": str(lookup_exc),
                        },
                    )
                self.send_notification_message(
                    account_id=account_id,
                    workspace_id=workspace_id,
                    session_id=session_id,
                    status="failed",
                    body=f"处理失败：{exc}",
                    receiver_uid=message.receiver or message.sender or None,
                )
            except Exception as notify_exc:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="gateway.xiaoluban.im_inbound.failure_notification_failed",
                    message="Failed to send Xiaoluban IM failure notification",
                    payload={
                        "account_id": account_id,
                        "session_id": message.session_id,
                        "error": str(notify_exc),
                    },
                )

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

    def send_notification_message(
        self,
        *,
        account_id: str,
        workspace_id: str,
        session_id: str,
        status: str,
        body: str,
        receiver_uid: Optional[str] = None,
    ) -> str:
        text = format_xiaoluban_notification_text(
            workspace_id=workspace_id,
            session_id=session_id,
            status=status,
            body=body,
        )
        return self.send_text_message(
            account_id=account_id,
            text=text,
            receiver_uid=receiver_uid,
        )

    def has_usable_credentials(self, account_id: str) -> bool:
        try:
            account = self._repository.get_account(account_id)
        except KeyError:
            return False
        return (
            account.status == XiaolubanAccountStatus.ENABLED
            and self._secret_store.get_token(self._config_dir, account_id) is not None
        )

    def should_suppress_xiaoluban_terminal_notification(
        self, run_id: Optional[str]
    ) -> bool:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return False
        self._cleanup_im_terminal_suppression()
        with self._im_terminal_suppression_lock:
            return normalized_run_id in self._im_terminal_suppressed_run_ids

    # NOTE: Xiaoluban IM forwarding URLs have a platform-enforced length limit.
    # Adding ?auth= pushes URLs past that limit, breaking the forwarding command.
    # DO NOT add auth tokens to the callback URL — this is intentionally omitted.
    def get_im_callback_auth_token(self, account_id: str) -> str:
        account = self._repository.get_account(account_id)
        if account.status != XiaolubanAccountStatus.ENABLED:
            raise RuntimeError("xiaoluban_account_disabled")
        token = self._secret_store.get_token(self._config_dir, account_id)
        if token is None:
            raise RuntimeError("missing_xiaoluban_token")
        return token

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

    def validate_im_workspace(self, workspace_id: str) -> None:
        self._validate_im_workspace(workspace_id)

    def _validate_im_workspace(self, workspace_id: str) -> None:
        if self._workspace_lookup is None:
            return
        try:
            _ = self._workspace_lookup.get_workspace(workspace_id)
        except KeyError as exc:
            raise ValueError(f"Unknown IM workspace: {workspace_id}") from exc

    def _resolve_im_config(
        self,
        request_im_config: XiaolubanImConfig | None,
        *,
        requires_workspace_id: bool,
    ) -> XiaolubanImConfig:
        if request_im_config is None:
            if requires_workspace_id:
                raise ValueError("workspace_id is required for Xiaoluban IM")
            return _default_im_config()
        workspace_id = request_im_config.workspace_id
        if workspace_id is None:
            raise ValueError("workspace_id is required for Xiaoluban IM")
        self._validate_im_workspace(workspace_id)
        return request_im_config.model_copy(update={"workspace_id": workspace_id})

    def _handle_im_inbound(
        self,
        account_id: str,
        message: XiaolubanInboundMessage,
    ) -> None:
        if self._gateway_session_service is None:
            raise RuntimeError("xiaoluban_im_runtime_unavailable")
        if self._run_service is None and self._session_ingress_service is None:
            raise RuntimeError("xiaoluban_im_runtime_unavailable")
        account = self._repository.get_account(account_id)
        if account.status != XiaolubanAccountStatus.ENABLED:
            raise RuntimeError("xiaoluban_account_disabled")
        workspace_id = account.im_config.workspace_id
        if workspace_id is None:
            raise RuntimeError("xiaoluban_im_workspace_missing")
        self._validate_im_workspace(workspace_id)
        token = self._secret_store.get_token(self._config_dir, account_id)
        if token is None:
            raise RuntimeError("missing_xiaoluban_token")
        self._keep_alive_if_possible(account, token, message)
        reply_target = message.receiver or message.sender or account.derived_uid
        text = _extract_im_text(message.content)
        if not text:
            self.send_notification_message(
                account_id=account_id,
                workspace_id=workspace_id,
                session_id=message.session_id
                or _external_session_id(
                    account_id=account_id,
                    workspace_id=workspace_id,
                    message=message,
                ),
                status="input_required",
                body="请输入任务内容，例如：帮我看一下这个项目",
                receiver_uid=reply_target,
            )
            return
        external_session_id = _external_session_id(
            account_id=account_id,
            workspace_id=workspace_id,
            message=message,
        )
        log_event(
            LOGGER,
            logging.INFO,
            event="gateway.xiaoluban.im_inbound.session_resolve",
            message=(
                "Resolving Xiaoluban IM gateway session: "
                f"account_id={account_id} "
                f"workspace_id={workspace_id} "
                f"external_session_id={external_session_id}"
            ),
            payload={
                "account_id": account_id,
                "workspace_id": workspace_id,
                "external_session_id": external_session_id,
            },
        )
        gateway_session = self._gateway_session_service.resolve_or_create_session(
            channel_type=GatewayChannelType.XIAOLUBAN,
            external_session_id=external_session_id,
            workspace_id=workspace_id,
            metadata={
                "source_provider": XIAOLUBAN_PLATFORM,
                "source_kind": "im",
                "xiaoluban_account_id": account_id,
            },
            cwd=None,
            capabilities={},
            channel_state={
                "account_id": account_id,
                "receiver": message.receiver,
                "sender": message.sender,
                "xiaoluban_session_id": message.session_id,
            },
            peer_user_id=message.sender or None,
            peer_chat_id=message.receiver or None,
        )
        log_event(
            LOGGER,
            logging.INFO,
            event="gateway.xiaoluban.im_inbound.session_resolved",
            message=(
                "Resolved Xiaoluban IM gateway session: "
                f"gateway_session_id={gateway_session.gateway_session_id} "
                f"internal_session_id={gateway_session.internal_session_id} "
                f"external_session_id={external_session_id}"
            ),
            payload={
                "gateway_session_id": gateway_session.gateway_session_id,
                "internal_session_id": gateway_session.internal_session_id,
                "external_session_id": external_session_id,
            },
        )
        if self._active_run_id(gateway_session.internal_session_id) is not None:
            self.send_notification_message(
                account_id=account_id,
                workspace_id=workspace_id,
                session_id=gateway_session.internal_session_id,
                status="busy",
                body="当前会话已有任务运行，请稍后再试。",
                receiver_uid=reply_target,
            )
            return
        intent = IntentInput(
            session_id=gateway_session.internal_session_id,
            input=content_parts_from_text(text),
            yolo=True,
            conversation_context=RuntimePromptConversationContext(
                source_provider=XIAOLUBAN_PLATFORM,
                source_kind="im",
            ),
        )
        run_id = self._start_im_run(intent)
        if run_id is None:
            self.send_notification_message(
                account_id=account_id,
                workspace_id=workspace_id,
                session_id=gateway_session.internal_session_id,
                status="busy",
                body="当前会话已有任务运行，请稍后再试。",
                receiver_uid=reply_target,
            )
            return
        self._mark_im_terminal_notification_suppressed(run_id)
        self._gateway_session_service.bind_active_run(
            gateway_session.gateway_session_id,
            run_id,
        )
        log_event(
            LOGGER,
            logging.INFO,
            event="gateway.xiaoluban.im_inbound.run_started",
            message=(
                "Started Xiaoluban IM run: "
                f"run_id={run_id} "
                f"internal_session_id={gateway_session.internal_session_id} "
                f"workspace_id={workspace_id}"
            ),
            payload={
                "run_id": run_id,
                "internal_session_id": gateway_session.internal_session_id,
                "workspace_id": workspace_id,
            },
        )
        self._ensure_poller_started()
        self._register_im_reply(
            account_id=account_id,
            gateway_session_id=gateway_session.gateway_session_id,
            workspace_id=workspace_id,
            session_id=gateway_session.internal_session_id,
            run_id=run_id,
            reply_target=reply_target,
        )

    def _keep_alive_if_possible(
        self,
        account: XiaolubanAccountRecord,
        token: str,
        message: XiaolubanInboundMessage,
    ) -> None:
        if not message.receiver or not message.session_id:
            return
        try:
            self._client.keep_alive(
                uid=message.receiver,
                session_id=message.session_id,
                auth_token=token,
                base_url=account.base_url,
                timeout_minutes=1440,
            )
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="gateway.xiaoluban.keep_alive.failed",
                message="Failed to keep Xiaoluban IM session alive",
                payload={"account_id": account.account_id, "error": str(exc)},
            )

    def _start_im_run(self, intent: IntentInput) -> str | None:
        if self._session_ingress_service is not None:
            result = self._session_ingress_service.submit(
                GatewaySessionIngressRequest(
                    intent=intent,
                    busy_policy=GatewaySessionIngressBusyPolicy.REJECT_IF_BUSY,
                )
            )
            if result.run_id is None:
                return None
            return result.run_id
        run_service = self._run_service
        if run_service is None:
            raise RuntimeError("xiaoluban_im_runtime_unavailable")
        create_detached_run = getattr(run_service, "create_detached_run", None)
        if callable(create_detached_run):
            run_id, _ = cast(_CreateRun, create_detached_run)(intent)
        else:
            run_id, _ = run_service.create_run(intent)
        run_service.ensure_run_started(run_id)
        return run_id

    def _active_run_id(self, session_id: str) -> str | None:
        if self._session_ingress_service is None:
            return None
        return self._session_ingress_service.active_run_id(session_id)

    def _ensure_poller_started(self) -> None:
        if self._im_poller_started:
            return
        with self._pending_im_replies_lock:
            if self._im_poller_started:
                return
            self._im_poller_started = True
        threading.Thread(
            target=self._im_reply_poller_loop,
            name="xiaoluban-im-reply-poller",
            daemon=True,
        ).start()

    def _im_reply_poller_loop(self) -> None:
        while True:
            time.sleep(_IM_REPLY_POLL_INTERVAL_SECONDS)
            try:
                self._drain_im_replies()
            except (
                RuntimeError,
                OSError,
                KeyError,
                ValueError,
                AttributeError,
                TypeError,
                IndexError,
            ):
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="gateway.xiaoluban.im_reply.poller_error",
                    message="IM reply poller iteration failed, will retry",
                )

    def _drain_im_replies(self) -> None:
        self._cleanup_im_terminal_suppression()
        with self._pending_im_replies_lock:
            pending = list(self._pending_im_replies.items())
        for run_id, ctx in pending:
            try:
                terminal_text = self._terminal_text_for_run(run_id)
                if not terminal_text:
                    continue
                self.send_notification_message(
                    account_id=ctx.account_id,
                    workspace_id=ctx.workspace_id,
                    session_id=ctx.session_id,
                    status="completed",
                    body=terminal_text,
                    receiver_uid=ctx.reply_target,
                )
                with self._pending_im_replies_lock:
                    self._pending_im_replies.pop(run_id, None)
                if self._gateway_session_service is not None:
                    self._gateway_session_service.bind_active_run(
                        ctx.gateway_session_id, None
                    )
            except (RuntimeError, OSError, KeyError, ValueError):
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="gateway.xiaoluban.im_reply.send_failed",
                    message="Failed to send Xiaoluban IM reply",
                    payload={
                        "run_id": run_id,
                        "session_id": ctx.session_id,
                        "account_id": ctx.account_id,
                    },
                )

    def _register_im_reply(
        self,
        *,
        account_id: str,
        gateway_session_id: str,
        workspace_id: str,
        session_id: str,
        run_id: str,
        reply_target: str,
    ) -> None:
        ctx = _IMReplyContext(
            account_id=account_id,
            gateway_session_id=gateway_session_id,
            workspace_id=workspace_id,
            session_id=session_id,
            run_id=run_id,
            reply_target=reply_target,
        )
        with self._pending_im_replies_lock:
            self._pending_im_replies[run_id] = ctx

    def _terminal_text_for_run(self, run_id: str) -> str:
        if self._event_log is None:
            return ""
        for row in reversed(self._event_log.list_by_trace_with_ids(run_id)):
            try:
                event_type = RunEventType(str(row["event_type"]))
            except ValueError:
                continue
            if event_type not in {
                RunEventType.RUN_COMPLETED,
                RunEventType.RUN_FAILED,
                RunEventType.RUN_STOPPED,
            }:
                continue
            payload = parse_terminal_payload_json(row["payload_json"])
            if event_type == RunEventType.RUN_COMPLETED:
                output = extract_terminal_output(payload).strip()
                return output or "任务已完成。"
            output = extract_terminal_output(payload).strip()
            if output:
                return output
            error = extract_terminal_error(payload).strip()
            if error:
                return f"任务失败：{error}"
            return "任务未完成。"
        return ""

    def _mark_im_terminal_notification_suppressed(self, run_id: str) -> None:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return
        expires_at = time.monotonic() + _IM_TERMINAL_SUPPRESSION_TTL_SECONDS
        with self._im_terminal_suppression_lock:
            self._im_terminal_suppressed_run_ids[normalized_run_id] = expires_at

    def _cleanup_im_terminal_suppression(self) -> None:
        now = time.monotonic()
        with self._im_terminal_suppression_lock:
            expired_run_ids = [
                run_id
                for run_id, expires_at in self._im_terminal_suppressed_run_ids.items()
                if expires_at <= now
            ]
            for run_id in expired_run_ids:
                self._im_terminal_suppressed_run_ids.pop(run_id, None)


class _IMReplyContext(NamedTuple):
    account_id: str
    gateway_session_id: str
    workspace_id: str
    session_id: str
    run_id: str
    reply_target: str


class _CreateRun(Protocol):
    def __call__(self, intent: IntentInput) -> tuple[str, str]: ...  # pragma: no cover


def derive_uid_from_token(token: str) -> str:
    normalized = _validate_token(token)
    prefix, _separator, _suffix = normalized.partition("_")
    return prefix


def _default_im_config() -> XiaolubanImConfig:
    return XiaolubanImConfig()


def _extract_im_text(content: str) -> str:
    return content.strip()


def _external_session_id(
    *,
    account_id: str,
    workspace_id: str,
    message: XiaolubanInboundMessage,
) -> str:
    if message.session_id:
        return f"xiaoluban:{account_id}:{workspace_id}:{message.session_id}"
    sender = message.sender or "unknown_sender"
    receiver = message.receiver or "unknown_receiver"
    return f"xiaoluban:{account_id}:{workspace_id}:{sender}:{receiver}"


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
