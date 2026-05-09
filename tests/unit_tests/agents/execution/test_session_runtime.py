# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections.abc import Sequence
from types import SimpleNamespace, TracebackType
from typing import cast

import pytest
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    SystemPromptPart,
    ToolCallPart,
    ToolReturnPart,
)

from relay_teams.agents.execution import session_runtime as session_runtime_module
from relay_teams.agents.tasks.models import (
    SpecCheckpointPolicy,
    TaskEnvelope,
    TaskLifecyclePolicy,
    TaskRecord,
    TaskSpec,
    TaskSpecArtifact,
    VerificationPlan,
)
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.tools.registry import ToolRegistry
from .agent_llm_session_test_support import (
    AgentLlmSession,
    APIStatusError,
    BinaryContent,
    LlmRetryConfig,
    McpRegistry,
    MessageRepository,
    ModelCapabilities,
    ModelEndpointConfig,
    ModelModalityMatrix,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
    _FakeMessageRepo,
    _build_request,
    httpx,
)


async def _none_tool_approval_policy_async(_run_id: str) -> object:
    return cast(object, None)


async def _none_workspace_async(_self: object, **_kwargs: object) -> object:
    return cast(object, None)


class _OpenAIRawStreamWithoutFinish:
    finish_reason = None


class _OpenAIRawStreamWithFinish:
    finish_reason = "stop"


class _ForeignRawStream:
    finish_reason = None


class _SlowStreamContext:
    async def __aenter__(self) -> session_runtime_module.AgentNodeStream:
        await asyncio.sleep(0.05)
        return cast(session_runtime_module.AgentNodeStream, object())

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        _ = (exc_type, exc, traceback)
        return None


class _ClosingStreamContext:
    def __init__(self) -> None:
        self.exited = False

    async def __aenter__(self) -> session_runtime_module.AgentNodeStream:
        return cast(session_runtime_module.AgentNodeStream, object())

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        _ = (exc_type, exc, traceback)
        self.exited = True
        return None


class _LateOpeningStreamContext:
    def __init__(self, *, delay_seconds: float = 0.01) -> None:
        self._delay_seconds = delay_seconds
        self.exited = asyncio.Event()
        self.exit_exc_type: object = None

    async def __aenter__(self) -> session_runtime_module.AgentNodeStream:
        await asyncio.sleep(self._delay_seconds)
        return cast(session_runtime_module.AgentNodeStream, object())

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        _ = (exc, traceback)
        self.exit_exc_type = exc_type
        self.exited.set()
        return None


class _ControlledOpeningStreamContext:
    def __init__(self) -> None:
        self.enter_started = asyncio.Event()
        self.allow_enter = asyncio.Event()
        self.exited = asyncio.Event()
        self.exit_exc_type: object = None

    async def __aenter__(self) -> session_runtime_module.AgentNodeStream:
        self.enter_started.set()
        await self.allow_enter.wait()
        return cast(session_runtime_module.AgentNodeStream, object())

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        _ = (exc, traceback)
        self.exit_exc_type = exc_type
        self.exited.set()
        return None


class _LateFailingStreamContext:
    def __init__(self, *, delay_seconds: float = 0.01) -> None:
        self._delay_seconds = delay_seconds
        self.failed = asyncio.Event()
        self.exited = False

    async def __aenter__(self) -> session_runtime_module.AgentNodeStream:
        await asyncio.sleep(self._delay_seconds)
        self.failed.set()
        raise RuntimeError("late stream open failed")

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        _ = (exc_type, exc, traceback)
        self.exited = True
        return None


class _NeverOpeningStreamContext:
    def __init__(self) -> None:
        self.enter_started = asyncio.Event()
        self.enter_cancelled = asyncio.Event()
        self._enter_complete = asyncio.Event()
        self.exited = False

    async def __aenter__(self) -> session_runtime_module.AgentNodeStream:
        self.enter_started.set()
        try:
            await self._enter_complete.wait()
        except asyncio.CancelledError:
            self.enter_cancelled.set()
            raise
        return cast(session_runtime_module.AgentNodeStream, object())

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        _ = (exc_type, exc, traceback)
        self.exited = True
        return None


class _CancellationSuppressingLateOpeningStreamContext:
    def __init__(self) -> None:
        self.enter_started = asyncio.Event()
        self.enter_cancelled = asyncio.Event()
        self.allow_late_open = asyncio.Event()
        self.exited = asyncio.Event()
        self.exit_exc_type: object = None
        self._enter_complete = asyncio.Event()

    async def __aenter__(self) -> session_runtime_module.AgentNodeStream:
        self.enter_started.set()
        try:
            await self._enter_complete.wait()
        except asyncio.CancelledError:
            self.enter_cancelled.set()
            await self.allow_late_open.wait()
        return cast(session_runtime_module.AgentNodeStream, object())

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        _ = (exc, traceback)
        self.exit_exc_type = exc_type
        self.exited.set()
        return None


class _CancellationSuppressingFailingOpenContext:
    def __init__(self) -> None:
        self.enter_started = asyncio.Event()
        self.enter_cancelled = asyncio.Event()
        self.failed = asyncio.Event()
        self._enter_complete = asyncio.Event()

    async def __aenter__(self) -> session_runtime_module.AgentNodeStream:
        self.enter_started.set()
        try:
            await self._enter_complete.wait()
        except asyncio.CancelledError:
            self.enter_cancelled.set()
            self.failed.set()
            raise RuntimeError("stream open failed after cancellation")
        return cast(session_runtime_module.AgentNodeStream, object())

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        _ = (exc_type, exc, traceback)
        return None


class _ExitCancellingStreamContext:
    def __init__(self) -> None:
        self.exit_called = False

    async def __aenter__(self) -> session_runtime_module.AgentNodeStream:
        return cast(session_runtime_module.AgentNodeStream, object())

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        _ = (exc_type, exc, traceback)
        self.exit_called = True
        raise asyncio.CancelledError()


class _ExitFailingStreamContext:
    def __init__(self) -> None:
        self.exit_called = False

    async def __aenter__(self) -> session_runtime_module.AgentNodeStream:
        return cast(session_runtime_module.AgentNodeStream, object())

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        _ = (exc_type, exc, traceback)
        self.exit_called = True
        raise RuntimeError("stream exit failed")


class _ExitHangingStreamContext:
    def __init__(self) -> None:
        self.exit_started = asyncio.Event()
        self.exit_cancelled = asyncio.Event()
        self._exit_complete = asyncio.Event()

    async def __aenter__(self) -> session_runtime_module.AgentNodeStream:
        return cast(session_runtime_module.AgentNodeStream, object())

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        _ = (exc_type, exc, traceback)
        self.exit_started.set()
        try:
            await self._exit_complete.wait()
        except asyncio.CancelledError:
            self.exit_cancelled.set()
            raise
        return None


class _ExitCancellationSuppressingFailingStreamContext:
    def __init__(self) -> None:
        self.exit_started = asyncio.Event()
        self.exit_cancelled = asyncio.Event()
        self.failed = asyncio.Event()
        self._exit_complete = asyncio.Event()

    async def __aenter__(self) -> session_runtime_module.AgentNodeStream:
        return cast(session_runtime_module.AgentNodeStream, object())

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        _ = (exc_type, exc, traceback)
        self.exit_started.set()
        try:
            await self._exit_complete.wait()
        except asyncio.CancelledError:
            self.exit_cancelled.set()
            self.failed.set()
            raise RuntimeError("stream exit failed after cancellation")
        return None


async def _opened_stream() -> session_runtime_module.AgentNodeStream:
    return cast(session_runtime_module.AgentNodeStream, object())


async def _slow_items() -> Sequence[object]:
    await asyncio.sleep(0.05)
    return (object(),)


async def _wait_for_abandoned_stream_context_cleanup() -> None:
    cleanup_tasks = tuple(
        session_runtime_module._ABANDONED_LLM_STREAM_CONTEXT_CLEANUP_TASKS
    )
    if cleanup_tasks:
        await asyncio.wait_for(
            asyncio.gather(*cleanup_tasks),
            timeout=1.0,
        )


