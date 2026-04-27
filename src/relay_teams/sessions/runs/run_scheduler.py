# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from json import dumps

from relay_teams.agents.orchestration.meta_agent import MetaAgent
from relay_teams.hooks import HookService
from relay_teams.logger import get_logger, log_event
from relay_teams.notifications import NotificationType
from relay_teams.sessions.runs.enums import InjectionSource, RunEventType
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.ids import new_trace_id
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.media_run_executor import MediaRunExecutor
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.run_models import (
    IntentInput,
    RunEvent,
    RunKind,
    RunResult,
)
from relay_teams.sessions.runs.run_recovery import RunRecoveryService
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRecord,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.trace import bind_trace_context

logger = get_logger(__name__)


class RunScheduler:
    def __init__(
        self,
        *,
        meta_agent: MetaAgent,
        injection_manager: RunInjectionManager,
        run_event_hub: RunEventHub,
        run_control_manager: RunControlManager,
        session_repo: SessionRepository,
        media_executor: MediaRunExecutor,
        recovery_service: RunRecoveryService,
        get_hook_service: Callable[[], HookService | None],
        get_run_runtime_repo: Callable[[], RunRuntimeRepository | None],
        get_run_intent_repo: Callable[[], RunIntentRepository | None],
        pending_runs: dict[str, IntentInput],
        running_run_ids: set[str],
        resume_requested_runs: set[str],
        should_delegate_to_bound_loop: Callable[[], bool],
        call_in_bound_loop: Callable[
            [Callable[[], tuple[str, str] | None]], tuple[str, str] | None
        ],
        ensure_session: Callable[[str], str],
        prepare_intent: Callable[[IntentInput], IntentInput],
        active_recoverable_run: Callable[
            [str], tuple[str, RunRuntimeRecord | None] | None
        ],
        run_accepts_followups: Callable[[str, IntentInput], bool],
        assert_auto_attach_allowed: Callable[[str, RunRuntimeRecord | None], None],
        merge_intent: Callable[[str, str], str],
        append_followup_to_coordinator: Callable[
            [str, str, bool, InjectionSource], bool
        ],
        update_run_yolo: Callable[[str, str, bool], None],
        remember_active_run: Callable[[str, str], None],
        runtime_for_run: Callable[[str], RunRuntimeRecord | None],
        worker: Callable[
            [str, str, Callable[[], Awaitable[RunResult]]],
            asyncio.Task[None],
        ],
        resume_existing_run: Callable[[str], Awaitable[RunResult]],
        complete_pending_user_questions: Callable[[str, str], None],
        emit_notification: Callable[
            [NotificationType, str, str, str, str, str, str, str],
            None,
        ],
    ) -> None:
        self._meta_agent = meta_agent
        self._injection_manager = injection_manager
        self._run_event_hub = run_event_hub
        self._run_control_manager = run_control_manager
        self._session_repo = session_repo
        self._media_executor = media_executor
        self._recovery_service = recovery_service
        self._get_hook_service = get_hook_service
        self._get_run_runtime_repo = get_run_runtime_repo
        self._get_run_intent_repo = get_run_intent_repo
        self._pending_runs = pending_runs
        self._running_run_ids = running_run_ids
        self._resume_requested_runs = resume_requested_runs
        self._should_delegate_to_bound_loop = should_delegate_to_bound_loop
        self._call_in_bound_loop = call_in_bound_loop
        self._ensure_session = ensure_session
        self._prepare_intent = prepare_intent
        self._active_recoverable_run = active_recoverable_run
        self._run_accepts_followups = run_accepts_followups
        self._assert_auto_attach_allowed = assert_auto_attach_allowed
        self._merge_intent = merge_intent
        self._append_followup_to_coordinator = append_followup_to_coordinator
        self._update_run_yolo = update_run_yolo
        self._remember_active_run = remember_active_run
        self._runtime_for_run = runtime_for_run
        self._worker = worker
        self._resume_existing_run = resume_existing_run
        self._complete_pending_user_questions = complete_pending_user_questions
        self._emit_notification = emit_notification

    def create_run(
        self,
        intent: IntentInput,
        *,
        source: InjectionSource = InjectionSource.USER,
    ) -> tuple[str, str]:
        if self._should_delegate_to_bound_loop():
            delegated_intent = intent.model_copy(deep=True)
            result = self._call_in_bound_loop(
                lambda: self._create_run_local(
                    delegated_intent,
                    allow_active_run_attach=True,
                    source=source,
                )
            )
            if result is None:
                raise RuntimeError("Run creation did not return a result")
            return result
        return self._create_run_local(
            intent,
            allow_active_run_attach=True,
            source=source,
        )

    def create_detached_run(self, intent: IntentInput) -> tuple[str, str]:
        if self._should_delegate_to_bound_loop():
            delegated_intent = intent.model_copy(deep=True)
            result = self._call_in_bound_loop(
                lambda: self._create_run_local(
                    delegated_intent,
                    allow_active_run_attach=False,
                    source=InjectionSource.USER,
                )
            )
            if result is None:
                raise RuntimeError("Run creation did not return a result")
            return result
        return self._create_run_local(
            intent,
            allow_active_run_attach=False,
            source=InjectionSource.USER,
        )

    def create_run_local(
        self,
        intent: IntentInput,
        *,
        allow_active_run_attach: bool,
        source: InjectionSource,
    ) -> tuple[str, str]:
        return self._create_run_local(
            intent,
            allow_active_run_attach=allow_active_run_attach,
            source=source,
        )

    def _create_run_local(
        self,
        intent: IntentInput,
        *,
        allow_active_run_attach: bool,
        source: InjectionSource,
    ) -> tuple[str, str]:
        session_id = self._ensure_session(intent.session_id)
        intent.session_id = session_id
        intent = self._prepare_intent(intent)
        self._run_control_manager.assert_session_allows_main_input(session_id)
        _ = self._session_repo.mark_started(session_id)

        existing = self._active_recoverable_run(session_id)
        if existing is not None:
            active_run_id, runtime = existing
            if not allow_active_run_attach:
                raise RuntimeError(
                    f"Session {session_id} already has active run {active_run_id}"
                )
            if not self._run_accepts_followups(active_run_id, intent):
                raise RuntimeError(
                    f"Run {active_run_id} is active and does not accept follow-up input"
                )
            self._assert_auto_attach_allowed(active_run_id, runtime)
            if (
                active_run_id in self._pending_runs
                and active_run_id not in self._running_run_ids
            ):
                pending = self._pending_runs[active_run_id]
                pending.intent = self._merge_intent(pending.intent, intent.intent)
                existing_skills = pending.skills or ()
                next_skills = intent.skills or ()
                merged_skills = tuple(dict.fromkeys((*existing_skills, *next_skills)))
                pending.skills = merged_skills or None
                pending.yolo = intent.yolo
                run_intent_repo = self._get_run_intent_repo()
                if run_intent_repo is not None:
                    run_intent_repo.upsert(
                        run_id=active_run_id,
                        session_id=session_id,
                        intent=pending,
                    )
                with bind_trace_context(
                    trace_id=active_run_id,
                    run_id=active_run_id,
                    session_id=session_id,
                ):
                    log_event(
                        logger,
                        logging.INFO,
                        event="run.followup.attached",
                        message="Follow-up merged into pending run",
                        payload={"mode": "pending_merge"},
                    )
                return active_run_id, session_id
            if (
                active_run_id in self._running_run_ids
                or self._injection_manager.is_active(active_run_id)
            ):
                self._update_run_yolo(active_run_id, session_id, intent.yolo)
                self._append_followup_to_coordinator(
                    active_run_id,
                    intent.intent,
                    True,
                    source,
                )
                with bind_trace_context(
                    trace_id=active_run_id,
                    run_id=active_run_id,
                    session_id=session_id,
                ):
                    log_event(
                        logger,
                        logging.INFO,
                        event="run.followup.attached",
                        message="Follow-up enqueued to active coordinator",
                        payload={"mode": "active_enqueue"},
                    )
                return active_run_id, session_id
            if (
                runtime is not None
                and runtime.is_recoverable
                and runtime.status
                in {RunRuntimeStatus.PAUSED, RunRuntimeStatus.STOPPED}
            ):
                self._append_followup_to_coordinator(
                    active_run_id,
                    intent.intent,
                    False,
                    InjectionSource.USER,
                )
                self._update_run_yolo(active_run_id, session_id, intent.yolo)
                self._resume_requested_runs.add(active_run_id)
                with bind_trace_context(
                    trace_id=active_run_id,
                    run_id=active_run_id,
                    session_id=session_id,
                ):
                    log_event(
                        logger,
                        logging.INFO,
                        event="run.followup.attached",
                        message="Follow-up queued for recoverable run",
                        payload={"mode": "recoverable_resume"},
                    )
                return active_run_id, session_id

        return self._queue_new_run(session_id=session_id, intent=intent)

    def _queue_new_run(
        self, *, session_id: str, intent: IntentInput
    ) -> tuple[str, str]:
        run_id = new_trace_id().value
        hook_service = self._get_hook_service()
        if hook_service is not None:
            hook_service.snapshot_run(run_id)
        self._pending_runs[run_id] = intent
        run_runtime_repo = self._get_run_runtime_repo()
        if run_runtime_repo is not None:
            run_runtime_repo.ensure(
                run_id=run_id,
                session_id=session_id,
                status=RunRuntimeStatus.QUEUED,
                phase=RunRuntimePhase.IDLE,
            )
        run_intent_repo = self._get_run_intent_repo()
        if run_intent_repo is not None:
            run_intent_repo.upsert(run_id=run_id, session_id=session_id, intent=intent)
        self._remember_active_run(session_id, run_id)
        with bind_trace_context(trace_id=run_id, run_id=run_id, session_id=session_id):
            log_event(
                logger,
                logging.INFO,
                event="run.queued",
                message="Run queued for streaming execution",
            )
        return run_id, session_id

    def queue_new_run(self, *, session_id: str, intent: IntentInput) -> tuple[str, str]:
        return self._queue_new_run(session_id=session_id, intent=intent)

    def ensure_run_started(self, run_id: str) -> None:
        if self._should_delegate_to_bound_loop():
            _ = self._call_in_bound_loop(
                lambda: self._ensure_run_started_local(run_id) or None
            )
            return
        self._ensure_run_started_local(run_id)

    def _ensure_run_started_local(self, run_id: str) -> None:
        if run_id in self._running_run_ids:
            return
        if run_id in self._pending_runs:
            self._start_new_run_worker(run_id)
            return
        if run_id in self._resume_requested_runs:
            runtime = self._runtime_for_run(run_id)
            if runtime is None:
                raise KeyError(f"Run {run_id} not found")
            if runtime.status not in {
                RunRuntimeStatus.QUEUED,
                RunRuntimeStatus.PAUSED,
                RunRuntimeStatus.STOPPED,
            }:
                raise RuntimeError(
                    f"Run {run_id} cannot be resumed from status {runtime.status.value}"
                )
            self._start_resume_worker(run_id)
            return
        raise KeyError(f"Run {run_id} not found")

    def ensure_run_started_local(self, run_id: str) -> None:
        self._ensure_run_started_local(run_id)

    def _start_new_run_worker(self, run_id: str) -> None:
        intent = self._pending_runs.get(run_id)
        if intent is None:
            raise KeyError(f"Run {run_id} not found")
        session_id = intent.session_id
        if session_id is None:
            raise RuntimeError(f"Run {run_id} is missing session id")
        self._running_run_ids.add(run_id)
        self._injection_manager.activate(run_id)
        run_runtime_repo = self._get_run_runtime_repo()
        if run_runtime_repo is not None:
            run_runtime_repo.ensure(
                run_id=run_id,
                session_id=session_id,
                status=RunRuntimeStatus.RUNNING,
                phase=RunRuntimePhase.COORDINATOR_RUNNING,
            )
            run_runtime_repo.update(
                run_id,
                status=RunRuntimeStatus.RUNNING,
                phase=RunRuntimePhase.COORDINATOR_RUNNING,
                last_error=None,
            )
        self._run_event_hub.publish(
            RunEvent(
                session_id=session_id,
                run_id=run_id,
                trace_id=run_id,
                task_id=None,
                event_type=RunEventType.RUN_STARTED,
                payload_json=dumps({"session_id": session_id}),
            )
        )
        runner = (
            (
                lambda: self._media_executor.run_media_generation(
                    run_id=run_id,
                    intent=intent,
                )
            )
            if intent.run_kind != RunKind.CONVERSATION
            else (lambda: self._meta_agent.handle_intent(intent, trace_id=run_id))
        )
        task = self._worker(run_id, session_id, runner)
        self._run_control_manager.register_run_task(
            run_id=run_id,
            session_id=session_id,
            task=task,
        )

    def start_new_run_worker(self, run_id: str) -> None:
        self._start_new_run_worker(run_id)

    def _start_resume_worker(self, run_id: str) -> None:
        runtime = self._runtime_for_run(run_id)
        if runtime is None:
            raise KeyError(f"Run {run_id} not found")
        session_id = runtime.session_id
        self._running_run_ids.add(run_id)
        self._resume_requested_runs.discard(run_id)
        self._injection_manager.activate(run_id)
        resume_payload = self._recovery_service.transition_run_to_resumed(
            run_id=run_id,
            session_id=session_id,
            reason="resume",
        )
        task = self._worker(
            run_id,
            session_id,
            lambda: self._resume_existing_run(run_id),
        )
        self._run_control_manager.register_run_task(
            run_id=run_id,
            session_id=session_id,
            task=task,
        )
        with bind_trace_context(trace_id=run_id, run_id=run_id, session_id=session_id):
            log_event(
                logger,
                logging.INFO,
                event="run.resumed",
                message="Recoverable run resumed",
                payload=resume_payload,
            )

    def start_resume_worker(self, run_id: str) -> None:
        self._start_resume_worker(run_id)

    def resume_run(self, run_id: str) -> str:
        if run_id in self._running_run_ids:
            raise RuntimeError(f"Run {run_id} is already running")
        if run_id in self._pending_runs:
            pending = self._pending_runs[run_id]
            if pending.session_id is None:
                raise RuntimeError(f"Run {run_id} is missing session id")
            if run_id in self._resume_requested_runs:
                return pending.session_id
            self._resume_requested_runs.add(run_id)
            self._remember_active_run(pending.session_id, run_id)
            with bind_trace_context(
                trace_id=run_id, run_id=run_id, session_id=pending.session_id
            ):
                log_event(
                    logger,
                    logging.INFO,
                    event="run.resume.requested",
                    message="Resume requested for pending run",
                )
            return pending.session_id

        runtime = self._runtime_for_run(run_id)
        if runtime is None:
            raise KeyError(f"Run {run_id} not found")
        if runtime.status == RunRuntimeStatus.RUNNING:
            raise RuntimeError(f"Run {run_id} is already running")
        if runtime.status == RunRuntimeStatus.STOPPING:
            raise RuntimeError(
                f"Run {run_id} is stopping. Wait for it to stop before resuming."
            )
        if not runtime.is_recoverable:
            raise RuntimeError(f"Run {run_id} is not recoverable")
        if run_id in self._resume_requested_runs:
            return runtime.session_id
        self._resume_requested_runs.add(run_id)
        self._remember_active_run(runtime.session_id, run_id)
        with bind_trace_context(
            trace_id=run_id, run_id=run_id, session_id=runtime.session_id
        ):
            log_event(
                logger,
                logging.INFO,
                event="run.resume.requested",
                message="Resume requested for recoverable run",
            )
        return runtime.session_id

    def stop_run(self, run_id: str) -> None:
        if self._should_delegate_to_bound_loop():
            _ = self._call_in_bound_loop(lambda: self._stop_run_local(run_id) or None)
            return
        self._stop_run_local(run_id)

    def _stop_run_local(self, run_id: str) -> None:
        self._run_control_manager.clear_paused_subagent_for_run(run_id)
        if run_id in self._pending_runs and run_id not in self._running_run_ids:
            self._complete_pending_user_questions(run_id, "run_stopped")
            intent = self._pending_runs.pop(run_id)
            session_id = intent.session_id
            if session_id is None:
                raise RuntimeError(f"Run {run_id} is missing session id")
            run_runtime_repo = self._get_run_runtime_repo()
            if run_runtime_repo is not None:
                run_runtime_repo.update(
                    run_id,
                    status=RunRuntimeStatus.STOPPED,
                    phase=RunRuntimePhase.IDLE,
                    active_instance_id=None,
                    active_task_id=None,
                    active_role_id=None,
                    active_subagent_instance_id=None,
                    last_error="stopped_before_start",
                )
            self._run_control_manager.publish_run_stopped(
                session_id=session_id,
                run_id=run_id,
                reason="stopped_before_start",
            )
            self._emit_notification(
                NotificationType.RUN_STOPPED,
                session_id,
                run_id,
                run_id,
                "Run Stopped",
                f"Run {run_id} was stopped before start.",
                intent.session_mode.value,
                intent.run_kind.value,
            )
            with bind_trace_context(
                trace_id=run_id, run_id=run_id, session_id=session_id
            ):
                log_event(
                    logger,
                    logging.WARNING,
                    event="run.stopped",
                    message="Pending run stopped before worker start",
                    payload={"reason": "stopped_before_start"},
                )
            return

        requested = self._run_control_manager.request_run_stop(run_id)
        if not requested and run_id not in self._running_run_ids:
            raise KeyError(f"Run {run_id} not found")
        if requested:
            self._complete_pending_user_questions(run_id, "run_stopped")
        run_runtime_repo = self._get_run_runtime_repo()
        if run_runtime_repo is not None and requested:
            runtime = run_runtime_repo.get(run_id)
            if runtime is not None:
                run_runtime_repo.update(
                    run_id,
                    status=RunRuntimeStatus.STOPPING,
                    phase=runtime.phase,
                    last_error="stop_requested",
                )
        with bind_trace_context(trace_id=run_id, run_id=run_id):
            log_event(
                logger,
                logging.WARNING,
                event="run.stop.requested",
                message="Run stop requested",
                payload={"was_running": requested},
            )

    def stop_run_local(self, run_id: str) -> None:
        self._stop_run_local(run_id)
