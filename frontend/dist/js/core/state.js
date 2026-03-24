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
    isGenerating: false,
    activeEventSource: null,
    agentViews: {},
    activeView: 'main',
    activeAgentRoleId: null,
    activeAgentInstanceId: null,
    activeRunId: null,
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
    mainAgentRoleId: null,
    rightRailExpanded: true,
};

export function setCoordinatorRoleId(roleId) {
    state.coordinatorRoleId = normalizeRoleId(roleId) || null;
}

export function getCoordinatorRoleId() {
    return normalizeRoleId(state.coordinatorRoleId);
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
        }))
        .filter(item => item.role_id);
}

export function getNormalModeRoles() {
    return Array.isArray(state.normalModeRoles) ? state.normalModeRoles : [];
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
}

export function resetCurrentSessionTopology() {
    state.currentSessionMode = 'normal';
    state.currentNormalRootRoleId = null;
    state.currentOrchestrationPresetId = null;
    state.currentSessionCanSwitchMode = false;
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
