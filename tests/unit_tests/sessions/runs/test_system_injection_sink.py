from __future__ import annotations

from pathlib import Path

from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.system_injection import SystemInjectionSink


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
