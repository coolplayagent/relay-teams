# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic_ai import Agent

import relay_teams.tools.task_tools.ask_question as ask_question_module
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.sessions.runs.user_question_manager import (
    UserQuestionClosedError,
    UserQuestionManager,
)
from relay_teams.sessions.runs.user_question_models import (
    UserQuestionAnswer,
    UserQuestionOption,
    UserQuestionPrompt,
    UserQuestionRequestRecord,
    UserQuestionRequestStatus,
    UserQuestionSelection,
)
from relay_teams.sessions.runs.user_question_repository import (
    UserQuestionRepository,
    UserQuestionStatusConflictError,
)
from relay_teams.tools.runtime.context import (
    ToolContext,
    ToolDeps,
)
from relay_teams.tools.runtime.models import ToolResultProjection
from relay_teams.tools.task_tools.ask_question import register


async def _invoke_tool_action(
    action: Callable[..., Awaitable[ToolResultProjection]],
    raw_args: dict[str, object] | None,
) -> ToolResultProjection:
    resolved_raw_args = {} if raw_args is None else raw_args
    tool_args = {
        name: resolved_raw_args[name]
        for name in inspect.signature(action).parameters
        if name in resolved_raw_args
    }
    return await action(**tool_args)


class _FakeAgent:
    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., object]] = {}

    def tool(
        self, *, description: str
    ) -> Callable[[Callable[..., object]], Callable[..., object]]:
        _ = description

        def decorator(func: Callable[..., object]) -> Callable[..., object]:
            self.tools[func.__name__] = func
            return func

        return decorator


class _ClosingUserQuestionManager(UserQuestionManager):
    def wait_for_answer(
        self,
        *,
        run_id: str,
        question_id: str,
        timeout: float = 0.0,
    ):
        del run_id, question_id, timeout
        raise UserQuestionClosedError("closed by stop")


class _TimingOutUserQuestionManager(UserQuestionManager):
    def wait_for_answer(
        self,
        *,
        run_id: str,
        question_id: str,
        timeout: float = 0.0,
    ):
        del run_id, question_id, timeout
        raise TimeoutError("timed out")


def _build_context(
    *,
    user_question_repo: UserQuestionRepository,
    user_question_manager: UserQuestionManager,
    run_runtime_repo: RunRuntimeRepository,
) -> SimpleNamespace:
    return SimpleNamespace(
        tool_call_id="call-question-1",
        deps=SimpleNamespace(
            user_question_repo=user_question_repo,
            user_question_manager=user_question_manager,
            run_runtime_repo=run_runtime_repo,
            run_event_hub=RunEventHub(),
            role_registry=SimpleNamespace(
                is_coordinator_role=lambda role_id: role_id == "Coordinator"
            ),
            run_id="run-1",
            session_id="session-1",
            task_id="task-1",
            instance_id="inst-1",
            role_id="Coordinator",
            trace_id="run-1",
        ),
    )


def test_set_runtime_phase_does_not_override_stopping_run(tmp_path: Path) -> None:
    runtime_repo = RunRuntimeRepository(tmp_path / "ask_question_runtime.db")
    runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
        root_task_id="task-1",
    )
    runtime_repo.update(
        "run-1",
        status=RunRuntimeStatus.STOPPING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    ctx = _build_context(
        user_question_repo=UserQuestionRepository(tmp_path / "ask_question_repo.db"),
        user_question_manager=UserQuestionManager(),
        run_runtime_repo=runtime_repo,
    )

    ask_question_module._set_runtime_phase(
        cast(ToolContext, ctx),
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )

    runtime = runtime_repo.get("run-1")
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.STOPPING
    assert runtime.phase == RunRuntimePhase.COORDINATOR_RUNNING


def test_set_runtime_phase_does_not_override_stopped_run(tmp_path: Path) -> None:
    runtime_repo = RunRuntimeRepository(tmp_path / "ask_question_runtime_stopped.db")
    runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
        root_task_id="task-1",
    )
    runtime_repo.update(
        "run-1",
        status=RunRuntimeStatus.STOPPED,
        phase=RunRuntimePhase.IDLE,
    )
    ctx = _build_context(
        user_question_repo=UserQuestionRepository(tmp_path / "ask_question_repo.db"),
        user_question_manager=UserQuestionManager(),
        run_runtime_repo=runtime_repo,
    )

    ask_question_module._set_runtime_phase(
        cast(ToolContext, ctx),
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )

    runtime = runtime_repo.get("run-1")
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.STOPPED
    assert runtime.phase == RunRuntimePhase.IDLE


