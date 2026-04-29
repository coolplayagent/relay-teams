# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_restore_main_session_view_dispatches_events_after_hydration(
    tmp_path: Path,
) -> None:
    payload = _run_session_view_script(
        tmp_path=tmp_path,
        runner_source="""
const { els } = await import("./mockDom.mjs");
const { restoreMainSessionView } = await import("./sessionView.mjs");

const restoring = restoreMainSessionView("session-1", { quiet: true });
await Promise.resolve();

const immediate = {
    html: els.chatMessages.innerHTML,
    eventTypes: globalThis.__events.map(event => event.type),
    hydrateCalls: globalThis.__hydrateCalls,
};

globalThis.__hydrateResolvers[0].resolve({ ok: true });
const snapshot = await restoring;

console.log(JSON.stringify({
    immediate,
    snapshot,
    finalEventTypes: globalThis.__events.map(event => event.type),
}));
""".strip(),
    )

    immediate = payload["immediate"]
    assert isinstance(immediate, dict)
    assert "subagent-main-session-loading" in str(immediate["html"])
    assert immediate["eventTypes"] == [
        "agent-teams-subagent-session-cleared",
    ]
    assert immediate["hydrateCalls"] == [
        {
            "sessionId": "session-1",
            "includeRounds": True,
            "quiet": True,
        }
    ]
    assert payload["snapshot"] == {"ok": True}
    assert payload["finalEventTypes"] == [
        "agent-teams-subagent-session-cleared",
        "agent-teams-session-activated",
        "agent-teams-session-selected",
    ]


def test_hydrate_main_session_for_switch_uses_switch_hydration_boundary(
    tmp_path: Path,
) -> None:
    payload = _run_session_view_script(
        tmp_path=tmp_path,
        runner_source="""
const { hydrateMainSessionForSwitch } = await import("./sessionView.mjs");

await hydrateMainSessionForSwitch("session-2", {
    priority: "high",
    quiet: true,
    roundsScrollPolicy: "preserve-anchor",
});

console.log(JSON.stringify({
    switchCalls: globalThis.__switchHydrateCalls,
    hydrateCalls: globalThis.__hydrateCalls,
}));
""".strip(),
    )

    assert payload == {
        "switchCalls": [
            {
                "sessionId": "session-2",
                "priority": "high",
                "quiet": True,
                "roundsScrollPolicy": "preserve-anchor",
            }
        ],
        "hydrateCalls": [],
    }


def _run_session_view_script(tmp_path: Path, runner_source: str) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "app" / "sessionView.js"
    module_under_test_path = tmp_path / "sessionView.mjs"
    runner_path = tmp_path / "runner.mjs"

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("./recovery.js", "./mockRecovery.mjs")
        .replace("../core/state.js", "./mockState.mjs")
        .replace("../utils/dom.js", "./mockDom.mjs")
        .replace("../utils/i18n.js", "./mockI18n.mjs")
        .replace("../utils/logger.js", "./mockLogger.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    (tmp_path / "mockRecovery.mjs").write_text(
        """
export async function hydrateSessionSwitchView(sessionId, options = {}) {
    globalThis.__switchHydrateCalls.push({
        sessionId,
        priority: options.priority || "",
        quiet: options.quiet === true,
        roundsScrollPolicy: options.roundsScrollPolicy || "",
    });
    return null;
}

export async function hydrateSessionView(sessionId, options = {}) {
    globalThis.__hydrateCalls.push({
        sessionId,
        includeRounds: options.includeRounds === true,
        quiet: options.quiet === true,
    });
    return await new Promise(resolve => {
        globalThis.__hydrateResolvers.push({ resolve });
    });
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: "session-1",
    activeSubagentSession: null,
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockDom.mjs").write_text(
        """
export const els = {
    chatMessages: { innerHTML: "" },
};

globalThis.CustomEvent = class CustomEvent {
    constructor(type, options = {}) {
        this.type = type;
        this.detail = options.detail || {};
    }
};

globalThis.document = {
    dispatchEvent(event) {
        globalThis.__events.push({ type: event.type, detail: event.detail });
        return true;
    },
};
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockI18n.mjs").write_text(
        """
export function t(key) {
    return key;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "mockLogger.mjs").write_text(
        """
export function sysLog(message) {
    globalThis.__logs.push(String(message));
}
""".strip(),
        encoding="utf-8",
    )
    runner_path.write_text(
        f"""
globalThis.__events = [];
globalThis.__hydrateCalls = [];
globalThis.__hydrateResolvers = [];
globalThis.__switchHydrateCalls = [];
globalThis.__logs = [];

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
        timeout=3,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    return json.loads(completed.stdout)
