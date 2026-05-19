/**
 * components/subagentRail.js
 * Session-level subagent rail state, selector, and visibility controls.
 */
import { fetchSessionAgents, fetchSessionTasks } from '../core/api.js';
import {
    isPrimaryRoleId,
    isReservedSystemRoleId,
    state,
} from '../core/state.js';
import { clearAllPanels, openAgentPanel } from './agentPanel.js';
import { rememberOrchestrationSubagentSession } from './subagentSessions.js';
import { els } from '../utils/dom.js';
import { t } from '../utils/i18n.js';
import { sysLog } from '../utils/logger.js';

const RIGHT_RAIL_COLLAPSED_KEY = 'agent_teams_right_rail_collapsed';
let languageRefreshBound = false;
let subagentRailLoadingSessionId = '';

function clearStoredRightRailCollapsedState() {
    localStorage.removeItem(RIGHT_RAIL_COLLAPSED_KEY);
}

function isPrimaryOrReservedRoleId(roleId) {
    return isPrimaryRoleId(roleId) || isReservedSystemRoleId(roleId);
}

export function initializeSubagentRail() {
    clearStoredRightRailCollapsedState();
    setSubagentRailExpanded(true);

    if (els.subagentRoleSelect) {
        els.subagentRoleSelect.onchange = (event) => {
            const nextRoleId = String(event?.target?.value || '').trim();
            if (!nextRoleId) return;
            selectSubagentRole(nextRoleId, { reveal: false, forceRefresh: true });
        };
    }
    if (!languageRefreshBound && typeof document?.addEventListener === 'function') {
        languageRefreshBound = true;
        document.addEventListener('agent-teams-language-changed', () => {
            clearAllPanels();
            renderSubagentRail({ preserveSelection: true, syncPanel: false });
        });
    }

    renderSubagentRail();
}

export function markSubagentRailLoading(sessionId = state.currentSessionId) {
    const safeSessionId = String(sessionId || '').trim();
    subagentRailLoadingSessionId = safeSessionId;
    renderSubagentRail({ preserveSelection: true, syncPanel: false });
}

export function getLiveSubagentSummary(sessionId = state.currentSessionId) {
    const safeSessionId = String(sessionId || '').trim();
    const currentSessionId = String(state.currentSessionId || '').trim();
    const isCurrentSession = !!safeSessionId && safeSessionId === currentSessionId;
    const roles = isCurrentSession ? getDisplaySessionAgents() : [];
    return {
        isLoading: !!(
            safeSessionId
            && subagentRailLoadingSessionId
            && subagentRailLoadingSessionId === safeSessionId
        ),
        count: roles.length,
        runningCount: countRunningSubagentInstances(roles),
    };
}

export async function refreshSubagentRail(
    sessionId = state.currentSessionId,
    { preserveSelection = true, priority = '', forceRefresh = false, signal = null } = {},
) {
    const safeSessionId = typeof sessionId === 'string' ? sessionId.trim() : '';
    if (!safeSessionId) {
        state.sessionAgents = [];
        state.sessionTasks = [];
        state.selectedRoleId = null;
        renderSubagentRail();
        return;
    }

    subagentRailLoadingSessionId = safeSessionId;
    renderSubagentRail({ preserveSelection, syncPanel: false });

    try {
        const [agentsPayload, tasksPayload] = await Promise.all([
            fetchSessionAgents(safeSessionId, {
                priority,
                forceRefresh: forceRefresh === true,
                signal,
            }),
            fetchSessionTasks(safeSessionId, {
                priority,
                forceRefresh: forceRefresh === true,
                signal,
            }),
        ]);
        if (signal?.aborted) return;
        if (state.currentSessionId !== safeSessionId) return;

        state.sessionTasks = normalizeSessionTasks(tasksPayload);
        state.sessionAgents = reconcileSessionAgentsWithTasks(
            normalizeSessionAgents(agentsPayload),
            state.sessionTasks,
        );
        state.sessionAgents.forEach(agent => {
            rememberOrchestrationSubagentSession(safeSessionId, {
                ...agent,
                subagent_kind: 'orchestration',
                interactive: true,
                deletable: false,
            });
        });
        subagentRailLoadingSessionId = '';
        renderSubagentRail({ preserveSelection, syncPanel: preserveSelection });
    } catch (e) {
        if (e?.name === 'AbortError') return;
        if (state.currentSessionId === safeSessionId) {
            subagentRailLoadingSessionId = '';
            renderSubagentRail({ preserveSelection: true, syncPanel: false });
        }
        sysLog(`Failed to load subagent rail: ${e.message || e}`, 'log-error');
    } finally {
        if (subagentRailLoadingSessionId === safeSessionId && signal?.aborted) {
            subagentRailLoadingSessionId = '';
        }
    }
}

