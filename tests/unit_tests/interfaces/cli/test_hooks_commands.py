from __future__ import annotations

from typer.testing import CliRunner

from relay_teams.interfaces.cli import app as cli_app

runner = CliRunner()


def test_hooks_list_command_renders_table(monkeypatch) -> None:
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
        assert method == "GET"
        assert path == "/api/system/configs/hooks"
        return {
            "config_path": "C:/app/hooks.json",
            "exists": True,
            "config": {"hooks": {"SessionStart": []}},
            "summary": {
                "event_count": 1,
                "matcher_group_count": 0,
                "handler_count": 0,
            },
        }

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)

    result = runner.invoke(cli_app.app, ["hooks", "list"])

    assert result.exit_code == 0
    assert "Config Path" in result.output
    assert "C:/app/hooks.json" in result.output
    assert "Events" in result.output


def test_hooks_validate_command_renders_json(monkeypatch) -> None:
    def fake_autostart(base_url: str, autostart: bool) -> None:
        _ = (base_url, autostart)

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object] | list[object]:
        _ = (base_url, payload, timeout_seconds)
        assert method == "POST"
        assert path == "/api/system/configs/hooks:validate"
        return {
            "valid": True,
            "config_path": "C:/app/hooks.json",
            "exists": True,
            "summary": {
                "event_count": 1,
                "matcher_group_count": 0,
                "handler_count": 0,
            },
            "error": None,
        }

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)

    result = runner.invoke(cli_app.app, ["hooks", "validate", "--format", "json"])

    assert result.exit_code == 0
    assert '"valid": true' in result.output.lower()
