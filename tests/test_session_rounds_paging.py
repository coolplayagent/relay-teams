from agent_teams.application.service import AgentTeamsService


class _FakeService:
    def __init__(self, rounds: list[dict]) -> None:
        self._rounds = rounds

    def _build_session_rounds(self, session_id: str) -> list[dict]:
        return list(self._rounds)


def test_get_session_rounds_returns_first_page_with_cursor() -> None:
    rounds = [
        {"run_id": "run-5", "created_at": "2026-03-03T12:05:00+00:00"},
        {"run_id": "run-4", "created_at": "2026-03-03T12:04:00+00:00"},
        {"run_id": "run-3", "created_at": "2026-03-03T12:03:00+00:00"},
        {"run_id": "run-2", "created_at": "2026-03-03T12:02:00+00:00"},
        {"run_id": "run-1", "created_at": "2026-03-03T12:01:00+00:00"},
    ]
    svc = _FakeService(rounds)

    page = AgentTeamsService.get_session_rounds(
        svc,  # type: ignore[arg-type]
        "session-1",
        limit=2,
        cursor_run_id=None,
    )

    assert [item["run_id"] for item in page["items"]] == ["run-5", "run-4"]
    assert page["has_more"] is True
    assert page["next_cursor"] == "run-4"


def test_get_session_rounds_uses_cursor_to_load_older() -> None:
    rounds = [
        {"run_id": "run-5", "created_at": "2026-03-03T12:05:00+00:00"},
        {"run_id": "run-4", "created_at": "2026-03-03T12:04:00+00:00"},
        {"run_id": "run-3", "created_at": "2026-03-03T12:03:00+00:00"},
        {"run_id": "run-2", "created_at": "2026-03-03T12:02:00+00:00"},
        {"run_id": "run-1", "created_at": "2026-03-03T12:01:00+00:00"},
    ]
    svc = _FakeService(rounds)

    page = AgentTeamsService.get_session_rounds(
        svc,  # type: ignore[arg-type]
        "session-1",
        limit=2,
        cursor_run_id="run-4",
    )

    assert [item["run_id"] for item in page["items"]] == ["run-3", "run-2"]
    assert page["has_more"] is True
    assert page["next_cursor"] == "run-2"
