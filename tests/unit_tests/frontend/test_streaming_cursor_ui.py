# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .css_helpers import load_components_css


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
    assert "const thinkingOpenState = new Map();" in helper_block_script
    assert "export function clearThinkingOpenState()" in helper_block_script
    assert "export function clearThinkingOpenStateForRun(runId)" in helper_block_script
    assert "function bindThinkingOpenState(block, options = {})" in helper_block_script
    assert (
        "function syncThinkingOpenFromState(block, defaultOpen = false)"
        in helper_block_script
    )
    assert "block.dataset.thinkingUserToggled = 'true';" in helper_block_script
    assert "thinkingOpenInitialized" not in helper_block_script
    assert (
        "function resolveThinkingOpenStateKey(block, options = {})"
        in helper_block_script
    )
    assert "export function syncStreamingCursor(textEl, active)" in helper_block_script
    assert "const LARGE_STREAM_TEXT_THRESHOLD = 12000;" in helper_block_script
    assert "const RICH_TEXT_AUTORENDER_LIMIT = 80000;" in helper_block_script
    assert "const PLAIN_TEXT_CHUNK_SIZE = 16384;" in helper_block_script
    assert (
        "function renderPlainTextContent(textEl, source, options = {})"
        in helper_block_script
    )
    assert "options.appendDelta === true" in helper_block_script
    assert "function resolveStreamingCursorHost(root)" in helper_block_script
    assert "const terminalSelector = [" in helper_block_script
    assert "'pre code'," in helper_block_script
    assert "'blockquote > :last-child'," in helper_block_script
    assert "'li:last-child'," in helper_block_script
    assert "function findLastRenderableElement(root)" in helper_block_script
    assert "function hasRenderableTerminalContent(node)" in helper_block_script
    assert "updateMessageText," in helper_facade_script
    assert "syncStreamingCursor," in helper_facade_script
    assert "clearThinkingOpenState," in helper_facade_script
    assert "clearThinkingOpenStateForRun," in helper_facade_script
    assert (
        "scheduleRichTextUpdate(st.activeTextEl, st.activeRaw, { streaming: true }, updateMessageText);"
        in stream_script
    )
    assert "const LARGE_STREAM_TEXT_THRESHOLD = 12000;" in stream_script
    assert "function shouldAppendPlainTextDelta(textEl)" in stream_script
    assert (
        "function scheduleStreamScrollBottom(container, follow = null)" in stream_script
    )
    assert "function captureStreamFollow(container)" in stream_script
    assert "appendDelta: true," in stream_script
    assert (
        "updateMessageText(entry.activeTextEl, entry.activeRaw, { streaming: false });"
        in stream_script
    )
    assert "syncStreamingCursor(entry.activeTextEl, false);" in stream_script
    assert (
        "updateThinkingText(entry.textEl, entry.raw, {\n"
        "        streaming: false,\n"
        "        runId: st.runId || runId,\n"
        "        instanceId: st.instanceId || instanceId,\n"
        "        streamKey: st.streamKey,\n"
        "        partIndex: entry.key,\n"
        "    });" in stream_script
    )
    assert (
        "updateThinkingText(entry.textEl, entry.raw, { streaming: false });"
        not in stream_script
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
    components_css = load_components_css(repo_root)

    assert "@keyframes streamingCaretPulse" in base_css
    assert "opacity: 0.26;" in base_css
    assert "opacity: 1;" in base_css
    assert ".streaming-cursor {" in components_css
    assert ".msg-text.plain-stream-text {" in components_css
    assert "white-space: pre-wrap;" in components_css
    assert "overflow-wrap: anywhere;" in components_css
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


def test_stream_follow_bottom_pauses_while_user_reads_expanded_stream_content(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "messageRenderer"
        / "stream.js"
    ).read_text(encoding="utf-8")
    (tmp_path / "stream.mjs").write_text(
        source.replace("../../core/state.js", "./mockState.mjs")
        .replace("./helpers.js", "./mockHelpers.mjs")
        .replace("../../utils/i18n.js", "./mockI18n.mjs"),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export function getRunPrimaryRoleId() { return ""; }
export function isPrimaryRoleId() { return false; }
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockI18n.mjs").write_text(
        """
export function formatMessage(key) { return key; }
export function t(key) { return key; }
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockHelpers.mjs").write_text(
        """
export function applyToolReturn() {}
export function appendStructuredContentPart() {}
export function appendThinkingText() { return {}; }
export function buildPendingToolBlock() { return { querySelector() { return null; } }; }
export function findToolBlock() { return null; }
export function findToolBlockInContainer() { return null; }
export function indexPendingToolBlock() {}
export function renderMessageBlock(container, _role, _label, _parts = [], options = {}) {
  const contentEl = {
    appendChild() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
  const wrapper = {
    dataset: {
      runId: String(options.runId || ""),
      roleId: String(options.roleId || ""),
      instanceId: String(options.instanceId || ""),
      streamKey: String(options.streamKey || ""),
    },
    querySelector(selector) {
      if (selector === ".msg-content") return contentEl;
      return null;
    },
    closest() { return null; },
  };
  container.children.push(wrapper);
  return { wrapper, contentEl };
}
export function resolvePendingToolBlock() { return null; }
export function scrollBottom() {}
export function setToolStatus() {}
export function setToolValidationFailureState() {}
export function syncStreamingCursor() {}
export function updateThinkingText() {}
export function updateMessageText() {}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "runner.mjs").write_text(
        """
const frames = [];
let now = 1000;
globalThis.performance = { now: () => now };
globalThis.window = {
  requestAnimationFrame(callback) {
    frames.push(callback);
    return frames.length;
  },
};
globalThis.document = {
  createElement() {
    return {
      className: "",
      dataset: {},
      appendChild() {},
      querySelector() { return null; },
      querySelectorAll() { return []; },
      closest() { return null; },
    };
  },
};

class FakeContainer {
  constructor() {
    this.dataset = {};
    this.children = [];
    this.scrollHeight = 1000;
    this.clientHeight = 400;
    this.scrollTop = 600;
    this.listeners = {};
  }
  addEventListener(type, listener) {
    this.listeners[type] = listener;
  }
  dispatch(type, event = {}) {
    this.listeners[type]?.(event);
  }
  querySelectorAll() {
    return [];
  }
}

function flushFrames() {
  while (frames.length) {
    frames.shift()(now);
  }
}

const { appendStreamChunk, getOrCreateStreamBlock } = await import("./stream.mjs");

const container = new FakeContainer();
getOrCreateStreamBlock(container, "inst-1", "Writer", "Writer", "run-1");

appendStreamChunk("inst-1", "first", "run-1", "Writer", "Writer");
container.scrollHeight = 1800;
flushFrames();
const followedFromBottom = container.scrollTop;

container.dispatch("pointerdown", {
  target: { closest: selector => selector.includes("summary") ? {} : null },
});
container.scrollTop = 900;
container.scrollHeight = 2100;
appendStreamChunk("inst-1", "second", "run-1", "Writer", "Writer");
container.scrollHeight = 2400;
flushFrames();
const preservedWhileReading = container.scrollTop;

now += 2500;
container.scrollTop = 2000;
container.dispatch("scroll");
appendStreamChunk("inst-1", "third", "run-1", "Writer", "Writer");
container.scrollHeight = 2600;
flushFrames();
const followedAfterReturningBottom = container.scrollTop;

console.log(JSON.stringify({
  followedFromBottom,
  preservedWhileReading,
  followedAfterReturningBottom,
}));
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(tmp_path / "runner.mjs")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=10,
    )

    assert json.loads(completed.stdout) == {
        "followedFromBottom": 1400,
        "preservedWhileReading": 900,
        "followedAfterReturningBottom": 2200,
    }
