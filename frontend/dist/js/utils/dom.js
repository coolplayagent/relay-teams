/**
 * utils/dom.js
 * Centralized DOM querying and manipulation helpers.
 */

export const qs = (selector, parent = document) => parent.querySelector(selector);
export const qsa = (selector, parent = document) => parent.querySelectorAll(selector);

// Cached references to persistent UI elements
export const els = {
    projectsList: qs('#projects-list'),
    roundsList: qs('#rounds-list'),
    backBtn: qs('#back-btn'),
    recoveryBannerHost: qs('#recovery-banner-host'),
    inspectorPanel: qs('#rail-inspector'),
    systemLogs: qs('#system-logs'),
    chatMessages: qs('#chat-messages'),
    sidebar: qs('.sidebar'),
    sidebarResizer: qs('#sidebar-resizer'),
    sidebarToggleBtn: qs('#toggle-sidebar'),
    inspectorToggleBtn: qs('#toggle-inspector'),
    rightRail: qs('#right-rail'),
    rightRailResizer: qs('#right-rail-resizer'),
    newProjectBtn: qs('#new-project-btn'),
    projectSortBtn: qs('#project-sort-btn'),
    themeToggleBtn: qs('#toggle-theme'),
    toggleSubagentsBtn: qs('#toggle-subagents'),
    backendStatus: qs('#backend-status'),
    backendStatusLabel: qs('#backend-status-label'),
    subagentRoleSelect: qs('#subagent-role-select'),
    subagentStatusSummary: qs('#subagent-status-summary'),
    subagentRoleMeta: qs('#subagent-role-meta'),
    promptInput: qs('#prompt-input'),
    promptInputHint: qs('#prompt-input-hint'),
    yoloModeToggle: qs('#yolo-mode-toggle'),
    sessionTokenUsage: qs('#session-token-usage'),
    sendBtn: qs('#send-btn'),
    stopBtn: qs('#stop-btn'),
    chatForm: qs('#chat-form')
};
