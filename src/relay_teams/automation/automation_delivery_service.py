# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Optional, Protocol
from uuid import uuid4

from relay_teams.automation.automation_delivery_repository import (
    AutomationDeliveryRepository,
)
from relay_teams.automation.automation_models import (
    AutomationCleanupStatus,
    AutomationDeliveryEvent,
    AutomationDeliveryStatus,
    AutomationFeishuBinding,
    AutomationProjectRecord,
    AutomationRunDeliveryRecord,
)
from relay_teams.gateway.feishu.models import FeishuEnvironment
from relay_teams.logger import get_logger, log_event
from relay_teams.notifications import NotificationContext, NotificationType
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.sessions.runs.terminal_payload import (
    extract_terminal_error,
    extract_terminal_output,
    parse_terminal_payload_json,
)

logger = get_logger(__name__)

_STARTED_MAX_ATTEMPTS = 3
_TERMINAL_MAX_ATTEMPTS = 5
_CLAIM_STALE_AFTER_SECONDS = 60


class FeishuRuntimeConfigLookup(Protocol):
    def get_runtime_config_by_trigger_id(
        self, trigger_id: str
    ) -> FeishuRuntimeConfigLike | None: ...


class FeishuRuntimeConfigLike(Protocol):
    @property
    def environment(self) -> FeishuEnvironment:
        raise NotImplementedError


class FeishuClientLike(Protocol):
    def send_text_message(
        self,
        *,
        chat_id: str,
        text: str,
        environment: FeishuEnvironment | None = None,
    ) -> str: ...

    def reply_text_message(
        self,
        *,
        message_id: str,
        text: str,
        environment: FeishuEnvironment | None = None,
    ) -> str: ...


class NotificationServiceLike(Protocol):
    def emit(
        self,
        *,
        notification_type: NotificationType,
        title: str,
        body: str,
        context: NotificationContext,
        dedupe_key: str | None = None,
    ) -> bool: ...


