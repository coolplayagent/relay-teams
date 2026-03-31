# -*- coding: utf-8 -*-
from __future__ import annotations

import re

_BASH_WRAPPER_PATTERN = re.compile(
    r"^\s*(?:/bin/)?bash\s+-lc\s+(?P<body>.+?)\s*$",
    re.DOTALL,
)


def canonicalize_shell_command(command: str) -> str:
    normalized = command.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return ""
    match = _BASH_WRAPPER_PATTERN.match(normalized)
    if match is not None:
        body = match.group("body").strip()
        if (body.startswith("'") and body.endswith("'")) or (
            body.startswith('"') and body.endswith('"')
        ):
            body = body[1:-1]
        normalized = body.strip()
    lines = [line.strip() for line in normalized.split("\n")]
    return "\n".join(line for line in lines if line)
