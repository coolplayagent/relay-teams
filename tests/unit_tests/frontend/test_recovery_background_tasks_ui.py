# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess

from .css_helpers import load_components_css


def test_recovery_continuity_polling_excludes_terminal_recoverable_runs() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source = (repo_root / "frontend" / "dist" / "js" / "app" / "recovery.js").read_text(
        encoding="utf-8"
    )
    block = source.split("function shouldPollContinuity()", 1)[1].split(
        "\n}\n",
        1,
    )[0]

    assert "isContinuityPollableRun(activeRun)" in block
    assert "activeRun?.is_recoverable" not in block
    assert "if (isTerminalRecoveryRun(activeRun)) return false;" in source


def test_recovery_ui_tracks_background_tasks_in_banner_and_events() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    recovery_script = (
        repo_root / "frontend" / "dist" / "js" / "app" / "recovery.js"
    ).read_text(encoding="utf-8")
    components_css = load_components_css()
    event_router_script = (
        repo_root / "frontend" / "dist" / "js" / "core" / "eventRouter" / "index.js"
    ).read_text(encoding="utf-8")
    i18n_script = (
        repo_root / "frontend" / "dist" / "js" / "utils" / "i18n.js"
    ).read_text(encoding="utf-8")

    assert "stopBackgroundTask" in recovery_script
    assert "backgroundTasks" in recovery_script
    assert "renderBackgroundTaskPanel" in recovery_script
    assert "ensureBackgroundTaskHost" in recovery_script
    assert "renderBackgroundTaskList" in recovery_script
    assert "handleBackgroundTaskAction" in recovery_script
    assert (
        "await refreshSubagentRail(safeSessionId, { preserveSelection: true, priority, signal });"
        in recovery_script
    )
    assert (
        "await refreshSubagentRail(safeSessionId, { preserveSelection: true, priority, signal });\n"
        "    throwIfAborted(signal);\n"
        "    syncSessionContinuity();" in recovery_script
    )
    assert "export function applyBackgroundTaskEvent" in recovery_script
    assert "normalizeBackgroundTaskEventStatus(payload, eventType)" in recovery_script
    assert (
        "const chatContainer = els.chatContainer || els.chatMessages?.parentElement;"
        in recovery_script
    )
    assert (
        "if (!chatContainer || !inputContainer || inputContainer.parentNode !== chatContainer) return null;"
        in recovery_script
    )
    assert "chatContainer.insertBefore(host, inputContainer);" in recovery_script
    assert "host.className = 'background-task-strip-host';" in recovery_script
    assert (
        '<div class="background-task-strip" role="status" aria-live="polite">'
        in recovery_script
    )
    assert '<div class="background-task-chip-list">' in recovery_script
    assert (
        'class="background-task-chip background-task-chip-${chipTone}"'
        in recovery_script
    )
    assert (
        "isDisplayableBackgroundTask(task) && isBackgroundTaskActive(task)"
        in recovery_script
    )
    assert (
        "const hidePanel = !runId || activeBackgroundTasks.length === 0;"
        in recovery_script
    )
    assert "filter(Boolean)" in recovery_script
    assert "const nextBackgroundTasks = found" in recovery_script
    assert ".background-task-strip-host" in components_css
    assert ".background-task-strip" in components_css
    assert ".background-task-chip" in components_css
    assert ".background-task-chip-stop" in components_css
    assert "recovery.background_task.panel_label" in i18n_script
    assert "background_task_started" in event_router_script
    assert "background_task_completed" in event_router_script
    assert "rememberNormalModeSubagentFromBackgroundTask" in event_router_script
    assert (
        "applyBackgroundTaskEvent(payload, eventMeta, evType);" in event_router_script
    )
    assert "const BACKGROUND_TASK_UPDATE_REFRESH_DELAY_MS = 650;" in event_router_script
    assert "const BACKGROUND_TASK_STATUS_REFRESH_DELAY_MS = 350;" in event_router_script
    assert "delayMs: BACKGROUND_TASK_UPDATE_REFRESH_DELAY_MS," in event_router_script
    assert "delayMs: BACKGROUND_TASK_STATUS_REFRESH_DELAY_MS," in event_router_script
    assert "recovery.background_task.stop" in i18n_script


