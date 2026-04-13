# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any, cast


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

globalThis.__runScheduledTimers();
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


def test_panel_tabs_are_deselected_by_default_and_activate_on_click(
    tmp_path: Path,
) -> None:
    payload = _run_panel_factory_script(
        tmp_path=tmp_path,
        runner_source="""
const { createPanel } = await import('./panelFactory.mjs');

const panel = createPanel('inst-1', 'writer', () => undefined);
const tabs = panel.panelEl.querySelectorAll('.agent-panel-tab[data-tab]');
const panes = panel.panelEl.querySelectorAll('.agent-panel-tabpane[data-tab]');

const initialTabState = tabs.map(t => ({
    tab: t.dataset.tab,
    selected: t.getAttribute('aria-selected'),
}));
const initialPaneState = panes.map(p => ({
    tab: p.dataset.tab,
    hidden: p.hidden,
}));

// click the first tab (prompt)
tabs[0].onclick();

const afterClickTabState = tabs.map(t => ({
    tab: t.dataset.tab,
    selected: t.getAttribute('aria-selected'),
}));
const afterClickPaneState = panes.map(p => ({
    tab: p.dataset.tab,
    hidden: p.hidden,
}));

console.log(JSON.stringify({ initialTabState, initialPaneState, afterClickTabState, afterClickPaneState }));
""".strip(),
    )

    initial_tabs = cast(list[dict[str, Any]], payload["initialTabState"])
    initial_panes = cast(list[dict[str, Any]], payload["initialPaneState"])
    after_tabs = cast(list[dict[str, Any]], payload["afterClickTabState"])
    after_panes = cast(list[dict[str, Any]], payload["afterClickPaneState"])

    # all tabs deselected initially
    for tab_info in initial_tabs:
        assert tab_info["selected"] == "false"
    # all panes hidden initially
    for pane_info in initial_panes:
        assert pane_info["hidden"] is True

    # after clicking prompt tab, only prompt is selected
    for tab_info in after_tabs:
        if tab_info["tab"] == "prompt":
            assert tab_info["selected"] == "true"
        else:
            assert tab_info["selected"] == "false"
    # after clicking prompt tab, only prompt pane is visible
    for pane_info in after_panes:
        if pane_info["tab"] == "prompt":
            assert pane_info["hidden"] is False
        else:
            assert pane_info["hidden"] is True


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
        "../../utils/i18n.js": "./mockI18n.mjs",
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
    (tmp_path / "mockI18n.mjs").write_text(
        """
const translations = {
    "subagent.reflect_title": "Refresh reflection memory",
    "subagent.reflect": "Reflect",
    "subagent.reflecting": "Reflecting...",
    "subagent.reflecting_title": "Refreshing reflection memory",
    "subagent.reflected": "Reflected",
    "subagent.reflected_title": "Reflection refreshed",
    "subagent.retry_reflect": "Retry reflect",
    "subagent.reflect_failed_title": "Reflection refresh failed",
    "subagent.stop_title": "Stop this subagent",
    "subagent.stop": "Stop",
    "subagent.sections": "Agent sections",
    "subagent.prompt": "Prompt",
    "subagent.tools": "Tools",
    "subagent.memory": "Memory",
    "subagent.tasks": "Tasks",
    "subagent.no_runtime_prompt": "No runtime system prompt yet.",
    "subagent.no_runtime_tools": "No runtime tools snapshot yet.",
    "subagent.reflection_actions": "Reflection memory actions",
    "subagent.edit_reflection": "Edit reflection memory",
    "subagent.delete_reflection": "Delete reflection memory",
    "subagent.no_reflection_memory": "No reflection memory yet.",
    "subagent.status_idle": "Idle",
    "subagent.no_tasks": "No delegated tasks yet.",
    "subagent.inject_placeholder": "Inject message to this agent...",
    "composer.context_title": "Prompt / context window",
    "composer.send_title": "Send (Enter)",
    "subagent.delete_reflection_confirm": "Delete reflection memory for this subagent role?",
    "subagent.reflection_placeholder": "Write long-term notes for this subagent role...",
};

export function t(key) {
    return translations[key] || key;
}

export function formatMessage(key, values = {}) {
    return Object.entries(values).reduce(
        (message, [name, value]) => message.replaceAll(`{${name}}`, String(value)),
        t(key),
    );
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
            '.agent-panel-runtime-prompt-meta',
            '.agent-panel-runtime-prompt-body',
            '.agent-panel-runtime-tools-meta',
            '.agent-panel-runtime-tools-body',
            '.agent-panel-reflection-meta',
            '.agent-panel-reflection-body',
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

        // Register tab and pane elements for the tab bar
        this._tabs = [];
        this._panes = [];
        for (const tabName of ['prompt', 'tools', 'memory', 'tasks']) {{
            const tab = new FakeElement();
            tab.dataset.tab = tabName;
            tab.setAttribute('aria-selected', 'false');
            tab.className = 'agent-panel-tab';
            this._tabs.push(tab);

            const pane = new FakeElement();
            pane.dataset.tab = tabName;
            pane.hidden = true;
            pane.className = 'agent-panel-tabpane';
            this._panes.push(pane);
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

    querySelectorAll(selector) {{
        // Tab/pane attribute selectors - return from registered arrays only
        if (this._tabs && selector.includes('.agent-panel-tab[data-tab]')) {{
            return [...this._tabs];
        }}
        if (this._panes && selector.includes('.agent-panel-tabpane[data-tab]')) {{
            return [...this._panes];
        }}
        const results = [];
        if (this._children.has(selector)) {{
            results.push(this._children.get(selector));
        }}
        for (const child of this._children.values()) {{
            if (typeof child.querySelectorAll === 'function') {{
                results.push(...child.querySelectorAll(selector));
            }}
        }}
        return results;
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
const __timers = [];
globalThis.setTimeout = (fn) => {{
    __timers.push(fn);
    return __timers.length;
}};
globalThis.clearTimeout = () => undefined;
globalThis.__runScheduledTimers = () => {{
    while (__timers.length > 0) {{
        const fn = __timers.shift();
        if (typeof fn === 'function') fn();
    }}
}};
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
        timeout=3,
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
