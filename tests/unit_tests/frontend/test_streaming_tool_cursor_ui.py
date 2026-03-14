# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


def test_tool_call_insertion_clears_existing_streaming_cursor() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    stream_script = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "messageRenderer"
        / "stream.js"
    ).read_text(encoding="utf-8")

    assert "if (st.activeTextEl) {" in stream_script
    assert "syncStreamingCursor(st.activeTextEl, false);" in stream_script
    assert "st.activeTextEl = null;" in stream_script
