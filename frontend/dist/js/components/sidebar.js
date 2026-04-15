/**
 * components/sidebar.js
 * Renders the left rail as a home-first navigation shell with feature entries and workspace sessions.
 */
import { els } from '../utils/dom.js';
import { showConfirmDialog, showFormDialog, showTextInputDialog } from '../utils/feedback.js';
import { sysLog } from '../utils/logger.js';
import {
    createAutomationProject,
    deleteAutomationProject,
    deleteSession,
    deleteSessionSubagent,
    deleteWorkspace,
    disableAutomationProject,
    enableAutomationProject,
    fetchAutomationProjects,
    fetchSessions,
    fetchWorkspaces,
    forkWorkspace,
    pickWorkspace,
    startNewSession,
    updateSession,
} from '../core/api.js';
import { state } from '../core/state.js';
import {
    closeNormalModeSubagentStream,
    detachActiveStreamForSessionSwitch,
} from '../core/stream.js';
import { clearSessionRecovery, stopSessionContinuity } from '../app/recovery.js';
import { clearAllStreamState } from './messageRenderer.js';
import { clearAllPanels } from './agentPanel.js';
import { clearContextIndicators } from './contextIndicators.js';
import { formatMessage, t } from '../utils/i18n.js';
import {
    hideProjectView,
    openAutomationHomeView,
    openImFeatureView,
    openSkillsFeatureView,
    openWorkspaceProjectView,
    requestAutomationProjectInput as requestAutomationProjectEditorInput,
} from './projectView.js';
import {
    buildSubagentSessionLabel,
    ensureSessionSubagents,
    getActiveSubagentSession,
    removeSessionSubagent,
    getSessionSubagentSessions,
    isSubagentSessionListExpanded,
    isSubagentSessionListLoading,
    toggleSubagentSessionList,
} from './subagentSessions.js';

const DEFAULT_VISIBLE_SESSION_COUNT = 10;
const AUTOMATION_INTERNAL_WORKSPACE_ID = 'automation-system';
const FEATURE_IDS = Object.freeze({
    skills: 'skills',
    automation: 'automation',
    gateway: 'gateway',
});

let selectSessionHandler = null;
let refreshTimer = null;
const expandedProjectIds = new Set();
const expandedProjectSessionIds = new Set();
const initializedProjectIds = new Set();
const sessionWorkspaceMap = new Map();
const automationBoundSessionIds = new Set();
let projectSortMode = 'recent';
let openProjectMenuId = null;
let projectMenuDismissBound = false;
let languageRefreshBound = false;
let pendingSessionAnimation = null;

const SESSION_ANIMATION_ENTER_MS = 220;
const SESSION_ANIMATION_REMOVE_MS = 180;
const SESSION_ANIMATION_ACTIVATE_MS = 240;

export function setSelectSessionHandler(handler) {
    selectSessionHandler = handler;
}

function clearActiveSessionView() {
    const sessionId = state.currentSessionId;
    if (state.activeEventSource) {
        detachActiveStreamForSessionSwitch({ focusPrompt: false });
    }
    if (sessionId) {
        stopSessionContinuity(sessionId);
    }
    state.currentSessionId = null;
    clearSessionRecovery();
    clearAllPanels();
    clearContextIndicators();
    clearAllStreamState();
    if (els.chatMessages) {
        els.chatMessages.innerHTML = '';
    }
}

