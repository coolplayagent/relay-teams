/**
 * components/sidebar.js
 * Renders the left rail as a project tree grouped by workspace and automation projects.
 */
import { els } from '../utils/dom.js';
import { showConfirmDialog, showFormDialog, showTextInputDialog } from '../utils/feedback.js';
import { sysLog } from '../utils/logger.js';
import {
    createAutomationProject,
    deleteAutomationProject,
    deleteSession,
    deleteWorkspace,
    disableAutomationProject,
    enableAutomationProject,
    fetchAutomationProjects,
    fetchSessions,
    fetchWorkspaces,
    forkWorkspace,
    pickWorkspace,
    runAutomationProject,
    startNewSession,
    updateSession,
} from '../core/api.js';
import { state } from '../core/state.js';
import { t } from '../utils/i18n.js';
import { hideProjectView, openAutomationProjectView, openWorkspaceProjectView } from './projectView.js';

const DEFAULT_VISIBLE_SESSION_COUNT = 10;
const AUTOMATION_INTERNAL_WORKSPACE_ID = 'automation-system';

let selectSessionHandler = null;
let refreshTimer = null;
const expandedProjectIds = new Set();
const expandedProjectSessionIds = new Set();
const initializedProjectIds = new Set();
const sessionWorkspaceMap = new Map();
let projectSortMode = 'recent';
let openProjectMenuId = null;
let projectMenuDismissBound = false;
let languageRefreshBound = false;