@pytest.mark.asyncio
async def test_spec_checkpoint_decision_reads_task_spec_from_runtime_repo() -> None:
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Implement API",
        verification=VerificationPlan(),
        spec=TaskSpec(requirements=("keep the route stable",)),
        lifecycle=TaskLifecyclePolicy(
            spec_checkpoint=SpecCheckpointPolicy(
                refresh_interval_tool_calls=1,
                refresh_interval_messages=99,
                refresh_interval_history_tokens=999_999,
            )
        ),
    )

    class _TaskRepo:
        async def get_async(self, task_id: str) -> TaskRecord:
            assert task_id == "task-1"
            return TaskRecord(envelope=task)

        async def get_spec_artifact_async(self, artifact_id: str) -> TaskSpecArtifact:
            raise AssertionError(artifact_id)

    class _RoleRegistry:
        def is_coordinator_role(self, role_id: str) -> bool:
            assert role_id == "Crafter"
            return False

    decision = await session_runtime_module._build_spec_checkpoint_decision_async(
        task_repo=_TaskRepo(),
        role_registry=_RoleRegistry(),
        request=_build_request(user_prompt=None).model_copy(
            update={"role_id": "Crafter", "task_id": "task-1"}
        ),
        history=[
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="shell",
                        tool_call_id="call-1",
                        content="ok",
                    )
                ]
            )
        ],
    )

    assert decision.should_inject is True
    assert "keep the route stable" in decision.content


def test_raise_if_stream_finished_without_reason_validates_provider_streams() -> None:
    session_runtime_module._raise_if_stream_finished_without_reason(object())
    session_runtime_module._raise_if_stream_finished_without_reason(
        SimpleNamespace(_raw_stream_response=_ForeignRawStream())
    )
    session_runtime_module._raise_if_stream_finished_without_reason(
        SimpleNamespace(_raw_stream_response=_OpenAIRawStreamWithFinish())
    )

    with pytest.raises(httpx.RemoteProtocolError):
        session_runtime_module._raise_if_stream_finished_without_reason(
            SimpleNamespace(_raw_stream_response=_OpenAIRawStreamWithoutFinish())
        )


def test_llm_stream_event_timeout_has_no_hard_upper_cap() -> None:
    assert session_runtime_module._llm_stream_event_timeout_seconds(1.0) == 5.0
    assert session_runtime_module._llm_stream_event_timeout_seconds(60.0) == 120.0


@pytest.mark.asyncio
async def test_llm_stream_timeout_helpers_raise_read_timeout() -> None:
    async def slow_generator():
        _ = await _slow_items()
        yield object()

    yielded = False
    with pytest.raises(httpx.ReadTimeout):
        async for _item in session_runtime_module._aiter_with_timeout(
            slow_generator(),
            timeout_seconds=0.001,
        ):
            yielded = True
    assert yielded is False

    entered = False
    with pytest.raises(httpx.ReadTimeout):
        async with session_runtime_module._llm_stream_context_with_timeout(
            cast(session_runtime_module.AgentNodeStreamContext, _SlowStreamContext()),
            timeout_seconds=0.001,
        ):
            entered = True
    assert entered is False


@pytest.mark.asyncio
async def test_llm_stream_context_timeout_closes_late_opened_context() -> None:
    context = _LateOpeningStreamContext()
    entered = False

    async def open_context() -> None:
        nonlocal entered
        async with session_runtime_module._llm_stream_context_with_timeout(
            cast(session_runtime_module.AgentNodeStreamContext, context),
            timeout_seconds=0.001,
        ):
            entered = True

    task = asyncio.create_task(open_context())
    await asyncio.wait_for(context.exited.wait(), timeout=1.0)
    await _wait_for_abandoned_stream_context_cleanup()
    results = await asyncio.gather(task, return_exceptions=True)

    assert len(results) == 1
    assert isinstance(results[0], httpx.ReadTimeout)
    assert entered is False
    assert context.exit_exc_type is asyncio.CancelledError


