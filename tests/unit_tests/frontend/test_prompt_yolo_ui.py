# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from pathlib import Path
import subprocess


def test_chat_input_renders_yolo_and_thinking_controls() -> None:
    html = Path("frontend/dist/index.html").read_text(encoding="utf-8")
    orchestration_css = Path(
        "frontend/dist/css/components/orchestration.css"
    ).read_text(encoding="utf-8")

    assert 'id="yolo-toggle"' in html
    assert 'id="thinking-mode-toggle"' in html
    assert 'id="thinking-effort-field"' in html
    assert re.search(r'id="thinking-effort-field"[\s\S]*?\bhidden\b', html)
    assert 'id="thinking-effort-select"' in html
    assert 'id="prompt-mention-menu"' in html
    assert ".composer-preset-field[hidden]," in orchestration_css
    assert ".composer-mode-inline[hidden]" in orchestration_css


def test_send_user_prompt_includes_yolo_and_thinking(tmp_path: Path) -> None:
    source = Path("frontend/dist/js/core/api/runs.js").read_text(encoding="utf-8")
    temp_dir = tmp_path / "api"
    temp_dir.mkdir()
    (temp_dir / "runs.js").write_text(source, encoding="utf-8")
    (temp_dir / "request.js").write_text(
        """
export async function requestJson(url, options, errorMessage) {
    globalThis.__captured = {
        url,
        errorMessage,
        method: options.method,
        body: JSON.parse(options.body),
    };
    return { run_id: "run-1", session_id: "session-1" };
}
""".strip(),
        encoding="utf-8",
    )
    runner = """
import { sendUserPrompt } from "./runs.js";

await sendUserPrompt("session-1", "ship it", true, { enabled: true, effort: "high" });
console.log(JSON.stringify(globalThis.__captured));
""".strip()
    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["url"] == "/api/runs"
    assert payload["method"] == "POST"
    assert payload["body"]["yolo"] is True
    assert payload["body"]["execution_mode"] == "ai"
    assert payload["body"]["thinking"] == {"enabled": True, "effort": "high"}


