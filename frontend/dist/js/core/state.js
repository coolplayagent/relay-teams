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
    selectedRoleId: null,
    coordinatorRoleId: null,
    coordinatorRoleOption: null,
    mainAgentRoleId: null,
};

export function setCoordinatorRoleId(roleId) {
    state.coordinatorRoleId = normalizeRoleId(roleId) || null;
}

export function getCoordinatorRoleId() {
    return normalizeRoleId(state.coordinatorRoleId);
}

export function setCoordinatorRoleOption(roleOption) {
    const normalizedRole = normalizeRoleOption(roleOption);
    const coordinatorRoleId = getCoordinatorRoleId();
    if (!normalizedRole) {
        state.coordinatorRoleOption = null;
        return;
    }
    if (coordinatorRoleId && normalizedRole.role_id !== coordinatorRoleId) {
        state.coordinatorRoleOption = null;
        return;
    }
    state.coordinatorRoleOption = normalizedRole;
}

export function setMainAgentRoleId(roleId) {
    state.mainAgentRoleId = normalizeRoleId(roleId) || null;
}

export function getMainAgentRoleId() {
    return normalizeRoleId(state.mainAgentRoleId);
}

export function setNormalModeRoles(roleOptions) {
    const rows = Array.isArray(roleOptions) ? roleOptions : [];
    state.normalModeRoles = rows
        .map(item => ({
            role_id: normalizeRoleId(item?.role_id),
            name: String(item?.name || '').trim(),
            description: String(item?.description || '').trim(),
            model_profile: normalizeOptionalText(item?.model_profile),
            model_name: normalizeOptionalText(item?.model_name),
            capabilities: normalizeModelCapabilities(item?.capabilities),
        }))
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
    const coordinatorRole = state.coordinatorRoleOption;
    if (coordinatorRole?.role_id === safeRoleId) {
        return coordinatorRole;
    }
    return getNormalModeRoles().find(role => role.role_id === safeRoleId) || null;
}

export function getRoleModelName(roleId, { fallback = '' } = {}) {
    const role = getRoleOption(roleId);
    if (role?.model_name) {
        return role.model_name;
    }
    if (role?.model_profile) {
        return role.model_profile;
    }
    return String(fallback || '').trim();
}

export function getRoleInputModalitySupport(roleId, modality) {
    const safeModality = String(modality || '').trim().toLowerCase();
    if (!safeModality) {
        return null;
    }
    const role = getRoleOption(roleId);
    const inputCapabilities = role?.capabilities?.input;
    if (!inputCapabilities || !(safeModality in inputCapabilities)) {
        return null;
    }
    const value = inputCapabilities[safeModality];
    return value === true ? true : (value === false ? false : null);
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
    const matchingRole = getNormalModeRoles().find(role => role.role_id === safeRoleId);
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

function normalizeSessionMode(value) {
    return String(value || '').trim().toLowerCase() === 'orchestration'
        ? 'orchestration'
        : 'normal';
}

function normalizeOptionalText(value) {
    const normalized = String(value || '').trim();
    return normalized || null;
}

function normalizeRoleOption(roleOption) {
    if (!roleOption || typeof roleOption !== 'object') {
        return null;
    }
    const roleId = normalizeRoleId(roleOption.role_id);
    if (!roleId) {
        return null;
    }
    return {
        role_id: roleId,
        name: String(roleOption.name || '').trim(),
        description: String(roleOption.description || '').trim(),
        model_profile: normalizeOptionalText(roleOption.model_profile),
        model_name: normalizeOptionalText(roleOption.model_name),
        capabilities: normalizeModelCapabilities(roleOption.capabilities),
    };
}

function normalizeModelCapabilities(capabilities) {
    if (!capabilities || typeof capabilities !== 'object') {
        return null;
    }
    const input = capabilities.input && typeof capabilities.input === 'object'
        ? {
            text: normalizeOptionalBoolean(capabilities.input.text),
            image: normalizeOptionalBoolean(capabilities.input.image),
        }
        : {};
    const output = capabilities.output && typeof capabilities.output === 'object'
        ? {
            text: normalizeOptionalBoolean(capabilities.output.text),
        }
        : {};
    if (Object.keys(input).length === 0 && Object.keys(output).length === 0) {
        return null;
    }
    return {
        input,
        output,
    };
}

function normalizeOptionalBoolean(value) {
    if (value === true) {
        return true;
    }
    if (value === false) {
        return false;
    }
    return null;
}

export const els = {
    newProjectBtn: document.getElementById('new-project-btn'),
    projectsList: document.getElementById('projects-list'),
    chatMessages: document.getElementById('chat-messages'),
    chatForm: document.getElementById('chat-form'),
    promptInput: document.getElementById('prompt-input'),
    sendBtn: document.getElementById('send-btn'),
    stopBtn: document.getElementById('stop-btn'),
    toggleSidebar: document.getElementById('toggle-sidebar'),
    sidebar: document.querySelector('.sidebar'),
    toggleSubagents: document.getElementById('toggle-subagents'),
};
