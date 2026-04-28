from __future__ import annotations

from pathlib import Path
import json
from typing import cast

from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.reminders import render_system_reminder
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.enums import InjectionSource
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.sessions.runs.system_injection import SystemInjectionSink
from relay_teams.system_reminder_delivery import SystemReminderDeliveryMode


class _CapturingRunEventHub:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    def publish(self, event: RunEvent) -> None:
        self.events.append(event)


def test_append_and_enqueue_persists_message_and_queues_injection(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "messages.db"
    message_repo = MessageRepository(db_path)
    injection_manager = RunInjectionManager()
    injection_manager.activate("run-1")
    sink = SystemInjectionSink(
        injection_manager=injection_manager,
        run_event_hub=RunEventHub(),
        message_repo=message_repo,
    )

    result = sink.append_and_enqueue(
        session_id="session-1",
        run_id="run-1",
        trace_id="run-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="role-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        content="<system-reminder>\nCheck todos.\n</system-reminder>",
    )

    history = message_repo.get_history_for_conversation("conversation-1")
    injections = injection_manager.drain_at_boundary("run-1", "inst-1")
    assert result.appended is True
    assert result.enqueued is True
    assert len(history) == 1
    assert len(injections) == 1
    assert (
        injections[0].content == "<system-reminder>\nCheck todos.\n</system-reminder>"
    )


def test_append_and_enqueue_appends_even_when_run_is_inactive(tmp_path: Path) -> None:
    db_path = tmp_path / "messages.db"
    message_repo = MessageRepository(db_path)
    sink = SystemInjectionSink(
        injection_manager=RunInjectionManager(),
        run_event_hub=RunEventHub(),
        message_repo=message_repo,
    )

    result = sink.append_and_enqueue(
        session_id="session-1",
        run_id="run-1",
        trace_id="run-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="role-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        content="<system-reminder>\nCheck todos.\n</system-reminder>",
    )

    history = message_repo.get_history_for_conversation("conversation-1")
    assert result.appended is True
    assert result.enqueued is False
    assert len(history) == 1


def test_append_only_persists_without_queueing(tmp_path: Path) -> None:
    db_path = tmp_path / "messages.db"
    message_repo = MessageRepository(db_path)
    injection_manager = RunInjectionManager()
    injection_manager.activate("run-1")
    sink = SystemInjectionSink(
        injection_manager=injection_manager,
        run_event_hub=RunEventHub(),
        message_repo=message_repo,
    )

    result = sink.append_only(
        session_id="session-1",
        trace_id="run-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="role-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        content="<system-reminder>\nCheck todos.\n</system-reminder>",
    )

    history = message_repo.get_history_for_conversation("conversation-1")
    injections = injection_manager.drain_at_boundary("run-1", "inst-1")
    assert result.appended is True
    assert result.enqueued is False
    assert len(history) == 1
    assert injections == ()


def test_append_only_without_message_repo_reports_not_appended() -> None:
    sink = SystemInjectionSink(
        injection_manager=RunInjectionManager(),
        run_event_hub=RunEventHub(),
        message_repo=None,
    )

    result = sink.append_only(
        session_id="session-1",
        trace_id="run-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="role-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        content="<system-reminder>\nCheck todos.\n</system-reminder>",
    )

    assert result.appended is False
    assert result.enqueued is False


def test_enqueue_only_degrades_when_active_run_disappears() -> None:
    class _RaceyInjectionManager:
        def is_active(self, run_id: str) -> bool:
            _ = run_id
            return True

        def enqueue(
            self,
            *,
            run_id: str,
            recipient_instance_id: str,
            source: InjectionSource,
            content: object,
            visibility: str = "public",
            internal_kind: str = "",
            internal_delivery_mode: str = "",
            internal_issue_key: str = "",
        ) -> object:
            _ = (
                run_id,
                recipient_instance_id,
                source,
                content,
                visibility,
                internal_kind,
                internal_delivery_mode,
                internal_issue_key,
            )
            raise KeyError("run disappeared")

    sink = SystemInjectionSink(
        injection_manager=cast(RunInjectionManager, _RaceyInjectionManager()),
        run_event_hub=RunEventHub(),
        message_repo=None,
    )

    result = sink.enqueue_only(
        session_id="session-1",
        run_id="run-1",
        trace_id="run-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="role-1",
        content="<system-reminder>\nCheck todos.\n</system-reminder>",
    )

    assert result.enqueued is False


def test_enqueue_only_redacts_internal_system_reminder_events() -> None:
    injection_manager = RunInjectionManager()
    injection_manager.activate("run-1")
    event_hub = _CapturingRunEventHub()
    sink = SystemInjectionSink(
        injection_manager=injection_manager,
        run_event_hub=cast(RunEventHub, event_hub),
        message_repo=None,
    )

    result = sink.enqueue_only(
        session_id="session-1",
        run_id="run-1",
        trace_id="run-1",
        task_id="task-1",
        instance_id="inst-1",
        role_id="role-1",
        content=render_system_reminder("Do not leak this body."),
        visibility="internal",
        internal_kind="read_only_streak",
        internal_delivery_mode=SystemReminderDeliveryMode.GUIDANCE.value,
        internal_issue_key="read_only_streak",
    )

    payload = json.loads(event_hub.events[0].payload_json)
    assert result.enqueued is True
    assert payload["content_redacted"] is True
    assert payload["internal_kind"] == "read_only_streak"
    assert "<system-reminder>" not in event_hub.events[0].payload_json
    assert "Do not leak this body" not in event_hub.events[0].payload_json
