from __future__ import annotations

from datetime import datetime, timedelta, timezone

from relay_teams.gateway.xiaoluban.notification_format import (
    format_help_text,
    format_im_command_reply,
    format_session_list_text,
    format_xiaoluban_notification_text,
    make_session_list_item,
)


def test_format_im_command_reply_includes_optional_session_id() -> None:
    text = format_im_command_reply(body="处理中", session_id="session-1")

    assert "【relay-teams】" in text
    assert "session-1" in text
    assert "处理中" in text


def test_format_xiaoluban_notification_text_omits_blank_body() -> None:
    text = format_xiaoluban_notification_text(
        workspace_id="workspace-1",
        session_id="session-1",
        status="completed",
        body="",
    )

    assert text.endswith("────────────────────")


def test_format_session_list_text_covers_relative_times_and_truncation() -> None:
    now = datetime.now(tz=timezone.utc)
    sessions = (
        make_session_list_item("session-now", now - timedelta(seconds=5), "短标题"),
        make_session_list_item(
            "session-minute",
            now - timedelta(minutes=3),
            "这是一个很长很长很长很长的中文标题",
        ),
        make_session_list_item("session-hour", now - timedelta(hours=2), "hour"),
        make_session_list_item("session-day", now - timedelta(days=3), "day"),
        make_session_list_item("session-old", now - timedelta(days=9), "old"),
        make_session_list_item("session-blank-title", now, "   "),
    )

    text = format_session_list_text(sessions=sessions, total_count=20)

    assert "刚刚" in text
    assert "分钟前" in text
    assert "小时前" in text
    assert "天前" in text
    assert "..." in text
    assert "...(5 more)" in text
    assert (
        f"session-old  {(now - timedelta(days=9)).astimezone().strftime('%m-%d')}"
        in text
    )
    assert "[6] session-blank-title" in text


def test_format_session_list_text_treats_naive_datetime_as_utc() -> None:
    naive = datetime.now(tz=timezone.utc).replace(tzinfo=None) - timedelta(minutes=2)

    text = format_session_list_text(
        sessions=(make_session_list_item("session-naive", naive, "naive"),),
        total_count=1,
    )

    assert "session-naive" in text
    assert "分钟前" in text


def test_format_help_text_lists_commands() -> None:
    text = format_help_text()

    assert "/new" in text
    assert "/resume" in text
    assert "/help" in text
