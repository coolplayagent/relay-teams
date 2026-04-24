# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Optional, Protocol
from uuid import uuid4

from relay_teams.automation.automation_bound_session_queue_repository import (
    AutomationBoundSessionQueueRepository,
)
from relay_teams.automation.automation_delivery_service import AutomationDeliveryService
from relay_teams.automation.automation_models import (
    AutomationBoundSessionQueueRecord,
    AutomationBoundSessionQueueStatus,
    AutomationCleanupStatus,
    AutomationDeliveryEvent,
    AutomationExecutionHandle,
    AutomationFeishuBinding,
    AutomationProjectRecord,
    AutomationRunConfig,
)
from relay_teams.gateway.session_ingress_service import (
    GatewaySessionIngressBusyPolicy,
    GatewaySessionIngressRequest,
    GatewaySessionIngressService,
)
from relay_teams.gateway.feishu.models import FEISHU_PLATFORM, FeishuEnvironment
from relay_teams.logger import get_logger, log_event
from relay_teams.automation.prompt_building import build_automation_prompt
from relay_teams.media import content_parts_from_text
from relay_teams.sessions.runs.run_models import (
    IntentInput,
    RuntimePromptConversationContext,
)
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimeRecord,
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.sessions.session_models import SessionRecord

logger = get_logger(__name__)

_START_MAX_ATTEMPTS = 5
_RESUME_MAX_ATTEMPTS = 5
_CLAIM_STALE_AFTER_SECONDS = 60


class SessionLookup(Protocol):
    def get_session(self, session_id: str) -> SessionRecord: ...

    def rebind_session_workspace(
        self,
        session_id: str,
        *,
        workspace_id: str,
    ) -> SessionRecord: ...


class RunServiceLike(Protocol):
    def create_detached_run(self, intent: IntentInput) -> tuple[str, str]: ...

    def ensure_run_started(self, run_id: str) -> None: ...

    def resume_run(self, run_id: str) -> str: ...


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


class AutomationProjectLookup(Protocol):
    def get(self, automation_project_id: str) -> AutomationProjectRecord: ...

    def update(self, record: AutomationProjectRecord) -> AutomationProjectRecord: ...


