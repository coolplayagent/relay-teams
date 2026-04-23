// js/state.js

export const state = {
    currentSessionId: null,
    currentWorkspaceId: null,
    currentSessionMode: 'normal',
    currentNormalRootRoleId: null,
    currentOrchestrationPresetId: null,
    currentSessionCanSwitchMode: false,
    currentMainView: 'session',
    currentProjectViewWorkspaceId: null,
    currentFeatureViewId: null,
    pendingNewSessionActive: false,
    pendingNewSessionWorkspaceId: null,
    activeSubagentSession: null,
    isGenerating: false,
    activeEventSource: null,
    agentViews: {},
    activeView: 'main',
    activeAgentRoleId: null,
    activeAgentInstanceId: null,
    activeRunId: null,
    runPrimaryRoleMap: {},
    pausedSubagent: null,
    instanceRoleMap: {}, // instanceId -> roleId, built from model_step_started SSE events
    roleInstanceMap: {}, // roleId -> latest instanceId
    taskInstanceMap: {}, // taskId -> instanceId
    taskStatusMap: {}, // taskId -> task status
    autoSwitchedSubagentInstances: {}, // instanceId -> true, auto-opened once per run
    currentRecoverySnapshot: null,
    sessionAgents: [],
    sessionTasks: [],
    yolo: true,
    thinking: {
        enabled: false,
        effort: 'medium',
    },
    normalModeRoles: [],
    coordinatorRole: null,
    mainAgentRole: null,
    selectedRoleId: null,
    coordinatorRoleId: null,
    mainAgentRoleId: null,
    rightRailExpanded: true,
};

export function setCoordinatorRoleId(roleId) {
    state.coordinatorRoleId = normalizeRoleId(roleId) || null;
}

export function getCoordinatorRoleId() {
    return normalizeRoleId(state.coordinatorRoleId);
}

export function setCoordinatorRoleOption(roleOption) {
    state.coordinatorRole = normalizeRoleOption(roleOption);
}

export function getCoordinatorRoleOption() {
    return normalizeRoleOption(state.coordinatorRole);
}

export function setMainAgentRoleId(roleId) {
    state.mainAgentRoleId = normalizeRoleId(roleId) || null;
}

export function getMainAgentRoleId() {
    return normalizeRoleId(state.mainAgentRoleId);
}

export function setMainAgentRoleOption(roleOption) {
    state.mainAgentRole = normalizeRoleOption(roleOption);
}

export function getMainAgentRoleOption() {
    return normalizeRoleOption(state.mainAgentRole);
}

export function setNormalModeRoles(roleOptions) {
    const rows = Array.isArray(roleOptions) ? roleOptions : [];
    state.normalModeRoles = rows
        .map(item => normalizeRoleOption(item))
        .filter(item => item.role_id);
}

export function getNormalModeRoles() {
    return Array.isArray(state.normalModeRoles) ? state.normalModeRoles : [];
}

export function getRoleOption(roleId) {
    const safeRoleId = normalizeRoleId(roleId);
    if (!safeRoleId) {
        return null;
    }
    const coordinatorRole = getCoordinatorRoleOption();
    if (coordinatorRole?.role_id === safeRoleId) {
        return coordinatorRole;
    }
    const mainAgentRole = getMainAgentRoleOption();
    if (mainAgentRole?.role_id === safeRoleId) {
        return mainAgentRole;
    }
    return getNormalModeRoles().find(role => role.role_id === safeRoleId) || null;
}

export function roleSupportsInputModality(roleId, modality) {
    return getRoleInputModalitySupport(roleId, modality) === true;
}

export function getRoleInputModalitySupport(roleId, modality) {
    const safeModality = String(modality || '').trim().toLowerCase();
    if (!safeModality) {
        return null;
    }
    const role = getRoleOption(roleId);
    if (!role) {
        return null;
    }
    const capabilitySupport = resolveCapabilitySupport(role.capabilities?.input, safeModality);
    if (capabilitySupport !== null) {
        return capabilitySupport;
    }
    return Array.isArray(role.input_modalities)
        ? role.input_modalities.includes(safeModality)
        : null;
}

export function isCoordinatorRoleId(roleId) {
    const safeRoleId = normalizeRoleId(roleId);
    if (!safeRoleId) {
        return false;
    }
    return safeRoleId === getCoordinatorRoleId();
}

