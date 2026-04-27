# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
from types import TracebackType
from typing import Optional

import httpx
from typer.testing import CliRunner

from relay_teams.interfaces.cli import app as cli_app
from relay_teams.interfaces.server import cli as server_cli
from relay_teams.interfaces.server.runtime_identity import (
    ServerHealthPayload,
    ServerRuntimeIdentity,
    SkillRegistrySanity,
)

runner = CliRunner()


class _FakeStartupInfo:
    def __init__(self) -> None:
        self.dwFlags = 0
        self.wShowWindow = 0


class _FakeServerHealthClient:
    def __init__(
        self,
        *,
        response: httpx.Response | None = None,
        error: httpx.HTTPError | None = None,
    ) -> None:
        self._response = response
        self._error = error
        self.requests: list[tuple[str, dict[str, str]]] = []

    async def __aenter__(self) -> _FakeServerHealthClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        _ = (exc_type, exc, traceback)

    async def get(self, url: str, *, headers: dict[str, str]) -> httpx.Response:
        self.requests.append((url, headers))
        if self._error is not None:
            raise self._error
        if self._response is None:
            raise RuntimeError("missing fake response")
        return self._response


class _FakeCliRequestJsonClient:
    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.requests: list[
            tuple[str, str, dict[str, str], dict[str, object] | None]
        ] = []

    async def __aenter__(self) -> _FakeCliRequestJsonClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        _ = (exc_type, exc, traceback)

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object] | None = None,
    ) -> httpx.Response:
        self.requests.append((method, url, headers, json))
        return self._response


def _runtime_identity(
    *,
    python_executable: str = "D:/workspace/agent_teams/.venv/Scripts/python.exe",
    package_root: str = "D:/workspace/agent_teams/src/relay_teams",
    config_dir: str = "C:/Users/test/.relay-teams",
    builtin_roles_dir: str = "D:/workspace/agent_teams/src/relay_teams/builtin/roles",
    builtin_skills_dir: str = "D:/workspace/agent_teams/src/relay_teams/builtin/skills",
) -> ServerRuntimeIdentity:
    return ServerRuntimeIdentity(
        python_executable=python_executable,
        package_root=package_root,
        config_dir=config_dir,
        builtin_roles_dir=builtin_roles_dir,
        builtin_skills_dir=builtin_skills_dir,
    )


def _health_payload(
    *,
    python_executable: str = "D:/workspace/agent_teams/.venv/Scripts/python.exe",
    package_root: str = "D:/workspace/agent_teams/src/relay_teams",
    config_dir: str = "C:/Users/test/.relay-teams",
    builtin_roles_dir: str = "D:/workspace/agent_teams/src/relay_teams/builtin/roles",
    builtin_skills_dir: str = "D:/workspace/agent_teams/src/relay_teams/builtin/skills",
) -> ServerHealthPayload:
    return ServerHealthPayload(
        status="ok",
        version="0.1.0",
        python_executable=python_executable,
        package_root=package_root,
        config_dir=config_dir,
        builtin_roles_dir=builtin_roles_dir,
        builtin_skills_dir=builtin_skills_dir,
        skill_registry_sanity=SkillRegistrySanity(
            builtin_skill_count=4,
            builtin_skill_names=(
                "deepresearch",
                "pptx-craft",
                "skill-installer",
                "time",
            ),
            has_builtin_deepresearch=True,
        ),
    )


def test_server_help_lists_stop_and_restart_commands() -> None:
    result = runner.invoke(cli_app.app, ["server", "--help"])

    assert result.exit_code == 0
    assert "start" in result.output
    assert "stop" in result.output
    assert "restart" in result.output


