# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any
from unittest.mock import MagicMock, create_autospec

from relay_teams.hooks import HookService
from relay_teams.memory.event_handler import MemoryEventHandler
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.run_hook_pipeline import RunHookPipeline
from relay_teams.sessions.session_repository import SessionRepository


def _make_pipeline(
    *,
    memory_event_handler: object | None = None,
    session_workspace_id: str | None = "ws-1",
) -> tuple[RunHookPipeline, MagicMock]:
    hook_service = create_autospec(HookService, instance=True)

    def get_hook_service() -> HookService | None:
        return hook_service

    session_repo = create_autospec(SessionRepository, instance=True)
    mock_session = MagicMock()
    mock_session.workspace_id = session_workspace_id
    session_repo.get.return_value = mock_session if session_workspace_id else None
    run_event_hub = create_autospec(RunEventHub, instance=True)
    append_followup = MagicMock(return_value=True)

    pipeline = RunHookPipeline(
        get_hook_service=get_hook_service,
        session_repo=session_repo,
        run_event_hub=run_event_hub,
        append_followup_to_coordinator=append_followup,
        memory_event_handler=memory_event_handler,
    )
    return pipeline, hook_service


def _run(coro: Coroutine[Any, Any, None]) -> None:
    asyncio.run(coro)


class TestRunHookPipelineMemoryConsolidation:
    """Tests for memory-bank lifecycle hooks wired into execute_session_end_hooks."""

    def test_no_memory_handler_no_error(self) -> None:
        pipeline, _ = _make_pipeline(memory_event_handler=None)
        _run(
            pipeline.execute_session_end_hooks(
                run_id="run-1",
                session_id="sess-1",
                status="completed",
                completion_reason="done",
                output_text="ok",
            )
        )

    def test_on_run_completed_called(self) -> None:
        handler = create_autospec(MemoryEventHandler, instance=True)
        pipeline, _ = _make_pipeline(memory_event_handler=handler)

        _run(
            pipeline.execute_session_end_hooks(
                run_id="run-1",
                session_id="sess-1",
                status="completed",
                completion_reason="done",
                output_text="ok",
            )
        )
        handler.on_run_completed.assert_called_once_with(
            workspace_id="ws-1",
            session_id="sess-1",
        )

    def test_on_session_completed_called(self) -> None:
        handler = create_autospec(MemoryEventHandler, instance=True)
        pipeline, _ = _make_pipeline(memory_event_handler=handler)

        _run(
            pipeline.execute_session_end_hooks(
                run_id="run-1",
                session_id="sess-1",
                status="completed",
                completion_reason="done",
                output_text="ok",
            )
        )
        handler.on_session_completed.assert_called_once_with(
            workspace_id="ws-1",
            session_id="sess-1",
        )

    def test_handler_skipped_when_no_workspace(self) -> None:
        handler = create_autospec(MemoryEventHandler, instance=True)
        pipeline, _ = _make_pipeline(
            memory_event_handler=handler,
            session_workspace_id=None,
        )

        _run(
            pipeline.execute_session_end_hooks(
                run_id="run-1",
                session_id="sess-1",
                status="completed",
                completion_reason="done",
                output_text="ok",
            )
        )
        handler.on_run_completed.assert_not_called()
        handler.on_session_completed.assert_not_called()

    def test_run_completed_exception_suppressed(self) -> None:
        handler = create_autospec(MemoryEventHandler, instance=True)
        handler.on_run_completed.side_effect = RuntimeError("db error")
        pipeline, _ = _make_pipeline(memory_event_handler=handler)

        _run(
            pipeline.execute_session_end_hooks(
                run_id="run-1",
                session_id="sess-1",
                status="completed",
                completion_reason="done",
                output_text="ok",
            )
        )
        # on_session_completed should still be called after failure
        handler.on_session_completed.assert_called_once()

    def test_session_completed_exception_suppressed(self) -> None:
        handler = create_autospec(MemoryEventHandler, instance=True)
        handler.on_session_completed.side_effect = RuntimeError("db error")
        pipeline, _ = _make_pipeline(memory_event_handler=handler)

        _run(
            pipeline.execute_session_end_hooks(
                run_id="run-1",
                session_id="sess-1",
                status="completed",
                completion_reason="done",
                output_text="ok",
            )
        )
        handler.on_run_completed.assert_called_once()

    def test_resolve_workspace_id_returns_workspace(self) -> None:
        pipeline, _ = _make_pipeline(session_workspace_id="ws-42")
        result = pipeline._resolve_workspace_id("sess-1")
        assert result == "ws-42"

    def test_resolve_workspace_id_returns_none_when_no_session(self) -> None:
        pipeline, _ = _make_pipeline(session_workspace_id=None)
        result = pipeline._resolve_workspace_id("sess-missing")
        assert result is None