@pytest.mark.asyncio
async def test_llm_stream_context_cancellation_closes_late_opened_context() -> None:
    context = _ControlledOpeningStreamContext()
    entered = False

    async def open_context() -> None:
        nonlocal entered
        async with session_runtime_module._llm_stream_context_with_timeout(
            cast(session_runtime_module.AgentNodeStreamContext, context),
            timeout_seconds=1.0,
        ):
            entered = True

    task = asyncio.create_task(open_context())
    await asyncio.wait_for(context.enter_started.wait(), timeout=1.0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await task

    context.allow_enter.set()
    await asyncio.wait_for(context.exited.wait(), timeout=1.0)
    await _wait_for_abandoned_stream_context_cleanup()

    assert entered is False
    assert context.exit_exc_type is asyncio.CancelledError


@pytest.mark.asyncio
async def test_llm_stream_context_timeout_observes_late_open_failure() -> None:
    context = _LateFailingStreamContext()
    entered = False

    async def open_context() -> None:
        nonlocal entered
        async with session_runtime_module._llm_stream_context_with_timeout(
            cast(session_runtime_module.AgentNodeStreamContext, context),
            timeout_seconds=0.001,
        ):
            entered = True

    task = asyncio.create_task(open_context())
    await asyncio.wait_for(context.failed.wait(), timeout=1.0)
    await _wait_for_abandoned_stream_context_cleanup()
    results = await asyncio.gather(task, return_exceptions=True)

    assert len(results) == 1
    assert isinstance(results[0], httpx.ReadTimeout)
    assert entered is False
    assert context.exited is False


@pytest.mark.asyncio
async def test_abandoned_stream_cleanup_finish_logs_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_events: list[str] = []
    cleanup_can_finish = asyncio.Event()

    def fake_log_event(
        _logger: object,
        _level: int,
        *,
        event: str,
        message: str,
        payload: dict[str, object] | None = None,
        duration_ms: int | None = None,
        exc_info: object = None,
    ) -> None:
        _ = (message, payload, duration_ms, exc_info)
        captured_events.append(event)

    async def hanging_cleanup(
        *,
        context: session_runtime_module.AgentNodeStreamContext,
        enter_task: asyncio.Task[session_runtime_module.AgentNodeStream],
        reason: str,
        cleanup_timeout_seconds: float,
    ) -> None:
        _ = (context, enter_task, reason, cleanup_timeout_seconds)
        await cleanup_can_finish.wait()

    monkeypatch.setattr(session_runtime_module, "log_event", fake_log_event)
    monkeypatch.setattr(
        session_runtime_module,
        "_cleanup_abandoned_llm_stream_context",
        hanging_cleanup,
    )

    context = _ClosingStreamContext()
    enter_task = asyncio.create_task(_opened_stream())
    session_runtime_module._schedule_abandoned_llm_stream_context_cleanup(
        context=cast(session_runtime_module.AgentNodeStreamContext, context),
        enter_task=enter_task,
        reason="test_cancelled_cleanup",
        cleanup_timeout_seconds=1.0,
    )

    cleanup_tasks = tuple(
        session_runtime_module._ABANDONED_LLM_STREAM_CONTEXT_CLEANUP_TASKS
    )
    assert len(cleanup_tasks) == 1
    cleanup_tasks[0].cancel()
    await asyncio.gather(*cleanup_tasks, return_exceptions=True)
    _ = await enter_task

    assert "llm.stream_context.cleanup.cancelled" in captured_events
    assert not session_runtime_module._ABANDONED_LLM_STREAM_CONTEXT_CLEANUP_TASKS


@pytest.mark.asyncio
async def test_abandoned_stream_cleanup_finish_logs_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_events: list[str] = []

    def fake_log_event(
        _logger: object,
        _level: int,
        *,
        event: str,
        message: str,
        payload: dict[str, object] | None = None,
        duration_ms: int | None = None,
        exc_info: object = None,
    ) -> None:
        _ = (message, payload, duration_ms, exc_info)
        captured_events.append(event)

    async def failing_cleanup(
        *,
        context: session_runtime_module.AgentNodeStreamContext,
        enter_task: asyncio.Task[session_runtime_module.AgentNodeStream],
        reason: str,
        cleanup_timeout_seconds: float,
    ) -> None:
        _ = (context, enter_task, reason, cleanup_timeout_seconds)
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(session_runtime_module, "log_event", fake_log_event)
    monkeypatch.setattr(
        session_runtime_module,
        "_cleanup_abandoned_llm_stream_context",
        failing_cleanup,
    )

    context = _ClosingStreamContext()
    enter_task = asyncio.create_task(_opened_stream())
    session_runtime_module._schedule_abandoned_llm_stream_context_cleanup(
        context=cast(session_runtime_module.AgentNodeStreamContext, context),
        enter_task=enter_task,
        reason="test_failed_cleanup",
        cleanup_timeout_seconds=1.0,
    )

    cleanup_tasks = tuple(
        session_runtime_module._ABANDONED_LLM_STREAM_CONTEXT_CLEANUP_TASKS
    )
    await asyncio.gather(*cleanup_tasks, return_exceptions=True)
    _ = await enter_task

    assert "llm.stream_context.cleanup.failed" in captured_events
    assert not session_runtime_module._ABANDONED_LLM_STREAM_CONTEXT_CLEANUP_TASKS


@pytest.mark.asyncio
async def test_abandoned_stream_cleanup_ignores_exit_cancellation() -> None:
    context = _ExitCancellingStreamContext()
    enter_task = asyncio.create_task(_opened_stream())

    await session_runtime_module._cleanup_abandoned_llm_stream_context(
        context=cast(session_runtime_module.AgentNodeStreamContext, context),
        enter_task=enter_task,
        reason="test_exit_cancelled",
        cleanup_timeout_seconds=1.0,
    )

    assert context.exit_called is True


@pytest.mark.asyncio
async def test_abandoned_stream_cleanup_logs_exit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_events: list[str] = []

    def fake_log_event(
        _logger: object,
        _level: int,
        *,
        event: str,
        message: str,
        payload: dict[str, object] | None = None,
        duration_ms: int | None = None,
        exc_info: object = None,
    ) -> None:
        _ = (message, payload, duration_ms, exc_info)
        captured_events.append(event)

    monkeypatch.setattr(session_runtime_module, "log_event", fake_log_event)

    context = _ExitFailingStreamContext()
    enter_task = asyncio.create_task(_opened_stream())

    await session_runtime_module._cleanup_abandoned_llm_stream_context(
        context=cast(session_runtime_module.AgentNodeStreamContext, context),
        enter_task=enter_task,
        reason="test_exit_failed",
        cleanup_timeout_seconds=1.0,
    )

    assert context.exit_called is True
    assert captured_events == ["llm.stream_context.exit_failed"]


@pytest.mark.asyncio
async def test_abandoned_stream_cleanup_cancels_wedged_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_events: list[str] = []

    def fake_log_event(
        _logger: object,
        _level: int,
        *,
        event: str,
        message: str,
        payload: dict[str, object] | None = None,
        duration_ms: int | None = None,
        exc_info: object = None,
    ) -> None:
        _ = (message, payload, duration_ms, exc_info)
        captured_events.append(event)

    monkeypatch.setattr(session_runtime_module, "log_event", fake_log_event)

    context = _NeverOpeningStreamContext()
    enter_task = asyncio.create_task(context.__aenter__())
    await asyncio.wait_for(context.enter_started.wait(), timeout=1.0)

    await session_runtime_module._cleanup_abandoned_llm_stream_context(
        context=cast(session_runtime_module.AgentNodeStreamContext, context),
        enter_task=enter_task,
        reason="test_open_cleanup_timeout",
        cleanup_timeout_seconds=0.001,
    )
    await asyncio.wait_for(context.enter_cancelled.wait(), timeout=1.0)
    await asyncio.gather(enter_task, return_exceptions=True)

    assert context.exited is False
    assert captured_events == ["llm.stream_context.open.cleanup_timeout"]


@pytest.mark.asyncio
async def test_abandoned_stream_cleanup_closes_late_open_after_timeout() -> None:
    context = _CancellationSuppressingLateOpeningStreamContext()
    enter_task = asyncio.create_task(context.__aenter__())
    await asyncio.wait_for(context.enter_started.wait(), timeout=1.0)

    await session_runtime_module._cleanup_abandoned_llm_stream_context(
        context=cast(session_runtime_module.AgentNodeStreamContext, context),
        enter_task=enter_task,
        reason="test_late_open_after_cleanup_timeout",
        cleanup_timeout_seconds=0.001,
    )

    await asyncio.wait_for(context.enter_cancelled.wait(), timeout=1.0)
    context.allow_late_open.set()
    await asyncio.wait_for(context.exited.wait(), timeout=1.0)
    await _wait_for_abandoned_stream_context_cleanup()

    assert context.exit_exc_type is asyncio.CancelledError
    assert not session_runtime_module._ABANDONED_LLM_STREAM_CONTEXT_CLEANUP_TASKS


@pytest.mark.asyncio
async def test_abandoned_stream_cleanup_observes_late_open_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_events: list[str] = []

    def fake_log_event(
        _logger: object,
        _level: int,
        *,
        event: str,
        message: str,
        payload: dict[str, object] | None = None,
        duration_ms: int | None = None,
        exc_info: object = None,
    ) -> None:
        _ = (message, payload, duration_ms, exc_info)
        captured_events.append(event)

    monkeypatch.setattr(session_runtime_module, "log_event", fake_log_event)

    context = _CancellationSuppressingFailingOpenContext()
    enter_task = asyncio.create_task(context.__aenter__())
    await asyncio.wait_for(context.enter_started.wait(), timeout=1.0)

    await session_runtime_module._cleanup_abandoned_llm_stream_context(
        context=cast(session_runtime_module.AgentNodeStreamContext, context),
        enter_task=enter_task,
        reason="test_late_open_failure_after_cleanup_timeout",
        cleanup_timeout_seconds=0.001,
    )
    await asyncio.wait_for(context.enter_cancelled.wait(), timeout=1.0)
    await asyncio.wait_for(context.failed.wait(), timeout=1.0)
    await asyncio.sleep(0)

    assert captured_events == [
        "llm.stream_context.open.cleanup_timeout",
        "llm.stream_context.open.failed_after_cleanup_timeout",
    ]


@pytest.mark.asyncio
async def test_abandoned_stream_cleanup_cancels_wedged_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_events: list[str] = []

    def fake_log_event(
        _logger: object,
        _level: int,
        *,
        event: str,
        message: str,
        payload: dict[str, object] | None = None,
        duration_ms: int | None = None,
        exc_info: object = None,
    ) -> None:
        _ = (message, payload, duration_ms, exc_info)
        captured_events.append(event)

    monkeypatch.setattr(session_runtime_module, "log_event", fake_log_event)

    context = _ExitHangingStreamContext()
    enter_task = asyncio.create_task(_opened_stream())

    await session_runtime_module._cleanup_abandoned_llm_stream_context(
        context=cast(session_runtime_module.AgentNodeStreamContext, context),
        enter_task=enter_task,
        reason="test_exit_cleanup_timeout",
        cleanup_timeout_seconds=0.001,
    )
    await asyncio.wait_for(context.exit_started.wait(), timeout=1.0)
    await asyncio.wait_for(context.exit_cancelled.wait(), timeout=1.0)

    assert captured_events == ["llm.stream_context.exit.cleanup_timeout"]


@pytest.mark.asyncio
async def test_abandoned_stream_cleanup_observes_late_exit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_events: list[str] = []

    def fake_log_event(
        _logger: object,
        _level: int,
        *,
        event: str,
        message: str,
        payload: dict[str, object] | None = None,
        duration_ms: int | None = None,
        exc_info: object = None,
    ) -> None:
        _ = (message, payload, duration_ms, exc_info)
        captured_events.append(event)

    monkeypatch.setattr(session_runtime_module, "log_event", fake_log_event)

    context = _ExitCancellationSuppressingFailingStreamContext()
    enter_task = asyncio.create_task(_opened_stream())

    await session_runtime_module._cleanup_abandoned_llm_stream_context(
        context=cast(session_runtime_module.AgentNodeStreamContext, context),
        enter_task=enter_task,
        reason="test_late_exit_failure_after_cleanup_timeout",
        cleanup_timeout_seconds=0.001,
    )
    await asyncio.wait_for(context.exit_started.wait(), timeout=1.0)
    await asyncio.wait_for(context.exit_cancelled.wait(), timeout=1.0)
    await asyncio.wait_for(context.failed.wait(), timeout=1.0)
    await asyncio.sleep(0)

    assert captured_events == [
        "llm.stream_context.exit.cleanup_timeout",
        "llm.stream_context.exit_failed_after_cleanup_timeout",
    ]


@pytest.mark.asyncio
async def test_llm_stream_context_closes_entered_context() -> None:
    context = _ClosingStreamContext()

    async with session_runtime_module._llm_stream_context_with_timeout(
        cast(session_runtime_module.AgentNodeStreamContext, context),
        timeout_seconds=1.0,
    ):
        assert context.exited is False

    assert context.exited is True


@pytest.mark.asyncio
async def test_spec_checkpoint_decision_skips_coordinator_roles() -> None:
    class _TaskRepo:
        async def get_async(self, task_id: str) -> TaskRecord:
            raise AssertionError(task_id)

        async def get_spec_artifact_async(self, artifact_id: str) -> TaskSpecArtifact:
            raise AssertionError(artifact_id)

    class _RoleRegistry:
        def is_coordinator_role(self, role_id: str) -> bool:
            assert role_id == "Coordinator"
            return True

    decision = await session_runtime_module._build_spec_checkpoint_decision_async(
        task_repo=_TaskRepo(),
        role_registry=_RoleRegistry(),
        request=_build_request(user_prompt=None).model_copy(
            update={"role_id": "Coordinator", "task_id": "task-1"}
        ),
        history=[],
    )

    assert decision.should_inject is False


@pytest.mark.asyncio
async def test_spec_checkpoint_decision_ignores_missing_task_record() -> None:
    class _TaskRepo:
        async def get_async(self, task_id: str) -> TaskRecord:
            assert task_id == "missing-task"
            raise KeyError(task_id)

        async def get_spec_artifact_async(self, artifact_id: str) -> TaskSpecArtifact:
            raise AssertionError(artifact_id)

    class _RoleRegistry:
        def is_coordinator_role(self, role_id: str) -> bool:
            assert role_id == "Crafter"
            raise KeyError(role_id)

    decision = await session_runtime_module._build_spec_checkpoint_decision_async(
        task_repo=_TaskRepo(),
        role_registry=_RoleRegistry(),
        request=_build_request(user_prompt=None).model_copy(
            update={"role_id": "Crafter", "task_id": "missing-task"}
        ),
        history=[],
    )

    assert decision.should_inject is False


def test_spec_checkpoint_event_payload_contains_refresh_counters() -> None:
    request = _build_request(user_prompt=None).model_copy(
        update={
            "role_id": "Crafter",
            "instance_id": "inst-1",
            "task_id": "task-1",
        }
    )
    decision = session_runtime_module.SpecCheckpointDecision(
        sequence=2,
        reason="messages>=2",
        tool_calls_since_last_checkpoint=1,
        messages_since_last_checkpoint=2,
        history_tokens_since_last_checkpoint=3,
    )

    payload = session_runtime_module._spec_checkpoint_event_payload(
        decision=decision,
        request=request,
    )

    assert payload == {
        "role_id": "Crafter",
        "instance_id": "inst-1",
        "task_id": "task-1",
        "sequence": 2,
        "reason": "messages>=2",
        "tool_calls_since_last_checkpoint": 1,
        "messages_since_last_checkpoint": 2,
        "history_tokens_since_last_checkpoint": 3,
    }


@pytest.mark.asyncio
async def test_generate_async_persists_only_provider_canonical_tool_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = object.__new__(AgentLlmSession)

    class _FakeInjectionManager:
        def drain_at_boundary(self, run_id: str, instance_id: str) -> list[object]:
            _ = (run_id, instance_id)
            return []

    class _FakeControlContext:
        def raise_if_cancelled(self) -> None:
            return None

    class _FakeRunControlManager:
        def context(self, *, run_id: str, instance_id: str) -> _FakeControlContext:
            _ = (run_id, instance_id)
            return _FakeControlContext()

    usage = SimpleNamespace(
        input_tokens=0,
        cache_read_tokens=0,
        output_tokens=0,
        requests=0,
        tool_calls=0,
        details={},
    )
    echoed_request = ModelRequest(
        parts=[
            UserPromptPart(
                content=(
                    "describe this image",
                    BinaryContent(data=b"image-bytes", media_type="image/png"),
                )
            )
        ]
    )
    final_response = ModelResponse(
        parts=[TextPart(content="done")],
        model_name="fake-model",
    )
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Implement API",
        verification=VerificationPlan(),
        spec=TaskSpec(requirements=("return the completed answer without restart",)),
        lifecycle=TaskLifecyclePolicy(
            spec_checkpoint=SpecCheckpointPolicy(
                refresh_interval_tool_calls=99,
                refresh_interval_messages=1,
                refresh_interval_history_tokens=999_999,
            )
        ),
    )
    provider_history = [echoed_request]

    class _TaskRepo:
        async def get_async(self, task_id: str) -> TaskRecord:
            assert task_id == "task-1"
            return TaskRecord(envelope=task)

        async def get_spec_artifact_async(self, artifact_id: str) -> object:
            raise AssertionError(artifact_id)

    class _RoleRegistry:
        def is_coordinator_role(self, role_id: str) -> bool:
            assert role_id == "writer"
            return False

    class _SpecCheckpointMessageRepo(_FakeMessageRepo):
        async def append_system_prompt_if_missing_async(
            self,
            *,
            session_id: str,
            workspace_id: str,
            conversation_id: str,
            agent_role_id: str,
            instance_id: str,
            task_id: str,
            trace_id: str,
            content: str,
        ) -> bool:
            _ = (
                session_id,
                workspace_id,
                conversation_id,
                agent_role_id,
                instance_id,
                task_id,
                trace_id,
            )
            self.appended_system_prompts.append(content)
            return True

    message_repo = _SpecCheckpointMessageRepo(history=[])
    streamed_tool_result = ToolReturnPart(
        tool_name="test_tool",
        content={"ok": True},
        tool_call_id="call-stream",
    )
    canonical_tool_call = ModelResponse(
        parts=[
            ToolCallPart(
                tool_name="test_tool",
                args={"value": "from-provider"},
                tool_call_id="call-stream",
            )
        ]
    )
    canonical_tool_result = ModelRequest(parts=[streamed_tool_result])
    captured_tool_outcome_messages: list[ModelRequest | ModelResponse] = []

    class _FakeToolEventStream:
        def __init__(self) -> None:
            self._index = 0

        def __aiter__(self) -> "_FakeToolEventStream":
            self._index = 0
            return self

        async def __anext__(self) -> object:
            if self._index > 0:
                raise StopAsyncIteration
            self._index += 1
            return FunctionToolResultEvent(result=streamed_tool_result)

    class _FakeToolEventStreamContext:
        async def __aenter__(self) -> _FakeToolEventStream:
            return _FakeToolEventStream()

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            tb: object,
        ) -> bool | None:
            _ = (exc_type, exc, tb)
            return None

    class _FakeToolNode:
        def stream(self, ctx: object) -> _FakeToolEventStreamContext:
            _ = ctx
            return _FakeToolEventStreamContext()

    monkeypatch.setattr(session_runtime_module, "CallToolsNode", _FakeToolNode)

    class _FakeResult:
        response = final_response

        def new_messages(self) -> list[ModelRequest | ModelResponse]:
            return [
                echoed_request,
                canonical_tool_call,
                canonical_tool_result,
                final_response,
            ]

        def usage(self) -> object:
            return usage

    class _FakeAgentRun:
        def __init__(self) -> None:
            self.result = _FakeResult()
            self._nodes: list[object] = []
            self.ctx = object()

        async def __aenter__(self) -> "_FakeAgentRun":
            self._nodes = [_FakeToolNode(), object()]
            return self

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            tb: object,
        ) -> bool | None:
            _ = (exc_type, exc, tb)
            return None

        def __aiter__(self) -> "_FakeAgentRun":
            return self

        async def __anext__(self) -> object:
            if not self._nodes:
                raise StopAsyncIteration
            return self._nodes.pop(0)

        def new_messages(self) -> list[ModelRequest | ModelResponse]:
            return [
                echoed_request,
                canonical_tool_call,
                canonical_tool_result,
                final_response,
            ]

        def usage(self) -> object:
            return usage

    class _FakeAgent:
        def iter(
            self,
            prompt: str | None,
            *,
            deps: object,
            message_history: Sequence[ModelRequest | ModelResponse],
            usage_limits: object,
        ) -> _FakeAgentRun:
            _ = (prompt, deps, message_history, usage_limits)
            return _FakeAgentRun()

    session.__dict__["_config"] = ModelEndpointConfig(
        model="gpt-test",
        base_url="https://example.test/v1",
        api_key="secret",
        capabilities=ModelCapabilities(
            input=ModelModalityMatrix(text=True, image=True),
            output=ModelModalityMatrix(text=True),
        ),
    )
    session.__dict__["_profile_name"] = None
    session.__dict__["_retry_config"] = LlmRetryConfig()
    session.__dict__["_metric_recorder"] = None
    session.__dict__["_tool_registry"] = cast(object, None)
    session.__dict__["_allowed_tools"] = ()
    session.__dict__["_allowed_mcp_servers"] = ()
    session.__dict__["_allowed_skills"] = ()
    session.__dict__["_task_repo"] = _TaskRepo()
    session.__dict__["_shared_store"] = cast(object, None)
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_message_repo"] = cast(MessageRepository, message_repo)
    session.__dict__["_approval_ticket_repo"] = cast(object, None)
    session.__dict__["_user_question_repo"] = None
    session.__dict__["_run_runtime_repo"] = cast(object, None)
    session.__dict__["_run_intent_repo"] = cast(object, None)
    session.__dict__["_background_task_service"] = None
    session.__dict__["_todo_service"] = None
    session.__dict__["_monitor_service"] = None
    session.__dict__["_workspace_manager"] = type(
        "_WorkspaceManager",
        (),
        {"resolve_async": _none_workspace_async},
    )()
    session.__dict__["_media_asset_service"] = cast(object, None)
    session.__dict__["_role_memory_service"] = None
    session.__dict__["_subagent_reflection_service"] = None
    session.__dict__["_conversation_compaction_service"] = None
    session.__dict__["_conversation_microcompact_service"] = None
    session.__dict__["_mcp_registry"] = McpRegistry()
    session.__dict__["_skill_registry"] = cast(object, None)
    session.__dict__["_role_registry"] = _RoleRegistry()
    session.__dict__["_task_execution_service"] = cast(object, object())
    session.__dict__["_task_service"] = cast(object, None)
    session.__dict__["_run_control_manager"] = _FakeRunControlManager()
    session.__dict__["_tool_approval_manager"] = cast(object, None)
    session.__dict__["_user_question_manager"] = None
    session.__dict__["_tool_approval_policy"] = cast(object, None)
    session.__dict__["_notification_service"] = None
    session.__dict__["_token_usage_repo"] = None
    session.__dict__["_fallback_middleware"] = cast(object, None)
    session.__dict__["_im_tool_service"] = None
    session.__dict__["_computer_runtime"] = None
    session.__dict__["_shell_approval_repo"] = None
    session.__dict__["_hook_service"] = None
    session.__dict__["_injection_manager"] = _FakeInjectionManager()
    session.__dict__["_run_event_hub"] = type(
        "_RunEventHub", (), {"publish": lambda self, event: None}
    )()
    session.__dict__["_agent_repo"] = cast(object, None)
    session.__dict__["_resolve_tool_approval_policy_async"] = (
        _none_tool_approval_policy_async
    )
    session.__dict__["_publish_committed_tool_outcome_events_from_messages"] = (
        lambda **kwargs: None
    )

    async def _publish_committed_tool_outcome_events_from_messages_async(
        *,
        request: object,
        messages: Sequence[ModelRequest | ModelResponse],
        published_tool_outcome_ids: set[str] | None = None,
    ) -> bool:
        _ = request
        published_messages: list[ModelRequest | ModelResponse] = []
        for message in messages:
            if isinstance(message, ModelResponse):
                continue
            unpublished_parts: list[ToolReturnPart] = []
            for part in message.parts:
                if not isinstance(part, ToolReturnPart):
                    continue
                tool_call_id = str(part.tool_call_id or "").strip()
                if (
                    tool_call_id
                    and published_tool_outcome_ids is not None
                    and tool_call_id in published_tool_outcome_ids
                ):
                    continue
                unpublished_parts.append(part)
                if tool_call_id and published_tool_outcome_ids is not None:
                    published_tool_outcome_ids.add(tool_call_id)
            if unpublished_parts:
                published_messages.append(ModelRequest(parts=unpublished_parts))
        captured_tool_outcome_messages.extend(published_messages)
        if published_tool_outcome_ids is not None:
            published_tool_outcome_ids.add("call-stream")
        return bool(published_messages)

    session.__dict__["_publish_committed_tool_outcome_events_from_messages_async"] = (
        _publish_committed_tool_outcome_events_from_messages_async
    )

    build_context_calls = 0

    async def _build_agent_iteration_context(
        **kwargs: object,
    ) -> tuple[object, Sequence[ModelRequest | ModelResponse], str, object]:
        nonlocal build_context_calls
        _ = kwargs
        build_context_calls += 1
        if build_context_calls > 1:
            raise AssertionError("final answer boundary should not restart")
        prepared_prompt = SimpleNamespace(
            history=(),
            system_prompt="System prompt",
            budget=SimpleNamespace(),
            estimated_history_tokens_before_microcompact=0,
            estimated_history_tokens_after_microcompact=0,
            microcompact_compacted_message_count=0,
            microcompact_compacted_part_count=0,
        )
        return prepared_prompt, [], "System prompt", _FakeAgent()

    async def _close_run_scoped_llm_http_client(*, request: object) -> None:
        _ = request
        return None

    observed_histories: list[Sequence[ModelRequest | ModelResponse]] = []

    def _drop_duplicate_leading_request(
        *,
        history: Sequence[ModelRequest | ModelResponse],
        new_messages: list[ModelRequest | ModelResponse],
    ) -> list[ModelRequest | ModelResponse]:
        observed_histories.append(history)
        return AgentLlmSession._drop_duplicate_leading_request(
            session,
            history=history,
            new_messages=new_messages,
        )

    session.__dict__["_provider_history_for_model_turn"] = lambda **kwargs: (
        provider_history
    )
    session.__dict__["_drop_duplicate_leading_request"] = (
        _drop_duplicate_leading_request
    )
    session.__dict__["_build_agent_iteration_context"] = _build_agent_iteration_context
    session.__dict__["_close_run_scoped_llm_http_client"] = (
        _close_run_scoped_llm_http_client
    )

    result = await AgentLlmSession._generate_async(
        session,
        _build_request(user_prompt=None),
        skip_initial_user_prompt_persist=True,
    )

    assert result == "done"
    assert observed_histories
    assert all(list(history) == provider_history for history in observed_histories)
    assert build_context_calls == 1
    assert message_repo.appended_system_prompts == []
    assert len(message_repo.append_calls) == 1
    assert message_repo.append_calls[0] == [
        canonical_tool_call,
        canonical_tool_result,
        final_response,
    ]
    streamed_tool_outcome_messages = [
        message
        for message in captured_tool_outcome_messages
        if isinstance(message, ModelRequest) and message.parts == [streamed_tool_result]
    ]
    assert len(streamed_tool_outcome_messages) == 1


