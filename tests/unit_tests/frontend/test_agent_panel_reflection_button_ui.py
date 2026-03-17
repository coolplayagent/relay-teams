# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import cast


def test_reflect_button_shows_loading_then_success_then_resets(tmp_path: Path) -> None:
    payload = _run_panel_factory_script(
        tmp_path=tmp_path,
        runner_source="""
const { createPanel } = await import('./panelFactory.mjs');
const { state } = await import('./mockState.mjs');
const { calls } = await import('./mockHistory.mjs');
const { calls: railCalls } = await import('./mockSubagentRail.mjs');

state.currentSessionId = 'session-1';
state.sessionAgents = [
    {
        instance_id: 'inst-1',
        role_id: 'writer',
        reflection_summary_preview: '',
        reflection_updated_at: '',
    },
];

const panel = createPanel('inst-1', 'writer', () => undefined);
const button = panel.panelEl.querySelector('.agent-panel-refresh-reflection');
const clickPromise = button.onclick();
const loadingState = {
    text: button.textContent,
    disabled: button.disabled,
    state: button.dataset.state,
};

globalThis.__resolveReflection({
    preview: 'Use concise drafts.',
    updated_at: '2026-03-16T08:20:45.539634+00:00',
});
await clickPromise;
const successState = {
    text: button.textContent,
    disabled: button.disabled,
    state: button.dataset.state,
};

await new Promise(resolve => setTimeout(resolve, 2600));
const resetState = {
    text: button.textContent,
    disabled: button.disabled,
    state: button.dataset.state,
};

console.log(JSON.stringify({
    loadingState,
    successState,
    resetState,
    historyCalls: calls.loadAgentHistory,
    railCalls: railCalls.refreshSubagentRail,
    sessionAgent: state.sessionAgents[0],
}));
""".strip(),
    )

    assert payload["loadingState"] == {
        "text": "Reflecting...",
        "disabled": True,
        "state": "loading",
    }
    assert payload["successState"] == {
        "text": "Reflected",
        "disabled": False,
        "state": "success",
    }
    assert payload["resetState"] == {
        "text": "Reflect",
        "disabled": False,
        "state": "idle",
    }
    assert payload["historyCalls"] == 1
    assert payload["railCalls"] == 1
    session_agent = cast(dict[str, object], payload["sessionAgent"])
    assert session_agent["reflection_summary_preview"] == "Use concise drafts."
    assert session_agent["reflection_updated_at"] == "2026-03-16T08:20:45.539634+00:00"


def test_reflection_memory_supports_inline_edit_and_delete(tmp_path: Path) -> None:
    payload = _run_panel_factory_script(
        tmp_path=tmp_path,
        runner_source="""
const { createPanel } = await import('./panelFactory.mjs');
const { state } = await import('./mockState.mjs');
const { calls } = await import('./mockApi.mjs');
const { calls: historyCalls } = await import('./mockHistory.mjs');
const { calls: railCalls } = await import('./mockSubagentRail.mjs');

globalThis.confirm = () => true;
state.currentSessionId = 'session-1';
state.sessionAgents = [
    {
        instance_id: 'inst-1',
        role_id: 'writer',
        reflection_summary_preview: 'Old note',
        reflection_updated_at: '2026-03-16T08:00:00Z',
    },
];

const panel = createPanel('inst-1', 'writer', () => undefined);
const body = panel.panelEl.querySelector('.agent-panel-reflection-body');
body.dataset.summary = 'Old note';
body.textContent = 'Old note';

panel.panelEl.querySelector('.agent-panel-reflection-edit').onclick();
const editor = panel.panelEl.querySelector('.agent-panel-reflection-editor-input');
editor.value = 'Edited by human';
await panel.panelEl.querySelector('.agent-panel-reflection-save').onclick();

const afterEdit = {
    updateCalls: calls.updateAgentReflection,
    summaryPreview: state.sessionAgents[0].reflection_summary_preview,
    updatedAt: state.sessionAgents[0].reflection_updated_at,
    historyCalls: historyCalls.loadAgentHistory,
    railCalls: railCalls.refreshSubagentRail,
};

await panel.panelEl.querySelector('.agent-panel-reflection-delete').onclick();

console.log(JSON.stringify({
    afterEdit,
    deleteCalls: calls.deleteAgentReflection,
    afterDelete: state.sessionAgents[0],
    historyCalls: historyCalls.loadAgentHistory,
    railCalls: railCalls.refreshSubagentRail,
}));
""".strip(),
    )

    assert payload["afterEdit"] == {
        "updateCalls": [["session-1", "inst-1", "Edited by human"]],
        "summaryPreview": "Edited by human",
        "updatedAt": "2026-03-16T08:31:00Z",
        "historyCalls": 1,
        "railCalls": 1,
    }
    assert payload["deleteCalls"] == [["session-1", "inst-1"]]
    after_delete = cast(dict[str, object], payload["afterDelete"])
    assert after_delete["reflection_summary_preview"] == ""
    assert after_delete["reflection_updated_at"] == ""
    assert payload["historyCalls"] == 2
    assert payload["railCalls"] == 2


