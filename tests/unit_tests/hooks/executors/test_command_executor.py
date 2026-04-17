from __future__ import annotations

import sys

import pytest

from relay_teams.hooks.executors.command_executor import (
    CommandHookExecutor,
    _strip_wrapping_quotes,
)
from relay_teams.hooks.hook_event_models import UserPromptSubmitInput
from relay_teams.hooks.hook_models import (
    HookDecisionType,
    HookEventName,
    HookHandlerConfig,
    HookHandlerType,
)
from relay_teams.sessions.runs.run_models import RunKind


def test_strip_wrapping_quotes_handles_wrapped_windows_args() -> None:
    assert (
        _strip_wrapping_quotes('"C:/Program Files/Python/python.exe"')
        == "C:/Program Files/Python/python.exe"
    )
    assert _strip_wrapping_quotes("'script path.py'") == "script path.py"
    assert _strip_wrapping_quotes("plain") == "plain"


@pytest.mark.asyncio
async def test_command_executor_runs_quoted_python_script(tmp_path) -> None:
    script_path = tmp_path / "hook.py"
    script_path.write_text(
        'import json\nimport sys\n_ = json.load(sys.stdin)\nprint(json.dumps({"decision": "updated_input", "updated_input": "rewritten"}))\n',
        encoding="utf-8",
    )
    executor = CommandHookExecutor()
    result = await executor.execute(
        handler=HookHandlerConfig(
            type=HookHandlerType.COMMAND,
            command=f'"{sys.executable}" "{script_path}"',
        ),
        event_input=UserPromptSubmitInput(
            event_name=HookEventName.USER_PROMPT_SUBMIT,
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
            task_id="task-1",
            instance_id="instance-1",
            role_id="MainAgent",
            user_prompt="hello",
            input_parts=(),
            run_kind=RunKind.CONVERSATION.value,
        ),
    )

    assert result.decision == HookDecisionType.UPDATED_INPUT
    assert result.updated_input == "rewritten"
