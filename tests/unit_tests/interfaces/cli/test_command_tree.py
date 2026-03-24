# -*- coding: utf-8 -*-
from __future__ import annotations

from typer.testing import CliRunner

from agent_teams.interfaces.cli import app as cli_app

runner = CliRunner()


def test_root_message_runs_single_prompt(monkeypatch) -> None:
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

    result = runner.invoke(cli_app.app, ["-m", "hello"])

    assert result.exit_code == 0
    assert calls == [
        ("POST", "/api/sessions", {"workspace_id": "default"}),
        (
            "POST",
            "/api/runs",
            {
                "session_id": "session-1",
                "intent": "hello",
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


def test_root_message_supports_normal_role_selection(monkeypatch) -> None:
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
        if path == "/api/sessions":
            return {"session_id": "session-1"}
        if path == "/api/sessions/session-1/topology":
            return {
                "session_id": "session-1",
                "workspace_id": "default",
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

    result = runner.invoke(cli_app.app, ["-m", "hello", "--role", "Crafter"])

    assert result.exit_code == 0
    assert calls == [
        ("POST", "/api/sessions", {"workspace_id": "default"}),
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
                "intent": "hello",
                "execution_mode": "ai",
                "yolo": True,
            },
        ),
    ]


def test_root_message_supports_orchestration_mode(monkeypatch) -> None:
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
        if path == "/api/sessions":
            return {"session_id": "session-1"}
        if path == "/api/sessions/session-1/topology":
            return {
                "session_id": "session-1",
                "workspace_id": "default",
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
        ("POST", "/api/sessions", {"workspace_id": "default"}),
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
                "intent": "hello",
                "execution_mode": "ai",
                "yolo": True,
            },
        ),
    ]


def test_root_message_allows_no_yolo_override(monkeypatch) -> None:
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
            "intent": "hello",
            "execution_mode": "ai",
            "yolo": False,
        },
    )


def test_root_message_rejects_orchestration_without_mode() -> None:
    result = runner.invoke(
        cli_app.app,
        ["-m", "hello", "--orchestration", "default"],
    )

    assert result.exit_code == 2
    assert "--orchestration can only be used with --mode orchestration" in result.output
    assert "Available quick prompt options:" in result.output
    assert "--orchestration <id>" in result.output


def test_root_message_rejects_role_with_orchestration_mode() -> None:
    result = runner.invoke(
        cli_app.app,
        ["-m", "hello", "--mode", "orchestration", "--role", "Crafter"],
    )

    assert result.exit_code == 2
    assert "--role can only be used with --mode normal" in result.output


def test_root_message_invalid_role_lists_available_ids(monkeypatch) -> None:
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
                    {"role_id": "MainAgent", "name": "Main Agent", "description": "Default"},
                    {"role_id": "Crafter", "name": "Crafter", "description": "Implements"},
                ],
            }
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)

    result = runner.invoke(cli_app.app, ["-m", "hello", "--role", "Missing"])

    assert result.exit_code == 2
    assert "Invalid --role 'Missing'" in result.output
    assert "MainAgent, Crafter." in result.output


def test_root_message_invalid_orchestration_id_lists_available_ids(
    monkeypatch,
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

    assert result.exit_code == 2
    assert "Invalid --orchestration 'missing'" in result.output
    assert "Available orchestration" in result.output
    assert "ids: default, release." in result.output
    assert calls == [
        ("POST", "/api/sessions", {"workspace_id": "default"}),
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


def test_root_help_lists_env_module() -> None:
    result = runner.invoke(cli_app.app, ["--help"])
    assert result.exit_code == 0
    assert "--mode" in result.output
    assert "--role" in result.output
    assert "--orchestration" in result.output
    assert "env" in result.output
    assert "mcp" in result.output
    assert "roles" in result.output
    assert "skills" in result.output
    assert "triggers" in result.output
    assert "prompts" not in result.output