def test_panel_sections_are_collapsed_by_default_and_expand_on_click(
    tmp_path: Path,
) -> None:
    payload = _run_panel_factory_script(
        tmp_path=tmp_path,
        runner_source="""
const { createPanel } = await import('./panelFactory.mjs');

const panel = createPanel('inst-1', 'writer', () => undefined);
const promptSection = panel.panelEl.querySelector('.agent-panel-runtime-prompt');
const promptToggle = panel.panelEl.querySelector('.agent-panel-runtime-prompt-toggle');
const promptBody = panel.panelEl.querySelector('.agent-panel-runtime-prompt-body');
const toolsSection = panel.panelEl.querySelector('.agent-panel-runtime-tools');
const toolsToggle = panel.panelEl.querySelector('.agent-panel-runtime-tools-toggle');
const toolsBody = panel.panelEl.querySelector('.agent-panel-runtime-tools-body');
const reflectionSection = panel.panelEl.querySelector('.agent-panel-reflection');
const reflectionToggle = panel.panelEl.querySelector('.agent-panel-reflection-toggle');
const reflectionBody = panel.panelEl.querySelector('.agent-panel-reflection-body');
const summarySection = panel.panelEl.querySelector('.agent-panel-summary');
const summaryToggle = panel.panelEl.querySelector('.agent-panel-summary-toggle');
const summaryBody = panel.panelEl.querySelector('.agent-panel-summary-body');

const initialState = {
    promptExpanded: promptToggle.getAttribute('aria-expanded'),
    promptHidden: promptBody.hidden,
    promptCollapsed: promptSection.dataset.collapsed,
    toolsExpanded: toolsToggle.getAttribute('aria-expanded'),
    toolsHidden: toolsBody.hidden,
    toolsCollapsed: toolsSection.dataset.collapsed,
    reflectionExpanded: reflectionToggle.getAttribute('aria-expanded'),
    reflectionHidden: reflectionBody.hidden,
    reflectionCollapsed: reflectionSection.dataset.collapsed,
    summaryExpanded: summaryToggle.getAttribute('aria-expanded'),
    summaryHidden: summaryBody.hidden,
    summaryCollapsed: summarySection.dataset.collapsed,
};

promptToggle.onclick();
toolsToggle.onclick();
reflectionToggle.onclick();
summaryToggle.onclick();

const expandedState = {
    promptExpanded: promptToggle.getAttribute('aria-expanded'),
    promptHidden: promptBody.hidden,
    promptCollapsed: promptSection.dataset.collapsed,
    toolsExpanded: toolsToggle.getAttribute('aria-expanded'),
    toolsHidden: toolsBody.hidden,
    toolsCollapsed: toolsSection.dataset.collapsed,
    reflectionExpanded: reflectionToggle.getAttribute('aria-expanded'),
    reflectionHidden: reflectionBody.hidden,
    reflectionCollapsed: reflectionSection.dataset.collapsed,
    summaryExpanded: summaryToggle.getAttribute('aria-expanded'),
    summaryHidden: summaryBody.hidden,
    summaryCollapsed: summarySection.dataset.collapsed,
};

console.log(JSON.stringify({ initialState, expandedState }));
""".strip(),
    )

    assert payload["initialState"] == {
        "promptExpanded": "false",
        "promptHidden": True,
        "promptCollapsed": "true",
        "toolsExpanded": "false",
        "toolsHidden": True,
        "toolsCollapsed": "true",
        "reflectionExpanded": "false",
        "reflectionHidden": True,
        "reflectionCollapsed": "true",
        "summaryExpanded": "false",
        "summaryHidden": True,
        "summaryCollapsed": "true",
    }
    assert payload["expandedState"] == {
        "promptExpanded": "true",
        "promptHidden": False,
        "promptCollapsed": "false",
        "toolsExpanded": "true",
        "toolsHidden": False,
        "toolsCollapsed": "false",
        "reflectionExpanded": "true",
        "reflectionHidden": False,
        "reflectionCollapsed": "false",
        "summaryExpanded": "true",
        "summaryHidden": False,
        "summaryCollapsed": "false",
    }


