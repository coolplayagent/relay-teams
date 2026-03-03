from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from dataclasses import dataclass

from agent_teams.acp.session_client import SessionHandle, SessionInitSpec, TurnInput, TurnOutput
from agent_teams.core.acp_config import AcpProviderConfig, AcpTimeoutsConfig


@dataclass
class _StdioSessionState:
    process: asyncio.subprocess.Process
    lock: asyncio.Lock


_DEFAULT_METHODS_BY_PROTOCOL: dict[str, dict[str, str]] = {
    "legacy_session_v1": {
        "open": "session.init",
        "turn": "session.run_turn",
        "close": "session.close",
    },
    "opencode_v1": {
        "initialize": "initialize",
        "open": "session/new",
        "turn": "session/prompt",
    },
}
_AUTO_PROTOCOL_CANDIDATES: tuple[str, ...] = ("opencode_v1", "legacy_session_v1")


class StdioAcpSessionClient:
    def __init__(self, provider: AcpProviderConfig, timeouts: AcpTimeoutsConfig) -> None:
        self._provider = provider
        self._timeouts = timeouts
        self._request_seq = 0
        self._sessions: dict[str, _StdioSessionState] = {}

    async def open(self, spec: SessionInitSpec) -> SessionHandle:
        if self._provider.protocol == "auto":
            last_error: Exception | None = None
            for protocol in _AUTO_PROTOCOL_CANDIDATES:
                process = await self._spawn_process()
                state = _StdioSessionState(process=process, lock=asyncio.Lock())
                try:
                    remote_session_id = await self._open_with_protocol(
                        state=state, spec=spec, protocol=protocol
                    )
                except Exception as exc:
                    last_error = exc
                    await _terminate_process(state.process)
                    continue

                self._sessions[remote_session_id] = state
                metadata = {"protocol": protocol}
                if protocol == "opencode_v1":
                    metadata["role_context"] = _compose_opencode_instructions(spec)
                return SessionHandle(
                    session_id=remote_session_id,
                    instance_id=spec.instance_id,
                    metadata=metadata,
                )
            raise RuntimeError(
                "ACP protocol auto-detection failed for provider "
                f"'{self._provider.command}'. Last error: {last_error}"
            )

        process = await self._spawn_process()
        state = _StdioSessionState(process=process, lock=asyncio.Lock())
        protocol = self._provider.protocol
        remote_session_id = await self._open_with_protocol(
            state=state, spec=spec, protocol=protocol
        )
        self._sessions[remote_session_id] = state
        metadata = {"protocol": protocol}
        if protocol == "opencode_v1":
            metadata["role_context"] = _compose_opencode_instructions(spec)
        return SessionHandle(
            session_id=remote_session_id,
            instance_id=spec.instance_id,
            metadata=metadata,
        )

    async def run_turn(self, handle: SessionHandle, turn: TurnInput) -> TurnOutput:
        state = self._sessions.get(handle.session_id)
        if state is None:
            raise RuntimeError(
                f"ACP session not found for session_id={handle.session_id}"
            )
        protocol = _resolve_handle_protocol(handle, self._provider.protocol)
        methods = _build_methods(self._provider, protocol)
        if protocol == "opencode_v1":
            role_context = handle.metadata.get("role_context")
            prompt_parts: list[dict[str, str]] = []
            if isinstance(role_context, str) and role_context.strip():
                prompt_parts.append(
                    {
                        "type": "text",
                        "text": "Role Context:\n" + role_context,
                    }
                )
            prompt_parts.append({"type": "text", "text": turn.user_prompt})
            result, notifications = await self._rpc(
                state,
                method=methods["turn"],
                params={
                    "sessionId": handle.session_id,
                    "prompt": prompt_parts,
                },
                timeout_ms=self._timeouts.turn_ms,
            )
            text = _extract_opencode_text(notifications)
            if not text:
                text = _extract_text(result)
            tool_calls = _extract_opencode_tool_calls(notifications)
            tool_results = _extract_opencode_tool_results(notifications)
        else:
            result, _ = await self._rpc(
                state,
                method=methods["turn"],
                params={"session_id": handle.session_id, "input": turn.user_prompt},
                timeout_ms=self._timeouts.turn_ms,
            )
            text = _extract_text(result)
            tool_calls = _extract_items(result, "tool_calls")
            tool_results = _extract_items(result, "tool_results")
        return TurnOutput(text=text, tool_calls=tool_calls, tool_results=tool_results)

    async def close(self, handle: SessionHandle) -> None:
        state = self._sessions.pop(handle.session_id, None)
        if state is None:
            return
        protocol = _resolve_handle_protocol(handle, self._provider.protocol)
        methods = _build_methods(self._provider, protocol)
        close_method = methods.get("close")
        if close_method:
            try:
                params: dict[str, object]
                if protocol == "opencode_v1":
                    params = {"sessionId": handle.session_id}
                else:
                    params = {"session_id": handle.session_id}
                await self._rpc(
                    state,
                    method=close_method,
                    params=params,
                    timeout_ms=2_000,
                )
            except Exception:
                pass
        await _terminate_process(state.process)

    async def _open_with_protocol(
        self, *, state: _StdioSessionState, spec: SessionInitSpec, protocol: str
    ) -> str:
        methods = _build_methods(self._provider, protocol)
        if protocol == "opencode_v1":
            await self._rpc(
                state,
                method=methods["initialize"],
                params={"protocolVersion": 1},
                timeout_ms=self._timeouts.session_init_ms,
            )
            open_result, _ = await self._rpc(
                state,
                method=methods["open"],
                params={
                    "cwd": self._provider.cwd or os.getcwd(),
                    "mcpServers": _build_opencode_mcp_servers(
                        mcp_servers=spec.mcp_servers,
                        skills=spec.skills,
                        cwd=self._provider.cwd or os.getcwd(),
                    ),
                    "instructions": _compose_opencode_instructions(spec),
                },
                timeout_ms=self._timeouts.session_init_ms,
            )
            return str(open_result.get("sessionId") or spec.instance_id)

        open_result, _ = await self._rpc(
            state,
            method=methods["open"],
            params={
                "run_id": spec.run_id,
                "trace_id": spec.trace_id,
                "task_id": spec.task_id,
                "session_id": spec.session_id,
                "instance_id": spec.instance_id,
                "role_id": spec.role_id,
                "system_prompt": spec.system_prompt,
                "tools": spec.tools,
                "skills": spec.skills,
                "mcp_servers": spec.mcp_servers,
            },
            timeout_ms=self._timeouts.session_init_ms,
        )
        return str(open_result.get("session_id") or spec.instance_id)

    async def _spawn_process(self) -> asyncio.subprocess.Process:
        env = os.environ.copy()
        env.update(self._provider.env)
        command, args = _normalize_command_for_platform(
            self._provider.command, self._provider.args
        )
        try:
            return await asyncio.create_subprocess_exec(
                command,
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"Failed to start ACP process '{self._provider.command}'. "
                "Check .agent_teams/acp.json provider command."
            ) from exc

    async def _rpc(
        self,
        state: _StdioSessionState,
        *,
        method: str,
        params: dict[str, object],
        timeout_ms: int,
    ) -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
        self._request_seq += 1
        request_id = self._request_seq
        payload: dict[str, object] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        process = state.process
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("ACP process stdio is not available")

        async with state.lock:
            process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            await process.stdin.drain()
            deadline = asyncio.get_event_loop().time() + (timeout_ms / 1000)
            notifications: list[dict[str, object]] = []
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    raise TimeoutError(
                        f"ACP method '{method}' timed out after {timeout_ms}ms"
                    )
                line = await asyncio.wait_for(process.stdout.readline(), timeout=remaining)
                if not line:
                    stderr_tail = await _read_stderr_tail(process)
                    raise RuntimeError(
                        f"ACP process closed while waiting for method '{method}'. "
                        f"stderr: {stderr_tail}"
                    )

                try:
                    response = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"Invalid ACP JSON response for method '{method}': {line!r}"
                    ) from exc

                if response.get("id") == request_id:
                    if "error" in response and response["error"]:
                        raise RuntimeError(
                            f"ACP method '{method}' failed: {response['error']}"
                        )
                    result = response.get("result")
                    if isinstance(result, dict):
                        return result, tuple(notifications)
                    if isinstance(result, str):
                        return {"text": result}, tuple(notifications)
                    if isinstance(response, dict):
                        return response, tuple(notifications)
                    return {"text": str(result)}, tuple(notifications)

                if isinstance(response, dict) and "method" in response:
                    notifications.append(response)