function escapeHtml(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function formatWorkspaceProjectLabel(workspace) {
    const fallbackProject = t('sidebar.project');
    const workspaceId = String(workspace?.workspace_id || fallbackProject).trim() || fallbackProject;
    const rootPath = String(workspace?.root_path || '').trim();
    if (!rootPath) {
        return workspaceId;
    }
    const parts = rootPath.split(/[\/\\]/).filter(Boolean);
    return parts.at(-1) || workspaceId;
}

function isForkedWorkspace(workspace) {
    return String(workspace?.profile?.file_scope?.backend || '').trim() === 'git_worktree';
}

function getSessionMetadata(session) {
    return session?.metadata && typeof session.metadata === 'object'
        ? session.metadata
        : {};
}

function formatSessionLabel(session) {
    const metadata = getSessionMetadata(session);
    const keys = ['title', 'name', 'label'];
    for (const key of keys) {
        const label = String(metadata[key] || '').trim();
        if (label) {
            return label;
        }
    }
    return String(session?.session_id || 'Session');
}

function isAutomationSession(session) {
    const sessionId = String(session?.session_id || '').trim();
    return (
        String(session?.project_kind || '').trim() === 'automation'
        || (sessionId && automationBoundSessionIds.has(sessionId))
    );
}

function isImSession(session) {
    return String(getSessionMetadata(session).source_kind || '').trim() === 'im';
}

function getSessionSourceKinds(session) {
    const sourceKinds = [];
    if (isAutomationSession(session)) {
        sourceKinds.push('automation');
    }
    if (isImSession(session)) {
        sourceKinds.push('im');
    }
    return sourceKinds;
}

function renderSingleSessionSourceIcon(sourceKind) {
    if (sourceKind === 'im') {
        return `
            <span class="session-source-icon session-source-icon-im" aria-hidden="true">
                <svg viewBox="0 0 16 16" fill="none" class="icon-sm">
                    <path d="M3.25 4.5a2.25 2.25 0 0 1 2.25-2.25h5a2.25 2.25 0 0 1 2.25 2.25v3a2.25 2.25 0 0 1-2.25 2.25H7.4L4.8 11.9a.45.45 0 0 1-.75-.33V9.75h-.55A2.25 2.25 0 0 1 1.25 7.5z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>
                    <path d="M5.1 5.95h5.8M5.1 7.85h3.6" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
                </svg>
            </span>
        `;
    }
    if (sourceKind === 'automation') {
        return `
            <span class="session-source-icon session-source-icon-automation" aria-hidden="true">
                <svg viewBox="0 0 16 16" fill="none" class="icon-sm">
                    <rect x="2.15" y="2.15" width="11.7" height="11.7" rx="2.35" stroke="currentColor" stroke-width="1.3"/>
                    <path d="M5 8h2l1-2.2L9.45 10 10.6 8H12" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
            </span>
        `;
    }
    return '';
}

function renderSessionSourceIcon(session) {
    return getSessionSourceKinds(session)
        .map(sourceKind => renderSingleSessionSourceIcon(sourceKind))
        .join('');
}

function getSessionSourceClassName(session) {
    const sourceKinds = getSessionSourceKinds(session);
    if (sourceKinds.length === 0) {
        return '';
    }
    return ` ${sourceKinds.map(sourceKind => `session-item-${sourceKind}`).join(' ')}`;
}

function shouldRenderSubagentChildren(session) {
    return String(session?.session_mode || '').trim() === 'normal';
}

function renderSubagentToggle(session) {
    const sessionId = String(session?.session_id || '').trim();
    if (!sessionId || !shouldRenderSubagentChildren(session)) {
        return '';
    }
    const children = getSessionSubagentSessions(sessionId);
    if (children.length === 0) {
        return '';
    }
    const expanded = isSubagentSessionListExpanded(sessionId);
    const icon = expanded ? '&#9662;' : '&#9656;';
    return `
        <button
            class="session-subagents-toggle"
            type="button"
            data-session-id="${escapeHtml(sessionId)}"
            aria-expanded="${expanded ? 'true' : 'false'}"
            title="${escapeHtml(t('sidebar.subagent_sessions_toggle'))}"
            aria-label="${escapeHtml(t('sidebar.subagent_sessions_toggle'))}"
        >
            <span class="session-subagents-toggle-icon" aria-hidden="true">${icon}</span>
            <span class="session-subagents-toggle-count">${escapeHtml(String(children.length))}</span>
        </button>
    `;
}

function renderSubagentChildren(session) {
    const sessionId = String(session?.session_id || '').trim();
    if (!sessionId || !shouldRenderSubagentChildren(session)) {
        return '';
    }
    const expanded = isSubagentSessionListExpanded(sessionId);
    if (!expanded) {
        return '';
    }
    const loading = isSubagentSessionListLoading(sessionId);
    const children = getSessionSubagentSessions(sessionId);
    if (loading && children.length === 0) {
        return `
            <div class="session-subagent-list">
                <div class="session-subagent-empty">${escapeHtml(t('sidebar.subagent_sessions_loading'))}</div>
            </div>
        `;
    }
    if (children.length === 0) {
        return '';
    }
    const activeSubagent = getActiveSubagentSession();
    return `
        <div class="session-subagent-list">
            ${children.map(child => {
                const active = !!(
                    activeSubagent
                    && activeSubagent.sessionId === sessionId
                    && activeSubagent.instanceId === child.instanceId
                );
                return `
                    <div
                        class="session-item session-subagent-item${active ? ' active' : ''}"
                        tabindex="0"
                        role="button"
                        data-session-id="${escapeHtml(sessionId)}"
                        data-subagent-instance-id="${escapeHtml(child.instanceId)}"
                        data-subagent-role-id="${escapeHtml(child.roleId)}"
                        data-subagent-run-id="${escapeHtml(child.runId)}"
                        data-subagent-title="${escapeHtml(child.title || '')}"
                    >
                        <span class="session-main">
                            <span class="session-id">
                                <span class="session-label-text">${escapeHtml(buildSubagentSessionLabel(child))}</span>
                            </span>
                        </span>
                        <span class="session-meta">
                            <span class="session-time">${escapeHtml(formatRelativeTime(child.updatedAt || child.createdAt || ''))}</span>
                            <span class="session-actions">
                                <button
                                    class="session-delete-btn session-subagent-delete-btn"
                                    type="button"
                                    data-session-id="${escapeHtml(sessionId)}"
                                    data-subagent-instance-id="${escapeHtml(child.instanceId)}"
                                    data-subagent-run-id="${escapeHtml(child.runId)}"
                                    data-subagent-label="${escapeHtml(buildSubagentSessionLabel(child))}"
                                    title="${escapeHtml(t('sidebar.delete_subagent'))}"
                                    aria-label="${escapeHtml(t('sidebar.delete_subagent'))}"
                                >
                                    <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                                        <path d="M5 7h14M9 7V5.8A1.8 1.8 0 0 1 10.8 4h2.4A1.8 1.8 0 0 1 15 5.8V7m-8 0v10.2A1.8 1.8 0 0 0 8.8 19h6.4A1.8 1.8 0 0 0 17 17.2V7M10 10.2v5.6M14 10.2v5.6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>
                                    </svg>
                                </button>
                            </span>
                        </span>
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

function timestampValue(value) {
    const parsed = Date.parse(String(value || ''));
    return Number.isNaN(parsed) ? 0 : parsed;
}

function formatRelativeTime(value) {
    const timestamp = timestampValue(value);
    if (!timestamp) return '';
    const diffMinutes = Math.max(0, Math.round((Date.now() - timestamp) / 60000));
    if (diffMinutes < 1) return t('time.just_now');
    if (diffMinutes < 60) return `${diffMinutes}${t('time.minute_short')}`;
    const diffHours = Math.round(diffMinutes / 60);
    if (diffHours < 24) return `${diffHours}${t('time.hour_short')}`;
    const diffDays = Math.round(diffHours / 24);
    if (diffDays < 7) return `${diffDays}${t('time.day_short')}`;
    const diffWeeks = Math.round(diffDays / 7);
    if (diffWeeks < 5) return `${diffWeeks}${t('time.week_short')}`;
    const diffMonths = Math.round(diffDays / 30);
    if (diffMonths < 12) return `${diffMonths}${t('time.month_short')}`;
    return `${Math.round(diffDays / 365)}${t('time.year_short')}`;
}

function formatWorkspaceLabel(workspace) {
    const fallbackProject = t('sidebar.project');
    const workspaceId = String(workspace?.workspace_id || fallbackProject).trim() || fallbackProject;
    if (String(workspace?.profile?.file_scope?.backend || '').trim() === 'git_worktree') {
        return workspaceId;
    }
    const rootPath = String(workspace?.root_path || '').trim();
    if (!rootPath) return workspaceId;
    const parts = rootPath.split(/[\\/]/).filter(Boolean);
    return parts.at(-1) || workspaceId;
}

function formatProjectLabel(group) {
    if (group.kind === 'automation') {
        return String(group.project.display_name || group.project.name || group.id).trim() || group.id;
    }
    return formatWorkspaceProjectLabel(group.workspace);
}

function formatWorkspaceOptionLabel(workspace) {
    const workspaceId = String(workspace?.workspace_id || '').trim();
    const rootPath = String(workspace?.root_path || '').trim();
    if (workspaceId && rootPath) {
        return `${workspaceId} - ${rootPath}`;
    }
    return workspaceId || rootPath;
}

function formatWorkspaceOptionDescription(workspace) {
    const rootPath = String(workspace?.root_path || '').trim();
    return rootPath || t('automation.workspace.help');
}

function buildFeishuBindingKey(binding) {
    const triggerId = String(binding?.trigger_id || '').trim();
    const tenantKey = String(binding?.tenant_key || '').trim();
    const chatId = String(binding?.chat_id || '').trim();
    const sessionId = String(binding?.session_id || '').trim();
    if (!triggerId || !tenantKey || !chatId || !sessionId) {
        return '';
    }
    return `${triggerId}::${tenantKey}::${chatId}::${sessionId}`;
}

function buildFeishuBindingOptions(bindings) {
    const safeBindings = Array.isArray(bindings) ? bindings : [];
    const options = [
        {
            value: '',
            label: t('sidebar.feishu_delivery_none'),
            description: t('sidebar.feishu_delivery_none_copy'),
        },
    ];
    safeBindings.forEach(binding => {
        const bindingKey = buildFeishuBindingKey(binding);
        if (!bindingKey) {
            return;
        }
        const triggerName = String(binding?.trigger_name || '').trim();
        const sourceLabel = String(binding?.source_label || '').trim();
        const chatType = String(binding?.chat_type || '').trim();
        const sessionTitle = String(binding?.session_title || '').trim();
        options.push({
            value: bindingKey,
            label: sessionTitle || sourceLabel || bindingKey,
            description: [triggerName, chatType].filter(Boolean).join(' - '),
        });
    });
    return options;
}

function groupKey(kind, id) {
    return `${kind}:${id}`;
}

function sessionGroupKey(session) {
    const workspaceId = String(session?.workspace_id || '').trim();
    return workspaceId ? groupKey('workspace', workspaceId) : '';
}

function buildProjectGroups(workspaces, sessions) {
    const sessionsByGroup = new Map();
    sessionWorkspaceMap.clear();

    sessions.forEach(session => {
        const sessionId = String(session?.session_id || '').trim();
        const key = sessionGroupKey(session);
        if (!key) return;
        const workspaceId = String(session?.workspace_id || '').trim();
        if (workspaceId && sessionId) {
            sessionWorkspaceMap.set(sessionId, workspaceId);
        }
        if (!sessionsByGroup.has(key)) sessionsByGroup.set(key, []);
        sessionsByGroup.get(key).push(session);
    });

    const groups = [];
    workspaces.forEach(workspace => {
        const id = String(workspace?.workspace_id || '').trim();
        if (!id) return;
        const key = groupKey('workspace', id);
        const projectSessions = Array.from(sessionsByGroup.get(key) || []).sort((a, b) => timestampValue(b.updated_at) - timestampValue(a.updated_at));
        if (!initializedProjectIds.has(key)) {
            initializedProjectIds.add(key);
            expandedProjectIds.add(key);
        }
        groups.push({
            kind: 'workspace',
            id,
            key,
            workspace,
            sessions: projectSessions,
            latestUpdatedAt: Math.max(timestampValue(workspace.updated_at), timestampValue(projectSessions[0]?.updated_at)),
        });
    });

    if (projectSortMode === 'name') {
        return groups.sort((a, b) => formatProjectLabel(a).toLowerCase().localeCompare(formatProjectLabel(b).toLowerCase()));
    }
    return groups.sort((a, b) => b.latestUpdatedAt - a.latestUpdatedAt);
}

function syncProjectSortButton() {
    const btn = els.projectsList?.querySelector('.projects-toolbar-sort-btn');
    if (!btn) return;
    const sortLabel = projectSortMode === 'name' ? t('sidebar.sort_name') : t('sidebar.sort_recent');
    btn.title = sortLabel;
    btn.setAttribute('aria-label', sortLabel);
    btn.dataset.sortMode = projectSortMode;
}

function ensureProjectMenuDismissBinding() {
    if (projectMenuDismissBound || typeof document === 'undefined' || typeof document.addEventListener !== 'function') {
        return;
    }
    document.addEventListener('click', event => {
        const target = event?.target;
        if (target?.closest?.('.project-options-btn, .project-menu')) return;
        if (openProjectMenuId !== null) {
            openProjectMenuId = null;
            void loadProjects();
        }
    });
    projectMenuDismissBound = true;
}

function renderEmptyProjectsState() {
    const emptyState = document.createElement('div');
    emptyState.className = 'projects-empty-state';
    emptyState.innerHTML = `
        <p class="projects-empty-title">${escapeHtml(t('sidebar.no_projects_title'))}</p>
        <p class="projects-empty-copy">${escapeHtml(t('sidebar.no_projects_copy'))}</p>
    `;
    return emptyState;
}

function renderProjectsToolbar() {
    const toolbar = document.createElement('div');
    toolbar.className = 'projects-toolbar';
    toolbar.innerHTML = `
        <div class="projects-toolbar-title">${escapeHtml(t('sidebar.workspace'))}</div>
        <div class="projects-toolbar-actions">
            <button class="sidebar-header-btn projects-toolbar-new-btn" type="button" title="${escapeHtml(t('sidebar.new_project'))}" aria-label="${escapeHtml(t('sidebar.new_project'))}">
                <svg viewBox="0 0 24 24" fill="none" class="icon" aria-hidden="true">
                    <path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
                </svg>
            </button>
            <button class="sidebar-header-btn projects-toolbar-sort-btn" type="button" title="${escapeHtml(t('sidebar.sort_recent'))}" aria-label="${escapeHtml(t('sidebar.sort_recent'))}">
                <svg viewBox="0 0 24 24" fill="none" class="icon" aria-hidden="true">
                    <path d="M7 6h10M7 12h7M7 18h4" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
                    <path d="M17 8l2-2 2 2M19 6v12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
            </button>
        </div>
    `;
    toolbar.querySelector('.projects-toolbar-new-btn')?.addEventListener('click', () => void handleNewProjectClick());
    toolbar.querySelector('.projects-toolbar-sort-btn')?.addEventListener('click', () => toggleProjectSortMode());
    return toolbar;
}

function getActiveFeatureId() {
    return String(state.currentFeatureViewId || '').trim();
}

function renderFeatureNav() {
    const activeFeatureId = getActiveFeatureId();
    const section = document.createElement('section');
    section.className = 'home-feature-section';
    section.innerHTML = `
        <button class="primary-btn home-new-session-btn" type="button">
            <span class="home-new-session-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" class="icon">
                    <path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" />
                </svg>
            </span>
            <span>${escapeHtml(t('sidebar.new_session_primary'))}</span>
        </button>
        <div class="home-feature-list" role="navigation" aria-label="${escapeHtml(t('sidebar.feature_navigation'))}">
            <button class="home-feature-item${activeFeatureId === FEATURE_IDS.skills ? ' is-active' : ''}" type="button" data-feature-id="${FEATURE_IDS.skills}">
                <span class="home-feature-icon" aria-hidden="true">
                    <svg viewBox="0 0 24 24" fill="none" class="icon home-feature-icon-svg home-feature-icon-svg-skills">
                        <path d="M10.35 3.95h3.3l.34 1.74c.53.15 1.04.36 1.5.62l1.55-.89 2.33 2.33-.89 1.55c.26.46.47.97.62 1.5l1.74.34v3.3l-1.74.34a6.7 6.7 0 0 1-.62 1.5l.89 1.55-2.33 2.33-1.55-.89a6.7 6.7 0 0 1-1.5.62l-.34 1.74h-3.3l-.34-1.74a6.7 6.7 0 0 1-1.5-.62l-1.55.89-2.33-2.33.89-1.55a6.7 6.7 0 0 1-.62-1.5l-1.74-.34v-3.3l1.74-.34c.15-.53.36-1.04.62-1.5l-.89-1.55 2.33-2.33 1.55.89c.46-.26.97-.47 1.5-.62z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
                        <circle cx="12" cy="12" r="2.45" stroke="currentColor" stroke-width="1.5"/>
                    </svg>
                </span>
                <span class="home-feature-label">${escapeHtml(t('sidebar.feature_skills'))}</span>
            </button>
            <button class="home-feature-item${activeFeatureId === FEATURE_IDS.automation ? ' is-active' : ''}" type="button" data-feature-id="${FEATURE_IDS.automation}">
                <span class="home-feature-icon" aria-hidden="true">
                    <svg viewBox="0 0 24 24" fill="none" class="icon home-feature-icon-svg home-feature-icon-svg-automation">
                        <path d="M6.25 6.75h11.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>
                        <path d="M8.5 4.75v4M15.5 4.75v4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>
                        <rect x="4.25" y="7.25" width="15.5" height="12.5" rx="2.4" stroke="currentColor" stroke-width="1.7"/>
                        <path d="M8 12.35h3.1l1.35-1.9 1.55 3.2 1.15-1.3H16" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>
                        <circle cx="8" cy="16.3" r=".85" fill="currentColor"/>
                        <circle cx="12" cy="16.3" r=".85" fill="currentColor"/>
                        <circle cx="16" cy="16.3" r=".85" fill="currentColor"/>
                    </svg>
                </span>
                <span class="home-feature-label">${escapeHtml(t('sidebar.feature_automation'))}</span>
            </button>
            <button class="home-feature-item${activeFeatureId === FEATURE_IDS.gateway ? ' is-active' : ''}" type="button" data-feature-id="${FEATURE_IDS.gateway}">
                <span class="home-feature-icon" aria-hidden="true">
                    <svg viewBox="0 0 24 24" fill="none" class="icon home-feature-icon-svg home-feature-icon-svg-gateway">
                        <path d="M5.2 6.4h13.6a1.8 1.8 0 0 1 1.8 1.8v7a1.8 1.8 0 0 1-1.8 1.8H12.3l-3.2 2.35a.5.5 0 0 1-.8-.4V17H5.2a1.8 1.8 0 0 1-1.8-1.8v-7a1.8 1.8 0 0 1 1.8-1.8Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/>
                        <path d="M8 10.05h8M8 13h5.1" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>
                    </svg>
                </span>
                <span class="home-feature-label">${escapeHtml(t('sidebar.feature_gateway'))}</span>
            </button>
        </div>
    `;
    section.querySelector('.home-new-session-btn')?.addEventListener('click', () => void handlePrimaryNewSessionClick());
    section.querySelectorAll('.home-feature-item').forEach(button => {
        button.addEventListener('click', () => {
            const featureId = String(button.getAttribute('data-feature-id') || '').trim();
            void openFeatureView(featureId);
        });
    });
    return section;
}

async function requestWorkspaceSelection(workspaces) {
    const workspaceOptions = (Array.isArray(workspaces) ? workspaces : []).map(workspace => ({
        value: String(workspace?.workspace_id || '').trim(),
        label: formatWorkspaceOptionLabel(workspace),
        description: formatWorkspaceOptionDescription(workspace),
    })).filter(option => option.value);
    if (workspaceOptions.length === 0) {
        return '';
    }
    const values = await showFormDialog({
        title: t('sidebar.new_session_primary'),
        message: t('sidebar.select_workspace_for_session'),
        tone: 'info',
        confirmLabel: t('sidebar.new_session'),
        cancelLabel: t('settings.action.cancel'),
        fields: [
            {
                id: 'workspace_id',
                label: t('sidebar.workspace_directory'),
                type: 'select',
                value: workspaceOptions[0]?.value || '',
                options: workspaceOptions,
            },
        ],
    });
    return String(values?.workspace_id || '').trim();
}

async function handlePrimaryNewSessionClick() {
    try {
        const fetchedWorkspaces = await fetchWorkspaces();
        const workspaces = Array.isArray(fetchedWorkspaces) ? fetchedWorkspaces : [];
        if (workspaces.length === 0) {
            await handleNewProjectClick();
            return;
        }
        const currentWorkspaceId = String(state.currentWorkspaceId || '').trim();
        const matchingWorkspace = workspaces.find(workspace => String(workspace?.workspace_id || '').trim() === currentWorkspaceId) || null;
        if (matchingWorkspace) {
            await handleNewSessionClick(currentWorkspaceId, true);
            return;
        }
        if (workspaces.length === 1) {
            await handleNewSessionClick(String(workspaces[0]?.workspace_id || '').trim(), true);
            return;
        }
        const selectedWorkspaceId = await requestWorkspaceSelection(workspaces);
        if (!selectedWorkspaceId) {
            return;
        }
        await handleNewSessionClick(selectedWorkspaceId, true);
    } catch (error) {
        sysLog(formatMessage('sidebar.error.creating_session', { error: error.message }), 'log-error');
    }
}

async function openFeatureView(featureId) {
    if (featureId === FEATURE_IDS.skills) {
        await openSkillsFeatureView();
    } else if (featureId === FEATURE_IDS.automation) {
        await openAutomationHomeView();
    } else if (featureId === FEATURE_IDS.gateway) {
        await openImFeatureView();
    }
    await loadProjects();
}

function isNativeDirectoryPickerUnavailable(error) {
    return error?.status === 503 && error?.detail === 'Native directory picker is unavailable';
}

async function requestWorkspaceRootPath() {
    const enteredPath = await showTextInputDialog({
        title: t('sidebar.enter_project_path_title'),
        message: t('sidebar.enter_project_path_message'),
        tone: 'info',
        confirmLabel: t('sidebar.new_project'),
        cancelLabel: t('settings.action.cancel'),
        placeholder: '/path/to/project',
    });
    const rootPath = String(enteredPath || '').trim();
    return rootPath || null;
}

async function requestAutomationProjectInput() {
    return requestAutomationProjectEditorInput({});
}

async function selectSessionById(sessionId) {
    if (!selectSessionHandler) throw new Error('selectSession handler is not configured');
    const workspaceId = sessionWorkspaceMap.get(sessionId);
    if (workspaceId) state.currentWorkspaceId = workspaceId;
    await selectSessionHandler(sessionId);
}

function setPendingSessionAnimation(sessionId, animation) {
    const safeSessionId = String(sessionId || '').trim();
    const safeAnimation = String(animation || '').trim();
    if (!safeSessionId || !safeAnimation) {
        pendingSessionAnimation = null;
        return;
    }
    pendingSessionAnimation = {
        sessionId: safeSessionId,
        animation: safeAnimation,
    };
}

function consumePendingSessionAnimation() {
    const pending = pendingSessionAnimation;
    pendingSessionAnimation = null;
    return pending;
}

function findSessionItem(sessionId) {
    const safeSessionId = String(sessionId || '').trim();
    if (!safeSessionId || !els.projectsList) return null;
    const items = Array.from(els.projectsList.querySelectorAll('.session-item'));
    return items.find(item => String(item?.getAttribute?.('data-session-id') || '').trim() === safeSessionId) || null;
}

function animateSessionItem(item, animation) {
    if (!item) return;
    const safeAnimation = String(animation || '').trim();
    if (!safeAnimation) return;
    const animationClass = `session-item-${safeAnimation}`;
    if (item.classList?.add) {
        item.classList.remove('session-item-entering', 'session-item-removing', 'session-item-activating');
        item.classList.add(animationClass);
        const duration = safeAnimation === 'removing'
            ? SESSION_ANIMATION_REMOVE_MS
            : safeAnimation === 'entering'
                ? SESSION_ANIMATION_ENTER_MS
                : SESSION_ANIMATION_ACTIVATE_MS;
        globalThis.setTimeout(() => {
            item.classList?.remove?.(animationClass);
        }, duration);
        return;
    }
    if (typeof item.className === 'string' && !item.className.includes(animationClass)) {
        item.className = `${item.className} ${animationClass}`.trim();
    }
}

function playPendingSessionAnimation() {
    const pending = consumePendingSessionAnimation();
    if (!pending) return;
    const item = findSessionItem(pending.sessionId);
    if (!item) return;
    animateSessionItem(item, pending.animation);
}

function bindProjectCard(card, group) {
    const projectId = group.id;
    const groupKeyValue = group.key;
    card.querySelector('.project-toggle')?.addEventListener('click', () => {
        if (expandedProjectIds.has(groupKeyValue)) expandedProjectIds.delete(groupKeyValue);
        else expandedProjectIds.add(groupKeyValue);
        void loadProjects();
    });
    card.querySelector('.project-title-btn')?.addEventListener('click', async event => {
        event?.stopPropagation?.();
        await openWorkspaceProjectView(group.workspace);
        await loadProjects();
    });
    card.querySelector('.project-new-session-btn')?.addEventListener('click', event => {
        event?.stopPropagation?.();
        void handleNewSessionClick(projectId, true);
    });
    card.querySelector('.project-options-btn')?.addEventListener('click', event => {
        event?.stopPropagation?.();
        openProjectMenuId = openProjectMenuId === groupKeyValue ? null : groupKeyValue;
        void loadProjects();
    });
    card.querySelector('.project-session-visibility-btn')?.addEventListener('click', event => {
        event?.stopPropagation?.();
        if (expandedProjectSessionIds.has(groupKeyValue)) expandedProjectSessionIds.delete(groupKeyValue);
        else expandedProjectSessionIds.add(groupKeyValue);
        void loadProjects();
    });
    card.querySelector('.project-fork-btn')?.addEventListener('click', event => {
        event?.stopPropagation?.();
        void handleForkWorkspaceClick(group.workspace);
    });
    card.querySelector('.project-remove-btn')?.addEventListener('click', event => {
        event?.stopPropagation?.();
        void handleRemoveWorkspaceClick(group.workspace);
    });

    card.querySelectorAll('.session-item').forEach(button => {
        if (button.classList.contains('session-subagent-item')) {
            return;
        }
        const selectTarget = () => {
            const sessionId = String(button.getAttribute('data-session-id') || '').trim();
            const targetWorkspaceId = String(button.getAttribute('data-workspace-id') || '').trim();
            if (!sessionId) return;
            state.currentWorkspaceId = targetWorkspaceId || projectId;
            void selectSessionById(sessionId).then(() => {
                animateSessionItem(button, 'activating');
            });
        };
        button.addEventListener('click', selectTarget);
        button.addEventListener('keydown', event => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                selectTarget();
            }
        });
    });

    card.querySelectorAll('.session-subagents-toggle').forEach(button => {
        button.addEventListener('click', event => {
            event.preventDefault();
            event.stopPropagation();
            const sessionId = String(button.getAttribute('data-session-id') || '').trim();
            if (!sessionId) return;
            toggleSubagentSessionList(sessionId);
        });
    });

    card.querySelectorAll('.session-subagent-item').forEach(button => {
        const selectChild = () => {
            const sessionId = String(button.getAttribute('data-session-id') || '').trim();
            const instanceId = String(button.getAttribute('data-subagent-instance-id') || '').trim();
            const roleId = String(button.getAttribute('data-subagent-role-id') || '').trim();
            const runId = String(button.getAttribute('data-subagent-run-id') || '').trim();
            const title = String(button.getAttribute('data-subagent-title') || '').trim();
            if (!sessionId || !instanceId) return;
            document.dispatchEvent(
                new CustomEvent('agent-teams-select-subagent-session', {
                    detail: {
                        sessionId,
                        subagent: {
                            instanceId,
                            roleId,
                            runId,
                            title,
                        },
                    },
                }),
            );
        };
        button.addEventListener('click', event => {
            event.preventDefault();
            event.stopPropagation();
            selectChild();
        });
        button.addEventListener('keydown', event => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                selectChild();
            }
        });
    });

    card.querySelectorAll('.session-rename-btn').forEach(button => {
        button.addEventListener('click', async event => {
            event.stopPropagation();
            const sessionId = String(button.getAttribute('data-session-id') || '').trim();
            if (!sessionId) return;
            let metadata = {};
            try { metadata = JSON.parse(String(button.getAttribute('data-session-metadata') || '{}')); } catch (_) {}
            const currentTitle = String(metadata.title || '').trim();
            const nextTitle = await showTextInputDialog({
                title: t('sidebar.rename_session_title'),
                message: t('sidebar.rename_session_message'),
                tone: 'info',
                confirmLabel: t('settings.action.save'),
                cancelLabel: t('settings.action.cancel'),
                placeholder: t('sidebar.session_name_placeholder'),
                value: currentTitle || sessionId,
            });
            if (nextTitle === null) return;
            const normalizedTitle = String(nextTitle || '').trim();
            const nextMetadata = { ...metadata };
            if (normalizedTitle) nextMetadata.title = normalizedTitle;
            else delete nextMetadata.title;
            await updateSession(sessionId, nextMetadata);
            await loadProjects();
        });
    });

    card.querySelectorAll('.session-subagent-delete-btn').forEach(button => {
        button.addEventListener('click', async event => {
            event.stopPropagation();
            const sessionId = String(button.getAttribute('data-session-id') || '').trim();
            const instanceId = String(button.getAttribute('data-subagent-instance-id') || '').trim();
            const runId = String(button.getAttribute('data-subagent-run-id') || '').trim();
            const subagentLabel = String(button.getAttribute('data-subagent-label') || '').trim() || instanceId;
            if (!sessionId || !instanceId) return;
            const shouldDelete = await showConfirmDialog({
                title: t('sidebar.delete_subagent_title'),
                message: formatMessage('sidebar.delete_subagent_message', { subagent: subagentLabel }),
                tone: 'warning',
                confirmLabel: t('settings.action.delete'),
                cancelLabel: t('settings.action.cancel'),
            });
            if (!shouldDelete) return;
            const subagentItem = button.closest?.('.session-subagent-item') || null;
            animateSessionItem(subagentItem, 'removing');
            await new Promise(resolve => globalThis.setTimeout(resolve, SESSION_ANIMATION_REMOVE_MS));
            try {
                await deleteSessionSubagent(sessionId, instanceId);
            } catch (error) {
                sysLog(
                    formatMessage('sidebar.error.deleting_subagent', {
                        error: error?.message || String(error),
                    }),
                    'log-error',
                );
                await loadProjects();
                return;
            }
            const removed = removeSessionSubagent(sessionId, instanceId);
            if (runId) {
                closeNormalModeSubagentStream(runId);
            } else if (removed?.runId) {
                closeNormalModeSubagentStream(removed.runId);
            }
            if (state.currentSessionId === sessionId && !state.activeSubagentSession && typeof selectSessionHandler === 'function') {
                await selectSessionHandler(sessionId);
            }
            await loadProjects();
        });
    });

    card.querySelectorAll('.session-delete-btn').forEach(button => {
        button.addEventListener('click', async event => {
            if (String(button.className || '').includes('session-subagent-delete-btn')) {
                return;
            }
            event.stopPropagation();
            const sessionId = String(button.getAttribute('data-session-id') || '').trim();
            if (!sessionId) return;
            const shouldDelete = await showConfirmDialog({
                title: t('sidebar.delete_session_title'),
                message: formatMessage('sidebar.delete_session_message', { session_id: sessionId }),
                tone: 'warning',
                confirmLabel: t('settings.action.delete'),
                cancelLabel: t('settings.action.cancel'),
            });
            if (!shouldDelete) return;
            const sessionItem = button.closest?.('.session-item') || null;
            animateSessionItem(sessionItem, 'removing');
            await new Promise(resolve => globalThis.setTimeout(resolve, SESSION_ANIMATION_REMOVE_MS));
            await deleteSession(sessionId);
            if (state.currentSessionId === sessionId) {
                clearActiveSessionView();
            }
            await loadProjects();
        });
    });
}

