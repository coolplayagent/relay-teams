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
    assert "const terminalSelector = [" in helper_block_script
    assert "'pre code'," in helper_block_script
    assert "'blockquote > :last-child'," in helper_block_script
    assert "'li:last-child'," in helper_block_script
    assert "function findLastRenderableElement(root)" in helper_block_script
    assert "function hasRenderableTerminalContent(node)" in helper_block_script
    assert "updateMessageText," in helper_facade_script
    assert "syncStreamingCursor," in helper_facade_script
    assert (
        "updateMessageText(st.activeTextEl, st.activeRaw, { streaming: true });"
        in stream_script
    )
    assert (
        "updateMessageText(entry.activeTextEl, entry.activeRaw, { streaming: false });"
        in stream_script
    )
    assert "syncStreamingCursor(entry.activeTextEl, false);" in stream_script
    assert (
        "updateThinkingText(entry.textEl, entry.raw, { streaming: false });"
        in stream_script
    )
    assert "appendThinkingText(contentEl, String(part.content || '')," in history_script
    assert "const flushText = (streaming = false) => {" in history_script
    assert (
        "appendMessageText(contentEl, streaming ? safeText : safeText.trim(), { streaming });"
        in history_script
    )
    assert "flushText(false);" in history_script
    assert "flushText(hasLiveTextTail && !!trailingTextPart);" in history_script
    assert "appendMessageText(contentEl, '', { streaming: true });" in history_script


def test_streaming_cursor_styles_are_declared_in_shared_frontend_css() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    base_css = (repo_root / "frontend" / "dist" / "css" / "base.css").read_text(
        encoding="utf-8"
    )
    components_css = (
        repo_root / "frontend" / "dist" / "css" / "components" / "messages.css"
    ).read_text(encoding="utf-8")

    assert "@keyframes streamingCaretPulse" in base_css
    assert "opacity: 0.26;" in base_css
    assert "opacity: 1;" in base_css
    assert ".streaming-cursor {" in components_css
    assert ".thinking-block {" in components_css
    assert ".thinking-live {" in components_css
    assert "border-radius: 999px;" in components_css
    assert (
        "animation: streamingCaretPulse 0.9s ease-in-out infinite alternate;"
        in components_css
    )
    assert (
        "background: color-mix(in srgb, var(--primary) 72%, var(--text-msg-content) 28%);"
        in components_css
    )
