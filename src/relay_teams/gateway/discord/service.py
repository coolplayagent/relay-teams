# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Coroutine, Mapping
from concurrent.futures import CancelledError as FutureCancelledError
from concurrent.futures import Future as ConcurrentFuture
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from relay_teams.agents.orchestration.settings_service import (
    OrchestrationSettingsService,
)
from relay_teams.gateway.discord.account_repository import DiscordAccountRepository
from relay_teams.gateway.discord.client import DiscordClient
from relay_teams.gateway.discord.gateway_worker import DiscordGatewayWorker
from relay_teams.gateway.discord.inbound_queue_repository import (
    DiscordInboundQueueRepository,
)
from relay_teams.gateway.discord.models import (
    DISCORD_PLATFORM,
    DiscordAccountCreateInput,
    DiscordAccountRecord,
    DiscordAccountStatus,
    DiscordAccountUpdateInput,
    DiscordChatType,
    DiscordInboundMessage,
    DiscordInboundQueueRecord,
    DiscordInboundQueueStatus,
    DiscordSecretStatus,
)
from relay_teams.gateway.discord.secret_store import (
    DiscordSecretStore,
    get_discord_secret_store,
)
from relay_teams.gateway.gateway_models import GatewayChannelType, GatewaySessionRecord
from relay_teams.gateway.gateway_session_service import GatewaySessionService
from relay_teams.gateway.im.command_service import ImSessionCommandResult
from relay_teams.gateway.session_ingress_service import (
    GatewaySessionIngressBusyPolicy,
    GatewaySessionIngressRequest,
    GatewaySessionIngressService,
)
from relay_teams.logger import get_logger, log_event
from relay_teams.media import content_parts_from_text
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_models import (
    IntentInput,
    RuntimePromptConversationContext,
)
from relay_teams.sessions.runs.terminal_payload import (
    extract_terminal_error,
    extract_terminal_output,
    parse_terminal_payload_json,
)
from relay_teams.sessions.session_models import SessionMode
from relay_teams.validation import require_force_delete
from relay_teams.workspace import WorkspaceService

_TERMINAL_EVENT_TYPES = {
    RunEventType.RUN_COMPLETED,
    RunEventType.RUN_FAILED,
    RunEventType.RUN_STOPPED,
}
_INBOUND_QUEUE_CLAIM_STALE_AFTER_SECONDS = 60

LOGGER = get_logger(__name__)


class _RunEventSource(Protocol):
    @property
    def bound_event_loop(self) -> asyncio.AbstractEventLoop | None:
        raise NotImplementedError  # pragma: no cover

    async def create_run_async(self, intent: IntentInput) -> tuple[str, str]:
        raise NotImplementedError  # pragma: no cover

    async def ensure_run_started_async(self, run_id: str) -> None:
        raise NotImplementedError  # pragma: no cover

    def stream_run_events(self, run_id: str) -> AsyncIterator["_RunEventRecord"]:
        raise NotImplementedError  # pragma: no cover


class _RunEventRecord(Protocol):
    event_type: RunEventType
    payload_json: str


class _ImSessionCommandService(Protocol):
    def handle_discord_command(
        self,
        *,
        session_id: str,
        gateway_session_id: str,
        text: str,
    ) -> ImSessionCommandResult | None:
        raise NotImplementedError  # pragma: no cover


class _ImToolService(Protocol):
    async def send_text_to_discord_channel(
        self,
        *,
        account_id: str,
        channel_id: str,
        text: str,
        reply_to_message_id: str | None,
    ) -> None:
        raise NotImplementedError  # pragma: no cover


class _SessionRecoveryLookup(Protocol):
    async def get_recovery_snapshot_async(
        self,
        session_id: str,
    ) -> Mapping[str, object]:
        raise NotImplementedError  # pragma: no cover


class DiscordGatewaySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str
    running: bool = False
    last_error: str | None = None
    last_event_at: datetime | None = None
    last_inbound_at: datetime | None = None
    last_outbound_at: datetime | None = None


