/**
 * components/subagentRail.js
 * Session-level subagent rail state, selector, and visibility controls.
 */
import { fetchSessionAgents, fetchSessionTasks } from '../core/api.js';
import { isCoordinatorRoleId, state } from '../core/state.js';
import { openAgentPanel } from './agentPanel.js';
import { els } from '../utils/dom.js';
import { sysLog } from '../utils/logger.js';

const RIGHT_RAIL_COLLAPSED_KEY = 'agent_teams_right_rail_collapsed';

export function initializeSubagentRail() {
    const collapsed = localStorage.getItem(RIGHT_RAIL_COLLAPSED_KEY) === '1';
    setSubagentRailExpanded(!collapsed);

    if (els.toggleSubagentsBtn) {
        els.toggleSubagentsBtn.onclick = () => {
            setSubagentRailExpanded(!state.rightRailExpanded);
        };
    }
    if (els.subagentRoleSelect) {
        els.subagentRoleSelect.onchange = (event) => {
            const nextRoleId = String(event?.target?.value || '').trim();
            if (!nextRoleId) return;
            selectSubagentRole(nextRoleId, { reveal: false, forceRefresh: true });
        };
    }

    renderSubagentRail();
}

export async function refreshSubagentRail(
    sessionId = state.currentSessionId,
    { preserveSelection = true } = {},
) {
    const safeSessionId = typeof sessionId === 'string' ? sessionId.trim() : '';
    if (!safeSessionId) {
        state.sessionAgents = [];
        state.sessionTasks = [];
        state.selectedRoleId = null;
        renderSubagentRail();
        return;
    }

    try {
        const [agentsPayload, tasksPayload] = await Promise.all([
            fetchSessionAgents(safeSessionId),
            fetchSessionTasks(safeSessionId),
        ]);
        if (state.currentSessionId !== safeSessionId) return;

        state.sessionAgents = normalizeSessionAgents(agentsPayload);
        state.sessionTasks = normalizeSessionTasks(tasksPayload);
        renderSubagentRail({ preserveSelection });
    } catch (e) {
        sysLog(`Failed to load subagent rail: ${e.message || e}`, 'log-error');
    }
}

export function rememberLiveSubagent(instanceId, roleId) {
    const safeInstanceId = String(instanceId || '').trim();
    const safeRoleId = String(roleId || '').trim();
    if (!safeInstanceId || !safeRoleId || isCoordinatorRoleId(safeRoleId)) return;

    const nowIso = new Date().toISOString();
    const nextAgents = [...(state.sessionAgents || [])];
    const existingIndex = nextAgents.findIndex(agent => agent.role_id === safeRoleId);
    const nextRecord = {
        instance_id: safeInstanceId,
        role_id: safeRoleId,
        status: 'running',
        created_at: existingIndex >= 0 ? nextAgents[existingIndex].created_at : nowIso,
        updated_at: nowIso,
        reflection_summary_preview: existingIndex >= 0 ? nextAgents[existingIndex].reflection_summary_preview : '',
        reflection_updated_at: existingIndex >= 0 ? nextAgents[existingIndex].reflection_updated_at : '',
    };
    if (existingIndex >= 0) {
        nextAgents[existingIndex] = {
            ...nextAgents[existingIndex],
            ...nextRecord,
        };
    } else {
        nextAgents.push(nextRecord);
    }
    state.sessionAgents = normalizeSessionAgents(nextAgents);
    renderSubagentRail({ preserveSelection: true });
}

export function markSubagentStatus(instanceId, status) {
    const safeInstanceId = String(instanceId || '').trim();
    if (!safeInstanceId) return;
    state.sessionAgents = (state.sessionAgents || []).map(agent =>
        agent.instance_id === safeInstanceId
            ? {
                ...agent,
                status: String(status || agent.status || 'idle'),
                updated_at: new Date().toISOString(),
            }
            : agent,
    );
    renderSubagentRail({ preserveSelection: true });
}