function renderProjectCard(group) {
    const projectKey = group.key;
    const projectId = group.id;
    const projectLabel = formatProjectLabel(group);
    const expanded = expandedProjectIds.has(projectKey);
    const menuOpen = openProjectMenuId === projectKey;
    const sessionsExpanded = expandedProjectSessionIds.has(projectKey);
    const visibleSessions = visibleSessionsForGroup(group, {
        sessionsExpanded,
    });
    const hasHiddenSessions = group.sessions.length > DEFAULT_VISIBLE_SESSION_COUNT;
    const projectViewActive = state.currentMainView === 'project' && state.currentProjectViewWorkspaceId === projectId;
    const pathHint = String(group.workspace?.root_path || '').trim();
    const projectIcon = '<svg viewBox="0 0 24 24" fill="none" class="icon-sm"><path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H10l2 2h6.5A2.5 2.5 0 0 1 21 9.5v7A2.5 2.5 0 0 1 18.5 19h-13A2.5 2.5 0 0 1 3 16.5z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>';
    const card = document.createElement('section');
    card.className = 'project-card';
    card.innerHTML = `
        <div class="project-row">
            <div class="project-title-group">
                <button class="project-toggle" type="button" aria-expanded="${expanded ? 'true' : 'false'}"><span class="project-icon-stack" aria-hidden="true"><span class="project-folder-icon">${projectIcon}</span><span class="project-toggle-icon">${expanded ? '&#9662;' : '&#9656;'}</span></span></button>
                <button class="project-title-btn${projectViewActive ? ' is-active' : ''}" type="button" aria-current="${projectViewActive ? 'page' : 'false'}"><span class="project-title">${escapeHtml(projectLabel)}</span></button>
            </div>
            <div class="project-actions">
                <button class="project-options-btn project-action-btn" type="button" title="${escapeHtml(t('sidebar.project_options'))}" aria-label="${escapeHtml(t('sidebar.project_options'))}"><svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true"><path d="M6 12a1.25 1.25 0 1 0 0 .01M12 12a1.25 1.25 0 1 0 0 .01M18 12a1.25 1.25 0 1 0 0 .01" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></svg></button>
                <button class="project-new-session-btn project-action-btn" type="button" title="${escapeHtml(t('sidebar.new_session'))}" aria-label="${escapeHtml(t('sidebar.new_session'))}"><svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" /></svg></button>
            </div>
        </div>
        <div class="project-path-hint">${escapeHtml(pathHint)}</div>
        ${menuOpen ? `<div class="project-menu project-menu-workspace" role="menu"><button class="project-fork-btn project-workspace-menu-btn" type="button" role="menuitem"><span class="project-menu-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" class="icon-sm"><path d="M9 5H6.75A1.75 1.75 0 0 0 5 6.75v10.5C5 18.22 5.78 19 6.75 19h10.5A1.75 1.75 0 0 0 19 17.25V15M15 5h4m0 0v4m0-4-7.5 7.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg></span><span>${escapeHtml(t('sidebar.fork'))}</span></button><button class="project-remove-btn project-workspace-menu-btn project-remove-workspace-btn" type="button" role="menuitem"><span class="project-menu-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" class="icon-sm"><path d="M6 7h12M9 7V5.8c0-.44 0-.66.09-.83a1 1 0 0 1 .42-.42C9.74 4.5 9.96 4.5 10.4 4.5h3.2c.44 0 .66 0 .83.08a1 1 0 0 1 .42.42c.09.17.09.39.09.83V7m-7 0 .55 9.18c.03.55.05.82.17 1.03a1 1 0 0 0 .43.4c.22.1.49.1 1.03.1h4.64c.54 0 .81 0 1.03-.1a1 1 0 0 0 .43-.4c.12-.21.14-.48.17-1.03L18 7" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg></span><span>${escapeHtml(t('sidebar.remove'))}</span></button></div>` : ''}
        <div class="project-body${expanded ? '' : ' is-collapsed'}">
            <div class="project-session-list">
                ${
                    visibleSessions.length > 0
                        ? visibleSessions.map(session => {
                            const sessionMetadata = getSessionMetadata(session);
                            const sourceClassName = getSessionSourceClassName(session);
                            return `
                                <div class="session-entry">
                                    <div
                                        class="session-item${sourceClassName}${session.session_id === state.currentSessionId && !state.activeSubagentSession ? ' active' : ''}"
                                        tabindex="0"
                                        role="button"
                                        data-session-id="${escapeHtml(session.session_id)}"
                                        data-workspace-id="${escapeHtml(session.workspace_id || group.workspace?.workspace_id || '')}"
                                    >
                                        <span class="session-main">
                                            ${renderSubagentToggle(session)}
                                            <span class="session-id">${renderSessionSourceIcon(session)}<span class="session-label-text">${escapeHtml(formatSessionLabel(session))}</span></span>
                                        </span>
                                        <span class="session-meta">
                                            <span class="session-time">${escapeHtml(formatRelativeTime(session.updated_at))}</span>
                                            <span class="session-actions">
                                                <button class="session-rename-btn" type="button" data-session-id="${escapeHtml(session.session_id)}" data-session-metadata="${escapeHtml(JSON.stringify(sessionMetadata))}" title="${escapeHtml(t('sidebar.rename_session'))}" aria-label="${escapeHtml(t('sidebar.rename_session'))}">
                                                    <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                                                        <path d="M4 16.5V20h3.5L18 9.5 14.5 6 4 16.5z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/>
                                                        <path d="M13 7.5 16.5 11" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>
                                                        <path d="M12 20h8" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>
                                                    </svg>
                                                </button>
                                                <button class="session-delete-btn" type="button" data-session-id="${escapeHtml(session.session_id)}" title="${escapeHtml(t('sidebar.delete_session'))}" aria-label="${escapeHtml(t('sidebar.delete_session'))}">
                                                    <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                                                        <path d="M5 7h14M9 7V5.8A1.8 1.8 0 0 1 10.8 4h2.4A1.8 1.8 0 0 1 15 5.8V7m-8 0v10.2A1.8 1.8 0 0 0 8.8 19h6.4A1.8 1.8 0 0 0 17 17.2V7M10 10.2v5.6M14 10.2v5.6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>
                                                    </svg>
                                                </button>
                                            </span>
                                        </span>
                                    </div>
                                    ${renderSubagentChildren(session)}
                                </div>
                            `;
                        }).join('')
                        : `
                            <div class="project-empty-sessions">
                                <p>${escapeHtml(t('sidebar.no_sessions'))}</p>
                            </div>
                        `
                }
            </div>
            ${hasHiddenSessions ? `<button class="project-session-visibility-btn" type="button">${sessionsExpanded ? escapeHtml(t('sidebar.collapse')) : escapeHtml(formatMessage('sidebar.show_all', { count: group.sessions.length }))}</button>` : ''}
        </div>
    `;
    bindProjectCard(card, group);
    return card;
}

function prefetchVisibleSubagentSessions(groups) {
    const sessionIds = new Set();
    const currentSessionId = String(state.currentSessionId || '').trim();
    groups.forEach(group => {
        const sessionsExpanded = expandedProjectSessionIds.has(group.key);
        const visibleSessions = visibleSessionsForGroup(group, {
            sessionsExpanded,
        });
        visibleSessions.forEach(session => {
            const sessionId = String(session?.session_id || '').trim();
            if (sessionId && shouldRenderSubagentChildren(session)) {
                sessionIds.add(sessionId);
            }
        });
        if (!currentSessionId) {
            return;
        }
        const currentSession = group.sessions.find(session => String(session?.session_id || '').trim() === currentSessionId);
        if (currentSession && shouldRenderSubagentChildren(currentSession)) {
            sessionIds.add(currentSessionId);
        }
    });
    sessionIds.forEach(sessionId => {
        if (isSubagentSessionListLoading(sessionId)) {
            return;
        }
        void ensureSessionSubagents(sessionId, {
            force: false,
            emitLoadingEvents: false,
        });
    });
}

function visibleSessionsForGroup(group, { sessionsExpanded = false } = {}) {
    if (sessionsExpanded) {
        return group.sessions;
    }
    const visibleSessions = group.sessions.slice(0, DEFAULT_VISIBLE_SESSION_COUNT);
    const pendingSessionId = String(pendingSessionAnimation?.sessionId || '').trim();
    if (!pendingSessionId) {
        return visibleSessions;
    }
    const pendingIndex = group.sessions.findIndex(
        session => String(session?.session_id || '').trim() === pendingSessionId,
    );
    if (pendingIndex < 0 || pendingIndex < DEFAULT_VISIBLE_SESSION_COUNT) {
        return visibleSessions;
    }
    const nextVisibleSessions = [...visibleSessions];
    nextVisibleSessions[nextVisibleSessions.length - 1] = group.sessions[pendingIndex];
    return nextVisibleSessions;
}

export async function loadProjects() {
    if (!els.projectsList) return;
    if (!languageRefreshBound && typeof document.addEventListener === 'function') {
        document.addEventListener('agent-teams-language-changed', () => void loadProjects());
        document.addEventListener('agent-teams-projects-changed', () => void loadProjects());
        document.addEventListener('agent-teams-subagent-sessions-changed', () => void loadProjects());
        document.addEventListener('agent-teams-session-selected', () => void loadProjects());
        document.addEventListener('agent-teams-subagent-session-selected', () => void loadProjects());
        languageRefreshBound = true;
    }
    try {
        ensureProjectMenuDismissBinding();
        const [workspaces, sessions, automationProjects] = await Promise.all([
            fetchWorkspaces(),
            fetchSessions(),
            fetchAutomationProjects(),
        ]);
        automationBoundSessionIds.clear();
        (Array.isArray(automationProjects) ? automationProjects : []).forEach(project => {
            const binding = project?.delivery_binding && typeof project.delivery_binding === 'object'
                ? project.delivery_binding
                : null;
            const sessionId = String(binding?.session_id || '').trim();
            if (sessionId) {
                automationBoundSessionIds.add(sessionId);
            }
        });
        void maybeSyncBackgroundStreams(sessions);
        els.projectsList.innerHTML = '';
        els.projectsList.appendChild(renderFeatureNav());
        els.projectsList.appendChild(renderProjectsToolbar());
        syncProjectSortButton();
        const groups = buildProjectGroups(
            Array.isArray(workspaces) ? workspaces : [],
            Array.isArray(sessions) ? sessions : [],
        );
        if (groups.length === 0) {
            openProjectMenuId = null;
            els.projectsList.appendChild(renderEmptyProjectsState());
            return;
        }
        if (!groups.some(group => group.key === openProjectMenuId)) {
            openProjectMenuId = null;
        }
        groups.forEach(group => els.projectsList.appendChild(renderProjectCard(group)));
        prefetchVisibleSubagentSessions(groups);
        playPendingSessionAnimation();
    } catch (error) {
        sysLog(formatMessage('sidebar.error.loading_projects', { error: error.message }), 'log-error');
    }
}

export function scheduleSessionsRefresh(delayMs = 120) {
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => {
        refreshTimer = null;
        void loadProjects();
    }, delayMs);
}

export function toggleProjectSortMode() {
    projectSortMode = projectSortMode === 'recent' ? 'name' : 'recent';
    syncProjectSortButton();
    void loadProjects();
}

export function setSessionMode() {
    if (els.projectsList) els.projectsList.style.display = 'block';
    els.roundsList.style.display = 'none';
    els.backBtn.style.display = 'none';
}

export function setRoundsMode() {
    if (els.projectsList) els.projectsList.style.display = 'block';
    els.roundsList.style.display = 'none';
    els.backBtn.style.display = 'none';
}

export async function handleNewProjectClick() {
    try {
        let response = null;
        try {
            response = await pickWorkspace();
        } catch (error) {
            if (!isNativeDirectoryPickerUnavailable(error)) throw error;
            const rootPath = await requestWorkspaceRootPath();
            if (!rootPath) return;
            response = await pickWorkspace(rootPath);
        }
        const workspace = response?.workspace || null;
        if (!workspace) return;
        expandedProjectIds.add(groupKey('workspace', workspace.workspace_id));
        state.currentWorkspaceId = workspace.workspace_id;
        sysLog(formatMessage('sidebar.log.added_project', { workspace_id: workspace.workspace_id }));
        await loadProjects();
        await handleNewSessionClick(workspace.workspace_id, true);
    } catch (error) {
        sysLog(formatMessage('sidebar.error.creating_project', { error: error.message }), 'log-error');
    }
}

export async function handleNewAutomationProjectClick() {
    try {
        const payload = await requestAutomationProjectInput();
        if (!payload) return;
        const project = await createAutomationProject(payload);
        state.currentWorkspaceId = String(project?.workspace_id || '').trim() || AUTOMATION_INTERNAL_WORKSPACE_ID;
        sysLog(formatMessage('sidebar.log.created_automation_project', { project_id: project.automation_project_id }));
        await loadProjects();
        await openAutomationHomeView(String(project?.automation_project_id || '').trim());
    } catch (error) {
        sysLog(formatMessage('sidebar.error.creating_automation_project', { error: error.message }), 'log-error');
    }
}

export async function handleForkWorkspaceClick(workspace) {
    const workspaceId = String(workspace?.workspace_id || '').trim();
    if (!workspaceId) return;
    const suggestedName = `${formatWorkspaceLabel(workspace)} ${t('sidebar.fork')}`;
    const enteredName = await showTextInputDialog({
        title: t('sidebar.fork_project'),
        message: t('sidebar.fork_project_message'),
        tone: 'info',
        confirmLabel: t('sidebar.fork'),
        cancelLabel: t('settings.action.cancel'),
        placeholder: t('sidebar.fork_project_placeholder'),
        value: suggestedName,
    });
    const nextName = String(enteredName || '').trim();
    if (!nextName) return;
    try {
        const forkedWorkspace = await forkWorkspace(workspaceId, nextName);
        expandedProjectIds.add(groupKey('workspace', forkedWorkspace.workspace_id));
        expandedProjectSessionIds.add(groupKey('workspace', forkedWorkspace.workspace_id));
        state.currentWorkspaceId = forkedWorkspace.workspace_id;
        openProjectMenuId = null;
        sysLog(formatMessage('sidebar.log.forked_project', { workspace_id: forkedWorkspace.workspace_id }));
        await loadProjects();
        await handleNewSessionClick(forkedWorkspace.workspace_id, true);
    } catch (error) {
        sysLog(formatMessage('sidebar.error.forking_project', { error: error.message }), 'log-error');
    }
}

export async function handleRemoveWorkspaceClick(workspace) {
    const workspaceId = String(workspace?.workspace_id || '').trim();
    if (!workspaceId) return;
    const workspaceLabel = formatWorkspaceLabel(workspace);
    const isWorktreeWorkspace = isForkedWorkspace(workspace);
    const values = await showFormDialog({
        title: t('sidebar.remove_workspace'),
        message: t('sidebar.remove_workspace_message').replace('{workspace}', workspaceLabel),
        tone: 'warning',
        confirmLabel: t('sidebar.remove'),
        cancelLabel: t('settings.action.cancel'),
        fields: [
            {
                id: 'remove_directory',
                label: isWorktreeWorkspace
                    ? t('sidebar.remove_workspace_delete_worktree_label')
                    : t('sidebar.remove_workspace_delete_directory_label'),
                type: 'checkbox',
                value: false,
                description: isWorktreeWorkspace
                    ? t('sidebar.remove_workspace_delete_worktree_message')
                    : t('sidebar.remove_workspace_delete_directory_message'),
            },
        ],
    });
    if (!values) return;
    const removeDirectory = values.remove_directory === true;
    try {
        const sessions = await fetchSessions();
        const workspaceSessions = Array.isArray(sessions) ? sessions.filter(session => String(session?.workspace_id || '') === workspaceId) : [];
        const shouldClearView = workspaceSessions.some(
            session => session.session_id === state.currentSessionId,
        );
        for (const session of workspaceSessions) {
            await deleteSession(session.session_id);
        }
        await deleteWorkspace(workspaceId, { removeDirectory });
        expandedProjectIds.delete(groupKey('workspace', workspaceId));
        expandedProjectSessionIds.delete(groupKey('workspace', workspaceId));
        initializedProjectIds.delete(groupKey('workspace', workspaceId));
        openProjectMenuId = null;
        if (shouldClearView) clearActiveSessionView();
        if (state.currentProjectViewWorkspaceId === workspaceId) hideProjectView();
        if (state.currentWorkspaceId === workspaceId) state.currentWorkspaceId = null;
        await loadProjects();
    } catch (error) {
        sysLog(formatMessage('sidebar.error.removing_project', { error: error.message }), 'log-error');
    }
}

async function handleRemoveAutomationProjectClick(project) {
    const projectId = String(project?.automation_project_id || '').trim();
    const projectLabel = String(project?.display_name || project?.name || projectId).trim() || projectId;
    if (!projectId) return;
    const shouldDelete = await showConfirmDialog({
        title: t('sidebar.remove_automation_title'),
        message: formatMessage('sidebar.remove_automation_message', { project: projectLabel }),
        tone: 'warning',
        confirmLabel: t('sidebar.remove'),
        cancelLabel: t('settings.action.cancel'),
    });
    if (!shouldDelete) return;
    await deleteAutomationProject(projectId);
    expandedProjectIds.delete(groupKey('automation', projectId));
    expandedProjectSessionIds.delete(groupKey('automation', projectId));
    initializedProjectIds.delete(groupKey('automation', projectId));
    openProjectMenuId = null;
    await loadProjects();
}

async function handleToggleAutomationProject(project) {
    const projectId = String(project?.automation_project_id || '').trim();
    const status = String(project?.status || '').trim().toLowerCase();
    if (!projectId) return;
    if (status === 'enabled') {
        await disableAutomationProject(projectId);
        sysLog(formatMessage('sidebar.log.disabled_automation_project', { project_id: projectId }));
    } else {
        await enableAutomationProject(projectId);
        sysLog(formatMessage('sidebar.log.enabled_automation_project', { project_id: projectId }));
    }
    openProjectMenuId = null;
    await loadProjects();
}

async function handleRunAutomationProject(project, manualClick = true) {
    const projectId = String(project?.automation_project_id || '').trim();
    if (!projectId) return;
    const data = await runAutomationProject(projectId);
    state.currentWorkspaceId = String(project?.workspace_id || '').trim() || AUTOMATION_INTERNAL_WORKSPACE_ID;
    if (manualClick) els.chatMessages.innerHTML = '';
    await loadProjects();
    if (data?.reused_bound_session === true) {
        const logMessage = data?.queued === true
            ? formatMessage('sidebar.log.queued_bound_session', { session_id: data.session_id })
            : formatMessage('sidebar.log.started_bound_session', { session_id: data.session_id });
        sysLog(logMessage);
        await openAutomationHomeView(projectId);
        return;
    }
    sysLog(formatMessage('sidebar.log.started_automation_run', { session_id: data.session_id }));
    await selectSessionById(data.session_id);
}

export async function handleNewSessionClick(workspaceId, manualClick = true) {
    const targetWorkspaceId = String(workspaceId || state.currentWorkspaceId || '').trim();
    if (!targetWorkspaceId) {
        sysLog(t('sidebar.error.no_project_selected'), 'log-error');
        return;
    }
    try {
        const data = await startNewSession(targetWorkspaceId);
        state.currentWorkspaceId = targetWorkspaceId;
        sysLog(formatMessage('sidebar.log.created_session', { session_id: data.session_id }));
        if (manualClick) els.chatMessages.innerHTML = '';
        setPendingSessionAnimation(data.session_id, 'entering');
        await loadProjects();
        await selectSessionById(data.session_id);
        animateSessionItem(findSessionItem(data.session_id), 'activating');
    } catch (error) {
        sysLog(formatMessage('sidebar.error.creating_session', { error: error.message }), 'log-error');
    }
}

async function maybeSyncBackgroundStreams(sessions) {
    try {
        const streamCore = await import('../core/stream.js');
        if (typeof streamCore.syncBackgroundStreamsForSessions === 'function') {
            streamCore.syncBackgroundStreamsForSessions(sessions);
        }
    } catch (_error) {
        return;
    }
}
