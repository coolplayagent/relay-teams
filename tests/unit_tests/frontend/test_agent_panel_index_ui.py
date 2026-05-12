# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_agent_panel_dom_targets_existing_drawer() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    dom_source = (
        repo_root / "frontend" / "dist" / "js" / "components" / "agentPanel" / "dom.js"
    ).read_text(encoding="utf-8")
    shared_dom_source = (
        repo_root / "frontend" / "dist" / "js" / "utils" / "dom.js"
    ).read_text(encoding="utf-8")

    assert "els.agentDrawer || document.getElementById('agent-drawer')" in dom_source
    assert 'agentDrawer: qs("#agent-drawer")' in shared_dom_source
    assert "subagentWorkspace" not in dom_source
    assert "els.chatMessages.hidden = true" not in dom_source
    assert "els.inputContainer.style.display = 'none'" not in dom_source


def test_agent_panel_history_updates_visible_rail_token_badge() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    history_source = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "agentPanel"
        / "history.js"
    ).read_text(encoding="utf-8")

    assert "getActiveInstanceId" in history_source
    assert "subagent-rail-token-badge" in history_source
    assert "railTokenBadge.innerHTML = html" in history_source


def test_open_agent_panel_does_not_reload_loaded_idle_panel(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "agentPanel"
        / "index.js"
    )
    module_under_test_path = tmp_path / "index.mjs"
    runner_path = tmp_path / "runner.mjs"

    source_text = source_path.read_text(encoding="utf-8")
    for original, replacement in {
        "../../core/api.js": "./mockApi.mjs",
        "../../core/state.js": "./mockState.mjs",
        "../../utils/i18n.js": "./mockI18n.mjs",
        "../../utils/markdown.js": "./mockMarkdown.mjs",
        "./dom.js": "./mockDom.mjs",
        "../contextIndicators.js": "./mockContextIndicators.mjs",
        "./history.js": "./mockHistory.mjs",
        "./panelFactory.js": "./mockPanelFactory.mjs",
        "./state.js": "./mockPanelState.mjs",
        "../messageRenderer.js": "./mockMessageRenderer.mjs",
    }.items():
        source_text = source_text.replace(original, replacement)
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockApi.mjs").write_text(
        "export async function resolveGate() { return undefined; }\n",
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: 'session-1',
    activeRunId: 'run-1',
    isGenerating: false,
    selectedRoleId: null,
    activeView: 'main',
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockI18n.mjs").write_text(
        "export function t(key) { return key; }\n",
        encoding="utf-8",
    )
    (tmp_path / "mockMarkdown.mjs").write_text(
        "export function parseMarkdown(value) { return String(value || ''); }\n",
        encoding="utf-8",
    )
    (tmp_path / "mockDom.mjs").write_text(
        """
export function getDrawer() {
    return { appendChild() { return undefined; } };
}

export function closeDrawerUi() {
    return undefined;
}

export function openDrawerUi() {
    globalThis.__openDrawerUiCalls += 1;
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockContextIndicators.mjs").write_text(
        "export function schedulePanelContextPreview() { return undefined; }\n",
        encoding="utf-8",
    )
    (tmp_path / "mockHistory.mjs").write_text(
        """
export const calls = { loadAgentHistory: 0, syncAgentPanelState: 0 };

export async function loadAgentHistory() {
    calls.loadAgentHistory += 1;
    return undefined;
}

export function syncAgentPanelState() {
    calls.syncAgentPanelState += 1;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockPanelFactory.mjs").write_text(
        """
export function createPanel() {
    throw new Error('createPanel should not be called for existing panel');
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockPanelState.mjs").write_text(
        """
const panel = {
    loadedSessionId: 'session-1',
    loadedRunId: 'run-1',
    panelEl: {
        style: {},
        querySelector() {
            return null;
        },
    },
    scrollEl: {},
};

export function clearPanels() {
    return undefined;
}

export function forEachPanel(callback) {
    callback(panel, 'inst-1');
}

export function getPanel(instanceId) {
    return instanceId === 'inst-1' ? panel : null;
}

export function getPanels() {
    return new Map([['inst-1', panel]]);
}

export function getPendingApprovalsForPanel() {
    return [];
}

export function getActiveInstanceId() {
    return null;
}

export function getActiveRoundRunId() {
    return 'run-1';
}

export function setActiveRoundContext() {
    return undefined;
}

export function setActiveInstanceId() {
    return undefined;
}

export function setPanel() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockMessageRenderer.mjs").write_text(
        "export function getInstanceStreamOverlay() { return null; }\n",
        encoding="utf-8",
    )
    runner_path.write_text(
        """
globalThis.document = {
    getElementById() {
        return {
            textContent: '',
            innerHTML: '',
            hidden: false,
            onclick: null,
            value: '',
        };
    },
};
globalThis.__openDrawerUiCalls = 0;

const { openAgentPanel } = await import('./index.mjs');
const { calls } = await import('./mockHistory.mjs');
const { state } = await import('./mockState.mjs');

openAgentPanel('inst-1', 'writer');
const afterLoadedOpen = { ...calls };
const activeViewAfterHiddenOpen = state.activeView;
openAgentPanel('inst-1', 'writer', { forceRefresh: true });
const afterForcedOpen = { ...calls };
const activeViewAfterHiddenForcedOpen = state.activeView;
openAgentPanel('inst-1', 'writer', { reveal: true });
const activeViewAfterReveal = state.activeView;

console.log(JSON.stringify({
    activeViewAfterHiddenOpen,
    activeViewAfterHiddenForcedOpen,
    activeViewAfterReveal,
    afterLoadedOpen,
    afterForcedOpen,
    openDrawerUiCalls: globalThis.__openDrawerUiCalls,
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
        timeout=5,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout)
    assert payload["afterLoadedOpen"] == {
        "loadAgentHistory": 0,
        "syncAgentPanelState": 1,
    }
    assert payload["afterForcedOpen"] == {
        "loadAgentHistory": 1,
        "syncAgentPanelState": 2,
    }
    assert payload["activeViewAfterHiddenOpen"] == "main"
    assert payload["activeViewAfterHiddenForcedOpen"] == "main"
    assert payload["activeViewAfterReveal"] == "subagent-agent"
    assert payload["openDrawerUiCalls"] == 3


def test_background_panel_sync_does_not_reopen_closed_drawer(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "agentPanel"
        / "index.js"
    )
    module_under_test_path = tmp_path / "index.mjs"
    runner_path = tmp_path / "runner-closed.mjs"

    source_text = source_path.read_text(encoding="utf-8")
    for original, replacement in {
        "../../core/api.js": "./mockApi.mjs",
        "../../core/state.js": "./mockState.mjs",
        "../../utils/i18n.js": "./mockI18n.mjs",
        "../../utils/markdown.js": "./mockMarkdown.mjs",
        "./dom.js": "./mockDom.mjs",
        "../contextIndicators.js": "./mockContextIndicators.mjs",
        "./history.js": "./mockHistory.mjs",
        "./panelFactory.js": "./mockPanelFactory.mjs",
        "./state.js": "./mockPanelState.mjs",
        "../messageRenderer.js": "./mockMessageRenderer.mjs",
    }.items():
        source_text = source_text.replace(original, replacement)
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockApi.mjs").write_text(
        "export async function resolveGate() { return undefined; }\n",
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        "export const state = { currentSessionId: 'session-1', activeRunId: 'run-1', activeView: 'main' };\n",
        encoding="utf-8",
    )
    (tmp_path / "mockI18n.mjs").write_text(
        "export function t(key) { return key; }\n",
        encoding="utf-8",
    )
    (tmp_path / "mockMarkdown.mjs").write_text(
        "export function parseMarkdown(value) { return String(value || ''); }\n",
        encoding="utf-8",
    )
    (tmp_path / "mockDom.mjs").write_text(
        """
const drawer = { hidden: true, appendChild() { return undefined; } };
export function getDrawer() { return drawer; }
export function closeDrawerUi() { drawer.hidden = true; }
export function openDrawerUi() { globalThis.__openDrawerUiCalls += 1; drawer.hidden = false; }
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockContextIndicators.mjs").write_text(
        "export function schedulePanelContextPreview() { return undefined; }\n",
        encoding="utf-8",
    )
    (tmp_path / "mockHistory.mjs").write_text(
        "export async function loadAgentHistory() { return undefined; }\nexport function syncAgentPanelState() {}\n",
        encoding="utf-8",
    )
    (tmp_path / "mockPanelFactory.mjs").write_text(
        "export function createPanel() { return null; }\n",
        encoding="utf-8",
    )
    (tmp_path / "mockPanelState.mjs").write_text(
        """
const panel = { loadedSessionId: 'session-1', loadedRunId: 'run-1', panelEl: { style: {}, querySelector() { return null; } }, scrollEl: {} };
export function clearPanels() {}
export function forEachPanel(callback) { callback(panel, 'inst-1'); }
export function getPanel(instanceId) { return instanceId === 'inst-1' ? panel : null; }
export function getPanels() { return new Map([['inst-1', panel]]); }
export function getPendingApprovalsForPanel() { return []; }
export function getActiveInstanceId() { return null; }
export function getActiveRoundRunId() { return 'run-1'; }
export function setActiveRoundContext() {}
export function setActiveInstanceId() {}
export function setPanel() {}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockMessageRenderer.mjs").write_text(
        "export function getInstanceStreamOverlay() { return null; }\n",
        encoding="utf-8",
    )
    runner_path.write_text(
        """
globalThis.__openDrawerUiCalls = 0;
globalThis.document = { getElementById() { return null; } };
const { openAgentPanel } = await import('./index.mjs');
openAgentPanel('inst-1', 'writer', { reveal: false, forceRefresh: false });
const callsAfterSync = globalThis.__openDrawerUiCalls;
openAgentPanel('inst-1', 'writer', { reveal: true, forceRefresh: false });
console.log(JSON.stringify({ callsAfterSync, callsAfterReveal: globalThis.__openDrawerUiCalls }));
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
        timeout=5,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    payload = json.loads(completed.stdout)
    assert payload == {"callsAfterSync": 0, "callsAfterReveal": 1}