def _run_panel_factory_script(tmp_path: Path, runner_source: str) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "agentPanel"
        / "panelFactory.js"
    )

    module_under_test_path = tmp_path / "panelFactory.mjs"
    runner_path = tmp_path / "runner.mjs"

    replacements = {
        "../../core/api.js": "./mockApi.mjs",
        "../../app/recovery.js": "./mockRecovery.mjs",
        "../contextIndicators.js": "./mockContextIndicators.mjs",
        "../../core/state.js": "./mockState.mjs",
        "../../utils/logger.js": "./mockLogger.mjs",
        "./dom.js": "./mockDom.mjs",
        "./history.js": "./mockHistory.mjs",
        "../subagentRail.js": "./mockSubagentRail.mjs",
    }
    source_text = source_path.read_text(encoding="utf-8")
    for original, replacement in replacements.items():
        source_text = source_text.replace(original, replacement)
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockApi.mjs").write_text(
        """
export const calls = {
    updateAgentReflection: [],
    deleteAgentReflection: [],
};

export async function injectSubagentMessage() {
    return undefined;
}

export async function stopRun() {
    return undefined;
}

export async function refreshAgentReflection() {
    return await new Promise(resolve => {
        globalThis.__resolveReflection = resolve;
    });
}

export async function updateAgentReflection(sessionId, instanceId, summary) {
    calls.updateAgentReflection.push([sessionId, instanceId, summary]);
    return {
        preview: summary,
        updated_at: '2026-03-16T08:31:00Z',
    };
}

export async function deleteAgentReflection(sessionId, instanceId) {
    calls.deleteAgentReflection.push([sessionId, instanceId]);
    return {
        preview: '',
        updated_at: '',
    };
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockRecovery.mjs").write_text(
        """
export async function refreshSessionRecovery() {
    return undefined;
}

export async function resumeRecoverableRun() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockContextIndicators.mjs").write_text(
        """
export function bindPanelContextIndicator() {
    return undefined;
}

export function schedulePanelContextPreview() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: null,
    activeRunId: null,
    currentRecoverySnapshot: null,
    pausedSubagent: null,
    sessionAgents: [],
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockLogger.mjs").write_text(
        """
export function sysLog() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockDom.mjs").write_text(
        """
const drawer = {
    children: [],
    appendChild(child) {
        this.children.push(child);
        return child;
    },
};

export function getDrawer() {
    return drawer;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockHistory.mjs").write_text(
        """
export const calls = { loadAgentHistory: 0 };

