/**
 * components/agentPanel/history.js
 * Subagent history loading into an existing panel.
 */
import { fetchAgentMessages, fetchRunTokenUsage } from '../../core/api.js';
import { state } from '../../core/state.js';
import { renderHistoricalMessageList } from '../messageRenderer.js';
import {
    getActiveRoundRunId,
    getPanel,
    getPendingApprovalsForPanel,
    getPendingStreamTextForPanel,
} from './state.js';

function renderTokenBadge(panelEl, instanceId, runUsage) {
    const badgeEl = panelEl.querySelector(`.agent-token-usage[data-instance-id="${instanceId}"]`);
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
    const fmt = n => n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
    badgeEl.innerHTML = `
        <span class="token-badge" title="Input: ${agent.input_tokens} | Output: ${agent.output_tokens} | Requests: ${agent.requests}">
            <span class="token-in">↑${fmt(agent.input_tokens)}</span>
            <span class="token-out">↓${fmt(agent.output_tokens)}</span>
        </span>`;
}

export async function loadAgentHistory(instanceId, roleId = null) {
    const panel = getPanel(instanceId);
    if (!panel) return;
    const scrollEl = panel.scrollEl;
    try {
        scrollEl.innerHTML = '<div class="panel-loading">Loading history...</div>';
        const runId = getActiveRoundRunId();
        const [messages, runUsage] = await Promise.all([
            fetchAgentMessages(state.currentSessionId, instanceId),
            runId && runId !== '__live__' ? fetchRunTokenUsage(state.currentSessionId, runId) : Promise.resolve(null),
        ]);
        const pendingToolApprovals = getPendingApprovalsForPanel(instanceId, roleId);
        const pendingStreamText = getPendingStreamTextForPanel(instanceId);
        scrollEl.innerHTML = '';
        if (messages.length === 0 && pendingToolApprovals.length === 0 && !pendingStreamText.trim()) {
            scrollEl.innerHTML = '<div class="panel-empty">No messages yet.</div>';
        } else {
            renderHistoricalMessageList(scrollEl, messages, {
                pendingToolApprovals,
                runId,
                pendingStreamText,
                pendingStreamRoleId: roleId || '',
                pendingStreamInstanceId: instanceId,
            });
        }
        renderTokenBadge(panel.panelEl, instanceId, runUsage);
    } catch (e) {
        scrollEl.innerHTML = '<div class="panel-empty" style="color:var(--danger)">Failed to load history.</div>';
    }
}
