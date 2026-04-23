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


def test_render_rich_content_can_disable_workspace_image_preview(
    tmp_path: Path,
) -> None:
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

renderRichContent(targetEl, 'Attached image from docs/relay_teams.png', {
    enableWorkspaceImagePreview: false,
});

console.log(JSON.stringify({
    innerHTML: targetEl.innerHTML,
    childCount: targetEl.children.length,
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
    assert "relay_teams.png" in payload["innerHTML"]
    assert payload["childCount"] == 0


def test_read_tool_return_renders_media_ref_preview(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    content_source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "messageRenderer"
        / "helpers"
        / "content.js"
    )
    tool_blocks_source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "messageRenderer"
        / "helpers"
        / "toolBlocks.js"
    )
    content_module_path = tmp_path / "content.mjs"
    tool_blocks_module_path = tmp_path / "toolBlocks.mjs"
    runner_path = tmp_path / "runner.mjs"

    content_module_path.write_text(
        content_source_path.read_text(encoding="utf-8")
        .replace("../../../core/api/workspaces.js", "./mockWorkspacesApi.mjs")
        .replace("../../../core/state.js", "./mockState.mjs")
        .replace("../../../utils/i18n.js", "./mockI18n.mjs")
        .replace("../../../utils/markdown.js", "./mockMarkdown.mjs"),
        encoding="utf-8",
    )
    tool_blocks_module_path.write_text(
        tool_blocks_source_path.read_text(encoding="utf-8")
        .replace("./approval.js", "./mockApproval.mjs")
        .replace("./content.js", "./content.mjs")
        .replace("../../../utils/i18n.js", "./mockI18n.mjs"),
        encoding="utf-8",
    )

    (tmp_path / "mockApproval.mjs").write_text(
        """
export function syncApprovalStateFromEnvelope() {}
""".strip(),
        encoding="utf-8",
    )
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
export function formatMessage(key, values = {}) {
    return `${key}:${JSON.stringify(values)}`;
}

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
class FakeClassList {
    constructor(element) {
        this.element = element;
    }

    add(...names) {
        const tokens = new Set(String(this.element.className || '').split(/\\s+/).filter(Boolean));
        names.forEach(name => tokens.add(name));
        this.element.className = Array.from(tokens).join(' ');
    }

    remove(...names) {
        const removeSet = new Set(names);
        const tokens = String(this.element.className || '').split(/\\s+/).filter(Boolean)
            .filter(name => !removeSet.has(name));
        this.element.className = tokens.join(' ');
    }
}

class FakeElement {
    constructor(tagName = 'div') {
        this.tagName = tagName;
        this.children = [];
        this.parentElement = null;
        this.className = '';
        this.dataset = {};
        this.attributes = {};
        this.src = '';
        this.alt = '';
        this.loading = '';
        this.decoding = '';
        this.textContent = '';
        this._innerHTML = '';
        this.classList = new FakeClassList(this);
    }

    get innerHTML() {
        return this._innerHTML;
    }

    set innerHTML(value) {
        this._innerHTML = String(value || '');
        this.children = [];
        if (this._innerHTML.includes('tool-output')) {
            const output = new FakeElement('div');
            output.className = 'tool-output';
            this.appendChild(output);
        }
        if (this._innerHTML.includes('tool-copy-btn')) {
            const button = new FakeElement('button');
            button.className = 'tool-copy-btn';
            this.appendChild(button);
        }
        if (this._innerHTML.includes('tool-status')) {
            const status = new FakeElement('span');
            status.className = 'tool-status';
            this.appendChild(status);
        }
    }

    appendChild(child) {
        child.parentElement = this;
        this.children.push(child);
        return child;
    }

    replaceChildren(...children) {
        this._innerHTML = '';
        this.children = [];
        children.forEach(child => this.appendChild(child));
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }

    addEventListener() {}

    querySelector(selector) {
        if (!String(selector || '').startsWith('.')) {
            return null;
        }
        const className = selector.slice(1);
        return this.findByClass(className);
    }

    findByClass(className) {
        const tokens = String(this.className || '').split(/\\s+/).filter(Boolean);
        if (tokens.includes(className)) {
            return this;
        }
        for (const child of this.children) {
            const found = child.findByClass(className);
            if (found) {
                return found;
            }
        }
        return null;
    }

    closest(selector) {
        if (!String(selector || '').startsWith('.')) {
            return null;
        }
        const className = selector.slice(1);
        let current = this;
        while (current) {
            const tokens = String(current.className || '').split(/\\s+/).filter(Boolean);
            if (tokens.includes(className)) {
                return current;
            }
            current = current.parentElement;
        }
        return null;
    }
}

globalThis.document = {
    createElement(tagName) {
        return new FakeElement(tagName);
    },
};

const { buildToolBlock, applyToolReturn } = await import('./toolBlocks.mjs');
const toolBlock = buildToolBlock('read', { path: 'docs/relay_teams.png' }, 'call-read-image');
applyToolReturn(toolBlock, {
    ok: true,
    data: {
        type: 'image',
        path: 'docs/relay_teams.png',
        content: [{
            kind: 'media_ref',
            modality: 'image',
            mime_type: 'image/png',
            name: 'relay_teams.png',
            url: '/api/sessions/session-1/media/asset-1/file',
        }],
    },
    error: null,
    meta: { tool_result_event_published: true },
});

const imageEl = toolBlock.querySelector('.msg-image-preview');
console.log(JSON.stringify({
    hasImage: Boolean(imageEl),
    imageSrc: imageEl?.src || '',
    previewTrigger: imageEl?.attributes?.['data-image-preview-trigger'] || '',
    previewSrc: imageEl?.attributes?.['data-image-preview-src'] || '',
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
    assert payload["hasImage"] is True
    assert payload["imageSrc"] == "/api/sessions/session-1/media/asset-1/file"
    assert payload["previewTrigger"] == "true"
    assert payload["previewSrc"] == "/api/sessions/session-1/media/asset-1/file"