def test_is_process_running_windows_hides_powershell_console(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(
        command: list[str],
        check: bool,
        capture_output: bool,
        text: bool,
        creationflags: int,
        startupinfo: object,
    ) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["check"] = check
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["creationflags"] = creationflags
        captured["startupinfo"] = startupinfo
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(server_cli.sys, "platform", "win32")
    monkeypatch.setattr(server_cli.subprocess, "run", fake_run)
    monkeypatch.setattr(
        server_cli.subprocess, "STARTUPINFO", _FakeStartupInfo, raising=False
    )
    monkeypatch.setattr(
        server_cli.subprocess, "CREATE_NO_WINDOW", 0x8000000, raising=False
    )
    monkeypatch.setattr(
        server_cli.subprocess, "STARTF_USESHOWWINDOW", 0x1, raising=False
    )
    monkeypatch.setattr(server_cli.subprocess, "SW_HIDE", 0, raising=False)

    assert server_cli._is_process_running(321) is True
    startupinfo = captured["startupinfo"]
    assert isinstance(startupinfo, _FakeStartupInfo)
    assert captured == {
        "command": [
            "powershell",
            "-NoProfile",
            "-Command",
            "$null = Get-Process -Id 321 -ErrorAction Stop",
        ],
        "check": False,
        "capture_output": True,
        "text": True,
        "creationflags": 0x8000000,
        "startupinfo": startupinfo,
    }
    assert startupinfo.dwFlags == 0x1
    assert startupinfo.wShowWindow == 0


def test_stop_managed_server_force_uses_hidden_powershell_force_stop(
    monkeypatch,
    tmp_path: Path,
) -> None:
    process_file = tmp_path / "server-process.json"
    process = server_cli.ManagedServerProcess(pid=321, host="127.0.0.1", port=8012)
    process_file.write_text(process.model_dump_json(indent=2), encoding="utf-8")
    captured: dict[str, object] = {}
    running_states = iter((True, False))

    def fake_run(
        command: list[str],
        check: bool,
        capture_output: bool,
        text: bool,
        creationflags: int,
        startupinfo: object,
    ) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["check"] = check
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["creationflags"] = creationflags
        captured["startupinfo"] = startupinfo
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(
        server_cli,
        "get_server_process_file_path",
        lambda project_root=None: process_file,
    )
    monkeypatch.setattr(server_cli.sys, "platform", "win32")
    monkeypatch.setattr(
        server_cli,
        "_is_process_running",
        lambda pid: next(running_states),
    )
    monkeypatch.setattr(server_cli.subprocess, "run", fake_run)
    monkeypatch.setattr(
        server_cli.subprocess, "STARTUPINFO", _FakeStartupInfo, raising=False
    )
    monkeypatch.setattr(
        server_cli.subprocess, "CREATE_NO_WINDOW", 0x8000000, raising=False
    )
    monkeypatch.setattr(
        server_cli.subprocess, "STARTF_USESHOWWINDOW", 0x1, raising=False
    )
    monkeypatch.setattr(server_cli.subprocess, "SW_HIDE", 0, raising=False)

    stopped = server_cli._stop_managed_server(force=True, timeout_seconds=0.1)

    assert stopped == process
    startupinfo = captured["startupinfo"]
    assert isinstance(startupinfo, _FakeStartupInfo)
    assert captured == {
        "command": [
            "powershell",
            "-NoProfile",
            "-Command",
            "Stop-Process -Id 321 -Force",
        ],
        "check": False,
        "capture_output": True,
        "text": True,
        "creationflags": 0x8000000,
        "startupinfo": startupinfo,
    }
    assert startupinfo.dwFlags == 0x1
    assert startupinfo.wShowWindow == 0
    assert not process_file.exists()


def test_restart_reuses_existing_server_binding(monkeypatch, tmp_path: Path) -> None:
    process_file = tmp_path / "server-process.json"
    process = server_cli.ManagedServerProcess(pid=654, host="127.0.0.1", port=8123)
    process_file.write_text(process.model_dump_json(indent=2), encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_stop(
        force: bool,
        timeout_seconds: float = 10.0,
    ) -> server_cli.ManagedServerProcess | None:
        captured["force"] = force
        captured["timeout_seconds"] = timeout_seconds
        return process

    def fake_start_server_daemon(host: str, port: int) -> None:
        captured["host"] = host
        captured["port"] = port

    def fake_wait_until_healthy(
        base_url: str,
        timeout_seconds: float = 20.0,
    ) -> bool:
        captured["base_url"] = base_url
        captured["health_timeout_seconds"] = timeout_seconds
        return True

    def fake_wait_for_managed_server(
        host: str,
        port: int,
        timeout_seconds: float = 20.0,
    ) -> bool:
        captured["managed_host"] = host
        captured["managed_port"] = port
        captured["managed_timeout_seconds"] = timeout_seconds
        return True

    monkeypatch.setattr(
        server_cli,
        "get_server_process_file_path",
        lambda project_root=None: process_file,
    )
    monkeypatch.setattr(server_cli, "_stop_managed_server", fake_stop)
    monkeypatch.setattr(server_cli, "start_server_daemon", fake_start_server_daemon)
    monkeypatch.setattr(server_cli, "wait_until_healthy", fake_wait_until_healthy)
    monkeypatch.setattr(
        server_cli,
        "_wait_for_managed_server",
        fake_wait_for_managed_server,
    )
    monkeypatch.setattr(server_cli, "get_server_health", lambda base_url: None)

    server_cli.restart(host=None, port=None, force=True)

    assert captured == {
        "force": True,
        "timeout_seconds": 10.0,
        "host": "127.0.0.1",
        "port": 8123,
        "base_url": "http://127.0.0.1:8123",
        "health_timeout_seconds": 60.0,
        "managed_host": "127.0.0.1",
        "managed_port": 8123,
        "managed_timeout_seconds": 60.0,
    }


def test_start_spawns_daemon_and_waits_for_health(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_load(*, raise_on_invalid: bool) -> server_cli.ManagedServerProcess | None:
        return None

    def fake_start_daemon(host: str, port: int) -> None:
        captured["daemon_host"] = host
        captured["daemon_port"] = port

    def fake_wait_until_healthy(base_url: str, timeout_seconds: float = 20.0) -> bool:
        captured["health_url"] = base_url
        return True

    def fake_wait_for_managed_server(
        host: str, port: int, timeout_seconds: float = 20.0
    ) -> bool:
        return True

    process = server_cli.ManagedServerProcess(pid=999, host="127.0.0.1", port=8000)

    def fake_load_after_start(
        *, raise_on_invalid: bool
    ) -> server_cli.ManagedServerProcess | None:
        return process

    monkeypatch.setattr(server_cli, "_load_managed_server", fake_load)
    monkeypatch.setattr(server_cli, "start_server_daemon", fake_start_daemon)
    monkeypatch.setattr(server_cli, "wait_until_healthy", fake_wait_until_healthy)
    monkeypatch.setattr(
        server_cli, "_wait_for_managed_server", fake_wait_for_managed_server
    )
    monkeypatch.setattr(server_cli, "get_server_health", lambda base_url: None)

    server_cli.start(host="127.0.0.1", port=8000, daemon=True)

    # After daemon spawned, swap to return process info for the echo
    monkeypatch.setattr(server_cli, "_load_managed_server", fake_load_after_start)
    # Re-run to verify daemon was called with correct args
    assert captured["daemon_host"] == "127.0.0.1"
    assert captured["daemon_port"] == 8000
    assert captured["health_url"] == "http://127.0.0.1:8000"


def test_start_skips_if_already_running(monkeypatch) -> None:
    process = server_cli.ManagedServerProcess(pid=123, host="127.0.0.1", port=8000)

    monkeypatch.setattr(
        server_cli,
        "_load_managed_server",
        lambda *, raise_on_invalid=False: process,
    )
    monkeypatch.setattr(server_cli, "_is_process_running", lambda pid: True)
    monkeypatch.setattr(
        server_cli, "_get_current_runtime_identity", lambda: _runtime_identity()
    )
    monkeypatch.setattr(
        server_cli, "get_server_health", lambda base_url: _health_payload()
    )

    # Should return without error (server already running)
    server_cli.start(host="127.0.0.1", port=8000, daemon=True)


def test_start_skips_if_matching_unmanaged_server_is_already_running(
    monkeypatch,
) -> None:
    started: list[tuple[str, int]] = []

    monkeypatch.setattr(server_cli, "_load_managed_server", lambda **kwargs: None)
    monkeypatch.setattr(
        server_cli, "_get_current_runtime_identity", lambda: _runtime_identity()
    )
    monkeypatch.setattr(
        server_cli, "get_server_health", lambda base_url: _health_payload()
    )
    monkeypatch.setattr(
        server_cli,
        "start_server_daemon",
        lambda host, port: started.append((host, port)),
    )

    server_cli.start(host="127.0.0.1", port=8000, daemon=True)

    assert started == []


def test_health_check_host_resolves_wildcard_addresses() -> None:
    assert server_cli._health_check_host("0.0.0.0") == "127.0.0.1"
    assert server_cli._health_check_host("::") == "::1"
    assert server_cli._health_check_host("127.0.0.1") == "127.0.0.1"
    assert server_cli._health_check_host("10.0.1.5") == "10.0.1.5"


def test_server_bind_base_url_preserves_advertised_bind_host() -> None:
    assert server_cli._server_bind_base_url("0.0.0.0", 8000) == "http://0.0.0.0:8000"
    assert server_cli._server_bind_base_url("::", 8000) == "http://[::]:8000"
    assert (
        server_cli._server_bind_base_url("127.0.0.1", 8000) == "http://127.0.0.1:8000"
    )


def test_server_cli_get_server_health_async_uses_async_http_client(monkeypatch) -> None:
    response = httpx.Response(
        200,
        text=_health_payload().model_dump_json(),
        request=httpx.Request("GET", "http://127.0.0.1:8000/api/system/health"),
    )
    fake_client = _FakeServerHealthClient(response=response)
    captured_kwargs: dict[str, object] = {}

    def fake_create_async_http_client(**kwargs: object) -> _FakeServerHealthClient:
        captured_kwargs.update(kwargs)
        return fake_client

    monkeypatch.setattr(
        server_cli,
        "create_async_http_client",
        fake_create_async_http_client,
    )

    health = asyncio.run(server_cli.get_server_health_async("http://127.0.0.1:8000/"))

    assert health is not None
    assert health.status == "ok"
    assert fake_client.requests == [
        (
            "http://127.0.0.1:8000/api/system/health",
            {"Accept": "application/json"},
        )
    ]
    assert captured_kwargs["timeout_seconds"] == 1.5
    assert captured_kwargs["connect_timeout_seconds"] == 1.5


def test_cli_request_json_async_applies_timeout_to_connect_phase(monkeypatch) -> None:
    response = httpx.Response(
        200,
        json={"status": "ok"},
        request=httpx.Request("GET", "http://127.0.0.1:8000/api/system/health"),
    )
    fake_client = _FakeCliRequestJsonClient(response)
    captured_kwargs: dict[str, object] = {}

    def fake_create_async_http_client(**kwargs: object) -> _FakeCliRequestJsonClient:
        captured_kwargs.update(kwargs)
        return fake_client

    monkeypatch.setattr(
        cli_app,
        "create_async_http_client",
        fake_create_async_http_client,
    )

    payload = asyncio.run(
        cli_app._request_json_async(
            base_url="http://127.0.0.1:8000",
            method="GET",
            path="/api/system/health",
            timeout_seconds=1.5,
        )
    )

    assert payload == {"status": "ok"}
    assert captured_kwargs["timeout_seconds"] == 1.5
    assert captured_kwargs["connect_timeout_seconds"] == 1.5


def test_server_cli_get_server_health_async_returns_none_on_http_error(
    monkeypatch,
) -> None:
    request = httpx.Request("GET", "http://127.0.0.1:8000/api/system/health")
    fake_client = _FakeServerHealthClient(
        error=httpx.ConnectError("offline", request=request)
    )

    def fake_create_async_http_client(**kwargs: object) -> _FakeServerHealthClient:
        _ = kwargs
        return fake_client

    monkeypatch.setattr(
        server_cli,
        "create_async_http_client",
        fake_create_async_http_client,
    )

    assert (
        asyncio.run(server_cli.get_server_health_async("http://127.0.0.1:8000")) is None
    )


def test_server_cli_wait_until_healthy_async_uses_async_health(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_get_server_health_async(
        base_url: str,
    ) -> Optional[ServerHealthPayload]:
        calls.append(base_url)
        return _health_payload()

    monkeypatch.setattr(
        server_cli,
        "get_server_health_async",
        fake_get_server_health_async,
    )

    result = asyncio.run(
        server_cli._wait_until_healthy_async(
            "http://127.0.0.1:8000",
            timeout_seconds=0.1,
        )
    )

    assert result is True
    assert calls == ["http://127.0.0.1:8000"]


def test_restart_fails_for_unmanaged_healthy_server(monkeypatch) -> None:
    def fake_stop(
        force: bool,
        timeout_seconds: float = 10.0,
    ) -> server_cli.ManagedServerProcess | None:
        _ = (force, timeout_seconds)
        return None

    monkeypatch.setattr(server_cli, "_stop_managed_server", fake_stop)
    monkeypatch.setattr(
        server_cli, "_get_current_runtime_identity", lambda: _runtime_identity()
    )
    monkeypatch.setattr(
        server_cli,
        "get_server_health",
        lambda base_url: _health_payload(),
    )

    try:
        server_cli.restart(host=None, port=None, force=False)
    except RuntimeError as exc:
        assert "not managed by this CLI" in str(exc)
    else:
        raise AssertionError("restart should reject unmanaged healthy servers")


def test_start_fails_for_mismatched_live_server_runtime(monkeypatch) -> None:
    monkeypatch.setattr(server_cli, "_load_managed_server", lambda **kwargs: None)
    monkeypatch.setattr(
        server_cli, "_get_current_runtime_identity", lambda: _runtime_identity()
    )
    monkeypatch.setattr(
        server_cli,
        "get_server_health",
        lambda base_url: _health_payload(
            python_executable="C:/Python312/python.exe",
            package_root="C:/Users/test/AppData/Local/Programs/Python/Python312/Lib/site-packages/relay_teams",
        ),
    )

    try:
        server_cli.start(host="127.0.0.1", port=8000, daemon=True)
    except RuntimeError as exc:
        assert "runtime mismatch" in str(exc)
        assert "Stop the conflicting server first" in str(exc)
    else:
        raise AssertionError("start should reject mismatched live runtimes")


def test_start_fails_for_mismatched_live_builtin_roles_dir(monkeypatch) -> None:
    monkeypatch.setattr(server_cli, "_load_managed_server", lambda **kwargs: None)
    monkeypatch.setattr(
        server_cli, "_get_current_runtime_identity", lambda: _runtime_identity()
    )
    monkeypatch.setattr(
        server_cli,
        "get_server_health",
        lambda base_url: _health_payload(
            builtin_roles_dir="D:/workspace/other/src/relay_teams/builtin/roles"
        ),
    )

    try:
        server_cli.start(host="127.0.0.1", port=8000, daemon=True)
    except RuntimeError as exc:
        assert "runtime mismatch" in str(exc)
        assert "builtin roles" in str(exc)
        assert "Stop the conflicting server first" in str(exc)
    else:
        raise AssertionError("start should reject builtin role path mismatches")


def test_root_cli_autostart_rejects_mismatched_local_runtime(monkeypatch) -> None:
    started: list[tuple[str, int]] = []

    monkeypatch.setattr(
        cli_app,
        "_get_server_health",
        lambda base_url: _health_payload(
            python_executable="C:/Python312/python.exe",
            package_root="C:/Users/test/AppData/Local/Programs/Python/Python312/Lib/site-packages/relay_teams",
        ),
    )
    monkeypatch.setattr(
        cli_app,
        "build_server_runtime_identity",
        lambda *, config_dir=None: _runtime_identity(),
    )
    monkeypatch.setattr(
        cli_app,
        "_start_server_daemon",
        lambda host, port: started.append((host, port)),
    )

    try:
        cli_app._auto_start_if_needed("http://127.0.0.1:8000", autostart=True)
    except RuntimeError as exc:
        assert "runtime mismatch" in str(exc)
        assert "Stop the conflicting server first" in str(exc)
    else:
        raise AssertionError("root CLI should reject mismatched local runtimes")

    assert started == []


def test_root_cli_autostart_rejects_mismatched_builtin_roles_dir(monkeypatch) -> None:
    started: list[tuple[str, int]] = []

    monkeypatch.setattr(
        cli_app,
        "_get_server_health",
        lambda base_url: _health_payload(
            builtin_roles_dir="D:/workspace/other/src/relay_teams/builtin/roles"
        ),
    )
    monkeypatch.setattr(
        cli_app,
        "build_server_runtime_identity",
        lambda *, config_dir=None: _runtime_identity(),
    )
    monkeypatch.setattr(
        cli_app,
        "_start_server_daemon",
        lambda host, port: started.append((host, port)),
    )

    try:
        cli_app._auto_start_if_needed("http://127.0.0.1:8000", autostart=True)
    except RuntimeError as exc:
        assert "runtime mismatch" in str(exc)
        assert "builtin roles" in str(exc)
        assert "Stop the conflicting server first" in str(exc)
    else:
        raise AssertionError("root CLI should reject builtin role path mismatches")

    assert started == []


def test_root_cli_wait_until_healthy_async_uses_async_health(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_get_server_health_async(
        base_url: str,
    ) -> Optional[ServerHealthPayload]:
        calls.append(base_url)
        return _health_payload()

    monkeypatch.setattr(
        cli_app,
        "_get_server_health_async",
        fake_get_server_health_async,
    )

    result = asyncio.run(
        cli_app._wait_until_healthy_async(
            "http://127.0.0.1:8000",
            timeout_seconds=0.1,
        )
    )

    assert result is True
    assert calls == ["http://127.0.0.1:8000"]
