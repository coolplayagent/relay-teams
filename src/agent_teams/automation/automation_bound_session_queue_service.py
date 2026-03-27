# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from typing import Protocol
from uuid import uuid4

from agent_teams.automation.automation_bound_session_queue_repository import (
    AutomationBoundSessionQueueRepository,
)
from agent_teams.automation.automation_delivery_service import AutomationDeliveryService
from agent_teams.automation.automation_models import (
    AutomationBoundSessionQueueRecord,
    AutomationBoundSessionQueueStatus,
    AutomationDeliveryEvent,
    AutomationExecutionHandle,
    AutomationFeishuBinding,
    AutomationProjectRecord,
    AutomationRunConfig,
)
from agent_teams.gateway.feishu.models import FEISHU_PLATFORM, FeishuEnvironment
from agent_teams.logger import get_logger, log_event
from agent_teams.media import content_parts_from_text
from agent_teams.sessions import ExternalSessionBindingRepository
from agent_teams.sessions.runs.run_models import (
    IntentInput,
    RuntimePromptConversationContext,
)
from agent_teams.sessions.runs.run_runtime_repo import (
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from agent_teams.sessions.session_models import SessionRecord

logger = get_logger(__name__)

_START_MAX_ATTEMPTS = 5
_CLAIM_STALE_AFTER_SECONDS = 60


class SessionLookup(Protocol):
    def get_session(self, session_id: str) -> SessionRecord: ...


class RunServiceLike(Protocol):
    def create_detached_run(self, intent: IntentInput) -> tuple[str, str]: ...

    def ensure_run_started(self, run_id: str) -> None: ...


class FeishuRuntimeConfigLookup(Protocol):
    def get_runtime_config_by_trigger_id(
        self, trigger_id: str
    ) -> FeishuRuntimeConfigLike | None: ...


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


class AutomationProjectLookup(Protocol):
    def get(self, automation_project_id: str) -> AutomationProjectRecord: ...

    def update(self, record: AutomationProjectRecord) -> AutomationProjectRecord: ...


class AutomationBoundSessionQueueService:
    def __init__(
        self,
        *,
        repository: AutomationBoundSessionQueueRepository,
        external_session_binding_repo: ExternalSessionBindingRepository,
        session_lookup: SessionLookup,
        run_service: RunServiceLike,
        run_runtime_repo: RunRuntimeRepository,
        delivery_service: AutomationDeliveryService,
        runtime_config_lookup: FeishuRuntimeConfigLookup,
        feishu_client: FeishuClientLike,
        project_repository: AutomationProjectLookup,
    ) -> None:
        self._repository = repository
        self._external_session_binding_repo = external_session_binding_repo
        self._session_lookup = session_lookup
        self._run_service = run_service
        self._run_runtime_repo = run_runtime_repo
        self._delivery_service = delivery_service
        self._runtime_config_lookup = runtime_config_lookup
        self._feishu_client = feishu_client
        self._project_repository = project_repository

    def materialize_execution(
        self,
        *,
        project: AutomationProjectRecord,
        reason: str,
    ) -> AutomationExecutionHandle | None:
        binding = project.delivery_binding
        if binding is None:
            return None
        binding_record = self._external_session_binding_repo.get_binding(
            platform=FEISHU_PLATFORM,
            trigger_id=binding.trigger_id,
            tenant_key=binding.tenant_key,
            external_chat_id=binding.chat_id,
        )
        if binding_record is None:
            return None
        try:
            _ = self._session_lookup.get_session(binding_record.session_id)
        except KeyError:
            return None

        session_id = binding_record.session_id
        active_run_id = self._active_recoverable_run_id(session_id)
        queued_count = self._repository.count_non_terminal_by_session(session_id)
        queued_represents_active_run = (
            active_run_id is not None
            and self._repository.has_non_terminal_item_for_run(active_run_id)
        )
        ahead_count = queued_count + (
            1 if active_run_id is not None and not queued_represents_active_run else 0
        )
        if ahead_count > 0:
            record = self._repository.create(
                AutomationBoundSessionQueueRecord(
                    automation_queue_id=f"autq_{uuid4().hex[:12]}",
                    automation_project_id=project.automation_project_id,
                    automation_project_name=project.display_name,
                    session_id=session_id,
                    reason=reason,
                    binding=binding,
                    delivery_events=project.delivery_events,
                    run_config=project.run_config,
                    prompt=_build_queued_prompt(
                        project_name=project.display_name,
                        prompt=project.prompt,
                    ),
                    queue_message=_build_queue_message(
                        project_name=project.display_name,
                        ahead_count=ahead_count,
                    ),
                )
            )
            self._send_direct_text(
                record.binding.trigger_id,
                record.binding.chat_id,
                record.queue_message,
            )
            return AutomationExecutionHandle(session_id=session_id, queued=True)

        run_id = self._start_bound_run(
            project_name=project.display_name,
            project_id=project.automation_project_id,
            session_id=session_id,
            reason=reason,
            prompt=project.prompt,
            run_config=project.run_config,
            binding=binding,
            delivery_events=project.delivery_events,
            send_started=True,
        )
        return AutomationExecutionHandle(
            session_id=session_id,
            run_id=run_id,
            queued=False,
        )

    def process_pending(self, *, limit: int = 20) -> bool:
        progress = False
        progress = self._finalize_waiting_results(limit=limit) or progress
        progress = self._start_queued_runs(limit=limit) or progress
        return progress

    def delete_project_queue(self, automation_project_id: str) -> None:
        self._repository.delete_by_project(automation_project_id)

    def _finalize_waiting_results(self, *, limit: int) -> bool:
        progress = False
        now = _utc_now()
        for record in self._repository.list_waiting_for_result(limit=limit):
            run_id = str(record.run_id or "").strip()
            if not run_id:
                _ = self._repository.update(
                    record.model_copy(
                        update={
                            "status": AutomationBoundSessionQueueStatus.FAILED,
                            "last_error": "missing_run_id",
                            "completed_at": now,
                            "updated_at": now,
                        }
                    )
                )
                progress = True
                continue
            runtime = self._run_runtime_repo.get(run_id)
            if runtime is None:
                continue
            if runtime.status == RunRuntimeStatus.COMPLETED:
                _ = self._repository.update(
                    record.model_copy(
                        update={
                            "status": AutomationBoundSessionQueueStatus.COMPLETED,
                            "completed_at": now,
                            "last_error": None,
                            "updated_at": now,
                        }
                    )
                )
                progress = True
                continue
            if runtime.status == RunRuntimeStatus.FAILED:
                _ = self._repository.update(
                    record.model_copy(
                        update={
                            "status": AutomationBoundSessionQueueStatus.FAILED,
                            "completed_at": now,
                            "last_error": runtime.last_error,
                            "updated_at": now,
                        }
                    )
                )
                progress = True
        return progress

    def _start_queued_runs(self, *, limit: int) -> bool:
        progress = False
        now = _utc_now()
        stale_before = now - timedelta(seconds=_CLAIM_STALE_AFTER_SECONDS)
        for record in self._repository.list_ready_to_start(ready_at=now, limit=limit):
            if (
                self._repository.count_non_terminal_ahead(record.automation_queue_id)
                > 0
            ):
                continue
            if self._active_recoverable_run_id(record.session_id) is not None:
                continue
            claimed = self._repository.claim_starting(
                automation_queue_id=record.automation_queue_id,
                stale_before=stale_before,
            )
            if claimed is None:
                continue
            progress = True
            try:
                run_id = self._start_bound_run(
                    project_name=claimed.automation_project_name,
                    project_id=claimed.automation_project_id,
                    session_id=claimed.session_id,
                    reason=claimed.reason,
                    prompt=claimed.prompt,
                    run_config=claimed.run_config,
                    binding=claimed.binding,
                    delivery_events=claimed.delivery_events,
                    send_started=False,
                )
            except RuntimeError as exc:
                self._handle_start_failure(claimed, error=str(exc))
                continue
            started_at = _utc_now()
            _ = self._repository.update(
                claimed.model_copy(
                    update={
                        "run_id": run_id,
                        "status": AutomationBoundSessionQueueStatus.WAITING_RESULT,
                        "start_attempts": claimed.start_attempts + 1,
                        "next_attempt_at": started_at,
                        "last_error": None,
                        "updated_at": started_at,
                    }
                )
            )
            self._touch_project_started(
                automation_project_id=claimed.automation_project_id,
                session_id=claimed.session_id,
                started_at=started_at,
            )
        return progress

    def _start_bound_run(
        self,
        *,
        project_name: str,
        project_id: str,
        session_id: str,
        reason: str,
        prompt: str,
        run_config: AutomationRunConfig,
        binding: AutomationFeishuBinding,
        delivery_events: tuple[AutomationDeliveryEvent, ...],
        send_started: bool,
    ) -> str:
        run_id, _ = self._run_service.create_detached_run(
            IntentInput(
                session_id=session_id,
                input=content_parts_from_text(prompt),
                execution_mode=run_config.execution_mode,
                yolo=run_config.yolo,
                thinking=run_config.thinking,
                conversation_context=RuntimePromptConversationContext(
                    source_provider=FEISHU_PLATFORM,
                    source_kind="im",
                    feishu_chat_type=binding.chat_type,
                    im_force_direct_send=True,
                ),
            )
        )
        self._run_service.ensure_run_started(run_id)
        _ = self._delivery_service.register_run(
            project=None,
            session_id=session_id,
            run_id=run_id,
            reason=reason,
            project_id=project_id,
            project_name=project_name,
            binding=binding,
            delivery_events=delivery_events,
            send_started=send_started,
        )
        return run_id

    def _handle_start_failure(
        self,
        record: AutomationBoundSessionQueueRecord,
        *,
        error: str,
    ) -> None:
        attempts = record.start_attempts + 1
        now = _utc_now()
        if attempts >= _START_MAX_ATTEMPTS:
            _ = self._repository.update(
                record.model_copy(
                    update={
                        "status": AutomationBoundSessionQueueStatus.FAILED,
                        "start_attempts": attempts,
                        "last_error": error,
                        "completed_at": now,
                        "updated_at": now,
                    }
                )
            )
            self._send_direct_text(
                record.binding.trigger_id,
                record.binding.chat_id,
                _build_start_failure_message(
                    project_name=record.automation_project_name,
                    error=error,
                ),
            )
            return
        _ = self._repository.update(
            record.model_copy(
                update={
                    "status": AutomationBoundSessionQueueStatus.QUEUED,
                    "start_attempts": attempts,
                    "next_attempt_at": now + _backoff_for_attempt(attempts),
                    "last_error": error,
                    "updated_at": now,
                }
            )
        )

    def _active_recoverable_run_id(self, session_id: str) -> str | None:
        runtimes = sorted(
            self._run_runtime_repo.list_by_session(session_id),
            key=lambda item: item.updated_at,
            reverse=True,
        )
        for runtime in runtimes:
            if runtime.status in {
                RunRuntimeStatus.QUEUED,
                RunRuntimeStatus.RUNNING,
                RunRuntimeStatus.PAUSED,
                RunRuntimeStatus.STOPPED,
            }:
                return runtime.run_id
        return None

    def _touch_project_started(
        self,
        *,
        automation_project_id: str,
        session_id: str,
        started_at: datetime,
    ) -> None:
        try:
            project = self._project_repository.get(automation_project_id)
        except KeyError:
            return
        _ = self._project_repository.update(
            project.model_copy(
                update={
                    "last_session_id": session_id,
                    "last_run_started_at": started_at,
                    "last_error": None,
                    "updated_at": started_at,
                }
            )
        )

    def _send_direct_text(self, trigger_id: str, chat_id: str, text: str) -> None:
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


class AutomationBoundSessionQueueWorker:
    def __init__(
        self,
        *,
        queue_service: AutomationBoundSessionQueueService,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self._queue_service = queue_service
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
            name="automation-bound-session-queue-worker",
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
                progress = self._queue_service.process_pending()
                if progress:
                    continue
            except Exception as exc:
                log_event(
                    logger,
                    logging.ERROR,
                    event="automation.bound_session_queue.loop_failed",
                    message="Automation bound session queue loop failed",
                    payload={"error": str(exc)},
                    exc_info=exc,
                )
            self._wake_event.wait(timeout=self._poll_interval_seconds)
            self._wake_event.clear()


def _build_queued_prompt(*, project_name: str, prompt: str) -> str:
    return f"定时任务触发：{project_name}\n\n{prompt}"


def _build_queue_message(*, project_name: str, ahead_count: int) -> str:
    return f"定时任务 {project_name} 准备执行，当前任务前面有 {ahead_count} 个消息"


def _build_start_failure_message(*, project_name: str, error: str) -> str:
    failure_detail = str(error).strip() or "未知错误。"
    return f"定时任务 {project_name} 执行失败。\n\n{failure_detail}"


def _backoff_for_attempt(attempt: int) -> timedelta:
    if attempt <= 1:
        return timedelta(seconds=1)
    if attempt == 2:
        return timedelta(seconds=5)
    if attempt == 3:
        return timedelta(seconds=15)
    return timedelta(minutes=1)


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


__all__ = [
    "AutomationBoundSessionQueueService",
    "AutomationBoundSessionQueueWorker",
]
