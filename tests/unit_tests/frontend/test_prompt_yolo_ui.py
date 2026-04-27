# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from pathlib import Path
import subprocess

import pytest


@pytest.fixture(autouse=True)
def _write_prompt_tokens_test_module(tmp_path: Path) -> None:
    utils_dir = tmp_path / "utils"
    utils_dir.mkdir(exist_ok=True)
    source = Path("frontend/dist/js/utils/promptTokens.js").read_text(encoding="utf-8")
    (utils_dir / "promptTokens.js").write_text(source, encoding="utf-8")


def test_chat_input_renders_yolo_and_thinking_controls() -> None:
    html = Path("frontend/dist/index.html").read_text(encoding="utf-8")
    orchestration_css = Path(
        "frontend/dist/css/components/orchestration.css"
    ).read_text(encoding="utf-8")
    new_session_composer_css = Path(
        "frontend/dist/css/components/new-session-draft-composer.css"
    ).read_text(encoding="utf-8")

    assert 'id="yolo-toggle"' in html
    assert 'id="thinking-mode-toggle"' in html
    assert 'id="thinking-effort-field"' in html
    assert re.search(r'id="thinking-effort-field"[\s\S]*?\bhidden\b', html)
    assert 'id="thinking-effort-select"' in html
    assert 'id="prompt-mention-menu"' in html
    assert ".composer-preset-field[hidden]," in orchestration_css
    assert ".composer-mode-inline[hidden]" in orchestration_css
    assert (
        '#input-container.is-new-session-draft-composer .composer-mode-toggle[for="yolo-toggle"]'
        in new_session_composer_css
    )
    assert "margin-left: auto;" in new_session_composer_css
    assert "#input-container.is-new-session-draft-composer .composer-usage-strip" in (
        new_session_composer_css
    )
    assert "display: none;" in new_session_composer_css


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


def test_send_user_prompt_uses_explicit_input_parts_when_provided(
    tmp_path: Path,
) -> None:
    source = Path("frontend/dist/js/core/api/runs.js").read_text(encoding="utf-8")
    temp_dir = tmp_path / "api_input_parts"
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

await sendUserPrompt(
    "session-1",
    "",
    false,
    { enabled: false, effort: null },
    "writer",
    [
        {
            kind: "inline_media",
            modality: "image",
            mime_type: "image/png",
            base64_data: "QUJDRA==",
            name: "diagram.png",
            size_bytes: 4,
        },
    ],
);
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
    assert payload["body"]["input"] == [
        {
            "kind": "inline_media",
            "modality": "image",
            "mime_type": "image/png",
            "base64_data": "QUJDRA==",
            "name": "diagram.png",
            "size_bytes": 4,
        }
    ]