@pytest.mark.asyncio
async def test_generate_async_passes_retry_after_to_retry_schedule() -> None:
    session = object.__new__(AgentLlmSession)
    session.__dict__["_config"] = ModelEndpointConfig(
        model="gpt-test",
        base_url="https://example.test/v1",
        api_key="secret",
        context_window=600,
    )
    session.__dict__["_retry_config"] = LlmRetryConfig(
        jitter=False,
        max_retries=2,
        initial_delay_ms=2000,
    )
    session.__dict__["_metric_recorder"] = None
    session.__dict__["_tool_registry"] = cast(object, None)
    session.__dict__["_allowed_tools"] = ()
    session.__dict__["_allowed_mcp_servers"] = ()
    session.__dict__["_allowed_skills"] = ()
    session.__dict__["_task_repo"] = cast(object, None)
    session.__dict__["_shared_store"] = cast(object, None)
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_message_repo"] = cast(
        MessageRepository, _FakeMessageRepo(history=[])
    )
    session.__dict__["_approval_ticket_repo"] = cast(object, None)
    session.__dict__["_run_runtime_repo"] = cast(object, None)
    session.__dict__["_injection_manager"] = type(
        "_InjectionManager",
        (),
        {"drain_at_boundary": lambda self, run_id, instance_id: []},
    )()
    session.__dict__["_run_event_hub"] = type(
        "_RunEventHub", (), {"publish": lambda self, event: None}
    )()
    session.__dict__["_agent_repo"] = cast(object, None)
    session.__dict__["_workspace_manager"] = type(
        "_WorkspaceManager",
        (),
        {"resolve_async": _none_workspace_async},
    )()
    session.__dict__["_role_memory_service"] = None
    session.__dict__["_media_asset_service"] = None
    session.__dict__["_computer_runtime"] = None
    session.__dict__["_background_task_service"] = None
    session.__dict__["_monitor_service"] = None
    session.__dict__["_metric_recorder"] = None
    session.__dict__["_token_usage_repo"] = None
    session.__dict__["_role_registry"] = cast(object, None)
    session.__dict__["_mcp_registry"] = McpRegistry()
    session.__dict__["_task_service"] = cast(object, None)
    session.__dict__["_task_execution_service"] = cast(object, object())
    session.__dict__["_tool_approval_manager"] = cast(object, None)
    session.__dict__["_shell_approval_repo"] = None
    session.__dict__["_notification_service"] = None
    session.__dict__["_im_tool_service"] = None
    session.__dict__["_resolve_tool_approval_policy_async"] = (
        _none_tool_approval_policy_async
    )
    session.__dict__["_build_model_api_error_message"] = lambda error: "rate limited"

    async def _no_recovery(**kwargs: object) -> None:
        _ = kwargs
        return None

    session.__dict__["_maybe_recover_from_tool_args_parse_failure"] = _no_recovery
    session.__dict__["_should_retry_request"] = lambda **kwargs: True

    captured_schedules: list[object] = []

    async def _capture_retry_scheduled(**kwargs: object) -> None:
        captured_schedules.append(kwargs["schedule"])
        raise RuntimeError("stop after scheduling retry")

    session.__dict__["_handle_retry_scheduled"] = _capture_retry_scheduled
    session.__dict__["_persist_user_prompt_if_needed"] = lambda **kwargs: (
        kwargs["history"],
        False,
    )
    session.__dict__["_run_control_manager"] = type(
        "_RunControlManager",
        (),
        {
            "context": lambda self, run_id, instance_id: type(
                "_ControlContext",
                (),
                {"raise_if_cancelled": lambda self: None},
            )()
        },
    )()

    class _FailingAgentContext:
        async def __aenter__(self) -> object:
            request = httpx.Request("POST", "https://example.test/v1/chat/completions")
            response = httpx.Response(
                429,
                headers={"Retry-After": "7"},
                request=request,
            )
            raise APIStatusError(
                "rate limited",
                response=response,
                body={"error": {"code": "rate_limited", "message": "slow down"}},
            )

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            _ = (exc_type, exc, tb)
            return False

    class _FailingAgent:
        def iter(self, *_args: object, **_kwargs: object) -> _FailingAgentContext:
            return _FailingAgentContext()

    async def _build_agent_iteration_context(
        **kwargs: object,
    ) -> tuple[str, list[object], str, object]:
        _ = kwargs
        return "", [], "System prompt", _FailingAgent()

    session.__dict__["_build_agent_iteration_context"] = _build_agent_iteration_context

    with pytest.raises(RuntimeError, match="stop after scheduling retry"):
        await AgentLlmSession._generate_async(
            session,
            _build_request(),
        )

    assert len(captured_schedules) == 1
    schedule = captured_schedules[0]
    assert getattr(schedule, "delay_ms") == 7000


