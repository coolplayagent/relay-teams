# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import cast


def test_context_indicator_uses_latest_prompt_tokens_and_role_profile_context_window(
    tmp_path: Path,
) -> None:
    payload = _run_context_indicators_script(
        tmp_path=tmp_path,
        runner_source="""
const { refreshVisibleContextIndicators } = await import('./contextIndicators.mjs');
const { state, setNormalModeRoles } = await import('./mockState.mjs');

state.currentSessionId = 'session-1';
state.currentSessionMode = 'normal';
state.currentNormalRootRoleId = 'writer';
state.activeRunId = 'run-1';
setNormalModeRoles([
    { role_id: 'writer', model_profile: 'writer-profile' },
]);

await refreshVisibleContextIndicators({ immediate: true });
await new Promise(resolve => setTimeout(resolve, 0));

const indicator = globalThis.document.getElementById('main-context-indicator');
console.log(JSON.stringify({
    textContent: indicator.textContent,
    title: indicator.title,
    state: indicator.dataset.state,
}));
""".strip(),
    )

    assert payload["textContent"] == "321 / 64k"
    assert payload["state"] == "ready"
    assert "321" in cast(str, payload["title"])
    assert "64000" in cast(str, payload["title"])


def test_context_indicator_falls_back_to_default_profile_when_role_profile_missing(
    tmp_path: Path,
) -> None:
    payload = _run_context_indicators_script(
        tmp_path=tmp_path,
        runner_source="""
const { refreshVisibleContextIndicators } = await import('./contextIndicators.mjs');
const { state, setNormalModeRoles } = await import('./mockState.mjs');

state.currentSessionId = 'session-1';
state.currentSessionMode = 'normal';
state.currentNormalRootRoleId = 'writer';
state.activeRunId = 'run-1';
setNormalModeRoles([
    { role_id: 'writer', model_profile: 'missing-profile' },
]);

await refreshVisibleContextIndicators({ immediate: true });
await new Promise(resolve => setTimeout(resolve, 0));

const indicator = globalThis.document.getElementById('main-context-indicator');
console.log(JSON.stringify({
    textContent: indicator.textContent,
    title: indicator.title,
}));
""".strip(),
    )

    assert payload["textContent"] == "321 / 128k"
    assert "128000" in cast(str, payload["title"])


def _run_context_indicators_script(
    tmp_path: Path,
    runner_source: str,
) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "contextIndicators.js"
    )
    module_under_test_path = tmp_path / "contextIndicators.mjs"
    runner_path = tmp_path / "runner.mjs"

    replacements = {
        "../core/api.js": "./mockApi.mjs",
        "../core/state.js": "./mockState.mjs",
        "../utils/dom.js": "./mockDom.mjs",
        "../utils/i18n.js": "./mockI18n.mjs",
        "./rounds/timeline.js": "./mockTimeline.mjs",
        "./agentPanel/state.js": "./mockPanelState.mjs",
    }
    source_text = source_path.read_text(encoding="utf-8")
    for original, replacement in replacements.items():
        source_text = source_text.replace(original, replacement)
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockApi.mjs").write_text(
        """
export async function fetchRunTokenUsage() {
    return {
        run_id: 'run-1',
        by_agent: [
            {
                instance_id: 'inst-1',
                role_id: 'writer',
                input_tokens: 999,
                latest_input_tokens: 321,
            },
        ],
    };
}

export async function fetchModelProfiles() {
    return {
        default: { is_default: true, context_window: 128000 },
        'writer-profile': { is_default: false, context_window: 64000 },
    };
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: null,
    currentSessionMode: 'normal',
    currentNormalRootRoleId: null,
    isGenerating: false,
    activeRunId: null,
    instanceRoleMap: {},
    normalModeRoles: [],
};

export function getPrimaryRoleId(sessionMode = state.currentSessionMode) {
    return sessionMode === 'normal' ? state.currentNormalRootRoleId : '';
}

export function setNormalModeRoles(roleOptions) {
    state.normalModeRoles = Array.isArray(roleOptions) ? roleOptions : [];
}

export function getRoleOption(roleId) {
    return state.normalModeRoles.find(role => role.role_id === roleId) || null;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockDom.mjs").write_text(
        "export const els = { promptInput: { disabled: false } };",
        encoding="utf-8",
    )
    (tmp_path / "mockI18n.mjs").write_text(
        """
export function t(key) {
    return key;
}

export function formatMessage(key, values) {
    if (key === 'context_indicator.latest_with_window') {
        return `Prompt tokens: ${values.input_tokens} / ${values.context_window}`;
    }
    return `Prompt tokens: ${values.input_tokens}`;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockTimeline.mjs").write_text(
        "export const currentRounds = [];",
        encoding="utf-8",
    )
    (tmp_path / "mockPanelState.mjs").write_text(
        """
export function getActiveInstanceId() {
    return '';
}

export function getPanel() {
    return null;
}

export function getPanels() {
    return new Map();
}
""".strip(),
        encoding="utf-8",
    )
    runner_path.write_text(
        """
globalThis.document = {
    _indicator: {
        style: { display: '' },
        dataset: {},
        textContent: '',
        title: '',
    },
    getElementById(id) {
        return id === 'main-context-indicator' ? this._indicator : null;
    },
};

"""
        + runner_source,
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(runner_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return cast(dict[str, object], json.loads(completed.stdout))