export function selectSubagentRole(
    roleId,
    { reveal = false, forceRefresh = false } = {},
) {
    const selected = findAgentByRole(roleId);
    if (!selected) {
        state.selectedRoleId = null;
        renderSubagentRail({ preserveSelection: false });
        return;
    }
    state.selectedRoleId = selected.role_id;
    renderSubagentRail({ preserveSelection: true });
    openAgentPanel(selected.instance_id, selected.role_id, {
        reveal,
        forceRefresh,
    });
}

export function focusSubagent(instanceId, roleId) {
    const safeRoleId = String(roleId || '').trim();
    if (!safeRoleId) return;
    setSubagentRailExpanded(true);
    selectSubagentRole(safeRoleId, { reveal: true, forceRefresh: true });
    if (instanceId) {
        markSubagentStatus(instanceId, 'running');
    }
}

export function syncSelectedRoleByInstance(instanceId, roleId) {
    const safeRoleId = String(roleId || '').trim();
    if (!safeRoleId || isCoordinatorRoleId(safeRoleId)) return;
    state.selectedRoleId = safeRoleId;
    if (els.subagentRoleSelect && els.subagentRoleSelect.value !== safeRoleId) {
        els.subagentRoleSelect.value = safeRoleId;
    }
}

export function setSubagentRailExpanded(expanded) {
    const nextExpanded = expanded !== false;
    state.rightRailExpanded = nextExpanded;
    if (els.rightRail) {
        els.rightRail.classList.toggle('collapsed', !nextExpanded);
    }
    if (els.rightRailResizer) {
        els.rightRailResizer.classList.toggle('hidden', !nextExpanded);
    }
    if (els.toggleSubagentsBtn) {
        els.toggleSubagentsBtn.classList.toggle('active', nextExpanded);
    }
    localStorage.setItem(RIGHT_RAIL_COLLAPSED_KEY, nextExpanded ? '0' : '1');
    updateSubagentSummary();
}

function renderSubagentRail({ preserveSelection = true } = {}) {
    updateSubagentSummary();
    renderRoleSelector({ preserveSelection });
    renderSelectedRoleMeta();
    ensureSelectedPanel({ preserveSelection });
}

function updateSubagentSummary() {
    const roles = Array.isArray(state.sessionAgents) ? state.sessionAgents : [];
    const runningCount = roles.filter(agent => String(agent.status || '') === 'running').length;
    const summary = roles.length === 0
        ? 'idle / 0 roles'
        : `${runningCount} running / ${roles.length} roles`;
    if (els.subagentStatusSummary) {
        els.subagentStatusSummary.textContent = summary;
    }
    if (els.toggleSubagentsBtn) {
        els.toggleSubagentsBtn.innerHTML = `
            <span class="subagent-toggle-label">Subagents</span>
            <span class="subagent-toggle-summary">${summary}</span>
        `;
    }
}

function renderRoleSelector({ preserveSelection = true } = {}) {
    const select = els.subagentRoleSelect;
    if (!select) return;

    const roles = Array.isArray(state.sessionAgents) ? state.sessionAgents : [];
    const selectedRoleId = preserveSelection ? resolveSelectedRoleId() : resolveDefaultRoleId();

    if (roles.length === 0) {
        select.innerHTML = '<option value="">No subagents</option>';
        select.disabled = true;
        state.selectedRoleId = null;
        return;
    }

    select.disabled = false;
    select.innerHTML = roles
        .map(agent => {
            const status = humanizeStatus(agent.status || 'idle');
            return `<option value="${escapeAttribute(agent.role_id)}">${escapeHtml(agent.role_id)} 路 ${escapeHtml(status)}</option>`;
        })
        .join('');
    state.selectedRoleId = selectedRoleId;
    select.value = selectedRoleId || roles[0].role_id;
}