@pytest.mark.asyncio
async def test_generate_async_closes_scoped_transport_cache_on_cancellation() -> None:
    session = object.__new__(AgentLlmSession)
    session.__dict__["_config"] = ModelEndpointConfig(
        model="glm-5",
        base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        api_key="test-key",
        connect_timeout_seconds=15.0,
    )
    session.__dict__["_retry_config"] = LlmRetryConfig(max_retries=0)
    session.__dict__["_tool_registry"] = cast(object, None)
    session.__dict__["_allowed_tools"] = ()
    session.__dict__["_allowed_mcp_servers"] = ()
    session.__dict__["_allowed_skills"] = ()
    session.__dict__["_task_repo"] = cast(object, None)
    session.__dict__["_shared_store"] = cast(object, None)
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_message_repo"] = cast(
        MessageRepository, _FakeMessageRepo(history=[])
    )
    session.__dict__["_approval_ticket_repo"] = cast(object, None)
    session.__dict__["_run_runtime_repo"] = cast(object, None)
    session.__dict__["_injection_manager"] = type(
        "_InjectionManager",
        (),
        {"drain_at_boundary": lambda self, run_id, instance_id: []},
    )()
    session.__dict__["_run_event_hub"] = type(
        "_RunEventHub", (), {"publish": lambda self, event: None}
    )()
    session.__dict__["_agent_repo"] = cast(object, None)
    session.__dict__["_workspace_manager"] = type(
        "_WorkspaceManager",
        (),
        {"resolve_async": _none_workspace_async},
    )()
    session.__dict__["_role_memory_service"] = None
    session.__dict__["_media_asset_service"] = None
    session.__dict__["_computer_runtime"] = None
    session.__dict__["_background_task_service"] = None
    session.__dict__["_monitor_service"] = None
    session.__dict__["_metric_recorder"] = None
    session.__dict__["_token_usage_repo"] = None
    session.__dict__["_role_registry"] = cast(object, None)
    session.__dict__["_mcp_registry"] = McpRegistry()
    session.__dict__["_task_service"] = cast(object, None)
    session.__dict__["_task_execution_service"] = cast(object, object())
    session.__dict__["_tool_approval_manager"] = cast(object, None)
    session.__dict__["_shell_approval_repo"] = None
    session.__dict__["_notification_service"] = None
    session.__dict__["_im_tool_service"] = None
    session.__dict__["_resolve_tool_approval_policy_async"] = (
        _none_tool_approval_policy_async
    )
    session.__dict__["_persist_user_prompt_if_needed"] = lambda **kwargs: (
        kwargs["history"],
        False,
    )

    class _CancelledControlContext:
        def raise_if_cancelled(self) -> None:
            raise asyncio.CancelledError()

    session.__dict__["_run_control_manager"] = type(
        "_RunControlManager",
        (),
        {
            "context": lambda self, run_id, instance_id: _CancelledControlContext(),
        },
    )()

    class _UnusedAgent:
        def iter(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("cancelled runs should not start agent iteration")

    async def _build_agent_iteration_context(
        **kwargs: object,
    ) -> tuple[str, list[object], str, object]:
        _ = kwargs
        return "", [], "System prompt", _UnusedAgent()

    session.__dict__["_build_agent_iteration_context"] = _build_agent_iteration_context

    closed_run_ids: list[str] = []

    async def _close_run_scoped_llm_http_client(*, request: object) -> None:
        closed_run_ids.append(getattr(request, "run_id"))

    session.__dict__["_close_run_scoped_llm_http_client"] = (
        _close_run_scoped_llm_http_client
    )

    with pytest.raises(asyncio.CancelledError):
        await AgentLlmSession._generate_async(
            session,
            _build_request(),
        )

    await asyncio.sleep(0)

    assert closed_run_ids == ["run-1"]


@pytest.mark.asyncio
async def test_generate_async_closes_scoped_transport_cache_on_setup_failure() -> None:
    session = object.__new__(AgentLlmSession)
    session.__dict__["_config"] = ModelEndpointConfig(
        model="glm-5",
        base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        api_key="test-key",
        connect_timeout_seconds=15.0,
    )
    session.__dict__["_retry_config"] = LlmRetryConfig(max_retries=0)
    session.__dict__["_tool_registry"] = cast(object, None)
    session.__dict__["_allowed_tools"] = ()
    session.__dict__["_allowed_mcp_servers"] = ()
    session.__dict__["_allowed_skills"] = ()
    session.__dict__["_task_repo"] = cast(object, None)
    session.__dict__["_shared_store"] = cast(object, None)
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_message_repo"] = cast(
        MessageRepository, _FakeMessageRepo(history=[])
    )
    session.__dict__["_approval_ticket_repo"] = cast(object, None)
    session.__dict__["_run_runtime_repo"] = cast(object, None)
    session.__dict__["_injection_manager"] = type(
        "_InjectionManager",
        (),
        {"drain_at_boundary": lambda self, run_id, instance_id: []},
    )()
    session.__dict__["_run_event_hub"] = type(
        "_RunEventHub", (), {"publish": lambda self, event: None}
    )()
    session.__dict__["_agent_repo"] = cast(object, None)
    session.__dict__["_workspace_manager"] = type(
        "_WorkspaceManager",
        (),
        {"resolve_async": _none_workspace_async},
    )()
    session.__dict__["_role_memory_service"] = None
    session.__dict__["_media_asset_service"] = None
    session.__dict__["_computer_runtime"] = None
    session.__dict__["_background_task_service"] = None
    session.__dict__["_monitor_service"] = None
    session.__dict__["_metric_recorder"] = None
    session.__dict__["_token_usage_repo"] = None
    session.__dict__["_role_registry"] = cast(object, None)
    session.__dict__["_mcp_registry"] = McpRegistry()
    session.__dict__["_task_service"] = cast(object, None)
    session.__dict__["_task_execution_service"] = cast(object, object())
    session.__dict__["_tool_approval_manager"] = cast(object, None)
    session.__dict__["_shell_approval_repo"] = None
    session.__dict__["_notification_service"] = None
    session.__dict__["_im_tool_service"] = None
    session.__dict__["_resolve_tool_approval_policy_async"] = (
        _none_tool_approval_policy_async
    )

    async def _build_agent_iteration_context(**kwargs: object) -> object:
        _ = kwargs
        raise RuntimeError("setup failed after creating scoped client")

    session.__dict__["_build_agent_iteration_context"] = _build_agent_iteration_context

    closed_run_ids: list[str] = []

    async def _close_run_scoped_llm_http_client(*, request: object) -> None:
        closed_run_ids.append(getattr(request, "run_id"))

    session.__dict__["_close_run_scoped_llm_http_client"] = (
        _close_run_scoped_llm_http_client
    )

    with pytest.raises(RuntimeError, match="setup failed"):
        await AgentLlmSession._generate_async(
            session,
            _build_request(),
        )

    await asyncio.sleep(0)

    assert closed_run_ids == ["run-1"]


