/**
 * components/sidebar.js
 * Renders the left rail as a project tree grouped by workspace.
 */
import { els } from '../utils/dom.js';
import { showConfirmDialog } from '../utils/feedback.js';
import { sysLog } from '../utils/logger.js';
import {
    deleteWorkspace,
    deleteSession,
    fetchSessions,
    fetchWorkspaces,
    pickWorkspace,
    startNewSession,
} from '../core/api.js';
import { state } from '../core/state.js';

let selectSessionHandler = null;
let refreshTimer = null;
const expandedProjectIds = new Set();
const initializedProjectIds = new Set();
const sessionWorkspaceMap = new Map();
let projectSortMode = 'recent';
let openProjectMenuId = null;
let projectMenuDismissBound = false;

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

function formatProjectLabel(workspace) {
    const rootPath = String(workspace?.root_path || '').trim();
    if (!rootPath) {
        return String(workspace?.workspace_id || 'Project');
    }
    const parts = rootPath.split(/[\\/]/).filter(Boolean);
    return parts.at(-1) || String(workspace?.workspace_id || 'Project');
}

function formatSessionLabel(session) {
    const metadata = session?.metadata && typeof session.metadata === 'object'
        ? session.metadata
        : {};
    const keys = ['title', 'name', 'label'];
    for (const key of keys) {
        const label = String(metadata[key] || '').trim();
        if (label) {
            return label;
        }
    }
    return String(session?.session_id || 'Session');
}

function timestampValue(value) {
    const parsed = Date.parse(String(value || ''));
    return Number.isNaN(parsed) ? 0 : parsed;
}

function formatRelativeTime(value) {
    const timestamp = timestampValue(value);
    if (!timestamp) {
        return '';
    }

    const diffMs = Date.now() - timestamp;
    const diffMinutes = Math.max(0, Math.round(diffMs / 60000));
    if (diffMinutes < 1) {
        return 'now';
    }
    if (diffMinutes < 60) {
        return `${diffMinutes}m`;
    }
    const diffHours = Math.round(diffMinutes / 60);
    if (diffHours < 24) {
        return `${diffHours}h`;
    }
    const diffDays = Math.round(diffHours / 24);
    if (diffDays < 7) {
        return `${diffDays}d`;
    }
    const diffWeeks = Math.round(diffDays / 7);
    if (diffWeeks < 5) {
        return `${diffWeeks}w`;
    }
    const diffMonths = Math.round(diffDays / 30);
    if (diffMonths < 12) {
        return `${diffMonths}mo`;
    }
    const diffYears = Math.round(diffDays / 365);
    return `${diffYears}y`;
}

function buildProjectGroups(workspaces, sessions) {
    const sessionsByWorkspace = new Map();
    sessionWorkspaceMap.clear();

    sessions.forEach(session => {
        const workspaceId = String(session?.workspace_id || '').trim();
        if (!workspaceId) {
            return;
        }
        sessionWorkspaceMap.set(session.session_id, workspaceId);
        if (!sessionsByWorkspace.has(workspaceId)) {
            sessionsByWorkspace.set(workspaceId, []);
        }
        sessionsByWorkspace.get(workspaceId).push(session);
    });

    const groups = workspaces.map(workspace => {
        const workspaceId = String(workspace.workspace_id || '').trim();
        const projectSessions = Array.from(sessionsByWorkspace.get(workspaceId) || []).sort(
            (left, right) => timestampValue(right.updated_at) - timestampValue(left.updated_at),
        );
        if (!initializedProjectIds.has(workspaceId)) {
            expandedProjectIds.add(workspaceId);
            initializedProjectIds.add(workspaceId);
        }
        const latestWorkspaceTimestamp = Math.max(
            timestampValue(projectSessions[0]?.updated_at),
            timestampValue(workspace.updated_at),
        );
        return {
            workspace,
            sessions: projectSessions,
            latestUpdatedAt: latestWorkspaceTimestamp,
        };
    });

    if (projectSortMode === 'name') {
        return groups.sort((left, right) => {
            const leftLabel = formatProjectLabel(left.workspace).toLowerCase();
            const rightLabel = formatProjectLabel(right.workspace).toLowerCase();
            return leftLabel.localeCompare(rightLabel);
        });
    }

    return groups.sort(
        (left, right) => Number(right.latestUpdatedAt) - Number(left.latestUpdatedAt),
    );
}

