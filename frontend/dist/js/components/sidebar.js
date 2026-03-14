/**
 * components/sidebar.js
 * Renders the left rail as a project tree grouped by workspace.
 */
import { els } from '../utils/dom.js';
import { showConfirmDialog } from '../utils/feedback.js';
import { sysLog } from '../utils/logger.js';
import {
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
        return {
            workspace,
            sessions: projectSessions,
            latestUpdatedAt: projectSessions[0]?.updated_at || workspace.updated_at,
        };
    });

    return groups.sort(
        (left, right) => timestampValue(right.latestUpdatedAt) - timestampValue(left.latestUpdatedAt),
    );
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

function bindProjectCard(card, group) {
    const { workspace } = group;
    const workspaceId = workspace.workspace_id;
    const toggleBtn = card.querySelector('.project-toggle');
    const newSessionButtons = card.querySelectorAll('.project-new-session-btn');
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
                <button class="project-new-session-btn project-action-btn" type="button" title="New session" aria-label="New session">
                    <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                        <path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
                    </svg>
                </button>
            </div>
        </div>
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
        const [workspaces, sessions] = await Promise.all([
            fetchWorkspaces(),
            fetchSessions(),
        ]);

        els.projectsList.innerHTML = '';

        if (!Array.isArray(workspaces) || workspaces.length === 0) {
            renderEmptyProjectsState();
            return;
        }

        const groups = buildProjectGroups(workspaces, Array.isArray(sessions) ? sessions : []);
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
    } catch (error) {
        sysLog(`Error creating project: ${error.message}`, 'log-error');
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
