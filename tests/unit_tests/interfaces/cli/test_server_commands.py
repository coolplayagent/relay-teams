# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import subprocess

from typer.testing import CliRunner

from agent_teams.interfaces.cli import app as cli_app
from agent_teams.interfaces.server import cli as server_cli

runner = CliRunner()


class _FakeStartupInfo:
    def __init__(self) -> None:
        self.dwFlags = 0
        self.wShowWindow = 0


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


def test_restart_fails_for_unmanaged_healthy_server(monkeypatch) -> None:
    def fake_stop(
        force: bool,
        timeout_seconds: float = 10.0,
    ) -> server_cli.ManagedServerProcess | None:
        _ = (force, timeout_seconds)
        return None

    def fake_is_server_healthy(base_url: str) -> bool:
        assert base_url == "http://127.0.0.1:8000"
        return True

    monkeypatch.setattr(server_cli, "_stop_managed_server", fake_stop)
    monkeypatch.setattr(server_cli, "is_server_healthy", fake_is_server_healthy)

    try:
        server_cli.restart(host=None, port=None, force=False)
    except RuntimeError as exc:
        assert "not managed by this CLI" in str(exc)
    else:
        raise AssertionError("restart should reject unmanaged healthy servers")