function syncProjectSortButton() {
    if (!els.projectSortBtn) {
        return;
    }
    const sortLabel = projectSortMode === 'name' ? 'Sort by name' : 'Sort by recent';
    els.projectSortBtn.title = sortLabel;
    els.projectSortBtn.setAttribute('aria-label', sortLabel);
    els.projectSortBtn.dataset.sortMode = projectSortMode;
    els.projectSortBtn.innerHTML = projectSortMode === 'name'
        ? `
            <svg viewBox="0 0 24 24" fill="none" class="icon" aria-hidden="true">
                <path d="M6 7h8M6 12h6M6 17h10" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
                <path d="M17 6l2-2 2 2M19 4v16" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
        `
        : `
            <svg viewBox="0 0 24 24" fill="none" class="icon" aria-hidden="true">
                <path d="M7 6h10M7 12h7M7 18h4" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
                <path d="M17 8l2-2 2 2M19 6v12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
        `;
}

async function selectSessionById(sessionId) {
    if (!selectSessionHandler) {
        throw new Error('selectSession handler is not configured');
    }
    const workspaceId = sessionWorkspaceMap.get(sessionId);
    if (workspaceId) {
        state.currentWorkspaceId = workspaceId;
    }
    await selectSessionHandler(sessionId);
}

function renderEmptyProjectsState() {
    if (!els.projectsList) {
        return;
    }
    els.projectsList.innerHTML = `
        <div class="projects-empty-state">
            <p class="projects-empty-title">No projects yet</p>
            <p class="projects-empty-copy">Add a project below to attach a workspace and start sessions.</p>
        </div>
    `;
}

function ensureProjectMenuDismissBinding() {
    if (
        projectMenuDismissBound
        || typeof document === 'undefined'
        || typeof document.addEventListener !== 'function'
    ) {
        return;
    }
    document.addEventListener('click', event => {
        const target = event?.target;
        if (target?.closest?.('.project-options-btn, .project-menu')) {
            return;
        }
        if (openProjectMenuId !== null) {
            openProjectMenuId = null;
            void loadProjects();
        }
    });
    projectMenuDismissBound = true;
}

function bindProjectCard(card, group) {
    const { workspace } = group;
    const workspaceId = workspace.workspace_id;
    const toggleBtn = card.querySelector('.project-toggle');
    const newSessionButtons = card.querySelectorAll('.project-new-session-btn');
    const optionsButtons = card.querySelectorAll('.project-options-btn');
    const removeButtons = card.querySelectorAll('.project-remove-btn');
    const deleteButtons = card.querySelectorAll('.session-delete-btn');
    const sessionButtons = card.querySelectorAll('.session-item');

    if (toggleBtn) {
        toggleBtn.onclick = () => {
            if (expandedProjectIds.has(workspaceId)) {
                expandedProjectIds.delete(workspaceId);
            } else {
                expandedProjectIds.add(workspaceId);
            }
            void loadProjects();
        };
    }

    newSessionButtons.forEach(button => {
        button.onclick = event => {
            event?.stopPropagation?.();
            void handleNewSessionClick(workspaceId, true);
        };
    });

    optionsButtons.forEach(button => {
        button.onclick = event => {
            event?.stopPropagation?.();
            openProjectMenuId = openProjectMenuId === workspaceId ? null : workspaceId;
            void loadProjects();
        };
    });

    removeButtons.forEach(button => {
        button.onclick = async event => {
            event?.stopPropagation?.();
            void handleRemoveWorkspaceClick(workspace);
        };
    });

    sessionButtons.forEach(button => {
        const selectTarget = () => {
            const sessionId = String(button.getAttribute('data-session-id') || '').trim();
            const targetWorkspaceId = String(button.getAttribute('data-workspace-id') || '').trim();
            if (!sessionId) {
                return;
            }
            state.currentWorkspaceId = targetWorkspaceId || workspaceId;
            void selectSessionById(sessionId);
        };
        button.onclick = selectTarget;
        button.onkeydown = event => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                selectTarget();
            }
        };
    });

    deleteButtons.forEach(button => {
        button.onclick = async event => {
            event.stopPropagation();
            const sessionId = String(button.getAttribute('data-session-id') || '').trim();
            if (!sessionId) {
                return;
            }
            const shouldDelete = await showConfirmDialog({
                title: 'Delete Session',
                message: `Delete session ${sessionId}?`,
                tone: 'warning',
                confirmLabel: 'Delete',
                cancelLabel: 'Cancel',
            });
            if (!shouldDelete) {
                return;
            }
            try {
                const deletedActiveSession = sessionId === state.currentSessionId;
                await deleteSession(sessionId);
                if (deletedActiveSession) {
                    state.currentSessionId = null;
                    state.currentWorkspaceId = workspaceId;
                }
                await loadProjects();
                if (deletedActiveSession) {
                    const nextSessionEl = document.querySelector('.session-item');
                    const nextSessionId = String(
                        nextSessionEl?.getAttribute('data-session-id') || '',
                    ).trim();
                    if (nextSessionId) {
                        await selectSessionById(nextSessionId);
                    } else {
                        els.chatMessages.innerHTML = '';
                    }
                }
            } catch (error) {
                sysLog(`Error deleting session: ${error.message}`, 'log-error');
            }
        };
    });
}

