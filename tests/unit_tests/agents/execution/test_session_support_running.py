# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

import pytest

from relay_teams.agents.execution.agent_llm_session import AgentLlmSession
from relay_teams.tools.runtime.context import ToolDeps
from relay_teams.tools.runtime.persisted_state import (
    PersistedToolCallState,
    ToolExecutionStatus,
)

from .agent_llm_session_test_support import LLMRequest


def _build_request() -> LLMRequest:
    return LLMRequest(
        run_id="run-1",
        trace_id="trace-1",
        task_id="task-1",
        session_id="session-1",
        workspace_id="ws-1",
        conversation_id="conv-1",
        instance_id="inst-1",
        role_id="writer",
        system_prompt="sys",
        user_prompt=None,
    )


@pytest.mark.asyncio
async def test_visible_result_for_batch_item_returns_interrupted_error_for_running_non_spawn() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    session.__dict__["_shared_store"] = cast(object, None)
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_task_repo"] = cast(object, None)

    state = PersistedToolCallState(
        tool_call_id="call-running-1",
        tool_name="some_other_tool",
        instance_id="inst-1",
        role_id="writer",
        execution_status=ToolExecutionStatus.RUNNING,
        result_envelope=None,
    )

    deps = MagicMock(spec=ToolDeps)

    result = await session._visible_result_for_batch_item(
        request=_build_request(),
        deps=deps,
        state=state,
        tool_call_id="call-running-1",
        tool_name="some_other_tool",
        raw_args=None,
        recover_ready_calls=False,
    )

    assert result is not None
    error_val = result.get("error")
    assert isinstance(error_val, dict)
    assert error_val["code"] == "tool_execution_interrupted"
