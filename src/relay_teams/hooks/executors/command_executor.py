from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
import sys

from relay_teams.hooks.hook_event_models import HookEventInput
from relay_teams.hooks.hook_models import (
    HookDecision,
    HookDecisionType,
    HookEventName,
    HookHandlerConfig,
    HookShell,
)
from relay_teams.hooks.executors.output_parser import (
    parse_empty_hook_output,
    parse_hook_decision_payload,
)

_MAX_STDOUT_BYTES = 1024 * 1024
_MAX_STDERR_BYTES = 64 * 1024


class CommandHookExecutor:
    async def execute(
        self,
        *,
        handler: HookHandlerConfig,
        event_input: HookEventInput,
    ) -> HookDecision:
        command = str(handler.command or "").strip()
        if not command:
            raise ValueError("Command hook requires a command")
        args = _build_command_args(command=command, shell=handler.shell)
        payload = event_input.model_dump_json().encode("utf-8")
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(payload),
                timeout=handler.timeout_seconds,
            )
            return_code = process.returncode
        except NotImplementedError:
            completed = await asyncio.to_thread(
                subprocess.run,
                args,
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=handler.timeout_seconds,
            )
            stdout = completed.stdout
            stderr = completed.stderr
            return_code = completed.returncode
        if return_code != 0:
            raw_stderr = _decode_limited(
                stderr,
                limit_bytes=_MAX_STDERR_BYTES,
                stream_name="stderr",
            ).strip()
            if return_code == 2:
                return HookDecision(
                    decision=_exit_code_2_decision(event_input),
                    reason=raw_stderr,
                )
            message = raw_stderr or f"Command hook exited with status {return_code}"
            raise RuntimeError(message)
        raw_stdout = _decode_limited(
            stdout,
            limit_bytes=_MAX_STDOUT_BYTES,
            stream_name="stdout",
        ).strip()
        if not raw_stdout:
            return parse_empty_hook_output(event_name=event_input.event_name)
        return parse_hook_decision_payload(
            json.loads(raw_stdout),
            event_name=event_input.event_name,
        )


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _build_command_args(*, command: str, shell: HookShell | None) -> list[str]:
    if shell == HookShell.BASH:
        return ["bash", "-lc", command]
    if shell == HookShell.POWERSHELL:
        executable = "powershell.exe" if sys.platform.startswith("win") else "pwsh"
        return [executable, "-NoProfile", "-NonInteractive", "-Command", command]
    args = shlex.split(command, posix=(not sys.platform.startswith("win")))
    if sys.platform.startswith("win"):
        return [_strip_wrapping_quotes(arg) for arg in args]
    return args


def _decode_limited(data: bytes, *, limit_bytes: int, stream_name: str) -> str:
    if len(data) > limit_bytes:
        raise RuntimeError(
            f"Command hook {stream_name} exceeded {limit_bytes} byte limit"
        )
    return data.decode("utf-8", errors="ignore")


def _exit_code_2_decision(event_input: HookEventInput) -> HookDecisionType:
    if event_input.event_name in {
        HookEventName.STOP,
        HookEventName.SUBAGENT_STOP,
    }:
        return HookDecisionType.RETRY
    if event_input.event_name in {
        HookEventName.TASK_CREATED,
        HookEventName.TASK_COMPLETED,
        HookEventName.PRE_COMPACT,
        HookEventName.USER_PROMPT_SUBMIT,
        HookEventName.PRE_TOOL_USE,
        HookEventName.PERMISSION_REQUEST,
    }:
        return HookDecisionType.DENY
    return HookDecisionType.OBSERVE
