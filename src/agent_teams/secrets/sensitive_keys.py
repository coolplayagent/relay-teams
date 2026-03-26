# -*- coding: utf-8 -*-
from __future__ import annotations

import re

SENSITIVE_ENV_TOKENS: tuple[str, ...] = ("KEY", "TOKEN", "SECRET", "PASSWORD")


def is_sensitive_env_key(key: str) -> bool:
    normalized_key = key.upper()
    tokens = [token for token in re.split(r"[^A-Z0-9]+", normalized_key) if token]
    for sensitive_token in SENSITIVE_ENV_TOKENS:
        if sensitive_token in tokens:
            return True
    return False
