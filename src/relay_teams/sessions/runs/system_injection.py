from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict

from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.sessions.runs.enums import InjectionSource, RunEventType
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.run_models import InjectionMessage, RunEvent


class SystemInjectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    appended: bool = False
    enqueued: bool = False


class SystemInjectionSink:
    def __init__(
        self,
        *,
        injection_manager: RunInjectionManager,
        run_event_hub: RunEventHub,
        message_repo: Optional[MessageRepository] = None,
    ) -> None:
        self._injection_manager = injection_manager
        self._run_event_hub = run_event_hub
        self._message_repo = message_repo

    def enqueue_only(
        self,
        *,
        session_id: str,
        run_id: str,
        trace_id: str,
        task_id: Optional[str],
        instance_id: str,
        role_id: str,
        content: str,
        source: InjectionSource = InjectionSource.SYSTEM,
    ) -> SystemInjectionResult:
        record = self._enqueue(
            session_id=session_id,
            run_id=run_id,
            trace_id=trace_id,
            task_id=task_id,
            instance_id=instance_id,
            role_id=role_id,
            content=content,
            source=source,
        )
        return SystemInjectionResult(enqueued=record is not None)

    def append_and_enqueue(
        self,
        *,
        session_id: str,
        run_id: str,
        trace_id: str,
        task_id: str,
        instance_id: str,
        role_id: str,
        workspace_id: str,
        conversation_id: str,
        content: str,
        source: InjectionSource = InjectionSource.SYSTEM,
    ) -> SystemInjectionResult:
        appended = self._append(
            session_id=session_id,
            trace_id=trace_id,
            task_id=task_id,
            instance_id=instance_id,
            role_id=role_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            content=content,
        )
        record = self._enqueue(
            session_id=session_id,
            run_id=run_id,
            trace_id=trace_id,
            task_id=task_id,
            instance_id=instance_id,
            role_id=role_id,
            content=content,
            source=source,
        )
        return SystemInjectionResult(appended=appended, enqueued=record is not None)

    def append_only(
        self,
        *,
        session_id: str,
        trace_id: str,
        task_id: str,
        instance_id: str,
        role_id: str,
        workspace_id: str,
        conversation_id: str,
        content: str,
    ) -> SystemInjectionResult:
        appended = self._append(
            session_id=session_id,
            trace_id=trace_id,
            task_id=task_id,
            instance_id=instance_id,
            role_id=role_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            content=content,
        )
        return SystemInjectionResult(appended=appended)

    def _append(
        self,
        *,
        session_id: str,
        trace_id: str,
        task_id: str,
        instance_id: str,
        role_id: str,
        workspace_id: str,
        conversation_id: str,
        content: str,
    ) -> bool:
        if self._message_repo is None:
            return False
        return self._message_repo.append_user_prompt_if_missing(
            session_id=session_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_role_id=role_id,
            instance_id=instance_id,
            task_id=task_id,
            trace_id=trace_id,
            content=content,
        )

    def _enqueue(
        self,
        *,
        session_id: str,
        run_id: str,
        trace_id: str,
        task_id: Optional[str],
        instance_id: str,
        role_id: str,
        content: str,
        source: InjectionSource,
    ) -> Optional[InjectionMessage]:
        if not self._injection_manager.is_active(run_id):
            return None
        try:
            record = self._injection_manager.enqueue(
                run_id=run_id,
                recipient_instance_id=instance_id,
                source=source,
                content=content,
            )
        except KeyError:
            return None
        self._run_event_hub.publish(
            RunEvent(
                session_id=session_id,
                run_id=run_id,
                trace_id=trace_id,
                task_id=task_id,
                instance_id=instance_id,
                role_id=role_id,
                event_type=RunEventType.INJECTION_ENQUEUED,
                payload_json=record.model_dump_json(),
            )
        )
        return record