class DiscordGatewayService:
    def __init__(
        self,
        *,
        config_dir: Path,
        repository: DiscordAccountRepository,
        secret_store: DiscordSecretStore | None,
        client: DiscordClient,
        gateway_session_service: GatewaySessionService,
        run_service: _RunEventSource,
        workspace_service: WorkspaceService,
        orchestration_settings_service: OrchestrationSettingsService,
        im_tool_service: _ImToolService,
        im_session_command_service: _ImSessionCommandService,
        inbound_queue_repo: DiscordInboundQueueRepository,
        session_ingress_service: GatewaySessionIngressService | None = None,
        session_recovery_service: _SessionRecoveryLookup | None = None,
    ) -> None:
        self._config_dir = config_dir
        self._repository = repository
        self._secret_store = (
            get_discord_secret_store() if secret_store is None else secret_store
        )
        self._client = client
        self._gateway_session_service = gateway_session_service
        self._run_service = run_service
        self._workspace_service = workspace_service
        self._orchestration_settings_service = orchestration_settings_service
        self._im_tool_service = im_tool_service
        self._im_session_command_service = im_session_command_service
        self._inbound_queue_repo = inbound_queue_repo
        self._session_ingress_service = session_ingress_service
        self._session_recovery_service = session_recovery_service
        self._status_lock = Lock()
        self._status_by_account: dict[str, DiscordGatewaySnapshot] = {}
        self._workers: dict[str, DiscordGatewayWorker] = {}
        self._watched_runs: set[str] = set()
        self._drain_watched_runs: set[str] = set()
        self._worker_loop: asyncio.AbstractEventLoop | None = None

    async def start_async(self) -> None:
        self._worker_loop = asyncio.get_running_loop()
        for account in await self._repository.list_accounts():
            if account.status == DiscordAccountStatus.ENABLED:
                self._start_account_worker(account.account_id)

    def stop(self) -> None:
        for account_id in tuple(self._workers):
            self._stop_account_worker(account_id)

    async def reload_async(self) -> None:
        self._worker_loop = asyncio.get_running_loop()
        accounts = {
            item.account_id: item for item in await self._repository.list_accounts()
        }
        running = set(self._workers)
        desired = {
            account_id
            for account_id, account in accounts.items()
            if account.status == DiscordAccountStatus.ENABLED
        }
        for account_id in sorted(running - desired):
            self._stop_account_worker(account_id)
        for account_id in sorted(desired):
            self._start_account_worker(account_id)

    async def list_accounts(self) -> tuple[DiscordAccountRecord, ...]:
        accounts: list[DiscordAccountRecord] = []
        for account in await self._repository.list_accounts():
            accounts.append(await self._merge_status(account))
        return tuple(accounts)

    async def get_account(self, account_id: str) -> DiscordAccountRecord:
        return await self._merge_status(await self._repository.get_account(account_id))

    async def create_account(
        self,
        request: DiscordAccountCreateInput,
    ) -> DiscordAccountRecord:
        token = request.bot_token.strip()
        identity = await self._client.fetch_current_bot_identity(token=token)
        existing = await self._get_existing_account(identity.user_id)
        now = datetime.now(tz=timezone.utc)
        display_name = (
            request.display_name
            or (existing.display_name if existing is not None else identity.username)
            or identity.user_id
        )
        record = DiscordAccountRecord(
            account_id=identity.user_id,
            display_name=display_name,
            status=(
                DiscordAccountStatus.ENABLED
                if request.enabled
                else DiscordAccountStatus.DISABLED
            ),
            bot_user_id=identity.user_id,
            application_id=request.application_id or identity.application_id,
            allowed_channel_ids=request.allowed_channel_ids,
            allow_channel_messages=request.allow_channel_messages,
            workspace_id=request.workspace_id,
            session_mode=request.session_mode,
            normal_root_role_id=request.normal_root_role_id,
            orchestration_preset_id=self._resolve_orchestration_preset_id(
                session_mode=request.session_mode,
                requested_preset_id=request.orchestration_preset_id,
                existing_preset_id=None,
            ),
            yolo=request.yolo,
            thinking=request.thinking,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        await self._validate_workspace(record.workspace_id)
        self._secret_store.set_bot_token(self._config_dir, record.account_id, token)
        stored = await self._repository.upsert_account(record)
        await self.reload_async()
        return await self._merge_status(stored)

    async def update_account(
        self,
        account_id: str,
        request: DiscordAccountUpdateInput,
    ) -> DiscordAccountRecord:
        existing = await self._repository.get_account(account_id)
        if request.bot_token is not None:
            identity = await self._client.fetch_current_bot_identity(
                token=request.bot_token
            )
            if identity.user_id != existing.account_id:
                raise ValueError("discord_bot_token_account_mismatch")
            self._secret_store.set_bot_token(
                self._config_dir,
                existing.account_id,
                request.bot_token,
            )
        session_mode = request.session_mode or existing.session_mode
        workspace_id = request.workspace_id or existing.workspace_id
        await self._validate_workspace(workspace_id)
        updated = existing.model_copy(
            update={
                "display_name": request.display_name or existing.display_name,
                "status": (
                    DiscordAccountStatus.ENABLED
                    if request.enabled is True
                    else (
                        DiscordAccountStatus.DISABLED
                        if request.enabled is False
                        else existing.status
                    )
                ),
                "application_id": (
                    request.application_id
                    if "application_id" in request.model_fields_set
                    else existing.application_id
                ),
                "allowed_channel_ids": (
                    request.allowed_channel_ids
                    if request.allowed_channel_ids is not None
                    else existing.allowed_channel_ids
                ),
                "allow_channel_messages": (
                    request.allow_channel_messages
                    if request.allow_channel_messages is not None
                    else existing.allow_channel_messages
                ),
                "workspace_id": workspace_id,
                "session_mode": session_mode,
                "normal_root_role_id": (
                    request.normal_root_role_id
                    if "normal_root_role_id" in request.model_fields_set
                    else existing.normal_root_role_id
                ),
                "orchestration_preset_id": self._resolve_orchestration_preset_id(
                    session_mode=session_mode,
                    requested_preset_id=request.orchestration_preset_id,
                    existing_preset_id=existing.orchestration_preset_id,
                ),
                "yolo": request.yolo if request.yolo is not None else existing.yolo,
                "thinking": request.thinking or existing.thinking,
                "updated_at": datetime.now(tz=timezone.utc),
            }
        )
        stored = await self._repository.upsert_account(updated)
        await self.reload_async()
        return await self._merge_status(stored)

    async def set_account_enabled(
        self,
        account_id: str,
        enabled: bool,
    ) -> DiscordAccountRecord:
        return await self.update_account(
            account_id,
            DiscordAccountUpdateInput(enabled=enabled),
        )

    async def delete_account(self, account_id: str, *, force: bool = False) -> None:
        account = await self._repository.get_account(account_id)
        if account.status == DiscordAccountStatus.ENABLED:
            require_force_delete(
                force,
                message="Cannot delete enabled Discord account without force",
            )
        self._stop_account_worker(account_id)
        self._secret_store.delete_bot_token(self._config_dir, account_id)
        await self._repository.delete_account(account_id)

    async def handle_inbound_message(
        self,
        *,
        account_id: str,
        message: DiscordInboundMessage,
    ) -> None:
        try:
            account = await self._repository.get_account(account_id)
        except KeyError:
            return
        if account.status != DiscordAccountStatus.ENABLED:
            return
        text = self._accepted_text(account=account, message=message)
        if text is None:
            return
        now = datetime.now(tz=timezone.utc)
        self._set_status(account.account_id, last_inbound_at=now, last_event_at=now)
        gateway_session = await asyncio.to_thread(
            self._resolve_gateway_session,
            account,
            message,
            now,
        )
        command_result = await asyncio.to_thread(
            self._im_session_command_service.handle_discord_command,
            session_id=gateway_session.internal_session_id,
            gateway_session_id=gateway_session.gateway_session_id,
            text=text,
        )
        if command_result is not None:
            response_text = str(command_result.text)
            await self._send_intermediate_text(
                account_id=account.account_id,
                gateway_session_id=gateway_session.gateway_session_id,
                channel_id=self._reply_channel_id(message),
                reply_to_message_id=message.message_id,
                text=response_text,
                event_name="discord.command.response",
                failure_message="Failed to send Discord command response",
            )
            resumed_run_id = command_result.resumed_run_id
            if resumed_run_id is not None:
                self._start_run_watcher(
                    account_id=account.account_id,
                    gateway_session_id=gateway_session.gateway_session_id,
                    run_id=resumed_run_id,
                    channel_id=self._reply_channel_id(message),
                    reply_to_message_id=message.message_id,
                )
            return
        queue_record, created = await self._inbound_queue_repo.create_or_get(
            DiscordInboundQueueRecord(
                inbound_queue_id=f"dq_{uuid4().hex[:16]}",
                account_id=account.account_id,
                message_key=f"mid:{message.message_id}",
                gateway_session_id=gateway_session.gateway_session_id,
                session_id=gateway_session.internal_session_id,
                peer_user_id=message.author_id,
                channel_id=self._reply_channel_id(message),
                guild_id=message.guild_id,
                thread_id=message.thread_id,
                reply_to_message_id=message.message_id,
                text=text,
            )
        )
        if not created:
            return
        await self._drain_inbound_queue()
        latest = await self._inbound_queue_repo.get(queue_record.inbound_queue_id)
        if latest is None:
            return
        await self._send_intermediate_text(
            account_id=account.account_id,
            gateway_session_id=gateway_session.gateway_session_id,
            channel_id=latest.channel_id,
            reply_to_message_id=latest.reply_to_message_id,
            text=await self._build_receipt_text(latest),
            event_name="discord.receipt",
            failure_message="Failed to send Discord receipt",
        )

    def _resolve_gateway_session(
        self,
        account: DiscordAccountRecord,
        message: DiscordInboundMessage,
        now: datetime,
    ) -> GatewaySessionRecord:
        chat_type = DiscordChatType.DIRECT if message.is_dm else DiscordChatType.GUILD
        external_session_id = self._external_session_id(
            account_id=account.account_id,
            message=message,
        )
        title_peer = message.author_name.strip() or message.author_id
        metadata = {
            "title": f"{account.display_name} - {title_peer}",
            "source_kind": "im",
            "source_provider": DISCORD_PLATFORM,
            "source_label": account.display_name,
        }
        return self._gateway_session_service.resolve_or_create_session(
            channel_type=GatewayChannelType.DISCORD,
            external_session_id=external_session_id,
            workspace_id=account.workspace_id,
            metadata=metadata,
            session_mode=account.session_mode,
            normal_root_role_id=account.normal_root_role_id,
            orchestration_preset_id=account.orchestration_preset_id,
            peer_user_id=message.author_id,
            peer_chat_id=self._reply_channel_id(message),
            capabilities={"chat_type": chat_type.value},
            channel_state={
                "account_id": account.account_id,
                "channel_id": self._reply_channel_id(message),
                "guild_id": message.guild_id,
                "thread_id": message.thread_id,
                "peer_user_id": message.author_id,
                "chat_type": chat_type.value,
                "reply_to_message_id": message.message_id,
                "last_inbound_at": now.isoformat(),
            },
        )

    async def _drain_inbound_queue(self) -> None:
        stale_before = datetime.now(tz=timezone.utc) - timedelta(
            seconds=_INBOUND_QUEUE_CLAIM_STALE_AFTER_SECONDS
        )
        for record in await self._inbound_queue_repo.list_ready_to_start(
            stale_before=stale_before
        ):
            claimed = await self._inbound_queue_repo.claim_starting(
                inbound_queue_id=record.inbound_queue_id,
                stale_before=stale_before,
            )
            if claimed is None:
                continue
            blocking_run_id = await self._active_run_id(claimed.session_id)
            if blocking_run_id is not None:
                self._start_queue_drain_watcher(
                    session_id=claimed.session_id,
                    run_id=blocking_run_id,
                )
            if await self._inbound_queue_repo.count_non_terminal_ahead(
                claimed.inbound_queue_id
            ):
                _ = await self._inbound_queue_repo.requeue_if_starting(
                    inbound_queue_id=claimed.inbound_queue_id
                )
                continue
            if blocking_run_id is not None:
                _ = await self._inbound_queue_repo.requeue_if_starting(
                    inbound_queue_id=claimed.inbound_queue_id
                )
                continue
            await self._start_queued_record(claimed)

    async def _start_queued_record(self, record: DiscordInboundQueueRecord) -> bool:
        try:
            account = await self._repository.get_account(record.account_id)
        except KeyError:
            await self._fail_starting_record(
                inbound_queue_id=record.inbound_queue_id,
                error_message=f"Discord account not found: {record.account_id}",
            )
            return False
        intent = IntentInput(
            session_id=record.session_id,
            input=content_parts_from_text(record.text),
            yolo=account.yolo,
            thinking=account.thinking,
            conversation_context=RuntimePromptConversationContext(
                source_provider=DISCORD_PLATFORM,
                source_kind="im",
                im_reply_to_message_id=record.reply_to_message_id,
            ),
        )
        try:
            result = await self._start_session_ingress_run(intent)
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="discord.inbound_queue.start_failed",
                message="Failed to start Discord inbound queue run",
                payload={
                    "inbound_queue_id": record.inbound_queue_id,
                    "account_id": record.account_id,
                    "gateway_session_id": record.gateway_session_id,
                    "session_id": record.session_id,
                    "error": str(exc),
                },
                exc_info=exc,
            )
            _ = await self._inbound_queue_repo.requeue_if_starting(
                inbound_queue_id=record.inbound_queue_id,
                last_error=str(exc),
            )
            return False
        if result is None:
            _ = await self._inbound_queue_repo.requeue_if_starting(
                inbound_queue_id=record.inbound_queue_id
            )
            return False
        now = datetime.now(tz=timezone.utc)
        current = await self._inbound_queue_repo.get(record.inbound_queue_id)
        if current is None or current.status != DiscordInboundQueueStatus.STARTING:
            return False
        updated = await self._inbound_queue_repo.update(
            current.model_copy(
                update={
                    "status": DiscordInboundQueueStatus.WAITING_RESULT,
                    "run_id": result,
                    "last_error": None,
                    "updated_at": now,
                }
            )
        )
        await asyncio.to_thread(
            self._gateway_session_service.bind_active_run,
            updated.gateway_session_id,
            result,
        )
        self._start_run_watcher(
            account_id=updated.account_id,
            gateway_session_id=updated.gateway_session_id,
            run_id=result,
            channel_id=updated.channel_id,
            reply_to_message_id=updated.reply_to_message_id,
        )
        return True

    async def _fail_starting_record(
        self,
        *,
        inbound_queue_id: str,
        error_message: str,
    ) -> None:
        current = await self._inbound_queue_repo.get(inbound_queue_id)
        if current is None or current.status != DiscordInboundQueueStatus.STARTING:
            return
        now = datetime.now(tz=timezone.utc)
        _ = await self._inbound_queue_repo.update(
            current.model_copy(
                update={
                    "status": DiscordInboundQueueStatus.FAILED,
                    "run_id": None,
                    "last_error": error_message,
                    "updated_at": now,
                    "completed_at": now,
                }
            )
        )

    async def _start_session_ingress_run(self, intent: IntentInput) -> str | None:
        if self._session_ingress_service is not None:
            result = await self._session_ingress_service.submit_async(
                GatewaySessionIngressRequest(
                    intent=intent,
                    busy_policy=GatewaySessionIngressBusyPolicy.QUEUE_IF_BUSY,
                )
            )
            return result.run_id
        run_id, _ = await self._run_service.create_run_async(intent)
        await self._run_service.ensure_run_started_async(run_id)
        return run_id

    async def _active_run_id(self, session_id: str) -> str | None:
        if self._session_ingress_service is not None:
            return await self._session_ingress_service.active_run_id_async(session_id)
        if self._session_recovery_service is None:
            return None
        recovery_snapshot = (
            await self._session_recovery_service.get_recovery_snapshot_async(session_id)
        )
        active_run = recovery_snapshot.get("active_run")
        if not isinstance(active_run, Mapping):
            return None
        run_id = active_run.get("run_id")
        if isinstance(run_id, str) and run_id.strip():
            return run_id.strip()
        return None

    def _start_run_watcher(
        self,
        *,
        account_id: str,
        gateway_session_id: str,
        run_id: str,
        channel_id: str,
        reply_to_message_id: str | None,
    ) -> None:
        if run_id in self._watched_runs:
            return
        self._watched_runs.add(run_id)
        future = asyncio.run_coroutine_threadsafe(
            self._await_terminal_and_reply(
                account_id=account_id,
                gateway_session_id=gateway_session_id,
                run_id=run_id,
                channel_id=channel_id,
                reply_to_message_id=reply_to_message_id,
            ),
            self._require_loop(),
        )

        def on_reply_done(done: ConcurrentFuture[None]) -> None:
            self._handle_reply_future(
                account_id=account_id,
                gateway_session_id=gateway_session_id,
                run_id=run_id,
                channel_id=channel_id,
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
        channel_id: str,
        reply_to_message_id: str | None,
    ) -> None:
        try:
            async for event in self._run_service.stream_run_events(run_id):
                if event.event_type == RunEventType.RUN_PAUSED:
                    text = self._paused_text(event)
                    await self._im_tool_service.send_text_to_discord_channel(
                        account_id=account_id,
                        channel_id=channel_id,
                        text=text,
                        reply_to_message_id=reply_to_message_id,
                    )
                    self._record_pause_notice(
                        account_id=account_id,
                        occurred_at=datetime.now(tz=timezone.utc),
                    )
                    return
                if event.event_type not in _TERMINAL_EVENT_TYPES:
                    continue
                text = self._terminal_text(event)
                await self._im_tool_service.send_text_to_discord_channel(
                    account_id=account_id,
                    channel_id=channel_id,
                    text=text,
                    reply_to_message_id=reply_to_message_id,
                )
                self._record_reply_success(
                    account_id=account_id,
                    gateway_session_id=gateway_session_id,
                    run_id=run_id,
                    channel_id=channel_id,
                    reply_to_message_id=reply_to_message_id,
                    occurred_at=datetime.now(tz=timezone.utc),
                )
                return
            raise RuntimeError(
                f"Discord reply watcher ended before a stop event for {run_id}."
            )
        except Exception as exc:
            await self._record_reply_failure(
                account_id=account_id,
                gateway_session_id=gateway_session_id,
                run_id=run_id,
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
                and await self._active_run_id(session_id) != run_id
            ):
                return

    @staticmethod
    def _handle_reply_future(
        *,
        account_id: str,
        gateway_session_id: str,
        run_id: str,
        channel_id: str,
        future: ConcurrentFuture[None],
    ) -> None:
        try:
            future.result()
        except FutureCancelledError as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="discord.reply.cancelled",
                message="Discord reply task was cancelled",
                payload={
                    "account_id": account_id,
                    "gateway_session_id": gateway_session_id,
                    "run_id": run_id,
                    "channel_id": channel_id,
                },
                exc_info=exc,
            )
        except Exception as exc:
            log_event(
                LOGGER,
                logging.ERROR,
                event="discord.reply.failed",
                message="Discord reply task failed",
                payload={
                    "account_id": account_id,
                    "gateway_session_id": gateway_session_id,
                    "run_id": run_id,
                    "channel_id": channel_id,
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
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="discord.queue_drain.failed",
                message="Discord queue drain watcher failed",
                payload={"session_id": session_id, "run_id": run_id, "error": str(exc)},
                exc_info=exc,
            )
        finally:
            self._drain_watched_runs.discard(run_id)
            self._run_or_schedule(self._drain_inbound_queue())

    def _record_reply_success(
        self,
        *,
        account_id: str,
        gateway_session_id: str,
        run_id: str,
        channel_id: str,
        reply_to_message_id: str | None,
        occurred_at: datetime,
    ) -> None:
        try:
            self._gateway_session_service.update_channel_state(
                gateway_session_id,
                channel_state={
                    "channel_id": channel_id,
                    "reply_to_message_id": reply_to_message_id,
                    "last_outbound_at": occurred_at.isoformat(),
                },
                peer_chat_id=channel_id,
            )
        except KeyError as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="discord.reply.channel_state_update_failed",
                message="Failed to update Discord channel state after sending reply",
                payload={
                    "account_id": account_id,
                    "gateway_session_id": gateway_session_id,
                    "run_id": run_id,
                    "error": str(exc),
                },
                exc_info=exc,
            )
        self._clear_active_run(gateway_session_id)
        self._run_or_schedule(self._mark_queue_record_completed(run_id=run_id))
        self._set_status(
            account_id,
            last_error=None,
            last_outbound_at=occurred_at,
            last_event_at=occurred_at,
        )
        self._run_or_schedule(self._drain_inbound_queue())

    async def _record_reply_failure(
        self,
        *,
        account_id: str,
        gateway_session_id: str,
        run_id: str,
        error_message: str,
    ) -> None:
        self._clear_active_run(gateway_session_id)
        await self._mark_queue_record_completed(
            run_id=run_id,
            failed=True,
            error_message=error_message,
        )
        self._set_status(
            account_id,
            last_error=error_message,
            last_event_at=datetime.now(tz=timezone.utc),
        )
        await self._drain_inbound_queue()

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
        except KeyError:
            return

    async def _send_intermediate_text(
        self,
        *,
        account_id: str,
        gateway_session_id: str,
        channel_id: str,
        reply_to_message_id: str | None,
        text: str,
        event_name: str,
        failure_message: str,
    ) -> None:
        try:
            await self._im_tool_service.send_text_to_discord_channel(
                account_id=account_id,
                channel_id=channel_id,
                text=text,
                reply_to_message_id=reply_to_message_id,
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
                    "channel_id": channel_id,
                    "error": str(exc),
                },
                exc_info=exc,
            )
            return
        self._record_intermediate_outbound(
            account_id=account_id,
            gateway_session_id=gateway_session_id,
            channel_id=channel_id,
            reply_to_message_id=reply_to_message_id,
            occurred_at=datetime.now(tz=timezone.utc),
        )

    def _record_intermediate_outbound(
        self,
        *,
        account_id: str,
        gateway_session_id: str,
        channel_id: str,
        reply_to_message_id: str | None,
        occurred_at: datetime,
    ) -> None:
        try:
            self._gateway_session_service.update_channel_state(
                gateway_session_id,
                channel_state={
                    "channel_id": channel_id,
                    "reply_to_message_id": reply_to_message_id,
                    "last_outbound_at": occurred_at.isoformat(),
                },
                peer_chat_id=channel_id,
            )
        except KeyError:
            return
        self._set_status(
            account_id,
            last_error=None,
            last_outbound_at=occurred_at,
            last_event_at=occurred_at,
        )

    async def _mark_queue_record_completed(
        self,
        *,
        run_id: str,
        failed: bool = False,
        error_message: str | None = None,
    ) -> None:
        record = await self._inbound_queue_repo.get_latest_by_run_id(run_id)
        if record is None:
            return
        now = datetime.now(tz=timezone.utc)
        _ = await self._inbound_queue_repo.update(
            record.model_copy(
                update={
                    "status": (
                        DiscordInboundQueueStatus.FAILED
                        if failed
                        else DiscordInboundQueueStatus.COMPLETED
                    ),
                    "last_error": error_message if failed else None,
                    "updated_at": now,
                    "completed_at": now,
                }
            )
        )

    async def _build_receipt_text(self, record: DiscordInboundQueueRecord) -> str:
        if record.status == DiscordInboundQueueStatus.FAILED:
            error_message = str(record.last_error or "").strip()
            if error_message:
                return f"Received, but processing failed: {error_message}"
            return "Received, but processing failed."
        if record.status == DiscordInboundQueueStatus.WAITING_RESULT:
            return "Received. Processing now."
        queue_depth = await self._queue_depth(record)
        if queue_depth <= 0:
            return "Received. Processing now."
        return f"Received. Queued behind {queue_depth} message(s) in this session."

    async def _queue_depth(self, record: DiscordInboundQueueRecord) -> int:
        ahead_count = await self._inbound_queue_repo.count_non_terminal_ahead(
            record.inbound_queue_id
        )
        blocking_run_id = await self._active_run_id(record.session_id)
        if blocking_run_id is None:
            return ahead_count
        if await self._inbound_queue_repo.has_non_terminal_item_for_run(
            blocking_run_id
        ):
            return ahead_count
        return ahead_count + 1

    def _start_account_worker(self, account_id: str) -> None:
        worker = self._workers.get(account_id)
        if worker is not None:
            if worker.is_alive():
                return
            worker.stop()
            self._workers.pop(account_id, None)
        token = self._secret_store.get_bot_token(self._config_dir, account_id)
        if token is None:
            self._set_status(account_id, running=False, last_error="missing_token")
            return
        loop = self._worker_loop or self._run_service.bound_event_loop
        if loop is None:
            self._set_status(account_id, running=False, last_error="missing_loop")
            return
        worker = DiscordGatewayWorker(
            account_id=account_id,
            target_loop=loop,
            handle_message=self._handle_worker_message,
            set_running=lambda running, error: self._set_status(
                account_id,
                running=running,
                last_error=error,
                last_event_at=datetime.now(tz=timezone.utc),
            ),
        )
        self._workers[account_id] = worker
        worker.start(token=token)

    def _stop_account_worker(self, account_id: str) -> None:
        worker = self._workers.get(account_id)
        if worker is not None:
            worker.stop()
            if not worker.is_alive():
                self._workers.pop(account_id, None)
        self._set_status(
            account_id,
            running=False,
            last_error=(
                "stop_timeout" if worker is not None and worker.is_alive() else None
            ),
        )

    async def _handle_worker_message(
        self,
        account_id: str,
        message: DiscordInboundMessage,
    ) -> None:
        await self.handle_inbound_message(account_id=account_id, message=message)

    async def _merge_status(
        self,
        account: DiscordAccountRecord,
    ) -> DiscordAccountRecord:
        snapshot = self._status(account.account_id)
        token = self._secret_store.get_bot_token(self._config_dir, account.account_id)
        return account.model_copy(
            update={
                "secret_status": DiscordSecretStatus(
                    bot_token_configured=token is not None
                ),
                "running": snapshot.running,
                "last_error": snapshot.last_error,
                "last_event_at": snapshot.last_event_at,
                "last_inbound_at": snapshot.last_inbound_at,
                "last_outbound_at": snapshot.last_outbound_at,
            }
        )

    def _status(self, account_id: str) -> DiscordGatewaySnapshot:
        with self._status_lock:
            existing = self._status_by_account.get(account_id)
            if existing is not None:
                return existing
            fresh = DiscordGatewaySnapshot(account_id=account_id)
            self._status_by_account[account_id] = fresh
            return fresh

    def _set_status(self, account_id: str, **updates: object) -> None:
        with self._status_lock:
            existing = self._status_by_account.get(account_id)
            if existing is None:
                existing = DiscordGatewaySnapshot(account_id=account_id)
            self._status_by_account[account_id] = existing.model_copy(update=updates)

    async def _get_existing_account(
        self,
        account_id: str,
    ) -> DiscordAccountRecord | None:
        try:
            return await self._repository.get_account(account_id)
        except KeyError:
            return None

    async def _validate_workspace(self, workspace_id: str) -> None:
        await asyncio.to_thread(self._workspace_service.get_workspace, workspace_id)

    def _resolve_orchestration_preset_id(
        self,
        *,
        session_mode: SessionMode,
        requested_preset_id: str | None,
        existing_preset_id: str | None,
    ) -> str | None:
        if session_mode != SessionMode.ORCHESTRATION:
            return None
        preset_id = requested_preset_id or existing_preset_id
        if preset_id is None:
            preset_id = (
                self._orchestration_settings_service.default_orchestration_preset_id()
            )
        if preset_id is None:
            raise ValueError("orchestration_preset_id is required")
        return preset_id

    @staticmethod
    def _accepted_text(
        *,
        account: DiscordAccountRecord,
        message: DiscordInboundMessage,
    ) -> str | None:
        if message.author_is_bot:
            return None
        if account.bot_user_id is not None and message.author_id == account.bot_user_id:
            return None
        text = message.content.strip()
        if not text:
            return None
        if message.is_dm:
            return text
        if message.mentions_bot:
            stripped_text = _strip_discord_bot_mentions(text)
            return stripped_text or None
        if (
            account.allow_channel_messages
            and message.channel_id in account.allowed_channel_ids
        ):
            return text
        return None

    @staticmethod
    def _reply_channel_id(message: DiscordInboundMessage) -> str:
        return message.thread_id or message.channel_id

    @staticmethod
    def _external_session_id(*, account_id: str, message: DiscordInboundMessage) -> str:
        if message.is_dm:
            return f"discord:{account_id}:dm:{message.author_id}"
        guild_id = message.guild_id or "unknown"
        if message.thread_id is not None:
            return (
                f"discord:{account_id}:guild:{guild_id}:"
                f"channel:{message.channel_id}:thread:{message.thread_id}"
            )
        return f"discord:{account_id}:guild:{guild_id}:channel:{message.channel_id}"

    @staticmethod
    def _run_or_schedule(coroutine: Coroutine[object, object, None]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(coroutine)
            return
        _ = loop.create_task(coroutine)

    def _require_loop(self) -> asyncio.AbstractEventLoop:
        loop = self._run_service.bound_event_loop or self._worker_loop
        if loop is None:
            raise RuntimeError("SessionRunService event loop is not bound")
        return loop

    @staticmethod
    def _terminal_text(event: _RunEventRecord) -> str:
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
    def _paused_text(event: _RunEventRecord) -> str:
        try:
            payload = json.loads(event.payload_json)
        except json.JSONDecodeError:
            payload = {}
        error_message = payload.get("error_message")
        if isinstance(error_message, str) and error_message.strip():
            return f"Run paused: {error_message.strip()}\nSend resume to continue."
        return "Run paused.\nSend resume to continue."


def _strip_discord_bot_mentions(text: str) -> str:
    parts: list[str] = []
    for raw_part in text.split():
        if raw_part.startswith("<@") and raw_part.endswith(">"):
            continue
        parts.append(raw_part)
    return " ".join(parts).strip()
