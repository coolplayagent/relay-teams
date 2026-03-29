# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from typing import Protocol
from uuid import uuid4

from agent_teams.gateway.feishu.inbound_runtime import FeishuInboundRuntime
from agent_teams.gateway.feishu.message_pool_repository import (
    FeishuMessagePoolRepository,
)
from agent_teams.gateway.feishu.models import (
    FeishuChatQueueClearResult,
    FeishuChatQueueItemPreview,
    FeishuChatQueueSummary,
    FeishuEnvironment,
    FeishuMessageDeliveryStatus,
    FeishuMessagePoolRecord,
    FeishuMessageProcessingStatus,
    FeishuNormalizedMessage,
    FeishuTriggerRuntimeConfig,
    TriggerProcessingResult,
)
from agent_teams.logger import get_logger, log_event
from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRecord,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from agent_teams.sessions.runs.terminal_payload import (
    extract_terminal_error,
    extract_terminal_output,
)

logger = get_logger(__name__)

_ACK_MAX_ATTEMPTS = 3
_FINAL_REPLY_MAX_ATTEMPTS = 5
_REACTION_MAX_ATTEMPTS = 3
_POLL_INTERVAL_SECONDS = 1.0
_STALE_CLAIM_SECONDS = 60.0
_WAITING_RUNTIME_TIMEOUT = timedelta(seconds=15)
_WAITING_QUEUED_TIMEOUT = timedelta(seconds=15)
_ACK_REACTION_TYPE = "OK"
_ACTIVE_PROCESSING_STATUSES = {
    FeishuMessageProcessingStatus.QUEUED,
    FeishuMessageProcessingStatus.CLAIMED,
    FeishuMessageProcessingStatus.WAITING_RESULT,
    FeishuMessageProcessingStatus.RETRYABLE_FAILED,
}


class FeishuRuntimeConfigLookup(Protocol):
    def get_runtime_config_by_trigger_id(
        self,
        trigger_id: str,
    ) -> FeishuTriggerRuntimeConfig | None: ...


class FeishuClientLike(Protocol):
    def reply_text_message(
        self,
        *,
        message_id: str,
        text: str,
        environment: FeishuEnvironment | None = None,
    ) -> None: ...

    def create_message_reaction(
        self,
        *,
        message_id: str,
        reaction_type: str,
        environment: FeishuEnvironment | None = None,
    ) -> None: ...

    def send_text_message(
        self,
        *,
        chat_id: str,
        text: str,
        environment: FeishuEnvironment | None = None,
    ) -> str: ...

    def resolve_user_name(
        self,
        *,
        open_id: str,
        chat_id: str | None = None,
        environment: FeishuEnvironment | None = None,
    ) -> str | None: ...


