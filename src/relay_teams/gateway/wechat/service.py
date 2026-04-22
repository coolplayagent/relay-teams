# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import base64
from collections.abc import Mapping
from concurrent.futures import CancelledError as FutureCancelledError
from concurrent.futures import Future as ConcurrentFuture
import json
import logging
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING
from urllib.parse import quote, urlparse
from uuid import uuid4

import qrcode
import qrcode.image.svg

from relay_teams.gateway.gateway_models import GatewayChannelType
from relay_teams.validation import require_force_delete
from relay_teams.gateway.gateway_session_service import GatewaySessionService
from relay_teams.gateway.session_ingress_service import (
    GatewaySessionIngressBusyPolicy,
    GatewaySessionIngressRequest,
    GatewaySessionIngressService,
)
from relay_teams.gateway.wechat.inbound_queue_repository import (
    WeChatInboundQueueRepository,
)
from relay_teams.logger import get_logger, log_event
from relay_teams.media import content_parts_from_text
from relay_teams.roles import RoleRegistry
from relay_teams.sessions.runs import RunEventHub
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_manager import RunManager
from relay_teams.sessions.runs.run_models import (
    IntentInput,
    RunThinkingConfig,
    RuntimePromptConversationContext,
)
from relay_teams.sessions.runs.terminal_payload import (
    extract_terminal_error,
    extract_terminal_output,
    parse_terminal_payload_json,
)
from relay_teams.agents.orchestration import OrchestrationSettingsService
from relay_teams.sessions import SessionService
from relay_teams.sessions.session_models import SessionMode
from relay_teams.workspace import WorkspaceService
from relay_teams.gateway.wechat.account_repository import WeChatAccountRepository
from relay_teams.gateway.wechat.client import WeChatClient
from relay_teams.gateway.wechat.models import (
    DEFAULT_WECHAT_BASE_URL,
    DEFAULT_WECHAT_CDN_BASE_URL,
    WeChatAccountRecord,
    WeChatAccountStatus,
    WeChatAccountUpdateInput,
    WeChatGatewaySnapshot,
    WeChatInboundMessage,
    WeChatInboundQueueRecord,
    WeChatInboundQueueStatus,
    WeChatLoginSession,
    WeChatLoginStartRequest,
    WeChatLoginStartResponse,
    WeChatLoginWaitRequest,
    WeChatLoginWaitResponse,
    WECHAT_PLATFORM,
)
from relay_teams.gateway.wechat.secret_store import (
    WeChatSecretStore,
    get_wechat_secret_store,
)

if TYPE_CHECKING:
    from relay_teams.gateway.im import ImSessionCommandService, ImToolService

_TERMINAL_EVENT_TYPES = {
    RunEventType.RUN_COMPLETED,
    RunEventType.RUN_FAILED,
    RunEventType.RUN_STOPPED,
}
_DEFAULT_POLL_TIMEOUT_MS = 35000
_INBOUND_QUEUE_CLAIM_STALE_AFTER_SECONDS = 60

LOGGER = get_logger(__name__)


