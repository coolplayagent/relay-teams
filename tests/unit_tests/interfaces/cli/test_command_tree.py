# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import re

from typer.testing import CliRunner

from relay_teams.interfaces.cli import app as cli_app

runner = CliRunner()
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _normalized_output(text: str) -> str:
    return " ".join(_ANSI_ESCAPE_RE.sub("", text).split())


def _workspace_response(
    root_path: Path,
    *,
    workspace_id: str = "workspace-1",
) -> dict[str, object]:
    return {
        "workspace": {
            "workspace_id": workspace_id,
            "root_path": str(root_path.resolve()),
        }
    }


def test_root_message_runs_single_prompt(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    streamed: dict[str, object] = {}

    def fake_autostart(base_url: str, autostart: bool) -> None:
        streamed["base_url"] = base_url
        streamed["autostart"] = autostart

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object] | list[object]:
        _ = (base_url, timeout_seconds)
        calls.append((method, path, payload))
        if path == "/api/workspaces/pick":
            return _workspace_response(tmp_path)
        if path == "/api/sessions":
            return {"session_id": "session-1"}
        if path == "/api/runs":
            return {"run_id": "run-1"}
        raise AssertionError(f"unexpected path: {path}")

    def fake_stream(base_url: str, run_id: str, debug: bool) -> None:
        streamed["stream_base_url"] = base_url
        streamed["run_id"] = run_id
        streamed["debug"] = debug

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)
    monkeypatch.setattr(cli_app, "_stream_events", fake_stream)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app.app, ["-m", "hello"])

    assert result.exit_code == 0
    assert calls == [
        (
            "POST",
            "/api/workspaces/pick",
            {"root_path": str(tmp_path.resolve())},
        ),
        ("POST", "/api/sessions", {"workspace_id": "workspace-1"}),
        (
            "POST",
            "/api/runs",
            {
                "session_id": "session-1",
                "input": [{"kind": "text", "text": "hello"}],
                "execution_mode": "ai",
                "yolo": True,
            },
        ),
    ]
    assert streamed == {
        "base_url": cli_app.DEFAULT_BASE_URL,
        "autostart": True,
        "stream_base_url": cli_app.DEFAULT_BASE_URL,
        "run_id": "run-1",
        "debug": False,
    }


def test_root_message_supports_workspace_selection(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    streamed: dict[str, object] = {}

    def fake_autostart(base_url: str, autostart: bool) -> None:
        _ = (base_url, autostart)

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object] | list[object]:
        _ = (base_url, timeout_seconds)
        calls.append((method, path, payload))
        if path == "/api/workspaces/pick":
            return _workspace_response(tmp_path)
        if path == "/api/sessions":
            return {"session_id": "session-1"}
        if path == "/api/runs":
            return {"run_id": "run-1"}
        raise AssertionError(f"unexpected path: {path}")

    def fake_stream(base_url: str, run_id: str, debug: bool) -> None:
        streamed["run_id"] = run_id
        streamed["debug"] = debug

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)
    monkeypatch.setattr(cli_app, "_stream_events", fake_stream)

    result = runner.invoke(cli_app.app, ["-m", "hello", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert calls == [
        (
            "POST",
            "/api/workspaces/pick",
            {"root_path": str(tmp_path.resolve())},
        ),
        ("POST", "/api/sessions", {"workspace_id": "workspace-1"}),
        (
            "POST",
            "/api/runs",
            {
                "session_id": "session-1",
                "input": [{"kind": "text", "text": "hello"}],
                "execution_mode": "ai",
                "yolo": True,
            },
        ),
    ]
    assert streamed == {"run_id": "run-1", "debug": False}


def test_root_message_supports_normal_role_selection(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_autostart(base_url: str, autostart: bool) -> None:
        _ = (base_url, autostart)

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object] | list[object]:
        _ = (base_url, timeout_seconds)
        calls.append((method, path, payload))
        if path == "/api/workspaces/pick":
            return _workspace_response(tmp_path)
        if path == "/api/sessions":
            return {"session_id": "session-1"}
        if path == "/api/sessions/session-1/topology":
            return {
                "session_id": "session-1",
                "workspace_id": "workspace-1",
                "metadata": {},
                "session_mode": "normal",
                "normal_root_role_id": "Crafter",
                "orchestration_preset_id": None,
            }
        if path == "/api/runs":
            return {"run_id": "run-1"}
        raise AssertionError(f"unexpected path: {path}")

    def fake_stream(base_url: str, run_id: str, debug: bool) -> None:
        _ = (base_url, run_id, debug)

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)
    monkeypatch.setattr(cli_app, "_stream_events", fake_stream)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app.app, ["-m", "hello", "--role", "Crafter"])

    assert result.exit_code == 0
    assert calls == [
        (
            "POST",
            "/api/workspaces/pick",
            {"root_path": str(tmp_path.resolve())},
        ),
        ("POST", "/api/sessions", {"workspace_id": "workspace-1"}),
        (
            "PATCH",
            "/api/sessions/session-1/topology",
            {
                "session_mode": "normal",
                "normal_root_role_id": "Crafter",
                "orchestration_preset_id": None,
            },
        ),
        (
            "POST",
            "/api/runs",
            {
                "session_id": "session-1",
                "input": [{"kind": "text", "text": "hello"}],
                "execution_mode": "ai",
                "yolo": True,
            },
        ),
    ]


