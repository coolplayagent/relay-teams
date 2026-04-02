# -*- coding: utf-8 -*-
from __future__ import annotations

import re

_BASH_WRAPPER_PATTERN = re.compile(
    r'^\s*(?:"(?P<quoted>[^"]*?bash(?:\.exe)?)"|(?P<plain>\S*?bash(?:\.exe)?))\s+-lc\s+(?P<body>.+?)\s*$',
    re.DOTALL | re.IGNORECASE,
)
_POWERSHELL_WRAPPER_PATTERN = re.compile(
    r'^\s*(?:"(?P<quoted>[^"]*?(?:pwsh|powershell)(?:\.exe)?)"|(?P<plain>\S*?(?:pwsh|powershell)(?:\.exe)?))\s+(?P<flags>(?:-\S+\s+)*)-Command\s+(?P<body>.+?)\s*$',
    re.DOTALL | re.IGNORECASE,
)
_POWERSHELL_UTF8_PREFIX_LINES = (
    "[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)",
    "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)",
    "$OutputEncoding = [Console]::OutputEncoding",
)


def canonicalize_shell_command(command: str) -> str:
    normalized = _normalize_line_endings(command)
    if not normalized.strip():
        return ""
    normalized = _unwrap_bash_wrapper(normalized)
    normalized = _unwrap_powershell_wrapper(normalized)
    normalized = _normalize_line_endings(normalized)
    if not normalized.strip():
        return ""
    return normalized


def _unwrap_bash_wrapper(command: str) -> str:
    match = _BASH_WRAPPER_PATTERN.match(command)
    if match is None:
        return command
    return _strip_outer_quotes(match.group("body"))


def _unwrap_powershell_wrapper(command: str) -> str:
    match = _POWERSHELL_WRAPPER_PATTERN.match(command)
    if match is None:
        return command
    body = _strip_outer_quotes(match.group("body"))
    lines = _normalize_line_endings(body).split("\n")
    if (
        tuple(lines[: len(_POWERSHELL_UTF8_PREFIX_LINES)])
        == _POWERSHELL_UTF8_PREFIX_LINES
    ):
        lines = lines[len(_POWERSHELL_UTF8_PREFIX_LINES) :]
    return "\n".join(lines)


def _strip_outer_quotes(value: str) -> str:
    if len(value) < 2:
        return value
    if (value.startswith("'") and value.endswith("'")) or (
        value.startswith('"') and value.endswith('"')
    ):
        return value[1:-1]
    return value


def _normalize_line_endings(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")