@pytest.mark.asyncio
async def test_ask_question_keeps_runtime_paused_when_closed_by_stop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["ask_question"],
    )
    runtime_repo = RunRuntimeRepository(tmp_path / "ask_question_runtime_closed.db")
    runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
        root_task_id="task-1",
    )
    user_question_repo = UserQuestionRepository(
        tmp_path / "ask_question_repo_closed.db"
    )
    ctx = _build_context(
        user_question_repo=user_question_repo,
        user_question_manager=_ClosingUserQuestionManager(),
        run_runtime_repo=runtime_repo,
    )

    async def _fake_execute_tool(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[..., Awaitable[ToolResultProjection]],
        raw_args: dict[str, object] | None = None,
        **_: object,
    ) -> dict[str, object]:
        del ctx, tool_name, args_summary
        projected = await _invoke_tool_action(action, raw_args)
        return cast(dict[str, object], projected.visible_data)

    monkeypatch.setattr(ask_question_module, "execute_tool_call", _fake_execute_tool)

    result = await tool(
        ctx,
        questions=[
            UserQuestionPrompt(
                question="Pick one",
                options=(UserQuestionOption(label="Only", description="Only"),),
                multiple=False,
            )
        ],
    )

    assert result == {
        "status": "completed",
        "question_id": "call-question-1",
    }
    runtime = runtime_repo.get("run-1")
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.PAUSED
    assert runtime.phase == RunRuntimePhase.AWAITING_MANUAL_ACTION


@pytest.mark.asyncio
async def test_ask_question_returns_persisted_answer_when_timeout_loses_race(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["ask_question"],
    )
    runtime_repo = RunRuntimeRepository(tmp_path / "ask_question_runtime_timeout.db")
    runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
        root_task_id="task-1",
    )
    user_question_repo = UserQuestionRepository(
        tmp_path / "ask_question_repo_timeout.db"
    )
    ctx = _build_context(
        user_question_repo=user_question_repo,
        user_question_manager=_TimingOutUserQuestionManager(),
        run_runtime_repo=runtime_repo,
    )

    async def _fake_execute_tool(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[..., Awaitable[ToolResultProjection]],
        raw_args: dict[str, object] | None = None,
        **_: object,
    ) -> dict[str, object]:
        del ctx, tool_name, args_summary
        projected = await _invoke_tool_action(action, raw_args)
        return cast(dict[str, object], projected.visible_data)

    monkeypatch.setattr(ask_question_module, "execute_tool_call", _fake_execute_tool)
    original_resolve = user_question_repo.resolve

    def resolve_with_answered_race(*, question_id: str, **kwargs: object):
        _ = kwargs
        _ = original_resolve(
            question_id=question_id,
            status=UserQuestionRequestStatus.ANSWERED,
            answers=(
                UserQuestionAnswer(selections=(UserQuestionSelection(label="Only"),)),
            ),
        )
        raise UserQuestionStatusConflictError(
            question_id=question_id,
            expected_status=UserQuestionRequestStatus.REQUESTED,
            actual_status=UserQuestionRequestStatus.ANSWERED,
        )

    monkeypatch.setattr(user_question_repo, "resolve", resolve_with_answered_race)

    result = await tool(
        ctx,
        questions=[
            UserQuestionPrompt(
                question="Pick one",
                options=(UserQuestionOption(label="Only", description="Only"),),
                multiple=False,
            )
        ],
    )

    assert result == {
        "status": "answered",
        "question_id": "call-question-1",
        "answers": [
            {
                "selections": [
                    {
                        "label": "Only",
                        "supplement": None,
                    }
                ]
            }
        ],
    }
    runtime = runtime_repo.get("run-1")
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.RUNNING
    assert runtime.phase == RunRuntimePhase.COORDINATOR_RUNNING


@pytest.mark.asyncio
async def test_ask_question_rejects_empty_question_list(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["ask_question"],
    )
    runtime_repo = RunRuntimeRepository(tmp_path / "ask_question_runtime_empty.db")
    runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
        root_task_id="task-1",
    )
    user_question_repo = UserQuestionRepository(tmp_path / "ask_question_repo_empty.db")
    ctx = _build_context(
        user_question_repo=user_question_repo,
        user_question_manager=UserQuestionManager(),
        run_runtime_repo=runtime_repo,
    )

    async def _fake_execute_tool(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[..., Awaitable[ToolResultProjection]],
        raw_args: dict[str, object] | None = None,
        **_: object,
    ) -> dict[str, object]:
        del ctx, tool_name, args_summary
        await _invoke_tool_action(action, raw_args)
        raise AssertionError("expected validation error")

    monkeypatch.setattr(ask_question_module, "execute_tool_call", _fake_execute_tool)

    with pytest.raises(
        ask_question_module.ToolExecutionError,
        match="requires at least one question",
    ):
        await tool(
            ctx,
            questions=[],
        )

    assert user_question_repo.list_by_run("run-1") == ()