class FeishuMessagePoolService:
    def __init__(
        self,
        *,
        runtime_config_lookup: FeishuRuntimeConfigLookup,
        inbound_runtime: FeishuInboundRuntime,
        feishu_client: FeishuClientLike | None,
        message_pool_repo: FeishuMessagePoolRepository,
        run_runtime_repo: RunRuntimeRepository,
        event_log: EventLog,
    ) -> None:
        self._runtime_config_lookup = runtime_config_lookup
        self._inbound_runtime = inbound_runtime
        self._feishu_client = feishu_client
        self._message_pool_repo = message_pool_repo
        self._run_runtime_repo = run_runtime_repo
        self._event_log = event_log
        self._stop_event = Event()
        self._wake_event = Event()
        self._thread: Thread | None = None
        self._pause_notice_keys: set[str] = set()

    def start(self) -> None:
        self._message_pool_repo.recover_stale_claims(
            claimed_before=datetime.now(tz=timezone.utc)
            - timedelta(seconds=_STALE_CLAIM_SECONDS)
        )
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._wake_event.clear()
        thread = Thread(
            target=self._run_loop,
            name="feishu-message-pool",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread is None:
            return
        thread.join(timeout=10)
        self._thread = None

    def enqueue_message(
        self,
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        normalized: FeishuNormalizedMessage,
        raw_body: str,
        headers: dict[str, str],
        remote_addr: str | None,
    ) -> TriggerProcessingResult:
        _ = (raw_body, headers, remote_addr)
        enriched = self._enrich_sender_name(
            normalized=normalized,
            runtime_config=runtime_config,
        )
        now = datetime.now(tz=timezone.utc)
        reaction_status = (
            FeishuMessageDeliveryStatus.PENDING
            if self._feishu_client is not None
            and self._should_send_reaction_acknowledgement(enriched)
            else FeishuMessageDeliveryStatus.SKIPPED
        )
        final_reply_status = (
            FeishuMessageDeliveryStatus.PENDING
            if self._feishu_client is not None
            else FeishuMessageDeliveryStatus.SKIPPED
        )
        record, created = self._message_pool_repo.create_or_get(
            FeishuMessagePoolRecord(
                message_pool_id=f"fmp_{uuid4().hex[:16]}",
                trigger_id=runtime_config.trigger_id,
                trigger_name=runtime_config.trigger_name,
                tenant_key=enriched.tenant_key,
                chat_id=enriched.chat_id,
                chat_type=enriched.chat_type,
                event_id=enriched.event_id,
                message_key=_message_key(enriched),
                message_id=enriched.message_id,
                sender_name=enriched.sender_name,
                intent_text=enriched.trigger_text,
                payload=enriched.payload,
                metadata=enriched.metadata,
                processing_status=FeishuMessageProcessingStatus.QUEUED,
                reaction_status=reaction_status,
                reaction_type=(
                    _ACK_REACTION_TYPE
                    if reaction_status == FeishuMessageDeliveryStatus.PENDING
                    else None
                ),
                ack_status=FeishuMessageDeliveryStatus.SKIPPED,
                final_reply_status=final_reply_status,
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        if not created:
            return TriggerProcessingResult(
                status="accepted",
                trigger_id=runtime_config.trigger_id,
                trigger_name=runtime_config.trigger_name,
                event_id=record.event_id,
                duplicate=True,
            )
        queue_depth = self._message_pool_repo.count_active_chat_messages_ahead(
            record.message_pool_id
        )
        queue_reply_text = _build_queue_reply_text(queue_depth)
        updated = self._message_pool_repo.update(
            record.message_pool_id,
            ack_status=(
                FeishuMessageDeliveryStatus.PENDING
                if self._feishu_client is not None and queue_reply_text is not None
                else FeishuMessageDeliveryStatus.SKIPPED
            ),
            ack_text=queue_reply_text,
            last_error=None,
        )
        self._attempt_reaction(updated)
        self._attempt_queue_reply(updated)
        self._wake_event.set()
        return TriggerProcessingResult(
            status="accepted",
            trigger_id=runtime_config.trigger_id,
            trigger_name=runtime_config.trigger_name,
            event_id=record.event_id,
            duplicate=False,
        )

    def get_chat_summary(
        self,
        *,
        trigger_id: str,
        tenant_key: str,
        chat_id: str,
        preview_limit: int = 3,
    ) -> FeishuChatQueueSummary:
        counts = self._message_pool_repo.get_chat_status_counts(
            trigger_id=trigger_id,
            tenant_key=tenant_key,
            chat_id=chat_id,
        )
        active_records = self._message_pool_repo.list_active_chat_messages(
            trigger_id=trigger_id,
            tenant_key=tenant_key,
            chat_id=chat_id,
        )
        processing_item: FeishuChatQueueItemPreview | None = None
        queued_items: list[FeishuChatQueueItemPreview] = []
        for index, record in enumerate(active_records):
            preview = self._build_queue_preview(record)
            if index == 0:
                processing_item = preview
                continue
            if len(queued_items) < max(0, preview_limit):
                queued_items.append(preview)
        return FeishuChatQueueSummary(
            trigger_id=trigger_id,
            tenant_key=tenant_key,
            chat_id=chat_id,
            active_total=len(active_records),
            queued_count=counts[FeishuMessageProcessingStatus.QUEUED],
            claimed_count=counts[FeishuMessageProcessingStatus.CLAIMED],
            waiting_result_count=counts[FeishuMessageProcessingStatus.WAITING_RESULT],
            retryable_failed_count=counts[
                FeishuMessageProcessingStatus.RETRYABLE_FAILED
            ],
            cancelled_count=counts[FeishuMessageProcessingStatus.CANCELLED],
            dead_letter_count=counts[FeishuMessageProcessingStatus.DEAD_LETTER],
            processing_item=processing_item,
            queued_items=tuple(queued_items),
        )

    def clear_chat(
        self,
        *,
        trigger_id: str,
        tenant_key: str,
        chat_id: str,
    ) -> FeishuChatQueueClearResult:
        active_records = self._message_pool_repo.list_active_chat_messages(
            trigger_id=trigger_id,
            tenant_key=tenant_key,
            chat_id=chat_id,
        )
        stopped_run_count = 0
        run_ids = {
            str(record.run_id)
            for record in active_records
            if str(record.run_id or "").strip()
        }
        for run_id in run_ids:
            try:
                self._inbound_runtime.stop_run(run_id)
            except (KeyError, RuntimeError) as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    event="feishu.message_pool.clear.stop_failed",
                    message="Failed to stop run while clearing Feishu queue",
                    payload={
                        "trigger_id": trigger_id,
                        "tenant_key": tenant_key,
                        "chat_id": chat_id,
                        "run_id": run_id,
                        "error": str(exc),
                    },
                )
            else:
                stopped_run_count += 1
        cleared_queue_count = self._message_pool_repo.cancel_active_chat_messages(
            trigger_id=trigger_id,
            tenant_key=tenant_key,
            chat_id=chat_id,
            cancelled_at=datetime.now(tz=timezone.utc),
        )
        self._wake_event.set()
        return FeishuChatQueueClearResult(
            trigger_id=trigger_id,
            tenant_key=tenant_key,
            chat_id=chat_id,
            cleared_queue_count=cleared_queue_count,
            stopped_run_count=stopped_run_count,
        )

    def should_suppress_terminal_notification(self, run_id: str | None) -> bool:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return False
        record = self._message_pool_repo.get_latest_by_run_id(normalized_run_id)
        if record is None:
            return False
        if record.processing_status == FeishuMessageProcessingStatus.CANCELLED:
            return True
        return (
            record.processing_status == FeishuMessageProcessingStatus.WAITING_RESULT
            and record.final_reply_status
            in {
                FeishuMessageDeliveryStatus.PENDING,
                FeishuMessageDeliveryStatus.FAILED,
                FeishuMessageDeliveryStatus.SKIPPED,
            }
        )

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            progress = False
            try:
                progress = self._retry_pending_reactions() or progress
                progress = self._retry_pending_queue_replies() or progress
                progress = self._process_queued_messages() or progress
                progress = self._finalize_waiting_results() or progress
            except Exception as exc:
                log_event(
                    logger,
                    logging.ERROR,
                    event="feishu.message_pool.loop_failed",
                    message="Feishu message pool loop failed",
                    payload={"error": str(exc)},
                    exc_info=exc,
                )
            if progress:
                continue
            self._wake_event.wait(timeout=_POLL_INTERVAL_SECONDS)
            self._wake_event.clear()

    def _retry_pending_reactions(self, *, limit: int = 20) -> bool:
        progress = False
        for record in self._message_pool_repo.list_pending_reactions(limit=limit):
            if record.reaction_status != FeishuMessageDeliveryStatus.PENDING:
                continue
            if record.reaction_attempts >= _REACTION_MAX_ATTEMPTS:
                self._message_pool_repo.update(
                    record.message_pool_id,
                    reaction_status=FeishuMessageDeliveryStatus.FAILED,
                )
                progress = True
                continue
            progress = self._attempt_reaction(record) or progress
        return progress

    def _retry_pending_queue_replies(self, *, limit: int = 20) -> bool:
        progress = False
        for record in self._message_pool_repo.list_pending_acknowledgements(
            limit=limit
        ):
            if record.ack_status != FeishuMessageDeliveryStatus.PENDING:
                continue
            if record.ack_attempts >= _ACK_MAX_ATTEMPTS:
                self._message_pool_repo.update(
                    record.message_pool_id,
                    ack_status=FeishuMessageDeliveryStatus.FAILED,
                )
                progress = True
                continue
            progress = self._attempt_queue_reply(record) or progress
        return progress

    def _process_queued_messages(self, *, limit: int = 20) -> bool:
        progress = False
        for record in self._message_pool_repo.list_ready_for_processing(
            ready_at=datetime.now(tz=timezone.utc),
            limit=limit,
        ):
            progress = True
            claimed = self._message_pool_repo.update(
                record.message_pool_id,
                processing_status=FeishuMessageProcessingStatus.CLAIMED,
                process_attempts=record.process_attempts + 1,
                last_claimed_at=datetime.now(tz=timezone.utc),
                last_error=None,
            )
            runtime_config = (
                self._runtime_config_lookup.get_runtime_config_by_trigger_id(
                    claimed.trigger_id
                )
            )
            if runtime_config is None:
                self._mark_retryable_failure(claimed, error="missing_runtime_config")
                continue
            try:
                session_id, run_id = self._inbound_runtime.start_run(
                    runtime_config=runtime_config,
                    message=_record_to_normalized_message(claimed),
                )
            except Exception as exc:
                self._mark_retryable_failure(claimed, error=str(exc))
                continue
            self._message_pool_repo.update(
                claimed.message_pool_id,
                session_id=session_id,
                run_id=run_id,
                processing_status=FeishuMessageProcessingStatus.WAITING_RESULT,
                next_attempt_at=datetime.now(tz=timezone.utc),
                last_error=None,
            )
        return progress

    def _finalize_waiting_results(self, *, limit: int = 20) -> bool:
        progress = False
        now = datetime.now(tz=timezone.utc)
        for record in self._message_pool_repo.list_waiting_for_result(limit=limit):
            run_id = str(record.run_id or "").strip()
            if not run_id:
                self._mark_retryable_failure(record, error="missing_run_id")
                progress = True
                continue
            runtime = self._run_runtime_repo.get(run_id)
            if runtime is None:
                if now - record.updated_at >= _WAITING_RUNTIME_TIMEOUT:
                    self._mark_retryable_failure(
                        record,
                        error="run_runtime_not_visible",
                    )
                    progress = True
                continue
            if runtime.status == RunRuntimeStatus.QUEUED:
                if now - runtime.updated_at >= _WAITING_QUEUED_TIMEOUT:
                    self._mark_retryable_failure(
                        record,
                        error="run_not_started_in_time",
                    )
                    progress = True
                continue
            if runtime.status in {
                RunRuntimeStatus.RUNNING,
                RunRuntimeStatus.STOPPING,
            }:
                continue
            if runtime.status == RunRuntimeStatus.PAUSED:
                if runtime.phase == RunRuntimePhase.AWAITING_RECOVERY:
                    progress = self._notify_recovery_pause(record, runtime) or progress
                continue
            if runtime.status not in {
                RunRuntimeStatus.COMPLETED,
                RunRuntimeStatus.FAILED,
                RunRuntimeStatus.STOPPED,
            }:
                continue
            progress = True
            self._finalize_terminal_record(record=record, runtime=runtime)
        return progress

    def _finalize_terminal_record(
        self,
        *,
        record: FeishuMessagePoolRecord,
        runtime: RunRuntimeRecord,
    ) -> None:
        self._pause_notice_keys = {
            key
            for key in self._pause_notice_keys
            if not key.startswith(f"{record.run_id}:")
        }
        reply_text = _build_terminal_reply(
            run_id=str(record.run_id or ""),
            runtime_status=runtime.status,
            fallback_error=runtime.last_error,
            event_log=self._event_log,
        )
        completed_at = datetime.now(tz=timezone.utc)
        if record.final_reply_status == FeishuMessageDeliveryStatus.SKIPPED:
            self._message_pool_repo.update(
                record.message_pool_id,
                final_reply_text=reply_text,
                processing_status=FeishuMessageProcessingStatus.COMPLETED,
                completed_at=completed_at,
                last_error=None,
            )
            return
        runtime_config = self._runtime_config_lookup.get_runtime_config_by_trigger_id(
            record.trigger_id
        )
        if runtime_config is None or self._feishu_client is None:
            self._message_pool_repo.update(
                record.message_pool_id,
                final_reply_status=FeishuMessageDeliveryStatus.FAILED,
                final_reply_text=reply_text,
                processing_status=FeishuMessageProcessingStatus.DEAD_LETTER,
                completed_at=completed_at,
                last_error="missing_runtime_config_for_reply",
            )
            return
        attempts = record.final_reply_attempts + 1
        try:
            self._send_terminal_reply(
                record=record,
                text=reply_text,
                environment=runtime_config.environment,
            )
        except RuntimeError as exc:
            status = (
                FeishuMessageDeliveryStatus.FAILED
                if attempts >= _FINAL_REPLY_MAX_ATTEMPTS
                else FeishuMessageDeliveryStatus.PENDING
            )
            processing_status = (
                FeishuMessageProcessingStatus.DEAD_LETTER
                if attempts >= _FINAL_REPLY_MAX_ATTEMPTS
                else FeishuMessageProcessingStatus.WAITING_RESULT
            )
            self._message_pool_repo.update(
                record.message_pool_id,
                final_reply_status=status,
                final_reply_text=reply_text,
                final_reply_attempts=attempts,
                processing_status=processing_status,
                next_attempt_at=completed_at + _backoff_for_attempt(attempts),
                last_error=str(exc),
                completed_at=completed_at
                if attempts >= _FINAL_REPLY_MAX_ATTEMPTS
                else None,
            )
            return
        self._message_pool_repo.update(
            record.message_pool_id,
            final_reply_status=FeishuMessageDeliveryStatus.SENT,
            final_reply_text=reply_text,
            final_reply_attempts=attempts,
            processing_status=FeishuMessageProcessingStatus.COMPLETED,
            completed_at=completed_at,
            last_error=None,
        )

    def _attempt_reaction(self, record: FeishuMessagePoolRecord) -> bool:
        if (
            self._feishu_client is None
            or record.reaction_status == FeishuMessageDeliveryStatus.SKIPPED
            or record.reaction_status == FeishuMessageDeliveryStatus.SENDING
        ):
            return False
        reaction_type = str(record.reaction_type or "").strip()
        message_id = str(record.message_id or "").strip()
        if not reaction_type or not message_id:
            return False
        runtime_config = self._runtime_config_lookup.get_runtime_config_by_trigger_id(
            record.trigger_id
        )
        if runtime_config is None:
            return False
        attempts = record.reaction_attempts + 1
        claimed = self._message_pool_repo.update(
            record.message_pool_id,
            reaction_status=FeishuMessageDeliveryStatus.SENDING,
            reaction_attempts=attempts,
            last_error=None,
        )
        try:
            self._feishu_client.create_message_reaction(
                message_id=message_id,
                reaction_type=reaction_type,
                environment=runtime_config.environment,
            )
        except RuntimeError as exc:
            status = (
                FeishuMessageDeliveryStatus.FAILED
                if attempts >= _REACTION_MAX_ATTEMPTS
                else FeishuMessageDeliveryStatus.PENDING
            )
            self._message_pool_repo.update(
                claimed.message_pool_id,
                reaction_status=status,
                last_error=str(exc),
            )
            return True
        self._message_pool_repo.update(
            claimed.message_pool_id,
            reaction_status=FeishuMessageDeliveryStatus.SENT,
            last_error=None,
        )
        return True

    def _attempt_queue_reply(self, record: FeishuMessagePoolRecord) -> bool:
        if (
            self._feishu_client is None
            or record.ack_status == FeishuMessageDeliveryStatus.SKIPPED
            or record.ack_status == FeishuMessageDeliveryStatus.SENDING
            or not str(record.ack_text or "").strip()
        ):
            return False
        runtime_config = self._runtime_config_lookup.get_runtime_config_by_trigger_id(
            record.trigger_id
        )
        if runtime_config is None:
            return False
        attempts = record.ack_attempts + 1
        claimed = self._message_pool_repo.update(
            record.message_pool_id,
            ack_status=FeishuMessageDeliveryStatus.SENDING,
            ack_attempts=attempts,
            last_error=None,
        )
        try:
            self._send_queue_reply(
                record=claimed,
                text=str(claimed.ack_text),
                environment=runtime_config.environment,
            )
        except RuntimeError as exc:
            status = (
                FeishuMessageDeliveryStatus.FAILED
                if attempts >= _ACK_MAX_ATTEMPTS
                else FeishuMessageDeliveryStatus.PENDING
            )
            self._message_pool_repo.update(
                claimed.message_pool_id,
                ack_status=status,
                last_error=str(exc),
            )
            return True
        self._message_pool_repo.update(
            claimed.message_pool_id,
            ack_status=FeishuMessageDeliveryStatus.SENT,
            last_error=None,
        )
        return True

    def _notify_recovery_pause(
        self,
        record: FeishuMessagePoolRecord,
        runtime: RunRuntimeRecord,
    ) -> bool:
        pause_event_id, error_message = _latest_pause_event(
            run_id=str(record.run_id or ""),
            event_log=self._event_log,
        )
        if pause_event_id is None:
            return False
        dedupe_key = f"{record.run_id}:{pause_event_id}"
        if dedupe_key in self._pause_notice_keys:
            return False
        runtime_config = self._runtime_config_lookup.get_runtime_config_by_trigger_id(
            record.trigger_id
        )
        if runtime_config is None or self._feishu_client is None:
            return False
        text = _build_pause_reply(
            run_id=str(record.run_id or ""),
            error_message=error_message or runtime.last_error,
        )
        try:
            self._send_terminal_reply(
                record=record,
                text=text,
                environment=runtime_config.environment,
            )
        except RuntimeError as exc:
            log_event(
                logger,
                logging.WARNING,
                event="feishu.message_pool.pause_notice_failed",
                message="Failed to send Feishu paused-run notice",
                payload={
                    "message_pool_id": record.message_pool_id,
                    "run_id": record.run_id,
                    "error": str(exc),
                },
            )
            return False
        self._pause_notice_keys.add(dedupe_key)
        return True

    def _send_queue_reply(
        self,
        *,
        record: FeishuMessagePoolRecord,
        text: str,
        environment: FeishuEnvironment,
    ) -> None:
        feishu_client = self._feishu_client
        if feishu_client is None:
            raise RuntimeError("Feishu client is not configured")
        if str(record.message_id or "").strip():
            feishu_client.reply_text_message(
                message_id=str(record.message_id),
                text=text,
                environment=environment,
            )
            return
        feishu_client.send_text_message(
            chat_id=record.chat_id,
            text=text,
            environment=environment,
        )

    def _send_terminal_reply(
        self,
        *,
        record: FeishuMessagePoolRecord,
        text: str,
        environment: FeishuEnvironment,
    ) -> None:
        feishu_client = self._feishu_client
        if feishu_client is None:
            raise RuntimeError("Feishu client is not configured")
        if str(record.message_id or "").strip():
            feishu_client.reply_text_message(
                message_id=str(record.message_id),
                text=text,
                environment=environment,
            )
            return
        feishu_client.send_text_message(
            chat_id=record.chat_id,
            text=text,
            environment=environment,
        )

    def _mark_retryable_failure(
        self,
        record: FeishuMessagePoolRecord,
        *,
        error: str,
    ) -> None:
        attempts = max(1, record.process_attempts)
        processing_status = (
            FeishuMessageProcessingStatus.DEAD_LETTER
            if attempts >= _FINAL_REPLY_MAX_ATTEMPTS
            else FeishuMessageProcessingStatus.RETRYABLE_FAILED
        )
        self._message_pool_repo.update(
            record.message_pool_id,
            processing_status=processing_status,
            next_attempt_at=datetime.now(tz=timezone.utc)
            + _backoff_for_attempt(attempts),
            last_error=error,
            completed_at=datetime.now(tz=timezone.utc)
            if processing_status == FeishuMessageProcessingStatus.DEAD_LETTER
            else None,
        )

    def _enrich_sender_name(
        self,
        *,
        normalized: FeishuNormalizedMessage,
        runtime_config: FeishuTriggerRuntimeConfig,
    ) -> FeishuNormalizedMessage:
        if (
            self._feishu_client is None
            or normalized.chat_type.strip().lower() != "group"
            or str(normalized.sender_name or "").strip()
            or not str(normalized.sender_open_id or "").strip()
        ):
            return normalized
        try:
            sender_name = self._feishu_client.resolve_user_name(
                open_id=str(normalized.sender_open_id),
                chat_id=normalized.chat_id,
                environment=runtime_config.environment,
            )
        except RuntimeError as exc:
            log_event(
                logger,
                logging.WARNING,
                event="feishu.message_pool.sender_lookup_failed",
                message="Failed to resolve Feishu sender name for group message",
                payload={
                    "trigger_id": runtime_config.trigger_id,
                    "chat_id": normalized.chat_id,
                    "sender_open_id": normalized.sender_open_id,
                    "error": str(exc),
                },
            )
            return normalized
        normalized_sender_name = str(sender_name or "").strip()
        if not normalized_sender_name:
            return normalized
        next_metadata = dict(normalized.metadata)
        next_metadata["sender_name"] = normalized_sender_name
        return normalized.model_copy(
            update={
                "sender_name": normalized_sender_name,
                "metadata": next_metadata,
            }
        )

    @staticmethod
    def _should_send_reaction_acknowledgement(
        message: FeishuNormalizedMessage,
    ) -> bool:
        return message.chat_type.strip().lower() in {"group", "p2p"} and bool(
            str(message.message_id).strip()
        )

    def _build_queue_preview(
        self,
        record: FeishuMessagePoolRecord,
    ) -> FeishuChatQueueItemPreview:
        runtime = (
            self._run_runtime_repo.get(str(record.run_id))
            if str(record.run_id or "").strip()
            else None
        )
        return FeishuChatQueueItemPreview(
            message_pool_id=record.message_pool_id,
            processing_status=record.processing_status,
            intent_preview=_build_intent_preview(record.intent_text),
            run_id=record.run_id,
            run_status=runtime.status.value if runtime is not None else None,
            run_phase=runtime.phase.value if runtime is not None else None,
            blocking_reason=_run_blocking_reason(runtime),
            last_error=record.last_error,
        )


def _message_key(message: FeishuNormalizedMessage) -> str:
    normalized_message_id = str(message.message_id).strip()
    if normalized_message_id:
        return normalized_message_id
    return message.event_id


def _build_ack_text(queue_depth: int) -> str:
    if queue_depth <= 0:
        return "收到，正在处理。"
    return f"收到，已进入排队。当前聊天前面还有 {queue_depth} 条消息。"


def _build_queue_reply_text(queue_depth: int) -> str | None:
    if queue_depth <= 0:
        return None
    return f"已进入队列，前面还有 {queue_depth} 条消息。"


def _record_to_normalized_message(
    record: FeishuMessagePoolRecord,
) -> FeishuNormalizedMessage:
    raw_text = str(record.payload.get("raw_text", ""))
    return FeishuNormalizedMessage(
        event_id=record.event_id,
        tenant_key=record.tenant_key,
        chat_id=record.chat_id,
        chat_type=record.chat_type,
        message_id=record.message_id or record.message_key,
        message_type="text",
        sender_name=record.sender_name,
        raw_text=raw_text,
        trigger_text=record.intent_text,
        payload=record.payload,
        metadata=record.metadata,
    )


def _backoff_for_attempt(attempt: int) -> timedelta:
    if attempt <= 1:
        return timedelta(seconds=1)
    if attempt == 2:
        return timedelta(seconds=5)
    if attempt == 3:
        return timedelta(seconds=30)
    if attempt == 4:
        return timedelta(minutes=2)
    return timedelta(minutes=10)


def _build_terminal_reply(
    *,
    run_id: str,
    runtime_status: RunRuntimeStatus,
    fallback_error: str | None,
    event_log: EventLog,
) -> str:
    if runtime_status == RunRuntimeStatus.STOPPED:
        return "处理已中断，请重试。"
    output = ""
    terminal_error = ""
    try:
        for event in reversed(event_log.list_by_trace_with_ids(run_id)):
            event_type = str(event.get("event_type") or "")
            if event_type not in {
                RunEventType.RUN_COMPLETED.value,
                RunEventType.RUN_FAILED.value,
            }:
                continue
            payload_json = str(event.get("payload_json") or "{}")
            payload = json.loads(payload_json)
            if isinstance(payload, dict):
                output = _extract_terminal_output(payload)
                terminal_error = _extract_terminal_error(payload)
            break
    except Exception:
        output = ""
        terminal_error = ""
    if runtime_status == RunRuntimeStatus.COMPLETED:
        return output or f"Run {run_id} completed successfully."
    if output:
        return f"Run {run_id} failed: {output}"
    if terminal_error:
        return f"Run {run_id} failed: {terminal_error}"
    if str(fallback_error or "").strip():
        return f"Run {run_id} failed: {fallback_error}"
    return f"Run {run_id} failed."


def _build_pause_reply(
    *,
    run_id: str,
    error_message: str | None,
) -> str:
    reason = str(error_message or "").strip()
    if reason:
        return f"运行已暂停：{reason}\n发送 resume 继续。"
    return f"运行 {run_id} 已暂停。\n发送 resume 继续。"


def _extract_terminal_output(payload: dict[str, object]) -> str:
    return extract_terminal_output(payload)


def _extract_terminal_error(payload: dict[str, object]) -> str:
    return extract_terminal_error(payload)


def _build_intent_preview(intent_text: str, max_length: int = 48) -> str:
    text = " ".join(str(intent_text).split())
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


def _run_blocking_reason(runtime: RunRuntimeRecord | None) -> str | None:
    if runtime is None:
        return None
    if runtime.phase == RunRuntimePhase.AWAITING_TOOL_APPROVAL:
        return "awaiting_tool_approval"
    if runtime.phase == RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP:
        return "awaiting_subagent_followup"
    if runtime.phase == RunRuntimePhase.AWAITING_RECOVERY:
        return "awaiting_recovery"
    if runtime.status == RunRuntimeStatus.STOPPING:
        return "stopping"
    return None


def _latest_pause_event(
    *,
    run_id: str,
    event_log: EventLog,
) -> tuple[int | None, str | None]:
    try:
        for event in reversed(event_log.list_by_trace_with_ids(run_id)):
            if str(event.get("event_type") or "") != RunEventType.RUN_PAUSED.value:
                continue
            event_id = event.get("id")
            if not isinstance(event_id, int):
                continue
            payload_json = str(event.get("payload_json") or "{}")
            try:
                payload = json.loads(payload_json)
            except json.JSONDecodeError:
                payload = {}
            error_message = None
            if isinstance(payload, dict):
                raw_message = payload.get("error_message")
                if isinstance(raw_message, str) and raw_message.strip():
                    error_message = raw_message.strip()
            return event_id, error_message
    except Exception:
        return None, None
    return None, None