function renderProjectCard(group) {
    const { workspace, sessions } = group;
    const workspaceId = workspace.workspace_id;
    const workspaceLabel = formatProjectLabel(workspace);
    const expanded = expandedProjectIds.has(workspaceId);
    const menuOpen = openProjectMenuId === workspaceId;

    const card = document.createElement('section');
    card.className = 'project-card';
    card.setAttribute('data-workspace-id', workspaceId);
    card.innerHTML = `
        <div class="project-row">
            <button class="project-toggle" type="button" aria-expanded="${expanded ? 'true' : 'false'}">
                <span class="project-icon-stack" aria-hidden="true">
                    <span class="project-folder-icon">
                        <svg viewBox="0 0 24 24" fill="none" class="icon-sm">
                            <path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H10l2 2h6.5A2.5 2.5 0 0 1 21 9.5v7A2.5 2.5 0 0 1 18.5 19h-13A2.5 2.5 0 0 1 3 16.5z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/>
                        </svg>
                    </span>
                    <span class="project-toggle-icon" aria-hidden="true">${expanded ? '&#9662;' : '&#9656;'}</span>
                </span>
                <span class="project-title">${escapeHtml(workspaceLabel)}</span>
            </button>
            <div class="project-actions">
                <button class="project-options-btn project-action-btn" type="button" title="Project options" aria-label="Project options">
                    <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                        <path d="M6 12a1.25 1.25 0 1 0 0 .01M12 12a1.25 1.25 0 1 0 0 .01M18 12a1.25 1.25 0 1 0 0 .01" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
                    </svg>
                </button>
                <button class="project-new-session-btn project-action-btn" type="button" title="New session" aria-label="New session">
                    <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                        <path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
                    </svg>
                </button>
            </div>
        </div>
        <div class="project-path-hint">${escapeHtml(String(workspace.root_path || ''))}</div>
        ${
            menuOpen
                ? `
                    <div class="project-menu" role="menu">
                        <button class="project-remove-btn" type="button" role="menuitem">
                            <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                                <path d="M5 7h14M9 7V5.8A1.8 1.8 0 0 1 10.8 4h2.4A1.8 1.8 0 0 1 15 5.8V7m-8 0v10.2A1.8 1.8 0 0 0 8.8 19h6.4A1.8 1.8 0 0 0 17 17.2V7" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>
                            </svg>
                            <span>Remove</span>
                        </button>
                    </div>
                `
                : ''
        }
        <div class="project-body${expanded ? '' : ' is-collapsed'}">
            <div class="project-session-list">
                ${
                    sessions.length > 0
                        ? sessions.map(session => {
                            return `
                                <div
                                    class="session-item${session.session_id === state.currentSessionId ? ' active' : ''}"
                                    tabindex="0"
                                    role="button"
                                    data-session-id="${escapeHtml(session.session_id)}"
                                    data-workspace-id="${escapeHtml(session.workspace_id)}"
                                >
                                    <span class="session-id">${escapeHtml(formatSessionLabel(session))}</span>
                                    <span class="session-meta">
                                        <span class="session-time">${escapeHtml(formatRelativeTime(session.updated_at))}</span>
                                        <button class="session-delete-btn" type="button" data-session-id="${escapeHtml(session.session_id)}" title="Delete session" aria-label="Delete session">
                                            <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                                                <path d="M5 7h14M9 7V5.8A1.8 1.8 0 0 1 10.8 4h2.4A1.8 1.8 0 0 1 15 5.8V7m-8 0v10.2A1.8 1.8 0 0 0 8.8 19h6.4A1.8 1.8 0 0 0 17 17.2V7M10 10.2v5.6M14 10.2v5.6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>
                                            </svg>
                                        </button>
                                    </span>
                                </div>
                            `;
                        }).join('')
                        : `
                            <div class="project-empty-sessions">
                                <p>No sessions yet</p>
                            </div>
                        `
                }
            </div>
        </div>
    `;

    bindProjectCard(card, group);
    return card;
}

