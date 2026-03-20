// js/state.js

export const state = {
    currentSessionId: null,
    currentWorkspaceId: null,
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
    selectedRoleId: null,
    coordinatorRoleId: null,
    rightRailExpanded: true,
};

export function setCoordinatorRoleId(roleId) {
    state.coordinatorRoleId = normalizeRoleId(roleId) || null;
}

export function getCoordinatorRoleId() {
    return normalizeRoleId(state.coordinatorRoleId);
}

export function isCoordinatorRoleId(roleId) {
    const safeRoleId = normalizeRoleId(roleId);
    if (!safeRoleId) {
        return false;
    }
    return safeRoleId === getCoordinatorRoleId();
}

export function humanizeRoleId(roleId, { coordinatorLabel = 'Coordinator', fallback = 'Agent' } = {}) {
    const safeRoleId = normalizeRoleId(roleId);
    if (!safeRoleId) {
        return fallback;
    }
    if (isCoordinatorRoleId(safeRoleId)) {
        return coordinatorLabel;
    }
    return safeRoleId
        .split(/[_\\s-]+/)
        .filter(Boolean)
        .map(part => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ');
}

function normalizeRoleId(roleId) {
    return String(roleId || '').trim();
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