def test_prompt_controls_toggle_mode_specific_fields_and_thinking_effort(
    tmp_path: Path,
) -> None:
    source = Path("frontend/dist/js/app/prompt.js").read_text(encoding="utf-8")
    temp_dir = tmp_path / "prompt"
    temp_dir.mkdir()
    _write_new_session_draft_mock(tmp_path)

    (temp_dir / "prompt.js").write_text(
        source.replace("../components/rounds/timeline.js", "./mockRounds.mjs")
        .replace("../components/rounds.js", "./mockRounds.mjs")
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

export async function fetchCommands() {
    globalThis.__fetchCommandsCalls = (globalThis.__fetchCommandsCalls || 0) + 1;
    if (globalThis.__commandsQueue?.length) {
        const next = globalThis.__commandsQueue.shift();
        return next();
    }
    if (globalThis.__commandsError) {
        throw globalThis.__commandsError;
    }
    return globalThis.__commandsResponse || {
        commands: [
            {
                name: "opsx-propose",
                aliases: ["opsx:propose"],
                description: "Create an OpenSpec proposal",
                argument_hint: "<change-id>",
            },
        ],
    };
}

export async function resolveCommandPrompt(payload) {
    return {
        matched: false,
        expanded_prompt: String(payload?.raw_text || ""),
    };
}

export async function searchWorkspacePaths(workspaceId, query, limit) {
    globalThis.__searchWorkspacePathCalls = [
        ...(globalThis.__searchWorkspacePathCalls || []),
        { workspaceId, query, limit },
    ];
    return globalThis.__resourceResponse || {
        workspace_id: "workspace-1",
        query: "",
        results: [],
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
    currentWorkspaceId: "workspace-1",
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
let coordinatorRoleOption = null;
let mainAgentRoleOption = null;

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

export function getPrimaryRoleId() {
    return String(state.mainAgentRoleId || "MainAgent");
}

export function getRoleOption(roleId) {
    if (String(roleId || "") === String(state.mainAgentRoleId || "")) {
        return mainAgentRoleOption;
    }
    return normalModeRoles.find(role => role.role_id === roleId) || null;
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

export function setCoordinatorRoleOption(roleOption) {
    coordinatorRoleOption = roleOption;
}

export function setMainAgentRoleId(roleId) {
    state.mainAgentRoleId = String(roleId || "");
}

export function setMainAgentRoleOption(roleOption) {
    mainAgentRoleOption = roleOption;
}

export function setNormalModeRoles(roleOptions) {
    normalModeRoles = Array.isArray(roleOptions) ? roleOptions : [];
}

export function roleSupportsInputModality(roleId, modality) {
    return String(roleId || "") !== "" && String(modality || "") === "image";
}

export function getRoleInputModalitySupport(roleId, modality) {
    return roleSupportsInputModality(roleId, modality);
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
        querySelectorAll() {
            return [];
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
    promptAttachments: createElement(),
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
    _write_new_session_draft_mock(tmp_path)

    (temp_dir / "prompt.js").write_text(
        source.replace("../components/rounds/timeline.js", "./mockRounds.mjs")
        .replace("../components/rounds.js", "./mockRounds.mjs")
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

export function createLiveRound(runId, text, inputParts) {
    globalThis.__liveRounds.push({ runId, text, inputParts });
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
        skills: globalThis.__skillsResponse || [],
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

export async function fetchCommands() {
    globalThis.__fetchCommandsCalls = (globalThis.__fetchCommandsCalls || 0) + 1;
    if (globalThis.__commandsQueue?.length) {
        const next = globalThis.__commandsQueue.shift();
        return next();
    }
    if (globalThis.__commandsError) {
        throw globalThis.__commandsError;
    }
    return globalThis.__commandsResponse || {
        commands: [
            {
                name: "opsx-propose",
                aliases: ["opsx:propose"],
                description: "Create an OpenSpec proposal",
                argument_hint: "<change-id>",
            },
        ],
    };
}

export async function resolveCommandPrompt(payload) {
    return {
        matched: false,
        expanded_prompt: String(payload?.raw_text || ""),
    };
}

export async function searchWorkspacePaths(workspaceId, query, limit) {
    globalThis.__searchWorkspacePathCalls = [
        ...(globalThis.__searchWorkspacePathCalls || []),
        { workspaceId, query, limit },
    ];
    return globalThis.__resourceResponse || {
        workspace_id: "workspace-1",
        query: "",
        results: [],
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
    currentWorkspaceId: "workspace-1",
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
let coordinatorRoleOption = null;
let mainAgentRoleOption = null;

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

export function getPrimaryRoleId() {
    return String(state.currentNormalRootRoleId || mainAgentRoleId);
}

export function getRoleOption(roleId) {
    if (roleId === coordinatorRoleId) return coordinatorRoleOption;
    if (roleId === mainAgentRoleId) return mainAgentRoleOption;
    return normalModeRoles.find(role => role.role_id === roleId) || null;
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

export function setCoordinatorRoleOption(roleOption) {
    coordinatorRoleOption = roleOption;
}

export function setMainAgentRoleId(roleId) {
    mainAgentRoleId = String(roleId || "");
}

export function setMainAgentRoleOption(roleOption) {
    mainAgentRoleOption = roleOption;
}

export function setNormalModeRoles(roleOptions) {
    normalModeRoles = Array.isArray(roleOptions) ? roleOptions : [];
}

export function roleSupportsInputModality(roleId, modality) {
    return String(roleId || "") !== "" && String(modality || "") === "image";
}

export function getRoleInputModalitySupport(roleId, modality) {
    return roleSupportsInputModality(roleId, modality);
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
        querySelectorAll() { return []; },
        focus() { return undefined; },
        ...initial,
    };
}

export const els = {
    promptInput: createElement({ value: "@Writer ship it" }),
    promptAttachments: createElement(),
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
            "inputParts": [{"kind": "text", "text": "ship it"}],
        },
        {
            "runId": "run-1",
            "text": "ship it",
            "inputParts": [{"kind": "text", "text": "ship it"}],
        },
    ]
    assert payload["roundMessages"] == []


def test_prompt_role_mentions_offer_autocomplete_and_insert_selection(
    tmp_path: Path,
) -> None:
    source = Path("frontend/dist/js/app/prompt.js").read_text(encoding="utf-8")
    temp_dir = tmp_path / "prompt_autocomplete"
    temp_dir.mkdir()
    _write_new_session_draft_mock(tmp_path)

    (temp_dir / "prompt.js").write_text(
        source.replace("../components/rounds/timeline.js", "./mockRounds.mjs")
        .replace("../components/rounds.js", "./mockRounds.mjs")
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
        skills: globalThis.__skillsResponse || [],
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

export async function fetchCommands() {
    globalThis.__fetchCommandsCalls = (globalThis.__fetchCommandsCalls || 0) + 1;
    if (globalThis.__commandsQueue?.length) {
        const next = globalThis.__commandsQueue.shift();
        return next();
    }
    if (globalThis.__commandsError) {
        throw globalThis.__commandsError;
    }
    return globalThis.__commandsResponse || {
        commands: [
            {
                name: "opsx-propose",
                aliases: ["opsx:propose"],
                description: "Create an OpenSpec proposal",
                argument_hint: "<change-id>",
            },
        ],
    };
}

export async function resolveCommandPrompt(payload) {
    return {
        matched: false,
        expanded_prompt: String(payload?.raw_text || ""),
    };
}

export async function searchWorkspacePaths(workspaceId, query, limit) {
    globalThis.__searchWorkspacePathCalls = [
        ...(globalThis.__searchWorkspacePathCalls || []),
        { workspaceId, query, limit },
    ];
    return globalThis.__resourceResponse || {
        workspace_id: "workspace-1",
        query: "",
        results: [],
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
    currentWorkspaceId: "workspace-1",
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
let coordinatorRoleOption = null;
let mainAgentRoleOption = null;

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

export function getPrimaryRoleId() {
    return String(state.currentNormalRootRoleId || mainAgentRoleId);
}

export function getRoleOption(roleId) {
    if (roleId === coordinatorRoleId) return coordinatorRoleOption;
    if (roleId === mainAgentRoleId) return mainAgentRoleOption;
    return normalModeRoles.find(role => role.role_id === roleId) || null;
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

export function setCoordinatorRoleOption(roleOption) {
    coordinatorRoleOption = roleOption;
}

export function setMainAgentRoleId(roleId) {
    mainAgentRoleId = String(roleId || "");
}

export function setMainAgentRoleOption(roleOption) {
    mainAgentRoleOption = roleOption;
}

export function setNormalModeRoles(roleOptions) {
    normalModeRoles = Array.isArray(roleOptions) ? roleOptions : [];
}

export function roleSupportsInputModality(roleId, modality) {
    return String(roleId || "") !== "" && String(modality || "") === "image";
}

export function getRoleInputModalitySupport(roleId, modality) {
    return roleSupportsInputModality(roleId, modality);
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
        querySelectorAll() {
            return [];
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
    promptAttachments: createElement({ hidden: false }),
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
    invalidatePromptCommandsCache,
    refreshRoleConfigOptions,
} from "./prompt.js";
import { els } from "./mockDom.mjs";
import { state } from "./mockState.mjs";

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
const arrowPreviewValue = els.promptInput.value;
const arrowPreviewSelectionStart = els.promptInput.selectionStart;

const asciiEnterHandled = handlePromptComposerKeydown({
    key: "Enter",
    preventDefault() { return undefined; },
    stopImmediatePropagation() { return undefined; },
    stopPropagation() { return undefined; },
});

const asciiValue = els.promptInput.value;
const asciiSelectionStart = els.promptInput.selectionStart;
const asciiSelectionEnd = els.promptInput.selectionEnd;

els.promptInput.value = "@";
els.promptInput.selectionStart = 1;
els.promptInput.selectionEnd = 1;
handlePromptComposerInput();
const escapePreviewArrowHandled = handlePromptComposerKeydown({
    key: "ArrowDown",
    preventDefault() { return undefined; },
    stopImmediatePropagation() { return undefined; },
    stopPropagation() { return undefined; },
});
const escapePreviewValue = els.promptInput.value;
const escapePreviewHandled = handlePromptComposerKeydown({
    key: "Escape",
    preventDefault() { return undefined; },
    stopImmediatePropagation() { return undefined; },
    stopPropagation() { return undefined; },
});
const escapeRestoredValue = els.promptInput.value;

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
const fullwidthValue = els.promptInput.value;
const fullwidthSelectionStart = els.promptInput.selectionStart;
const fullwidthSelectionEnd = els.promptInput.selectionEnd;

els.promptInput.value = "/";
els.promptInput.selectionStart = 1;
els.promptInput.selectionEnd = 1;
handlePromptComposerInput();
await new Promise(resolve => setTimeout(resolve, 0));
const beforeCommandSelect = {
    menuHidden: els.promptMentionMenu.hidden,
    menuHtml: els.promptMentionMenu.innerHTML,
};

const commandTabHandled = handlePromptComposerKeydown({
    key: "Tab",
    preventDefault() { return undefined; },
    stopImmediatePropagation() { return undefined; },
    stopPropagation() { return undefined; },
});

const commandValue = els.promptInput.value;
const commandSelectionStart = els.promptInput.selectionStart;
const commandSelectionEnd = els.promptInput.selectionEnd;

state.currentWorkspaceId = "workspace-empty";
globalThis.__commandsResponse = { commands: [] };
els.promptInput.value = "/";
els.promptInput.selectionStart = 1;
els.promptInput.selectionEnd = 1;
handlePromptComposerInput();
await new Promise(resolve => setTimeout(resolve, 0));
const emptyCommandPanel = {
    menuHidden: els.promptMentionMenu.hidden,
    menuHtml: els.promptMentionMenu.innerHTML,
};
const emptyEnterHandled = handlePromptComposerKeydown({
    key: "Enter",
    preventDefault() { return undefined; },
    stopImmediatePropagation() { return undefined; },
    stopPropagation() { return undefined; },
});
const emptyEscapeHandled = handlePromptComposerKeydown({
    key: "Escape",
    preventDefault() { return undefined; },
    stopImmediatePropagation() { return undefined; },
    stopPropagation() { return undefined; },
});
const emptyHiddenAfterEscape = els.promptMentionMenu.hidden;

state.currentWorkspaceId = "";
globalThis.__commandsResponse = { commands: [] };
els.promptInput.value = "/";
els.promptInput.selectionStart = 1;
els.promptInput.selectionEnd = 1;
handlePromptComposerInput();
await new Promise(resolve => setTimeout(resolve, 0));
const noWorkspaceCommandPanel = {
    menuHidden: els.promptMentionMenu.hidden,
    menuHtml: els.promptMentionMenu.innerHTML,
};
const noWorkspaceTabHandled = handlePromptComposerKeydown({
    key: "Tab",
    preventDefault() { return undefined; },
    stopImmediatePropagation() { return undefined; },
    stopPropagation() { return undefined; },
});

state.currentWorkspaceId = "workspace-error";
globalThis.__commandsError = new Error("registry down");
els.promptInput.value = "/";
els.promptInput.selectionStart = 1;
els.promptInput.selectionEnd = 1;
handlePromptComposerInput();
await new Promise(resolve => setTimeout(resolve, 0));
const errorCommandPanel = {
    menuHidden: els.promptMentionMenu.hidden,
    menuHtml: els.promptMentionMenu.innerHTML,
};
const commandFetchCallsAfterError = globalThis.__fetchCommandsCalls;

globalThis.__commandsError = null;
globalThis.__commandsResponse = {
    commands: [
        {
            name: "retry",
            aliases: [],
            description: "Recovered command list",
            argument_hint: "",
        },
    ],
};
handlePromptComposerInput();
await new Promise(resolve => setTimeout(resolve, 0));
const retryCommandPanel = {
    menuHidden: els.promptMentionMenu.hidden,
    menuHtml: els.promptMentionMenu.innerHTML,
};
const commandFetchCallsAfterRetry = globalThis.__fetchCommandsCalls;

globalThis.__commandsResponse = {
    commands: [
        {
            name: "fresh",
            aliases: [],
            description: "Fresh command list",
            argument_hint: "",
        },
    ],
};
invalidatePromptCommandsCache();
handlePromptComposerInput();
await new Promise(resolve => setTimeout(resolve, 0));
const invalidatedCommandPanel = {
    menuHidden: els.promptMentionMenu.hidden,
    menuHtml: els.promptMentionMenu.innerHTML,
};
const commandFetchCallsAfterInvalidation = globalThis.__fetchCommandsCalls;

let rejectStaleCommands;
let resolveCurrentCommands;
globalThis.__commandsQueue = [
    () => new Promise((resolve, reject) => {
        rejectStaleCommands = reject;
    }),
    () => new Promise(() => {}),
    () => new Promise((resolve) => {
        resolveCurrentCommands = resolve;
    }),
];
globalThis.__commandsResponse = null;
state.currentWorkspaceId = "workspace-stale";
els.promptInput.value = "/";
els.promptInput.selectionStart = 1;
els.promptInput.selectionEnd = 1;
handlePromptComposerInput();
state.currentWorkspaceId = "workspace-current";
handlePromptComposerInput();
state.currentWorkspaceId = "workspace-stale";
handlePromptComposerInput();
resolveCurrentCommands({
    commands: [
        {
            name: "current",
            aliases: [],
            description: "Current workspace command",
            argument_hint: "",
        },
    ],
});
await new Promise(resolve => setTimeout(resolve, 0));
rejectStaleCommands(new Error("stale registry down"));
await new Promise(resolve => setTimeout(resolve, 0));
const staleFailurePanel = {
    menuHidden: els.promptMentionMenu.hidden,
    menuHtml: els.promptMentionMenu.innerHTML,
};

state.currentWorkspaceId = "workspace-skill";
globalThis.__skillsResponse = [
    {
        ref: "data-analysis",
        name: "Data Analysis",
        description: "Analyze a dataset.",
        source: "builtin",
    },
];
await refreshRoleConfigOptions({ refreshControls: false });
globalThis.__commandsResponse = { commands: [] };
invalidatePromptCommandsCache();
els.promptInput.value = "/Data";
els.promptInput.selectionStart = els.promptInput.value.length;
els.promptInput.selectionEnd = els.promptInput.value.length;
handlePromptComposerInput();
await new Promise(resolve => setTimeout(resolve, 0));
const skillCommandPanel = {
    menuHidden: els.promptMentionMenu.hidden,
    menuHtml: els.promptMentionMenu.innerHTML,
};
const skillTabHandled = handlePromptComposerKeydown({
    key: "Tab",
    preventDefault() { return undefined; },
    stopImmediatePropagation() { return undefined; },
    stopPropagation() { return undefined; },
});
const skillCommandValue = els.promptInput.value;

els.promptInput.value = "/Nope";
els.promptInput.selectionStart = els.promptInput.value.length;
els.promptInput.selectionEnd = els.promptInput.value.length;
handlePromptComposerInput();
await new Promise(resolve => setTimeout(resolve, 0));
const unmatchedActionPanel = {
    menuHidden: els.promptMentionMenu.hidden,
    menuHtml: els.promptMentionMenu.innerHTML,
};

state.currentWorkspaceId = "workspace-files";
globalThis.__resourceResponse = {
    workspace_id: "workspace-files",
    query: "src",
    results: [
        { name: "src", path: "src/", kind: "directory", mount_name: "default" },
        { name: "main.py", path: "src/relay_teams/main.py", kind: "file", mount_name: "default" },
    ],
};
els.promptInput.value = "@src";
els.promptInput.selectionStart = 4;
els.promptInput.selectionEnd = 4;
handlePromptComposerInput();
await new Promise(resolve => setTimeout(resolve, 120));
const directoryPanel = {
    menuHidden: els.promptMentionMenu.hidden,
    menuHtml: els.promptMentionMenu.innerHTML,
};
const directoryEnterHandled = handlePromptComposerKeydown({
    key: "Enter",
    preventDefault() { return undefined; },
    stopImmediatePropagation() { return undefined; },
    stopPropagation() { return undefined; },
});
const directoryValue = els.promptInput.value;
const directorySelectionStart = els.promptInput.selectionStart;
const directoryPanelAfterEnter = {
    menuHidden: els.promptMentionMenu.hidden,
    menuHtml: els.promptMentionMenu.innerHTML,
};

globalThis.__resourceResponse = {
    workspace_id: "workspace-files",
    query: "src/relay_teams/agents/ds",
    results: [],
};
els.promptInput.value = "@src/relay_teams/agents/ds";
els.promptInput.selectionStart = 26;
els.promptInput.selectionEnd = 26;
handlePromptComposerInput();
await new Promise(resolve => setTimeout(resolve, 120));
const emptyResourcePanel = {
    menuHidden: els.promptMentionMenu.hidden,
    menuHtml: els.promptMentionMenu.innerHTML,
};

globalThis.__resourceResponse = {
    workspace_id: "workspace-files",
    query: "relay",
    results: [],
};
els.promptInput.value = "@relay";
els.promptInput.selectionStart = 6;
els.promptInput.selectionEnd = 6;
handlePromptComposerInput();
const cachedRelayPanel = {
    menuHidden: els.promptMentionMenu.hidden,
    menuHtml: els.promptMentionMenu.innerHTML,
};

state.currentWorkspaceId = "workspace-case";
globalThis.__resourceResponse = {
    workspace_id: "workspace-case",
    query: "src/relay_teams/media/",
    results: [],
};
els.promptInput.value = "@src/relay_teams/media/";
els.promptInput.selectionStart = els.promptInput.value.length;
els.promptInput.selectionEnd = els.promptInput.value.length;
handlePromptComposerInput();
await new Promise(resolve => setTimeout(resolve, 120));
const lowerCaseMissPanel = {
    menuHidden: els.promptMentionMenu.hidden,
    menuHtml: els.promptMentionMenu.innerHTML,
};
globalThis.__resourceResponse = {
    workspace_id: "workspace-case",
    query: "Src/Relay_Teams/Media/",
    results: [
        { name: "models.py", path: "Src/Relay_Teams/Media/models.py", kind: "file", mount_name: "default" },
    ],
};
els.promptInput.value = "@Src/Relay_Teams/Media/";
els.promptInput.selectionStart = els.promptInput.value.length;
els.promptInput.selectionEnd = els.promptInput.value.length;
handlePromptComposerInput();
await new Promise(resolve => setTimeout(resolve, 120));
const mixedCaseHitPanel = {
    menuHidden: els.promptMentionMenu.hidden,
    menuHtml: els.promptMentionMenu.innerHTML,
};
const caseResourceCalls = (globalThis.__searchWorkspacePathCalls || [])
    .filter((call) => call.workspaceId === "workspace-case");

console.log(JSON.stringify({
    beforeAsciiSelect,
    arrowDownHandled,
    afterArrowDownScrollEvents,
    arrowPreviewValue,
    arrowPreviewSelectionStart,
    asciiEnterHandled,
    asciiValue,
    asciiSelectionStart,
    asciiSelectionEnd,
    escapePreviewArrowHandled,
    escapePreviewValue,
    escapePreviewHandled,
    escapeRestoredValue,
    beforeFullwidthSelect,
    fullwidthEnterHandled,
    fullwidthValue,
    fullwidthSelectionStart,
    fullwidthSelectionEnd,
    beforeCommandSelect,
    commandTabHandled,
    commandValue,
    commandSelectionStart,
    commandSelectionEnd,
    emptyCommandPanel,
    emptyEnterHandled,
    emptyEscapeHandled,
    emptyHiddenAfterEscape,
    noWorkspaceCommandPanel,
    noWorkspaceTabHandled,
    errorCommandPanel,
    commandFetchCallsAfterError,
    commandFetchCallsAfterRetry,
    retryCommandPanel,
    invalidatedCommandPanel,
    commandFetchCallsAfterInvalidation,
    staleFailurePanel,
    skillCommandPanel,
    skillTabHandled,
    skillCommandValue,
    unmatchedActionPanel,
    directoryPanel,
    directoryEnterHandled,
    directoryValue,
    directorySelectionStart,
    directoryPanelAfterEnter,
    emptyResourcePanel,
    lowerCaseMissPanel,
    mixedCaseHitPanel,
    caseResourceCalls,
    cachedRelayPanel,
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
    rendered_command_text = re.sub(
        r"<[^>]+>", "", payload["beforeCommandSelect"]["menuHtml"]
    )
    rendered_retry_command_text = re.sub(
        r"<[^>]+>", "", payload["retryCommandPanel"]["menuHtml"]
    )
    rendered_invalidated_command_text = re.sub(
        r"<[^>]+>", "", payload["invalidatedCommandPanel"]["menuHtml"]
    )
    rendered_stale_failure_text = re.sub(
        r"<[^>]+>", "", payload["staleFailurePanel"]["menuHtml"]
    )
    rendered_directory_text = re.sub(
        r"<[^>]+>", "", payload["directoryPanel"]["menuHtml"]
    )
    rendered_cached_relay_text = re.sub(
        r"<[^>]+>", "", payload["cachedRelayPanel"]["menuHtml"]
    )
    assert payload["beforeAsciiSelect"]["menuHidden"] is False
    assert payload["beforeFullwidthSelect"]["menuHidden"] is False
    assert "prompt-mention-menu-header" in payload["beforeAsciiSelect"]["menuHtml"]
    assert "prompt-mention-item-accent" in payload["beforeAsciiSelect"]["menuHtml"]
    assert "prompt-mention-match" in payload["beforeFullwidthSelect"]["menuHtml"]
    assert "Draft final responses" in rendered_ascii_text
    assert "Main Agent" in rendered_ascii_text
    assert "MainAgent" in rendered_ascii_text
    assert "Main Agent" in rendered_fullwidth_text
    assert "MainAgent" in rendered_fullwidth_text
    assert payload["arrowDownHandled"] is True
    assert payload["afterArrowDownScrollEvents"] == []
    assert payload["arrowPreviewValue"] == "@Main Agent"
    assert payload["arrowPreviewSelectionStart"] == 11
    assert payload["asciiEnterHandled"] is True
    assert payload["asciiValue"] == "@Main Agent "
    assert payload["asciiSelectionStart"] == 12
    assert payload["asciiSelectionEnd"] == 12
    assert payload["fullwidthEnterHandled"] is True
    assert payload["fullwidthValue"] == "＠Main Agent "
    assert payload["fullwidthSelectionStart"] == 12
    assert payload["fullwidthSelectionEnd"] == 12
    assert payload["escapePreviewArrowHandled"] is True
    assert payload["escapePreviewValue"] == "@Main Agent"
    assert payload["escapePreviewHandled"] is True
    assert payload["escapeRestoredValue"] == "@"
    assert payload["beforeCommandSelect"]["menuHidden"] is False
    assert "/ 命令" in rendered_command_text
    assert "opsx:propose" in rendered_command_text
    assert "Create an OpenSpec proposal" in rendered_command_text
    assert "&lt;change-id&gt;" in payload["beforeCommandSelect"]["menuHtml"]
    assert payload["commandTabHandled"] is True
    assert payload["commandValue"] == "/opsx-propose "
    assert payload["commandSelectionStart"] == 14
    assert payload["commandSelectionEnd"] == 14
    assert payload["emptyCommandPanel"]["menuHidden"] is True
    assert payload["emptyCommandPanel"]["menuHtml"] == ""
    assert payload["emptyEnterHandled"] is False
    assert payload["emptyEscapeHandled"] is False
    assert payload["emptyHiddenAfterEscape"] is True
    assert payload["noWorkspaceCommandPanel"]["menuHidden"] is True
    assert payload["noWorkspaceCommandPanel"]["menuHtml"] == ""
    assert payload["noWorkspaceTabHandled"] is False
    assert payload["errorCommandPanel"]["menuHidden"] is True
    assert payload["errorCommandPanel"]["menuHtml"] == ""
    assert payload["commandFetchCallsAfterRetry"] == (
        payload["commandFetchCallsAfterError"] + 1
    )
    assert payload["retryCommandPanel"]["menuHidden"] is False
    assert "Recovered command list" in rendered_retry_command_text
    assert payload["commandFetchCallsAfterInvalidation"] == (
        payload["commandFetchCallsAfterRetry"] + 1
    )
    assert payload["invalidatedCommandPanel"]["menuHidden"] is False
    assert "Fresh command list" in rendered_invalidated_command_text
    assert payload["staleFailurePanel"]["menuHidden"] is False
    assert "current" in rendered_stale_failure_text
    assert "composer.command_load_failed" not in rendered_stale_failure_text
    assert payload["skillCommandPanel"]["menuHidden"] is False
    assert "Data Analysis" in re.sub(
        r"<[^>]+>", "", payload["skillCommandPanel"]["menuHtml"]
    )
    assert payload["skillTabHandled"] is True
    assert payload["skillCommandValue"] == "/data-analysis "
    assert payload["unmatchedActionPanel"]["menuHidden"] is True
    assert payload["unmatchedActionPanel"]["menuHtml"] == ""
    assert payload["directoryPanel"]["menuHidden"] is False
    assert "src/" in rendered_directory_text
    assert payload["directoryEnterHandled"] is True
    assert payload["directoryValue"] == "@src/"
    assert payload["directorySelectionStart"] == 5
    assert payload["directoryPanelAfterEnter"]["menuHidden"] is False
    assert "src/relay_teams/main.py" in re.sub(
        r"<[^>]+>", "", payload["directoryPanelAfterEnter"]["menuHtml"]
    )
    assert payload["emptyResourcePanel"]["menuHidden"] is True
    assert "正在搜索" not in payload["emptyResourcePanel"]["menuHtml"]
    assert payload["lowerCaseMissPanel"]["menuHidden"] is True
    assert payload["mixedCaseHitPanel"]["menuHidden"] is False
    assert "Src/Relay_Teams/Media/models.py" in re.sub(
        r"<[^>]+>", "", payload["mixedCaseHitPanel"]["menuHtml"]
    )
    assert payload["caseResourceCalls"] == [
        {
            "workspaceId": "workspace-case",
            "query": "src/relay_teams/media/",
            "limit": 500,
        },
        {
            "workspaceId": "workspace-case",
            "query": "Src/Relay_Teams/Media/",
            "limit": 500,
        },
    ]
    assert payload["cachedRelayPanel"]["menuHidden"] is False
    assert "src/relay_teams/main.py" in rendered_cached_relay_text


def test_handle_send_restores_composer_when_command_resolution_aborts(
    tmp_path: Path,
) -> None:
    temp_dir = _write_multimodal_prompt_fixture(tmp_path, role_supports_image=True)
    runner = """
import { handleSend } from "./prompt.js";
import { els } from "./mockDom.mjs";
import { state } from "./mockState.mjs";

globalThis.__streamCalls = [];
globalThis.__logs = [];
globalThis.__notifications = [];
els.promptInput.value = "/opsx:propose";

await handleSend();

console.log(JSON.stringify({
    isGenerating: state.isGenerating,
    sendDisabled: els.sendBtn.disabled,
    inputDisabled: els.promptInput.disabled,
    streamCalls: globalThis.__streamCalls,
    statusHidden: els.promptInputStatus.hidden,
    statusText: els.promptInputStatus.textContent,
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
    assert payload["isGenerating"] is False
    assert payload["sendDisabled"] is False
    assert payload["inputDisabled"] is False
    assert payload["streamCalls"] == []
    assert payload["statusHidden"] is False
    assert (
        payload["statusText"] == "Cannot resolve command without an active workspace."
    )


def test_handle_send_prefers_command_alias_over_skill_alias(tmp_path: Path) -> None:
    temp_dir = _write_multimodal_prompt_fixture(tmp_path, role_supports_image=True)
    runner = """
import {
    handleSend,
    refreshRoleConfigOptions,
} from "./prompt.js";
import { els } from "./mockDom.mjs";
import { state } from "./mockState.mjs";

globalThis.__streamCalls = [];
globalThis.__logs = [];
globalThis.__notifications = [];
globalThis.__skillsResponse = [
    {
        ref: "builtin:deepresearch",
        name: "deepresearch",
        description: "Research deeply.",
        source: "builtin",
    },
];
globalThis.__resolveCommandResponse = {
    matched: true,
    expanded_prompt: "Run the project command for the topic.",
};
state.currentWorkspaceId = "workspace-1";
els.promptInput.value = "/deepresearch topic";

await refreshRoleConfigOptions({ refreshControls: false });
await handleSend();

console.log(JSON.stringify({
    resolveCalls: globalThis.__resolveCommandCalls,
    streamCalls: globalThis.__streamCalls,
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
    assert payload["resolveCalls"] == [
        {
            "workspace_id": "workspace-1",
            "raw_text": "/deepresearch topic",
            "mode": "normal",
        }
    ]
    assert len(payload["streamCalls"]) == 1
    assert payload["streamCalls"][0]["promptText"] == "/deepresearch topic"
    assert payload["streamCalls"][0]["options"]["inputParts"] == [
        {
            "kind": "text",
            "text": "Run the project command for the topic.",
        }
    ]
    assert payload["streamCalls"][0]["options"]["displayInputParts"] == [
        {
            "kind": "text",
            "text": "/deepresearch topic",
        }
    ]
    assert payload["streamCalls"][0]["options"]["skills"] == []


def test_slash_menu_shows_same_named_command_and_skill_separately(
    tmp_path: Path,
) -> None:
    temp_dir = _write_multimodal_prompt_fixture(tmp_path, role_supports_image=True)
    runner = """
import {
    handlePromptComposerInput,
    initializePromptMentionAutocomplete,
    invalidatePromptCommandsCache,
    refreshRoleConfigOptions,
} from "./prompt.js";
import { els } from "./mockDom.mjs";
import { state } from "./mockState.mjs";

globalThis.__streamCalls = [];
globalThis.__logs = [];
globalThis.__notifications = [];
globalThis.__skillsResponse = [
    {
        ref: "dedupe-probe",
        name: "dedupe-probe",
        description: "Skill probe",
        source: "project_agents",
    },
];
globalThis.__commandsResponse = {
    commands: [
        {
            name: "dedupe-probe",
            aliases: [],
            description: "Command probe",
            argument_hint: "",
            discovery_source: "project_relay_teams",
        },
    ],
};
state.currentWorkspaceId = "workspace-1";
els.promptInput.value = "/dedu";
els.promptInput.selectionStart = els.promptInput.value.length;
els.promptInput.selectionEnd = els.promptInput.value.length;

await refreshRoleConfigOptions({ refreshControls: false });
initializePromptMentionAutocomplete();
invalidatePromptCommandsCache();
handlePromptComposerInput();
await new Promise(resolve => setTimeout(resolve, 0));

console.log(JSON.stringify({
    hidden: els.promptMentionMenu.hidden,
    html: els.promptMentionMenu.innerHTML,
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
    rendered_text = re.sub(r"<[^>]+>", "", payload["html"])
    assert payload["hidden"] is False
    assert "/ 命令" in rendered_text
    assert "Skills" in rendered_text
    assert "Command probe" in rendered_text
    assert "Skill probe" in rendered_text


def test_selected_same_named_skill_does_not_resolve_as_command(
    tmp_path: Path,
) -> None:
    temp_dir = _write_multimodal_prompt_fixture(tmp_path, role_supports_image=True)
    runner = """
import {
    handlePromptComposerInput,
    handleSend,
    initializePromptMentionAutocomplete,
    invalidatePromptCommandsCache,
    refreshRoleConfigOptions,
} from "./prompt.js";
import { els } from "./mockDom.mjs";
import { state } from "./mockState.mjs";

globalThis.__streamCalls = [];
globalThis.__logs = [];
globalThis.__notifications = [];
globalThis.__skillsResponse = [
    {
        ref: "dedupe-probe",
        name: "dedupe-probe",
        description: "Skill probe",
        source: "project_agents",
    },
];
globalThis.__commandsResponse = {
    commands: [
        {
            name: "dedupe-probe",
            aliases: [],
            description: "Command probe",
            argument_hint: "",
            discovery_source: "project_relay_teams",
        },
    ],
};
globalThis.__resolveCommandResponse = {
    matched: true,
    expanded_prompt: "Command should not run.",
};
state.currentWorkspaceId = "workspace-1";
els.promptInput.value = "/dedu";
els.promptInput.selectionStart = els.promptInput.value.length;
els.promptInput.selectionEnd = els.promptInput.value.length;

await refreshRoleConfigOptions({ refreshControls: false });
initializePromptMentionAutocomplete();
invalidatePromptCommandsCache();
handlePromptComposerInput();
await new Promise(resolve => setTimeout(resolve, 0));
const skillButton = Array.from(
    els.promptMentionMenu.innerHTML.matchAll(/<button[\\s\\S]*?<\\/button>/g),
).find((match) => match[0].includes('data-kind="skill"'));
const skillMatch = skillButton[0].match(/data-index="(\\d+)"/);
els.promptMentionMenu._listeners.get("click")({
    target: { dataset: { index: skillMatch[1] } },
    preventDefault() { return undefined; },
    stopPropagation() { return undefined; },
});
els.promptInput.value = "/dedupe-probe topic";
els.promptInput.selectionStart = els.promptInput.value.length;
els.promptInput.selectionEnd = els.promptInput.value.length;
handlePromptComposerInput();
await handleSend();

console.log(JSON.stringify({
    resolveCalls: globalThis.__resolveCommandCalls || [],
    streamCalls: globalThis.__streamCalls,
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
    assert payload["resolveCalls"] == []
    assert len(payload["streamCalls"]) == 1
    assert payload["streamCalls"][0]["options"]["inputParts"] == [
        {"kind": "text", "text": "topic"}
    ]
    assert payload["streamCalls"][0]["options"]["skills"] == ["dedupe-probe"]


def test_committing_resource_mention_preserves_selected_slash_skill(
    tmp_path: Path,
) -> None:
    temp_dir = _write_multimodal_prompt_fixture(tmp_path, role_supports_image=True)
    runner = """
import {
    handlePromptComposerInput,
    handleSend,
    initializePromptMentionAutocomplete,
    invalidatePromptCommandsCache,
    refreshRoleConfigOptions,
} from "./prompt.js";
import { els } from "./mockDom.mjs";
import { state } from "./mockState.mjs";

globalThis.__streamCalls = [];
globalThis.__logs = [];
globalThis.__notifications = [];
globalThis.__skillsResponse = [
    {
        ref: "dedupe-probe",
        name: "dedupe-probe",
        description: "Skill probe",
        source: "project_agents",
    },
];
globalThis.__commandsResponse = {
    commands: [
        {
            name: "dedupe-probe",
            aliases: [],
            description: "Command probe",
            argument_hint: "",
            discovery_source: "project_relay_teams",
        },
    ],
};
globalThis.__resolveCommandResponse = {
    matched: true,
    expanded_prompt: "Command should not run.",
};
state.currentWorkspaceId = "workspace-1";
els.promptInput.value = "/dedu";
els.promptInput.selectionStart = els.promptInput.value.length;
els.promptInput.selectionEnd = els.promptInput.value.length;

await refreshRoleConfigOptions({ refreshControls: false });
initializePromptMentionAutocomplete();
invalidatePromptCommandsCache();
handlePromptComposerInput();
await new Promise(resolve => setTimeout(resolve, 0));
const skillButton = Array.from(
    els.promptMentionMenu.innerHTML.matchAll(/<button[\\s\\S]*?<\\/button>/g),
).find((match) => match[0].includes('data-kind="skill"'));
const skillMatch = skillButton[0].match(/data-index="(\\d+)"/);
els.promptMentionMenu._listeners.get("click")({
    target: { dataset: { index: skillMatch[1] } },
    preventDefault() { return undefined; },
    stopPropagation() { return undefined; },
});

globalThis.__resourceResponse = {
    workspace_id: "workspace-1",
    query: "src",
    results: [
        { name: "main.py", path: "src/relay_teams/main.py", kind: "file", mount_name: "default" },
    ],
};
els.promptInput.value = "/dedupe-probe @src";
els.promptInput.selectionStart = els.promptInput.value.length;
els.promptInput.selectionEnd = els.promptInput.value.length;
handlePromptComposerInput();
await new Promise(resolve => setTimeout(resolve, 120));
const resourceButton = Array.from(
    els.promptMentionMenu.innerHTML.matchAll(/<button[\\s\\S]*?<\\/button>/g),
)[0];
const resourceMatch = resourceButton[0].match(/data-index="(\\d+)"/);
els.promptMentionMenu._listeners.get("click")({
    target: { dataset: { index: resourceMatch[1] } },
    preventDefault() { return undefined; },
    stopPropagation() { return undefined; },
});
await handleSend();

console.log(JSON.stringify({
    resolveCalls: globalThis.__resolveCommandCalls || [],
    streamCalls: globalThis.__streamCalls,
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
    assert payload["resolveCalls"] == []
    assert len(payload["streamCalls"]) == 1
    assert payload["streamCalls"][0]["options"]["skills"] == ["dedupe-probe"]


def test_stale_selected_skill_falls_back_to_command_resolution(
    tmp_path: Path,
) -> None:
    temp_dir = _write_multimodal_prompt_fixture(tmp_path, role_supports_image=True)
    runner = """
import {
    handlePromptComposerInput,
    handleSend,
    initializePromptMentionAutocomplete,
    invalidatePromptCommandsCache,
    refreshRoleConfigOptions,
} from "./prompt.js";
import { els } from "./mockDom.mjs";
import { state } from "./mockState.mjs";

globalThis.__streamCalls = [];
globalThis.__logs = [];
globalThis.__notifications = [];
globalThis.__skillsResponse = [
    {
        ref: "dedupe-probe",
        name: "dedupe-probe",
        description: "Skill probe",
        source: "project_agents",
    },
];
globalThis.__commandsResponse = {
    commands: [
        {
            name: "dedupe-probe",
            aliases: [],
            description: "Command probe",
            argument_hint: "",
            discovery_source: "project_relay_teams",
        },
    ],
};
globalThis.__resolveCommandResponse = {
    matched: true,
    expanded_prompt: "Command ran after skill removal.",
};
state.currentWorkspaceId = "workspace-1";
els.promptInput.value = "/dedu";
els.promptInput.selectionStart = els.promptInput.value.length;
els.promptInput.selectionEnd = els.promptInput.value.length;

await refreshRoleConfigOptions({ refreshControls: false });
initializePromptMentionAutocomplete();
invalidatePromptCommandsCache();
handlePromptComposerInput();
await new Promise(resolve => setTimeout(resolve, 0));
const skillButton = Array.from(
    els.promptMentionMenu.innerHTML.matchAll(/<button[\\s\\S]*?<\\/button>/g),
).find((match) => match[0].includes('data-kind="skill"'));
const skillMatch = skillButton[0].match(/data-index="(\\d+)"/);
els.promptMentionMenu._listeners.get("click")({
    target: { dataset: { index: skillMatch[1] } },
    preventDefault() { return undefined; },
    stopPropagation() { return undefined; },
});

globalThis.__skillsResponse = [];
await refreshRoleConfigOptions({ refreshControls: false });
els.promptInput.value = "/dedupe-probe topic";
els.promptInput.selectionStart = els.promptInput.value.length;
els.promptInput.selectionEnd = els.promptInput.value.length;
handlePromptComposerInput();
await handleSend();

console.log(JSON.stringify({
    resolveCalls: globalThis.__resolveCommandCalls || [],
    streamCalls: globalThis.__streamCalls,
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
    assert payload["resolveCalls"] == [
        {
            "workspace_id": "workspace-1",
            "raw_text": "/dedupe-probe topic",
            "mode": "normal",
        }
    ]
    assert len(payload["streamCalls"]) == 1
    assert payload["streamCalls"][0]["options"]["inputParts"] == [
        {"kind": "text", "text": "Command ran after skill removal."}
    ]
    assert payload["streamCalls"][0]["options"]["skills"] == []


def test_stale_selected_command_falls_back_to_skill_resolution(
    tmp_path: Path,
) -> None:
    temp_dir = _write_multimodal_prompt_fixture(tmp_path, role_supports_image=True)
    runner = """
import {
    handlePromptComposerInput,
    handleSend,
    initializePromptMentionAutocomplete,
    invalidatePromptCommandsCache,
    refreshRoleConfigOptions,
} from "./prompt.js";
import { els } from "./mockDom.mjs";
import { state } from "./mockState.mjs";

globalThis.__streamCalls = [];
globalThis.__logs = [];
globalThis.__notifications = [];
globalThis.__skillsResponse = [
    {
        ref: "dedupe-probe",
        name: "dedupe-probe",
        description: "Skill probe",
        source: "project_agents",
    },
];
globalThis.__commandsResponse = {
    commands: [
        {
            name: "dedupe-probe",
            aliases: [],
            description: "Command probe",
            argument_hint: "",
            discovery_source: "project_relay_teams",
        },
    ],
};
globalThis.__resolveCommandResponse = {
    matched: false,
    expanded_prompt: "",
};
state.currentWorkspaceId = "workspace-1";
els.promptInput.value = "/dedu";
els.promptInput.selectionStart = els.promptInput.value.length;
els.promptInput.selectionEnd = els.promptInput.value.length;

await refreshRoleConfigOptions({ refreshControls: false });
initializePromptMentionAutocomplete();
invalidatePromptCommandsCache();
handlePromptComposerInput();
await new Promise(resolve => setTimeout(resolve, 0));
const commandButton = Array.from(
    els.promptMentionMenu.innerHTML.matchAll(/<button[\\s\\S]*?<\\/button>/g),
).find((match) => match[0].includes('data-kind="command"'));
const commandMatch = commandButton[0].match(/data-index="(\\d+)"/);
els.promptMentionMenu._listeners.get("click")({
    target: { dataset: { index: commandMatch[1] } },
    preventDefault() { return undefined; },
    stopPropagation() { return undefined; },
});

globalThis.__commandsResponse = { commands: [] };
invalidatePromptCommandsCache();
els.promptInput.value = "/dedupe-probe topic";
els.promptInput.selectionStart = els.promptInput.value.length;
els.promptInput.selectionEnd = els.promptInput.value.length;
handlePromptComposerInput();
await handleSend();

console.log(JSON.stringify({
    resolveCalls: globalThis.__resolveCommandCalls || [],
    streamCalls: globalThis.__streamCalls,
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
    assert payload["resolveCalls"] == [
        {
            "workspace_id": "workspace-1",
            "raw_text": "/dedupe-probe topic",
            "mode": "normal",
        }
    ]
    assert len(payload["streamCalls"]) == 1
    assert payload["streamCalls"][0]["options"]["inputParts"] == [
        {"kind": "text", "text": "topic"}
    ]
    assert payload["streamCalls"][0]["options"]["skills"] == ["dedupe-probe"]


def test_stale_selected_command_without_workspace_falls_back_to_skill(
    tmp_path: Path,
) -> None:
    temp_dir = _write_multimodal_prompt_fixture(tmp_path, role_supports_image=True)
    runner = """
import {
    handlePromptComposerInput,
    handleSend,
    initializePromptMentionAutocomplete,
    invalidatePromptCommandsCache,
    refreshRoleConfigOptions,
} from "./prompt.js";
import { els } from "./mockDom.mjs";
import { state } from "./mockState.mjs";

globalThis.__streamCalls = [];
globalThis.__logs = [];
globalThis.__notifications = [];
globalThis.__skillsResponse = [
    {
        ref: "dedupe-probe",
        name: "dedupe-probe",
        description: "Skill probe",
        source: "project_agents",
    },
];
globalThis.__commandsResponse = {
    commands: [
        {
            name: "dedupe-probe",
            aliases: [],
            description: "Command probe",
            argument_hint: "",
            discovery_source: "project_relay_teams",
        },
    ],
};
state.currentWorkspaceId = "workspace-1";
els.promptInput.value = "/dedu";
els.promptInput.selectionStart = els.promptInput.value.length;
els.promptInput.selectionEnd = els.promptInput.value.length;

await refreshRoleConfigOptions({ refreshControls: false });
initializePromptMentionAutocomplete();
invalidatePromptCommandsCache();
handlePromptComposerInput();
await new Promise(resolve => setTimeout(resolve, 0));
const commandButton = Array.from(
    els.promptMentionMenu.innerHTML.matchAll(/<button[\\s\\S]*?<\\/button>/g),
).find((match) => match[0].includes('data-kind="command"'));
const commandMatch = commandButton[0].match(/data-index="(\\d+)"/);
els.promptMentionMenu._listeners.get("click")({
    target: { dataset: { index: commandMatch[1] } },
    preventDefault() { return undefined; },
    stopPropagation() { return undefined; },
});

state.currentWorkspaceId = "";
globalThis.__commandsResponse = { commands: [] };
invalidatePromptCommandsCache();
els.promptInput.value = "/dedupe-probe topic";
els.promptInput.selectionStart = els.promptInput.value.length;
els.promptInput.selectionEnd = els.promptInput.value.length;
handlePromptComposerInput();
await handleSend();

console.log(JSON.stringify({
    logs: globalThis.__logs,
    resolveCalls: globalThis.__resolveCommandCalls || [],
    streamCalls: globalThis.__streamCalls,
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
    assert all(
        log["message"] != "Cannot resolve command without an active workspace."
        for log in payload["logs"]
    )
    assert payload["resolveCalls"] == []
    assert len(payload["streamCalls"]) == 1
    assert payload["streamCalls"][0]["options"]["inputParts"] == [
        {"kind": "text", "text": "topic"}
    ]
    assert payload["streamCalls"][0]["options"]["skills"] == ["dedupe-probe"]


def test_selected_same_named_command_does_not_submit_skill(
    tmp_path: Path,
) -> None:
    temp_dir = _write_multimodal_prompt_fixture(tmp_path, role_supports_image=True)
    runner = """
import {
    handlePromptComposerInput,
    handleSend,
    initializePromptMentionAutocomplete,
    invalidatePromptCommandsCache,
    refreshRoleConfigOptions,
} from "./prompt.js";
import { els } from "./mockDom.mjs";
import { state } from "./mockState.mjs";

globalThis.__streamCalls = [];
globalThis.__logs = [];
globalThis.__notifications = [];
globalThis.__skillsResponse = [
    {
        ref: "dedupe-probe",
        name: "dedupe-probe",
        description: "Skill probe",
        source: "project_agents",
    },
];
globalThis.__commandsResponse = {
    commands: [
        {
            name: "dedupe-probe",
            aliases: [],
            description: "Command probe",
            argument_hint: "",
            discovery_source: "project_relay_teams",
        },
    ],
};
globalThis.__resolveCommandResponse = {
    matched: true,
    expanded_prompt: "Command ran.",
};
state.currentWorkspaceId = "workspace-1";
els.promptInput.value = "/dedu";
els.promptInput.selectionStart = els.promptInput.value.length;
els.promptInput.selectionEnd = els.promptInput.value.length;

await refreshRoleConfigOptions({ refreshControls: false });
initializePromptMentionAutocomplete();
invalidatePromptCommandsCache();
handlePromptComposerInput();
await new Promise(resolve => setTimeout(resolve, 0));
const commandButton = Array.from(
    els.promptMentionMenu.innerHTML.matchAll(/<button[\\s\\S]*?<\\/button>/g),
).find((match) => match[0].includes('data-kind="command"'));
const commandMatch = commandButton[0].match(/data-index="(\\d+)"/);
els.promptMentionMenu._listeners.get("click")({
    target: { dataset: { index: commandMatch[1] } },
    preventDefault() { return undefined; },
    stopPropagation() { return undefined; },
});
els.promptInput.value = "/dedupe-probe topic";
els.promptInput.selectionStart = els.promptInput.value.length;
els.promptInput.selectionEnd = els.promptInput.value.length;
handlePromptComposerInput();
await handleSend();

console.log(JSON.stringify({
    resolveCalls: globalThis.__resolveCommandCalls || [],
    streamCalls: globalThis.__streamCalls,
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
    assert payload["resolveCalls"] == [
        {
            "workspace_id": "workspace-1",
            "raw_text": "/dedupe-probe topic",
            "mode": "normal",
        }
    ]
    assert len(payload["streamCalls"]) == 1
    assert payload["streamCalls"][0]["options"]["inputParts"] == [
        {"kind": "text", "text": "Command ran."}
    ]
    assert payload["streamCalls"][0]["options"]["skills"] == []


def test_handle_send_does_not_parse_inline_slash_prose_as_action(
    tmp_path: Path,
) -> None:
    temp_dir = _write_multimodal_prompt_fixture(tmp_path, role_supports_image=True)
    runner = """
import {
    handleSend,
    refreshRoleConfigOptions,
} from "./prompt.js";
import { els } from "./mockDom.mjs";
import { state } from "./mockState.mjs";

globalThis.__streamCalls = [];
globalThis.__logs = [];
globalThis.__notifications = [];
globalThis.__skillsResponse = [
    {
        ref: "builtin:time",
        name: "time",
        description: "Get the current time.",
        source: "builtin",
    },
];
globalThis.__resolveCommandResponse = {
    matched: true,
    expanded_prompt: "This should not be used.",
};
state.currentWorkspaceId = "workspace-1";
els.promptInput.value = "Please explain /time complexity";

await refreshRoleConfigOptions({ refreshControls: false });
await handleSend();

console.log(JSON.stringify({
    resolveCalls: globalThis.__resolveCommandCalls || [],
    streamCalls: globalThis.__streamCalls,
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
    assert payload["resolveCalls"] == []
    assert len(payload["streamCalls"]) == 1
    assert payload["streamCalls"][0]["options"]["inputParts"] == [
        {
            "kind": "text",
            "text": "Please explain /time complexity",
        }
    ]
    assert payload["streamCalls"][0]["options"]["skills"] == []


def test_handle_send_sends_pasted_image_as_inline_media_for_multimodal_role(
    tmp_path: Path,
) -> None:
    temp_dir = _write_multimodal_prompt_fixture(tmp_path, role_supports_image=True)
    runner = """
import {
    handlePromptComposerPaste,
    handleSend,
} from "./prompt.js";
import { els } from "./mockDom.mjs";

globalThis.__streamCalls = [];
globalThis.__logs = [];
globalThis.__notifications = [];
globalThis.__pastePrevented = false;
globalThis.FileReader = class {
    constructor() {
        this.result = null;
        this.onload = null;
        this.onerror = null;
        this.error = null;
    }
    readAsDataURL(file) {
        this.result = file.__dataUrl;
        this.onload?.();
    }
};

await handlePromptComposerPaste({
    clipboardData: {
        items: [
            {
                type: "image/png",
                getAsFile() {
                    return {
                        name: "diagram.png",
                        size: 4,
                        __dataUrl: "data:image/png;base64,QUJDRA==",
                    };
                },
            },
        ],
    },
    preventDefault() {
        globalThis.__pastePrevented = true;
    },
});

await handleSend();

console.log(JSON.stringify({
    pastePrevented: globalThis.__pastePrevented,
    streamCalls: globalThis.__streamCalls,
    logs: globalThis.__logs,
    attachmentHtmlAfterSend: els.promptAttachments.innerHTML,
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
    assert len(payload["streamCalls"]) == 1
    assert payload["streamCalls"][0]["promptText"] == "[image]"
    assert payload["streamCalls"][0]["options"]["inputParts"] == [
        {
            "kind": "inline_media",
            "modality": "image",
            "mime_type": "image/png",
            "base64_data": "QUJDRA==",
            "name": "diagram.png",
            "size_bytes": 4,
            "width": None,
            "height": None,
        }
    ]
    assert (
        'data-image-preview-trigger="true"' in payload["attachmentHtmlAfterSend"]
        or payload["attachmentHtmlAfterSend"] == ""
    )
    assert payload["attachmentHtmlAfterSend"] == ""


def test_handle_send_blocks_pasted_image_for_text_only_role(tmp_path: Path) -> None:
    temp_dir = _write_multimodal_prompt_fixture(tmp_path, role_supports_image=False)
    runner = """
import {
    handlePromptComposerPaste,
    handleSend,
} from "./prompt.js";
import { els } from "./mockDom.mjs";

globalThis.__streamCalls = [];
globalThis.__logs = [];
globalThis.__notifications = [];
globalThis.__pastePrevented = false;
globalThis.FileReader = class {
    constructor() {
        this.result = null;
        this.onload = null;
        this.onerror = null;
        this.error = null;
    }
    readAsDataURL(file) {
        this.result = file.__dataUrl;
        this.onload?.();
    }
};

await handlePromptComposerPaste({
    clipboardData: {
        items: [
            {
                type: "image/png",
                getAsFile() {
                    return {
                        name: "diagram.png",
                        size: 4,
                        __dataUrl: "data:image/png;base64,QUJDRA==",
                    };
                },
            },
        ],
    },
    preventDefault() {
        globalThis.__pastePrevented = true;
    },
});

await handleSend();

console.log(JSON.stringify({
    streamCalls: globalThis.__streamCalls,
    logs: globalThis.__logs,
    notifications: globalThis.__notifications,
    attachmentHtml: els.promptAttachments.innerHTML,
    attachmentClassName: els.promptAttachments.className,
    promptStatusText: els.promptInputStatus.textContent,
    promptStatusHidden: els.promptInputStatus.hidden,
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
    assert payload["streamCalls"] == []
    assert payload["notifications"] == [
        {
            "title": "Send Blocked",
            "message": "gpt-4.1-mini is currently configured as not supporting image input. Remove the image, or go to Settings > Model and set Image Input to Supports image input for this model.",
            "tone": "warning",
        }
    ]
    assert any(
        "is currently configured as not supporting image input" in entry["message"]
        for entry in payload["logs"]
    )
    assert "prompt-attachment" in payload["attachmentHtml"]
    assert 'data-image-preview-trigger="true"' in payload["attachmentHtml"]
    assert 'role="button"' in payload["attachmentHtml"]
    assert "is-error" in payload["attachmentClassName"]
    assert payload["promptStatusText"] == (
        "gpt-4.1-mini is currently configured as not supporting image input. Remove the image, or go to Settings > Model and set Image Input to Supports image input for this model."
    )
    assert payload["promptStatusHidden"] is False


def test_handle_send_blocks_pasted_image_when_image_support_is_unknown(
    tmp_path: Path,
) -> None:
    temp_dir = _write_multimodal_prompt_fixture(tmp_path, role_supports_image=None)
    runner = """
import {
    handlePromptComposerPaste,
    handleSend,
} from "./prompt.js";
import { els } from "./mockDom.mjs";

globalThis.__streamCalls = [];
globalThis.__logs = [];
globalThis.__notifications = [];

class FakeFileReader {
  readAsDataURL(file) {
    this.result = file.__dataUrl;
    this.onload?.();
  }
}

globalThis.FileReader = FakeFileReader;

const fakeFile = {
  name: "diagram.png",
  size: 2048,
  type: "image/png",
  __dataUrl: "data:image/png;base64,QUJDRA==",
};

await handlePromptComposerPaste({
  preventDefault() {
    return undefined;
  },
  clipboardData: {
    items: [{
      type: "image/png",
      getAsFile() {
        return fakeFile;
      },
    }],
  },
});

await handleSend();

console.log(JSON.stringify({
  streamCalls: globalThis.__streamCalls,
  logs: globalThis.__logs,
  notifications: globalThis.__notifications,
  promptStatusText: els.promptInputStatus.textContent,
  promptStatusHidden: els.promptInputStatus.hidden,
}));
""".strip()
    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=temp_dir,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(result.stdout)

    assert payload["streamCalls"] == []
    assert payload["notifications"] == [
        {
            "title": "Send Blocked",
            "message": "Cannot confirm whether gpt-4.1-mini supports image input. Remove the image, or go to Settings > Model and set Image Input to Supports image input for this model.",
            "tone": "warning",
        }
    ]
    assert any(
        "Cannot confirm whether gpt-4.1-mini supports image input." in entry["message"]
        for entry in payload["logs"]
    )
    assert payload["promptStatusText"] == (
        "Cannot confirm whether gpt-4.1-mini supports image input. Remove the image, or go to Settings > Model and set Image Input to Supports image input for this model."
    )
    assert payload["promptStatusHidden"] is False


def _write_new_session_draft_mock(tmp_path: Path) -> None:
    components_dir = tmp_path / "components"
    components_dir.mkdir(exist_ok=True)
    core_dir = tmp_path / "core"
    core_dir.mkdir(exist_ok=True)
    (components_dir / "newSessionDraft.js").write_text(
        """
export function applyDraftSessionTopology() {
    return undefined;
}

export async function ensureSessionForNewSessionDraft() {
    return "";
}

export function isNewSessionDraftActive() {
    return false;
}
""".strip(),
        encoding="utf-8",
    )
    (core_dir / "submission.js").write_text(
        """
export function beginForegroundSubmission() {
    return { detached: false };
}

export function finishForegroundSubmission() {
    return undefined;
}

export function isForegroundSubmissionActive(submission) {
    return submission?.detached !== true;
}
""".strip(),
        encoding="utf-8",
    )


def _write_multimodal_prompt_fixture(
    tmp_path: Path,
    *,
    role_supports_image: bool | None,
) -> Path:
    source = Path("frontend/dist/js/app/prompt.js").read_text(encoding="utf-8")
    temp_dir = tmp_path / (
        "prompt_multimodal_supported"
        if role_supports_image is True
        else "prompt_multimodal_unknown"
        if role_supports_image is None
        else "prompt_multimodal_blocked"
    )
    temp_dir.mkdir()
    _write_new_session_draft_mock(tmp_path)
    (temp_dir / "prompt.js").write_text(
        source.replace("../components/rounds/timeline.js", "./mockRounds.mjs")
        .replace("../components/rounds.js", "./mockRounds.mjs")
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
        skills: globalThis.__skillsResponse || [],
        coordinator_role: {
            role_id: "Coordinator",
            name: "Coordinator",
            description: "",
            model_profile: "default",
            input_modalities: [],
        },
        main_agent_role: {
            role_id: "MainAgent",
            name: "Main Agent",
            description: "",
            model_profile: "default",
            model_name: "gpt-4.1-mini",
            input_modalities: ["image"],
        },
        normal_mode_roles: [
            {
                role_id: "MainAgent",
                name: "Main Agent",
                description: "",
                model_profile: "default",
                model_name: "gpt-4.1-mini",
                input_modalities: ["image"],
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

export async function fetchCommands() {
    return globalThis.__commandsResponse || { commands: [] };
}

export async function resolveCommandPrompt(payload) {
    globalThis.__resolveCommandCalls = [
        ...(globalThis.__resolveCommandCalls || []),
        payload,
    ];
    if (globalThis.__resolveCommandResponse) {
        return globalThis.__resolveCommandResponse;
    }
    return {
        matched: false,
        expanded_prompt: String(payload?.raw_text || ""),
    };
}

export async function searchWorkspacePaths(workspaceId, query, limit) {
    globalThis.__searchWorkspacePathCalls = [
        ...(globalThis.__searchWorkspacePathCalls || []),
        { workspaceId, query, limit },
    ];
    return globalThis.__resourceResponse || {
        workspace_id: "workspace-1",
        query: "",
        results: [],
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
        f"""
export const state = {{
    currentSessionId: "session-1",
    currentSessionMode: "normal",
    currentSessionCanSwitchMode: true,
    currentNormalRootRoleId: "MainAgent",
    currentOrchestrationPresetId: null,
    pausedSubagent: null,
    isGenerating: false,
    yolo: true,
    thinking: {{ enabled: false, effort: "medium" }},
    instanceRoleMap: {{}},
    roleInstanceMap: {{}},
    taskInstanceMap: {{}},
    activeAgentRoleId: null,
    activeAgentInstanceId: null,
    autoSwitchedSubagentInstances: {{}},
    activeRunId: null,
}};

let normalModeRoles = [
    {{
        role_id: "MainAgent",
        name: "Main Agent",
        description: "",
        model_profile: "default",
        model_name: "gpt-4.1-mini",
        input_modalities: {json.dumps(["image"] if role_supports_image is True else [])},
    }},
];

export function applyCurrentSessionRecord() {{
    return undefined;
}}

export function getCoordinatorRoleId() {{
    return "Coordinator";
}}

export function getMainAgentRoleId() {{
    return "MainAgent";
}}

export function getNormalModeRoles() {{
    return normalModeRoles;
}}

export function getPrimaryRoleId() {{
    return "MainAgent";
}}

export function getRoleOption(roleId) {{
    return normalModeRoles.find(role => role.role_id === roleId) || null;
}}

export function getRoleDisplayName(roleId, {{ fallback = "Agent" }} = {{}}) {{
    if (roleId === "MainAgent") {{
        return "Main Agent";
    }}
    return fallback;
}}

export function setCoordinatorRoleId() {{
    return undefined;
}}

export function setCoordinatorRoleOption() {{
    return undefined;
}}

export function setMainAgentRoleId() {{
    return undefined;
}}

export function setMainAgentRoleOption() {{
    return undefined;
}}

export function setNormalModeRoles(roleOptions) {{
    normalModeRoles = Array.isArray(roleOptions) ? roleOptions : [];
}}

export function roleSupportsInputModality(roleId, modality) {{
    return (
        String(roleId || "") === "MainAgent"
        && String(modality || "") === "image"
        && {str(role_supports_image is True).lower()}
    );
}}

export function getRoleInputModalitySupport(roleId, modality) {{
    if (String(roleId || "") !== "MainAgent" || String(modality || "") !== "image") {{
        return null;
    }}
    return {json.dumps(role_supports_image)};
}}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockStream.mjs").write_text(
        """
export async function startIntentStream(promptText, sessionId, onCompleted, options = {}) {
    globalThis.__streamCalls.push({
        promptText,
        sessionId,
        options,
    });
    return onCompleted;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockDom.mjs").write_text(
        """
function createElement(initial = {}) {
    const element = {
        value: "",
        checked: false,
        disabled: false,
        hidden: false,
        textContent: "",
        innerHTML: "",
        title: "",
        className: "",
        selectionStart: 0,
        selectionEnd: 0,
        scrollHeight: 36,
        style: { display: "", height: "" },
        _listeners: new Map(),
        querySelectorAll() { return []; },
        addEventListener(type, listener) {
            this._listeners.set(type, listener);
        },
        focus() { return undefined; },
        ...initial,
    };
    element.classList = {
        toggle(name, enabled) {
            const tokens = new Set(String(element.className || "").split(/\\s+/).filter(Boolean));
            const shouldEnable = enabled !== false;
            if (shouldEnable) {
                tokens.add(name);
            } else {
                tokens.delete(name);
            }
            element.className = Array.from(tokens).join(" ");
            return shouldEnable;
        },
    };
    return element;
}

export const els = {
    promptInput: createElement({ value: "" }),
    promptAttachments: createElement(),
    promptMentionMenu: createElement({ hidden: true }),
    promptInputStatus: createElement({ hidden: true }),
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
    globalThis.__notifications.push(arguments[0]);
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    (temp_dir / "mockI18n.mjs").write_text(
        """
const translations = {
    "composer.error.image_input_unsupported": "{agent} is currently configured as not supporting image input. Remove the image, or go to Settings > Model and set Image Input to Supports image input for this model.",
    "composer.error.image_input_unknown": "Cannot confirm whether {agent} supports image input. Remove the image, or go to Settings > Model and set Image Input to Supports image input for this model.",
    "composer.toast.send_blocked_title": "Send Blocked",
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
    return temp_dir