class AutomationDeliveryService:
    def __init__(
        self,
        *,
        repository: AutomationDeliveryRepository,
        runtime_config_lookup: FeishuRuntimeConfigLookup,
        feishu_client: FeishuClientLike,
        run_runtime_repo: RunRuntimeRepository,
        event_log: EventLog,
        notification_service: NotificationServiceLike | None = None,
    ) -> None:
        self._repository = repository
        self._runtime_config_lookup = runtime_config_lookup
        self._feishu_client = feishu_client
        self._run_runtime_repo = run_runtime_repo
        self._event_log = event_log
        self._notification_service = notification_service

    def register_run(
        self,
        *,
        project: AutomationProjectRecord | None,
        session_id: str,
        run_id: str,
        reason: str,
        project_id: str | None = None,
        project_name: str | None = None,
        binding: AutomationFeishuBinding | None = None,
        delivery_events: tuple[AutomationDeliveryEvent, ...] | None = None,
        send_started: bool = True,
        reply_to_message_id: str | None = None,
    ) -> AutomationRunDeliveryRecord | None:
        resolved_binding = (
            binding
            if binding is not None
            else (project.delivery_binding if project is not None else None)
        )
        if resolved_binding is None:
            return None
        resolved_project_id = str(project_id or "").strip() or (
            project.automation_project_id if project is not None else ""
        )
        resolved_project_name = str(project_name or "").strip() or (
            project.display_name if project is not None else ""
        )
        resolved_events = (
            delivery_events
            if delivery_events is not None
            else (project.delivery_events if project is not None else ())
        )
        record = self._repository.create(
            AutomationRunDeliveryRecord(
                automation_delivery_id=f"autd_{uuid4().hex[:12]}",
                automation_project_id=resolved_project_id,
                automation_project_name=resolved_project_name,
                run_id=run_id,
                session_id=session_id,
                reason=reason,
                binding=resolved_binding,
                delivery_events=resolved_events,
                reply_to_message_id=reply_to_message_id,
                started_status=(
                    AutomationDeliveryStatus.PENDING
                    if send_started
                    and AutomationDeliveryEvent.STARTED in resolved_events
                    else AutomationDeliveryStatus.SKIPPED
                ),
                terminal_status=(
                    AutomationDeliveryStatus.PENDING
                    if any(
                        event in resolved_events
                        for event in (
                            AutomationDeliveryEvent.COMPLETED,
                            AutomationDeliveryEvent.FAILED,
                        )
                    )
                    else AutomationDeliveryStatus.SKIPPED
                ),
                started_message=_build_started_message(
                    project_name=resolved_project_name
                ),
            )
        )
        if record.started_status == AutomationDeliveryStatus.PENDING:
            _ = self._attempt_started_delivery(record)
            return self._repository.get_by_run_id(run_id)
        return record

    def process_pending(self, *, limit: int = 20) -> bool:
        progress = False
        stale_before = _utc_now() - timedelta(seconds=_CLAIM_STALE_AFTER_SECONDS)
        for record in self._repository.list_pending_started(
            limit=limit,
            stale_before=stale_before,
        ):
            progress = self._attempt_started_delivery(record) or progress
        for record in self._repository.list_pending_terminal(
            limit=limit,
            stale_before=stale_before,
        ):
            progress = self._attempt_terminal_delivery(record) or progress
        for record in self._repository.list_pending_started_cleanup(
            limit=limit,
            stale_before=stale_before,
        ):
            progress = self._attempt_started_cleanup(record) or progress
        return progress

    def has_project_deliveries(self, automation_project_id: str) -> bool:
        return self._repository.has_project_records(automation_project_id)

    def delete_project_deliveries(self, automation_project_id: str) -> None:
        self._repository.delete_by_project(automation_project_id)

    def should_suppress_terminal_notification(self, run_id: str | None) -> bool:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return False
        try:
            record = self._repository.get_by_run_id(normalized_run_id)
        except KeyError:
            return False
        if record.terminal_status in {
            AutomationDeliveryStatus.PENDING,
            AutomationDeliveryStatus.SENDING,
            AutomationDeliveryStatus.SENT,
        }:
            return True
        if record.terminal_status == AutomationDeliveryStatus.FAILED:
            return False
        return bool(str(record.terminal_message or "").strip())

    def mark_terminal_delivery_skipped(
        self,
        *,
        run_id: str,
        terminal_message: str | None = None,
    ) -> None:
        try:
            record = self._repository.get_by_run_id(run_id)
        except KeyError:
            return
        if record.terminal_status == AutomationDeliveryStatus.SENT:
            return
        now = _utc_now()
        _ = self._repository.update(
            record.model_copy(
                update={
                    "terminal_event": AutomationDeliveryEvent.FAILED,
                    "terminal_status": AutomationDeliveryStatus.SKIPPED,
                    "terminal_message": (
                        terminal_message
                        if terminal_message is not None
                        else record.terminal_message
                    ),
                    "updated_at": now,
                }
            )
        )

    def _attempt_started_delivery(self, record: AutomationRunDeliveryRecord) -> bool:
        claim_cutoff = _utc_now() - timedelta(seconds=_CLAIM_STALE_AFTER_SECONDS)
        if record.started_status not in {
            AutomationDeliveryStatus.PENDING,
            AutomationDeliveryStatus.SENDING,
        }:
            return False
        claimed = self._repository.claim_started(
            automation_delivery_id=record.automation_delivery_id,
            stale_before=claim_cutoff,
        )
        if claimed is None:
            return False
        attempts = claimed.started_attempts + 1
        try:
            message_id = self._send_text(
                trigger_id=claimed.binding.trigger_id,
                chat_id=claimed.binding.chat_id,
                text=str(claimed.started_message or "").strip(),
            )
        except RuntimeError as exc:
            now = _utc_now()
            next_status = (
                AutomationDeliveryStatus.FAILED
                if attempts >= _STARTED_MAX_ATTEMPTS
                else AutomationDeliveryStatus.PENDING
            )
            _ = self._repository.update(
                claimed.model_copy(
                    update={
                        "started_attempts": attempts,
                        "started_status": next_status,
                        "last_error": str(exc),
                        "updated_at": now,
                    }
                )
            )
            return True
        now = _utc_now()
        _ = self._repository.update(
            claimed.model_copy(
                update={
                    "started_attempts": attempts,
                    "started_status": AutomationDeliveryStatus.SENT,
                    "started_message_id": message_id,
                    "started_sent_at": now,
                    "last_error": None,
                    "updated_at": now,
                }
            )
        )
        return True

    def _attempt_terminal_delivery(self, record: AutomationRunDeliveryRecord) -> bool:
        if record.terminal_status not in {
            AutomationDeliveryStatus.PENDING,
            AutomationDeliveryStatus.SENDING,
        }:
            return False
        runtime = self._run_runtime_repo.get(record.run_id)
        if runtime is None or runtime.status not in {
            RunRuntimeStatus.COMPLETED,
            RunRuntimeStatus.FAILED,
        }:
            return False
        if runtime.phase == RunRuntimePhase.AWAITING_RECOVERY:
            return False
        claim_cutoff = _utc_now() - timedelta(seconds=_CLAIM_STALE_AFTER_SECONDS)
        claimed = self._repository.claim_terminal(
            automation_delivery_id=record.automation_delivery_id,
            stale_before=claim_cutoff,
        )
        if claimed is None:
            return False
        terminal_event = (
            AutomationDeliveryEvent.COMPLETED
            if runtime.status == RunRuntimeStatus.COMPLETED
            else AutomationDeliveryEvent.FAILED
        )
        terminal_message = _build_terminal_message(
            project_name=claimed.automation_project_name,
            run_id=claimed.run_id,
            runtime_status=runtime.status,
            event_log=self._event_log,
            fallback_error=runtime.last_error,
        )
        if (
            terminal_event not in claimed.delivery_events
            or terminal_message.strip() == ""
        ):
            now = _utc_now()
            _ = self._repository.update(
                claimed.model_copy(
                    update={
                        "terminal_event": terminal_event,
                        "terminal_status": AutomationDeliveryStatus.SKIPPED,
                        "terminal_message": terminal_message,
                        "updated_at": now,
                    }
                )
            )
            return True
        attempts = claimed.terminal_attempts + 1
        reply_to_message_id = (
            str(claimed.started_message_id or "").strip()
            or str(claimed.reply_to_message_id or "").strip()
        )
        try:
            message_id = self._send_text(
                trigger_id=claimed.binding.trigger_id,
                chat_id=claimed.binding.chat_id,
                text=terminal_message,
                reply_to_message_id=reply_to_message_id or None,
            )
        except RuntimeError as exc:
            now = _utc_now()
            next_status = (
                AutomationDeliveryStatus.FAILED
                if attempts >= _TERMINAL_MAX_ATTEMPTS
                else AutomationDeliveryStatus.PENDING
            )
            persisted = self._repository.update(
                claimed.model_copy(
                    update={
                        "terminal_event": terminal_event,
                        "terminal_message": terminal_message,
                        "terminal_attempts": attempts,
                        "terminal_status": next_status,
                        "last_error": str(exc),
                        "updated_at": now,
                    }
                )
            )
            if next_status == AutomationDeliveryStatus.FAILED:
                self._emit_fallback_terminal_notification(
                    record=persisted,
                    runtime_status=runtime.status,
                )
            return True
        now = _utc_now()
        _ = self._repository.update(
            claimed.model_copy(
                update={
                    "terminal_event": terminal_event,
                    "terminal_message": terminal_message,
                    "terminal_message_id": message_id,
                    "terminal_attempts": attempts,
                    "terminal_status": AutomationDeliveryStatus.SENT,
                    "terminal_sent_at": now,
                    "started_cleanup_status": AutomationCleanupStatus.SKIPPED,
                    "last_error": None,
                    "updated_at": now,
                }
            )
        )
        return True

    def _attempt_started_cleanup(self, record: AutomationRunDeliveryRecord) -> bool:
        if record.started_cleanup_status not in {
            AutomationCleanupStatus.PENDING,
            AutomationCleanupStatus.CLEANING,
        }:
            return False
        now = _utc_now()
        _ = self._repository.update(
            record.model_copy(
                update={
                    "started_cleanup_status": AutomationCleanupStatus.SKIPPED,
                    "updated_at": now,
                }
            )
        )
        return True

    def _send_text(
        self,
        *,
        trigger_id: str,
        chat_id: str,
        text: str,
        reply_to_message_id: str | None = None,
    ) -> str:
        runtime_config = self._runtime_config_lookup.get_runtime_config_by_trigger_id(
            trigger_id
        )
        if runtime_config is None:
            raise RuntimeError("missing_runtime_config")
        normalized_reply_to_message_id = str(reply_to_message_id or "").strip()
        if normalized_reply_to_message_id:
            return self._feishu_client.reply_text_message(
                message_id=normalized_reply_to_message_id,
                text=text,
                environment=runtime_config.environment,
            )
        return self._feishu_client.send_text_message(
            chat_id=chat_id,
            text=text,
            environment=runtime_config.environment,
        )

    def _emit_fallback_terminal_notification(
        self,
        *,
        record: AutomationRunDeliveryRecord,
        runtime_status: RunRuntimeStatus,
    ) -> None:
        if self._notification_service is None:
            return
        notification_type = (
            NotificationType.RUN_COMPLETED
            if runtime_status == RunRuntimeStatus.COMPLETED
            else NotificationType.RUN_FAILED
        )
        title = (
            "Run Completed"
            if notification_type == NotificationType.RUN_COMPLETED
            else "Run Failed"
        )
        body = str(record.terminal_message or "").strip()
        if not body:
            return
        _ = self._notification_service.emit(
            notification_type=notification_type,
            title=title,
            body=body,
            context=NotificationContext(
                session_id=record.session_id,
                run_id=record.run_id,
                trace_id=record.run_id,
            ),
            dedupe_key=f"automation-terminal-fallback:{record.run_id}",
        )