export async function loadAgentHistory() {
    calls.loadAgentHistory += 1;
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockSubagentRail.mjs").write_text(
        """
export const calls = { refreshSubagentRail: 0 };

export async function refreshSubagentRail() {
    calls.refreshSubagentRail += 1;
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )

    runner_path.write_text(
        f"""
class FakeElement {{
    constructor() {{
        this.className = '';
        this.dataset = {{}};
        this.style = {{}};
        this.disabled = false;
        this.textContent = '';
        this.title = '';
        this.value = '';
        this.hidden = false;
        this.scrollHeight = 24;
        this.listeners = new Map();
        this.attributes = new Map();
        this._children = new Map();
    }}

    set innerHTML(value) {{
        this._innerHTML = value;
        this._children = new Map();
        if (String(value).includes('agent-panel-stop')) {{
            this._registerRootChildren();
            return;
        }}
        if (String(value).includes('agent-panel-reflection-editor-input')) {{
            const editor = new FakeElement();
            const match = String(value).match(/<textarea[^>]*>([\\s\\S]*?)<\\/textarea>/);
            editor.value = match ? match[1]
                .replaceAll('&amp;', '&')
                .replaceAll('&lt;', '<')
                .replaceAll('&gt;', '>')
                .replaceAll('&quot;', '"')
                .replaceAll('&#39;', "'")
                : '';
            this._children.set('.agent-panel-reflection-editor-input', editor);
            this._children.set('.agent-panel-reflection-cancel', new FakeElement());
            this._children.set('.agent-panel-reflection-save', new FakeElement());
        }}
    }}

    _registerRootChildren() {{
        for (const selector of [
            '.agent-panel-stop',
            '.agent-panel-refresh-reflection',
            '.agent-panel-reflection-edit',
            '.agent-panel-reflection-delete',
            '.panel-inject-input',
            '.panel-send-btn',
            '.agent-panel-scroll',
            '.agent-token-usage',
            '.agent-panel-runtime-prompt',
            '.agent-panel-runtime-prompt-toggle',
            '.agent-panel-runtime-prompt-meta',
            '.agent-panel-runtime-prompt-body',
            '.agent-panel-runtime-tools',
            '.agent-panel-runtime-tools-toggle',
            '.agent-panel-runtime-tools-meta',
            '.agent-panel-runtime-tools-body',
            '.agent-panel-reflection',
            '.agent-panel-reflection-toggle',
            '.agent-panel-reflection-meta',
            '.agent-panel-reflection-body',
            '.agent-panel-summary',
            '.agent-panel-summary-toggle',
            '.agent-panel-summary-body',
            '.agent-panel-summary-status',
            '.agent-panel-summary-updated',
            '.agent-panel-summary-tasks',
        ]) {{
            const child = new FakeElement();
            if (selector === '.agent-panel-refresh-reflection') {{
                child.textContent = 'Reflect';
                child.title = 'Refresh reflection memory';
            }}
            this._children.set(selector, child);
        }}
    }}

    get innerHTML() {{
        return this._innerHTML || '';
    }}

    querySelector(selector) {{
        if (this._children.has(selector)) {{
            return this._children.get(selector) || null;
        }}
        for (const child of this._children.values()) {{
            const nested = child.querySelector(selector);
            if (nested) return nested;
        }}
        return null;
    }}

    appendChild(child) {{
        this.lastChild = child;
        return child;
    }}

    addEventListener(type, handler) {{
        this.listeners.set(type, handler);
    }}

    setAttribute(name, value) {{
        this.attributes.set(name, String(value));
    }}

    getAttribute(name) {{
        return this.attributes.get(name) || null;
    }}

    focus() {{
        return undefined;
    }}

    setSelectionRange() {{
        return undefined;
    }}

    click() {{
        if (typeof this.onclick === 'function') {{
            return this.onclick();
        }}
        return undefined;
    }}
}}

globalThis.window = globalThis;
globalThis.document = {{
    createElement() {{
        return new FakeElement();
    }},
}};

{runner_source}
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
            f"Node runner failed:\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )

    return json.loads(completed.stdout)


def test_layout_css_keeps_reflect_button_styles() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    css_path = repo_root / "frontend" / "dist" / "css" / "layout.css"
    css_text = css_path.read_text(encoding="utf-8")

    assert ".agent-panel-refresh-reflection," in css_text
    assert ".agent-panel-refresh-reflection:hover" in css_text
    assert ".agent-panel-icon-btn" in css_text
    assert ".agent-panel-reflection-editor-input" in css_text
    assert ".agent-panel-runtime-prompt-body" in css_text
    assert ".agent-panel-runtime-tools-body" in css_text
    assert ".agent-panel-runtime-pre" in css_text
    assert ".agent-panel-json-pre" in css_text
    assert "font-size: 0.78rem;" in css_text
    assert "line-height: 1.5;" in css_text
    assert "max-height: 20rem;" in css_text
    assert "overflow: auto;" in css_text
    assert "border: none;" in css_text
    assert "background: transparent;" in css_text
    assert "scrollbar-gutter: stable;" in css_text
    assert "scrollbar-width: thin;" in css_text
    assert ".agent-panel-section-body[hidden]" in css_text
    assert "display: none !important;" in css_text