@pytest.mark.asyncio
async def test_generate_async_does_not_emit_retry_exhausted_after_fallback_exhausted() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        trace_id="run-1",
        objective="Implement API",
        verification=VerificationPlan(),
        spec=TaskSpec(requirements=("keep retry fallback available",)),
        lifecycle=TaskLifecyclePolicy(
            spec_checkpoint=SpecCheckpointPolicy(
                refresh_interval_tool_calls=99,
                refresh_interval_messages=1,
                refresh_interval_history_tokens=999_999,
            )
        ),
    )

    class _TaskRepo:
        async def get_async(self, task_id: str) -> TaskRecord:
            assert task_id == "task-1"
            return TaskRecord(envelope=task)

        async def get_spec_artifact_async(self, artifact_id: str) -> object:
            raise AssertionError(artifact_id)

    class _SpecCheckpointMessageRepo(_FakeMessageRepo):
        async def append_system_prompt_if_missing_async(
            self,
            *,
            session_id: str,
            workspace_id: str,
            conversation_id: str,
            agent_role_id: str,
            instance_id: str,
            task_id: str,
            trace_id: str,
            content: str,
        ) -> bool:
            _ = (
                session_id,
                workspace_id,
                conversation_id,
                agent_role_id,
                instance_id,
                task_id,
                trace_id,
            )
            self.appended_system_prompts.append(content)
            return True

    message_repo = _SpecCheckpointMessageRepo(history=[])
    session.__dict__["_config"] = ModelEndpointConfig(
        model="primary-model",
        base_url="https://example.test/v1",
        api_key="primary-key",
        fallback_policy_id="same_provider_then_other_provider",
    )
    session.__dict__["_profile_name"] = "primary"
    session.__dict__["_retry_config"] = LlmRetryConfig(max_retries=0)
    session.__dict__["_tool_registry"] = cast(object, None)
    session.__dict__["_skill_registry"] = cast(object, None)
    session.__dict__["_allowed_tools"] = ()
    session.__dict__["_allowed_mcp_servers"] = ()
    session.__dict__["_allowed_skills"] = ()
    session.__dict__["_task_repo"] = _TaskRepo()
    session.__dict__["_shared_store"] = cast(object, None)
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_message_repo"] = cast(MessageRepository, message_repo)
    session.__dict__["_approval_ticket_repo"] = cast(object, None)
    session.__dict__["_run_runtime_repo"] = cast(object, None)
    session.__dict__["_injection_manager"] = type(
        "_InjectionManager",
        (),
        {"drain_at_boundary": lambda self, run_id, instance_id: []},
    )()
    session.__dict__["_run_event_hub"] = type(
        "_RunEventHub", (), {"publish": lambda self, event: None}
    )()
    session.__dict__["_agent_repo"] = cast(object, None)
    session.__dict__["_workspace_manager"] = type(
        "_WorkspaceManager",
        (),
        {"resolve_async": _none_workspace_async},
    )()
    session.__dict__["_role_memory_service"] = None
    session.__dict__["_media_asset_service"] = None
    session.__dict__["_computer_runtime"] = None
    session.__dict__["_background_task_service"] = None
    session.__dict__["_monitor_service"] = None
    session.__dict__["_metric_recorder"] = None
    session.__dict__["_token_usage_repo"] = None
    session.__dict__["_role_registry"] = cast(object, None)
    session.__dict__["_mcp_registry"] = McpRegistry()
    session.__dict__["_task_service"] = cast(object, None)
    session.__dict__["_task_execution_service"] = cast(object, object())
    session.__dict__["_tool_approval_manager"] = cast(object, None)
    session.__dict__["_shell_approval_repo"] = None
    session.__dict__["_notification_service"] = None
    session.__dict__["_im_tool_service"] = None
    session.__dict__["_resolve_tool_approval_policy_async"] = (
        _none_tool_approval_policy_async
    )
    session.__dict__["_build_model_api_error_message"] = lambda error: "rate limited"
    session.__dict__["_persist_user_prompt_if_needed"] = lambda **kwargs: (
        kwargs["history"],
        False,
    )
    session.__dict__["_run_control_manager"] = type(
        "_RunControlManager",
        (),
        {
            "context": lambda self, run_id, instance_id: type(
                "_ControlContext",
                (),
                {"raise_if_cancelled": lambda self: None},
            )()
        },
    )()

    async def _no_recovery(**kwargs: object) -> None:
        _ = kwargs
        return None

    session.__dict__["_maybe_recover_from_tool_args_parse_failure"] = _no_recovery
    session.__dict__["_should_retry_request"] = lambda **kwargs: False
    session.__dict__["_fallback_middleware"] = type(
        "_FallbackMiddleware",
        (),
        {
            "has_enabled_policy": lambda self, config: True,
            "select_fallback": lambda self, **kwargs: None,
        },
    )()

    retry_exhausted_calls: list[dict[str, object]] = []
    fallback_exhausted_calls: list[dict[str, object]] = []
    retry_scheduled_calls: list[dict[str, object]] = []

    async def _capture_retry_scheduled(**kwargs: object) -> None:
        retry_scheduled_calls.append(kwargs)

    session.__dict__["_handle_retry_scheduled"] = _capture_retry_scheduled
    session.__dict__["_handle_retry_exhausted"] = lambda **kwargs: (
        retry_exhausted_calls.append(kwargs)
    )
    session.__dict__["_handle_fallback_exhausted"] = lambda **kwargs: (
        fallback_exhausted_calls.append(kwargs)
    )
    session.__dict__["_raise_assistant_run_error"] = lambda **kwargs: (
        _ for _ in ()
    ).throw(RuntimeError("stop after fallback exhaustion"))

    class _FailingAgentContext:
        async def __aenter__(self) -> object:
            request = httpx.Request("POST", "https://example.test/v1/chat/completions")
            response = httpx.Response(
                429,
                headers={"Retry-After": "1"},
                request=request,
            )
            raise APIStatusError(
                "rate limited",
                response=response,
                body={"error": {"code": "rate_limit_exceeded", "message": "slow down"}},
            )

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            _ = (exc_type, exc, tb)
            return False

    class _FailingAgent:
        def iter(self, *_args: object, **_kwargs: object) -> _FailingAgentContext:
            return _FailingAgentContext()

    async def _build_agent_iteration_context(
        **kwargs: object,
    ) -> tuple[str, list[ModelRequest], str, object]:
        _ = kwargs
        history = [ModelRequest(parts=[UserPromptPart(content="existing progress")])]
        if message_repo.appended_system_prompts:
            history.append(
                ModelRequest(
                    parts=[
                        SystemPromptPart(
                            content=message_repo.appended_system_prompts[-1]
                        )
                    ]
                )
            )
        return "", history, "System prompt", _FailingAgent()

    session.__dict__["_build_agent_iteration_context"] = _build_agent_iteration_context

    with pytest.raises(RuntimeError, match="stop after fallback exhaustion"):
        await AgentLlmSession._generate_async(
            session,
            _build_request(),
        )

    assert len(fallback_exhausted_calls) == 1
    # Context editing injection adds a second system prompt append when
    # the spec checkpoint content differs from the agent's current system prompt.
    assert len(message_repo.appended_system_prompts) == 2
    assert retry_scheduled_calls == []
    assert retry_exhausted_calls == []


