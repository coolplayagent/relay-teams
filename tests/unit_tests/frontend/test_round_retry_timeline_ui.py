# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_retry_timeline_escapes_fallback_target_markup(tmp_path: Path) -> None:
    payload = _run_round_timeline_script(
        tmp_path=tmp_path,
        runner_source="""
const { renderRetryEventMarkup } = await import('./timeline.mjs');

const html = renderRetryEventMarkup(
    {
        kind: 'fallback',
        phase: 'scheduled',
        to_profile_id: '<img src=x onerror=alert(1)>',
    },
    Date.now(),
);

console.log(JSON.stringify({ html }));
""".strip(),
    )

    html = str(payload["html"])
    assert "<img src=x onerror=alert(1)>" not in html
    assert (
        '<span class="round-retry-copy">Switched to &lt;img src=x onerror=alert(1)&gt;</span>'
        in html
    )


def _run_round_timeline_script(tmp_path: Path, runner_source: str) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "rounds" / "timeline.js"
    )

    module_under_test_path = tmp_path / "timeline.mjs"
    runner_path = tmp_path / "runner-timeline.mjs"

    replacements = {
        "../../utils/dom.js": "./mockDom.mjs",
        "../../core/state.js": "./mockState.mjs",
        "../../core/api.js": "./mockApi.mjs",
        "../agentPanel.js": "./mockAgentPanel.mjs",
        "../messageRenderer.js": "./mockMessageRenderer.mjs",
        "../messageRenderer/helpers/block.js": "./mockMessageRendererBlock.mjs",
        "../messageRenderer/helpers/content.js": "./mockMessageRendererContent.mjs",
        "./navigator.js": "./mockNavigator.mjs",
        "./paging.js": "./mockPaging.mjs",
        "./state.js": "./mockRoundsState.mjs",
        "./utils.js": "./mockRoundUtils.mjs",
        "../../utils/logger.js": "./mockLogger.mjs",
        "../../utils/i18n.js": "./mockI18n.mjs",
    }
    source_text = source_path.read_text(encoding="utf-8")
    for original, replacement in replacements.items():
        source_text = source_text.replace(original, replacement)
    source_text = source_text.replace(
        "function renderRetryEventMarkup(event, nowMs) {",
        "export function renderRetryEventMarkup(event, nowMs) {",
        1,
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockDom.mjs").write_text(
        """
export const els = {
    chatMessages: {
        scrollHeight: 0,
        scrollTop: 0,
    },
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    activeSubagentSession: null,
    currentSessionId: 'session-1',
};

export function getRunPrimaryRoleId() {
    return null;
}

export function getRunPrimaryRoleLabel() {
    return 'Main Agent';
}

export function isRunPrimaryRoleId() {
    return false;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockApi.mjs").write_text(
        """
export async function fetchRunTokenUsage() {
    return {};
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockAgentPanel.mjs").write_text(
        """
export function setRoundPendingApprovals() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockMessageRenderer.mjs").write_text(
        """
export function clearAllStreamState() {
    return undefined;
}

export function getCoordinatorStreamOverlay() {
    return null;
}

export function renderHistoricalMessageList() {
    return undefined;
}

export function getOrCreateStreamBlock() {
    return undefined;
}

    export function appendStreamChunk() {
        return undefined;
    }
    """.strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockMessageRendererBlock.mjs").write_text(
        """
export function buildStructuredUserPromptSummary() {
    return '';
}

export function userPromptItemToStructuredPart() {
    return null;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockMessageRendererContent.mjs").write_text(
        """
export function appendStructuredContentPart() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockNavigator.mjs").write_text(
        """
export function renderRoundNavigator() {
    return undefined;
}

export function setActiveRoundNav() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockPaging.mjs").write_text(
        """
export function applyRoundPage() {
    return undefined;
}

export async function fetchInitialRoundsPage() {
    return { rounds: [] };
}

export async function fetchOlderRoundsPage() {
    return { rounds: [] };
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockRoundsState.mjs").write_text(
        """
export const roundsState = {
    currentRound: null,
    currentRounds: [],
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockRoundUtils.mjs").write_text(
        """
export function roundSectionId(runId) {
    return String(runId || '');
}

export function esc(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

export function roundStateLabel() {
    return '';
}

export function roundStateTone() {
    return 'idle';
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockLogger.mjs").write_text(
        """
export function errorToPayload() {
    return {};
}

export function logError() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockI18n.mjs").write_text(
        """
export function formatMessage(message, values = {}) {
    return Object.entries(values).reduce(
        (result, [key, value]) => result.replaceAll(`{${key}}`, String(value)),
        String(message || ''),
    );
}

export function t(key) {
    return String(key || '');
}
""".strip(),
        encoding="utf-8",
    )

    runner_path.write_text(runner_source, encoding="utf-8")

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
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    return json.loads(completed.stdout)
