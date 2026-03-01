from __future__ import annotations

from dataclasses import dataclass, field
from json import dumps
from typing import Callable

from agent_teams.agents.management.instance_pool import InstancePool
from agent_teams.core.enums import EventType, ExecutionMode, InstanceStatus, RunEventType, ScopeType, TaskStatus
from agent_teams.core.ids import new_task_id, new_trace_id
from agent_teams.core.models import EventEnvelope, IntentInput, RoleDefinition, RunEvent, TaskEnvelope, VerificationPlan
from agent_teams.coordination.task_execution_service import TaskExecutionService
from agent_teams.state.event_log import EventLog
from agent_teams.prompting.runtime_prompt_builder import RuntimePromptBuilder
from agent_teams.providers.llm import LLMProvider
from agent_teams.roles.registry import RoleRegistry
from agent_teams.runtime.console import log_debug
from agent_teams.runtime.gate_manager import GateManager
from agent_teams.runtime.run_event_hub import RunEventHub
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.shared_store import SharedStore
from agent_teams.state.task_repo import TaskRepository
from agent_teams.tools.verify_task.impl import verify_task

ROLE_COORDINATOR = 'coordinator_agent'
MAX_ORCHESTRATION_CYCLES = 8
HUMAN_DISPATCH_TIMEOUT = 600.0  # 10 min before timing out a human-mode run
GATE_TIMEOUT = 300.0             # 5 min before timing out a gate decision