class AutomationBoundSessionQueueService:
    def __init__(
        self,
        *,
        repository: AutomationBoundSessionQueueRepository,
        session_lookup: SessionLookup,
        run_service: RunServiceLike,
        run_runtime_repo: RunRuntimeRepository,
        delivery_service: AutomationDeliveryService,
        runtime_config_lookup: FeishuRuntimeConfigLookup,
        feishu_client: FeishuClientLike,
        project_repository: AutomationProjectLookup,
        session_ingress_service: GatewaySessionIngressService | None = None,
    ) -> None:
        self._repository = repository
        self._session_lookup = session_lookup
        self._run_service = run_service
        self._run_runtime_repo = run_runtime_repo
        self._delivery_service = delivery_service
        self._runtime_config_lookup = runtime_config_lookup
        self._feishu_client = feishu_client
        self._project_repository = project_repository
        self._session_ingress_service = session_ingress_service

    def materialize_execution(
        self,
        *,
        project: AutomationProjectRecord,
        reason: str,
    ) -> AutomationExecutionHandle | None:
        binding = project.delivery_binding
        if binding is None:
            return None
        if not isinstance(binding, AutomationFeishuBinding):
            return None
        session_id = str(binding.session_id or "").strip()
        if not session_id:
            raise RuntimeError("missing_bound_session_id")
        runtime_config = self._runtime_config_lookup.get_runtime_config_by_trigger_id(
            binding.trigger_id
        )
        if runtime_config is None:
            raise RuntimeError("missing_runtime_config")
        try:
            _ = self._session_lookup.get_session(session_id)
        except KeyError:
            raise RuntimeError(f"missing_bound_session:{session_id}") from None
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
                    prompt=build_automation_prompt(
                        project_name=project.display_name,
                        prompt=project.prompt,
                    ),
                    queue_message=_build_queue_message(
                        project_name=project.display_name,
                        ahead_count=ahead_count,
                    ),
                )
            )
            queue_message_id = self._send_direct_text(
                record.binding.trigger_id,
                record.binding.chat_id,
                record.queue_message,
            )
            _ = self._repository.update(
                record.model_copy(
                    update={
                        "queue_message_id": queue_message_id,
                        "updated_at": _utc_now(),
                    }
                )
            )
            return AutomationExecutionHandle(session_id=session_id, queued=True)

        run_id = self._start_bound_run(
            project_name=project.display_name,
            project_id=project.automation_project_id,
            session_id=session_id,
            workspace_id=project.workspace_id,
            reason=reason,
            prompt=build_automation_prompt(
                project_name=project.display_name,
                prompt=project.prompt,
            ),
            run_config=project.run_config,
            binding=binding,
            delivery_events=project.delivery_events,
            send_started=True,
        )
        started_at = _utc_now()
        _ = self._repository.create(
            AutomationBoundSessionQueueRecord(
                automation_queue_id=f"autq_{uuid4().hex[:12]}",
                automation_project_id=project.automation_project_id,
                automation_project_name=project.display_name,
                session_id=session_id,
                reason=reason,
                binding=binding,
                delivery_events=project.delivery_events,
                run_config=project.run_config,
                prompt=build_automation_prompt(
                    project_name=project.display_name,
                    prompt=project.prompt,
                ),
                queue_message=_build_direct_start_message(
                    project_name=project.display_name
                ),
                run_id=run_id,
                status=AutomationBoundSessionQueueStatus.WAITING_RESULT,
                next_attempt_at=started_at,
                resume_next_attempt_at=started_at,
            )
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
        progress = self._cleanup_queue_receipts(limit=limit) or progress
        return progress

    def has_project_queue(self, automation_project_id: str) -> bool:
        return self._repository.has_project_records(automation_project_id)

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
            runtime = self._normalize_recovery_runtime(runtime)
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
            if self._should_auto_resume(runtime):
                updated_record, changed = self._handle_recoverable_runtime(
                    record=record,
                    runtime=runtime,
                )
                record = updated_record
                progress = changed or progress
                if changed:
                    continue
            if runtime.status == RunRuntimeStatus.FAILED:
                final_record = self._repository.update(
                    record.model_copy(
                        update={
                            "status": AutomationBoundSessionQueueStatus.FAILED,
                            "completed_at": now,
                            "last_error": runtime.last_error,
                            "updated_at": now,
                        }
                    )
                )
                failure_message = _build_start_failure_message(
                    project_name=record.automation_project_name,
                    error=str(runtime.last_error or "").strip(),
                )
                _ = self._send_record_text(
                    record=record,
                    text=failure_message,
                )
                _ = self._schedule_queue_cleanup(
                    final_record,
                    updated_at=_utc_now(),
                )
                self._delivery_service.mark_terminal_delivery_skipped(
                    run_id=run_id,
                    terminal_message=failure_message,
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
                    workspace_id=self._project_workspace_id(
                        claimed.automation_project_id
                    ),
                    reason=claimed.reason,
                    prompt=claimed.prompt,
                    run_config=claimed.run_config,
                    binding=claimed.binding,
                    delivery_events=claimed.delivery_events,
                    send_started=False,
                    reply_to_message_id=claimed.queue_message_id,
                )
            except RuntimeError as exc:
                self._handle_start_failure(claimed, error=str(exc))
                continue
            started_at = _utc_now()
            started_record = self._repository.update(
                claimed.model_copy(
                    update={
                        "run_id": run_id,
                        "status": AutomationBoundSessionQueueStatus.WAITING_RESULT,
                        "start_attempts": claimed.start_attempts + 1,
                        "next_attempt_at": started_at,
                        "resume_next_attempt_at": started_at,
                        "last_error": None,
                        "updated_at": started_at,
                    }
                )
            )
            _ = self._schedule_queue_cleanup(
                started_record,
                updated_at=started_at,
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
        workspace_id: str,
        reason: str,
        prompt: str,
        run_config: AutomationRunConfig,
        binding: AutomationFeishuBinding,
        delivery_events: tuple[AutomationDeliveryEvent, ...],
        send_started: bool,
        reply_to_message_id: str | None = None,
    ) -> str:
        _ = self._ensure_session_workspace(
            session_id=session_id,
            workspace_id=workspace_id,
        )
        intent = IntentInput(
            session_id=session_id,
            input=content_parts_from_text(prompt),
            execution_mode=run_config.execution_mode,
            yolo=run_config.yolo,
            thinking=run_config.thinking,
            conversation_context=RuntimePromptConversationContext(
                source_provider=FEISHU_PLATFORM,
                source_kind="im",
                feishu_chat_type=binding.chat_type,
                im_reply_to_message_id=reply_to_message_id,
            ),
        )
        if self._session_ingress_service is not None:
            result = self._session_ingress_service.require_started(
                GatewaySessionIngressRequest(
                    intent=intent,
                    busy_policy=GatewaySessionIngressBusyPolicy.START_IF_IDLE,
                )
            )
            if result.run_id is None:
                raise RuntimeError("automation_bound_run_not_started")
            run_id = result.run_id
        else:
            run_id, _ = self._run_service.create_detached_run(intent)
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
            reply_to_message_id=reply_to_message_id,
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
            failed_record = self._repository.update(
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
            _ = self._send_record_text(
                record=record,
                text=_build_start_failure_message(
                    project_name=record.automation_project_name,
                    error=error,
                ),
            )
            _ = self._schedule_queue_cleanup(failed_record, updated_at=_utc_now())
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
            normalized_runtime = self._normalize_recovery_runtime(runtime)
            if normalized_runtime.status in {
                RunRuntimeStatus.QUEUED,
                RunRuntimeStatus.RUNNING,
                RunRuntimeStatus.PAUSED,
                RunRuntimeStatus.STOPPED,
            } or self._should_auto_resume(normalized_runtime):
                return runtime.run_id
        return None

    def _handle_recoverable_runtime(
        self,
        *,
        record: AutomationBoundSessionQueueRecord,
        runtime: RunRuntimeRecord,
    ) -> tuple[AutomationBoundSessionQueueRecord, bool]:
        now = _utc_now()
        if (
            record.resume_attempts == 0
            and record.resume_next_attempt_at <= record.updated_at
        ):
            next_record = self._repository.update(
                record.model_copy(
                    update={
                        "resume_next_attempt_at": now + _resume_backoff_for_attempt(1),
                        "last_error": runtime.last_error,
                        "updated_at": now,
                    }
                )
            )
            return next_record, True
        if record.resume_next_attempt_at > now:
            return record, False
        if record.resume_attempts >= _RESUME_MAX_ATTEMPTS:
            failed_record = self._repository.update(
                record.model_copy(
                    update={
                        "status": AutomationBoundSessionQueueStatus.FAILED,
                        "last_error": runtime.last_error or record.last_error,
                        "completed_at": now,
                        "updated_at": now,
                    }
                )
            )
            failure_message = _build_resume_failure_message(
                project_name=record.automation_project_name,
                error=str(runtime.last_error or record.last_error or "").strip(),
            )
            _ = self._send_record_text(
                record=record,
                text=failure_message,
            )
            _ = self._schedule_queue_cleanup(
                failed_record,
                updated_at=_utc_now(),
            )
            self._delivery_service.mark_terminal_delivery_skipped(
                run_id=runtime.run_id,
                terminal_message=failure_message,
            )
            return failed_record, True
        attempts = record.resume_attempts + 1
        try:
            _ = self._run_service.resume_run(runtime.run_id)
        except RuntimeError as exc:
            if attempts >= _RESUME_MAX_ATTEMPTS:
                failed_record = self._repository.update(
                    record.model_copy(
                        update={
                            "status": AutomationBoundSessionQueueStatus.FAILED,
                            "resume_attempts": attempts,
                            "last_error": str(exc),
                            "completed_at": now,
                            "updated_at": now,
                        }
                    )
                )
                failure_message = _build_resume_failure_message(
                    project_name=record.automation_project_name,
                    error=str(exc),
                )
                _ = self._send_record_text(
                    record=record,
                    text=failure_message,
                )
                _ = self._schedule_queue_cleanup(
                    failed_record,
                    updated_at=_utc_now(),
                )
                self._delivery_service.mark_terminal_delivery_skipped(
                    run_id=runtime.run_id,
                    terminal_message=failure_message,
                )
                return failed_record, True
            next_record = self._repository.update(
                record.model_copy(
                    update={
                        "resume_attempts": attempts,
                        "resume_next_attempt_at": now
                        + _resume_backoff_for_attempt(attempts + 1),
                        "last_error": str(exc),
                        "updated_at": now,
                    }
                )
            )
            return next_record, True
        next_record = self._repository.update(
            record.model_copy(
                update={
                    "resume_attempts": attempts,
                    "resume_next_attempt_at": now
                    + _resume_backoff_for_attempt(attempts + 1),
                    "last_error": None,
                    "updated_at": now,
                }
            )
        )
        return next_record, True

    def _should_auto_resume(self, runtime: RunRuntimeRecord) -> bool:
        if runtime.phase != RunRuntimePhase.AWAITING_RECOVERY:
            return False
        return runtime.status in {
            RunRuntimeStatus.PAUSED,
            RunRuntimeStatus.STOPPED,
            RunRuntimeStatus.FAILED,
        }

    def _normalize_recovery_runtime(
        self, runtime: RunRuntimeRecord
    ) -> RunRuntimeRecord:
        if not (
            runtime.phase == RunRuntimePhase.AWAITING_RECOVERY
            and runtime.status == RunRuntimeStatus.FAILED
        ):
            return runtime
        repaired = self._run_runtime_repo.update(
            runtime.run_id,
            status=RunRuntimeStatus.PAUSED,
        )
        log_event(
            logger,
            logging.WARNING,
            event="automation.bound_session_queue.runtime_repaired",
            message="Repaired dirty recoverable runtime state",
            payload={
                "run_id": runtime.run_id,
                "from_status": RunRuntimeStatus.FAILED.value,
                "to_status": RunRuntimeStatus.PAUSED.value,
            },
        )
        return repaired

    def _schedule_queue_cleanup(
        self,
        record: AutomationBoundSessionQueueRecord,
        *,
        updated_at: datetime,
    ) -> AutomationBoundSessionQueueRecord:
        if not str(record.queue_message_id or "").strip():
            return record
        if record.queue_cleanup_status == AutomationCleanupStatus.SKIPPED:
            return record
        return self._repository.update(
            record.model_copy(
                update={
                    "queue_cleanup_status": AutomationCleanupStatus.SKIPPED,
                    "updated_at": updated_at,
                }
            )
        )

    def _cleanup_queue_receipts(self, *, limit: int) -> bool:
        progress = False
        stale_before = _utc_now() - timedelta(seconds=_CLAIM_STALE_AFTER_SECONDS)
        for record in self._repository.list_pending_queue_cleanup(
            limit=limit,
            stale_before=stale_before,
        ):
            now = _utc_now()
            _ = self._repository.update(
                record.model_copy(
                    update={
                        "queue_cleanup_status": AutomationCleanupStatus.SKIPPED,
                        "updated_at": now,
                    }
                )
            )
            progress = True
        return progress

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

    def _send_direct_text(self, trigger_id: str, chat_id: str, text: str) -> str:
        runtime_config = self._runtime_config_lookup.get_runtime_config_by_trigger_id(
            trigger_id
        )
        if runtime_config is None:
            raise RuntimeError("missing_runtime_config")
        return self._feishu_client.send_text_message(
            chat_id=chat_id,
            text=text,
            environment=runtime_config.environment,
        )

    def _send_record_text(
        self,
        *,
        record: AutomationBoundSessionQueueRecord,
        text: str,
    ) -> str:
        runtime_config = self._runtime_config_lookup.get_runtime_config_by_trigger_id(
            record.binding.trigger_id
        )
        if runtime_config is None:
            raise RuntimeError("missing_runtime_config")
        reply_to_message_id = str(record.queue_message_id or "").strip()
        if reply_to_message_id:
            return self._feishu_client.reply_text_message(
                message_id=reply_to_message_id,
                text=text,
                environment=runtime_config.environment,
            )
        return self._feishu_client.send_text_message(
            chat_id=record.binding.chat_id,
            text=text,
            environment=runtime_config.environment,
        )

    def _ensure_session_workspace(
        self,
        *,
        session_id: str,
        workspace_id: str,
    ) -> SessionRecord:
        try:
            session = self._session_lookup.get_session(session_id)
        except KeyError:
            raise RuntimeError(f"missing_bound_session:{session_id}") from None
        if session.workspace_id == workspace_id:
            return session
        try:
            return self._session_lookup.rebind_session_workspace(
                session_id,
                workspace_id=workspace_id,
            )
        except KeyError:
            raise RuntimeError(f"missing_bound_session:{session_id}") from None

    def _project_workspace_id(self, automation_project_id: str) -> str:
        try:
            return self._project_repository.get(automation_project_id).workspace_id
        except KeyError:
            raise RuntimeError(
                f"missing_automation_project:{automation_project_id}"
            ) from None


class AutomationBoundSessionQueueWorker:
    def __init__(
        self,
        *,
        queue_service: AutomationBoundSessionQueueService,
        poll_interval_seconds: float = 1.0,
        stop_timeout_seconds: float = 10.0,
    ) -> None:
        self._queue_service = queue_service
        self._poll_interval_seconds = poll_interval_seconds
        self._stop_timeout_seconds = stop_timeout_seconds
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
            name="automation-bound-session-queue-worker",
        )

    async def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        task = self._task
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=self._stop_timeout_seconds)
        except asyncio.TimeoutError:
            log_event(
                logger,
                logging.WARNING,
                event="automation.bound_session_queue.stop_timeout",
                message=(
                    "Timed out waiting for automation bound session queue worker to stop"
                ),
                payload={"timeout_seconds": self._stop_timeout_seconds},
            )
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        except asyncio.CancelledError:
            pass
        self._task = None

    def wake(self) -> None:
        self._wake_event.set()

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                progress = await asyncio.to_thread(self._queue_service.process_pending)
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
            try:
                await asyncio.wait_for(
                    self._wake_event.wait(),
                    timeout=self._poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass
            self._wake_event.clear()


def _build_queue_message(*, project_name: str, ahead_count: int) -> str:
    return f"定时任务 {project_name} 准备执行，当前任务前面有 {ahead_count} 个消息"


def _build_direct_start_message(*, project_name: str) -> str:
    return f"定时任务 {project_name} 已直接启动"


def _build_start_failure_message(*, project_name: str, error: str) -> str:
    failure_detail = str(error).strip() or "未知错误。"
    return f"定时任务 {project_name} 执行失败。\n\n{failure_detail}"


def _build_resume_failure_message(*, project_name: str, error: str) -> str:
    failure_detail = str(error).strip() or "未知错误。"
    return f"定时任务 {project_name} 自动恢复失败。\n\n{failure_detail}"


def _backoff_for_attempt(attempt: int) -> timedelta:
    if attempt <= 1:
        return timedelta(seconds=1)
    if attempt == 2:
        return timedelta(seconds=5)
    if attempt == 3:
        return timedelta(seconds=15)
    return timedelta(minutes=1)


def _resume_backoff_for_attempt(attempt: int) -> timedelta:
    safe_attempt = max(1, attempt)
    return timedelta(seconds=10 * (2 ** (safe_attempt - 1)))


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


__all__ = [
    "AutomationBoundSessionQueueService",
    "AutomationBoundSessionQueueWorker",
]