export function isMainAgentRoleId(roleId) {
    const safeRoleId = normalizeRoleId(roleId);
    if (!safeRoleId) {
        return false;
    }
    return safeRoleId === getMainAgentRoleId();
}

export function isReservedSystemRoleId(roleId) {
    return isCoordinatorRoleId(roleId) || isMainAgentRoleId(roleId);
}

export function getPrimaryRoleId(sessionMode = state.currentSessionMode) {
    return sessionMode === 'orchestration'
        ? getCoordinatorRoleId()
        : (normalizeRoleId(state.currentNormalRootRoleId) || getMainAgentRoleId());
}

export function getPrimaryRoleLabel(sessionMode = state.currentSessionMode) {
    return getRoleDisplayName(getPrimaryRoleId(sessionMode), {
        fallback: sessionMode === 'orchestration' ? 'Coordinator' : 'Main Agent',
    });
}

export function isPrimaryRoleId(roleId, sessionMode = state.currentSessionMode) {
    const safeRoleId = normalizeRoleId(roleId);
    if (!safeRoleId) {
        return false;
    }
    return safeRoleId === getPrimaryRoleId(sessionMode);
}

export function setRunPrimaryRole(runId, roleId) {
    const safeRunId = String(runId || '').trim();
    const safeRoleId = normalizeRoleId(roleId);
    if (!safeRunId) {
        return;
    }
    if (!safeRoleId) {
        delete state.runPrimaryRoleMap[safeRunId];
        return;
    }
    state.runPrimaryRoleMap[safeRunId] = safeRoleId;
}

export function clearRunPrimaryRole(runId) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) {
        return;
    }
    delete state.runPrimaryRoleMap[safeRunId];
}

export function getRunPrimaryRoleId(runId, sessionMode = state.currentSessionMode) {
    const safeRunId = String(runId || '').trim();
    const mappedRoleId = safeRunId ? normalizeRoleId(state.runPrimaryRoleMap[safeRunId]) : '';
    if (mappedRoleId) {
        return mappedRoleId;
    }
    return getPrimaryRoleId(sessionMode);
}

export function getRunPrimaryRoleLabel(runId, sessionMode = state.currentSessionMode) {
    return getRoleDisplayName(getRunPrimaryRoleId(runId, sessionMode), {
        fallback: getPrimaryRoleLabel(sessionMode),
    });
}

export function isRunPrimaryRoleId(roleId, runId, sessionMode = state.currentSessionMode) {
    const safeRoleId = normalizeRoleId(roleId);
    if (!safeRoleId) {
        return false;
    }
    return safeRoleId === getRunPrimaryRoleId(runId, sessionMode);
}

export function isPrimaryOrReservedRoleId(roleId, sessionMode = state.currentSessionMode) {
    return isPrimaryRoleId(roleId, sessionMode) || isReservedSystemRoleId(roleId);
}

export function applyCurrentSessionRecord(record) {
    state.currentSessionMode = normalizeSessionMode(record?.session_mode);
    state.currentNormalRootRoleId = normalizeRoleId(record?.normal_root_role_id) || null;
    state.currentOrchestrationPresetId = normalizeRoleId(record?.orchestration_preset_id) || null;
    state.currentSessionCanSwitchMode = record?.can_switch_mode === true;
    state.currentMainView = 'session';
    state.currentProjectViewWorkspaceId = null;
    state.currentFeatureViewId = null;
}

export function resetCurrentSessionTopology() {
    state.currentSessionMode = 'normal';
    state.currentNormalRootRoleId = null;
    state.currentOrchestrationPresetId = null;
    state.currentSessionCanSwitchMode = false;
}

export function getActiveSubagentSession() {
    return state.activeSubagentSession
        && typeof state.activeSubagentSession === 'object'
        ? state.activeSubagentSession
        : null;
}

export function isViewingSubagentSession() {
    return !!getActiveSubagentSession();
}

