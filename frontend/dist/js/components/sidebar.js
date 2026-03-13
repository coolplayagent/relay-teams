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

const DEFAULT_VISIBLE_SESSION_COUNT = 6;

let selectSessionHandler = null;
let refreshTimer = null;
const expandedProjectIds = new Set();
const expandedProjectSessionIds = new Set();
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

function formatTimestamp(value) {
    if (!value) {
        return '';
    }
    return new Date(value).toLocaleString();
}

function timestampValue(value) {
    const parsed = Date.parse(String(value || ''));
    return Number.isNaN(parsed) ? 0 : parsed;
}

function sessionStatusLabel(session) {
    const phase = String(session?.active_run_phase || '');
    const status = String(session?.active_run_status || '');
    if (phase === 'awaiting_tool_approval') return 'Awaiting Approval';
    if (phase === 'awaiting_subagent_followup') return 'Awaiting Follow-up';
    if (status === 'running' || phase === 'running') return 'Running';
    if (status === 'queued' || phase === 'queued') return 'Queued';
    if (status === 'stopped' || phase === 'stopped') return 'Stopped';
    return '';
}

function sessionStatusTone(session) {
    const phase = String(session?.active_run_phase || '');
    const status = String(session?.active_run_status || '');
    if (phase === 'awaiting_tool_approval' || phase === 'awaiting_subagent_followup') {
        return 'warning';
    }
    if (status === 'running' || phase === 'running' || status === 'queued' || phase === 'queued') {
        return 'running';
    }
    if (status === 'stopped' || phase === 'stopped') {
        return 'stopped';
    }
    return 'idle';
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
        if (!expandedProjectIds.has(workspaceId)) {
            expandedProjectIds.add(workspaceId);
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
            <p class="projects-empty-copy">Create a project to bind a workspace directory and start sessions inside it.</p>
        </div>
    `;
}

function bindProjectCard(card, group) {
    const { workspace } = group;
    const workspaceId = workspace.workspace_id;
    const toggleBtn = card.querySelector('.project-toggle');
    const newSessionButtons = card.querySelectorAll('.project-new-session-btn');
    const sessionToggleBtn = card.querySelector('.project-session-toggle-btn');
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
        button.onclick = () => {
            void handleNewSessionClick(workspaceId, true);
        };
    });

    if (sessionToggleBtn) {
        sessionToggleBtn.onclick = () => {
            if (expandedProjectSessionIds.has(workspaceId)) {
                expandedProjectSessionIds.delete(workspaceId);
            } else {
                expandedProjectSessionIds.add(workspaceId);
            }
            void loadProjects();
        };
    }

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
    const showAllSessions = expandedProjectSessionIds.has(workspaceId);
    const hiddenSessionCount = Math.max(0, sessions.length - DEFAULT_VISIBLE_SESSION_COUNT);
    const visibleSessions = showAllSessions
        ? sessions
        : sessions.slice(0, DEFAULT_VISIBLE_SESSION_COUNT);

    const card = document.createElement('section');
    card.className = 'project-card';
    card.setAttribute('data-workspace-id', workspaceId);
    card.innerHTML = `
        <div class="project-header">
            <button class="project-toggle" type="button" aria-expanded="${expanded ? 'true' : 'false'}">
                <span class="project-toggle-icon" aria-hidden="true">${expanded ? '&#9662;' : '&#9656;'}</span>
                <span class="project-summary">
                    <span class="project-title-row">
                        <span class="project-title">${escapeHtml(workspaceLabel)}</span>
                        <span class="project-count">${sessions.length}</span>
                    </span>
                    <span class="project-path">${escapeHtml(workspace.root_path)}</span>
                </span>
            </button>
            <button class="project-new-session-btn" type="button" title="Create session">
                <svg viewBox="0 0 24 24" fill="none" class="icon" aria-hidden="true">
                    <path d="M12 4V20M4 12H20" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
                </svg>
            </button>
        </div>
        <div class="project-body${expanded ? '' : ' is-collapsed'}">
            <div class="project-session-list">
                ${
                    visibleSessions.length > 0
                        ? visibleSessions.map(session => {
                            const statusLabel = sessionStatusLabel(session);
                            const approvalCount = Number(session.pending_tool_approval_count || 0);
                            return `
                                <div
                                    class="session-item${session.session_id === state.currentSessionId ? ' active' : ''}"
                                    tabindex="0"
                                    role="button"
                                    data-session-id="${escapeHtml(session.session_id)}"
                                    data-workspace-id="${escapeHtml(session.workspace_id)}"
                                >
                                    <span class="session-main-row">
                                        <span class="session-id">${escapeHtml(session.session_id)}</span>
                                        ${
                                            statusLabel
                                                ? `<span class="session-status-pill session-status-${sessionStatusTone(session)}">${escapeHtml(statusLabel)}</span>`
                                                : ''
                                        }
                                    </span>
                                    <span class="session-sub-row">
                                        <span class="session-time">${escapeHtml(formatTimestamp(session.updated_at))}</span>
                                        ${
                                            approvalCount > 0
                                                ? `<span class="session-side-note">${approvalCount} approval${approvalCount === 1 ? '' : 's'}</span>`
                                                : ''
                                        }
                                    </span>
                                    <span class="session-delete-btn" data-session-id="${escapeHtml(session.session_id)}" title="Delete session">
                                        <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                                            <path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                                        </svg>
                                    </span>
                                </div>
                            `;
                        }).join('')
                        : `
                            <div class="project-empty-sessions">
                                <p>No sessions yet</p>
                                <button class="project-new-session-btn project-new-session-inline" type="button">New session</button>
                            </div>
                        `
                }
            </div>
            ${
                hiddenSessionCount > 0
                    ? `
                        <button class="project-session-toggle-btn" type="button">
                            ${showAllSessions ? 'Show less' : `Show ${hiddenSessionCount} more session${hiddenSessionCount === 1 ? '' : 's'}`}
                        </button>
                    `
                    : ''
            }
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
