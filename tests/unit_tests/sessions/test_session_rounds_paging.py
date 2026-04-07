from relay_teams.sessions.session_rounds_projection import paginate_rounds
from typing import cast


def test_get_session_rounds_returns_first_page_with_cursor() -> None:
    rounds: list[dict[str, object]] = [
        {"run_id": "run-5", "created_at": "2026-03-03T12:05:00+00:00"},
        {"run_id": "run-4", "created_at": "2026-03-03T12:04:00+00:00"},
        {"run_id": "run-3", "created_at": "2026-03-03T12:03:00+00:00"},
        {"run_id": "run-2", "created_at": "2026-03-03T12:02:00+00:00"},
        {"run_id": "run-1", "created_at": "2026-03-03T12:01:00+00:00"},
    ]
    page = paginate_rounds(
        rounds,
        limit=2,
        cursor_run_id=None,
    )

    items = cast(list[dict[str, object]], page["items"])
    assert [item["run_id"] for item in items] == ["run-5", "run-4"]
    assert page["has_more"] is True
    assert page["next_cursor"] == "run-4"


def test_get_session_rounds_uses_cursor_to_load_older() -> None:
    rounds: list[dict[str, object]] = [
        {"run_id": "run-5", "created_at": "2026-03-03T12:05:00+00:00"},
        {"run_id": "run-4", "created_at": "2026-03-03T12:04:00+00:00"},
        {"run_id": "run-3", "created_at": "2026-03-03T12:03:00+00:00"},
        {"run_id": "run-2", "created_at": "2026-03-03T12:02:00+00:00"},
        {"run_id": "run-1", "created_at": "2026-03-03T12:01:00+00:00"},
    ]
    page = paginate_rounds(
        rounds,
        limit=2,
        cursor_run_id="run-4",
    )

    items = cast(list[dict[str, object]], page["items"])
    assert [item["run_id"] for item in items] == ["run-3", "run-2"]
    assert page["has_more"] is True
    assert page["next_cursor"] == "run-2"