export function getRoleDisplayName(roleId, { fallback = 'Agent' } = {}) {
    const safeRoleId = normalizeRoleId(roleId);
    if (!safeRoleId) {
        return fallback;
    }
    if (isCoordinatorRoleId(safeRoleId)) {
        return 'Coordinator';
    }
    if (isMainAgentRoleId(safeRoleId)) {
        return 'Main Agent';
    }
    const matchingRole = getRoleOption(safeRoleId);
    if (matchingRole && matchingRole.name) {
        return matchingRole.name;
    }
    return safeRoleId
        .split(/[_\\s-]+/)
        .filter(Boolean)
        .map(part => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ') || fallback;
}

export function humanizeRoleId(roleId, { coordinatorLabel = 'Coordinator', fallback = 'Agent' } = {}) {
    const safeRoleId = normalizeRoleId(roleId);
    if (!safeRoleId) {
        return fallback;
    }
    if (isCoordinatorRoleId(safeRoleId)) {
        return coordinatorLabel;
    }
    if (isMainAgentRoleId(safeRoleId)) {
        return 'Main Agent';
    }
    return getRoleDisplayName(safeRoleId, { fallback });
}

function normalizeRoleId(roleId) {
    return String(roleId || '').trim();
}

function normalizeRoleOption(roleOption) {
    if (!roleOption || typeof roleOption !== 'object') {
        return null;
    }
    const role_id = normalizeRoleId(roleOption?.role_id);
    if (!role_id) {
        return null;
    }
    return {
        role_id,
        name: String(roleOption?.name || '').trim(),
        description: String(roleOption?.description || '').trim(),
        model_profile: String(roleOption?.model_profile || '').trim(),
        model_name: String(roleOption?.model_name || '').trim(),
        capabilities: normalizeModelCapabilities(
            roleOption?.capabilities,
            roleOption?.input_modalities,
        ),
        input_modalities: Array.isArray(roleOption?.input_modalities)
            ? roleOption.input_modalities
                .map(item => String(item || '').trim().toLowerCase())
                .filter(Boolean)
            : [],
    };
}

function normalizeSessionMode(value) {
    return String(value || '').trim().toLowerCase() === 'orchestration'
        ? 'orchestration'
        : 'normal';
}

function normalizeModelCapabilities(capabilities, inputModalities) {
    const normalizedInput = normalizeCapabilityMatrix(capabilities?.input);
    const normalizedOutput = normalizeCapabilityMatrix(capabilities?.output);
    const normalizedInputModalities = Array.isArray(inputModalities)
        ? inputModalities
            .map(item => String(item || '').trim().toLowerCase())
            .filter(Boolean)
        : [];
    if (normalizedInput.image === null && normalizedInputModalities.includes('image')) {
        normalizedInput.image = true;
    }
    if (normalizedInput.audio === null && normalizedInputModalities.includes('audio')) {
        normalizedInput.audio = true;
    }
    if (normalizedInput.video === null && normalizedInputModalities.includes('video')) {
        normalizedInput.video = true;
    }
    if (normalizedInput.text === null) {
        normalizedInput.text = true;
    }
    if (normalizedOutput.text === null) {
        normalizedOutput.text = true;
    }
    return {
        input: normalizedInput,
        output: normalizedOutput,
    };
}

function normalizeCapabilityMatrix(matrix) {
    return {
        text: normalizeOptionalCapabilityFlag(matrix?.text),
        image: normalizeOptionalCapabilityFlag(matrix?.image),
        audio: normalizeOptionalCapabilityFlag(matrix?.audio),
        video: normalizeOptionalCapabilityFlag(matrix?.video),
        pdf: normalizeOptionalCapabilityFlag(matrix?.pdf),
    };
}

function normalizeOptionalCapabilityFlag(value) {
    if (value === true) {
        return true;
    }
    if (value === false) {
        return false;
    }
    return null;
}

function resolveCapabilitySupport(capabilityMatrix, modality) {
    if (!capabilityMatrix || typeof capabilityMatrix !== 'object') {
        return null;
    }
    if (!(modality in capabilityMatrix)) {
        return null;
    }
    return normalizeOptionalCapabilityFlag(capabilityMatrix[modality]);
}

export const els = {
    newProjectBtn: document.getElementById('new-project-btn'),
    projectsList: document.getElementById('projects-list'),
    chatMessages: document.getElementById('chat-messages'),
    chatForm: document.getElementById('chat-form'),
    promptInput: document.getElementById('prompt-input'),
    sendBtn: document.getElementById('send-btn'),
    stopBtn: document.getElementById('stop-btn'),
    systemLogs: document.getElementById('system-logs'),
    toggleInspector: document.getElementById('toggle-inspector'),
    inspectorPanel: document.getElementById('rail-inspector'),
    toggleSidebar: document.getElementById('toggle-sidebar'),
    sidebar: document.querySelector('.sidebar'),
    toggleSubagents: document.getElementById('toggle-subagents'),
    rightRail: document.getElementById('right-rail'),
    rightRailResizer: document.getElementById('right-rail-resizer'),
};
