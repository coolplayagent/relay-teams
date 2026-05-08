# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from unittest.mock import MagicMock, create_autospec

from relay_teams.hooks import HookService
from relay_teams.memory.event_handler import MemoryEventHandler
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.run_hook_pipeline import RunHookPipeline
from relay_teams.sessions.session_repository import SessionRepository


class _FakeCaptureService:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    async def capture_all_for_session(
        self,
        *,
        _session_id: str,
        _workspace_id: str,
    ) -> tuple[object, ...]:
        self.calls.append((_session_id, _workspace_id))
        if self.fail:
            raise RuntimeError("capture error")
        return ()


def _make_pipeline(
    *,
    memory_event_handler: MemoryEventHandler | None = None,
    session_workspace_id: str | None = "ws-1",
) -> tuple[RunHookPipeline, MagicMock]:
    hook_service = create_autospec(HookService, instance=True)

    def get_hook_service() -> HookService | None:
        return hook_service

    session_repo = create_autospec(SessionRepository, instance=True)
    mock_session = MagicMock()
    mock_session.workspace_id = session_workspace_id
    if session_workspace_id is None:
        session_repo.get_async.side_effect = KeyError("missing session")
    else:
        session_repo.get_async.return_value = mock_session
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


def _run(coro: Coroutine[object, object, None]) -> None:
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
        handler.on_run_completed_async.assert_awaited_once_with(
            workspace_id="ws-1",
            session_id="sess-1",
            run_id="run-1",
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
        handler.on_session_completed_async.assert_awaited_once_with(
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
        handler.on_run_completed_async.assert_not_awaited()
        handler.on_session_completed_async.assert_not_awaited()

    def test_run_completed_exception_suppressed(self) -> None:
        handler = create_autospec(MemoryEventHandler, instance=True)
        handler.on_run_completed_async.side_effect = RuntimeError("db error")
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
        handler.on_session_completed_async.assert_awaited_once()

    def test_session_completed_exception_suppressed(self) -> None:
        handler = create_autospec(MemoryEventHandler, instance=True)
        handler.on_session_completed_async.side_effect = RuntimeError("db error")
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
        handler.on_run_completed_async.assert_awaited_once()


class TestRunHookPipelineTemporaryKnowledgeCapture:
    """Tests for RP-2 temporary role knowledge capture wired into execute_session_end_hooks."""

    def test_capture_service_called_when_set(self) -> None:
        handler = create_autospec(MemoryEventHandler, instance=True)
        pipeline, _ = _make_pipeline(memory_event_handler=handler)

        capture_svc = _FakeCaptureService()
        pipeline.set_temporary_knowledge_capture_service(capture_svc)

        _run(
            pipeline.execute_session_end_hooks(
                run_id="run-1",
                session_id="sess-1",
                status="completed",
                completion_reason="done",
                output_text="ok",
            )
        )
        assert capture_svc.calls == [("sess-1", "ws-1")]

    def test_capture_service_not_called_when_none(self) -> None:
        handler = create_autospec(MemoryEventHandler, instance=True)
        pipeline, _ = _make_pipeline(memory_event_handler=handler)
        # No capture service set — default is None
        _run(
            pipeline.execute_session_end_hooks(
                run_id="run-1",
                session_id="sess-1",
                status="completed",
                completion_reason="done",
                output_text="ok",
            )
        )
        # No assertion needed — just verify no error raised

    def test_capture_failure_suppressed(self) -> None:
        handler = create_autospec(MemoryEventHandler, instance=True)
        pipeline, _ = _make_pipeline(memory_event_handler=handler)

        capture_svc = _FakeCaptureService(fail=True)
        pipeline.set_temporary_knowledge_capture_service(capture_svc)

        _run(
            pipeline.execute_session_end_hooks(
                run_id="run-1",
                session_id="sess-1",
                status="completed",
                completion_reason="done",
                output_text="ok",
            )
        )
        # Should not raise — capture failure is suppressed
        assert capture_svc.calls == [("sess-1", "ws-1")]

    def test_resolve_workspace_id_returns_workspace(self) -> None:
        pipeline, _ = _make_pipeline(session_workspace_id="ws-42")
        result = asyncio.run(pipeline._resolve_workspace_id_async("sess-1"))
        assert result == "ws-42"

    def test_resolve_workspace_id_returns_none_when_no_session(self) -> None:
        pipeline, _ = _make_pipeline(session_workspace_id=None)
        result = asyncio.run(pipeline._resolve_workspace_id_async("sess-missing"))
        assert result is None
