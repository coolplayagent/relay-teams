# -*- coding: utf-8 -*-
from __future__ import annotations

_HEADER = "【relay-teams】"
_SEPARATOR = "────────────────────"


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


__all__ = ["format_xiaoluban_notification_text"]