class AutomationDeliveryWorker:
    def __init__(
        self,
        *,
        delivery_service: AutomationDeliveryService,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self._delivery_service = delivery_service
        self._poll_interval_seconds = poll_interval_seconds
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._wake_event.clear()
        self._task = asyncio.create_task(
            self._run_loop(),
            name="automation-delivery-worker",
        )

    async def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        task = self._task
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._task = None

    def wake(self) -> None:
        self._wake_event.set()

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                progress = await asyncio.to_thread(
                    self._delivery_service.process_pending
                )
                if progress:
                    continue
            except Exception as exc:
                log_event(
                    logger,
                    logging.ERROR,
                    event="automation.delivery.loop_failed",
                    message="Automation delivery loop failed",
                    payload={"error": str(exc)},
                    exc_info=exc,
                )
            try:
                await asyncio.wait_for(
                    self._wake_event.wait(),
                    timeout=self._poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass
            self._wake_event.clear()


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _build_started_message(*, project_name: str) -> str:
    return f"定时任务 {project_name} 开始执行"


def _build_terminal_message(
    *,
    project_name: str,
    run_id: str,
    runtime_status: RunRuntimeStatus,
    event_log: EventLog,
    fallback_error: str | None,
) -> str:
    output = ""
    terminal_error = ""
    for event in reversed(event_log.list_by_trace_with_ids(run_id)):
        event_type = str(event.get("event_type") or "")
        if event_type not in {"run_completed", "run_failed"}:
            continue
        payload = parse_terminal_payload_json(event.get("payload_json"))
        output = extract_terminal_output(payload)
        terminal_error = extract_terminal_error(payload)
        break
    if runtime_status == RunRuntimeStatus.COMPLETED:
        if output:
            return output
        return ""
    failure_detail = (
        output or terminal_error or str(fallback_error or "").strip() or "未知错误。"
    )
    return f"定时任务 {project_name} 执行失败。\n\n{failure_detail}"


__all__ = ["AutomationDeliveryService", "AutomationDeliveryWorker"]
