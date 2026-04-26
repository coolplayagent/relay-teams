# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_long_stream_text_uses_plain_append_renderer(tmp_path: Path) -> None:
    payload = _run_block_helper_script(
        tmp_path,
        """
const { updateMessageText, updateThinkingText } = await import('./block.mjs');

const shortEl = document.createElement('div');
updateMessageText(shortEl, 'short **markdown**', { streaming: true });

const longEl = document.createElement('div');
const longText = 'x'.repeat(13000);
updateMessageText(longEl, longText, { streaming: true });
updateMessageText(longEl, `${longText}y`, { streaming: true });
syncTextContent(longEl);

const thinkingEl = document.createElement('div');
const thinkingText = 'z'.repeat(100000);
updateThinkingText(thinkingEl, thinkingText, { streaming: true });
syncTextContent(thinkingEl);

console.log(JSON.stringify({
    richCalls: globalThis.__richCalls,
    longMode: longEl.dataset.renderMode,
    longTextLength: longEl.textContent.length,
    longTextNodes: countTextNodes(longEl),
    thinkingMode: thinkingEl.dataset.renderMode,
    thinkingTextLength: thinkingEl.textContent.length,
    thinkingTextNodes: countTextNodes(thinkingEl),
}));
""",
    )

    assert payload == {
        "richCalls": [18],
        "longMode": "plain-stream",
        "longTextLength": 13001,
        "longTextNodes": 1,
        "thinkingMode": "plain-stream",
        "thinkingTextLength": 100000,
        "thinkingTextNodes": 7,
    }