def test_background_task_event_renders_control_strip_immediately(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source = (repo_root / "frontend" / "dist" / "js" / "app" / "recovery.js").read_text(
        encoding="utf-8"
    )
    temp_dir = tmp_path / "recovery_background_task_event"
    temp_dir.mkdir()
    (temp_dir / "recovery.mjs").write_text(
        source.replace("../components/subagentRail.js", "./mockSubagentRail.mjs")
        .replace("../components/contextIndicators.js", "./mockContextIndicators.mjs")
        .replace("../components/messageRenderer.js", "./mockMessageRenderer.mjs")
        .replace("../components/rounds/timeline.js", "./mockTimeline.mjs")
        .replace("../components/sidebar.js", "./mockSidebar.mjs")
        .replace("../core/api.js", "./mockApi.mjs")
        .replace("../core/state.js", "./mockState.mjs")
        .replace("../core/stream.js", "./mockStream.mjs")
        .replace("../utils/dom.js", "./mockDom.mjs")
        .replace("../utils/i18n.js", "./mockI18n.mjs")
        .replace("../utils/logger.js", "./mockLogger.mjs"),
        encoding="utf-8",
    )
    (temp_dir / "mockSubagentRail.mjs").write_text(
        "export async function refreshSubagentRail() { return undefined; }",
        encoding="utf-8",
    )
    (temp_dir / "mockContextIndicators.mjs").write_text(
        "export function refreshVisibleContextIndicators() { return undefined; }",
        encoding="utf-8",
    )
    (temp_dir / "mockMessageRenderer.mjs").write_text(
        """
export function clearRunStreamState() { return undefined; }
export function reconcileTerminalRunStreamState() { return undefined; }
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockTimeline.mjs").write_text(
        """
export async function loadSessionRounds() { return undefined; }
export function overlayRoundRecoveryState() { return undefined; }
export function syncRoundTodoVisibility() { return undefined; }
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockSidebar.mjs").write_text(
        "export function scheduleSessionsRefresh() { return undefined; }",
        encoding="utf-8",
    )
    (temp_dir / "mockApi.mjs").write_text(
        """
export async function answerUserQuestion() { return {}; }
export async function fetchSessionRecovery() { return {}; }
export async function invalidateSessionRecovery() { return {}; }
export async function resolveToolApproval() { return {}; }
export async function resumeRun() { return {}; }
export async function stopBackgroundTask() { return {}; }
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: "session-1",
    currentRecoverySnapshot: null,
    pausedSubagent: null,
    isGenerating: true,
    activeRunId: "run-1",
};
export function clearRunPrimaryRole() { return undefined; }
export function humanizeRoleId(roleId) { return String(roleId || ""); }
export function isPrimaryRoleId() { return false; }
export function isReservedSystemRoleId() { return false; }
export function setRunPrimaryRole() { return undefined; }
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockStream.mjs").write_text(
        """
export function endStream() { return undefined; }
export function resumeRunStream() { return undefined; }
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockDom.mjs").write_text(
        """
function createElement() {
    return {
        style: { display: "none" },
        innerHTML: "",
        textContent: "",
        hidden: false,
        querySelectorAll() { return []; },
        querySelector() { return null; },
    };
}
export const els = {
    backgroundTaskHost: createElement(),
    recoveryQuestionHost: createElement(),
    recoveryApprovalHost: createElement(),
    resumeRunBtn: createElement(),
};
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockI18n.mjs").write_text(
        """
const labels = {
    "recovery.background_task.panel_label": "BACKGROUND TASKS",
    "recovery.background_task.stop": "Stop Session",
    "recovery.state.running": "Running",
};
export function t(key) { return labels[key] || key; }
export function formatMessage(key) { return t(key); }
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockLogger.mjs").write_text(
        "export function sysLog() { return undefined; }",
        encoding="utf-8",
    )
    runner = """
import { applyBackgroundTaskEvent } from "./recovery.mjs";
import { state } from "./mockState.mjs";
import { els } from "./mockDom.mjs";

const applied = applyBackgroundTaskEvent({
    background_task_id: "bg-1",
    run_id: "run-1",
    session_id: "session-1",
    kind: "subagent",
    command: "subagent:Explorer",
    status: "running",
}, { run_id: "run-1", session_id: "session-1" }, "background_task_started");
const startedDisplay = els.backgroundTaskHost.style.display;
const startedHtml = els.backgroundTaskHost.innerHTML;
const failedApplied = applyBackgroundTaskEvent({
    background_task_id: "bg-1",
    run_id: "run-1",
    session_id: "session-1",
    kind: "subagent",
    command: "subagent:Explorer",
    status: "failed",
}, { run_id: "run-1", session_id: "session-1" }, "background_task_completed");

console.log(JSON.stringify({
    applied,
    failedApplied,
    taskCount: state.currentRecoverySnapshot.backgroundTasks.length,
    statuses: state.currentRecoverySnapshot.backgroundTasks.map(task => task.status),
    startedDisplay,
    startedHtml,
}));
""".strip()
    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["applied"] is True
    assert payload["failedApplied"] is True
    assert payload["taskCount"] == 1
    assert payload["statuses"] == ["failed"]
    assert payload["startedDisplay"] == "block"
    assert "BACKGROUND TASKS" in payload["startedHtml"]
    assert "Stop Session" in payload["startedHtml"]


def test_foreground_command_task_event_does_not_open_background_strip(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source = (repo_root / "frontend" / "dist" / "js" / "app" / "recovery.js").read_text(
        encoding="utf-8"
    )
    temp_dir = tmp_path / "recovery_foreground_command_event"
    temp_dir.mkdir()
    (temp_dir / "recovery.mjs").write_text(
        source.replace("../components/subagentRail.js", "./mockSubagentRail.mjs")
        .replace("../components/contextIndicators.js", "./mockContextIndicators.mjs")
        .replace("../components/messageRenderer.js", "./mockMessageRenderer.mjs")
        .replace("../components/rounds/timeline.js", "./mockTimeline.mjs")
        .replace("../components/sidebar.js", "./mockSidebar.mjs")
        .replace("../core/api.js", "./mockApi.mjs")
        .replace("../core/state.js", "./mockState.mjs")
        .replace("../core/stream.js", "./mockStream.mjs")
        .replace("../utils/dom.js", "./mockDom.mjs")
        .replace("../utils/i18n.js", "./mockI18n.mjs")
        .replace("../utils/logger.js", "./mockLogger.mjs"),
        encoding="utf-8",
    )
    (temp_dir / "mockSubagentRail.mjs").write_text(
        "export async function refreshSubagentRail() { return undefined; }",
        encoding="utf-8",
    )
    (temp_dir / "mockContextIndicators.mjs").write_text(
        "export function refreshVisibleContextIndicators() { return undefined; }",
        encoding="utf-8",
    )
    (temp_dir / "mockMessageRenderer.mjs").write_text(
        """
export function clearRunStreamState() { return undefined; }
export function reconcileTerminalRunStreamState() { return undefined; }
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockTimeline.mjs").write_text(
        """
export async function loadSessionRounds() { return undefined; }
export function overlayRoundRecoveryState() { return undefined; }
export function syncRoundTodoVisibility() { return undefined; }
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockSidebar.mjs").write_text(
        "export function scheduleSessionsRefresh() { return undefined; }",
        encoding="utf-8",
    )
    (temp_dir / "mockApi.mjs").write_text(
        """
export async function answerUserQuestion() { return {}; }
export async function fetchSessionRecovery() { return {}; }
export async function invalidateSessionRecovery() { return {}; }
export async function resolveToolApproval() { return {}; }
export async function resumeRun() { return {}; }
export async function stopBackgroundTask() { return {}; }
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: "session-1",
    currentRecoverySnapshot: null,
    pausedSubagent: null,
    isGenerating: true,
    activeRunId: "run-1",
};
export function clearRunPrimaryRole() { return undefined; }
export function humanizeRoleId(roleId) { return String(roleId || ""); }
export function isPrimaryRoleId() { return false; }
export function isReservedSystemRoleId() { return false; }
export function setRunPrimaryRole() { return undefined; }
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockStream.mjs").write_text(
        """
export function endStream() { return undefined; }
export function resumeRunStream() { return undefined; }
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockDom.mjs").write_text(
        """
function createElement() {
    return {
        style: { display: "none" },
        innerHTML: "",
        textContent: "",
        hidden: false,
        querySelectorAll() { return []; },
        querySelector() { return null; },
    };
}
export const els = {
    backgroundTaskHost: createElement(),
    recoveryQuestionHost: createElement(),
    recoveryApprovalHost: createElement(),
    resumeRunBtn: createElement(),
};
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockI18n.mjs").write_text(
        """
export function t(key) { return key; }
export function formatMessage(key) { return key; }
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockLogger.mjs").write_text(
        "export function sysLog() { return undefined; }",
        encoding="utf-8",
    )
    runner = """
import { applyBackgroundTaskEvent } from "./recovery.mjs";
import { state } from "./mockState.mjs";
import { els } from "./mockDom.mjs";

const applied = applyBackgroundTaskEvent({
    background_task_id: "fg-1",
    run_id: "run-1",
    session_id: "session-1",
    kind: "command",
    execution_mode: "foreground",
    command: "python script.py",
    status: "running",
}, { run_id: "run-1", session_id: "session-1" }, "background_task_started");

console.log(JSON.stringify({
    applied,
    snapshot: state.currentRecoverySnapshot,
    display: els.backgroundTaskHost.style.display,
    html: els.backgroundTaskHost.innerHTML,
}));
""".strip()
    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["applied"] is False
    assert payload["snapshot"] is None
    assert payload["display"] == "none"
    assert payload["html"] == ""


def test_terminal_run_state_patches_round_without_recovery_snapshot(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source = (repo_root / "frontend" / "dist" / "js" / "app" / "recovery.js").read_text(
        encoding="utf-8"
    )
    temp_dir = tmp_path / "recovery_terminal_state"
    temp_dir.mkdir()
    (temp_dir / "recovery.mjs").write_text(
        source.replace("../components/subagentRail.js", "./mockSubagentRail.mjs")
        .replace("../components/contextIndicators.js", "./mockContextIndicators.mjs")
        .replace("../components/messageRenderer.js", "./mockMessageRenderer.mjs")
        .replace("../components/rounds/timeline.js", "./mockTimeline.mjs")
        .replace("../components/sidebar.js", "./mockSidebar.mjs")
        .replace("../core/api.js", "./mockApi.mjs")
        .replace("../core/state.js", "./mockState.mjs")
        .replace("../core/stream.js", "./mockStream.mjs")
        .replace("../utils/dom.js", "./mockDom.mjs")
        .replace("../utils/i18n.js", "./mockI18n.mjs")
        .replace("../utils/logger.js", "./mockLogger.mjs"),
        encoding="utf-8",
    )
    (temp_dir / "mockSubagentRail.mjs").write_text(
        "export async function refreshSubagentRail() { return undefined; }",
        encoding="utf-8",
    )
    (temp_dir / "mockContextIndicators.mjs").write_text(
        "export function refreshVisibleContextIndicators() { return undefined; }",
        encoding="utf-8",
    )
    (temp_dir / "mockMessageRenderer.mjs").write_text(
        """
export function clearRunStreamState() { return undefined; }
export function reconcileTerminalRunStreamState() { return undefined; }
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockTimeline.mjs").write_text(
        """
globalThis.__roundOverlays = [];
export async function loadSessionRounds() { return undefined; }
export function overlayRoundRecoveryState(runId, overlay) {
    globalThis.__roundOverlays.push({ runId, overlay });
}
export function syncRoundTodoVisibility() { return undefined; }
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockSidebar.mjs").write_text(
        """
globalThis.__sessionRefreshes = 0;
export function scheduleSessionsRefresh() {
    globalThis.__sessionRefreshes += 1;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockApi.mjs").write_text(
        """
export async function answerUserQuestion() { return {}; }
export async function fetchSessionRecovery() { return {}; }
export async function invalidateSessionRecovery() { return {}; }
export async function resolveToolApproval() { return {}; }
export async function resumeRun() { return {}; }
export async function stopBackgroundTask() { return {}; }
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: "session-1",
    currentRecoverySnapshot: null,
    pausedSubagent: null,
    isGenerating: true,
    activeRunId: "run-1",
};
export function clearRunPrimaryRole() { return undefined; }
export function humanizeRoleId(roleId) { return String(roleId || ""); }
export function isPrimaryRoleId() { return false; }
export function isReservedSystemRoleId() { return false; }
export function setRunPrimaryRole() { return undefined; }
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockStream.mjs").write_text(
        """
export function endStream() { return undefined; }
export function resumeRunStream() { return undefined; }
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockDom.mjs").write_text(
        """
function createElement() {
    return {
        style: { display: "none" },
        innerHTML: "",
        textContent: "",
        hidden: false,
        querySelectorAll() { return []; },
        querySelector() { return null; },
    };
}
export const els = {
    backgroundTaskHost: createElement(),
    recoveryQuestionHost: createElement(),
    recoveryApprovalHost: createElement(),
    resumeRunBtn: createElement(),
};
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockI18n.mjs").write_text(
        """
export function t(key) { return key; }
export function formatMessage(key) { return key; }
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockLogger.mjs").write_text(
        "export function sysLog() { return undefined; }",
        encoding="utf-8",
    )
    runner = """
import { markRunTerminalState } from "./recovery.mjs";
import { state } from "./mockState.mjs";

markRunTerminalState("run-1", {
    status: "completed",
    phase: "terminal",
    recoverable: false,
});

console.log(JSON.stringify({
    overlays: globalThis.__roundOverlays,
    refreshes: globalThis.__sessionRefreshes,
    snapshot: state.currentRecoverySnapshot,
}));
""".strip()
    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload == {
        "overlays": [
            {
                "runId": "run-1",
                "overlay": {
                    "run_status": "completed",
                    "run_phase": "terminal",
                    "is_recoverable": False,
                    "pending_tool_approval_count": 0,
                    "pending_tool_approvals": [],
                },
            }
        ],
        "refreshes": 1,
        "snapshot": None,
    }