def test_prompt_controls_toggle_mode_specific_fields_and_thinking_effort(
    tmp_path: Path,
) -> None:
    source = Path("frontend/dist/js/app/prompt.js").read_text(encoding="utf-8")
    temp_dir = tmp_path / "prompt"
    temp_dir.mkdir()

    (temp_dir / "prompt.js").write_text(
        source.replace("../components/rounds.js", "./mockRounds.mjs")
        .replace("../components/contextIndicators.js", "./mockContextIndicators.mjs")
        .replace("../components/messageRenderer.js", "./mockMessageRenderer.mjs")
        .replace("../core/api.js", "./mockApi.mjs")
        .replace("./recovery.js", "./mockRecovery.mjs")
        .replace("../core/state.js", "./mockState.mjs")
        .replace("../core/stream.js", "./mockStream.mjs")
        .replace("../utils/dom.js", "./mockDom.mjs")
        .replace("../utils/feedback.js", "./mockFeedback.mjs")
        .replace("../utils/i18n.js", "./mockI18n.mjs")
        .replace("../utils/logger.js", "./mockLogger.mjs"),
        encoding="utf-8",
    )
    (temp_dir / "mockRounds.mjs").write_text(
        """
export function appendRoundUserMessage() {
    return undefined;
}

export function createLiveRound() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockContextIndicators.mjs").write_text(
        """
export function refreshVisibleContextIndicators() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockMessageRenderer.mjs").write_text(
        """
export function clearAllStreamState() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockApi.mjs").write_text(
        """
export async function fetchRoleConfigOptions() {
    return {
        coordinator_role_id: "Coordinator",
        main_agent_role_id: "MainAgent",
        normal_mode_roles: [
            { role_id: "MainAgent", name: "Main Agent", description: "" },
            { role_id: "writer", name: "Writer", description: "" },
        ],
    };
}

export async function fetchOrchestrationConfig() {
    return {
        default_orchestration_preset_id: "preset-1",
        presets: [
            {
                preset_id: "preset-1",
                name: "Default Preset",
                description: "",
                role_ids: ["writer"],
                orchestration_prompt: "",
            },
        ],
    };
}

export async function updateSessionTopology() {
    return {
        session_mode: "normal",
        normal_root_role_id: "MainAgent",
        orchestration_preset_id: null,
        can_switch_mode: true,
    };
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockRecovery.mjs").write_text(
        """
export async function hydrateSessionView() {
    return null;
}

export function startSessionContinuity() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: "session-1",
    currentSessionMode: "normal",
    currentSessionCanSwitchMode: true,
    currentNormalRootRoleId: "MainAgent",
    currentOrchestrationPresetId: "preset-1",
    mainAgentRoleId: "MainAgent",
    isGenerating: false,
    thinking: {
        enabled: false,
        effort: "medium",
    },
    yolo: true,
};

let normalModeRoles = [];

export function applyCurrentSessionRecord(record) {
    state.currentSessionMode = String(record?.session_mode || "normal");
    state.currentNormalRootRoleId = String(record?.normal_root_role_id || "");
    state.currentOrchestrationPresetId = String(record?.orchestration_preset_id || "");
    state.currentSessionCanSwitchMode = record?.can_switch_mode === true;
}

export function getCoordinatorRoleId() {
    return String(state.coordinatorRoleId || "");
}

export function getMainAgentRoleId() {
    return String(state.mainAgentRoleId || "");
}

export function getNormalModeRoles() {
    return normalModeRoles;
}

export function getRoleDisplayName(roleId, { fallback = "Agent" } = {}) {
    if (String(roleId || "") === String(state.mainAgentRoleId || "")) {
        return "Main Agent";
    }
    const match = normalModeRoles.find(role => role.role_id === roleId);
    return match?.name || fallback;
}

export function setCoordinatorRoleId(roleId) {
    state.coordinatorRoleId = String(roleId || "");
}

export function setMainAgentRoleId(roleId) {
    state.mainAgentRoleId = String(roleId || "");
}

export function setNormalModeRoles(roleOptions) {
    normalModeRoles = Array.isArray(roleOptions) ? roleOptions : [];
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockStream.mjs").write_text(
        """
export async function startIntentStream() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockDom.mjs").write_text(
        """
function createClassList() {
    return {
        values: new Map(),
        toggle(name, active) {
            this.values.set(name, active !== false);
        },
    };
}

function createElement(initial = {}) {
    return {
        hidden: false,
        disabled: false,
        value: "",
        innerHTML: "",
        textContent: "",
        title: "",
        checked: false,
        style: {
            display: "",
        },
        classList: createClassList(),
        _listeners: new Map(),
        addEventListener(type, listener) {
            this._listeners.set(type, listener);
        },
        dispatch(type) {
            const listener = this._listeners.get(type);
            if (listener) {
                listener({ target: this });
            }
        },
        ...initial,
    };
}

export const els = {
    yoloToggle: createElement({ checked: true }),
    thinkingModeToggle: createElement({ checked: false }),
    thinkingEffortField: createElement({ hidden: true }),
    thinkingEffortSelect: createElement({ value: "medium", disabled: true }),
    sessionModeLock: createElement(),
    sessionModeLabel: createElement(),
    sessionModeNormalBtn: createElement(),
    sessionModeOrchestrationBtn: createElement(),
    normalRoleField: createElement(),
    normalRoleSelect: createElement(),
    orchestrationPresetField: createElement({ hidden: true }),
    orchestrationPresetSelect: createElement(),
    promptInput: createElement(),
    sendBtn: createElement(),
    stopBtn: createElement(),
};
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockFeedback.mjs").write_text(
        """
export function showToast() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockI18n.mjs").write_text(
        """
export function t(key) {
    return key;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockLogger.mjs").write_text(
        """
export function sysLog() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )

    runner = """
globalThis.localStorage = {
    _values: new Map(),
    getItem(key) {
        return this._values.has(key) ? this._values.get(key) : null;
    },
    setItem(key, value) {
        this._values.set(key, String(value));
    },
};
globalThis.document = {
    addEventListener() {
        return undefined;
    },
};

const prompt = await import("./prompt.js");
const { state } = await import("./mockState.mjs");
const { els } = await import("./mockDom.mjs");

await prompt.initializeSessionTopologyControls();
prompt.initializeThinkingControls();

state.currentSessionMode = "normal";
prompt.refreshSessionTopologyControls();
    const normalModeSnapshot = {
        normalRoleHidden: els.normalRoleField.hidden,
        normalRoleDisplay: els.normalRoleField.style.display,
        orchestrationPresetHidden: els.orchestrationPresetField.hidden,
        orchestrationPresetDisplay: els.orchestrationPresetField.style.display,
    };

state.currentSessionMode = "orchestration";
prompt.refreshSessionTopologyControls();
    const orchestrationModeSnapshot = {
        normalRoleHidden: els.normalRoleField.hidden,
        normalRoleDisplay: els.normalRoleField.style.display,
        orchestrationPresetHidden: els.orchestrationPresetField.hidden,
        orchestrationPresetDisplay: els.orchestrationPresetField.style.display,
    };

    const initialThinkingSnapshot = {
        effortHidden: els.thinkingEffortField.hidden,
        effortDisplay: els.thinkingEffortField.style.display,
        effortDisabled: els.thinkingEffortSelect.disabled,
    };

els.thinkingModeToggle.checked = true;
els.thinkingModeToggle.dispatch("change");
    const enabledThinkingSnapshot = {
        effortHidden: els.thinkingEffortField.hidden,
        effortDisplay: els.thinkingEffortField.style.display,
        effortDisabled: els.thinkingEffortSelect.disabled,
    };

els.thinkingModeToggle.checked = false;
els.thinkingModeToggle.dispatch("change");
    const disabledThinkingSnapshot = {
        effortHidden: els.thinkingEffortField.hidden,
        effortDisplay: els.thinkingEffortField.style.display,
        effortDisabled: els.thinkingEffortSelect.disabled,
    };

console.log(JSON.stringify({
    normalModeSnapshot,
    orchestrationModeSnapshot,
    initialThinkingSnapshot,
    enabledThinkingSnapshot,
    disabledThinkingSnapshot,
}));
""".strip()
    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["normalModeSnapshot"] == {
        "normalRoleHidden": False,
        "normalRoleDisplay": "inline-flex",
        "orchestrationPresetHidden": True,
        "orchestrationPresetDisplay": "none",
    }
    assert payload["orchestrationModeSnapshot"] == {
        "normalRoleHidden": True,
        "normalRoleDisplay": "none",
        "orchestrationPresetHidden": False,
        "orchestrationPresetDisplay": "inline-flex",
    }
    assert payload["initialThinkingSnapshot"] == {
        "effortHidden": True,
        "effortDisplay": "none",
        "effortDisabled": True,
    }
    assert payload["enabledThinkingSnapshot"] == {
        "effortHidden": False,
        "effortDisplay": "inline-flex",
        "effortDisabled": False,
    }
    assert payload["disabledThinkingSnapshot"] == {
        "effortHidden": True,
        "effortDisplay": "none",
        "effortDisabled": True,
    }


def test_handle_send_strips_leading_role_mention_and_targets_run_role(
    tmp_path: Path,
) -> None:
    source = Path("frontend/dist/js/app/prompt.js").read_text(encoding="utf-8")
    temp_dir = tmp_path / "prompt_mentions"
    temp_dir.mkdir()

    (temp_dir / "prompt.js").write_text(
        source.replace("../components/rounds.js", "./mockRounds.mjs")
        .replace("../components/contextIndicators.js", "./mockContextIndicators.mjs")
        .replace("../components/messageRenderer.js", "./mockMessageRenderer.mjs")
        .replace("../core/api.js", "./mockApi.mjs")
        .replace("./recovery.js", "./mockRecovery.mjs")
        .replace("../core/state.js", "./mockState.mjs")
        .replace("../core/stream.js", "./mockStream.mjs")
        .replace("../utils/dom.js", "./mockDom.mjs")
        .replace("../utils/feedback.js", "./mockFeedback.mjs")
        .replace("../utils/i18n.js", "./mockI18n.mjs")
        .replace("../utils/logger.js", "./mockLogger.mjs"),
        encoding="utf-8",
    )
    (temp_dir / "mockRounds.mjs").write_text(
        """
export function appendRoundUserMessage(runId, text) {
    globalThis.__roundMessages.push({ runId, text });
}

export function createLiveRound(runId, text) {
    globalThis.__liveRounds.push({ runId, text });
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockContextIndicators.mjs").write_text(
        """
export function refreshVisibleContextIndicators() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockMessageRenderer.mjs").write_text(
        """
export function clearAllStreamState() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockApi.mjs").write_text(
        """
export async function fetchRoleConfigOptions() {
    return {
        coordinator_role_id: "Coordinator",
        main_agent_role_id: "MainAgent",
        normal_mode_roles: [
            { role_id: "writer", name: "Writer", description: "" },
            { role_id: "reviewer", name: "Reviewer", description: "" },
        ],
    };
}

export async function fetchOrchestrationConfig() {
    return {
        default_orchestration_preset_id: "",
        presets: [],
    };
}

export async function updateSessionTopology() {
    return {
        session_mode: "normal",
        normal_root_role_id: "MainAgent",
        orchestration_preset_id: null,
        can_switch_mode: true,
    };
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockRecovery.mjs").write_text(
        """
export async function hydrateSessionView() {
    return null;
}

export function startSessionContinuity() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: "session-1",
    currentSessionMode: "normal",
    currentSessionCanSwitchMode: true,
    currentNormalRootRoleId: "MainAgent",
    currentOrchestrationPresetId: null,
    pausedSubagent: null,
    isGenerating: false,
    yolo: true,
    thinking: { enabled: false, effort: "medium" },
    instanceRoleMap: {},
    roleInstanceMap: {},
    taskInstanceMap: {},
    activeAgentRoleId: null,
    activeAgentInstanceId: null,
    autoSwitchedSubagentInstances: {},
    activeRunId: null,
};

let coordinatorRoleId = "Coordinator";
let mainAgentRoleId = "MainAgent";
let normalModeRoles = [
    { role_id: "writer", name: "Writer", description: "" },
    { role_id: "reviewer", name: "Reviewer", description: "" },
];

export function applyCurrentSessionRecord() {
    return undefined;
}

export function getCoordinatorRoleId() {
    return coordinatorRoleId;
}

export function getMainAgentRoleId() {
    return mainAgentRoleId;
}

export function getNormalModeRoles() {
    return normalModeRoles;
}

export function getRoleDisplayName(roleId, { fallback = "Agent" } = {}) {
    if (roleId === coordinatorRoleId) return "Coordinator";
    if (roleId === mainAgentRoleId) return "Main Agent";
    const match = normalModeRoles.find(role => role.role_id === roleId);
    return match?.name || fallback;
}

export function setCoordinatorRoleId(roleId) {
    coordinatorRoleId = String(roleId || "");
}

export function setMainAgentRoleId(roleId) {
    mainAgentRoleId = String(roleId || "");
}

export function setNormalModeRoles(roleOptions) {
    normalModeRoles = Array.isArray(roleOptions) ? roleOptions : [];
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockStream.mjs").write_text(
        """
export async function startIntentStream(text, sessionId, onCompleted, options = {}) {
    globalThis.__streamCalls.push({ text, sessionId, options });
    if (typeof options.onRunCreated === "function") {
        options.onRunCreated({ run_id: "run-1", target_role_id: options.targetRoleId || null });
    }
    return onCompleted;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockDom.mjs").write_text(
        """
function createElement(initial = {}) {
    return {
        value: "",
        checked: false,
        disabled: false,
        hidden: false,
        textContent: "",
        innerHTML: "",
        title: "",
        style: { display: "", height: "" },
        classList: { toggle() { return undefined; } },
        addEventListener() { return undefined; },
        focus() { return undefined; },
        ...initial,
    };
}

export const els = {
    promptInput: createElement({ value: "@Writer ship it" }),
    sendBtn: createElement(),
    stopBtn: createElement({ style: { display: "none" } }),
    yoloToggle: createElement({ checked: true }),
    thinkingModeToggle: createElement({ checked: false }),
    thinkingEffortSelect: createElement({ value: "medium", disabled: true }),
};
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockFeedback.mjs").write_text(
        """
export function showToast() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockI18n.mjs").write_text(
        """
export function t(key) {
    return key;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockLogger.mjs").write_text(
        """
export function sysLog(message, tone = "log-info") {
    globalThis.__logs.push({ message, tone });
}
""".strip(),
        encoding="utf-8",
    )

    runner = """
import { handleSend } from "./prompt.js";

globalThis.__streamCalls = [];
globalThis.__logs = [];
globalThis.__liveRounds = [];
globalThis.__roundMessages = [];

await handleSend();

console.log(JSON.stringify({
    streamCall: globalThis.__streamCalls[0],
    liveRounds: globalThis.__liveRounds,
    roundMessages: globalThis.__roundMessages,
}));
""".strip()
    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["streamCall"]["text"] == "ship it"
    assert payload["streamCall"]["sessionId"] == "session-1"
    assert payload["streamCall"]["options"]["targetRoleId"] == "writer"
    assert payload["liveRounds"] == [{"runId": "run-1", "text": "ship it"}]
    assert payload["roundMessages"] == [{"runId": "run-1", "text": "ship it"}]


def test_prompt_role_mentions_offer_autocomplete_and_insert_selection(
    tmp_path: Path,
) -> None:
    source = Path("frontend/dist/js/app/prompt.js").read_text(encoding="utf-8")
    temp_dir = tmp_path / "prompt_autocomplete"
    temp_dir.mkdir()

    (temp_dir / "prompt.js").write_text(
        source.replace("../components/rounds.js", "./mockRounds.mjs")
        .replace("../components/contextIndicators.js", "./mockContextIndicators.mjs")
        .replace("../components/messageRenderer.js", "./mockMessageRenderer.mjs")
        .replace("../core/api.js", "./mockApi.mjs")
        .replace("./recovery.js", "./mockRecovery.mjs")
        .replace("../core/state.js", "./mockState.mjs")
        .replace("../core/stream.js", "./mockStream.mjs")
        .replace("../utils/dom.js", "./mockDom.mjs")
        .replace("../utils/feedback.js", "./mockFeedback.mjs")
        .replace("../utils/i18n.js", "./mockI18n.mjs")
        .replace("../utils/logger.js", "./mockLogger.mjs"),
        encoding="utf-8",
    )
    (temp_dir / "mockRounds.mjs").write_text(
        """
export function appendRoundUserMessage() {
    return undefined;
}

export function createLiveRound() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockContextIndicators.mjs").write_text(
        """
export function refreshVisibleContextIndicators() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockMessageRenderer.mjs").write_text(
        """
export function clearAllStreamState() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockApi.mjs").write_text(
        """
export async function fetchRoleConfigOptions() {
    return {
        coordinator_role_id: "Coordinator",
        main_agent_role_id: "MainAgent",
        normal_mode_roles: [
            { role_id: "writer", name: "Writer", description: "" },
            { role_id: "reviewer", name: "Reviewer", description: "" },
        ],
    };
}

export async function fetchOrchestrationConfig() {
    return {
        default_orchestration_preset_id: "",
        presets: [],
    };
}

export async function updateSessionTopology() {
    return {
        session_mode: "normal",
        normal_root_role_id: "MainAgent",
        orchestration_preset_id: null,
        can_switch_mode: true,
    };
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockRecovery.mjs").write_text(
        """
export async function hydrateSessionView() {
    return null;
}

export function startSessionContinuity() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockState.mjs").write_text(
        """
export const state = {
    currentSessionId: "session-1",
    currentSessionMode: "normal",
    currentSessionCanSwitchMode: true,
    currentNormalRootRoleId: "MainAgent",
    currentOrchestrationPresetId: null,
    pausedSubagent: null,
    isGenerating: false,
    yolo: true,
    thinking: { enabled: false, effort: "medium" },
    instanceRoleMap: {},
    roleInstanceMap: {},
    taskInstanceMap: {},
    activeAgentRoleId: null,
    activeAgentInstanceId: null,
    autoSwitchedSubagentInstances: {},
    activeRunId: null,
};

let coordinatorRoleId = "Coordinator";
let mainAgentRoleId = "MainAgent";
let normalModeRoles = [
    { role_id: "writer", name: "Writer", description: "" },
    { role_id: "reviewer", name: "Reviewer", description: "" },
];

export function applyCurrentSessionRecord() {
    return undefined;
}

export function getCoordinatorRoleId() {
    return coordinatorRoleId;
}

export function getMainAgentRoleId() {
    return mainAgentRoleId;
}

export function getNormalModeRoles() {
    return normalModeRoles;
}

export function getRoleDisplayName(roleId, { fallback = "Agent" } = {}) {
    if (roleId === coordinatorRoleId) return "Coordinator";
    if (roleId === mainAgentRoleId) return "Main Agent";
    const match = normalModeRoles.find(role => role.role_id === roleId);
    return match?.name || fallback;
}

export function setCoordinatorRoleId(roleId) {
    coordinatorRoleId = String(roleId || "");
}

export function setMainAgentRoleId(roleId) {
    mainAgentRoleId = String(roleId || "");
}

export function setNormalModeRoles(roleOptions) {
    normalModeRoles = Array.isArray(roleOptions) ? roleOptions : [];
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockStream.mjs").write_text(
        """
export async function startIntentStream() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockDom.mjs").write_text(
        """
function createElement(initial = {}) {
    return {
        value: "",
        checked: false,
        disabled: false,
        hidden: true,
        textContent: "",
        innerHTML: "",
        title: "",
        selectionStart: 0,
        selectionEnd: 0,
        scrollHeight: 36,
        style: { display: "", height: "" },
        dataset: {},
        classList: { toggle() { return undefined; } },
        _listeners: new Map(),
        addEventListener(type, listener) {
            this._listeners.set(type, listener);
        },
        focus() { return undefined; },
        contains(target) {
            return target === this;
        },
        ...initial,
    };
}

export const els = {
    promptInput: createElement({
        value: "@Ma",
        selectionStart: 3,
        selectionEnd: 3,
        hidden: false,
    }),
    promptMentionMenu: createElement({ hidden: true }),
    sendBtn: createElement({ hidden: false }),
    stopBtn: createElement({ style: { display: "none" }, hidden: false }),
    yoloToggle: createElement({ checked: true, hidden: false }),
    thinkingModeToggle: createElement({ checked: false, hidden: false }),
    thinkingEffortSelect: createElement({ value: "medium", disabled: true, hidden: false }),
};
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockFeedback.mjs").write_text(
        """
export function showToast() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockI18n.mjs").write_text(
        """
export function t(key) {
    return key;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockLogger.mjs").write_text(
        """
export function sysLog() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )

    runner = """
import {
    handlePromptComposerInput,
    handlePromptComposerKeydown,
} from "./prompt.js";
import { els } from "./mockDom.mjs";

handlePromptComposerInput();
const beforeSelect = {
    menuHidden: els.promptMentionMenu.hidden,
    menuHtml: els.promptMentionMenu.innerHTML,
};

const enterHandled = handlePromptComposerKeydown({
    key: "Enter",
    preventDefault() { return undefined; },
    stopImmediatePropagation() { return undefined; },
    stopPropagation() { return undefined; },
});

console.log(JSON.stringify({
    beforeSelect,
    enterHandled,
    value: els.promptInput.value,
    selectionStart: els.promptInput.selectionStart,
    selectionEnd: els.promptInput.selectionEnd,
}));
""".strip()
    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["beforeSelect"]["menuHidden"] is False
    assert "Main Agent" in payload["beforeSelect"]["menuHtml"]
    assert "MainAgent" in payload["beforeSelect"]["menuHtml"]
    assert payload["enterHandled"] is True
    assert payload["value"] == "@Main Agent "
    assert payload["selectionStart"] == 12
    assert payload["selectionEnd"] == 12
