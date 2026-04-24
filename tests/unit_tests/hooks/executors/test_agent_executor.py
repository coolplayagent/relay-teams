from __future__ import annotations

import pytest

from relay_teams.hooks.executors.agent_executor import AgentHookExecutor
from relay_teams.hooks.hook_event_models import StopInput
from relay_teams.hooks.hook_models import (
    HookDecisionType,
    HookEventName,
    HookHandlerConfig,
    HookHandlerType,
)


class _SessionRecord:
    def __init__(self, workspace_id: str) -> None:
        self.workspace_id = workspace_id


class _SessionRepo:
    def get(self, session_id: str) -> _SessionRecord:
        assert session_id == "session-1"
        return _SessionRecord(workspace_id="default")


class _EmptyWorkspaceSessionRepo:
    def get(self, session_id: str) -> _SessionRecord:
        assert session_id == "session-1"
        return _SessionRecord(workspace_id="")


class _Result:
    def __init__(self, output: str) -> None:
        self.output = output


class _BackgroundTaskService:
    async def run_subagent(
        self,
        *,
        run_id: str,
        session_id: str,
        workspace_id: str,
        subagent_role_id: str,
        title: str,
        prompt: str,
        suppress_hooks: bool = False,
    ) -> _Result:
        assert run_id == "run-1"
        assert session_id == "session-1"
        assert workspace_id == "default"
        assert subagent_role_id == "Verifier"
        assert suppress_hooks is True
        assert "$ARGUMENTS" not in prompt
        return _Result('{"decision":"retry","additional_context":["keep going"]}')


class _EmptyOutputBackgroundTaskService:
    async def run_subagent(self, **kwargs: object) -> _Result:
        _ = kwargs
        return _Result("")


@pytest.mark.asyncio
async def test_agent_executor_runs_subagent_and_parses_decision() -> None:
    executor = AgentHookExecutor(
        background_task_service=_BackgroundTaskService(),
        session_repo=_SessionRepo(),
    )

    result = await executor.execute(
        handler=HookHandlerConfig(
            type=HookHandlerType.AGENT,
            role_id="Verifier",
            prompt="Review this stop request: $ARGUMENTS",
        ),
        event_input=StopInput(
            event_name=HookEventName.STOP,
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            output_text="candidate answer",
        ),
    )

    assert result.decision == HookDecisionType.RETRY
    assert result.additional_context == ("keep going",)


@pytest.mark.asyncio
async def test_agent_executor_requires_role_id() -> None:
    executor = AgentHookExecutor(
        background_task_service=_BackgroundTaskService(),
        session_repo=_SessionRepo(),
    )

    with pytest.raises(ValueError, match="role_id"):
        await executor.execute(
            handler=HookHandlerConfig.model_construct(
                type=HookHandlerType.AGENT,
                prompt="Review: $ARGUMENTS",
            ),
            event_input=StopInput(
                event_name=HookEventName.STOP,
                session_id="session-1",
                run_id="run-1",
                trace_id="run-1",
            ),
        )


@pytest.mark.asyncio
async def test_agent_executor_requires_prompt() -> None:
    executor = AgentHookExecutor(
        background_task_service=_BackgroundTaskService(),
        session_repo=_SessionRepo(),
    )

    with pytest.raises(ValueError, match="prompt"):
        await executor.execute(
            handler=HookHandlerConfig.model_construct(
                type=HookHandlerType.AGENT,
                role_id="Verifier",
            ),
            event_input=StopInput(
                event_name=HookEventName.STOP,
                session_id="session-1",
                run_id="run-1",
                trace_id="run-1",
            ),
        )


@pytest.mark.asyncio
async def test_agent_executor_requires_workspace() -> None:
    executor = AgentHookExecutor(
        background_task_service=_BackgroundTaskService(),
        session_repo=_EmptyWorkspaceSessionRepo(),
    )

    with pytest.raises(RuntimeError, match="workspace"):
        await executor.execute(
            handler=HookHandlerConfig(
                type=HookHandlerType.AGENT,
                role_id="Verifier",
                prompt="Review: $ARGUMENTS",
            ),
            event_input=StopInput(
                event_name=HookEventName.STOP,
                session_id="session-1",
                run_id="run-1",
                trace_id="run-1",
            ),
        )


@pytest.mark.asyncio
async def test_agent_executor_requires_decision_payload() -> None:
    executor = AgentHookExecutor(
        background_task_service=_EmptyOutputBackgroundTaskService(),
        session_repo=_SessionRepo(),
    )

    with pytest.raises(RuntimeError, match="decision payload"):
        await executor.execute(
            handler=HookHandlerConfig(
                type=HookHandlerType.AGENT,
                role_id="Verifier",
                prompt="Review: $ARGUMENTS",
            ),
            event_input=StopInput(
                event_name=HookEventName.STOP,
                session_id="session-1",
                run_id="run-1",
                trace_id="run-1",
            ),
        )
