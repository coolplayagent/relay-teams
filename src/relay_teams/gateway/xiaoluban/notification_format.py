# -*- coding: utf-8 -*-
from __future__ import annotations

import unicodedata
from datetime import datetime, timezone

_HEADER = "【relay-teams】"
_SEPARATOR = "────────────────────"
_MAX_LIST_DISPLAY = 15
_TITLE_DISPLAY_WIDTH = 30


def format_xiaoluban_notification_text(
    *,
    workspace_id: str,
    session_id: str,
    status: str,
    body: str,
) -> str:
    _ = workspace_id
    _ = status
    lines = [_HEADER]
    normalized_session_id = str(session_id or "").strip()
    if normalized_session_id:
        lines.append(normalized_session_id)
    lines.append(_SEPARATOR)
    normalized_body = str(body or "").strip()
    if normalized_body:
        lines.append(normalized_body)
    return "\n".join(lines)


def format_im_command_reply(
    *,
    body: str,
    session_id: str = "",
) -> str:
    lines = [_HEADER]
    normalized_session_id = str(session_id or "").strip()
    if normalized_session_id:
        lines.append(normalized_session_id)
    lines.append(_SEPARATOR)
    normalized_body = str(body or "").strip()
    if normalized_body:
        lines.append(normalized_body)
    return "\n".join(lines)


def format_session_list_text(
    *,
    sessions: tuple[_SessionListItem, ...],
    total_count: int,
) -> str:
    now = datetime.now(tz=timezone.utc)
    lines = ["当前workspace会话列表", _SEPARATOR]
    displayed = sessions[:_MAX_LIST_DISPLAY]
    for idx, item in enumerate(displayed, start=1):
        relative = _format_relative_time(item.last_active_at, now)
        title = item.title.strip() if item.title else "新会话"
        title_display = _truncate_display_width(title, _TITLE_DISPLAY_WIDTH)
        if title_display:
            lines.append(
                f"[{idx}] {item.internal_session_id}  {relative}  {title_display}"
            )
        else:
            lines.append(f"[{idx}] {item.internal_session_id}  {relative}")
    if total_count > _MAX_LIST_DISPLAY:
        remaining = total_count - _MAX_LIST_DISPLAY
        lines.append(f"...({remaining} more)")
    lines.append(_SEPARATOR)
    lines.append("使用 /resume {编号或session_id} 切换会话")
    return "\n".join(lines)


def format_help_text() -> str:
    lines = [
        "可用命令",
        _SEPARATOR,
        "/new [任务内容]  创建新会话，可选附带任务",
        "/resume          列出当前workspace会话列表",
        "/resume {id}     切换到指定会话",
        "/help            显示此帮助",
        "",
        "输入 q 退出转发，回到小鲁班对话",
    ]
    return "\n".join(lines)


class _SessionListItem:
    __slots__ = ("internal_session_id", "last_active_at", "title")

    def __init__(
        self, internal_session_id: str, last_active_at: datetime, title: str = ""
    ) -> None:
        self.internal_session_id = internal_session_id
        self.last_active_at = last_active_at
        self.title = title


def make_session_list_item(
    internal_session_id: str, last_active_at: datetime, title: str = ""
) -> _SessionListItem:
    return _SessionListItem(internal_session_id, last_active_at, title)


def _format_relative_time(dt: datetime, now: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    diff = (now - dt).total_seconds()
    if diff < 60:
        return "刚刚"
    minutes = int(diff / 60)
    if minutes < 60:
        return f"{minutes}分钟前"
    hours = int(diff / 3600)
    if hours < 24:
        return f"{hours}小时前"
    days = int(diff / 86400)
    if days < 7:
        return f"{days}天前"
    return dt.astimezone().strftime("%m-%d %H:%M")


def _display_width(text: str) -> int:
    width = 0
    for char in text:
        if unicodedata.east_asian_width(char) in ("W", "F", "A"):
            width += 2
        else:
            width += 1
    return width


def _truncate_display_width(text: str, max_width: int) -> str:
    if _display_width(text) <= max_width:
        return text
    result: list[str] = []
    current = 0
    suffix = "..."
    suffix_width = _display_width(suffix)
    limit = max_width - suffix_width
    for char in text:
        char_width = 2 if unicodedata.east_asian_width(char) in ("W", "F", "A") else 1
        if current + char_width > limit:
            break
        result.append(char)
        current += char_width
    return "".join(result) + suffix


__all__ = [
    "format_xiaoluban_notification_text",
    "format_im_command_reply",
    "format_session_list_text",
    "format_help_text",
    "make_session_list_item",
]