def test_stream_crossing_plain_threshold_keeps_rendered_prefix(
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
export function buildPendingToolBlock() { return {}; }
export function findToolBlock() { return null; }
export function findToolBlockInContainer() { return null; }
export function indexPendingToolBlock() {}
export function renderMessageBlock(container, _role, _label, _parts = [], options = {}) {
  const contentEl = document.createElement("div");
  const wrapper = document.createElement("div");
  wrapper.dataset = {
    runId: String(options.runId || ""),
    roleId: String(options.roleId || ""),
    instanceId: String(options.instanceId || ""),
    streamKey: String(options.streamKey || ""),
  };
  wrapper.querySelector = selector => selector === ".msg-content" ? contentEl : null;
  container.appendChild(wrapper);
  return { wrapper, contentEl };
}
export function resolvePendingToolBlock() { return null; }
export function setToolStatus() {}
export function setToolValidationFailureState() {}
export function syncStreamingCursor() {}
export function updateThinkingText() {}
export function updateMessageText(textEl, text, options = {}) {
  globalThis.__textUpdates.push({
    length: String(text || "").length,
    appendDelta: options.appendDelta === true,
    streaming: options.streaming === true,
  });
  textEl.textContent = String(text || "");
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "runner.mjs").write_text(
        """
globalThis.__textUpdates = [];
globalThis.document = {
  createElement() {
    return {
      className: "",
      dataset: {},
      textContent: "",
      children: [],
      appendChild(child) {
        this.children.push(child);
        child.parentNode = this;
        return child;
      },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      closest() { return null; },
    };
  },
};

const { appendStreamChunk, getOrCreateStreamBlock } = await import("./stream.mjs");
const container = document.createElement("div");
container.scrollHeight = 1000;
container.clientHeight = 500;
container.scrollTop = 500;
container.addEventListener = () => {};
getOrCreateStreamBlock(container, "inst-1", "Writer", "Writer", "run-1");
appendStreamChunk("inst-1", "x".repeat(10000), "run-1", "Writer", "Writer");
appendStreamChunk("inst-1", "y".repeat(3000), "run-1", "Writer", "Writer");

console.log(JSON.stringify(globalThis.__textUpdates));
""".strip(),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", str(tmp_path / "runner.mjs")],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Node runner failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    assert json.loads(result.stdout) == [
        {"length": 10000, "appendDelta": False, "streaming": True},
        {"length": 13000, "appendDelta": False, "streaming": True},
    ]


def _run_block_helper_script(tmp_path: Path, runner_source: str) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "messageRenderer"
        / "helpers"
        / "block.js"
    )
    source = (
        source_path.read_text(encoding="utf-8")
        .replace("../../../core/state.js", "./mockState.mjs")
        .replace("../../../utils/i18n.js", "./mockI18n.mjs")
        .replace("./toolBlocks.js", "./mockToolBlocks.mjs")
        .replace("./content.js", "./mockContent.mjs")
        .replace("./prompt.js", "./mockPrompt.mjs")
    )
    (tmp_path / "block.mjs").write_text(source, encoding="utf-8")
    (tmp_path / "mockState.mjs").write_text(
        """
export function getPrimaryRoleLabel() { return 'Main Agent'; }
export function isCoordinatorRoleId() { return false; }
export function isMainAgentRoleId() { return false; }
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockI18n.mjs").write_text(
        """
export function t(key) { return String(key || ''); }
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockToolBlocks.mjs").write_text(
        """
export function applyToolReturn() {}
export function buildToolBlock() { return document.createElement('div'); }
export function indexPendingToolBlock() {}
export function resolvePendingToolBlock() { return null; }
export function setToolValidationFailureState() {}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockContent.mjs").write_text(
        """
export function appendStructuredContentPart() { return null; }
export function renderRichContent(targetEl, source) {
    globalThis.__richCalls.push(String(source || '').length);
    targetEl.replaceChildren(document.createTextNode(String(source || '')));
    return targetEl;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockPrompt.mjs").write_text(
        """
export function appendPromptContentBlock() { return document.createElement('div'); }
export function normalizePromptContentPart(item) { return item; }
export function updatePromptContentBlock() { return null; }
""".strip(),
        encoding="utf-8",
    )
    runner_path = tmp_path / "runner.mjs"
    runner_path.write_text(
        f"""
globalThis.__richCalls = [];
globalThis.Node = {{ TEXT_NODE: 3, ELEMENT_NODE: 1 }};

class FakeClassList {{
    constructor(owner) {{ this.owner = owner; }}
    add(...classes) {{
        const next = new Set(String(this.owner.className || '').split(/\\s+/).filter(Boolean));
        classes.forEach(cls => next.add(cls));
        this.owner.className = Array.from(next).join(' ');
    }}
    remove(...classes) {{
        const blocked = new Set(classes);
        this.owner.className = String(this.owner.className || '')
            .split(/\\s+/)
            .filter(cls => cls && !blocked.has(cls))
            .join(' ');
    }}
    contains(cls) {{
        return String(this.owner.className || '').split(/\\s+/).includes(cls);
    }}
}}

class FakeText {{
    constructor(text = '') {{
        this.nodeType = 3;
        this.textContent = String(text || '');
        this.parentNode = null;
    }}
    remove() {{
        if (!this.parentNode) return;
        this.parentNode.children = this.parentNode.children.filter(child => child !== this);
        this.parentNode.childNodes = this.parentNode.children;
        this.parentNode = null;
    }}
}}

class FakeElement {{
    constructor(tagName = 'div') {{
        this.nodeType = 1;
        this.tagName = String(tagName || 'div').toUpperCase();
        this.children = [];
        this.childNodes = this.children;
        this.parentNode = null;
        this.dataset = {{}};
        this.className = '';
        this.classList = new FakeClassList(this);
        this.textContent = '';
    }}
    appendChild(node) {{
        node.parentNode = this;
        this.children.push(node);
        this.childNodes = this.children;
        syncTextContent(this);
        return node;
    }}
    replaceChildren(...nodes) {{
        this.children.forEach(child => {{ child.parentNode = null; }});
        this.children = [];
        this.childNodes = this.children;
        nodes.forEach(node => this.appendChild(node));
        syncTextContent(this);
    }}
    querySelector(selector) {{
        return this.querySelectorAll(selector)[0] || null;
    }}
    querySelectorAll(selector) {{
        const results = [];
        const className = String(selector || '').startsWith('.')
            ? String(selector).slice(1)
            : '';
        walk(this, node => {{
            if (node.nodeType === 1 && className && node.classList.contains(className)) {{
                results.push(node);
            }}
        }});
        return results;
    }}
    closest() {{ return null; }}
    setAttribute() {{}}
    remove() {{
        if (!this.parentNode) return;
        this.parentNode.children = this.parentNode.children.filter(child => child !== this);
        this.parentNode.childNodes = this.parentNode.children;
        this.parentNode = null;
    }}
}}

function walk(root, visit) {{
    (root.children || []).forEach(child => {{
        visit(child);
        if (child.nodeType === 1) walk(child, visit);
    }});
}}

function countTextNodes(root) {{
    let count = 0;
    walk(root, node => {{
        if (node.nodeType === 3 && String(node.textContent || '').length > 0) count += 1;
    }});
    return count;
}}

function syncTextContent(root) {{
    if (!root || root.nodeType !== 1) return;
    root.textContent = (root.children || []).map(child => {{
        if (child.nodeType === 3) return String(child.textContent || '');
        syncTextContent(child);
        return String(child.textContent || '');
    }}).join('');
}}

globalThis.document = {{
    createElement(tagName) {{ return new FakeElement(tagName); }},
    createTextNode(text) {{ return new FakeText(text); }},
}};

{runner_source}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(runner_path)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Node runner failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return json.loads(result.stdout)
