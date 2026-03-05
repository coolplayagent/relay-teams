from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from json import dumps
from threading import Lock
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict
from pydantic_ai.messages import ModelRequest, UserPromptPart

from agent_teams.core.enums import (
    InjectionSource,
    InstanceStatus,
    RunEventType,
    TaskStatus,
    EventType,
)
from agent_teams.core.models import (
    InjectionMessage,
    RunEvent,
    TaskEnvelope,
    EventEnvelope,
)

if TYPE_CHECKING:
    from agent_teams.agents.management.instance_pool import InstancePool
    from agent_teams.runtime.injection_manager import RunInjectionManager
    from agent_teams.runtime.run_event_hub import RunEventHub
    from agent_teams.state.agent_repo import AgentInstanceRepository
    from agent_teams.state.event_log import EventLog
    from agent_teams.state.message_repo import MessageRepository
    from agent_teams.state.task_repo import TaskRepository


class RunControlContext(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    manager: "RunControlManager"
    run_id: str
    instance_id: str | None = None

    def is_cancelled(self) -> bool:
        return self.manager.is_cancelled(
            run_id=self.run_id, instance_id=self.instance_id
        )

    def raise_if_cancelled(self) -> None:
        self.manager.raise_if_cancelled(
            run_id=self.run_id, instance_id=self.instance_id
        )


class PausedSubagent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    run_id: str
    instance_id: str
    role_id: str
    task_id: str | None
    reason: str
    paused_at: datetime


class RunControlManager:
    def __init__(self) -> None:
        self._lock = Lock()
        self._run_tasks: dict[str, asyncio.Task[None]] = {}
        self._run_stop_requested: set[str] = set()
        self._instance_tasks: dict[tuple[str, str], asyncio.Task[str]] = {}
        self._instance_context: dict[tuple[str, str], tuple[str, str, str | None]] = {}
        self._subagent_stop_requested: set[tuple[str, str]] = set()
        self._paused_by_session: dict[str, PausedSubagent] = {}

        self._run_event_hub: RunEventHub | None = None
        self._injection_manager: RunInjectionManager | None = None
        self._agent_repo: AgentInstanceRepository | None = None
        self._task_repo: TaskRepository | None = None
        self._message_repo: MessageRepository | None = None
        self._instance_pool: InstancePool | None = None
        self._event_bus: EventLog | None = None

    def bind_runtime(
        self,
        *,
        run_event_hub: RunEventHub,
        injection_manager: RunInjectionManager,
        agent_repo: AgentInstanceRepository,
        task_repo: TaskRepository,
        message_repo: MessageRepository,
        instance_pool: InstancePool,
        event_bus: EventLog,
    ) -> None:
        self._run_event_hub = run_event_hub
        self._injection_manager = injection_manager
        self._agent_repo = agent_repo
        self._task_repo = task_repo
        self._message_repo = message_repo
        self._instance_pool = instance_pool
        self._event_bus = event_bus

    def context(
        self, *, run_id: str, instance_id: str | None = None
    ) -> RunControlContext:
        return RunControlContext(manager=self, run_id=run_id, instance_id=instance_id)

    def is_cancelled(self, *, run_id: str, instance_id: str | None = None) -> bool:
        if self.is_run_stop_requested(run_id):
            return True
        if instance_id is None:
            return False
        return self.is_subagent_stop_requested(run_id=run_id, instance_id=instance_id)

    def raise_if_cancelled(
        self, *, run_id: str, instance_id: str | None = None
    ) -> None:
        if self.is_cancelled(run_id=run_id, instance_id=instance_id):
            raise asyncio.CancelledError

    def assert_session_allows_main_input(self, session_id: str) -> None:
        paused = self.get_paused_subagent(session_id)
        if paused is None:
            return
        raise RuntimeError(
            f"Subagent {paused.role_id} ({paused.instance_id}) is paused in run {paused.run_id}. "
            "Please send a follow-up message to that subagent before messaging the main agent."
        )

    def register_run_task(
        self,
        *,
        run_id: str,
        session_id: str,
        task: asyncio.Task[None],
    ) -> None:
        with self._lock:
            self._run_tasks[run_id] = task
            self._run_stop_requested.discard(run_id)

    def unregister_run_task(self, run_id: str) -> None:
        with self._lock:
            self._run_tasks.pop(run_id, None)
            self._run_stop_requested.discard(run_id)

    def request_run_stop(self, run_id: str) -> bool:
        with self._lock:
            task = self._run_tasks.get(run_id)
            self._run_stop_requested.add(run_id)
            if task is not None and not task.done():
                task.cancel()
            for (rid, _), inst_task in list(self._instance_tasks.items()):
                if rid == run_id and not inst_task.done():
                    inst_task.cancel()
            return task is not None

    def is_run_stop_requested(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._run_stop_requested

    def publish_run_stopped(self, *, session_id: str, run_id: str, reason: str) -> None:
        self._require_run_event_hub().publish(
            RunEvent(
                session_id=session_id,
                run_id=run_id,
                trace_id=run_id,
                task_id=None,
                event_type=RunEventType.RUN_STOPPED,
                payload_json=dumps({"reason": reason}),
            )
        )

    def inject_to_running_agents(
        self,
        *,
        run_id: str,
        source: InjectionSource,
        content: str,
    ) -> InjectionMessage:
        agent_repo = self._require_agent_repo()
        created: InjectionMessage | None = None
        running = agent_repo.list_running(run_id)
        if not running:
            raise KeyError(f"No RUNNING agent for run_id={run_id}")
        for record in running:
            created = self._require_injection_manager().enqueue(
                run_id=run_id,
                recipient_instance_id=record.instance_id,
                source=source,
                content=content,
            )
            self._publish_injection_event(run_id=run_id, record=record, created=created)
        if created is None:
            raise KeyError(f"No RUNNING agent for run_id={run_id}")
        return created

    def register_instance_task(
        self,
        *,
        run_id: str,
        session_id: str,
        instance_id: str,
        role_id: str,
        task_id: str | None,
        task: asyncio.Task[str],
    ) -> None:
        key = (run_id, instance_id)
        with self._lock:
            self._instance_tasks[key] = task
            self._instance_context[key] = (session_id, role_id, task_id)

    def unregister_instance_task(self, *, run_id: str, instance_id: str) -> None:
        key = (run_id, instance_id)
        with self._lock:
            self._instance_tasks.pop(key, None)
            self._instance_context.pop(key, None)

    def stop_subagent(self, *, run_id: str, instance_id: str) -> dict[str, str]:
        record = self._require_agent_repo().get_instance(instance_id)
        if record.run_id != run_id:
            raise KeyError(f"Instance {instance_id} does not belong to run {run_id}")
        if record.role_id == "coordinator_agent":
            raise ValueError("Stopping coordinator via subagent API is not allowed")

        paused = self.request_subagent_stop(run_id=run_id, instance_id=instance_id)
        if paused is None:
            paused = self.pause_subagent(
                session_id=record.session_id,
                run_id=run_id,
                instance_id=instance_id,
                role_id=record.role_id,
                task_id=self._find_task_for_instance(
                    run_id=run_id, instance_id=instance_id
                ),
            )

        if paused.task_id:
            self._require_task_repo().update_status(
                task_id=paused.task_id,
                status=TaskStatus.STOPPED,
                assigned_instance_id=instance_id,
                error_message="Task stopped by user",
            )
        self._require_agent_repo().mark_status(instance_id, InstanceStatus.STOPPED)

        self._require_run_event_hub().publish(
            RunEvent(
                session_id=record.session_id,
                run_id=run_id,
                trace_id=run_id,
                task_id=paused.task_id,
                instance_id=instance_id,
                role_id=record.role_id,
                event_type=RunEventType.SUBAGENT_STOPPED,
                payload_json=dumps(
                    {
                        "instance_id": instance_id,
                        "role_id": record.role_id,
                        "task_id": paused.task_id,
                        "reason": paused.reason,
                    }
                ),
            )
        )
        return {
            "status": "paused",
            "instance_id": instance_id,
            "role_id": record.role_id,
            "task_id": paused.task_id or "",
            "run_id": run_id,
        }

    def resume_subagent_with_message(
        self,
        *,
        run_id: str,
        instance_id: str,
        content: str,
    ) -> None:
        record = self._require_agent_repo().get_instance(instance_id)
        if record.run_id != run_id:
            raise KeyError(f"Instance {instance_id} does not belong to run {run_id}")

        paused = self.get_paused_subagent(record.session_id)
        if paused is not None and paused.instance_id != instance_id:
            raise RuntimeError(
                f"Subagent {paused.role_id} ({paused.instance_id}) is paused. "
                "Please continue that paused subagent first."
            )

        task_id = self._find_task_for_instance(run_id=run_id, instance_id=instance_id)
        if task_id:
            self._require_task_repo().update_status(
                task_id=task_id,
                status=TaskStatus.ASSIGNED,
                assigned_instance_id=instance_id,
            )

        self._require_message_repo().append(
            session_id=record.session_id,
            instance_id=instance_id,
            task_id=task_id or "subagent-followup",
            trace_id=run_id,
            messages=[ModelRequest(parts=[UserPromptPart(content=content)])],
        )

        if (
            self._require_injection_manager().is_active(run_id)
            and record.status == InstanceStatus.RUNNING
        ):
            created = self._require_injection_manager().enqueue(
                run_id=run_id,
                recipient_instance_id=instance_id,
                source=InjectionSource.USER,
                content=content,
            )
            self._publish_injection_event(run_id=run_id, record=record, created=created)

        self.release_paused_subagent(
            session_id=record.session_id, instance_id=instance_id
        )
        self._require_agent_repo().mark_status(instance_id, InstanceStatus.IDLE)

        self._require_run_event_hub().publish(
            RunEvent(
                session_id=record.session_id,
                run_id=run_id,
                trace_id=run_id,
                task_id=task_id,
                instance_id=instance_id,
                role_id=record.role_id,
                event_type=RunEventType.SUBAGENT_RESUMED,
                payload_json=dumps(
                    {
                        "instance_id": instance_id,
                        "role_id": record.role_id,
                        "task_id": task_id,
                    }
                ),
            )
        )

    def handle_instance_cancelled(
        self,
        *,
        task: TaskEnvelope,
        instance_id: str,
    ) -> bool:
        stopped = self.is_cancelled(run_id=task.trace_id, instance_id=instance_id)
        if stopped:
            self._require_task_repo().update_status(
                task.task_id,
                TaskStatus.STOPPED,
                error_message="Task stopped by user",
            )
            self._require_instance_pool().mark_stopped(instance_id)
            self._require_agent_repo().mark_status(instance_id, InstanceStatus.STOPPED)
            self._require_event_bus().emit(
                EventEnvelope(
                    event_type=EventType.TASK_STOPPED,
                    trace_id=task.trace_id,
                    session_id=task.session_id,
                    task_id=task.task_id,
                    instance_id=instance_id,
                    payload_json="{}",
                )
            )
            self._require_event_bus().emit(
                EventEnvelope(
                    event_type=EventType.INSTANCE_STOPPED,
                    trace_id=task.trace_id,
                    session_id=task.session_id,
                    task_id=task.task_id,
                    instance_id=instance_id,
                    payload_json="{}",
                )
            )
        else:
            self._require_task_repo().update_status(
                task.task_id,
                TaskStatus.FAILED,
                error_message="Task cancelled",
            )
            self._require_instance_pool().mark_failed(instance_id)
            self._require_agent_repo().mark_status(instance_id, InstanceStatus.FAILED)
            self._require_event_bus().emit(
                EventEnvelope(
                    event_type=EventType.TASK_FAILED,
                    trace_id=task.trace_id,
                    session_id=task.session_id,
                    task_id=task.task_id,
                    instance_id=instance_id,
                    payload_json="{}",
                )
            )
        return stopped

    def request_subagent_stop(
        self, *, run_id: str, instance_id: str
    ) -> PausedSubagent | None:
        key = (run_id, instance_id)
        with self._lock:
            context = self._instance_context.get(key)
            if context is None:
                return None
            session_id, role_id, task_id = context
            paused = PausedSubagent(
                session_id=session_id,
                run_id=run_id,
                instance_id=instance_id,
                role_id=role_id,
                task_id=task_id,
                reason="stopped_by_user",
                paused_at=datetime.now(tz=timezone.utc),
            )
            self._paused_by_session[session_id] = paused
            self._subagent_stop_requested.add(key)
            task = self._instance_tasks.get(key)
            if task is not None and not task.done():
                task.cancel()
            return paused

    def pause_subagent(
        self,
        *,
        session_id: str,
        run_id: str,
        instance_id: str,
        role_id: str,
        task_id: str | None,
        reason: str = "stopped_by_user",
    ) -> PausedSubagent:
        paused = PausedSubagent(
            session_id=session_id,
            run_id=run_id,
            instance_id=instance_id,
            role_id=role_id,
            task_id=task_id,
            reason=reason,
            paused_at=datetime.now(tz=timezone.utc),
        )
        with self._lock:
            self._paused_by_session[session_id] = paused
            self._subagent_stop_requested.add((run_id, instance_id))
        return paused

    def is_subagent_stop_requested(self, *, run_id: str, instance_id: str) -> bool:
        with self._lock:
            return (run_id, instance_id) in self._subagent_stop_requested

    def get_paused_subagent(self, session_id: str) -> PausedSubagent | None:
        with self._lock:
            return self._paused_by_session.get(session_id)

    def is_subagent_paused(self, *, session_id: str, instance_id: str) -> bool:
        with self._lock:
            paused = self._paused_by_session.get(session_id)
            return paused is not None and paused.instance_id == instance_id

    def release_paused_subagent(
        self,
        *,
        session_id: str,
        instance_id: str | None = None,
    ) -> PausedSubagent | None:
        with self._lock:
            paused = self._paused_by_session.get(session_id)
            if paused is None:
                return None
            if instance_id is not None and paused.instance_id != instance_id:
                return None
            self._paused_by_session.pop(session_id, None)
            self._subagent_stop_requested.discard((paused.run_id, paused.instance_id))
            return paused

    def clear_paused_subagent_for_run(self, run_id: str) -> None:
        with self._lock:
            for session_id, paused in list(self._paused_by_session.items()):
                if paused.run_id == run_id:
                    self._paused_by_session.pop(session_id, None)
                    self._subagent_stop_requested.discard(
                        (paused.run_id, paused.instance_id)
                    )

    def get_coordinator_instance_id(self, session_id: str) -> str | None:
        return self._require_agent_repo().get_coordinator_instance_id(session_id)

    def _find_task_for_instance(self, *, run_id: str, instance_id: str) -> str | None:
        for record in self._require_task_repo().list_by_trace(run_id):
            if record.assigned_instance_id != instance_id:
                continue
            if record.status in (
                TaskStatus.RUNNING,
                TaskStatus.ASSIGNED,
                TaskStatus.CREATED,
                TaskStatus.STOPPED,
            ):
                return record.envelope.task_id
        return None

    def _publish_injection_event(
        self, *, run_id: str, record, created: InjectionMessage
    ) -> None:
        self._require_run_event_hub().publish(
            RunEvent(
                session_id=record.session_id,
                run_id=run_id,
                trace_id=run_id,
                task_id=None,
                instance_id=record.instance_id,
                role_id=record.role_id,
                event_type=RunEventType.INJECTION_ENQUEUED,
                payload_json=created.model_dump_json(),
            )
        )

    def _require_run_event_hub(self) -> RunEventHub:
        if self._run_event_hub is None:
            raise RuntimeError("RunControlManager is not bound: run_event_hub missing")
        return self._run_event_hub

    def _require_injection_manager(self) -> RunInjectionManager:
        if self._injection_manager is None:
            raise RuntimeError(
                "RunControlManager is not bound: injection_manager missing"
            )
        return self._injection_manager

    def _require_agent_repo(self) -> AgentInstanceRepository:
        if self._agent_repo is None:
            raise RuntimeError("RunControlManager is not bound: agent_repo missing")
        return self._agent_repo

    def _require_task_repo(self) -> TaskRepository:
        if self._task_repo is None:
            raise RuntimeError("RunControlManager is not bound: task_repo missing")
        return self._task_repo

    def _require_message_repo(self) -> MessageRepository:
        if self._message_repo is None:
            raise RuntimeError("RunControlManager is not bound: message_repo missing")
        return self._message_repo

    def _require_instance_pool(self) -> InstancePool:
        if self._instance_pool is None:
            raise RuntimeError("RunControlManager is not bound: instance_pool missing")
        return self._instance_pool

    def _require_event_bus(self) -> EventLog:
        if self._event_bus is None:
            raise RuntimeError("RunControlManager is not bound: event_bus missing")
        return self._event_bus
