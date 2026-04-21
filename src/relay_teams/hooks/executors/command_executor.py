from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
import sys

from relay_teams.hooks.hook_event_models import HookEventInput
from relay_teams.hooks.hook_models import HookDecision, HookHandlerConfig


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
        args = shlex.split(command, posix=(not sys.platform.startswith("win")))
        if sys.platform.startswith("win"):
            args = [_strip_wrapping_quotes(arg) for arg in args]
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
            message = stderr.decode("utf-8", errors="ignore").strip() or (
                f"Command hook exited with status {return_code}"
            )
            raise RuntimeError(message)
        raw_stdout = stdout.decode("utf-8", errors="ignore").strip()
        if not raw_stdout:
            raise ValueError("Command hook returned no JSON payload")
        return HookDecision.model_validate(json.loads(raw_stdout))


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value
