from __future__ import annotations

from typer.testing import CliRunner

from relay_teams.interfaces.cli import app as cli_app

runner = CliRunner()


def test_questions_list_renders_table_by_default(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_autostart(base_url: str, autostart: bool) -> None:
        captured["base_url"] = base_url
        captured["autostart"] = autostart

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object] | list[object]:
        _ = (base_url, payload, timeout_seconds)
        captured["method"] = method
        captured["path"] = path
        return [
            {
                "question_id": "call-1",
                "status": "requested",
                "role_id": "Coordinator",
                "instance_id": "inst-1",
                "questions": [
                    {
                        "question": "Pick one",
                        "options": [{"label": "A", "description": "Option A"}],
                    }
                ],
            }
        ]

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)

    result = runner.invoke(
        cli_app.app,
        ["questions", "list", "--run-id", "run-1"],
    )

    assert result.exit_code == 0
    assert captured == {
        "base_url": cli_app.DEFAULT_BASE_URL,
        "autostart": True,
        "method": "GET",
        "path": "/api/runs/run-1/questions",
    }
    assert "Question ID | Status | Role | Instance | Prompts | Preview" in result.output
    assert "call-1 | requested | Coordinator | inst-1 | 1 | Pick one" in result.output


def test_questions_list_supports_json_output(monkeypatch) -> None:
    def fake_autostart(base_url: str, autostart: bool) -> None:
        _ = (base_url, autostart)

    def fake_request_json(
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object] | list[object]:
        _ = (base_url, method, path, payload, timeout_seconds)
        return {
            "data": [
                {
                    "question_id": "call-1",
                    "status": "requested",
                    "role_id": "Coordinator",
                    "instance_id": "inst-1",
                    "questions": [{"question": "Pick one"}],
                }
            ]
        }

    monkeypatch.setattr(cli_app, "_auto_start_if_needed", fake_autostart)
    monkeypatch.setattr(cli_app, "_request_json", fake_request_json)

    result = runner.invoke(
        cli_app.app,
        ["questions", "list", "--run-id", "run-1", "--format", "json"],
    )

    assert result.exit_code == 0
    assert '"question_id": "call-1"' in result.output
    assert (
        "Question ID | Status | Role | Instance | Prompts | Preview"
        not in result.output
    )
