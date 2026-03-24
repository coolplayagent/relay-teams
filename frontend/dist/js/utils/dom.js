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
    chatContainer: qs('.chat-container'),
    projectView: qs('#project-view'),
    projectViewTitle: qs('#project-view-title'),
    projectViewSummary: qs('#project-view-summary'),
    projectViewContent: qs('#project-view-content'),
    projectViewReloadBtn: qs('#project-view-reload'),
    projectViewCloseBtn: qs('#project-view-close'),
    sidebar: qs('.sidebar'),
    sidebarResizer: qs('#sidebar-resizer'),
    sidebarToggleBtn: qs('#toggle-sidebar'),
    inspectorToggleBtn: qs('#toggle-inspector'),
    rightRail: qs('#right-rail'),
    rightRailResizer: qs('#right-rail-resizer'),
    newProjectBtn: qs('#new-project-btn'),
    projectSortBtn: qs('#project-sort-btn'),
    languageToggleBtn: qs('#language-toggle-btn'),
    themeToggleBtn: qs('#toggle-theme'),
    toggleSubagentsBtn: qs('#toggle-subagents'),
    backendStatus: qs('#backend-status'),
    backendStatusLabel: qs('#backend-status-label'),
    subagentRoleSelect: qs('#subagent-role-select'),
    subagentStatusSummary: qs('#subagent-status-summary'),
    subagentRoleMeta: qs('#subagent-role-meta'),
    promptInput: qs('#prompt-input'),
    promptInputHint: qs('#prompt-input-hint'),
    yoloToggle: qs('#yolo-toggle'),
    thinkingModeToggle: qs('#thinking-mode-toggle'),
    thinkingEffortField: qs('#thinking-effort-field'),
    thinkingEffortSelect: qs('#thinking-effort-select'),
    sessionModeLock: qs('#session-mode-lock'),
    sessionModeLabel: qs('#session-mode-label'),
    sessionModeNormalBtn: qs('#session-mode-normal-btn'),
    sessionModeOrchestrationBtn: qs('#session-mode-orchestration-btn'),
    normalRoleField: qs('#normal-role-field'),
    normalRoleSelect: qs('#normal-role-select'),
    orchestrationPresetField: qs('#orchestration-preset-field'),
    orchestrationPresetSelect: qs('#orchestration-preset-select'),
    sessionTokenUsage: qs('#session-token-usage'),
    sendBtn: qs('#send-btn'),
    stopBtn: qs('#stop-btn'),
    chatForm: qs('#chat-form')
};