def test_resolve_role_allowed_tools_uses_updated_role_registry_tools() -> None:
    role_registry = RoleRegistry()
    role_registry.register(
        RoleDefinition(
            role_id="Crafter",
            name="Crafter",
            description="Builds things.",
            version="1",
            tools=("alpha", "generated_sum"),
            system_prompt="Build things.",
        )
    )
    tool_registry = ToolRegistry(
        {
            "alpha": lambda _agent: None,
            "generated_sum": lambda _agent: None,
        }
    )

    resolved = session_runtime_module.resolve_role_allowed_tools(
        tool_registry=tool_registry,
        role_registry=role_registry,
        role_id="Crafter",
        fallback_allowed_tools=("alpha",),
        session_id="session-1",
    )

    assert resolved == ("alpha", "generated_sum")


def test_resolve_role_allowed_tools_filters_coordinator_tools_for_subagents() -> None:
    role_registry = RoleRegistry()
    role_registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates work.",
            version="1",
            tools=("orch_create_tasks", "orch_update_task", "orch_dispatch_task"),
            system_prompt="Coordinate work.",
        )
    )
    role_registry.register(
        RoleDefinition(
            role_id="Crafter",
            name="Crafter",
            description="Builds things.",
            version="1",
            tools=("alpha", "orch_dispatch_task"),
            system_prompt="Build things.",
        )
    )
    tool_registry = ToolRegistry(
        {
            "alpha": lambda _agent: None,
            "orch_dispatch_task": lambda _agent: None,
        }
    )

    resolved = session_runtime_module.resolve_role_allowed_tools(
        tool_registry=tool_registry,
        role_registry=role_registry,
        role_id="Crafter",
        fallback_allowed_tools=("alpha",),
        session_id="session-1",
    )

    assert resolved == ("alpha",)


