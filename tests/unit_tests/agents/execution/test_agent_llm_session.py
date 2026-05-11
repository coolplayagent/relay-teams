# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace

import pytest
import relay_teams.agents.execution.agent_llm_session as llm_module
from relay_teams.agents.execution.agent_llm_session import AgentLlmSession
from relay_teams.agents.execution.session_prompt import SessionPromptMixin
from relay_teams.agents.execution.session_recovery import SessionRecoveryMixin
from relay_teams.agents.execution.session_runtime import SessionRuntimeMixin
from relay_teams.agents.execution.session_support import SessionSupportMixin
from relay_teams.tools.runtime.policy import ToolApprovalPolicy


def test_agent_llm_session_module_re_exports_runtime_symbols() -> None:
    assert llm_module.build_coordination_agent is not None
    assert llm_module.ModelRequestNode is not None
    assert llm_module.compute_retry_delay_ms is not None
    assert llm_module.load_tool_call_state is not None
    assert llm_module.load_or_recover_tool_call_state is not None
    assert llm_module.log_event is not None
    assert llm_module.log_model_stream_chunk is not None


def test_agent_llm_session_composes_runtime_prompt_support_and_recovery_mixins() -> (
    None
):
    mro = AgentLlmSession.__mro__

    assert SessionRuntimeMixin in mro
    assert SessionRecoveryMixin in mro
    assert SessionSupportMixin in mro
    assert SessionPromptMixin in mro


class _RunIntentRepo:
    async def get_async(self, _run_id: str) -> object:
        return SimpleNamespace(
            yolo=True,
            shell_safety_policy_enabled=False,
        )


@pytest.mark.asyncio
async def test_session_prompt_resolves_shell_safety_policy_from_run_intent() -> None:
    session = object.__new__(SessionPromptMixin)
    session.__dict__["_run_intent_repo"] = _RunIntentRepo()
    session.__dict__["_tool_approval_policy"] = ToolApprovalPolicy()

    policy = await SessionPromptMixin._resolve_tool_approval_policy_async(
        session,
        "run-1",
    )

    assert policy.yolo is True
    assert policy.shell_safety_policy_enabled is False