def test_root_message_supports_orchestration_mode(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_autostart(base_url: str, autostart: bool) -> None:
        _ = (base_url, autostart)

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object] | list[object]:
        _ = (base_url, timeout_seconds)
        calls.append((method, path, payload))
        if path == "/api/workspaces/pick":
            return _workspace_response(tmp_path)
        if path == "/api/sessions":
            return {"session_id": "session-1"}
        if path == "/api/sessions/session-1/topology":
            return {
                "session_id": "session-1",
                "workspace_id": "workspace-1",
                "metadata": {},
                "session_mode": "orchestration",
                "orchestration_preset_id": "default",
            }
        if path == "/api/runs":
            return {"run_id": "run-1"}
        raise AssertionError(f"unexpected path: {path}")

    def fake_stream(base_url: str, run_id: str, debug: bool) -> None:
        _ = (base_url, run_id, debug)

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)
    monkeypatch.setattr(cli_app, "_stream_events", fake_stream)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        cli_app.app,
        [
            "-m",
            "hello",
            "--mode",
            "orchestration",
            "--orchestration",
            "default",
        ],
    )

    assert result.exit_code == 0
    assert calls == [
        (
            "POST",
            "/api/workspaces/pick",
            {"root_path": str(tmp_path.resolve())},
        ),
        ("POST", "/api/sessions", {"workspace_id": "workspace-1"}),
        (
            "PATCH",
            "/api/sessions/session-1/topology",
            {
                "session_mode": "orchestration",
                "orchestration_preset_id": "default",
            },
        ),
        (
            "POST",
            "/api/runs",
            {
                "session_id": "session-1",
                "input": [{"kind": "text", "text": "hello"}],
                "execution_mode": "ai",
                "yolo": True,
            },
        ),
    ]


def test_root_message_allows_no_yolo_override(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_autostart(base_url: str, autostart: bool) -> None:
        _ = (base_url, autostart)

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object] | list[object]:
        _ = (base_url, timeout_seconds)
        calls.append((method, path, payload))
        if path == "/api/workspaces/pick":
            return _workspace_response(tmp_path)
        if path == "/api/sessions":
            return {"session_id": "session-1"}
        if path == "/api/runs":
            return {"run_id": "run-1"}
        raise AssertionError(f"unexpected path: {path}")

    def fake_stream(base_url: str, run_id: str, debug: bool) -> None:
        _ = (base_url, run_id, debug)

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)
    monkeypatch.setattr(cli_app, "_stream_events", fake_stream)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        cli_app.app,
        ["-m", "hello", "--no-yolo"],
    )

    assert result.exit_code == 0
    assert calls[-1] == (
        "POST",
        "/api/runs",
        {
            "session_id": "session-1",
            "input": [{"kind": "text", "text": "hello"}],
            "execution_mode": "ai",
            "yolo": False,
        },
    )


def test_root_message_rejects_orchestration_without_mode() -> None:
    result = runner.invoke(
        cli_app.app,
        ["-m", "hello", "--orchestration", "default"],
    )

    normalized_output = _normalized_output(result.output)
    assert result.exit_code == 2
    assert (
        "--orchestration can only be used with --mode orchestration"
        in normalized_output
    )
    assert "Available quick prompt options:" in normalized_output
    assert "--orchestration <id>" in normalized_output


def test_root_message_rejects_role_with_orchestration_mode() -> None:
    result = runner.invoke(
        cli_app.app,
        ["-m", "hello", "--mode", "orchestration", "--role", "Crafter"],
    )

    normalized_output = _normalized_output(result.output)
    assert result.exit_code == 2
    assert "--role can only be used with --mode normal" in normalized_output


