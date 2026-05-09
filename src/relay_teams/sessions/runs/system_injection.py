from __future__ import annotations

from json import dumps, loads

from pydantic import BaseModel, ConfigDict, JsonValue

from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.media import user_prompt_content_to_text
from relay_teams.sessions.runs.enums import InjectionSource, RunEventType
from relay_teams.sessions.runs.event_stream import RunEventHub, publish_run_event_async
from relay_teams.sessions.runs.injection_classification import (
    INJECTION_CLASSIFIER,
    InjectionBoundaryContext,
    InjectionDisposition,
    public_injection_payload_json,
)
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
        message_repo: MessageRepository | None = None,
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
        task_id: str | None,
        instance_id: str,
        role_id: str,
        content: str,
        source: InjectionSource = InjectionSource.SYSTEM,
        visibility: str = "public",
        internal_kind: str = "",
        internal_delivery_mode: str = "",
        internal_issue_key: str = "",
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
            visibility=visibility,
            internal_kind=internal_kind,
            internal_delivery_mode=internal_delivery_mode,
            internal_issue_key=internal_issue_key,
        )
        return SystemInjectionResult(enqueued=record is not None)

    async def enqueue_only_async(
        self,
        *,
        session_id: str,
        run_id: str,
        trace_id: str,
        task_id: str | None,
        instance_id: str,
        role_id: str,
        content: str,
        source: InjectionSource = InjectionSource.SYSTEM,
        visibility: str = "public",
        internal_kind: str = "",
        internal_delivery_mode: str = "",
        internal_issue_key: str = "",
    ) -> SystemInjectionResult:
        record = await self._enqueue_async(
            session_id=session_id,
            run_id=run_id,
            trace_id=trace_id,
            task_id=task_id,
            instance_id=instance_id,
            role_id=role_id,
            content=content,
            source=source,
            visibility=visibility,
            internal_kind=internal_kind,
            internal_delivery_mode=internal_delivery_mode,
            internal_issue_key=internal_issue_key,
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
        visibility: str = "public",
        internal_kind: str = "",
        internal_delivery_mode: str = "",
        internal_issue_key: str = "",
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
            visibility=visibility,
            internal_kind=internal_kind,
            internal_delivery_mode=internal_delivery_mode,
            internal_issue_key=internal_issue_key,
        )
        return SystemInjectionResult(appended=appended, enqueued=record is not None)

    async def append_and_enqueue_async(
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
        visibility: str = "public",
        internal_kind: str = "",
        internal_delivery_mode: str = "",
        internal_issue_key: str = "",
    ) -> SystemInjectionResult:
        appended = await self._append_async(
            session_id=session_id,
            trace_id=trace_id,
            task_id=task_id,
            instance_id=instance_id,
            role_id=role_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            content=content,
        )
        record = await self._enqueue_async(
            session_id=session_id,
            run_id=run_id,
            trace_id=trace_id,
            task_id=task_id,
            instance_id=instance_id,
            role_id=role_id,
            content=content,
            source=source,
            visibility=visibility,
            internal_kind=internal_kind,
            internal_delivery_mode=internal_delivery_mode,
            internal_issue_key=internal_issue_key,
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

    async def append_only_async(
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
        appended = await self._append_async(
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

    async def _append_async(
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
        return await self._message_repo.append_user_prompt_if_missing_async(
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
        task_id: str | None,
        instance_id: str,
        role_id: str,
        content: str,
        source: InjectionSource,
        visibility: str,
        internal_kind: str,
        internal_delivery_mode: str,
        internal_issue_key: str,
    ) -> InjectionMessage | None:
        if not self._injection_manager.is_active(run_id):
            return None
        try:
            record = self._injection_manager.enqueue(
                run_id=run_id,
                recipient_instance_id=instance_id,
                source=source,
                content=content,
                visibility=visibility,
                internal_kind=internal_kind,
                internal_delivery_mode=internal_delivery_mode,
                internal_issue_key=internal_issue_key,
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
                payload_json=public_injection_payload_json(record),
            )
        )
        return record

    async def _enqueue_async(
        self,
        *,
        session_id: str,
        run_id: str,
        trace_id: str,
        task_id: str | None,
        instance_id: str,
        role_id: str,
        content: str,
        source: InjectionSource,
        visibility: str,
        internal_kind: str,
        internal_delivery_mode: str,
        internal_issue_key: str,
    ) -> InjectionMessage | None:
        if not self._injection_manager.is_active(run_id):
            return None
        try:
            record = self._injection_manager.enqueue(
                run_id=run_id,
                recipient_instance_id=instance_id,
                source=source,
                content=content,
                visibility=visibility,
                internal_kind=internal_kind,
                internal_delivery_mode=internal_delivery_mode,
                internal_issue_key=internal_issue_key,
            )
        except KeyError:
            return None
        await publish_run_event_async(
            self._run_event_hub,
            RunEvent(
                session_id=session_id,
                run_id=run_id,
                trace_id=trace_id,
                task_id=task_id,
                instance_id=instance_id,
                role_id=role_id,
                event_type=RunEventType.INJECTION_ENQUEUED,
                payload_json=public_injection_payload_json(record),
            ),
        )
        return record


class AppliedSystemInjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    messages: tuple[InjectionMessage, ...] = ()
    content: tuple[str, ...] = ()

    @property
    def applied(self) -> bool:
        return bool(self.messages)


class SystemInjectionConsumer:
    def __init__(
        self,
        *,
        injection_manager: RunInjectionManager,
        run_event_hub: RunEventHub,
        message_repo: MessageRepository | None = None,
    ) -> None:
        self._injection_manager = injection_manager
        self._run_event_hub = run_event_hub
        self._message_repo = message_repo

    async def apply_startup_system_reminders_async(
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
        restart_scope: str,
    ) -> AppliedSystemInjection:
        messages = self._injection_manager.drain_system_reminders_at_start(
            run_id,
            instance_id,
        )
        return await self._apply_messages_async(
            messages=messages,
            session_id=session_id,
            run_id=run_id,
            trace_id=trace_id,
            task_id=task_id,
            instance_id=instance_id,
            role_id=role_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            restart_scope=restart_scope,
            final_answer_ready=False,
        )

    async def apply_boundary_system_reminders_async(
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
        restart_scope: str,
        final_answer_ready: bool = False,
    ) -> AppliedSystemInjection:
        messages = self._injection_manager.drain_system_reminders_at_boundary(
            run_id,
            instance_id,
        )
        return await self._apply_messages_async(
            messages=messages,
            session_id=session_id,
            run_id=run_id,
            trace_id=trace_id,
            task_id=task_id,
            instance_id=instance_id,
            role_id=role_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            restart_scope=restart_scope,
            final_answer_ready=final_answer_ready,
        )

    async def _apply_messages_async(
        self,
        *,
        messages: tuple[InjectionMessage, ...],
        session_id: str,
        run_id: str,
        trace_id: str,
        task_id: str,
        instance_id: str,
        role_id: str,
        workspace_id: str,
        conversation_id: str,
        restart_scope: str,
        final_answer_ready: bool,
    ) -> AppliedSystemInjection:
        if not messages:
            return AppliedSystemInjection()
        boundary_context = InjectionBoundaryContext(
            final_answer_ready=final_answer_ready,
        )
        applied = tuple(
            message
            for message in messages
            if INJECTION_CLASSIFIER.disposition(
                message,
                context=boundary_context,
            )
            == InjectionDisposition.APPLY
        )
        if not applied:
            return AppliedSystemInjection()
        content: list[str] = []
        for message in applied:
            await publish_run_event_async(
                self._run_event_hub,
                RunEvent(
                    session_id=session_id,
                    run_id=run_id,
                    trace_id=trace_id,
                    task_id=task_id,
                    instance_id=instance_id,
                    role_id=role_id,
                    event_type=RunEventType.INJECTION_APPLIED,
                    payload_json=_applied_injection_payload_json(
                        message,
                        restart_scope=restart_scope,
                    ),
                ),
            )
            if self._message_repo is not None:
                _ = await self._message_repo.append_user_prompt_if_missing_async(
                    session_id=session_id,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    agent_role_id=role_id,
                    instance_id=instance_id,
                    task_id=task_id,
                    trace_id=trace_id,
                    content=message.content,
                )
            text = user_prompt_content_to_text(message.content).strip()
            if text:
                content.append(text)
        return AppliedSystemInjection(messages=applied, content=tuple(content))


def _applied_injection_payload_json(
    message: InjectionMessage,
    *,
    restart_scope: str,
) -> str:
    raw_payload = loads(public_injection_payload_json(message))
    payload = (
        {str(key): value for key, value in raw_payload.items()}
        if isinstance(raw_payload, dict)
        else {}
    )
    payload["interrupted_current_step"] = False
    payload["restart_scope"] = restart_scope
    payload["supersedes_pending_tool_calls"] = False
    payload["applied_injection_ids"] = [message.injection_id]
    return dumps(_json_payload(payload), ensure_ascii=False)


def _json_payload(payload: dict[str, object]) -> dict[str, JsonValue]:
    return {
        key: value
        for key, value in payload.items()
        if isinstance(value, str | int | float | bool | list | dict) or value is None
    }