def test_resolve_role_allowed_tools_uses_fallback_for_missing_runtime_role() -> None:
    role_registry = RoleRegistry()
    tool_registry = ToolRegistry({"alpha": lambda _agent: None})

    resolved = session_runtime_module.resolve_role_allowed_tools(
        tool_registry=tool_registry,
        role_registry=role_registry,
        role_id="Temporary",
        fallback_allowed_tools=("alpha",),
        session_id="session-1",
    )

    assert resolved == ("alpha",)


def test_consume_auto_harness_dirty_tools_returns_pending_runtime_refresh() -> None:
    class _DirtyService:
        def consume_tools_dirty(
            self,
            *,
            run_id: str,
            instance_id: str,
        ) -> tuple[str, ...]:
            assert run_id == "run-1"
            assert instance_id == "instance-1"
            return ("generated_sum",)

    assert session_runtime_module.consume_auto_harness_dirty_tools(
        _DirtyService(),
        run_id="run-1",
        instance_id="instance-1",
    ) == ("generated_sum",)
    assert (
        session_runtime_module.consume_auto_harness_dirty_tools(
            None,
            run_id="run-1",
            instance_id="instance-1",
        )
        == ()
    )


class TestEvaluateCheckpointDrift:
    @pytest.mark.asyncio
    async def test_returns_early_when_task_record_is_none(self) -> None:
        from unittest.mock import MagicMock

        from relay_teams.agents.execution.session_runtime import (
            _evaluate_checkpoint_drift,
        )

        await _evaluate_checkpoint_drift(
            task_repo=MagicMock(),
            task_record=None,
            request=MagicMock(),
            decision=MagicMock(),
            run_event_hub=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_returns_early_when_spec_is_none(self) -> None:
        from unittest.mock import MagicMock
        from relay_teams.agents.execution.session_runtime import (
            _evaluate_checkpoint_drift,
        )

        task_record = MagicMock()
        task_record.envelope.spec = None
        await _evaluate_checkpoint_drift(
            task_repo=MagicMock(),
            task_record=task_record,
            request=MagicMock(),
            decision=MagicMock(),
            run_event_hub=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_returns_early_when_artifact_id_is_none(self) -> None:
        from unittest.mock import MagicMock

        from relay_teams.agents.execution.session_runtime import (
            _evaluate_checkpoint_drift,
        )

        task_record = MagicMock()
        task_record.envelope.spec = MagicMock()
        task_record.envelope.spec_artifact_id = None
        await _evaluate_checkpoint_drift(
            task_repo=MagicMock(),
            task_record=task_record,
            request=MagicMock(),
            decision=MagicMock(),
            run_event_hub=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_returns_early_when_repo_is_not_task_repository(self) -> None:
        from unittest.mock import MagicMock

        from relay_teams.agents.execution.session_runtime import (
            _evaluate_checkpoint_drift,
        )

        task_record = MagicMock()
        task_record.envelope.spec = MagicMock()
        task_record.envelope.spec_artifact_id = "art-1"
        task_record.envelope.lifecycle.spec_checkpoint.drift_score_threshold = 5.0
        await _evaluate_checkpoint_drift(
            task_repo="not_a_repo",  # not a TaskRepository
            task_record=task_record,
            request=MagicMock(),
            decision=MagicMock(),
            run_event_hub=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_returns_early_when_no_llm_evaluator(self) -> None:
        from unittest.mock import MagicMock

        from relay_teams.agents.execution.session_runtime import (
            _evaluate_checkpoint_drift,
        )
        from relay_teams.agents.tasks.task_repository import TaskRepository

        repo = MagicMock(spec=TaskRepository)
        task_record = MagicMock()
        task_record.envelope.spec = MagicMock()
        task_record.envelope.spec_artifact_id = "art-1"
        task_record.envelope.lifecycle.spec_checkpoint.drift_score_threshold = 5.0
        request = MagicMock(spec=[])
        await _evaluate_checkpoint_drift(
            task_repo=repo,
            task_record=task_record,
            request=request,
            decision=MagicMock(),
            run_event_hub=MagicMock(),
        )
