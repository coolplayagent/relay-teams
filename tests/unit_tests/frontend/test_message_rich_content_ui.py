# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_render_rich_content_appends_workspace_image_preview(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "messageRenderer"
        / "helpers"
        / "content.js"
    )
    module_under_test_path = tmp_path / "content.mjs"
    runner_path = tmp_path / "runner.mjs"

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../../../core/api/workspaces.js", "./mockWorkspacesApi.mjs")
        .replace("../../../core/state.js", "./mockState.mjs")
        .replace("../../../utils/i18n.js", "./mockI18n.mjs")
        .replace("../../../utils/markdown.js", "./mockMarkdown.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockWorkspacesApi.mjs").write_text(
        """
export function buildWorkspaceImagePreviewUrl(workspaceId, path) {
    return `/preview/${workspaceId}/${encodeURIComponent(path)}`;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
    export const state = {
        currentWorkspaceId: 'hello',
        currentProjectViewWorkspaceId: null,
    };
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockI18n.mjs").write_text(
        """
export function t(key) {
    return String(key || '');
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockMarkdown.mjs").write_text(
        """
export function parseMarkdown(text) {
    return `<p>${text}</p>`;
}
""".strip(),
        encoding="utf-8",
    )
    runner_path.write_text(
        """
class FakeElement {
    constructor(tagName = 'div') {
        this.tagName = tagName;
        this.children = [];
        this.className = '';
        this.innerHTML = '';
        this.src = '';
        this.alt = '';
        this.loading = '';
        this.decoding = '';
        this.attributes = {};
    }

    appendChild(child) {
        this.children.push(child);
        return child;
    }

    replaceChildren(...children) {
        this.innerHTML = '';
        this.children = children;
    }

    setAttribute(name, value) {
        this.attributes[name] = value;
    }
}

globalThis.document = {
    createElement(tagName) {
        return new FakeElement(tagName);
    },
};

const { renderRichContent } = await import('./content.mjs');
const targetEl = new FakeElement('div');

renderRichContent(targetEl, '已生成 `ai_briefing.png`（17.7KB）。');

console.log(JSON.stringify({
    innerHTML: targetEl.innerHTML,
    childCount: targetEl.children.length,
    figureClassName: targetEl.children[0]?.className || '',
    imageSrc: targetEl.children[0]?.children[0]?.src || '',
}));
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(runner_path)],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        encoding="utf-8",
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout)
    assert "ai_briefing.png" in payload["innerHTML"]
    assert payload["childCount"] == 1
    assert payload["figureClassName"] == "msg-image"
    assert payload["imageSrc"] == "/preview/hello/ai_briefing.png"