def _extract_text(result: dict[str, object]) -> str:
    for key in ("text", "output", "content", "message"):
        value = result.get(key)
        if isinstance(value, str):
            return value
    return json.dumps(result, ensure_ascii=False)


def _extract_items(
    result: dict[str, object], key: str
) -> tuple[dict[str, object], ...]:
    value = result.get(key)
    if not isinstance(value, list):
        return ()
    normalized: list[dict[str, object]] = []
    for item in value:
        if isinstance(item, dict):
            normalized.append(item)
    return tuple(normalized)


def _extract_opencode_text(notifications: tuple[dict[str, object], ...]) -> str:
    chunks: list[str] = []
    for event in notifications:
        if event.get("method") != "session/update":
            continue
        params = event.get("params")
        if not isinstance(params, dict):
            continue
        update = params.get("update")
        if not isinstance(update, dict):
            continue
        if update.get("sessionUpdate") != "agent_message_chunk":
            continue
        content = update.get("content")
        if not isinstance(content, dict):
            continue
        if content.get("type") != "text":
            continue
        text = content.get("text")
        if isinstance(text, str):
            chunks.append(text)
    return "".join(chunks).strip()


def _extract_opencode_tool_calls(
    notifications: tuple[dict[str, object], ...]
) -> tuple[dict[str, object], ...]:
    calls: list[dict[str, object]] = []
    for event in notifications:
        if event.get("method") != "session/update":
            continue
        params = event.get("params")
        if not isinstance(params, dict):
            continue
        update = params.get("update")
        if not isinstance(update, dict):
            continue
        if update.get("sessionUpdate") != "tool_call":
            continue
        calls.append(update)
    return tuple(calls)


