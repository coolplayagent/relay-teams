# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime


def run() -> str:
    return datetime.now().astimezone().isoformat()
