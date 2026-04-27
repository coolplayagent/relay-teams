from __future__ import annotations

import sys

import pytest

from relay_teams.hooks.executors.command_executor import (
    CommandHookExecutor,
    _build_command_args,
    _decode_limited,
    _exit_code_2_decision,
    _strip_wrapping_quotes,
)
from relay_teams.hooks.hook_event_models import (
    NotificationInput,
    PreToolUseInput,
    StopInput,
    UserPromptSubmitInput,
)
from relay_teams.hooks.hook_models import (
    HookDecisionType,
    HookEventName,
    HookHandlerConfig,
    HookHandlerType,
    HookShell,
)
from relay_teams.sessions.runs.run_models import RunKind


def test_strip_wrapping_quotes_handles_wrapped_windows_args() -> None:
    assert (
        _strip_wrapping_quotes('"C:/Program Files/Python/python.exe"')
        == "C:/Program Files/Python/python.exe"
    )
    assert _strip_wrapping_quotes("'script path.py'") == "script path.py"
    assert _strip_wrapping_quotes("plain") == "plain"


def test_decode_limited_rejects_oversized_output() -> None:
    with pytest.raises(RuntimeError, match="stdout exceeded 4 byte limit"):
        _ = _decode_limited(b"12345", limit_bytes=4, stream_name="stdout")


def test_build_command_args_wraps_explicit_shell() -> None:
    assert _build_command_args(command="echo hello | cat", shell=HookShell.BASH) == [
        "bash",
        "-lc",
        "echo hello | cat",
    ]
    powershell_args = _build_command_args(
        command="Write-Output hello",
        shell=HookShell.POWERSHELL,
    )

    assert powershell_args[-2:] == ["-Command", "Write-Output hello"]


def test_build_command_args_returns_posix_split_when_not_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "relay_teams.hooks.executors.command_executor.sys.platform",
        "linux",
    )

    assert _build_command_args(command="python hook.py", shell=None) == [
        "python",
        "hook.py",
    ]


def test_exit_code_two_decisions_cover_deny_and_observe_events() -> None:
    pre_tool_decision = _exit_code_2_decision(
        PreToolUseInput(
            event_name=HookEventName.PRE_TOOL_USE,
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
            tool_name="shell",
            tool_call_id="tool-1",
            tool_input={"command": "rm -rf tmp"},
        )
    )
    notification_decision = _exit_code_2_decision(
        NotificationInput(
            event_name=HookEventName.NOTIFICATION,
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
            notification_type="run_failed",
            title="Run Failed",
            body="Run failed.",
        )
    )

    assert pre_tool_decision == HookDecisionType.DENY
    assert notification_decision == HookDecisionType.OBSERVE


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


@pytest.mark.asyncio
async def test_command_executor_empty_stdout_allows_by_default(tmp_path) -> None:
    script_path = tmp_path / "hook.py"
    script_path.write_text("import sys\n_ = sys.stdin.read()\n", encoding="utf-8")
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
            user_prompt="hello",
            input_parts=(),
            run_kind=RunKind.CONVERSATION.value,
        ),
    )

    assert result.decision == HookDecisionType.ALLOW


@pytest.mark.asyncio
async def test_command_executor_allows_success_with_large_stderr(tmp_path) -> None:
    script_path = tmp_path / "hook.py"
    script_path.write_text(
        "import sys\n_ = sys.stdin.read()\nsys.stderr.write('x' * (70 * 1024))\n",
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
            user_prompt="hello",
            input_parts=(),
            run_kind=RunKind.CONVERSATION.value,
        ),
    )

    assert result.decision == HookDecisionType.ALLOW


@pytest.mark.asyncio
async def test_command_executor_empty_stdout_observes_notification(tmp_path) -> None:
    script_path = tmp_path / "hook.py"
    script_path.write_text("import sys\n_ = sys.stdin.read()\n", encoding="utf-8")
    executor = CommandHookExecutor()

    result = await executor.execute(
        handler=HookHandlerConfig(
            type=HookHandlerType.COMMAND,
            command=f'"{sys.executable}" "{script_path}"',
        ),
        event_input=NotificationInput(
            event_name=HookEventName.NOTIFICATION,
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
            notification_type="run_failed",
            title="Run Failed",
            body="Run failed.",
        ),
    )

    assert result.decision == HookDecisionType.OBSERVE


@pytest.mark.asyncio
async def test_command_executor_parses_pre_tool_hook_specific_output(tmp_path) -> None:
    script_path = tmp_path / "hook.py"
    script_path.write_text(
        "import json\n"
        "import sys\n"
        "_ = json.load(sys.stdin)\n"
        "print(json.dumps({"
        '"hookSpecificOutput": {'
        '"hookEventName": "PreToolUse",'
        '"permissionDecision": "deny",'
        '"permissionDecisionReason": "blocked"'
        "}}))\n",
        encoding="utf-8",
    )
    executor = CommandHookExecutor()

    result = await executor.execute(
        handler=HookHandlerConfig(
            type=HookHandlerType.COMMAND,
            command=f'"{sys.executable}" "{script_path}"',
        ),
        event_input=PreToolUseInput(
            event_name=HookEventName.PRE_TOOL_USE,
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
            tool_name="shell",
            tool_call_id="tool-1",
            tool_input={"command": "rm -rf tmp"},
        ),
    )

    assert result.decision == HookDecisionType.DENY
    assert result.reason == "blocked"


@pytest.mark.asyncio
async def test_command_executor_exit_code_two_retries_stop(tmp_path) -> None:
    script_path = tmp_path / "hook.py"
    script_path.write_text(
        "import sys\n_ = sys.stdin.read()\nsys.exit(2)\n", encoding="utf-8"
    )
    executor = CommandHookExecutor()

    result = await executor.execute(
        handler=HookHandlerConfig(
            type=HookHandlerType.COMMAND,
            command=f'"{sys.executable}" "{script_path}"',
        ),
        event_input=StopInput(
            event_name=HookEventName.STOP,
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
            completion_reason="assistant_response",
            output_text="done",
        ),
    )

    assert result.decision == HookDecisionType.RETRY


@pytest.mark.asyncio
async def test_command_executor_nonzero_exit_raises_stderr(tmp_path) -> None:
    script_path = tmp_path / "hook.py"
    script_path.write_text(
        "import sys\n_ = sys.stdin.read()\nprint('nope', file=sys.stderr)\nsys.exit(3)\n",
        encoding="utf-8",
    )
    executor = CommandHookExecutor()

    with pytest.raises(RuntimeError, match="nope"):
        _ = await executor.execute(
            handler=HookHandlerConfig(
                type=HookHandlerType.COMMAND,
                command=f'"{sys.executable}" "{script_path}"',
            ),
            event_input=UserPromptSubmitInput(
                event_name=HookEventName.USER_PROMPT_SUBMIT,
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
                user_prompt="hello",
                input_parts=(),
                run_kind=RunKind.CONVERSATION.value,
            ),
        )