export function setSelectSessionHandler(handler) {
    selectSessionHandler = handler;
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
    const parts = rootPath.split(/[\/]/).filter(Boolean);
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

function isImSession(session) {
    return String(getSessionMetadata(session).source_kind || '').trim() === 'im';
}

function renderSessionSourceIcon(session) {
    if (!isImSession(session)) {
        return '';
    }
    return `
        <span class="session-source-icon" aria-hidden="true">
            <svg viewBox="0 0 16 16" fill="none" class="icon-sm">
                <path d="M3.25 4.5a2.25 2.25 0 0 1 2.25-2.25h5a2.25 2.25 0 0 1 2.25 2.25v3a2.25 2.25 0 0 1-2.25 2.25H7.4L4.8 11.9a.45.45 0 0 1-.75-.33V9.75h-.55A2.25 2.25 0 0 1 1.25 7.5z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>
                <path d="M5.1 5.95h5.8M5.1 7.85h3.6" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
            </svg>
        </span>
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
    if (diffMinutes < 1) return 'now';
    if (diffMinutes < 60) return `${diffMinutes}m`;
    const diffHours = Math.round(diffMinutes / 60);
    if (diffHours < 24) return `${diffHours}h`;
    const diffDays = Math.round(diffHours / 24);
    if (diffDays < 7) return `${diffDays}d`;
    const diffWeeks = Math.round(diffDays / 7);
    if (diffWeeks < 5) return `${diffWeeks}w`;
    const diffMonths = Math.round(diffDays / 30);
    if (diffMonths < 12) return `${diffMonths}mo`;
    return `${Math.round(diffDays / 365)}y`;
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
    return rootPath || 'Use this workspace directory for automation runs.';
}

function groupKey(kind, id) {
    return `${kind}:${id}`;
}

function sessionGroupKey(session) {
    const projectKind = String(session?.project_kind || '').trim().toLowerCase();
    const projectId = String(session?.project_id || '').trim();
    if (projectKind === 'automation' && projectId) {
        return groupKey('automation', projectId);
    }
    const workspaceId = String(session?.workspace_id || '').trim();
    return workspaceId ? groupKey('workspace', workspaceId) : '';
}

function buildProjectGroups(workspaces, automationProjects, sessions) {
    const sessionsByGroup = new Map();
    sessionWorkspaceMap.clear();

    sessions.forEach(session => {
        const key = sessionGroupKey(session);
        if (!key) return;
        const workspaceId = String(session?.workspace_id || '').trim();
        if (workspaceId) sessionWorkspaceMap.set(session.session_id, workspaceId);
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
    automationProjects.forEach(project => {
        const id = String(project?.automation_project_id || '').trim();
        if (!id) return;
        const key = groupKey('automation', id);
        const projectSessions = Array.from(sessionsByGroup.get(key) || []).sort((a, b) => timestampValue(b.updated_at) - timestampValue(a.updated_at));
        if (!initializedProjectIds.has(key)) {
            initializedProjectIds.add(key);
            expandedProjectIds.add(key);
        }
        groups.push({
            kind: 'automation',
            id,
            key,
            project,
            sessions: projectSessions,
            latestUpdatedAt: Math.max(timestampValue(project.updated_at), timestampValue(project.last_run_started_at), timestampValue(projectSessions[0]?.updated_at)),
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
            <button class="sidebar-header-btn projects-toolbar-new-automation-btn" type="button" title="New automation" aria-label="New automation">
                <svg viewBox="0 0 24 24" fill="none" class="icon" aria-hidden="true">
                    <path d="M12 4v3M12 17v3M4 12h3M17 12h3M6.8 6.8l2.1 2.1M15.1 15.1l2.1 2.1M6.8 17.2l2.1-2.1M15.1 8.9l2.1-2.1" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
                    <circle cx="12" cy="12" r="4" stroke="currentColor" stroke-width="1.8"/>
                </svg>
            </button>
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
    toolbar.querySelector('.projects-toolbar-new-automation-btn')?.addEventListener('click', () => void handleNewAutomationProjectClick());
    toolbar.querySelector('.projects-toolbar-sort-btn')?.addEventListener('click', () => toggleProjectSortMode());
    return toolbar;
}

function isNativeDirectoryPickerUnavailable(error) {
    return error?.status === 503 && error?.detail === 'Native directory picker is unavailable';
}

async function requestWorkspaceRootPath() {
    const enteredPath = await showTextInputDialog({
        title: 'Enter Project Path',
        message: 'Native directory picker is unavailable. Enter an existing project directory path.',
        tone: 'info',
        confirmLabel: t('sidebar.new_project'),
        cancelLabel: t('settings.action.cancel'),
        placeholder: '/path/to/project',
    });
    const rootPath = String(enteredPath || '').trim();
    return rootPath || null;
}

async function requestAutomationProjectInput() {
    const workspaces = await fetchWorkspaces();
    const workspaceOptions = (Array.isArray(workspaces) ? workspaces : []).map(workspace => ({
        value: String(workspace?.workspace_id || '').trim(),
        label: formatWorkspaceOptionLabel(workspace),
        description: formatWorkspaceOptionDescription(workspace),
    })).filter(option => option.value);
    if (workspaceOptions.length === 0) return null;
    const values = await showFormDialog({
        title: 'New Automation Project',
        message: 'Create an automation project and schedule it in one step.',
        tone: 'info',
        confirmLabel: 'Create',
        cancelLabel: 'Cancel',
        fields: [
            {
                id: 'display_name',
                label: 'Project Name',
                placeholder: 'Daily Briefing',
                value: '',
            },
            {
                id: 'workspace_id',
                label: 'Workspace Directory',
                type: 'select',
                value: workspaceOptions[0]?.value || '',
                options: workspaceOptions,
            },
            {
                id: 'prompt',
                label: 'Prompt',
                placeholder: 'Summarize the latest project changes.',
                value: '',
                multiline: true,
            },
            {
                id: 'cron_expression',
                label: 'Cron Schedule',
                placeholder: '0 9 * * *',
                value: '0 9 * * *',
            },
            {
                id: 'timezone',
                label: 'Timezone',
                type: 'select',
                value: 'UTC',
                options: [
                    { value: 'UTC', label: 'UTC' },
                    { value: 'Asia/Shanghai', label: 'Asia/Shanghai' },
                    { value: 'America/Los_Angeles', label: 'America/Los_Angeles' },
                    { value: 'America/New_York', label: 'America/New_York' },
                    { value: 'Europe/London', label: 'Europe/London' },
                ],
            },
            {
                id: 'enabled',
                label: 'Enable Automation',
                type: 'checkbox',
                value: true,
                description: 'Turn on the schedule immediately after creation.',
            },
        ],
    });
    if (!values || typeof values !== 'object') return null;

    const displayName = String(values.display_name || '').trim();
    const workspaceId = String(values.workspace_id || '').trim();
    const prompt = String(values.prompt || '').trim();
    const cronExpression = String(values.cron_expression || '').trim();
    const timezone = String(values.timezone || 'UTC').trim() || 'UTC';
    const enabled = values.enabled !== false;
    if (!workspaceId || !displayName || !prompt || !cronExpression) return null;

    const slug = displayName.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'automation-project';
    return {
        name: slug,
        display_name: displayName,
        workspace_id: workspaceId,
        prompt,
        schedule_mode: 'cron',
        cron_expression: cronExpression,
        timezone,
        enabled,
        run_config: {
            session_mode: 'normal',
            orchestration_preset_id: null,
            execution_mode: 'ai',
            yolo: true,
            thinking: { enabled: false, effort: 'medium' },
        },
    };
}

async function selectSessionById(sessionId) {
    if (!selectSessionHandler) throw new Error('selectSession handler is not configured');
    const workspaceId = sessionWorkspaceMap.get(sessionId);
    if (workspaceId) state.currentWorkspaceId = workspaceId;
    await selectSessionHandler(sessionId);
}

function renderAutomationHint(project) {
    const status = String(project?.status || '').trim() || 'unknown';
    const nextRunAt = String(project?.next_run_at || '').trim();
    return nextRunAt ? `Automation - ${status} - next ${nextRunAt}` : `Automation - ${status}`;
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
        if (group.kind === 'workspace') {
            await openWorkspaceProjectView(group.workspace);
            await loadProjects();
            return;
        }
        await openAutomationProjectView(group.project);
        await loadProjects();
    });
    card.querySelector('.project-new-session-btn')?.addEventListener('click', event => {
        event?.stopPropagation?.();
        if (group.kind === 'workspace') void handleNewSessionClick(projectId, true);
        else void handleRunAutomationProject(group.project, true);
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
        if (group.kind === 'workspace') void handleRemoveWorkspaceClick(group.workspace);
        else void handleRemoveAutomationProjectClick(group.project);
    });
    card.querySelector('.project-run-automation-btn')?.addEventListener('click', event => {
        event?.stopPropagation?.();
        void handleRunAutomationProject(group.project, true);
    });
    card.querySelector('.project-toggle-automation-btn')?.addEventListener('click', event => {
        event?.stopPropagation?.();
        void handleToggleAutomationProject(group.project);
    });

    card.querySelectorAll('.session-item').forEach(button => {
        const selectTarget = () => {
            const sessionId = String(button.getAttribute('data-session-id') || '').trim();
            const targetWorkspaceId = String(button.getAttribute('data-workspace-id') || '').trim();
            if (!sessionId) return;
            state.currentWorkspaceId = targetWorkspaceId || AUTOMATION_INTERNAL_WORKSPACE_ID;
            void selectSessionById(sessionId);
        };
        button.addEventListener('click', selectTarget);
        button.addEventListener('keydown', event => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                selectTarget();
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
                title: 'Rename Session',
                message: 'Enter a new session name.',
                tone: 'info',
                confirmLabel: 'Save',
                cancelLabel: 'Cancel',
                placeholder: 'Session name',
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

    card.querySelectorAll('.session-delete-btn').forEach(button => {
        button.addEventListener('click', async event => {
            event.stopPropagation();
            const sessionId = String(button.getAttribute('data-session-id') || '').trim();
            if (!sessionId) return;
            const shouldDelete = await showConfirmDialog({ title: 'Delete Session', message: `Delete session ${sessionId}?`, tone: 'warning', confirmLabel: 'Delete', cancelLabel: 'Cancel' });
            if (!shouldDelete) return;
            await deleteSession(sessionId);
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
    const visibleSessions = sessionsExpanded ? group.sessions : group.sessions.slice(0, DEFAULT_VISIBLE_SESSION_COUNT);
    const hasHiddenSessions = group.sessions.length > DEFAULT_VISIBLE_SESSION_COUNT;
    const projectViewActive = state.currentMainView === 'project' && (
        (group.kind === 'workspace' && state.currentProjectViewWorkspaceId === projectId) ||
        (group.kind === 'automation' && state.currentProjectViewWorkspaceId === `automation:${projectId}`)
    );
    const pathHint = group.kind === 'workspace' ? String(group.workspace?.root_path || '') : renderAutomationHint(group.project);
    const automationToggleLabel = String(group.project?.status || '').trim().toLowerCase() === 'enabled'
        ? t('sidebar.automation_disable')
        : t('sidebar.automation_enable');
    const projectIcon = group.kind === 'automation'
        ? '<svg viewBox="0 0 24 24" fill="none" class="icon-sm"><path d="M12 4v3M12 17v3M4 12h3M17 12h3M6.8 6.8l2.1 2.1M15.1 15.1l2.1 2.1M6.8 17.2l2.1-2.1M15.1 8.9l2.1-2.1" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><circle cx="12" cy="12" r="4" stroke="currentColor" stroke-width="1.7"/></svg>'
        : '<svg viewBox="0 0 24 24" fill="none" class="icon-sm"><path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H10l2 2h6.5A2.5 2.5 0 0 1 21 9.5v7A2.5 2.5 0 0 1 18.5 19h-13A2.5 2.5 0 0 1 3 16.5z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>';
    const card = document.createElement('section');
    card.className = `project-card${group.kind === 'automation' ? ' automation-project-card' : ''}`;
    card.innerHTML = `
        <div class="project-row">
            <div class="project-title-group">
                <button class="project-toggle" type="button" aria-expanded="${expanded ? 'true' : 'false'}"><span class="project-icon-stack" aria-hidden="true"><span class="project-folder-icon">${projectIcon}</span><span class="project-toggle-icon">${expanded ? '&#9662;' : '&#9656;'}</span></span></button>
                <button class="project-title-btn${projectViewActive ? ' is-active' : ''}" type="button" aria-current="${projectViewActive ? 'page' : 'false'}"><span class="project-title">${escapeHtml(projectLabel)}</span></button>
            </div>
            <div class="project-actions">
                <button class="project-options-btn project-action-btn" type="button" title="${escapeHtml(t('sidebar.project_options'))}" aria-label="${escapeHtml(t('sidebar.project_options'))}"><svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true"><path d="M6 12a1.25 1.25 0 1 0 0 .01M12 12a1.25 1.25 0 1 0 0 .01M18 12a1.25 1.25 0 1 0 0 .01" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></svg></button>
                <button class="project-new-session-btn project-action-btn" type="button" title="${escapeHtml(group.kind === 'workspace' ? t('sidebar.new_session') : t('sidebar.automation_run_now'))}" aria-label="${escapeHtml(group.kind === 'workspace' ? t('sidebar.new_session') : t('sidebar.automation_run_now'))}">${group.kind === 'workspace' ? '<svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" /></svg>' : '<svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true"><path d="M8 6.5v11l9-5.5-9-5.5z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>'}</button>
            </div>
        </div>
        <div class="project-path-hint">${escapeHtml(pathHint)}</div>
        ${menuOpen ? (group.kind === 'workspace'
            ? `<div class="project-menu project-menu-workspace" role="menu"><button class="project-fork-btn project-workspace-menu-btn" type="button" role="menuitem"><span class="project-menu-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" class="icon-sm"><path d="M9 5H6.75A1.75 1.75 0 0 0 5 6.75v10.5C5 18.22 5.78 19 6.75 19h10.5A1.75 1.75 0 0 0 19 17.25V15M15 5h4m0 0v4m0-4-7.5 7.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg></span><span>${escapeHtml(t('sidebar.fork'))}</span></button><button class="project-remove-btn project-workspace-menu-btn project-remove-workspace-btn" type="button" role="menuitem"><span class="project-menu-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" class="icon-sm"><path d="M6 7h12M9 7V5.8c0-.44 0-.66.09-.83a1 1 0 0 1 .42-.42C9.74 4.5 9.96 4.5 10.4 4.5h3.2c.44 0 .66 0 .83.08a1 1 0 0 1 .42.42c.09.17.09.39.09.83V7m-7 0 .55 9.18c.03.55.05.82.17 1.03a1 1 0 0 0 .43.4c.22.1.49.1 1.03.1h4.64c.54 0 .81 0 1.03-.1a1 1 0 0 0 .43-.4c.12-.21.14-.48.17-1.03L18 7" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg></span><span>${escapeHtml(t('sidebar.remove'))}</span></button></div>`
            : `<div class="project-menu project-menu-automation" role="menu"><button class="project-run-automation-btn" type="button" role="menuitem"><span class="project-menu-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" class="icon-sm"><path d="M8 6.5v11l9-5.5-9-5.5z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg></span><span>${escapeHtml(t('sidebar.automation_run_now'))}</span></button><button class="project-toggle-automation-btn" type="button" role="menuitem"><span class="project-menu-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" class="icon-sm"><path d="M12 4v3M12 17v3M4 12h3M17 12h3" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><circle cx="12" cy="12" r="4" stroke="currentColor" stroke-width="1.7"/></svg></span><span>${escapeHtml(automationToggleLabel)}</span></button><button class="project-remove-btn project-remove-automation-btn" type="button" role="menuitem"><span class="project-menu-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" class="icon-sm"><path d="M6 7h12M9 7V5.8c0-.44 0-.66.09-.83a1 1 0 0 1 .42-.42C9.74 4.5 9.96 4.5 10.4 4.5h3.2c.44 0 .66 0 .83.08a1 1 0 0 1 .42.42c.09.17.09.39.09.83V7m-7 0 .55 9.18c.03.55.05.82.17 1.03a1 1 0 0 0 .43.4c.22.1.49.1 1.03.1h4.64c.54 0 .81 0 1.03-.1a1 1 0 0 0 .43-.4c.12-.21.14-.48.17-1.03L18 7" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg></span><span>${escapeHtml(t('sidebar.automation_delete'))}</span></button></div>`)
            : ''}
        <div class="project-body${expanded ? '' : ' is-collapsed'}">
            <div class="project-session-list">
                ${
                    visibleSessions.length > 0
                        ? visibleSessions.map(session => {
                            const sessionMetadata = getSessionMetadata(session);
                            const isIm = isImSession(session);
                            return `
                                <div
                                    class="session-item${isIm ? ' session-item-im' : ''}${session.session_id === state.currentSessionId ? ' active' : ''}"
                                    tabindex="0"
                                    role="button"
                                    data-session-id="${escapeHtml(session.session_id)}"
                                    data-workspace-id="${escapeHtml(session.workspace_id || AUTOMATION_INTERNAL_WORKSPACE_ID)}"
                                >
                                    <span class="session-id">${renderSessionSourceIcon(session)}<span class="session-label-text">${escapeHtml(formatSessionLabel(session))}</span></span>
                                    <span class="session-meta">
                                        <span class="session-time">${escapeHtml(formatRelativeTime(session.updated_at))}</span>
                                        <span class="session-actions">
                                            <button class="session-rename-btn" type="button" data-session-id="${escapeHtml(session.session_id)}" data-session-metadata="${escapeHtml(JSON.stringify(sessionMetadata))}" title="Rename session" aria-label="Rename session">
                                                <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                                                    <path d="M4 16.5V20h3.5L18 9.5 14.5 6 4 16.5z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/>
                                                    <path d="M13 7.5 16.5 11" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>
                                                    <path d="M12 20h8" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>
                                                </svg>
                                            </button>
                                            <button class="session-delete-btn" type="button" data-session-id="${escapeHtml(session.session_id)}" title="Delete session" aria-label="Delete session">
                                                <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                                                    <path d="M5 7h14M9 7V5.8A1.8 1.8 0 0 1 10.8 4h2.4A1.8 1.8 0 0 1 15 5.8V7m-8 0v10.2A1.8 1.8 0 0 0 8.8 19h6.4A1.8 1.8 0 0 0 17 17.2V7M10 10.2v5.6M14 10.2v5.6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>
                                                </svg>
                                            </button>
                                        </span>
                                    </span>
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
            ${hasHiddenSessions ? `<button class="project-session-visibility-btn" type="button">${sessionsExpanded ? 'Collapse' : `Show all (${group.sessions.length})`}</button>` : ''}
        </div>
    `;
    bindProjectCard(card, group);
    return card;
}

export async function loadProjects() {
    if (!els.projectsList) return;
    if (!languageRefreshBound && typeof document.addEventListener === 'function') {
        document.addEventListener('agent-teams-language-changed', () => void loadProjects());
        document.addEventListener('agent-teams-projects-changed', () => void loadProjects());
        languageRefreshBound = true;
    }
    try {
        ensureProjectMenuDismissBinding();
        const [workspaces, automationProjects, sessions] = await Promise.all([
            fetchWorkspaces(),
            fetchAutomationProjects(),
            fetchSessions(),
        ]);
        void maybeSyncBackgroundStreams(sessions);
        els.projectsList.innerHTML = '';
        els.projectsList.appendChild(renderProjectsToolbar());
        syncProjectSortButton();
        const groups = buildProjectGroups(
            Array.isArray(workspaces) ? workspaces : [],
            Array.isArray(automationProjects) ? automationProjects : [],
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
    } catch (error) {
        sysLog(`Error loading projects: ${error.message}`, 'log-error');
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
        sysLog(`Added project: ${workspace.workspace_id}`);
        await loadProjects();
        await handleNewSessionClick(workspace.workspace_id, true);
    } catch (error) {
        sysLog(`Error creating project: ${error.message}`, 'log-error');
    }
}

export async function handleNewAutomationProjectClick() {
    try {
        const payload = await requestAutomationProjectInput();
        if (!payload) return;
        const project = await createAutomationProject(payload);
        expandedProjectIds.add(groupKey('automation', project.automation_project_id));
        expandedProjectSessionIds.add(groupKey('automation', project.automation_project_id));
        state.currentWorkspaceId = String(project?.workspace_id || '').trim() || AUTOMATION_INTERNAL_WORKSPACE_ID;
        sysLog(`Created automation project: ${project.automation_project_id}`);
        await loadProjects();
        await handleRunAutomationProject(project, true);
    } catch (error) {
        sysLog(`Error creating automation project: ${error.message}`, 'log-error');
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
        sysLog(`Forked project: ${forkedWorkspace.workspace_id}`);
        await loadProjects();
        await handleNewSessionClick(forkedWorkspace.workspace_id, true);
    } catch (error) {
        sysLog(`Error forking project: ${error.message}`, 'log-error');
    }
}

export async function handleRemoveWorkspaceClick(workspace) {
    const workspaceId = String(workspace?.workspace_id || '').trim();
    if (!workspaceId) return;
    const workspaceLabel = formatWorkspaceLabel(workspace);
    const shouldDelete = await showConfirmDialog({
        title: t('sidebar.remove_workspace'),
        message: t('sidebar.remove_workspace_message').replace('{workspace}', workspaceLabel),
        tone: 'warning',
        confirmLabel: t('sidebar.remove'),
        cancelLabel: t('settings.action.cancel'),
    });
    if (!shouldDelete) return;
    let removeWorktree = false;
    if (isForkedWorkspace(workspace)) {
        removeWorktree = await showConfirmDialog({
            title: t('sidebar.remove_project_worktree'),
            message: t('sidebar.remove_project_worktree_message').replace('{workspace}', workspaceLabel),
            tone: 'warning',
            confirmLabel: t('sidebar.delete_worktree'),
            cancelLabel: t('sidebar.keep_worktree'),
        });
    }
    try {
        const sessions = await fetchSessions();
        const workspaceSessions = Array.isArray(sessions) ? sessions.filter(session => String(session?.workspace_id || '') === workspaceId) : [];
        for (const session of workspaceSessions) {
            await deleteSession(session.session_id);
        }
        await deleteWorkspace(workspaceId, { removeWorktree });
        expandedProjectIds.delete(groupKey('workspace', workspaceId));
        expandedProjectSessionIds.delete(groupKey('workspace', workspaceId));
        initializedProjectIds.delete(groupKey('workspace', workspaceId));
        openProjectMenuId = null;
        if (state.currentProjectViewWorkspaceId === workspaceId) hideProjectView();
        if (state.currentWorkspaceId === workspaceId) state.currentWorkspaceId = null;
        await loadProjects();
    } catch (error) {
        sysLog(`Error removing project: ${error.message}`, 'log-error');
    }
}

async function handleRemoveAutomationProjectClick(project) {
    const projectId = String(project?.automation_project_id || '').trim();
    const projectLabel = String(project?.display_name || project?.name || projectId).trim() || projectId;
    if (!projectId) return;
    const shouldDelete = await showConfirmDialog({ title: 'Remove Automation Project', message: `Remove automation project ${projectLabel}?`, tone: 'warning', confirmLabel: 'Remove', cancelLabel: 'Cancel' });
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
        sysLog(`Disabled automation project: ${projectId}`);
    } else {
        await enableAutomationProject(projectId);
        sysLog(`Enabled automation project: ${projectId}`);
    }
    openProjectMenuId = null;
    await loadProjects();
}

async function handleRunAutomationProject(project, manualClick = true) {
    const projectId = String(project?.automation_project_id || '').trim();
    if (!projectId) return;
    const data = await runAutomationProject(projectId);
    state.currentWorkspaceId = String(project?.workspace_id || '').trim() || AUTOMATION_INTERNAL_WORKSPACE_ID;
    sysLog(`Started automation run: ${data.session_id}`);
    if (manualClick) els.chatMessages.innerHTML = '';
    await loadProjects();
    await selectSessionById(data.session_id);
}

export async function handleNewSessionClick(workspaceId, manualClick = true) {
    const targetWorkspaceId = String(workspaceId || state.currentWorkspaceId || '').trim();
    if (!targetWorkspaceId) {
        sysLog('No project selected. Create a project first.', 'log-error');
        return;
    }
    try {
        expandedProjectSessionIds.add(groupKey('workspace', targetWorkspaceId));
        const data = await startNewSession(targetWorkspaceId);
        state.currentWorkspaceId = targetWorkspaceId;
        sysLog(`Created new session: ${data.session_id}`);
        if (manualClick) els.chatMessages.innerHTML = '';
        await loadProjects();
        await selectSessionById(data.session_id);
    } catch (error) {
        sysLog(`Error creating session: ${error.message}`, 'log-error');
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

