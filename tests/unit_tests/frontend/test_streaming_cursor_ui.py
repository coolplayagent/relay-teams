# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


def test_streaming_messages_render_a_terminal_cursor_until_finalize() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    helper_block_script = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "messageRenderer"
        / "helpers"
        / "block.js"
    ).read_text(encoding="utf-8")
    helper_facade_script = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "messageRenderer"
        / "helpers.js"
    ).read_text(encoding="utf-8")
    stream_script = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "messageRenderer"
        / "stream.js"
    ).read_text(encoding="utf-8")
    history_script = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "messageRenderer"
        / "history.js"
    ).read_text(encoding="utf-8")

    assert "const STREAMING_CURSOR_CLASS = 'streaming-cursor';" in helper_block_script
    assert "export function syncStreamingCursor(textEl, active)" in helper_block_script
    assert "function resolveStreamingCursorHost(root)" in helper_block_script
    assert "updateMessageText," in helper_facade_script
    assert "syncStreamingCursor," in helper_facade_script
    assert (
        "updateMessageText(st.activeTextEl, st.raw, { streaming: true });"
        in stream_script
    )
    assert (
        "updateMessageText(st.activeTextEl, st.raw, { streaming: false });"
        in stream_script
    )
    assert "syncStreamingCursor(entry.activeTextEl, false);" in stream_script
    assert (
        "appendMessageText(contentEl, safeText.trim(), { streaming: true });"
        in history_script
    )


def test_streaming_cursor_styles_are_declared_in_shared_frontend_css() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    base_css = (repo_root / "frontend" / "dist" / "css" / "base.css").read_text(
        encoding="utf-8"
    )
    components_css = (
        repo_root / "frontend" / "dist" / "css" / "components.css"
    ).read_text(encoding="utf-8")

    assert "@keyframes streamingCaretPulse" in base_css
    assert ".streaming-cursor {" in components_css
    assert "border-radius: 999px;" in components_css
    assert (
        "animation: streamingCaretPulse 1.05s steps(1, end) infinite;" in components_css
    )
    assert (
        "background: color-mix(in srgb, var(--primary) 72%, var(--text-msg-content) 28%);"
        in components_css
    )
