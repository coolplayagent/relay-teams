// js/state.js

export const state = {
    currentSessionId: null,
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
    selectedRoleId: null,
    rightRailExpanded: true,
};

export const els = {
    newBtn: document.getElementById('new-btn'),
    sessionsList: document.getElementById('sessions-list'),
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