@dataclass
class CoordinatorGraph:
    role_registry: RoleRegistry
    instance_pool: InstancePool
    task_repo: TaskRepository
    shared_store: SharedStore
    event_bus: EventLog
    agent_repo: AgentInstanceRepository
    prompt_builder: RuntimePromptBuilder
    provider_factory: Callable[[RoleDefinition], LLMProvider]
    task_execution_service: TaskExecutionService
    gate_manager: GateManager = field(default_factory=GateManager)
    run_event_hub: RunEventHub | None = None

    def run(self, intent: IntentInput, trace_id: str | None = None) -> tuple[str, str, str, str]:
        trace_id = trace_id or new_trace_id().value
        self.role_registry.get(ROLE_COORDINATOR)
        log_debug(
            f'[coord:start] run={trace_id} mode={intent.execution_mode.value} '
            f'gate={intent.confirmation_gate} session={intent.session_id} intent={intent.intent[:120]}'
        )

        root_task = TaskEnvelope(
            task_id=new_task_id().value,
            session_id=intent.session_id,
            parent_task_id=None,
            trace_id=trace_id,
            objective=intent.intent,
            scope=('end_to_end_delivery',),
            dod=('response produced',),
            verification=VerificationPlan(checklist=('non_empty_response',)),
            confirmation_gate=intent.confirmation_gate,
        )
        self.task_repo.create(root_task)
        log_debug(f'[coord:root-task] run={trace_id} task={root_task.task_id}')
        self.event_bus.emit(
            EventEnvelope(
                event_type=EventType.TASK_CREATED,
                trace_id=trace_id,
                session_id=intent.session_id,
                task_id=root_task.task_id,
                payload_json='{}',
            )
        )

        # Reuse coordinator instance across turns so message history is preserved.
        existing_instance_id = self.agent_repo.get_coordinator_instance_id(intent.session_id)
        known_ids = {i.instance_id for i in self.instance_pool.list_instances()}
        if existing_instance_id and existing_instance_id in known_ids:
            coordinator_instance_id = existing_instance_id
            self.instance_pool.mark_idle(coordinator_instance_id)
            self.agent_repo.mark_status(coordinator_instance_id, InstanceStatus.IDLE)
            self.task_repo.update_status(
                task_id=root_task.task_id,
                status=TaskStatus.ASSIGNED,
                assigned_instance_id=coordinator_instance_id,
            )
            self.event_bus.emit(
                EventEnvelope(
                    event_type=EventType.TASK_ASSIGNED,
                    trace_id=trace_id,
                    session_id=intent.session_id,
                    task_id=root_task.task_id,
                    instance_id=coordinator_instance_id,
                    payload_json='{}',
                )
            )
            log_debug(f'[coord:reuse-instance] run={trace_id} instance={coordinator_instance_id} role={ROLE_COORDINATOR}')
        else:
            coordinator_instance_id = self._create_instance(
                role_id=ROLE_COORDINATOR,
                task=root_task,
                session_id=intent.session_id,
                trace_id=trace_id,
            )
            log_debug(f'[coord:new-instance] run={trace_id} instance={coordinator_instance_id} role={ROLE_COORDINATOR}')

        from pydantic_ai.messages import ModelRequest, UserPromptPart
        self.task_execution_service.message_repo.append(
            session_id=intent.session_id,
            instance_id=coordinator_instance_id,
            task_id=root_task.task_id,
            trace_id=trace_id,
            messages=[ModelRequest(parts=[UserPromptPart(content=intent.intent)])]
        )
        log_debug(f'[coord:instance-ready] run={trace_id} instance={coordinator_instance_id} role={ROLE_COORDINATOR}')

        # ── Dispatch to the correct execution mode ──────────────────────────
        mode = intent.execution_mode

        if mode == ExecutionMode.AI:
            result = self._run_ai_mode(
                intent=intent,
                trace_id=trace_id,
                root_task=root_task,
                coordinator_instance_id=coordinator_instance_id,
            )
        elif mode == ExecutionMode.AUTO:
            result = self._run_auto_mode(
                intent=intent,
                trace_id=trace_id,
                root_task=root_task,
                coordinator_instance_id=coordinator_instance_id,
            )
        elif mode == ExecutionMode.HUMAN:
            result = self._run_human_mode(
                intent=intent,
                trace_id=trace_id,
                root_task=root_task,
                coordinator_instance_id=coordinator_instance_id,
            )
        else:
            raise ValueError(f'Unknown execution mode: {mode}')

        verification = verify_task(self.task_repo, self.event_bus, root_task.task_id)
        status = 'completed' if verification.passed else 'failed'
        log_debug(f'[coord:finish] run={trace_id} mode={mode.value} status={status} root_task={root_task.task_id}')
        return trace_id, root_task.task_id, status, result

    # ─────────────────────────────────────────────────────────────────────────
    # Execution modes
    # ─────────────────────────────────────────────────────────────────────────

    def _run_ai_mode(
        self,
        intent: IntentInput,
        trace_id: str,
        root_task: TaskEnvelope,
        coordinator_instance_id: str,
    ) -> str:
        """Current default: Coordinator LLM plans + re-plans between sub-task batches."""
        coordinator_result = self._task_executor(
            instance_id=coordinator_instance_id,
            role_id=ROLE_COORDINATOR,
            task=root_task,
        )
        log_debug(f'[coord:ai:first-pass-done] run={trace_id}')

        cycle = 0
        while cycle < MAX_ORCHESTRATION_CYCLES:
            cycle += 1
            log_debug(f'[coord:ai:cycle] run={trace_id} cycle={cycle}')
            ran_any = self._run_pending_delegated_tasks(
                trace_id=trace_id,
                root_task_id=root_task.task_id,
                confirmation_gate=intent.confirmation_gate,
            )
            if not ran_any:
                log_debug(f'[coord:ai:cycle-stop] run={trace_id} cycle={cycle} reason=no-pending-subtasks')
                break
            coordinator_result = self._task_executor(
                instance_id=coordinator_instance_id,
                role_id=ROLE_COORDINATOR,
                task=root_task,
            )
            log_debug(f'[coord:ai:cycle-pass-done] run={trace_id} cycle={cycle}')

        return coordinator_result

    def _run_auto_mode(
        self,
        intent: IntentInput,
        trace_id: str,
        root_task: TaskEnvelope,
        coordinator_instance_id: str,
    ) -> str:
        """
        Full-auto: Coordinator LLM plans once, then all sub-tasks run to completion
        without returning to the Coordinator between batches.  The confirmation gate
        still applies to each sub-task if enabled.
        """
        coordinator_result = self._task_executor(
            instance_id=coordinator_instance_id,
            role_id=ROLE_COORDINATOR,
            task=root_task,
        )
        log_debug(f'[coord:auto:plan-done] run={trace_id}')

        # Keep draining pending sub-tasks until none are left — no re-planning.
        max_drain = MAX_ORCHESTRATION_CYCLES * 10  # safety cap
        iteration = 0
        while iteration < max_drain:
            iteration += 1
            ran_any = self._run_pending_delegated_tasks(
                trace_id=trace_id,
                root_task_id=root_task.task_id,
                confirmation_gate=intent.confirmation_gate,
            )
            if not ran_any:
                log_debug(f'[coord:auto:drain-done] run={trace_id} iterations={iteration}')
                break

        return coordinator_result

    def _run_human_mode(
        self,
        intent: IntentInput,
        trace_id: str,
        root_task: TaskEnvelope,
        coordinator_instance_id: str,
    ) -> str:
        """
        Human orchestration: Coordinator LLM still plans the initial sub-tasks, but
        the human decides *which* sub-task to run next by calling the dispatch API.
        After each human-dispatched execution the loop re-publishes the pending list.
        """
        # Let the coordinator LLM produce the initial plan (creates sub-tasks in task_repo)
        coordinator_result = self._task_executor(
            instance_id=coordinator_instance_id,
            role_id=ROLE_COORDINATOR,
            task=root_task,
        )
        log_debug(f'[coord:human:plan-done] run={trace_id}')

        max_cycles = MAX_ORCHESTRATION_CYCLES * 10
        cycle = 0
        while cycle < max_cycles:
            cycle += 1
            pending = self._get_pending_delegated_tasks(trace_id=trace_id, root_task_id=root_task.task_id)
            if not pending:
                log_debug(f'[coord:human:all-done] run={trace_id} cycles={cycle}')
                break

            # Publish the "waiting for human to pick a task" event
            self._publish_run_event(
                session_id=intent.session_id,
                run_id=trace_id,
                trace_id=trace_id,
                task_id=root_task.task_id,
                event_type=RunEventType.AWAITING_HUMAN_DISPATCH,
                payload={
                    'pending_tasks': [
                        {
                            'task_id': r.envelope.task_id,
                            'objective': r.envelope.objective,
                            'role_id': r.assigned_instance_id and self._role_for_instance(r.assigned_instance_id),
                        }
                        for r in pending
                    ]
                },
            )
            log_debug(f'[coord:human:awaiting-dispatch] run={trace_id} pending={len(pending)}')

            # Block until the human dispatches exactly one task via the API.
            # The API handler calls injection_manager.enqueue() with a special
            # JSON payload: {"__human_dispatch__": "<task_id>"}
            dispatched_task_id = self._wait_for_human_dispatch(
                run_id=trace_id,
                coordinator_instance_id=coordinator_instance_id,
                timeout=HUMAN_DISPATCH_TIMEOUT,
            )
            if dispatched_task_id is None:
                log_debug(f'[coord:human:dispatch-timeout] run={trace_id}')
                break

            # Find the record and execute only that task
            record = next((r for r in pending if r.envelope.task_id == dispatched_task_id), None)
            if record is None:
                log_debug(f'[coord:human:dispatch-unknown] run={trace_id} task={dispatched_task_id}')
                continue

            try:
                instance = self.instance_pool.get(record.assigned_instance_id)
            except KeyError:
                log_debug(f'[coord:human:dispatch-no-instance] run={trace_id} task={dispatched_task_id}')
                continue

            self._publish_run_event(
                session_id=intent.session_id,
                run_id=trace_id,
                trace_id=trace_id,
                task_id=dispatched_task_id,
                event_type=RunEventType.HUMAN_TASK_DISPATCHED,
                payload={'task_id': dispatched_task_id, 'role_id': instance.role_id},
            )
            result = self._task_executor(
                instance_id=instance.instance_id,
                role_id=instance.role_id,
                task=record.envelope,
            )
            log_debug(f'[coord:human:task-done] run={trace_id} task={dispatched_task_id}')

            # Apply confirmation gate if requested
            if intent.confirmation_gate:
                self._apply_gate(
                    run_id=trace_id,
                    session_id=intent.session_id,
                    task=record.envelope,
                    instance_id=instance.instance_id,
                    role_id=instance.role_id,
                    result=result,
                )

        return coordinator_result

    # ─────────────────────────────────────────────────────────────────────────
    # Shared helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _run_pending_delegated_tasks(
        self,
        trace_id: str,
        root_task_id: str,
        confirmation_gate: bool = False,
    ) -> bool:
        """Execute all currently-pending sub-tasks.  Returns True if any ran."""
        records = self.task_repo.list_by_trace(trace_id)
        ran_any = False
        for record in records:
            task = record.envelope
            if task.task_id == root_task_id:
                continue
            if record.status not in (TaskStatus.ASSIGNED, TaskStatus.CREATED):
                continue
            if record.assigned_instance_id is None:
                continue
            try:
                instance = self.instance_pool.get(record.assigned_instance_id)
            except KeyError:
                msg = f'Assigned instance not found: {record.assigned_instance_id}'
                log_debug(f'[coord:dispatch-error] run={trace_id} task={task.task_id} err={msg}')
                self.task_repo.update_status(task.task_id, TaskStatus.FAILED, error_message=msg)
                self.event_bus.emit(
                    EventEnvelope(
                        event_type=EventType.TASK_FAILED,
                        trace_id=task.trace_id,
                        session_id=task.session_id,
                        task_id=task.task_id,
                        instance_id=record.assigned_instance_id,
                        payload_json='{}',
                    )
                )
                continue
            log_debug(
                f'[coord:dispatch] run={trace_id} task={task.task_id} '
                f'instance={instance.instance_id} role={instance.role_id} status={record.status.value}'
            )
            result = self._task_executor(
                instance_id=instance.instance_id,
                role_id=instance.role_id,
                task=task,
            )
            ran_any = True

            # Confirmation gate: pause here and wait for human decision
            if confirmation_gate or task.confirmation_gate:
                self._apply_gate(
                    run_id=trace_id,
                    session_id=task.session_id,
                    task=task,
                    instance_id=instance.instance_id,
                    role_id=instance.role_id,
                    result=result,
                )
        return ran_any

    def _get_pending_delegated_tasks(self, trace_id: str, root_task_id: str):
        """Return all pending sub-task records for a trace."""
        return [
            r for r in self.task_repo.list_by_trace(trace_id)
            if r.envelope.task_id != root_task_id
            and r.status in (TaskStatus.ASSIGNED, TaskStatus.CREATED)
            and r.assigned_instance_id is not None
        ]

    def _apply_gate(
        self,
        run_id: str,
        session_id: str,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        result: str,
    ) -> None:
        """
        Open a confirmation gate and block until the human decides.
        On 'revise', inject the feedback and re-run the subagent (once).
        """
        self.gate_manager.open_gate(
            run_id=run_id,
            task_id=task.task_id,
            instance_id=instance_id,
            role_id=role_id,
            summary=result[:500],
        )
        self._publish_run_event(
            session_id=session_id,
            run_id=run_id,
            trace_id=task.trace_id,
            task_id=task.task_id,
            event_type=RunEventType.SUBAGENT_GATE,
            payload={
                'task_id': task.task_id,
                'instance_id': instance_id,
                'role_id': role_id,
                'summary': result[:500],
            },
        )
        log_debug(f'[coord:gate:open] run={run_id} task={task.task_id}')

        try:
            action, feedback = self.gate_manager.wait_for_gate(
                run_id=run_id, task_id=task.task_id, timeout=GATE_TIMEOUT
            )
        except TimeoutError:
            log_debug(f'[coord:gate:timeout] run={run_id} task={task.task_id}')
            self.gate_manager.close_gate(run_id, task.task_id)
            return

        self._publish_run_event(
            session_id=session_id,
            run_id=run_id,
            trace_id=task.trace_id,
            task_id=task.task_id,
            event_type=RunEventType.GATE_RESOLVED,
            payload={'task_id': task.task_id, 'action': action, 'feedback': feedback},
        )
        self.gate_manager.close_gate(run_id, task.task_id)
        log_debug(f'[coord:gate:resolved] run={run_id} task={task.task_id} action={action}')

        if action == 'revise' and feedback:
            # Inject the feedback as a user message and re-run this subagent once
            from agent_teams.core.enums import InjectionSource
            self.task_execution_service.injection_manager.enqueue(
                run_id=run_id,
                recipient_instance_id=instance_id,
                source=InjectionSource.USER,
                content=f'Please revise your previous output based on this feedback: {feedback}',
            )
            self.instance_pool.mark_idle(instance_id)
            log_debug(f'[coord:gate:revise] run={run_id} task={task.task_id} feedback={feedback[:80]}')
            self._task_executor(instance_id=instance_id, role_id=role_id, task=task)

    def _wait_for_human_dispatch(
        self,
        run_id: str,
        coordinator_instance_id: str,
        timeout: float,
    ) -> str | None:
        """
        Block until the human calls the dispatch API (which injects a special
        marker message) or until timeout.  Returns the dispatched task_id or None.
        """
        import json, threading
        result_holder: list[str | None] = [None]
        done = threading.Event()

        def _poll():
            import time
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                injections = self.task_execution_service.injection_manager.drain_at_boundary(
                    run_id, coordinator_instance_id
                )
                for msg in injections:
                    try:
                        data = json.loads(msg.content)
                        if '__human_dispatch__' in data:
                            result_holder[0] = data['__human_dispatch__']
                            done.set()
                            return
                    except (json.JSONDecodeError, TypeError):
                        pass
                time.sleep(0.5)
            done.set()

        t = threading.Thread(target=_poll, daemon=True)
        t.start()
        done.wait()
        t.join(timeout=1)
        return result_holder[0]

    def _role_for_instance(self, instance_id: str) -> str | None:
        try:
            return self.instance_pool.get(instance_id).role_id
        except KeyError:
            return None

    def _publish_run_event(
        self,
        session_id: str,
        run_id: str,
        trace_id: str,
        task_id: str | None,
        event_type: RunEventType,
        payload: dict,
    ) -> None:
        if self.run_event_hub is None:
            return
        self.run_event_hub.publish(
            RunEvent(
                session_id=session_id,
                run_id=run_id,
                trace_id=trace_id,
                task_id=task_id,
                event_type=event_type,
                payload_json=dumps(payload),
            )
        )

    def _create_instance(self, role_id: str, task: TaskEnvelope, session_id: str, trace_id: str) -> str:
        instance = self.instance_pool.create_subagent(role_id)
        log_debug(f'[subagent:create] run={trace_id} task={task.task_id} role={role_id} instance={instance.instance_id}')
        self.task_repo.update_status(
            task_id=task.task_id,
            status=TaskStatus.ASSIGNED,
            assigned_instance_id=instance.instance_id,
        )
        self.agent_repo.upsert_instance(
            run_id=trace_id,
            trace_id=trace_id,
            session_id=session_id,
            instance_id=instance.instance_id,
            role_id=role_id,
            status=InstanceStatus.IDLE,
        )
        self.event_bus.emit(
            EventEnvelope(
                event_type=EventType.INSTANCE_CREATED,
                trace_id=trace_id,
                session_id=session_id,
                task_id=task.task_id,
                instance_id=instance.instance_id,
                payload_json='{}',
            )
        )
        self.event_bus.emit(
            EventEnvelope(
                event_type=EventType.TASK_ASSIGNED,
                trace_id=trace_id,
                session_id=session_id,
                task_id=task.task_id,
                instance_id=instance.instance_id,
                payload_json='{}',
            )
        )
        return instance.instance_id

    def _task_executor(self, instance_id: str, role_id: str, task: TaskEnvelope) -> str:
        return self.task_execution_service.execute(instance_id=instance_id, role_id=role_id, task=task)
