from __future__ import annotations

DEFAULT_TIMEOUT_SECONDS = 120
MAX_TIMEOUT_SECONDS = 1200

MAX_COMMAND_LENGTH = 16_000


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
    if len(text) > MAX_COMMAND_LENGTH:
        raise ValueError(
            f"command is too long ({len(text)} chars, max {MAX_COMMAND_LENGTH})"
        )
