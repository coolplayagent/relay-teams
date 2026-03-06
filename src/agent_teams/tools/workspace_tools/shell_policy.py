from __future__ import annotations

import re

DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 120

BANNED_PATTERNS = (
    r"(^|\s)vim(\s|$)",
    r"(^|\s)nano(\s|$)",
    r"(^|\s)less(\s|$)",
    r"(^|\s)more(\s|$)",
    r"(^|\s)top(\s|$)",
    r"(^|\s)watch(\s|$)",
    r"(^|\s)ssh(\s|$)",
    r"(^|\s)sftp(\s|$)",
    r"(^|\s)ftp(\s|$)",
    r"(^|\s)start(\s|$)",
    r"(^|\s)explorer(\s|$)",
    r"xdg-open",
    r"(^|\s)open(\s|$)",
    r"cmd\s*/c\s*start",
    r"rm\s+-rf\s+/",
    r"(^|\s)del\s+/f\s+/s\s+/q(\s|$)",
    r"(^|\s)format(\s|$)",
    r"(^|\s)shutdown(\s|$)",
    r"(^|\s)reboot(\s|$)",
    r"(^|\s)mkfs",
    r"(^|\s)dd\s+if=",
)


def normalize_timeout(timeout_seconds: int | None) -> int:
    if timeout_seconds is None:
        return DEFAULT_TIMEOUT_SECONDS
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be >= 1")
    if timeout_seconds > MAX_TIMEOUT_SECONDS:
        return MAX_TIMEOUT_SECONDS
    return timeout_seconds


def validate_shell_command(command: str) -> None:
    text = command.strip()
    if not text:
        raise ValueError("command must not be empty")
    if len(text) > 2000:
        raise ValueError("command is too long")

    lower = text.lower()
    for pattern in BANNED_PATTERNS:
        if re.search(pattern, lower):
            raise ValueError(f"command is blocked by policy: {pattern}")