class WeChatGatewayService:
    def __init__(
        self,
        *,
        config_dir: Path,
        repository: WeChatAccountRepository,
        secret_store: WeChatSecretStore | None,
        client: WeChatClient,
        gateway_session_service: GatewaySessionService,
        run_service: RunManager,
        run_event_hub: RunEventHub,
        workspace_service: WorkspaceService,
        role_registry: RoleRegistry,
        orchestration_settings_service: OrchestrationSettingsService,
        session_service: SessionService,
        im_tool_service: ImToolService,
        im_session_command_service: ImSessionCommandService,
        inbound_queue_repo: WeChatInboundQueueRepository,
        session_ingress_service: GatewaySessionIngressService | None = None,
    ) -> None:
        self._config_dir = config_dir
        self._repository = repository
        self._secret_store = (
            get_wechat_secret_store() if secret_store is None else secret_store
        )
        self._client = client
        self._gateway_session_service = gateway_session_service
        self._run_service = run_service
        self._run_event_hub = run_event_hub
        self._workspace_service = workspace_service
        self._role_registry = role_registry
        self._orchestration_settings_service = orchestration_settings_service
        self._session_service = session_service
        self._im_tool_service = im_tool_service
        self._im_session_command_service = im_session_command_service
        self._inbound_queue_repo = inbound_queue_repo
        self._session_ingress_service = session_ingress_service
        self._status_lock = Lock()
        self._status_by_account: dict[str, WeChatGatewaySnapshot] = {}
        self._monitor_stop_events: dict[str, Event] = {}
        self._monitor_threads: dict[str, Thread] = {}
        self._login_sessions: dict[str, WeChatLoginSession] = {}
        self._watched_runs: set[str] = set()
        self._drain_watched_runs: set[str] = set()

    def replace_role_registry(self, role_registry: RoleRegistry) -> None:
        self._role_registry = role_registry

    def start(self) -> None:
        for account in self._repository.list_accounts():
            if account.status == WeChatAccountStatus.ENABLED:
                self._start_account_worker(account.account_id)

    def stop(self) -> None:
        for account_id in tuple(self._monitor_stop_events):
            self._stop_account_worker(account_id)

    def reload(self) -> None:
        accounts = {item.account_id: item for item in self._repository.list_accounts()}
        running = set(self._monitor_threads)
        desired = {
            account_id
            for account_id, account in accounts.items()
            if account.status == WeChatAccountStatus.ENABLED
        }
        for account_id in sorted(running - desired):
            self._stop_account_worker(account_id)
        for account_id in sorted(desired):
            self._start_account_worker(account_id)

    def list_accounts(self) -> tuple[WeChatAccountRecord, ...]:
        accounts = []
        for account in self._repository.list_accounts():
            snapshot = self._status(account.account_id)
            accounts.append(
                account.model_copy(
                    update={
                        "running": snapshot.running,
                        "last_error": snapshot.last_error,
                        "last_event_at": snapshot.last_event_at,
                        "last_inbound_at": snapshot.last_inbound_at,
                        "last_outbound_at": snapshot.last_outbound_at,
                    }
                )
            )
        return tuple(accounts)

    def start_login(self, request: WeChatLoginStartRequest) -> WeChatLoginStartResponse:
        base_url = request.base_url or DEFAULT_WECHAT_BASE_URL
        qr = self._client.start_qr_login(
            base_url=base_url,
            route_tag=request.route_tag,
            bot_type=request.bot_type,
        )
        session = WeChatLoginSession(
            session_key=f"wechat-login-{uuid4().hex[:12]}",
            qrcode=qr.qrcode,
            qr_code_url=self._normalize_qr_code_url(qr.qrcode_img_content),
            base_url=base_url,
            route_tag=request.route_tag,
        )
        self._login_sessions[session.session_key] = session
        return WeChatLoginStartResponse(
            session_key=session.session_key,
            qr_code_url=session.qr_code_url,
            message="Scan the QR code with WeChat to connect the account.",
        )

    def wait_login(self, request: WeChatLoginWaitRequest) -> WeChatLoginWaitResponse:
        login_session = self._login_sessions.get(request.session_key)
        if login_session is None:
            raise KeyError(f"Unknown WeChat login session: {request.session_key}")
        status = self._client.wait_qr_login(
            login_session=login_session,
            timeout_ms=request.timeout_ms,
        )
        if status.status != "confirmed":
            return WeChatLoginWaitResponse(
                connected=False,
                message="WeChat login did not complete before timeout.",
            )
        if (
            status.bot_token is None
            or status.ilink_bot_id is None
            or not status.bot_token.strip()
            or not status.ilink_bot_id.strip()
        ):
            return WeChatLoginWaitResponse(
                connected=False,
                message="WeChat login completed without a usable bot token.",
            )
        now = datetime.now(tz=timezone.utc)
        existing = self._get_existing_account(status.ilink_bot_id)
        record = WeChatAccountRecord(
            account_id=status.ilink_bot_id,
            display_name=(
                existing.display_name if existing is not None else status.ilink_bot_id
            ),
            base_url=(status.baseurl or login_session.base_url).strip(),
            cdn_base_url=(
                existing.cdn_base_url
                if existing is not None
                else DEFAULT_WECHAT_CDN_BASE_URL
            ),
            route_tag=(
                existing.route_tag if existing is not None else login_session.route_tag
            ),
            status=existing.status
            if existing is not None
            else WeChatAccountStatus.ENABLED,
            remote_user_id=status.ilink_user_id,
            sync_cursor=existing.sync_cursor if existing is not None else "",
            workspace_id=existing.workspace_id if existing is not None else "default",
            session_mode=(
                existing.session_mode if existing is not None else SessionMode.NORMAL
            ),
            normal_root_role_id=existing.normal_root_role_id
            if existing is not None
            else None,
            orchestration_preset_id=existing.orchestration_preset_id
            if existing is not None
            else None,
            yolo=existing.yolo if existing is not None else True,
            thinking=existing.thinking if existing is not None else RunThinkingConfig(),
            last_login_at=now,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        self._secret_store.set_bot_token(
            self._config_dir,
            status.ilink_bot_id,
            status.bot_token,
        )
        _ = self._repository.upsert_account(record)
        self._login_sessions.pop(request.session_key, None)
        self.reload()
        return WeChatLoginWaitResponse(
            connected=True,
            account_id=status.ilink_bot_id,
            message="WeChat account connected.",
        )

    def update_account(
        self,
        account_id: str,
        request: WeChatAccountUpdateInput,
    ) -> WeChatAccountRecord:
        existing = self._repository.get_account(account_id)
        workspace_id = request.workspace_id or existing.workspace_id
        self._workspace_service.get_workspace(workspace_id)
        session_mode = request.session_mode or existing.session_mode
        normal_root_role_id = request.normal_root_role_id
        if session_mode == existing.session_mode and normal_root_role_id is None:
            normal_root_role_id = existing.normal_root_role_id
        if normal_root_role_id:
            self._role_registry.get(normal_root_role_id)
        orchestration_preset_id = request.orchestration_preset_id
        if session_mode == existing.session_mode and orchestration_preset_id is None:
            orchestration_preset_id = existing.orchestration_preset_id
        if session_mode.value == "orchestration":
            preset_id = (
                orchestration_preset_id
                or self._orchestration_settings_service.default_orchestration_preset_id()
            )
            if not preset_id:
                raise ValueError("orchestration_preset_id is required")
            settings = self._orchestration_settings_service.get_orchestration_config()
            presets = settings.presets
            if not any(item.preset_id == preset_id for item in presets):
                raise ValueError(f"Unknown orchestration preset: {preset_id}")
            orchestration_preset_id = preset_id
        updated = existing.model_copy(
            update={
                "display_name": request.display_name or existing.display_name,
                "base_url": request.base_url or existing.base_url,
                "cdn_base_url": request.cdn_base_url or existing.cdn_base_url,
                "route_tag": request.route_tag
                if "route_tag" in request.model_fields_set
                else existing.route_tag,
                "status": (
                    WeChatAccountStatus.ENABLED
                    if request.enabled is None
                    else WeChatAccountStatus.ENABLED
                    if request.enabled
                    else WeChatAccountStatus.DISABLED
                ),
                "workspace_id": workspace_id,
                "session_mode": session_mode,
                "normal_root_role_id": normal_root_role_id,
                "orchestration_preset_id": orchestration_preset_id,
                "yolo": request.yolo if request.yolo is not None else existing.yolo,
                "thinking": request.thinking or existing.thinking,
                "updated_at": datetime.now(tz=timezone.utc),
            }
        )
        saved = self._repository.upsert_account(updated)
        self.reload()
        return self._merge_status(saved)

    def set_account_enabled(
        self, account_id: str, enabled: bool
    ) -> WeChatAccountRecord:
        return self.update_account(
            account_id,
            WeChatAccountUpdateInput(enabled=enabled),
        )

    def delete_account(self, account_id: str, *, force: bool = False) -> None:
        account = self._repository.get_account(account_id)
        if account.status == WeChatAccountStatus.ENABLED:
            require_force_delete(
                force,
                message="Cannot delete enabled WeChat account without force",
            )
        self._stop_account_worker(account_id)
        self._secret_store.delete_bot_token(self._config_dir, account_id)
        self._repository.delete_account(account_id)

    def _exists(self, account_id: str) -> bool:
        try:
            self._repository.get_account(account_id)
        except KeyError:
            return False
        return True

    def _get_existing_account(self, account_id: str) -> WeChatAccountRecord | None:
        try:
            return self._repository.get_account(account_id)
        except KeyError:
            return None

    def _merge_status(self, account: WeChatAccountRecord) -> WeChatAccountRecord:
        snapshot = self._status(account.account_id)
        return account.model_copy(
            update={
                "running": snapshot.running,
                "last_error": snapshot.last_error,
                "last_event_at": snapshot.last_event_at,
                "last_inbound_at": snapshot.last_inbound_at,
                "last_outbound_at": snapshot.last_outbound_at,
            }
        )

    def _status(self, account_id: str) -> WeChatGatewaySnapshot:
        with self._status_lock:
            existing = self._status_by_account.get(account_id)
            if existing is not None:
                return existing
            fresh = WeChatGatewaySnapshot(account_id=account_id)
            self._status_by_account[account_id] = fresh
            return fresh

    def _set_status(self, account_id: str, **updates: object) -> None:
        with self._status_lock:
            existing = self._status_by_account.get(account_id)
            if existing is None:
                existing = WeChatGatewaySnapshot(account_id=account_id)
            self._status_by_account[account_id] = existing.model_copy(update=updates)

    def _start_account_worker(self, account_id: str) -> None:
        thread = self._monitor_threads.get(account_id)
        if thread is not None and thread.is_alive():
            return
        try:
            token = self._secret_store.get_bot_token(self._config_dir, account_id)
        except Exception as exc:
            self._set_status(account_id, running=False, last_error=str(exc))
            log_event(
                LOGGER,
                logging.WARNING,
                event="wechat.worker.start_failed",
                message="Failed to start WeChat worker",
                payload={"account_id": account_id, "error": str(exc)},
                exc_info=exc,
            )
            return
        if token is None:
            self._set_status(account_id, running=False, last_error="missing_token")
            log_event(
                LOGGER,
                logging.WARNING,
                event="wechat.worker.missing_token",
                message="Skipped WeChat worker startup because token is missing",
                payload={"account_id": account_id},
            )
            return
        stop_event = Event()
        self._monitor_stop_events[account_id] = stop_event
        thread = Thread(
            target=self._run_monitor,
            name=f"wechat-monitor-{account_id}",
            args=(account_id, stop_event),
            daemon=True,
        )
        self._monitor_threads[account_id] = thread
        thread.start()
        log_event(
            LOGGER,
            logging.INFO,
            event="wechat.worker.started",
            message="Started WeChat worker",
            payload={"account_id": account_id},
        )

    def _stop_account_worker(self, account_id: str) -> None:
        stop_event = self._monitor_stop_events.pop(account_id, None)
        if stop_event is not None:
            stop_event.set()
        thread = self._monitor_threads.pop(account_id, None)
        if thread is not None:
            thread.join(timeout=5)
        self._set_status(account_id, running=False)

    def _run_monitor(self, account_id: str, stop_event: Event) -> None:
        self._set_status(account_id, running=True, last_error=None)
        while not stop_event.is_set():
            try:
                account = self._repository.get_account(account_id)
                token = self._secret_store.get_bot_token(self._config_dir, account_id)
                if token is None:
                    self._set_status(
                        account_id, running=False, last_error="missing_token"
                    )
                    return
                response = self._client.get_updates(
                    account=account, token=token, timeout_ms=_DEFAULT_POLL_TIMEOUT_MS
                )
                updated_account = account
                if (
                    response.get_updates_buf
                    and response.get_updates_buf != account.sync_cursor
                ):
                    updated_account = self._repository.upsert_account(
                        account.model_copy(
                            update={
                                "sync_cursor": response.get_updates_buf,
                                "updated_at": datetime.now(tz=timezone.utc),
                            }
                        )
                    )
                now = datetime.now(tz=timezone.utc)
                self._set_status(account_id, running=True, last_event_at=now)
                for message in response.msgs:
                    self._handle_message(updated_account, token, message)
            except Exception as exc:
                self._set_status(
                    account_id,
                    running=True,
                    last_error=str(exc),
                    last_event_at=datetime.now(tz=timezone.utc),
                )
                stop_event.wait(2.0)

    def _handle_message(
        self,
        account: WeChatAccountRecord,
        token: str,
        message: WeChatInboundMessage,
    ) -> None:
        peer_user_id = (message.from_user_id or "").strip()
        if not peer_user_id:
            return
        text = self._extract_text(message)
        if not text:
            return
        now = datetime.now(tz=timezone.utc)
        self._set_status(
            account.account_id,
            last_inbound_at=now,
            last_event_at=now,
        )
        gateway_session = self._gateway_session_service.resolve_or_create_session(
            channel_type=GatewayChannelType.WECHAT,
            external_session_id=self._external_session_id(
                account.account_id, peer_user_id
            ),
            workspace_id=account.workspace_id,
            metadata={
                "title": f"{account.display_name} - {peer_user_id}",
                "source_kind": "im",
                "source_provider": "wechat",
                "source_label": account.display_name,
            },
            session_mode=account.session_mode,
            normal_root_role_id=account.normal_root_role_id,
            orchestration_preset_id=account.orchestration_preset_id,
            peer_user_id=peer_user_id,
            peer_chat_id=peer_user_id,
            capabilities={"chat_type": "direct"},
            channel_state={
                "account_id": account.account_id,
                "peer_user_id": peer_user_id,
                "chat_type": "direct",
                "context_token": message.context_token,
                "last_inbound_at": now.isoformat(),
            },
        )
        command_result = self._im_session_command_service.handle_wechat_command(
            session_id=gateway_session.internal_session_id,
            gateway_session_id=gateway_session.gateway_session_id,
            text=text,
        )
        if command_result is not None:
            response_text = (
                command_result
                if isinstance(command_result, str)
                else command_result.text
            )
            self._send_intermediate_text(
                account_id=account.account_id,
                gateway_session_id=gateway_session.gateway_session_id,
                peer_user_id=peer_user_id,
                context_token=message.context_token,
                text=response_text,
                event_name="wechat.command.response",
                failure_message="Failed to send WeChat command response",
            )
            resumed_run_id = (
                None
                if isinstance(command_result, str)
                else command_result.resumed_run_id
            )
            if resumed_run_id is not None:
                self._start_run_watcher(
                    account_id=account.account_id,
                    gateway_session_id=gateway_session.gateway_session_id,
                    run_id=resumed_run_id,
                    peer_user_id=peer_user_id,
                    context_token=message.context_token,
                )
            return
        queue_record, created = self._inbound_queue_repo.create_or_get(
            WeChatInboundQueueRecord(
                inbound_queue_id=f"wq_{uuid4().hex[:16]}",
                account_id=account.account_id,
                message_key=self._message_key(message),
                gateway_session_id=gateway_session.gateway_session_id,
                session_id=gateway_session.internal_session_id,
                peer_user_id=peer_user_id,
                context_token=message.context_token,
                text=text,
            )
        )
        if not created:
            return
        self._drain_inbound_queue()
        latest = self._inbound_queue_repo.get(queue_record.inbound_queue_id)
        if latest is None:
            return
        receipt_text = self._build_receipt_text(latest)
        self._send_intermediate_text(
            account_id=account.account_id,
            gateway_session_id=gateway_session.gateway_session_id,
            peer_user_id=peer_user_id,
            context_token=message.context_token,
            text=receipt_text,
            event_name="wechat.receipt",
            failure_message="Failed to send WeChat receipt",
        )

    def _start_run_watcher(
        self,
        *,
        account_id: str,
        gateway_session_id: str,
        run_id: str,
        peer_user_id: str,
        context_token: str | None,
    ) -> None:
        if run_id in self._watched_runs:
            return
        self._watched_runs.add(run_id)
        future = asyncio.run_coroutine_threadsafe(
            self._await_terminal_and_reply(
                account_id=account_id,
                gateway_session_id=gateway_session_id,
                run_id=run_id,
                peer_user_id=peer_user_id,
                context_token=context_token,
            ),
            self._require_loop(),
        )

        def on_reply_done(done: ConcurrentFuture[None]) -> None:
            self._handle_reply_future(
                account_id=account_id,
                gateway_session_id=gateway_session_id,
                run_id=run_id,
                peer_user_id=peer_user_id,
                future=done,
            )

        future.add_done_callback(on_reply_done)

    def _start_queue_drain_watcher(self, *, session_id: str, run_id: str) -> None:
        if run_id in self._watched_runs or run_id in self._drain_watched_runs:
            return
        try:
            loop = self._require_loop()
        except RuntimeError:
            return
        self._drain_watched_runs.add(run_id)
        future = asyncio.run_coroutine_threadsafe(
            self._await_run_completion_for_queue_drain(
                session_id=session_id,
                run_id=run_id,
            ),
            loop,
        )

        def on_drain_done(done: ConcurrentFuture[None]) -> None:
            self._handle_queue_drain_future(
                session_id=session_id,
                run_id=run_id,
                future=done,
            )

        future.add_done_callback(on_drain_done)

    async def _await_terminal_and_reply(
        self,
        *,
        account_id: str,
        gateway_session_id: str,
        run_id: str,
        peer_user_id: str,
        context_token: str | None,
    ) -> None:
        try:
            async for event in self._run_service.stream_run_events(run_id):
                if event.event_type == RunEventType.RUN_PAUSED:
                    account = self._repository.get_account(account_id)
                    token = self._secret_store.get_bot_token(
                        self._config_dir, account_id
                    )
                    if token is None:
                        raise RuntimeError(
                            f"WeChat reply failed because bot token is missing for {account_id}."
                        )
                    text = self._paused_text(event)
                    self._send_typing(account, token, peer_user_id, context_token, 2)
                    self._im_tool_service.send_text_to_wechat_peer(
                        account_id=account_id,
                        peer_user_id=peer_user_id,
                        text=text,
                        context_token=context_token,
                    )
                    self._record_pause_notice(
                        account_id=account_id,
                        occurred_at=datetime.now(tz=timezone.utc),
                    )
                    return
                if event.event_type not in _TERMINAL_EVENT_TYPES:
                    continue
                account = self._repository.get_account(account_id)
                token = self._secret_store.get_bot_token(self._config_dir, account_id)
                if token is None:
                    raise RuntimeError(
                        f"WeChat reply failed because bot token is missing for {account_id}."
                    )
                text = self._terminal_text(event)
                self._send_typing(account, token, peer_user_id, context_token, 2)
                log_event(
                    LOGGER,
                    logging.INFO,
                    event="wechat.reply.attempted",
                    message="Attempting to send WeChat reply",
                    payload={
                        "account_id": account_id,
                        "gateway_session_id": gateway_session_id,
                        "run_id": run_id,
                        "peer_user_id": peer_user_id,
                    },
                )
                self._im_tool_service.send_text_to_wechat_peer(
                    account_id=account_id,
                    peer_user_id=peer_user_id,
                    text=text,
                    context_token=context_token,
                )
                now = datetime.now(tz=timezone.utc)
                self._record_reply_success(
                    account_id=account_id,
                    gateway_session_id=gateway_session_id,
                    run_id=run_id,
                    peer_user_id=peer_user_id,
                    context_token=context_token,
                    occurred_at=now,
                )
                return
            raise RuntimeError(
                f"WeChat reply watcher ended before a stop event for {run_id}."
            )
        except Exception as exc:
            self._record_reply_failure(
                account_id=account_id,
                gateway_session_id=gateway_session_id,
                run_id=run_id,
                peer_user_id=peer_user_id,
                error_message=str(exc),
            )
            raise
        finally:
            self._watched_runs.discard(run_id)

    async def _await_run_completion_for_queue_drain(
        self,
        *,
        session_id: str,
        run_id: str,
    ) -> None:
        async for event in self._run_service.stream_run_events(run_id):
            if event.event_type in _TERMINAL_EVENT_TYPES:
                return
            if (
                event.event_type == RunEventType.RUN_PAUSED
                and self._active_run_id(session_id) != run_id
            ):
                return
        if self._active_run_id(session_id) == run_id:
            raise RuntimeError(
                f"WeChat queue drain watcher ended before a terminal event for {run_id}."
            )

    def _handle_reply_future(
        self,
        *,
        account_id: str,
        gateway_session_id: str,
        run_id: str,
        peer_user_id: str,
        future: ConcurrentFuture[None],
    ) -> None:
        try:
            future.result()
        except FutureCancelledError as exc:
            message = f"WeChat reply task was cancelled for run {run_id}."
            self._record_reply_failure(
                account_id=account_id,
                gateway_session_id=gateway_session_id,
                run_id=run_id,
                peer_user_id=peer_user_id,
                error_message=message,
            )
            log_event(
                LOGGER,
                logging.WARNING,
                event="wechat.reply.cancelled",
                message="WeChat reply task was cancelled",
                payload={
                    "account_id": account_id,
                    "gateway_session_id": gateway_session_id,
                    "run_id": run_id,
                    "peer_user_id": peer_user_id,
                },
                exc_info=exc,
            )
        except Exception as exc:
            log_event(
                LOGGER,
                logging.ERROR,
                event="wechat.reply.failed",
                message="WeChat reply task failed",
                payload={
                    "account_id": account_id,
                    "gateway_session_id": gateway_session_id,
                    "run_id": run_id,
                    "peer_user_id": peer_user_id,
                    "error": str(exc),
                },
                exc_info=exc,
            )

    def _handle_queue_drain_future(
        self,
        *,
        session_id: str,
        run_id: str,
        future: ConcurrentFuture[None],
    ) -> None:
        try:
            future.result()
        except FutureCancelledError as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="wechat.queue_drain.cancelled",
                message="WeChat queue drain watcher was cancelled",
                payload={"session_id": session_id, "run_id": run_id},
                exc_info=exc,
            )
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="wechat.queue_drain.failed",
                message="WeChat queue drain watcher failed",
                payload={
                    "session_id": session_id,
                    "run_id": run_id,
                    "error": str(exc),
                },
                exc_info=exc,
            )
        finally:
            self._drain_watched_runs.discard(run_id)
            if self._active_run_id(session_id) != run_id:
                self._drain_inbound_queue()

    def _record_reply_success(
        self,
        *,
        account_id: str,
        gateway_session_id: str,
        run_id: str,
        peer_user_id: str,
        context_token: str | None,
        occurred_at: datetime,
    ) -> None:
        try:
            self._gateway_session_service.update_channel_state(
                gateway_session_id,
                channel_state={
                    "context_token": context_token,
                    "last_outbound_at": occurred_at.isoformat(),
                },
                peer_user_id=peer_user_id,
                peer_chat_id=peer_user_id,
            )
        except KeyError as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="wechat.reply.channel_state_update_failed",
                message="Failed to update WeChat channel state after sending reply",
                payload={
                    "account_id": account_id,
                    "gateway_session_id": gateway_session_id,
                    "run_id": run_id,
                    "peer_user_id": peer_user_id,
                    "error": str(exc),
                },
                exc_info=exc,
            )
        self._clear_active_run(gateway_session_id)
        self._mark_queue_record_completed(run_id=run_id, failed=False)
        self._set_status(
            account_id,
            last_error=None,
            last_outbound_at=occurred_at,
            last_event_at=occurred_at,
        )
        self._drain_inbound_queue()
        log_event(
            LOGGER,
            logging.INFO,
            event="wechat.reply.sent",
            message="Sent WeChat reply",
            payload={
                "account_id": account_id,
                "gateway_session_id": gateway_session_id,
                "run_id": run_id,
                "peer_user_id": peer_user_id,
            },
        )

    def _record_reply_failure(
        self,
        *,
        account_id: str,
        gateway_session_id: str,
        run_id: str,
        peer_user_id: str,
        error_message: str,
    ) -> None:
        self._clear_active_run(gateway_session_id)
        self._mark_queue_record_completed(
            run_id=run_id,
            failed=True,
            error_message=error_message,
        )
        self._set_status(
            account_id,
            last_error=error_message,
            last_event_at=datetime.now(tz=timezone.utc),
        )
        self._drain_inbound_queue()

    def _record_pause_notice(
        self,
        *,
        account_id: str,
        occurred_at: datetime,
    ) -> None:
        self._set_status(
            account_id,
            last_error=None,
            last_outbound_at=occurred_at,
            last_event_at=occurred_at,
        )

    def _clear_active_run(self, gateway_session_id: str) -> None:
        try:
            self._gateway_session_service.bind_active_run(gateway_session_id, None)
        except KeyError as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="wechat.reply.clear_active_run_failed",
                message="Failed to clear WeChat active run binding",
                payload={
                    "gateway_session_id": gateway_session_id,
                    "error": str(exc),
                },
                exc_info=exc,
            )

    def _send_intermediate_text(
        self,
        *,
        account_id: str,
        gateway_session_id: str,
        peer_user_id: str,
        context_token: str | None,
        text: str,
        event_name: str,
        failure_message: str,
    ) -> None:
        try:
            self._im_tool_service.send_text_to_wechat_peer(
                account_id=account_id,
                peer_user_id=peer_user_id,
                text=text,
                context_token=context_token,
            )
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event=f"{event_name}.failed",
                message=failure_message,
                payload={
                    "account_id": account_id,
                    "gateway_session_id": gateway_session_id,
                    "peer_user_id": peer_user_id,
                    "error": str(exc),
                },
                exc_info=exc,
            )
            return
        self._record_intermediate_outbound(
            account_id=account_id,
            gateway_session_id=gateway_session_id,
            peer_user_id=peer_user_id,
            context_token=context_token,
            occurred_at=datetime.now(tz=timezone.utc),
        )

    def _record_intermediate_outbound(
        self,
        *,
        account_id: str,
        gateway_session_id: str,
        peer_user_id: str,
        context_token: str | None,
        occurred_at: datetime,
    ) -> None:
        try:
            self._gateway_session_service.update_channel_state(
                gateway_session_id,
                channel_state={
                    "context_token": context_token,
                    "last_outbound_at": occurred_at.isoformat(),
                },
                peer_user_id=peer_user_id,
                peer_chat_id=peer_user_id,
            )
        except KeyError as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="wechat.intermediate.channel_state_update_failed",
                message="Failed to update WeChat channel state after sending text",
                payload={
                    "account_id": account_id,
                    "gateway_session_id": gateway_session_id,
                    "peer_user_id": peer_user_id,
                    "error": str(exc),
                },
                exc_info=exc,
            )
        self._set_status(
            account_id,
            last_error=None,
            last_outbound_at=occurred_at,
            last_event_at=occurred_at,
        )

    def _send_typing(
        self,
        account: WeChatAccountRecord,
        token: str,
        peer_user_id: str,
        context_token: str | None,
        status: int,
    ) -> None:
        try:
            ticket = self._client.get_typing_ticket(
                account=account,
                token=token,
                peer_user_id=peer_user_id,
                context_token=context_token,
            )
            if ticket is None:
                return
            self._client.send_typing(
                account=account,
                token=token,
                peer_user_id=peer_user_id,
                typing_ticket=ticket,
                status=status,
            )
        except Exception:
            return

    def _require_loop(self) -> asyncio.AbstractEventLoop:
        loop = self._run_service._event_loop
        if loop is None:
            raise RuntimeError("RunManager event loop is not bound")
        return loop

    def _drain_inbound_queue(self) -> None:
        stale_before = datetime.now(tz=timezone.utc) - timedelta(
            seconds=_INBOUND_QUEUE_CLAIM_STALE_AFTER_SECONDS
        )
        for record in self._inbound_queue_repo.list_ready_to_start(
            stale_before=stale_before
        ):
            claimed = self._inbound_queue_repo.claim_starting(
                inbound_queue_id=record.inbound_queue_id,
                stale_before=stale_before,
            )
            if claimed is None:
                continue
            blocking_run_id = self._active_run_id(claimed.session_id)
            if blocking_run_id is not None:
                self._start_queue_drain_watcher(
                    session_id=claimed.session_id,
                    run_id=blocking_run_id,
                )
            if self._inbound_queue_repo.count_non_terminal_ahead(
                claimed.inbound_queue_id
            ):
                _ = self._inbound_queue_repo.requeue_if_starting(
                    inbound_queue_id=claimed.inbound_queue_id
                )
                continue
            if blocking_run_id is not None:
                _ = self._inbound_queue_repo.requeue_if_starting(
                    inbound_queue_id=claimed.inbound_queue_id
                )
                continue
            if not self._start_queued_record(claimed):
                continue

    def _start_queued_record(self, record: WeChatInboundQueueRecord) -> bool:
        try:
            account = self._repository.get_account(record.account_id)
        except KeyError:
            self._fail_starting_record(
                inbound_queue_id=record.inbound_queue_id,
                error_message=f"WeChat account not found: {record.account_id}",
            )
            return False
        intent = IntentInput(
            session_id=record.session_id,
            input=content_parts_from_text(record.text),
            yolo=account.yolo,
            thinking=account.thinking,
            conversation_context=RuntimePromptConversationContext(
                source_provider=WECHAT_PLATFORM,
                source_kind="im",
            ),
        )
        try:
            run_id = self._start_session_ingress_run(intent)
        except RuntimeError as exc:
            if str(exc).strip() != "session_busy":
                _ = self._inbound_queue_repo.requeue_if_starting(
                    inbound_queue_id=record.inbound_queue_id,
                    last_error=str(exc),
                )
                return False
            _ = self._inbound_queue_repo.requeue_if_starting(
                inbound_queue_id=record.inbound_queue_id
            )
            return False
        now = datetime.now(tz=timezone.utc)
        current = self._inbound_queue_repo.get(record.inbound_queue_id)
        if current is None or current.status != WeChatInboundQueueStatus.STARTING:
            return False
        updated = self._inbound_queue_repo.update(
            current.model_copy(
                update={
                    "status": WeChatInboundQueueStatus.WAITING_RESULT,
                    "run_id": run_id,
                    "last_error": None,
                    "updated_at": now,
                }
            )
        )
        self._gateway_session_service.bind_active_run(
            updated.gateway_session_id,
            run_id,
        )
        token = self._secret_store.get_bot_token(self._config_dir, record.account_id)
        if token is not None:
            self._send_typing(
                account,
                token,
                record.peer_user_id,
                record.context_token,
                1,
            )
        self._start_run_watcher(
            account_id=updated.account_id,
            gateway_session_id=updated.gateway_session_id,
            run_id=run_id,
            peer_user_id=updated.peer_user_id,
            context_token=updated.context_token,
        )
        return True

    def _fail_starting_record(
        self,
        *,
        inbound_queue_id: str,
        error_message: str,
    ) -> None:
        current = self._inbound_queue_repo.get(inbound_queue_id)
        if current is None or current.status != WeChatInboundQueueStatus.STARTING:
            return
        now = datetime.now(tz=timezone.utc)
        _ = self._inbound_queue_repo.update(
            current.model_copy(
                update={
                    "status": WeChatInboundQueueStatus.FAILED,
                    "run_id": None,
                    "last_error": error_message,
                    "updated_at": now,
                    "completed_at": now,
                }
            )
        )

    def _start_session_ingress_run(self, intent: IntentInput) -> str:
        if self._session_ingress_service is not None:
            result = self._session_ingress_service.submit(
                GatewaySessionIngressRequest(
                    intent=intent,
                    busy_policy=GatewaySessionIngressBusyPolicy.QUEUE_IF_BUSY,
                )
            )
            if result.run_id is None:
                raise RuntimeError("session_busy")
            return result.run_id
        run_id, _ = self._run_service.create_run(intent)
        self._run_service.ensure_run_started(run_id)
        return run_id

    def _active_run_id(self, session_id: str) -> str | None:
        if self._session_ingress_service is not None:
            return self._session_ingress_service.active_run_id(session_id)
        recovery_snapshot = self._session_service.get_recovery_snapshot(session_id)
        active_run = recovery_snapshot.get("active_run")
        if not isinstance(active_run, Mapping):
            return None
        run_id = active_run.get("run_id")
        if isinstance(run_id, str) and run_id.strip():
            return run_id.strip()
        return None

    def _build_receipt_text(self, record: WeChatInboundQueueRecord) -> str:
        if record.status == WeChatInboundQueueStatus.FAILED:
            error_message = str(record.last_error or "").strip()
            if error_message:
                return f"\u6536\u5230\uff0c\u4f46\u5904\u7406\u5931\u8d25\uff1a{error_message}"
            return "\u6536\u5230\uff0c\u4f46\u5904\u7406\u5931\u8d25\u3002"
        if record.status == WeChatInboundQueueStatus.WAITING_RESULT:
            return "\u6536\u5230\uff0c\u6b63\u5728\u5904\u7406\u3002"
        queue_depth = self._queue_depth(record)
        if queue_depth <= 0:
            return "\u6536\u5230\uff0c\u6b63\u5728\u5904\u7406\u3002"
        return f"\u6536\u5230\uff0c\u5df2\u8fdb\u5165\u6392\u961f\u3002\u5f53\u524d\u4f1a\u8bdd\u524d\u9762\u8fd8\u6709 {queue_depth} \u6761\u6d88\u606f\u3002"

    def _queue_depth(self, record: WeChatInboundQueueRecord) -> int:
        ahead_count = self._inbound_queue_repo.count_non_terminal_ahead(
            record.inbound_queue_id
        )
        blocking_run_id = self._active_run_id(record.session_id)
        if blocking_run_id is None:
            return ahead_count
        if self._inbound_queue_repo.has_non_terminal_item_for_run(blocking_run_id):
            return ahead_count
        return ahead_count + 1

    def _mark_queue_record_completed(
        self,
        *,
        run_id: str,
        failed: bool,
        error_message: str | None = None,
    ) -> None:
        record = self._inbound_queue_repo.get_latest_by_run_id(run_id)
        if record is None:
            return
        now = datetime.now(tz=timezone.utc)
        _ = self._inbound_queue_repo.update(
            record.model_copy(
                update={
                    "status": (
                        WeChatInboundQueueStatus.FAILED
                        if failed
                        else WeChatInboundQueueStatus.COMPLETED
                    ),
                    "last_error": error_message if failed else None,
                    "updated_at": now,
                    "completed_at": now,
                }
            )
        )

    @staticmethod
    def _external_session_id(account_id: str, peer_user_id: str) -> str:
        return f"wechat:{account_id}:{peer_user_id}"

    @staticmethod
    def _message_key(message: WeChatInboundMessage) -> str:
        if message.message_id is not None:
            return f"mid:{message.message_id}"
        if message.seq is not None:
            return f"seq:{message.seq}"
        if message.context_token is not None and message.context_token.strip():
            return f"ctx:{message.context_token.strip()}"
        if message.create_time_ms is not None:
            return f"ts:{message.create_time_ms}"
        return f"anon:{uuid4().hex[:12]}"

    @staticmethod
    def _extract_text(message: WeChatInboundMessage) -> str:
        parts: list[str] = []
        for item in message.item_list:
            if item.text_item is not None and item.text_item.text.strip():
                parts.append(item.text_item.text.strip())
        return "\n".join(parts).strip()

    @staticmethod
    def _terminal_text(event) -> str:
        payload = parse_terminal_payload_json(event.payload_json)
        if event.event_type == RunEventType.RUN_COMPLETED:
            output = extract_terminal_output(payload)
            if output:
                return output
            return "Completed."
        if event.event_type == RunEventType.RUN_STOPPED:
            return "Run stopped."
        output = extract_terminal_output(payload)
        if output:
            return output
        error = extract_terminal_error(payload)
        if error:
            return f"Run failed: {error}"
        return "Run failed."

    @staticmethod
    def _paused_text(event) -> str:
        try:
            payload = json.loads(event.payload_json)
        except json.JSONDecodeError:
            payload = {}
        error_message = payload.get("error_message")
        if isinstance(error_message, str) and error_message.strip():
            return f"Run paused: {error_message.strip()}\nSend resume to continue."
        return "Run paused.\nSend resume to continue."

    @staticmethod
    def _normalize_qr_code_url(value: str) -> str:
        normalized = value.strip()
        if normalized.startswith("http://") or normalized.startswith("https://"):
            if WeChatGatewayService._looks_like_image_url(normalized):
                return normalized
            return WeChatGatewayService._render_qr_svg_data_uri(normalized)
        if normalized.startswith("weixin://"):
            return WeChatGatewayService._render_qr_svg_data_uri(normalized)
        if normalized.startswith("wxp://"):
            return WeChatGatewayService._render_qr_svg_data_uri(normalized)
        if normalized.startswith("wx://"):
            return WeChatGatewayService._render_qr_svg_data_uri(normalized)
        if normalized.startswith("wechat://"):
            return WeChatGatewayService._render_qr_svg_data_uri(normalized)
        if normalized.startswith("openwechat://"):
            return WeChatGatewayService._render_qr_svg_data_uri(normalized)
        if normalized.startswith("wxapp://"):
            return WeChatGatewayService._render_qr_svg_data_uri(normalized)
        if normalized.startswith("wxwork://"):
            return WeChatGatewayService._render_qr_svg_data_uri(normalized)
        if normalized.startswith("file://"):
            return normalized
        if normalized.startswith("data:"):
            return normalized
        if normalized.startswith("<svg") or normalized.startswith("<?xml"):
            return f"data:image/svg+xml;utf8,{quote(normalized)}"
        if normalized.startswith("%3Csvg") or normalized.startswith("%3C%3Fxml"):
            return f"data:image/svg+xml;utf8,{normalized}"

        compact = "".join(normalized.split())
        mime_type = WeChatGatewayService._detect_qr_mime_type(compact)
        return f"data:{mime_type};base64,{compact}"

    @staticmethod
    def _looks_like_image_url(value: str) -> bool:
        path = urlparse(value).path.lower()
        return path.endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico")
        )

    @staticmethod
    def _render_qr_svg_data_uri(value: str) -> str:
        image = qrcode.make(value, image_factory=qrcode.image.svg.SvgImage)
        buffer = BytesIO()
        image.save(buffer)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/svg+xml;base64,{encoded}"

    @staticmethod
    def _detect_qr_mime_type(value: str) -> str:
        padded = value + ("=" * ((4 - len(value) % 4) % 4))
        try:
            decoded = base64.b64decode(padded, validate=False)
        except Exception:
            return "image/png"
        if decoded.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if decoded.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if decoded.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        text = decoded.decode("utf-8", errors="ignore").lstrip()
        if text.startswith("<svg") or text.startswith("<?xml"):
            return "image/svg+xml"
        return "image/png"
