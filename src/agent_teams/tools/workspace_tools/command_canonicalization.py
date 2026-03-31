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
    normalized = command.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return ""
    normalized = _unwrap_bash_wrapper(normalized)
    normalized = _unwrap_powershell_wrapper(normalized)
    lines = [line.strip() for line in normalized.split("\n")]
    return "\n".join(line for line in lines if line)


def _unwrap_bash_wrapper(command: str) -> str:
    match = _BASH_WRAPPER_PATTERN.match(command)
    if match is None:
        return command
    return _strip_outer_quotes(match.group("body").strip())


def _unwrap_powershell_wrapper(command: str) -> str:
    match = _POWERSHELL_WRAPPER_PATTERN.match(command)
    if match is None:
        return command
    body = _strip_outer_quotes(match.group("body").strip())
    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if (
        tuple(lines[: len(_POWERSHELL_UTF8_PREFIX_LINES)])
        == _POWERSHELL_UTF8_PREFIX_LINES
    ):
        lines = lines[len(_POWERSHELL_UTF8_PREFIX_LINES) :]
    return "\n".join(lines).strip()


def _strip_outer_quotes(value: str) -> str:
    if len(value) < 2:
        return value
    if (value.startswith("'") and value.endswith("'")) or (
        value.startswith('"') and value.endswith('"')
    ):
        return value[1:-1].strip()
    return value