def _extract_opencode_tool_results(
    notifications: tuple[dict[str, object], ...]
) -> tuple[dict[str, object], ...]:
    results: list[dict[str, object]] = []
    for event in notifications:
        if event.get("method") != "session/update":
            continue
        params = event.get("params")
        if not isinstance(params, dict):
            continue
        update = params.get("update")
        if not isinstance(update, dict):
            continue
        if update.get("sessionUpdate") != "tool_call_update":
            continue
        if update.get("status") != "completed":
            continue
        results.append(update)
    return tuple(results)


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except TimeoutError:
        process.kill()
        await process.wait()


async def _read_stderr_tail(process: asyncio.subprocess.Process) -> str:
    if process.stderr is None:
        return ""
    try:
        chunk = await asyncio.wait_for(process.stderr.read(1024), timeout=0.2)
    except TimeoutError:
        return ""
    if not chunk:
        return ""
    return chunk.decode("utf-8", errors="replace").strip()


def _normalize_command_for_platform(
    command: str, args: tuple[str, ...]
) -> tuple[str, tuple[str, ...]]:
    if os.name != "nt":
        return command, args

    suffix = Path(command).suffix.lower()
    needs_powershell_wrapper = suffix in {".ps1", ".cmd", ".bat"} or suffix == ""
    if not needs_powershell_wrapper:
        return command, args

    ps_script = _to_powershell_invocation(command, args)
    return (
        "powershell.exe",
        ("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script),
    )


def _to_powershell_invocation(command: str, args: tuple[str, ...]) -> str:
    tokens = [command, *args]
    quoted = []
    for token in tokens:
        escaped = token.replace("'", "''")
        quoted.append(f"'{escaped}'")
    return "& " + " ".join(quoted)


def _build_methods(provider: AcpProviderConfig, protocol: str) -> dict[str, str]:
    defaults = dict(_DEFAULT_METHODS_BY_PROTOCOL.get(protocol, {}))
    defaults.update(provider.methods)
    return defaults


def _resolve_handle_protocol(handle: SessionHandle, configured_protocol: str) -> str:
    metadata_protocol = handle.metadata.get("protocol")
    if isinstance(metadata_protocol, str) and metadata_protocol:
        return metadata_protocol
    if configured_protocol == "auto":
        return _AUTO_PROTOCOL_CANDIDATES[0]
    return configured_protocol


def _compose_opencode_instructions(spec: SessionInitSpec) -> str:
    lines = [spec.system_prompt.strip()]

    if spec.tools:
        names = [str(item.get("name", "")) for item in spec.tools]
        names = [name for name in names if name]
        if names:
            lines.append("Allowed role tools: " + ", ".join(names))

    if spec.skills:
        skill_lines: list[str] = []
        for item in spec.skills:
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            description = str(item.get("description", "")).strip()
            skill_file = str(item.get("skill_file", "")).strip()
            scripts = item.get("scripts")
            resources = item.get("resources")

            parts = [f"- {name}"]
            if description:
                parts.append(f"({description})")
            skill_lines.append(" ".join(parts))
            if skill_file:
                skill_lines.append("  skill_file: " + skill_file)
            if isinstance(scripts, (list, tuple)) and scripts:
                script_names: list[str] = []
                for script in scripts:
                    if isinstance(script, dict):
                        script_name = str(script.get("name", "")).strip()
                        script_path = str(script.get("path", "")).strip()
                        if script_name and script_path:
                            script_names.append(f"{script_name} ({script_path})")
                            continue
                        if script_name:
                            script_names.append(script_name)
                            continue
                    if isinstance(script, str) and script.strip():
                        script_names.append(script.strip())
                if script_names:
                    skill_lines.append("  scripts: " + ", ".join(script_names))
            if isinstance(resources, (list, tuple)) and resources:
                resource_names: list[str] = []
                for resource in resources:
                    if isinstance(resource, dict):
                        resource_name = str(resource.get("name", "")).strip()
                        resource_path = str(resource.get("path", "")).strip()
                        if resource_name and resource_path:
                            resource_names.append(f"{resource_name} ({resource_path})")
                            continue
                        if resource_name:
                            resource_names.append(resource_name)
                            continue
                    if isinstance(resource, str) and resource.strip():
                        resource_names.append(resource.strip())
                if resource_names:
                    skill_lines.append("  resources: " + ", ".join(resource_names))
        if skill_lines:
            lines.append("Enabled skills (catalog):\n" + "\n".join(skill_lines))
            lines.append(
                "Load skills progressively: list and inspect first, then load only the "
                "specific skill details needed for the current step."
            )

    if spec.mcp_servers:
        names = [str(item.get("name", "")) for item in spec.mcp_servers]
        names = [name for name in names if name]
        if names:
            lines.append("Enabled MCP servers: " + ", ".join(names))

    return "\n\n".join(lines)


def _build_opencode_mcp_servers(
    *,
    mcp_servers: tuple[dict[str, object], ...],
    skills: tuple[dict[str, object], ...],
    cwd: str,
) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for item in mcp_servers:
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        raw_config = item.get("config")
        config = _unwrap_manifest_mcp_config(name, raw_config)
        if config is None:
            continue
        opencode_config = _normalize_mcp_server_for_opencode(name, config)
        if opencode_config:
            normalized.append(opencode_config)

    skills_server = _build_skills_mcp_server(skills=skills, cwd=cwd)
    if skills_server and not any(
        isinstance(server.get("name"), str)
        and server["name"] == str(skills_server.get("name"))
        for server in normalized
    ):
        normalized.append(skills_server)
    return normalized


def _build_skills_mcp_server(
    *, skills: tuple[dict[str, object], ...], cwd: str
) -> dict[str, object] | None:
    enabled_skill_names: list[str] = []
    for item in skills:
        name = str(item.get("name", "")).strip()
        if name and name not in enabled_skill_names:
            enabled_skill_names.append(name)
    if not enabled_skill_names:
        return None

    config_dir = str((Path(cwd) / ".agent_teams").resolve())
    args: list[str] = ["-m", "agent_teams", "skills-mcp", "--config-dir", config_dir]
    for skill_name in enabled_skill_names:
        args.extend(["--allowed-skill", skill_name])

    return {
        "name": "skills-mcp",
        "command": sys.executable,
        "args": args,
        "env": [],
    }


def _unwrap_manifest_mcp_config(
    name: str, raw_config: object
) -> dict[str, object] | None:
    if not isinstance(raw_config, dict):
        return None

    # Manifest may store MCP server config as {"mcpServers": {name: cfg}}.
    wrapped = raw_config.get("mcpServers")
    if isinstance(wrapped, dict):
        by_name = wrapped.get(name)
        if isinstance(by_name, dict):
            return by_name

        # Fallback for single-entry wrapped config when name mismatch occurs.
        if len(wrapped) == 1:
            only_value = next(iter(wrapped.values()))
            if isinstance(only_value, dict):
                return only_value

    return raw_config


def _normalize_mcp_server_for_opencode(
    name: str, config: dict[str, object]
) -> dict[str, object] | None:
    command = config.get("command")
    if isinstance(command, str) and command.strip():
        args = _normalize_string_list(config.get("args"))
        env = _normalize_env_pairs(config.get("env"))
        return {"name": name, "command": command, "args": args, "env": env}

    url = config.get("url")
    if isinstance(url, str) and url.strip():
        server: dict[str, object] = {"name": name, "url": url}
        server_type = config.get("type")
        if isinstance(server_type, str) and server_type in {"http", "sse"}:
            server["type"] = server_type
        headers = _normalize_env_pairs(config.get("headers"))
        if headers:
            server["headers"] = headers
        return server

    return None


def _normalize_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            result.append(item)
    return result


def _normalize_env_pairs(value: object) -> list[dict[str, str]]:
    if isinstance(value, dict):
        pairs: list[dict[str, str]] = []
        for key, raw in value.items():
            pairs.append({"name": str(key), "value": str(raw)})
        return pairs

    if isinstance(value, list):
        pairs = []
        for item in value:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            raw_value = item.get("value")
            if isinstance(name, str):
                pairs.append(
                    {
                        "name": name,
                        "value": "" if raw_value is None else str(raw_value),
                    }
                )
        return pairs

    return []


