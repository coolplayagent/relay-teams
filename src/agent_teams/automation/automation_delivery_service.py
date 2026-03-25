# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
from datetime import timedelta
from threading import Event, Thread
from typing import Protocol
from uuid import uuid4

from agent_teams.automation.automation_delivery_repository import (
    AutomationDeliveryRepository,
)
from agent_teams.automation.automation_models import (
    AutomationDeliveryEvent,
    AutomationDeliveryStatus,
    AutomationProjectRecord,
    AutomationRunDeliveryRecord,
)
from agent_teams.feishu.models import FeishuEnvironment
from agent_teams.logger import get_logger, log_event
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.sessions.runs.run_runtime_repo import (
    RunRuntimeRepository,
    RunRuntimeStatus,
)

logger = get_logger(__name__)

_STARTED_MAX_ATTEMPTS = 3
_TERMINAL_MAX_ATTEMPTS = 5
_CLAIM_STALE_AFTER_SECONDS = 60


class FeishuRuntimeConfigLookup(Protocol):
    def get_runtime_config_by_trigger_id(
        self, trigger_id: str
    ) -> "FeishuRuntimeConfigLike | None": ...


class FeishuRuntimeConfigLike(Protocol):
    @property
    def environment(self) -> FeishuEnvironment: ...


class FeishuClientLike(Protocol):
    def send_text_message(
        self,
        *,
        chat_id: str,
        text: str,
        environment: FeishuEnvironment | None = None,
    ) -> None: ...


