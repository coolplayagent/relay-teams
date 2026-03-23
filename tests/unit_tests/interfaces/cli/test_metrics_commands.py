from __future__ import annotations

from typer.testing import CliRunner

from agent_teams.interfaces.cli import app as cli_app

runner = CliRunner()


def test_metrics_overview_command_renders_prettylog(monkeypatch) -> None:
    calls: list[tuple[str, str, str]] = []

    def fake_autostart(base_url: str, autostart: bool) -> None:
        assert base_url == cli_app.DEFAULT_BASE_URL
        assert autostart is True

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object] | list[object]:
        _ = (base_url, payload, timeout_seconds)
        calls.append((method, path, "overview"))
        return {
            "scope": "global",
            "scope_id": "",
            "kpis": {
                "steps": 4,
                "input_tokens": 120,
                "output_tokens": 30,
                "tool_calls": 2,
                "tool_success_rate": 0.5,
            },
        }

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)

    result = runner.invoke(
        cli_app.app, ["metrics", "overview", "--format", "prettylog"]
    )

    assert result.exit_code == 0
    assert "[metrics] scope=global" in result.output
    assert calls == [
        (
            "GET",
            "/api/observability/overview?scope=global&scope_id=&time_window_minutes=1440",
            "overview",
        )
    ]


def test_metrics_breakdowns_command_requires_scope_id() -> None:
    result = runner.invoke(cli_app.app, ["metrics", "breakdowns", "--scope", "session"])
    assert result.exit_code == 2
    assert "--scope-id is required when scope is session or run" in result.output
