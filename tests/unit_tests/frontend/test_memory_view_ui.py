# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


def test_memory_search_payload_preserves_any_status() -> None:
    source = Path("frontend/dist/js/components/memoryView.js").read_text(
        encoding="utf-8"
    )

    assert "payload.status = memoryState.status || null;" in source
    assert "if (memoryState.status) {\n        payload.status" not in source
