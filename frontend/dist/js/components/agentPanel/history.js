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

function getSessionAgent(instanceId, roleId) {
    const agents = Array.isArray(state.sessionAgents) ? state.sessionAgents : [];
    return agents.find(agent => String(agent.instance_id || '') === String(instanceId || ''))
        || agents.find(agent => !!roleId && String(agent.role_id || '') === String(roleId || ''))
        || null;
}

function renderRuntimePrompt(panelEl, sessionAgent) {
    const metaEl = panelEl.querySelector('.agent-panel-runtime-prompt-meta');
    const bodyEl = panelEl.querySelector('.agent-panel-runtime-prompt-body');
    if (!metaEl || !bodyEl) return;

    const prompt = String(sessionAgent?.runtime_system_prompt || '').trim();
    const lineCount = prompt ? prompt.split(/\r?\n/).length : 0;
    metaEl.textContent = lineCount > 0 ? `${lineCount} lines` : 'No snapshot yet';
    bodyEl.innerHTML = prompt
        ? `<pre class="agent-panel-runtime-pre">${escapeHtml(prompt)}</pre>`
        : 'No runtime system prompt yet.';
}

function renderRuntimeTools(panelEl, sessionAgent) {
    const metaEl = panelEl.querySelector('.agent-panel-runtime-tools-meta');
    const bodyEl = panelEl.querySelector('.agent-panel-runtime-tools-body');
    if (!metaEl || !bodyEl) return;

    const raw = String(sessionAgent?.runtime_tools_json || '').trim();
    if (!raw) {
        metaEl.textContent = 'No snapshot yet';
        bodyEl.textContent = 'No runtime tools snapshot yet.';
        return;
    }

    let parsed = null;
    try {
        parsed = JSON.parse(raw);
    } catch {
        parsed = null;
    }

    const pretty = parsed ? JSON.stringify(parsed, null, 2) : raw;
    const toolCount = parsed ? countRuntimeTools(parsed) : 0;
    metaEl.textContent = toolCount > 0 ? `${toolCount} tools` : 'JSON snapshot';
    bodyEl.innerHTML = `<pre class="agent-panel-json-pre"><code>${escapeHtml(pretty)}</code></pre>`;
}

function countRuntimeTools(payload) {
    if (!payload || typeof payload !== 'object') return 0;
    const localTools = Array.isArray(payload.local_tools) ? payload.local_tools.length : 0;
    const skillTools = Array.isArray(payload.skill_tools) ? payload.skill_tools.length : 0;
    const mcpTools = Array.isArray(payload.mcp_tools) ? payload.mcp_tools.length : 0;
    return localTools + skillTools + mcpTools;
}

function renderReflection(panelEl, reflection) {
    const metaEl = panelEl.querySelector('.agent-panel-reflection-meta');
    const bodyEl = panelEl.querySelector('.agent-panel-reflection-body');
    if (!metaEl || !bodyEl) return;

    const updatedAt = String(reflection?.updated_at || '').trim();
    const summary = String(reflection?.summary || '').trim();
    metaEl.textContent = updatedAt ? new Date(updatedAt).toLocaleString() : 'No reflection yet';
    bodyEl.dataset.summary = summary;
    bodyEl.dataset.updatedAt = updatedAt;
    bodyEl.dataset.source = String(reflection?.source || 'stored');
    if (bodyEl.dataset.mode === 'editing') return;
    bodyEl.textContent = summary || 'No reflection memory yet.';
}

function renderPanelSummary(panelEl, instanceId, roleId) {
    const statusEl = panelEl.querySelector('.agent-panel-summary-status');
    const updatedEl = panelEl.querySelector('.agent-panel-summary-updated');
    const tasksEl = panelEl.querySelector('.agent-panel-summary-tasks');
    if (!statusEl || !updatedEl || !tasksEl) return;

    const selectedAgent = getSessionAgent(instanceId, roleId);
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

export function syncAgentPanelState(instanceId, roleId = null) {
    const panel = getPanel(instanceId);
    if (!panel) return;
    const sessionAgent = getSessionAgent(instanceId, roleId);
    renderRuntimePrompt(panel.panelEl, sessionAgent);
    renderRuntimeTools(panel.panelEl, sessionAgent);
    renderPanelSummary(panel.panelEl, instanceId, roleId);
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
        syncAgentPanelState(instanceId, roleId);
        renderReflection(panel.panelEl, reflection);
    } catch (e) {
        scrollEl.innerHTML =
            '<div class="panel-empty" style="color:var(--danger)">Failed to load history.</div>';
    }
}
