from __future__ import annotations

import asyncio
from json import dumps
import logging
from typing import Callable, cast

from agent_teams.core.enums import InjectionSource, RunEventType
from agent_teams.core.ids import new_trace_id
from agent_teams.core.models import IntentInput, RunEvent, RunResult
from agent_teams.runtime.gate_manager import GateManager
from agent_teams.runtime.injection_manager import RunInjectionManager
from agent_teams.runtime.logging import get_logger, log_event
from agent_teams.runtime.run_control_manager import RunControlManager
from agent_teams.runtime.run_event_hub import RunEventHub
from agent_teams.runtime.tool_approval_manager import ToolApprovalAction, ToolApprovalManager
from agent_teams.runtime.trace import bind_trace_context

logger = get_logger(__name__)


class RunManager:
    def __init__(
        self,
        *,
        meta_agent,
        injection_manager: RunInjectionManager,
        run_event_hub: RunEventHub,
        gate_manager: GateManager,
        run_control_manager: RunControlManager,
        tool_approval_manager: ToolApprovalManager,
    ) -> None:
        self._meta_agent = meta_agent
        self._injection_manager = injection_manager
        self._run_event_hub = run_event_hub
        self._gate_manager = gate_manager
        self._run_control_manager = run_control_manager
        self._tool_approval_manager = tool_approval_manager
        self._pending_runs: dict[str, IntentInput] = {}
        self._running_run_ids: set[str] = set()

    async def run_intent(
        self,
        intent: IntentInput,
        *,
        ensure_session: Callable[[str | None], str],
    ) -> RunResult:
        intent.session_id = ensure_session(intent.session_id)
        self._run_control_manager.assert_session_allows_main_input(intent.session_id)
        run_id = new_trace_id().value
        with bind_trace_context(trace_id=run_id, run_id=run_id, session_id=intent.session_id):
            log_event(logger, logging.INFO, event='run.started.direct', message='Direct run started')
            self._injection_manager.activate(run_id)
            try:
                result = await self._meta_agent.handle_intent(intent, trace_id=run_id)
                log_event(
                    logger,
                    logging.INFO,
                    event='run.completed.direct',
                    message='Direct run completed',
                    payload={'root_task_id': result.root_task_id},
                )
                return result
            finally:
                self._injection_manager.deactivate(run_id)

    def create_run(
        self,
        intent: IntentInput,
        *,
        ensure_session: Callable[[str | None], str],
    ) -> tuple[str, str]:
        intent.session_id = ensure_session(intent.session_id)
        self._run_control_manager.assert_session_allows_main_input(intent.session_id)
        run_id = new_trace_id().value
        self._pending_runs[run_id] = intent
        with bind_trace_context(trace_id=run_id, run_id=run_id, session_id=intent.session_id):
            log_event(logger, logging.INFO, event='run.queued', message='Run queued for streaming execution')
        return run_id, intent.session_id

    def ensure_run_started(self, run_id: str) -> None:
        if run_id in self._running_run_ids:
            return
        intent = self._pending_runs.get(run_id)
        if intent is None:
            raise KeyError(f'Run {run_id} not found')

        self._running_run_ids.add(run_id)
        self._injection_manager.activate(run_id)
        with bind_trace_context(trace_id=run_id, run_id=run_id, session_id=intent.session_id):
            log_event(logger, logging.INFO, event='run.started', message='Run worker started')
        self._run_event_hub.publish(
            RunEvent(
                session_id=intent.session_id,
                run_id=run_id,
                trace_id=run_id,
                task_id=None,
                event_type=RunEventType.RUN_STARTED,
                payload_json=dumps({'session_id': intent.session_id}),
            )
        )

        async def _worker() -> None:
            try:
                result = await self._meta_agent.handle_intent(intent, trace_id=run_id)
                self._run_event_hub.publish(
                    RunEvent(
                        session_id=intent.session_id,
                        run_id=run_id,
                        trace_id=result.trace_id,
                        task_id=result.root_task_id,
                        event_type=RunEventType.RUN_COMPLETED,
                        payload_json=dumps(result.model_dump()),
                    )
                )
                with bind_trace_context(trace_id=run_id, run_id=run_id, session_id=intent.session_id):
                    log_event(
                        logger,
                        logging.INFO,
                        event='run.completed',
                        message='Run completed',
                        payload={'root_task_id': result.root_task_id},
                    )
            except asyncio.CancelledError:
                self._run_control_manager.publish_run_stopped(
                    session_id=intent.session_id,
                    run_id=run_id,
                    reason='stopped_by_user',
                )
                with bind_trace_context(trace_id=run_id, run_id=run_id, session_id=intent.session_id):
                    log_event(
                        logger,
                        logging.WARNING,
                        event='run.stopped',
                        message='Run cancelled',
                        payload={'reason': 'stopped_by_user'},
                    )
            except Exception as exc:
                self._run_event_hub.publish(
                    RunEvent(
                        session_id=intent.session_id,
                        run_id=run_id,
                        trace_id=run_id,
                        task_id=None,
                        event_type=RunEventType.RUN_FAILED,
                        payload_json=dumps({'error': str(exc)}),
                    )
                )
                with bind_trace_context(trace_id=run_id, run_id=run_id, session_id=intent.session_id):
                    log_event(
                        logger,
                        logging.ERROR,
                        event='run.failed',
                        message='Run failed',
                        exc_info=exc,
                    )
            finally:
                self._injection_manager.deactivate(run_id)
                self._run_control_manager.unregister_run_task(run_id)
                self._running_run_ids.discard(run_id)
                self._pending_runs.pop(run_id, None)

        task = asyncio.create_task(_worker())
        self._run_control_manager.register_run_task(
            run_id=run_id,
            session_id=intent.session_id,
            task=task,
        )

    async def stream_run_events(self, run_id: str):
        queue = self._run_event_hub.subscribe(run_id)
        self.ensure_run_started(run_id)

        while True:
            event = await queue.get()
            yield event
            if event.event_type in (
                RunEventType.RUN_COMPLETED,
                RunEventType.RUN_FAILED,
                RunEventType.RUN_STOPPED,
            ):
                self._run_event_hub.unsubscribe_all(run_id)
                break

    async def run_intent_stream(
        self,
        intent: IntentInput,
        *,
        ensure_session: Callable[[str | None], str],
    ):
        run_id, _ = self.create_run(intent, ensure_session=ensure_session)
        async for event in self.stream_run_events(run_id):
            yield event

    def inject_message(
        self,
        run_id: str,
        source: InjectionSource,
        content: str,
    ):
        return self._run_control_manager.inject_to_running_agents(
            run_id=run_id,
            source=source,
            content=content,
        )

    def stop_run(self, run_id: str) -> None:
        self._run_control_manager.clear_paused_subagent_for_run(run_id)
        if run_id in self._pending_runs and run_id not in self._running_run_ids:
            intent = self._pending_runs.pop(run_id)
            self._run_control_manager.publish_run_stopped(
                session_id=intent.session_id,
                run_id=run_id,
                reason='stopped_before_start',
            )
            return

        requested = self._run_control_manager.request_run_stop(run_id)
        if not requested and run_id not in self._running_run_ids:
            raise KeyError(f'Run {run_id} not found')

    def stop_subagent(self, run_id: str, instance_id: str) -> dict[str, str]:
        return self._run_control_manager.stop_subagent(
            run_id=run_id,
            instance_id=instance_id,
        )

    def inject_subagent_message(
        self,
        *,
        run_id: str,
        instance_id: str,
        content: str,
    ) -> None:
        self._run_control_manager.resume_subagent_with_message(
            run_id=run_id,
            instance_id=instance_id,
            content=content,
        )

    def resolve_gate(
        self,
        run_id: str,
        task_id: str,
        action: str,
        feedback: str = '',
    ) -> None:
        self._gate_manager.resolve_gate(
            run_id, task_id, action=action, feedback=feedback
        )  # type: ignore[arg-type]

    def list_open_gates(self, run_id: str) -> list[dict]:
        return self._gate_manager.list_open_gates(run_id)

    def resolve_tool_approval(
        self,
        run_id: str,
        tool_call_id: str,
        action: str,
        feedback: str = '',
    ) -> None:
        if action not in {'approve', 'deny'}:
            raise ValueError(f'Unsupported action: {action}')
        self._tool_approval_manager.resolve_approval(
            run_id=run_id,
            tool_call_id=tool_call_id,
            action=cast(ToolApprovalAction, action),
            feedback=feedback,
        )

    def list_open_tool_approvals(self, run_id: str) -> list[dict[str, str]]:
        return self._tool_approval_manager.list_open_approvals(run_id=run_id)

    def dispatch_task_human(
        self,
        run_id: str,
        task_id: str,
        coordinator_instance_id: str,
    ) -> None:
        self._run_control_manager.dispatch_task_human(
            run_id=run_id,
            task_id=task_id,
            coordinator_instance_id=coordinator_instance_id,
        )

    def get_coordinator_instance_id(self, session_id: str) -> str | None:
        return self._run_control_manager.get_coordinator_instance_id(session_id)

    def dispatch_task_human_for_session(
        self,
        session_id: str,
        run_id: str,
        task_id: str,
    ) -> None:
        coordinator_instance_id = self.get_coordinator_instance_id(session_id)
        if coordinator_instance_id is None:
            raise KeyError(f'No coordinator instance found for session={session_id}')
        self.dispatch_task_human(
            run_id=run_id,
            task_id=task_id,
            coordinator_instance_id=coordinator_instance_id,
        )