export async function loadProjects() {
    if (!els.projectsList) {
        return;
    }

    try {
        ensureProjectMenuDismissBinding();
        syncProjectSortButton();
        const [workspaces, sessions] = await Promise.all([
            fetchWorkspaces(),
            fetchSessions(),
        ]);

        els.projectsList.innerHTML = '';

        if (!Array.isArray(workspaces) || workspaces.length === 0) {
            openProjectMenuId = null;
            renderEmptyProjectsState();
            return;
        }

        const groups = buildProjectGroups(workspaces, Array.isArray(sessions) ? sessions : []);
        if (!groups.some(group => group.workspace.workspace_id === openProjectMenuId)) {
            openProjectMenuId = null;
        }
        groups.forEach(group => {
            els.projectsList.appendChild(renderProjectCard(group));
        });
    } catch (error) {
        sysLog(`Error loading projects: ${error.message}`, 'log-error');
    }
}

export function scheduleSessionsRefresh(delayMs = 120) {
    if (refreshTimer) {
        clearTimeout(refreshTimer);
    }
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
    if (els.projectsList) {
        els.projectsList.style.display = 'block';
    }
    els.roundsList.style.display = 'none';
    els.backBtn.style.display = 'none';
}

export function setRoundsMode() {
    if (els.projectsList) {
        els.projectsList.style.display = 'block';
    }
    els.roundsList.style.display = 'none';
    els.backBtn.style.display = 'none';
}

export async function handleNewProjectClick() {
    try {
        const response = await pickWorkspace();
        const workspace = response?.workspace || null;
        if (!workspace) {
            return;
        }
        expandedProjectIds.add(workspace.workspace_id);
        state.currentWorkspaceId = workspace.workspace_id;
        sysLog(`Added project: ${workspace.workspace_id}`);
        await loadProjects();
        await handleNewSessionClick(workspace.workspace_id, true);
    } catch (error) {
        sysLog(`Error creating project: ${error.message}`, 'log-error');
    }
}

export async function handleRemoveWorkspaceClick(workspace) {
    const workspaceId = String(workspace?.workspace_id || '').trim();
    if (!workspaceId) {
        return;
    }
    const workspaceLabel = formatProjectLabel(workspace);
    const shouldDelete = await showConfirmDialog({
        title: 'Remove Workspace',
        message: `Remove workspace ${workspaceLabel}? This will also delete its sessions from the sidebar.`,
        tone: 'warning',
        confirmLabel: 'Remove',
        cancelLabel: 'Cancel',
    });
    if (!shouldDelete) {
        return;
    }

    try {
        const sessions = await fetchSessions();
        const workspaceSessions = Array.isArray(sessions)
            ? sessions.filter(session => String(session?.workspace_id || '') === workspaceId)
            : [];
        const removedCurrentSession = workspaceSessions.some(
            session => session.session_id === state.currentSessionId,
        );

        for (const session of workspaceSessions) {
            await deleteSession(session.session_id);
        }
        await deleteWorkspace(workspaceId);

        expandedProjectIds.delete(workspaceId);
        initializedProjectIds.delete(workspaceId);
        sessionWorkspaceMap.forEach((value, key) => {
            if (value === workspaceId) {
                sessionWorkspaceMap.delete(key);
            }
        });
        openProjectMenuId = null;

        if (state.currentWorkspaceId === workspaceId) {
            state.currentWorkspaceId = null;
        }
        if (removedCurrentSession) {
            state.currentSessionId = null;
        }

        await loadProjects();

        if (removedCurrentSession) {
            const nextSessionEl = document.querySelector('.session-item');
            const nextSessionId = String(
                nextSessionEl?.getAttribute('data-session-id') || '',
            ).trim();
            if (nextSessionId) {
                await selectSessionById(nextSessionId);
            } else {
                els.chatMessages.innerHTML = '';
            }
        }
    } catch (error) {
        sysLog(`Error removing project: ${error.message}`, 'log-error');
    }
}

export async function handleNewSessionClick(workspaceId, manualClick = true) {
    const targetWorkspaceId = String(workspaceId || state.currentWorkspaceId || '').trim();
    if (!targetWorkspaceId) {
        sysLog('No project selected. Create a project first.', 'log-error');
        return;
    }
    try {
        const data = await startNewSession(targetWorkspaceId);
        state.currentWorkspaceId = targetWorkspaceId;
        sysLog(`Created new session: ${data.session_id}`);

        if (manualClick) {
            els.chatMessages.innerHTML = '';
        }

        await loadProjects();
        await selectSessionById(data.session_id);
    } catch (error) {
        sysLog(`Error creating session: ${error.message}`, 'log-error');
    }
}