function renderSelectedRoleMeta() {
    const metaEl = els.subagentRoleMeta;
    if (!metaEl) return;
    metaEl.hidden = true;
    metaEl.innerHTML = '';
}

function ensureSelectedPanel({ preserveSelection = true } = {}) {
    const selectedRoleId = preserveSelection ? resolveSelectedRoleId() : resolveDefaultRoleId();
    const selected = findAgentByRole(selectedRoleId);
    if (!selected) return;
    openAgentPanel(selected.instance_id, selected.role_id, {
        reveal: false,
        forceRefresh: false,
    });
}

function resolveSelectedRoleId() {
    const current = String(state.selectedRoleId || '').trim();
    if (current && findAgentByRole(current)) {
        return current;
    }
    return resolveDefaultRoleId();
}

function resolveDefaultRoleId() {
    const roles = Array.isArray(state.sessionAgents) ? state.sessionAgents : [];
    if (roles.length === 0) return null;

    const activeRoleId = String(
        state.pausedSubagent?.roleId
        || state.currentRecoverySnapshot?.pausedSubagent?.roleId
        || state.activeAgentRoleId
        || '',
    ).trim();
    if (activeRoleId && findAgentByRole(activeRoleId)) {
        return activeRoleId;
    }
    return roles[0].role_id;
}

function findAgentByRole(roleId) {
    const safeRoleId = String(roleId || '').trim();
    if (!safeRoleId) return null;
    return (state.sessionAgents || []).find(agent => agent.role_id === safeRoleId) || null;
}

function normalizeSessionAgents(payload) {
    const rows = Array.isArray(payload) ? payload : [];
    const latestByRole = new Map();
    rows.forEach(item => {
        if (!item || typeof item !== 'object') return;
        const roleId = String(item.role_id || '').trim();
        const instanceId = String(item.instance_id || '').trim();
        if (!roleId || !instanceId || isCoordinatorRoleId(roleId)) return;
        const record = {
            instance_id: instanceId,
            role_id: roleId,
            status: String(item.status || 'idle'),
            created_at: String(item.created_at || ''),
            updated_at: String(item.updated_at || item.created_at || ''),
            reflection_summary_preview: String(item.reflection_summary_preview || ''),
            reflection_updated_at: String(item.reflection_updated_at || ''),
        };
        const existing = latestByRole.get(roleId);
        if (!existing || String(record.updated_at).localeCompare(String(existing.updated_at)) >= 0) {
            latestByRole.set(roleId, record);
        }
    });
    return Array.from(latestByRole.values()).sort((left, right) =>
        String(left.role_id || '').localeCompare(String(right.role_id || ''))
    );
}

function normalizeSessionTasks(payload) {
    const rows = Array.isArray(payload) ? payload : [];
    return rows
        .filter(item => item && typeof item === 'object' && !isCoordinatorRoleId(item.role_id))
        .map(item => ({
            task_id: String(item.task_id || ''),
            title: String(item.title || item.task_id || ''),
            role_id: String(item.role_id || ''),
            status: String(item.status || 'created'),
            instance_id: String(item.instance_id || ''),
            run_id: String(item.run_id || ''),
            created_at: String(item.created_at || ''),
            updated_at: String(item.updated_at || item.created_at || ''),
        }));
}

function humanizeStatus(value) {
    const safe = String(value || 'idle').trim();
    if (!safe) return 'Idle';
    return safe.charAt(0).toUpperCase() + safe.slice(1);
}

function shortInstanceId(instanceId) {
    const safe = String(instanceId || '').trim();
    if (!safe) return 'No instance';
    return safe.length > 14 ? `${safe.slice(0, 8)}...${safe.slice(-4)}` : safe;
}

function formatTimestamp(value) {
    const safe = String(value || '').trim();
    if (!safe) return 'No activity yet';
    const parsed = new Date(safe);
    if (Number.isNaN(parsed.getTime())) return safe;
    return parsed.toLocaleString();
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function escapeAttribute(value) {
    return escapeHtml(value);
}