class AutomationDeliveryService:
    def __init__(
        self,
        *,
        repository: AutomationDeliveryRepository,
        runtime_config_lookup: FeishuRuntimeConfigLookup,
        feishu_client: FeishuClientLike,
        run_runtime_repo: RunRuntimeRepository,
        event_log: EventLog,
    ) -> None:
        self._repository = repository
        self._runtime_config_lookup = runtime_config_lookup
        self._feishu_client = feishu_client
        self._run_runtime_repo = run_runtime_repo
        self._event_log = event_log

    def register_run(
        self,
        *,
        project: AutomationProjectRecord,
        session_id: str,
        run_id: str,
        reason: str,
    ) -> AutomationRunDeliveryRecord | None:
        binding = project.delivery_binding
        if binding is None:
            return None
        delivery_events = project.delivery_events
        record = self._repository.create(
            AutomationRunDeliveryRecord(
                automation_delivery_id=f"autd_{uuid4().hex[:12]}",
                automation_project_id=project.automation_project_id,
                automation_project_name=project.display_name,
                run_id=run_id,
                session_id=session_id,
                reason=reason,
                binding=binding,
                delivery_events=delivery_events,
                started_status=(
                    AutomationDeliveryStatus.PENDING
                    if AutomationDeliveryEvent.STARTED in delivery_events
                    else AutomationDeliveryStatus.SKIPPED
                ),
                terminal_status=(
                    AutomationDeliveryStatus.PENDING
                    if any(
                        event in delivery_events
                        for event in (
                            AutomationDeliveryEvent.COMPLETED,
                            AutomationDeliveryEvent.FAILED,
                        )
                    )
                    else AutomationDeliveryStatus.SKIPPED
                ),
                started_message=_build_started_message(
                    project_name=project.display_name,
                    reason=reason,
                    run_id=run_id,
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
        return progress

    def delete_project_deliveries(self, automation_project_id: str) -> None:
        self._repository.delete_by_project(automation_project_id)

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
        record = claimed
        attempts = record.started_attempts + 1
        try:
            self._send_text(
                trigger_id=record.binding.trigger_id,
                chat_id=record.binding.chat_id,
                text=str(record.started_message or "").strip(),
            )
        except RuntimeError as exc:
            now = _utc_now()
            next_status = (
                AutomationDeliveryStatus.FAILED
                if attempts >= _STARTED_MAX_ATTEMPTS
                else AutomationDeliveryStatus.PENDING
            )
            self._repository.update(
                record.model_copy(
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
        self._repository.update(
            record.model_copy(
                update={
                    "started_attempts": attempts,
                    "started_status": AutomationDeliveryStatus.SENT,
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
        claim_cutoff = _utc_now() - timedelta(seconds=_CLAIM_STALE_AFTER_SECONDS)
        claimed = self._repository.claim_terminal(
            automation_delivery_id=record.automation_delivery_id,
            stale_before=claim_cutoff,
        )
        if claimed is None:
            return False
        record = claimed
        terminal_event = (
            AutomationDeliveryEvent.COMPLETED
            if runtime.status == RunRuntimeStatus.COMPLETED
            else AutomationDeliveryEvent.FAILED
        )
        if terminal_event not in record.delivery_events:
            now = _utc_now()
            self._repository.update(
                record.model_copy(
                    update={
                        "terminal_event": terminal_event,
                        "terminal_status": AutomationDeliveryStatus.SKIPPED,
                        "terminal_message": _build_terminal_message(
                            project_name=record.automation_project_name,
                            run_id=record.run_id,
                            runtime_status=runtime.status,
                            event_log=self._event_log,
                            fallback_error=runtime.last_error,
                        ),
                        "updated_at": now,
                    }
                )
            )
            return True
        terminal_message = _build_terminal_message(
            project_name=record.automation_project_name,
            run_id=record.run_id,
            runtime_status=runtime.status,
            event_log=self._event_log,
            fallback_error=runtime.last_error,
        )
        attempts = record.terminal_attempts + 1
        try:
            self._send_text(
                trigger_id=record.binding.trigger_id,
                chat_id=record.binding.chat_id,
                text=terminal_message,
            )
        except RuntimeError as exc:
            now = _utc_now()
            next_status = (
                AutomationDeliveryStatus.FAILED
                if attempts >= _TERMINAL_MAX_ATTEMPTS
                else AutomationDeliveryStatus.PENDING
            )
            self._repository.update(
                record.model_copy(
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
            return True
        now = _utc_now()
        self._repository.update(
            record.model_copy(
                update={
                    "terminal_event": terminal_event,
                    "terminal_message": terminal_message,
                    "terminal_attempts": attempts,
                    "terminal_status": AutomationDeliveryStatus.SENT,
                    "terminal_sent_at": now,
                    "last_error": None,
                    "updated_at": now,
                }
            )
        )
        return True

    def _send_text(self, *, trigger_id: str, chat_id: str, text: str) -> None:
        runtime_config = self._runtime_config_lookup.get_runtime_config_by_trigger_id(
            trigger_id
        )
        if runtime_config is None:
            raise RuntimeError("missing_runtime_config")
        self._feishu_client.send_text_message(
            chat_id=chat_id,
            text=text,
            environment=runtime_config.environment,
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
        self._stop_event = Event()
        self._wake_event = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._wake_event.clear()
        self._thread = Thread(
            target=self._run_loop,
            name="automation-delivery-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread is None:
            return
        thread.join(timeout=10.0)
        self._thread = None

    def wake(self) -> None:
        self._wake_event.set()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                progress = self._delivery_service.process_pending()
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
            self._wake_event.wait(timeout=self._poll_interval_seconds)
            self._wake_event.clear()


def _utc_now():
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc)


def _build_started_message(
    *,
    project_name: str,
    reason: str,
    run_id: str,
) -> str:
    _ = run_id
    reason_label = _describe_reason(reason)
    if reason_label is None:
        return f"{project_name} 定时任务开始执行。"
    return f"{project_name} 定时任务开始执行（{reason_label}）。"


def _build_terminal_message(
    *,
    project_name: str,
    run_id: str,
    runtime_status: RunRuntimeStatus,
    event_log: EventLog,
    fallback_error: str | None,
) -> str:
    output = ""
    for event in reversed(event_log.list_by_trace_with_ids(run_id)):
        event_type = str(event.get("event_type") or "")
        if event_type not in {"run_completed", "run_failed"}:
            continue
        payload_json = str(event.get("payload_json") or "{}")
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            output_value = payload.get("output")
            if isinstance(output_value, str):
                output = output_value.strip()
        break
    if runtime_status == RunRuntimeStatus.COMPLETED:
        if output:
            return f"{project_name} 定时任务执行完成。\n\n{output}"
        return f"{project_name} 定时任务执行完成。"
    _ = run_id
    failure_detail = output or str(fallback_error or "").strip() or "未知错误。"
    return f"{project_name} 定时任务执行失败。\n\n{failure_detail}"


def _describe_reason(reason: str) -> str | None:
    normalized_reason = str(reason).strip().lower()
    if normalized_reason == "manual":
        return "手动触发"
    if normalized_reason == "schedule":
        return "定时触发"
    return None


__all__ = ["AutomationDeliveryService", "AutomationDeliveryWorker"]
