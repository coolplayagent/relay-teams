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
        encoding="utf-8",
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

export function getPrimaryRoleId() {
    return String(state.currentNormalRootRoleId || state.mainAgentRoleId || "");
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

export function getRoleInputModalitySupport(roleId, modality) {
    const match = normalModeRoles.find(role => role.role_id === roleId);
    const value = match?.capabilities?.input?.[modality];
    return value === true ? true : (value === false ? false : null);
}

export function getRoleModelName(roleId, { fallback = "" } = {}) {
    const match = normalModeRoles.find(role => role.role_id === roleId);
    return String(match?.model_name || fallback || "");
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

export function formatMessage(key, values = {}) {
    return Object.entries(values).reduce(
        (message, [name, value]) => message.replaceAll(`{${name}}`, String(value)),
        t(key),
    );
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
        encoding="utf-8",
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
export function appendRoundUserMessage(runId, prompt) {
    globalThis.__roundMessages.push({ runId, prompt });
}

export function createLiveRound(runId, text, intentParts) {
    globalThis.__liveRounds.push({ runId, text, intentParts });
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
            { role_id: "writer", name: "Writer", description: "Draft final responses" },
            { role_id: "reviewer", name: "Reviewer", description: "Check correctness and risk" },
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
    { role_id: "writer", name: "Writer", description: "Draft final responses" },
    { role_id: "reviewer", name: "Reviewer", description: "Check correctness and risk" },
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

export function getPrimaryRoleId() {
    return String(state.currentNormalRootRoleId || mainAgentRoleId || "");
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

export function getRoleInputModalitySupport(roleId, modality) {
    const match = normalModeRoles.find(role => role.role_id === roleId);
    const value = match?.capabilities?.input?.[modality];
    return value === true ? true : (value === false ? false : null);
}

export function getRoleModelName(roleId, { fallback = "" } = {}) {
    const match = normalModeRoles.find(role => role.role_id === roleId);
    return String(match?.model_name || fallback || "");
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

export function formatMessage(key, values = {}) {
    return Object.entries(values).reduce(
        (message, [name, value]) => message.replaceAll(`{${name}}`, String(value)),
        t(key),
    );
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
import { state } from "./mockState.mjs";
import { els } from "./mockDom.mjs";

globalThis.__streamCalls = [];
globalThis.__logs = [];
globalThis.__liveRounds = [];
globalThis.__roundMessages = [];

await handleSend();
els.promptInput.value = "＠Writer ship it";
els.promptInput.disabled = false;
state.isGenerating = false;
await handleSend();

console.log(JSON.stringify({
    streamCalls: globalThis.__streamCalls,
    liveRounds: globalThis.__liveRounds,
    roundMessages: globalThis.__roundMessages,
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
    assert [call["text"] for call in payload["streamCalls"]] == ["ship it", "ship it"]
    assert [call["sessionId"] for call in payload["streamCalls"]] == [
        "session-1",
        "session-1",
    ]
    assert [call["options"]["targetRoleId"] for call in payload["streamCalls"]] == [
        "writer",
        "writer",
    ]
    assert payload["liveRounds"] == [
        {
            "runId": "run-1",
            "text": "ship it",
            "intentParts": [{"kind": "text", "text": "ship it"}],
        },
        {
            "runId": "run-1",
            "text": "ship it",
            "intentParts": [{"kind": "text", "text": "ship it"}],
        },
    ]
    assert payload["roundMessages"] == [
        {"runId": "run-1", "prompt": [{"kind": "text", "text": "ship it"}]},
        {"runId": "run-1", "prompt": [{"kind": "text", "text": "ship it"}]},
    ]


def test_prompt_paste_blocks_by_model_capability_and_sends_inline_images(
    tmp_path: Path,
) -> None:
    source = Path("frontend/dist/js/app/prompt.js").read_text(encoding="utf-8")
    temp_dir = tmp_path / "prompt_images"
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
export function appendRoundUserMessage(runId, prompt) {
    globalThis.__roundMessages.push({ runId, prompt });
}

export function createLiveRound(runId, text, intentParts) {
    globalThis.__liveRounds.push({ runId, text, intentParts });
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
            {
                role_id: "MainAgent",
                name: "Main Agent",
                description: "Default role",
                model_name: "gpt-4o-mini",
                capabilities: { input: { image: false } },
            },
            {
                role_id: "writer",
                name: "Writer",
                description: "Vision capable",
                model_name: "gpt-4.1",
                capabilities: { input: { image: true } },
            },
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
    {
        role_id: "MainAgent",
        name: "Main Agent",
        description: "Default role",
        model_name: "gpt-4o-mini",
        capabilities: { input: { image: false } },
    },
    {
        role_id: "writer",
        name: "Writer",
        description: "Vision capable",
        model_name: "gpt-4.1",
        capabilities: { input: { image: true } },
    },
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

export function getPrimaryRoleId() {
    return String(state.currentNormalRootRoleId || mainAgentRoleId || "");
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

export function getRoleInputModalitySupport(roleId, modality) {
    const match = normalModeRoles.find(role => role.role_id === roleId);
    const value = match?.capabilities?.input?.[modality];
    return value === true ? true : (value === false ? false : null);
}

export function getRoleModelName(roleId, { fallback = "" } = {}) {
    const match = normalModeRoles.find(role => role.role_id === roleId);
    return String(match?.model_name || fallback || "");
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
        dataset: {},
        selectionStart: 0,
        selectionEnd: 0,
        style: { display: "", height: "" },
        classList: { toggle() { return undefined; } },
        addEventListener() { return undefined; },
        querySelectorAll() { return []; },
        focus() { return undefined; },
        contains(target) { return target === this; },
        ...initial,
    };
}

export const els = {
    promptInput: createElement({ value: "", selectionStart: 0, selectionEnd: 0 }),
    promptMentionMenu: createElement({ hidden: true }),
    composerMediaPreviews: createElement({ style: { display: "none" } }),
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
const translations = {
    "composer.warning.run_in_progress": "run in progress",
    "composer.error.no_active_session": "no active session",
    "composer.error.paused_subagent": "paused {agent}",
    "composer.error.empty_after_mention": "empty after mention",
    "composer.log.sending_prompt": "Sending prompt",
    "composer.error.mention_not_found": "mention not found",
    "composer.error.mention_ambiguous": "mention ambiguous: {roles}",
    "composer.error.image_input_unsupported": "Model {model} does not support image input.",
    "composer.error.image_input_unknown": "Cannot confirm whether model {model} supports image input. Mark the model as supporting image input in model settings.",
    "composer.error.image_paste_failed": "paste failed",
    "composer.image_attachment": "image",
    "composer.image_attachment_kind": "Pasted image",
    "composer.remove_image_attachment": "Remove image",
    "composer.selected_model": "Selected model",
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
    (temp_dir / "mockLogger.mjs").write_text(
        """
export function sysLog(message, tone = "log-info") {
    globalThis.__logs.push({ message, tone });
}
""".strip(),
        encoding="utf-8",
    )

    runner = """
import {
    handlePromptComposerPaste,
    handleSend,
} from "./prompt.js";
import { els } from "./mockDom.mjs";

globalThis.__logs = [];
globalThis.__streamCalls = [];
globalThis.__liveRounds = [];
globalThis.__roundMessages = [];

globalThis.FileReader = class {
    readAsDataURL(file) {
        this.result = `data:${file.type};base64,QUJD`;
        this.onload();
    }
};

function buildPasteEvent(file) {
    return {
        prevented: false,
        clipboardData: {
            items: [
                {
                    type: file.type,
                    getAsFile() {
                        return file;
                    },
                },
            ],
        },
        preventDefault() {
            this.prevented = true;
        },
    };
}

const blockedEvent = buildPasteEvent({ name: "blocked.png", size: 12, type: "image/png" });
await handlePromptComposerPaste(blockedEvent);

els.promptInput.value = "@Writer inspect this";
els.promptInput.selectionStart = els.promptInput.value.length;
els.promptInput.selectionEnd = els.promptInput.value.length;
const allowedEvent = buildPasteEvent({ name: "allowed.png", size: 24, type: "image/png" });
await handlePromptComposerPaste(allowedEvent);
await handleSend();

console.log(JSON.stringify({
    blockedPrevented: blockedEvent.prevented,
    allowedPrevented: allowedEvent.prevented,
    logs: globalThis.__logs,
    streamCalls: globalThis.__streamCalls,
    liveRounds: globalThis.__liveRounds,
    roundMessages: globalThis.__roundMessages,
    mediaPreviewDisplay: els.composerMediaPreviews.style.display,
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
    assert payload["blockedPrevented"] is True
    assert payload["allowedPrevented"] is True
    assert payload["logs"][0] == {
        "message": "Model gpt-4o-mini does not support image input.",
        "tone": "log-error",
    }
    assert payload["streamCalls"][0]["text"] == "inspect this"
    assert payload["streamCalls"][0]["options"]["targetRoleId"] == "writer"
    assert payload["streamCalls"][0]["options"]["inputParts"] == [
        {"kind": "text", "text": "inspect this"},
        {
            "kind": "inline_media",
            "modality": "image",
            "mime_type": "image/png",
            "base64_data": "QUJD",
            "name": "allowed.png",
            "size_bytes": 24,
        },
    ]
    assert payload["liveRounds"] == [
        {
            "runId": "run-1",
            "text": "inspect this\n\n[image: allowed.png]",
            "intentParts": [
                {"kind": "text", "text": "inspect this"},
                {
                    "kind": "inline_media",
                    "modality": "image",
                    "mime_type": "image/png",
                    "base64_data": "QUJD",
                    "name": "allowed.png",
                    "size_bytes": 24,
                },
            ],
        },
    ]
    assert payload["roundMessages"] == [
        {
            "runId": "run-1",
            "prompt": [
                {"kind": "text", "text": "inspect this"},
                {
                    "kind": "inline_media",
                    "modality": "image",
                    "mime_type": "image/png",
                    "base64_data": "QUJD",
                    "name": "allowed.png",
                    "size_bytes": 24,
                },
            ],
        },
    ]
    assert payload["mediaPreviewDisplay"] == "none"


def test_prompt_paste_allows_orchestration_images_when_coordinator_supports_them(
    tmp_path: Path,
) -> None:
    source = Path("frontend/dist/js/app/prompt.js").read_text(encoding="utf-8")
    temp_dir = tmp_path / "prompt_orchestration_images"
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
export function appendRoundUserMessage(runId, prompt) {
    globalThis.__roundMessages.push({ runId, prompt });
}

export function createLiveRound(runId, text, intentParts) {
    globalThis.__liveRounds.push({ runId, text, intentParts });
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
        coordinator_role: {
            role_id: "Coordinator",
            name: "Coordinator",
            description: "Coordinates the run",
            model_name: "gpt-4.1",
            capabilities: { input: { image: true } },
        },
        main_agent_role_id: "MainAgent",
        normal_mode_roles: [
            {
                role_id: "MainAgent",
                name: "Main Agent",
                description: "Default role",
                model_name: "gpt-4o-mini",
                capabilities: { input: { image: false } },
            },
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
        session_mode: "orchestration",
        normal_root_role_id: null,
        orchestration_preset_id: "preset-1",
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
    currentSessionMode: "orchestration",
    currentSessionCanSwitchMode: true,
    currentNormalRootRoleId: null,
    currentOrchestrationPresetId: "preset-1",
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
let coordinatorRoleOption = null;
let mainAgentRoleId = "MainAgent";
let normalModeRoles = [
    {
        role_id: "MainAgent",
        name: "Main Agent",
        description: "Default role",
        model_name: "gpt-4o-mini",
        capabilities: { input: { image: false } },
    },
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

export function getPrimaryRoleId(sessionMode) {
    return sessionMode === "orchestration"
        ? coordinatorRoleId
        : String(state.currentNormalRootRoleId || mainAgentRoleId || "");
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

export function getRoleInputModalitySupport(roleId, modality) {
    const coordinatorValue =
        roleId === coordinatorRoleId
            ? coordinatorRoleOption?.capabilities?.input?.[modality]
            : undefined;
    if (coordinatorValue === true) return true;
    if (coordinatorValue === false) return false;
    const match = normalModeRoles.find(role => role.role_id === roleId);
    const value = match?.capabilities?.input?.[modality];
    return value === true ? true : (value === false ? false : null);
}

export function getRoleModelName(roleId, { fallback = "" } = {}) {
    if (roleId === coordinatorRoleId) {
        return String(coordinatorRoleOption?.model_name || fallback || "");
    }
    const match = normalModeRoles.find(role => role.role_id === roleId);
    return String(match?.model_name || fallback || "");
}

export function setCoordinatorRoleId(roleId) {
    coordinatorRoleId = String(roleId || "");
}

export function setCoordinatorRoleOption(roleOption) {
    coordinatorRoleOption = roleOption || null;
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
        dataset: {},
        selectionStart: 0,
        selectionEnd: 0,
        style: { display: "", height: "" },
        classList: { toggle() { return undefined; } },
        addEventListener() { return undefined; },
        querySelectorAll() { return []; },
        focus() { return undefined; },
        contains(target) { return target === this; },
        ...initial,
    };
}

export const els = {
    promptInput: createElement({ value: "", selectionStart: 0, selectionEnd: 0 }),
    promptMentionMenu: createElement({ hidden: true }),
    composerMediaPreviews: createElement({ style: { display: "none" } }),
    sendBtn: createElement(),
    stopBtn: createElement({ style: { display: "none" } }),
    yoloToggle: createElement({ checked: true }),
    thinkingModeToggle: createElement({ checked: false }),
    thinkingEffortSelect: createElement({ value: "medium", disabled: true }),
    sessionModeLock: createElement(),
    sessionModeNormalBtn: createElement(),
    sessionModeOrchestrationBtn: createElement(),
    normalRoleSelect: createElement(),
    orchestrationPresetSelect: createElement(),
    sessionModeLabel: createElement(),
    normalRoleField: createElement({ style: { display: "" } }),
    orchestrationPresetField: createElement({ style: { display: "" } }),
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
const translations = {
    "composer.warning.run_in_progress": "run in progress",
    "composer.error.no_active_session": "no active session",
    "composer.error.paused_subagent": "paused {agent}",
    "composer.error.empty_after_mention": "empty after mention",
    "composer.log.sending_prompt": "Sending prompt",
    "composer.error.mention_not_found": "mention not found",
    "composer.error.mention_ambiguous": "mention ambiguous: {roles}",
    "composer.error.image_input_unsupported": "Model {model} does not support image input.",
    "composer.error.image_input_unknown": "Cannot confirm whether model {model} supports image input. Mark the model as supporting image input in model settings.",
    "composer.error.image_paste_failed": "paste failed",
    "composer.image_attachment": "image",
    "composer.image_attachment_kind": "Pasted image",
    "composer.remove_image_attachment": "Remove image",
    "composer.selected_model": "Selected model",
    "composer.mode_orchestration": "Orchestration",
    "composer.mode_normal": "Normal",
    "composer.no_roles": "No roles",
    "composer.no_presets": "No presets",
    "composer.disabled.no_preset": "No preset",
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
    (temp_dir / "mockLogger.mjs").write_text(
        """
export function sysLog(message, tone = "log-info") {
    globalThis.__logs.push({ message, tone });
}
""".strip(),
        encoding="utf-8",
    )

    runner = """
import {
    handlePromptComposerPaste,
    handleSend,
    initializeSessionTopologyControls,
} from "./prompt.js";
import { els } from "./mockDom.mjs";

globalThis.__logs = [];
globalThis.__streamCalls = [];
globalThis.__liveRounds = [];
globalThis.__roundMessages = [];
globalThis.document = {
    addEventListener() {
        return undefined;
    },
};

globalThis.FileReader = class {
    readAsDataURL(file) {
        this.result = `data:${file.type};base64,QUJD`;
        this.onload();
    }
};

function buildPasteEvent(file) {
    return {
        prevented: false,
        clipboardData: {
            items: [
                {
                    type: file.type,
                    getAsFile() {
                        return file;
                    },
                },
            ],
        },
        preventDefault() {
            this.prevented = true;
        },
    };
}

await initializeSessionTopologyControls();
els.promptInput.value = "inspect this orchestration image";
els.promptInput.selectionStart = els.promptInput.value.length;
els.promptInput.selectionEnd = els.promptInput.value.length;
const pasteEvent = buildPasteEvent({ name: "orchestrated.png", size: 32, type: "image/png" });
await handlePromptComposerPaste(pasteEvent);
await handleSend();

console.log(JSON.stringify({
    pastePrevented: pasteEvent.prevented,
    logs: globalThis.__logs,
    streamCalls: globalThis.__streamCalls,
    liveRounds: globalThis.__liveRounds,
    roundMessages: globalThis.__roundMessages,
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
    assert payload["pastePrevented"] is True
    assert payload["logs"] == [{"message": "Sending prompt", "tone": "log-info"}]
    assert payload["streamCalls"] == [
        {
            "text": "inspect this orchestration image",
            "sessionId": "session-1",
            "options": {
                "targetRoleId": "Coordinator",
                "yolo": True,
                "thinking": {"enabled": False, "effort": "medium"},
                "inputParts": [
                    {"kind": "text", "text": "inspect this orchestration image"},
                    {
                        "kind": "inline_media",
                        "modality": "image",
                        "mime_type": "image/png",
                        "base64_data": "QUJD",
                        "name": "orchestrated.png",
                        "size_bytes": 32,
                    },
                ],
            },
        }
    ]
    assert payload["liveRounds"] == [
        {
            "runId": "run-1",
            "text": "inspect this orchestration image\n\n[image: orchestrated.png]",
            "intentParts": [
                {"kind": "text", "text": "inspect this orchestration image"},
                {
                    "kind": "inline_media",
                    "modality": "image",
                    "mime_type": "image/png",
                    "base64_data": "QUJD",
                    "name": "orchestrated.png",
                    "size_bytes": 32,
                },
            ],
        }
    ]
    assert payload["roundMessages"] == [
        {
            "runId": "run-1",
            "prompt": [
                {"kind": "text", "text": "inspect this orchestration image"},
                {
                    "kind": "inline_media",
                    "modality": "image",
                    "mime_type": "image/png",
                    "base64_data": "QUJD",
                    "name": "orchestrated.png",
                    "size_bytes": 32,
                },
            ],
        }
    ]


def test_model_profile_update_event_refreshes_prompt_role_capabilities(
    tmp_path: Path,
) -> None:
    source = Path("frontend/dist/js/app/prompt.js").read_text(encoding="utf-8")
    temp_dir = tmp_path / "prompt_profile_updates"
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
let loadCount = 0;

export async function fetchRoleConfigOptions() {
    loadCount += 1;
    globalThis.__roleOptionsLoads = loadCount;
    return {
        coordinator_role_id: "Coordinator",
        main_agent_role_id: "MainAgent",
        normal_mode_roles: [
            {
                role_id: "MainAgent",
                name: "Main Agent",
                description: "Default role",
                model_name: loadCount > 1 ? "gpt-4.1" : "gpt-4o-mini",
                capabilities: { input: { image: loadCount > 1 } },
            },
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
let normalModeRoles = [];

export function applyCurrentSessionRecord() {
    return undefined;
}

export function getCoordinatorRoleId() {
    return coordinatorRoleId;
}

export function getMainAgentRoleId() {
    return mainAgentRoleId;
}

export function getPrimaryRoleId() {
    return String(state.currentNormalRootRoleId || mainAgentRoleId || "");
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

export function getRoleInputModalitySupport(roleId, modality) {
    const match = normalModeRoles.find(role => role.role_id === roleId);
    const value = match?.capabilities?.input?.[modality];
    return value === true ? true : (value === false ? false : null);
}

export function getRoleModelName(roleId, { fallback = "" } = {}) {
    const match = normalModeRoles.find(role => role.role_id === roleId);
    return String(match?.model_name || fallback || "");
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
        hidden: false,
        textContent: "",
        innerHTML: "",
        title: "",
        dataset: {},
        selectionStart: 0,
        selectionEnd: 0,
        style: { display: "", height: "" },
        classList: { toggle() { return undefined; } },
        addEventListener() { return undefined; },
        querySelectorAll() { return []; },
        focus() { return undefined; },
        contains(target) { return target === this; },
        ...initial,
    };
}

export const els = {
    promptInput: createElement({ value: "", selectionStart: 0, selectionEnd: 0 }),
    promptMentionMenu: createElement({ hidden: true }),
    composerMediaPreviews: createElement({ style: { display: "none" } }),
    sendBtn: createElement(),
    stopBtn: createElement({ style: { display: "none" } }),
    yoloToggle: createElement({ checked: true }),
    thinkingModeToggle: createElement({ checked: false }),
    thinkingEffortSelect: createElement({ value: "medium", disabled: true }),
    sessionModeLock: createElement(),
    sessionModeLabel: createElement(),
    sessionModeNormalBtn: createElement(),
    sessionModeOrchestrationBtn: createElement(),
    normalRoleField: createElement(),
    normalRoleSelect: createElement(),
    orchestrationPresetField: createElement({ hidden: true }),
    orchestrationPresetSelect: createElement(),
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
const translations = {
    "composer.error.image_input_unsupported": "Model {model} does not support image input.",
    "composer.error.image_input_unknown": "Cannot confirm whether model {model} supports image input. Mark the model as supporting image input in model settings.",
    "composer.error.image_paste_failed": "paste failed",
    "composer.image_attachment": "image",
    "composer.image_attachment_kind": "Pasted image",
    "composer.remove_image_attachment": "Remove image",
    "composer.selected_model": "Selected model",
    "composer.mode_normal": "Normal",
    "composer.mode_orchestration": "Orchestration",
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
    (temp_dir / "mockLogger.mjs").write_text(
        """
export function sysLog(message, tone = "log-info") {
    globalThis.__logs.push({ message, tone });
}
""".strip(),
        encoding="utf-8",
    )

    runner = """
globalThis.__logs = [];
globalThis.__roleOptionsLoads = 0;
globalThis.FileReader = class {
    readAsDataURL(file) {
        this.result = `data:${file.type};base64,QUJD`;
        this.onload();
    }
};
globalThis.localStorage = {
    getItem() {
        return null;
    },
    setItem() {
        return undefined;
    },
};
globalThis.document = {
    _listeners: new Map(),
    addEventListener(type, listener) {
        this._listeners.set(type, listener);
    },
    dispatchEvent(event) {
        const listener = this._listeners.get(event?.type);
        if (listener) {
            listener(event);
        }
        return true;
    },
};

const prompt = await import("./prompt.js");
const { els } = await import("./mockDom.mjs");

function buildPasteEvent(file) {
    return {
        prevented: false,
        clipboardData: {
            items: [
                {
                    type: file.type,
                    getAsFile() {
                        return file;
                    },
                },
            ],
        },
        preventDefault() {
            this.prevented = true;
        },
    };
}

await prompt.initializeSessionTopologyControls();

const blockedEvent = buildPasteEvent({ name: "blocked.png", size: 12, type: "image/png" });
await prompt.handlePromptComposerPaste(blockedEvent);

document.dispatchEvent({ type: "agent-teams-model-profiles-updated" });
await Promise.resolve();
await Promise.resolve();

const allowedEvent = buildPasteEvent({ name: "allowed.png", size: 24, type: "image/png" });
await prompt.handlePromptComposerPaste(allowedEvent);

console.log(JSON.stringify({
    roleOptionsLoads: globalThis.__roleOptionsLoads,
    blockedPrevented: blockedEvent.prevented,
    allowedPrevented: allowedEvent.prevented,
    logs: globalThis.__logs,
    mediaPreviewDisplay: els.composerMediaPreviews.style.display,
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
    assert payload["roleOptionsLoads"] == 2
    assert payload["blockedPrevented"] is True
    assert payload["allowedPrevented"] is True
    assert payload["logs"] == [
        {
            "message": "Model gpt-4o-mini does not support image input.",
            "tone": "log-error",
        }
    ]
    assert payload["mediaPreviewDisplay"] == "flex"


def test_prompt_pasted_image_preview_supports_open_and_close(tmp_path: Path) -> None:
    source = Path("frontend/dist/js/app/prompt.js").read_text(encoding="utf-8")
    temp_dir = tmp_path / "prompt_image_preview"
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
            {
                role_id: "MainAgent",
                name: "Main Agent",
                description: "Default role",
                model_name: "gpt-4.1",
                capabilities: { input: { image: true } },
            },
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
let normalModeRoles = [];

export function applyCurrentSessionRecord() {
    return undefined;
}

export function getCoordinatorRoleId() {
    return coordinatorRoleId;
}

export function getMainAgentRoleId() {
    return mainAgentRoleId;
}

export function getPrimaryRoleId() {
    return String(state.currentNormalRootRoleId || mainAgentRoleId || "");
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

export function getRoleInputModalitySupport(roleId, modality) {
    const match = normalModeRoles.find(role => role.role_id === roleId);
    const value = match?.capabilities?.input?.[modality];
    return value === true ? true : (value === false ? false : null);
}

export function getRoleModelName(roleId, { fallback = "" } = {}) {
    const match = normalModeRoles.find(role => role.role_id === roleId);
    return String(match?.model_name || fallback || "");
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
        hidden: false,
        textContent: "",
        innerHTML: "",
        title: "",
        dataset: {},
        children: [],
        selectionStart: 0,
        selectionEnd: 0,
        style: { display: "", height: "" },
        classList: { toggle() { return undefined; } },
        addEventListener() { return undefined; },
        appendChild(child) {
            this.children.push(child);
            child.parentNode = this;
            return child;
        },
        replaceChildren(...children) {
            this.children = children.filter(Boolean);
            this.children.forEach(child => {
                child.parentNode = this;
            });
        },
        querySelectorAll() { return []; },
        focus() { return undefined; },
        setAttribute(name, value) {
            this[name] = value;
        },
        contains(target) { return target === this || this.children.includes(target); },
        ...initial,
    };
}

export const els = {
    promptInput: createElement({ value: "", selectionStart: 0, selectionEnd: 0 }),
    promptMentionMenu: createElement({ hidden: true }),
    composerMediaPreviews: createElement({ style: { display: "none" } }),
    sendBtn: createElement(),
    stopBtn: createElement({ style: { display: "none" } }),
    yoloToggle: createElement({ checked: true }),
    thinkingModeToggle: createElement({ checked: false }),
    thinkingEffortSelect: createElement({ value: "medium", disabled: true }),
    sessionModeLock: createElement(),
    sessionModeLabel: createElement(),
    sessionModeNormalBtn: createElement(),
    sessionModeOrchestrationBtn: createElement(),
    normalRoleField: createElement(),
    normalRoleSelect: createElement(),
    orchestrationPresetField: createElement({ hidden: true }),
    orchestrationPresetSelect: createElement(),
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
const translations = {
    "composer.error.image_input_unsupported": "Model {model} does not support image input.",
    "composer.error.image_input_unknown": "Cannot confirm whether model {model} supports image input. Mark the model as supporting image input in model settings.",
    "composer.error.image_paste_failed": "paste failed",
    "composer.image_attachment": "image",
    "composer.image_attachment_kind": "Pasted image",
    "composer.remove_image_attachment": "Remove image",
    "composer.close_image_preview": "Close image preview",
    "composer.selected_model": "Selected model",
    "composer.mode_normal": "Normal",
    "composer.mode_orchestration": "Orchestration",
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
    (temp_dir / "mockLogger.mjs").write_text(
        """
export function sysLog(message, tone = "log-info") {
    globalThis.__logs.push({ message, tone });
}
""".strip(),
        encoding="utf-8",
    )

    runner = """
globalThis.__logs = [];
globalThis.__documentListeners = new Map();
globalThis.FileReader = class {
    readAsDataURL(file) {
        this.result = `data:${file.type};base64,QUJD`;
        this.onload();
    }
};
globalThis.localStorage = {
    getItem() {
        return null;
    },
    setItem() {
        return undefined;
    },
};

function createNode(tagName) {
    return {
        tagName,
        className: "",
        type: "",
        title: "",
        textContent: "",
        innerHTML: "",
        hidden: false,
        src: "",
        alt: "",
        dataset: {},
        style: {},
        children: [],
        parentNode: null,
        appendChild(child) {
            this.children.push(child);
            child.parentNode = this;
            return child;
        },
        replaceChildren(...children) {
            this.children = children.filter(Boolean);
            this.children.forEach(child => {
                child.parentNode = this;
            });
        },
        setAttribute(name, value) {
            this[name] = value;
        },
        addEventListener() {
            return undefined;
        },
        querySelectorAll() {
            return [];
        },
        focus() {
            return undefined;
        },
        contains(target) {
            if (target === this) {
                return true;
            }
            return this.children.includes(target);
        },
    };
}

globalThis.document = {
    body: createNode("body"),
    createElement(tagName) {
        return createNode(tagName);
    },
    addEventListener(type, listener) {
        globalThis.__documentListeners.set(type, listener);
    },
};

const prompt = await import("./prompt.js");
const { els } = await import("./mockDom.mjs");

function buildPasteEvent(file) {
    return {
        prevented: false,
        clipboardData: {
            items: [
                {
                    type: file.type,
                    getAsFile() {
                        return file;
                    },
                },
            ],
        },
        preventDefault() {
            this.prevented = true;
        },
    };
}

await prompt.initializeSessionTopologyControls();

const pasteEvent = buildPasteEvent({ name: "preview.png", size: 24, type: "image/png" });
await prompt.handlePromptComposerPaste(pasteEvent);

const chip = els.composerMediaPreviews.children[0];
const previewButton = chip.children[0];
previewButton.onclick();

const overlay = document.body.children[0];
const imageEl = overlay.children[0].children[1];
const closeEl = overlay.children[0].children[0].children[1];
const overlayHiddenBeforeEscape = overlay.hidden;
const overlayImageSrcBeforeEscape = imageEl.src;
const overlayImageAltBeforeEscape = imageEl.alt;
const keydownListener = globalThis.__documentListeners.get("keydown");
keydownListener({ key: "Escape" });

console.log(JSON.stringify({
    prevented: pasteEvent.prevented,
    mediaPreviewDisplay: els.composerMediaPreviews.style.display,
    chipCount: els.composerMediaPreviews.children.length,
    previewTitle: previewButton.title,
    overlayHiddenBeforeEscape,
    overlayImageSrcBeforeEscape,
    overlayImageAltBeforeEscape,
    overlayHiddenAfterEscape: overlay.hidden,
    overlayImageSrcAfterEscape: imageEl.src,
    overlayImageAltAfterEscape: imageEl.alt,
    closeLabel: closeEl["aria-label"],
    logs: globalThis.__logs,
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
    assert payload["prevented"] is True
    assert payload["mediaPreviewDisplay"] == "flex"
    assert payload["chipCount"] == 1
    assert payload["previewTitle"] == "preview.png"
    assert payload["overlayHiddenBeforeEscape"] is False
    assert payload["overlayImageSrcBeforeEscape"] == "data:image/png;base64,QUJD"
    assert payload["overlayImageAltBeforeEscape"] == "preview.png"
    assert payload["overlayHiddenAfterEscape"] is True
    assert payload["overlayImageSrcAfterEscape"] == ""
    assert payload["overlayImageAltAfterEscape"] == ""
    assert payload["closeLabel"] == "Close image preview"
    assert payload["logs"] == []


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
            { role_id: "writer", name: "Writer", description: "Draft final responses" },
            { role_id: "reviewer", name: "Reviewer", description: "Check correctness and risk" },
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
    { role_id: "writer", name: "Writer", description: "Draft final responses" },
    { role_id: "reviewer", name: "Reviewer", description: "Check correctness and risk" },
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

export function getPrimaryRoleId() {
    return String(state.currentNormalRootRoleId || mainAgentRoleId || "");
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

export function getRoleInputModalitySupport(roleId, modality) {
    const match = normalModeRoles.find(role => role.role_id === roleId);
    const value = match?.capabilities?.input?.[modality];
    return value === true ? true : (value === false ? false : null);
}

export function getRoleModelName(roleId, { fallback = "" } = {}) {
    const match = normalModeRoles.find(role => role.role_id === roleId);
    return String(match?.model_name || fallback || "");
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
        _scrollEvents: [],
        addEventListener(type, listener) {
            this._listeners.set(type, listener);
        },
        querySelector(selector) {
            if (selector !== ".prompt-mention-item.active") {
                return null;
            }
            return {
                scrollIntoView: (options) => {
                    this._scrollEvents.push(options);
                },
            };
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
        value: "@",
        selectionStart: 1,
        selectionEnd: 1,
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

export function formatMessage(key, values = {}) {
    return Object.entries(values).reduce(
        (message, [name, value]) => message.replaceAll(`{${name}}`, String(value)),
        t(key),
    );
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
const beforeAsciiSelect = {
    menuHidden: els.promptMentionMenu.hidden,
    menuHtml: els.promptMentionMenu.innerHTML,
    scrollEvents: els.promptMentionMenu._scrollEvents.slice(),
};

const arrowDownHandled = handlePromptComposerKeydown({
    key: "ArrowDown",
    preventDefault() { return undefined; },
    stopImmediatePropagation() { return undefined; },
    stopPropagation() { return undefined; },
});

const afterArrowDownScrollEvents = els.promptMentionMenu._scrollEvents.slice();

const asciiEnterHandled = handlePromptComposerKeydown({
    key: "Enter",
    preventDefault() { return undefined; },
    stopImmediatePropagation() { return undefined; },
    stopPropagation() { return undefined; },
});

const asciiValue = els.promptInput.value;
const asciiSelectionStart = els.promptInput.selectionStart;
const asciiSelectionEnd = els.promptInput.selectionEnd;

els.promptInput.value = "＠Ma";
els.promptInput.selectionStart = 3;
els.promptInput.selectionEnd = 3;
handlePromptComposerInput();
const beforeFullwidthSelect = {
    menuHidden: els.promptMentionMenu.hidden,
    menuHtml: els.promptMentionMenu.innerHTML,
    scrollEvents: els.promptMentionMenu._scrollEvents.slice(),
};

const fullwidthEnterHandled = handlePromptComposerKeydown({
    key: "Enter",
    preventDefault() { return undefined; },
    stopImmediatePropagation() { return undefined; },
    stopPropagation() { return undefined; },
});

console.log(JSON.stringify({
    beforeAsciiSelect,
    arrowDownHandled,
    afterArrowDownScrollEvents,
    asciiEnterHandled,
    asciiValue,
    asciiSelectionStart,
    asciiSelectionEnd,
    beforeFullwidthSelect,
    fullwidthEnterHandled,
    fullwidthValue: els.promptInput.value,
    fullwidthSelectionStart: els.promptInput.selectionStart,
    fullwidthSelectionEnd: els.promptInput.selectionEnd,
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
    rendered_ascii_text = re.sub(
        r"<[^>]+>", "", payload["beforeAsciiSelect"]["menuHtml"]
    )
    rendered_fullwidth_text = re.sub(
        r"<[^>]+>", "", payload["beforeFullwidthSelect"]["menuHtml"]
    )
    assert payload["beforeAsciiSelect"]["menuHidden"] is False
    assert payload["beforeFullwidthSelect"]["menuHidden"] is False
    assert "prompt-mention-menu-header" in payload["beforeAsciiSelect"]["menuHtml"]
    assert "prompt-mention-item-accent" in payload["beforeAsciiSelect"]["menuHtml"]
    assert "prompt-mention-menu-footer" in payload["beforeAsciiSelect"]["menuHtml"]
    assert "prompt-mention-match" in payload["beforeFullwidthSelect"]["menuHtml"]
    assert "Draft final responses" in rendered_ascii_text
    assert "Main Agent" in rendered_ascii_text
    assert "MainAgent" in rendered_ascii_text
    assert "Main Agent" in rendered_fullwidth_text
    assert "MainAgent" in rendered_fullwidth_text
    assert payload["arrowDownHandled"] is True
    assert (
        len(payload["afterArrowDownScrollEvents"])
        == len(payload["beforeAsciiSelect"]["scrollEvents"]) + 1
    )
    assert payload["afterArrowDownScrollEvents"][-1] == {"block": "nearest"}
    assert payload["asciiEnterHandled"] is True
    assert payload["asciiValue"] == "@Main Agent "
    assert payload["asciiSelectionStart"] == 12
    assert payload["asciiSelectionEnd"] == 12
    assert payload["fullwidthEnterHandled"] is True
    assert payload["fullwidthValue"] == "＠Main Agent "
    assert payload["fullwidthSelectionStart"] == 12
    assert payload["fullwidthSelectionEnd"] == 12