def test_root_message_invalid_role_lists_available_ids(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_autostart(base_url: str, autostart: bool) -> None:
        _ = (base_url, autostart)

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object] | list[object]:
        _ = (base_url, timeout_seconds)
        calls.append((method, path, payload))
        if path == "/api/workspaces/pick":
            return _workspace_response(tmp_path)
        if path == "/api/sessions":
            return {"session_id": "session-1"}
        if path == "/api/sessions/session-1/topology":
            raise RuntimeError(
                'HTTP 422 PATCH /api/sessions/session-1/topology: {"detail":"Unknown normal mode role: Missing"}'
            )
        if path == "/api/roles:options":
            return {
                "coordinator_role_id": "Coordinator",
                "main_agent_role_id": "MainAgent",
                "normal_mode_roles": [
                    {
                        "role_id": "MainAgent",
                        "name": "Main Agent",
                        "description": "Default",
                    },
                    {
                        "role_id": "Crafter",
                        "name": "Crafter",
                        "description": "Implements",
                    },
                ],
            }
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app.app, ["-m", "hello", "--role", "Missing"])

    normalized_output = _normalized_output(result.output)
    assert result.exit_code == 2
    assert "Invalid --role 'Missing'" in normalized_output
    assert "MainAgent, Crafter." in normalized_output
    assert calls == [
        (
            "POST",
            "/api/workspaces/pick",
            {"root_path": str(tmp_path.resolve())},
        ),
        ("POST", "/api/sessions", {"workspace_id": "workspace-1"}),
        (
            "PATCH",
            "/api/sessions/session-1/topology",
            {
                "session_mode": "normal",
                "normal_root_role_id": "Missing",
                "orchestration_preset_id": None,
            },
        ),
        ("GET", "/api/roles:options", None),
    ]


def test_root_message_invalid_orchestration_id_lists_available_ids(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_autostart(base_url: str, autostart: bool) -> None:
        _ = (base_url, autostart)

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object] | list[object]:
        _ = (base_url, timeout_seconds)
        calls.append((method, path, payload))
        if path == "/api/workspaces/pick":
            return _workspace_response(tmp_path)
        if path == "/api/sessions":
            return {"session_id": "session-1"}
        if path == "/api/sessions/session-1/topology":
            raise RuntimeError(
                'HTTP 422 PATCH /api/sessions/session-1/topology: {"detail":"Unknown orchestration preset: missing"}'
            )
        if path == "/api/system/configs/orchestration":
            return {
                "default_orchestration_preset_id": "default",
                "presets": [
                    {"preset_id": "default"},
                    {"preset_id": "release"},
                ],
            }
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        cli_app.app,
        [
            "-m",
            "hello",
            "--mode",
            "orchestration",
            "--orchestration",
            "missing",
        ],
    )

    normalized_output = _normalized_output(result.output)
    assert result.exit_code == 2
    assert "Invalid --orchestration 'missing'" in normalized_output
    assert "Available orchestration" in normalized_output
    assert "ids: default, release." in normalized_output
    assert calls == [
        (
            "POST",
            "/api/workspaces/pick",
            {"root_path": str(tmp_path.resolve())},
        ),
        ("POST", "/api/sessions", {"workspace_id": "workspace-1"}),
        (
            "PATCH",
            "/api/sessions/session-1/topology",
            {
                "session_mode": "orchestration",
                "orchestration_preset_id": "missing",
            },
        ),
        ("GET", "/api/system/configs/orchestration", None),
    ]


def test_run_module_removed() -> None:
    result = runner.invoke(cli_app.app, ["run", "prompt", "-m", "hello"])
    assert result.exit_code != 0
    assert "No such command 'run'" in result.output


def test_runs_module_todo_command(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_autostart(base_url: str, autostart: bool) -> None:
        _ = (base_url, autostart)

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object] | list[object]:
        _ = (base_url, timeout_seconds)
        calls.append((method, path, payload))
        return {
            "todo": {
                "run_id": "run-1",
                "session_id": "session-1",
                "items": [
                    {"content": "Inspect issue", "status": "completed"},
                    {"content": "Implement todo flow", "status": "in_progress"},
                ],
                "version": 2,
                "updated_at": "2026-04-20T00:00:00+00:00",
                "updated_by_role_id": "MainAgent",
                "updated_by_instance_id": "inst-1",
            }
        }

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)

    result = runner.invoke(cli_app.app, ["runs", "todo", "--run-id", "run-1"])

    normalized_output = _normalized_output(result.output)
    assert result.exit_code == 0
    assert "Run ID : run-1" in normalized_output
    assert "[in_progress] Implement todo flow" in normalized_output
    assert calls == [("GET", "/api/runs/run-1/todo", None)]


def test_root_help_lists_env_module() -> None:
    result = runner.invoke(cli_app.app, ["--help"])
    normalized_output = _normalized_output(result.output)
    assert result.exit_code == 0
    assert "--mode" in normalized_output
    assert "--role" in normalized_output
    assert "--orchestration" in normalized_output
    assert "--workspace" in normalized_output
    assert "Defaults" in normalized_output
    assert "directory. Requires" in normalized_output
    assert "env" in normalized_output
    assert "mcp" in normalized_output
    assert "agents" in normalized_output
    assert "roles" in normalized_output
    assert "skills" in normalized_output
    assert "gateway" in normalized_output
    assert "runs" in normalized_output
    assert "prompts" not in normalized_output