@pytest.mark.asyncio
async def test_ask_question_publishes_resolution_event_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["ask_question"],
    )
    runtime_repo = RunRuntimeRepository(
        tmp_path / "ask_question_runtime_timeout_event.db"
    )
    runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
        root_task_id="task-1",
    )
    user_question_repo = UserQuestionRepository(
        tmp_path / "ask_question_repo_timeout_event.db"
    )
    ctx = _build_context(
        user_question_repo=user_question_repo,
        user_question_manager=_TimingOutUserQuestionManager(),
        run_runtime_repo=runtime_repo,
    )
    published_events: list[RunEvent] = []

    def publish(event: RunEvent) -> None:
        published_events.append(event)

    setattr(ctx.deps.run_event_hub, "publish", publish)

    async def _fake_execute_tool(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[..., Awaitable[ToolResultProjection]],
        raw_args: dict[str, object] | None = None,
        **_: object,
    ) -> dict[str, object]:
        del ctx, tool_name, args_summary
        projected = await _invoke_tool_action(action, raw_args)
        return cast(dict[str, object], projected.visible_data)

    monkeypatch.setattr(ask_question_module, "execute_tool_call", _fake_execute_tool)

    result = await tool(
        ctx,
        questions=[
            UserQuestionPrompt(
                question="Pick one",
                options=(UserQuestionOption(label="Only", description="Only"),),
                multiple=False,
            )
        ],
    )

    assert result == {
        "status": "timed_out",
        "question_id": "call-question-1",
    }
    assert [event.event_type for event in published_events] == [
        RunEventType.USER_QUESTION_REQUESTED,
        RunEventType.USER_QUESTION_ANSWERED,
    ]
    timeout_payload = json.loads(published_events[-1].payload_json)
    assert timeout_payload == {
        "question_id": "call-question-1",
        "status": "timed_out",
        "instance_id": "inst-1",
        "role_id": "Coordinator",
    }


@pytest.mark.asyncio
async def test_ask_question_completes_without_publishing_request_when_closed_during_persist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["ask_question"],
    )
    runtime_repo = RunRuntimeRepository(tmp_path / "ask_question_runtime_stop.db")
    runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
        root_task_id="task-1",
    )
    user_question_repo = UserQuestionRepository(tmp_path / "ask_question_repo_stop.db")
    user_question_manager = UserQuestionManager()
    ctx = _build_context(
        user_question_repo=user_question_repo,
        user_question_manager=user_question_manager,
        run_runtime_repo=runtime_repo,
    )
    published_events: list[RunEvent] = []

    def publish(event: RunEvent) -> None:
        published_events.append(event)

    setattr(ctx.deps.run_event_hub, "publish", publish)

    async def _fake_execute_tool(
        ctx,
        *,
        tool_name: str,
        args_summary: dict[str, object],
        action: Callable[..., Awaitable[ToolResultProjection]],
        raw_args: dict[str, object] | None = None,
        **_: object,
    ) -> dict[str, object]:
        del ctx, tool_name, args_summary
        projected = await _invoke_tool_action(action, raw_args)
        return cast(dict[str, object], projected.visible_data)

    monkeypatch.setattr(ask_question_module, "execute_tool_call", _fake_execute_tool)
    original_upsert = user_question_repo.upsert_requested

    def upsert_and_close(
        *,
        question_id: str,
        run_id: str,
        session_id: str,
        task_id: str,
        instance_id: str,
        role_id: str,
        tool_name: str,
        questions: tuple[UserQuestionPrompt, ...],
    ) -> UserQuestionRequestRecord:
        user_question_manager.mark_questions_closed_for_run(
            run_id=run_id,
            reason="run_stopped",
        )
        return original_upsert(
            question_id=question_id,
            run_id=run_id,
            session_id=session_id,
            task_id=task_id,
            instance_id=instance_id,
            role_id=role_id,
            tool_name=tool_name,
            questions=questions,
        )

    monkeypatch.setattr(user_question_repo, "upsert_requested", upsert_and_close)

    result = await tool(
        ctx,
        questions=[
            UserQuestionPrompt(
                question="Pick one",
                options=(UserQuestionOption(label="Only", description="Only"),),
                multiple=False,
            )
        ],
    )

    assert result == {
        "status": "completed",
        "question_id": "call-question-1",
    }
    assert published_events == []
    record = user_question_repo.get("call-question-1")
    assert record is not None
    assert record.status == UserQuestionRequestStatus.COMPLETED
