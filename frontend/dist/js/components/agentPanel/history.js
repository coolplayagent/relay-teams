/**
 * components/agentPanel/history.js
 * Subagent history loading into an existing panel.
 */
import { fetchAgentMessages, fetchAgentReflection, fetchRunTokenUsage } from '../../core/api.js';
import { state } from '../../core/state.js';
import {
    getInstanceStreamOverlay,
    renderHistoricalMessageList,
} from '../messageRenderer.js';
import {
    getActiveRoundRunId,
    getPanel,
    getPendingApprovalsForPanel,
} from './state.js';

function renderTokenBadge(panelEl, instanceId, runUsage) {
    const badgeEl = panelEl.querySelector(
        `.agent-token-usage[data-instance-id="${instanceId}"]`
    );
    if (!badgeEl) return;
    if (!runUsage) {
        badgeEl.innerHTML = '';
        return;
    }
    const agent = (runUsage.by_agent || []).find(a => a.instance_id === instanceId);
    if (!agent || agent.total_tokens === 0) {
        badgeEl.innerHTML = '';
        return;
    }
    const fmt = n => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));
    badgeEl.innerHTML = `
        <span class="token-badge" title="Input: ${agent.input_tokens} | Output: ${agent.output_tokens} | Requests: ${agent.requests}">
            <span class="token-in">In ${fmt(agent.input_tokens)}</span>
            <span class="token-out">Out ${fmt(agent.output_tokens)}</span>
        </span>
    `;
}

function renderReflection(panelEl, reflection) {
    const metaEl = panelEl.querySelector('.agent-panel-reflection-meta');
    const bodyEl = panelEl.querySelector('.agent-panel-reflection-body');
    if (!metaEl || !bodyEl) return;

    const updatedAt = String(reflection?.updated_at || '').trim();
    metaEl.textContent = updatedAt ? new Date(updatedAt).toLocaleString() : 'No reflection yet';
    bodyEl.textContent = String(reflection?.summary || '').trim() || 'No reflection memory yet.';
}


function renderPanelSummary(panelEl, instanceId, roleId) {
    const statusEl = panelEl.querySelector('.agent-panel-summary-status');
    const updatedEl = panelEl.querySelector('.agent-panel-summary-updated');
    const tasksEl = panelEl.querySelector('.agent-panel-summary-tasks');
    if (!statusEl || !updatedEl || !tasksEl) return;

    const agents = Array.isArray(state.sessionAgents) ? state.sessionAgents : [];
    const selectedAgent = agents.find(agent => String(agent.instance_id || '') === String(instanceId || ''))
        || agents.find(agent => !!roleId && String(agent.role_id || '') === String(roleId || ''))
        || null;
    const status = humanizeStatus(selectedAgent?.status || 'idle');
    statusEl.textContent = status;
    statusEl.className = `agent-panel-summary-status is-${escapeAttribute(String(selectedAgent?.status || 'idle'))}`;
    updatedEl.textContent = formatTimestamp(selectedAgent?.updated_at || selectedAgent?.created_at || '');

    const tasks = (state.sessionTasks || [])
        .filter(task => {
            const taskInstanceId = String(task?.instance_id || '').trim();
            if (taskInstanceId && taskInstanceId === String(instanceId || '').trim()) return true;
            return !!roleId && String(task?.role_id || '').trim() === String(roleId || '').trim();
        })
        .slice()
        .sort((left, right) => compareTimelineDesc(left, right))
        .slice(0, 4);

    if (tasks.length === 0) {
        tasksEl.innerHTML = '<div class="agent-panel-summary-empty">No delegated tasks yet.</div>';
        return;
    }

    tasksEl.innerHTML = tasks.map(task => `
        <div class="agent-panel-summary-task-row">
            <span class="agent-panel-summary-task-title">${escapeHtml(task.title || task.task_id || 'Task')}</span>
            <span class="agent-panel-summary-task-state is-${escapeAttribute(String(task.status || 'created'))}">
                ${escapeHtml(humanizeStatus(task.status || 'created'))}
            </span>
        </div>
    `).join('');
}

function humanizeStatus(value) {
    const safeValue = String(value || 'idle').trim();
    if (!safeValue) return 'Idle';
    return safeValue.charAt(0).toUpperCase() + safeValue.slice(1);
}

function formatTimestamp(value) {
    const safeValue = String(value || '').trim();
    if (!safeValue) return '';
    const parsed = new Date(safeValue);
    if (Number.isNaN(parsed.getTime())) return safeValue;
    return parsed.toLocaleString();
}


function compareTimelineDesc(left, right) {
    const leftTs = parseTimelineValue(left?.updated_at || left?.created_at || '');
    const rightTs = parseTimelineValue(right?.updated_at || right?.created_at || '');
    if (leftTs !== rightTs) {
        return rightTs - leftTs;
    }
    return String(right?.task_id || '').localeCompare(String(left?.task_id || ''));
}

function parseTimelineValue(value) {
    const safeValue = String(value || '').trim();
    if (!safeValue) return 0;
    const parsed = Date.parse(safeValue);
    return Number.isNaN(parsed) ? 0 : parsed;
}

function escapeHtml(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function escapeAttribute(value) {
    return escapeHtml(value).replaceAll('`', '&#96;');
}

export async function loadAgentHistory(instanceId, roleId = null) {
    const panel = getPanel(instanceId);
    if (!panel) return;
    const scrollEl = panel.scrollEl;
    const runId = state.activeRunId || getActiveRoundRunId();
    try {
        scrollEl.innerHTML = '<div class="panel-loading">Loading history...</div>';
        const [messages, runUsage, reflection] = await Promise.all([
            fetchAgentMessages(state.currentSessionId, instanceId),
            runId && runId !== '__live__'
                ? fetchRunTokenUsage(state.currentSessionId, runId)
                : Promise.resolve(null),
            fetchAgentReflection(state.currentSessionId, instanceId),
        ]);
        const recoveryApprovals = (
            state.currentRecoverySnapshot?.pendingToolApprovals || []
        ).filter(item => {
            const itemInstance = String(item?.instance_id || '');
            if (itemInstance && itemInstance === instanceId) return true;
            const itemRole = String(item?.role_id || '');
            return !!roleId && itemRole === roleId;
        });
        const pendingToolApprovals = [
            ...getPendingApprovalsForPanel(instanceId, roleId),
            ...recoveryApprovals,
        ];
        const streamOverlayEntry = getInstanceStreamOverlay(runId, instanceId);
        scrollEl.innerHTML = '';
        if (
            messages.length === 0
            && pendingToolApprovals.length === 0
            && !streamOverlayEntry
        ) {
            scrollEl.innerHTML = '<div class="panel-empty">No messages yet.</div>';
        } else {
            renderHistoricalMessageList(scrollEl, messages, {
                pendingToolApprovals,
                runId,
                streamOverlayEntry,
            });
        }
        panel.loadedSessionId = state.currentSessionId || '';
        panel.loadedRunId = runId || '';
        renderTokenBadge(panel.panelEl, instanceId, runUsage);
        renderReflection(panel.panelEl, reflection);
        renderPanelSummary(panel.panelEl, instanceId, roleId);
    } catch (e) {
        scrollEl.innerHTML =
            '<div class="panel-empty" style="color:var(--danger)">Failed to load history.</div>';
    }
}