export function rememberLiveSubagent(instanceId, roleId) {
    const safeInstanceId = String(instanceId || '').trim();
    const safeRoleId = String(roleId || '').trim();
    if (!safeInstanceId || !safeRoleId || isPrimaryOrReservedRoleId(safeRoleId)) return;

    const nowIso = new Date().toISOString();
    const nextAgents = [...(state.sessionAgents || [])];
    const existingIndex = nextAgents.findIndex(agent => agent.role_id === safeRoleId);
    const existingRecord = existingIndex >= 0 ? nextAgents[existingIndex] : null;
    const nextRecord = {
        instance_id: safeInstanceId,
        role_id: safeRoleId,
        run_id: String(existingRecord?.run_id || existingRecord?.runId || state.activeRunId || '').trim(),
        status: 'running',
        created_at: existingIndex >= 0 ? existingRecord.created_at : nowIso,
        updated_at: nowIso,
        runtime_system_prompt: existingIndex >= 0 ? existingRecord.runtime_system_prompt : '',
        runtime_tools_json: existingIndex >= 0 ? existingRecord.runtime_tools_json : '',
        reflection_summary_preview: existingIndex >= 0 ? existingRecord.reflection_summary_preview : '',
        reflection_updated_at: existingIndex >= 0 ? existingRecord.reflection_updated_at : '',
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
    if (nextRecord.run_id) {
        rememberOrchestrationSubagentSession(state.currentSessionId, nextRecord);
    }
    renderSubagentRail({ preserveSelection: true, syncPanel: false });
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
    renderSubagentRail({ preserveSelection: true, syncPanel: false });
}

export function selectSubagentRole(
    roleId,
    { reveal = false, forceRefresh = false } = {},
) {
    const selected = findAgentByRole(roleId);
    if (!selected) {
        state.selectedRoleId = null;
        renderSubagentRail({ preserveSelection: false, syncPanel: false });
        return;
    }
    state.selectedRoleId = selected.role_id;
    renderSubagentRail({ preserveSelection: true, syncPanel: false });
    openAgentPanel(selected.instance_id, selected.role_id, {
        reveal,
        forceRefresh,
    });
}

export function openSubagentAgent(
    instanceId,
    roleId,
    { reveal = false, forceRefresh = false, record = null } = {},
) {
    const safeInstanceId = String(instanceId || '').trim();
    const safeRoleId = String(roleId || '').trim();
    if (!safeInstanceId || !safeRoleId || isPrimaryOrReservedRoleId(safeRoleId)) {
        return false;
    }

    const nowIso = new Date().toISOString();
    const existing = (state.sessionAgents || [])
        .find(agent => String(agent?.instance_id || '').trim() === safeInstanceId)
        || (state.sessionAgents || [])
            .find(agent => String(agent?.role_id || '').trim() === safeRoleId)
        || null;
    const nextRecord = {
        instance_id: safeInstanceId,
        role_id: safeRoleId,
        status: String(record?.status || existing?.status || 'idle'),
        created_at: String(record?.created_at || existing?.created_at || nowIso),
        updated_at: String(record?.updated_at || existing?.updated_at || nowIso),
        runtime_system_prompt: String(
            record?.runtime_system_prompt || existing?.runtime_system_prompt || '',
        ),
        runtime_tools_json: String(
            record?.runtime_tools_json || existing?.runtime_tools_json || '',
        ),
        reflection_summary_preview: String(
            record?.reflection_summary_preview || existing?.reflection_summary_preview || '',
        ),
        reflection_updated_at: String(
            record?.reflection_updated_at || existing?.reflection_updated_at || '',
        ),
    };
    const nextAgents = (state.sessionAgents || [])
        .filter(agent =>
            String(agent?.instance_id || '').trim() !== safeInstanceId
            && String(agent?.role_id || '').trim() !== safeRoleId
        );
    nextAgents.push(nextRecord);
    state.sessionAgents = normalizeSessionAgents(nextAgents);
    state.selectedRoleId = safeRoleId;
    setSubagentRailExpanded(true);
    renderSubagentRail({ preserveSelection: true, syncPanel: false });
    openAgentPanel(safeInstanceId, safeRoleId, {
        reveal,
        forceRefresh,
    });
    return true;
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
    if (!safeRoleId || isPrimaryOrReservedRoleId(safeRoleId)) return;
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
    clearStoredRightRailCollapsedState();
    updateSubagentSummary();
}

function renderSubagentRail({ preserveSelection = true, syncPanel = true } = {}) {
    updateSubagentSummary();
    renderRoleSelector({ preserveSelection });
    renderSelectedRoleMeta();
    if (syncPanel) {
        ensureSelectedPanel({ preserveSelection });
    }
    emitLiveSubagentsChanged();
}

function updateSubagentSummary() {
    const roles = getDisplaySessionAgents();
    const isLoading = subagentRailLoadingSessionId
        && subagentRailLoadingSessionId === String(state.currentSessionId || '').trim();
    const runningCount = countRunningSubagentInstances(roles);
    const summary = isLoading && roles.length === 0
        ? t('settings.system.loading_state')
        : roles.length === 0
        ? t('subagent.summary_idle').replace('{roles}', '0')
        : t('subagent.summary_running')
            .replace('{running}', String(runningCount))
            .replace('{roles}', String(roles.length));
    if (els.subagentStatusSummary) {
        els.subagentStatusSummary.textContent = summary;
    }
}

function renderRoleSelector({ preserveSelection = true } = {}) {
    const select = els.subagentRoleSelect;
    if (!select) return;

    const roles = getDisplaySessionAgents();
    const selectedRoleId = preserveSelection ? resolveSelectedRoleId() : resolveDefaultRoleId();
    const isLoading = subagentRailLoadingSessionId
        && subagentRailLoadingSessionId === String(state.currentSessionId || '').trim();

    if (roles.length === 0) {
        const label = isLoading ? t('settings.system.loading_state') : t('subagent.none');
        select.innerHTML = `<option value="">${escapeHtml(label)}</option>`;
        select.disabled = true;
        state.selectedRoleId = null;
        return;
    }

    select.disabled = false;
    select.innerHTML = roles
        .map(agent => {
            const friendly = agent.role_id.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
            return `<option value="${escapeAttribute(agent.role_id)}">${escapeHtml(friendly)}</option>`;
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
    const roles = getDisplaySessionAgents();
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
    return getDisplaySessionAgents().find(agent => agent.role_id === safeRoleId) || null;
}

function normalizeSessionAgents(payload) {
    const rows = Array.isArray(payload) ? payload : [];
    const latestByRole = new Map();
    rows.forEach(item => {
        if (!item || typeof item !== 'object') return;
        const roleId = String(item.role_id || '').trim();
        const instanceId = String(item.instance_id || '').trim();
        if (!roleId || !instanceId || isPrimaryOrReservedRoleId(roleId)) return;
        const record = {
            instance_id: instanceId,
            role_id: roleId,
            run_id: String(item.run_id || item.runId || ''),
            status: String(item.status || 'idle'),
            created_at: String(item.created_at || ''),
            updated_at: String(item.updated_at || item.created_at || ''),
            runtime_system_prompt: String(item.runtime_system_prompt || ''),
            runtime_tools_json: String(item.runtime_tools_json || ''),
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
        .filter(item => {
            if (!item || typeof item !== 'object') return false;
            const assignedRoleId = String(item.assigned_role_id || item.role_id || '').trim();
            return !assignedRoleId || !isPrimaryOrReservedRoleId(assignedRoleId);
        })
        .map(item => ({
            task_id: String(item.task_id || ''),
            title: String(item.title || item.task_id || ''),
            assigned_role_id: String(item.assigned_role_id || item.role_id || ''),
            role_id: String(item.assigned_role_id || item.role_id || ''),
            status: String(item.status || 'created'),
            assigned_instance_id: String(item.assigned_instance_id || item.instance_id || ''),
            instance_id: String(item.assigned_instance_id || item.instance_id || ''),
            run_id: String(item.run_id || ''),
            created_at: String(item.created_at || ''),
            updated_at: String(item.updated_at || item.created_at || ''),
            spec_artifact_id: String(item.spec_artifact_id || ''),
            spec_source_task_id: String(item.spec_source_task_id || ''),
            spec_summary: String(item.spec_summary || ''),
            spec_strictness: String(item.spec_strictness || ''),
            evidence_bundle: item.evidence_bundle && typeof item.evidence_bundle === 'object'
                ? item.evidence_bundle
                : null,
        }));
}

function getDisplaySessionAgents() {
    return reconcileSessionAgentsWithTasks(state.sessionAgents, state.sessionTasks);
}

function reconcileSessionAgentsWithTasks(agentsPayload, tasksPayload) {
    const rows = normalizeSessionAgents(agentsPayload);
    const activeTasks = activeRunningTasks(tasksPayload);
    if (activeTasks.length === 0) {
        return rows;
    }

    const latestByRole = new Map(rows.map(agent => [agent.role_id, agent]));
    activeTasks.forEach(task => {
        const roleId = String(task.assigned_role_id || task.role_id || '').trim();
        const instanceId = String(
            task.assigned_instance_id || task.instance_id || '',
        ).trim();
        if (!roleId || !instanceId || isPrimaryOrReservedRoleId(roleId)) {
            return;
        }
        const existing = latestByRole.get(roleId);
        if (!shouldProjectRunningTask(existing, task, instanceId)) {
            return;
        }
        latestByRole.set(roleId, {
            instance_id: instanceId,
            role_id: roleId,
            run_id: existing?.run_id || task.run_id || '',
            status: 'running',
            created_at: existing?.created_at || task.created_at || '',
            updated_at: latestTimestamp(existing?.updated_at || '', task.updated_at || ''),
            runtime_system_prompt: existing?.runtime_system_prompt || '',
            runtime_tools_json: existing?.runtime_tools_json || '',
            reflection_summary_preview: existing?.reflection_summary_preview || '',
            reflection_updated_at: existing?.reflection_updated_at || '',
        });
    });

    return Array.from(latestByRole.values()).sort((left, right) =>
        String(left.role_id || '').localeCompare(String(right.role_id || ''))
    );
}

function activeRunningTasks(tasksPayload) {
    const rows = Array.isArray(tasksPayload) ? tasksPayload : [];
    return rows.filter(task => {
        if (!task || typeof task !== 'object') {
            return false;
        }
        const status = String(task.status || '').trim().toLowerCase();
        const roleId = String(task.assigned_role_id || task.role_id || '').trim();
        const instanceId = String(
            task.assigned_instance_id || task.instance_id || '',
        ).trim();
        return (
            status === 'running'
            && !!roleId
            && !!instanceId
            && !isPrimaryOrReservedRoleId(roleId)
        );
    });
}

function shouldProjectRunningTask(existing, task, instanceId) {
    if (!existing) {
        return true;
    }
    if (String(existing.status || '').trim().toLowerCase() === 'running') {
        return String(existing.instance_id || '').trim() === instanceId
            || timestampIsAfter(
                task.updated_at || task.created_at || '',
                existing.updated_at || existing.created_at || '',
            );
    }
    return timestampIsAfter(
        task.updated_at || task.created_at || '',
        existing.updated_at || existing.created_at || '',
    );
}

function countRunningSubagentInstances(agents) {
    const runningInstanceIds = new Set();
    const rows = Array.isArray(agents) ? agents : [];
    rows.forEach(agent => {
        if (String(agent?.status || '').trim().toLowerCase() !== 'running') {
            return;
        }
        const instanceId = String(agent?.instance_id || '').trim();
        if (instanceId) {
            runningInstanceIds.add(instanceId);
        }
    });
    return runningInstanceIds.size;
}

function latestTimestamp(left, right) {
    const safeLeft = String(left || '');
    const safeRight = String(right || '');
    if (!safeLeft) return safeRight;
    if (!safeRight) return safeLeft;
    return timestampIsAfter(safeRight, safeLeft) ? safeRight : safeLeft;
}

function timestampIsAfter(left, right) {
    const safeLeft = String(left || '').trim();
    const safeRight = String(right || '').trim();
    if (!safeLeft) return false;
    if (!safeRight) return true;

    const leftMs = Date.parse(safeLeft);
    const rightMs = Date.parse(safeRight);
    if (!Number.isNaN(leftMs) && !Number.isNaN(rightMs)) {
        return leftMs > rightMs;
    }
    return safeLeft.localeCompare(safeRight) > 0;
}

function humanizeStatus(value) {
    const safe = String(value || 'idle').trim();
    if (!safe) return 'Idle';
    return safe.charAt(0).toUpperCase() + safe.slice(1);
}

function shortInstanceId(instanceId) {
    const safe = String(instanceId || '').trim();
    if (!safe) return t('subagent_rail.no_instance');
    return safe.length > 14 ? `${safe.slice(0, 8)}...${safe.slice(-4)}` : safe;
}

function formatTimestamp(value) {
    const safe = String(value || '').trim();
    if (!safe) return t('subagent_rail.no_activity');
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

function emitLiveSubagentsChanged(detail = {}) {
    if (typeof document?.dispatchEvent !== 'function') {
        return;
    }
    document.dispatchEvent(new CustomEvent('agent-teams-live-subagents-changed', {
        detail: {
            sessionId: String(state.currentSessionId || '').trim(),
            selectedRoleId: String(state.selectedRoleId || '').trim(),
            ...(detail || {}),
        },
    }));
}
