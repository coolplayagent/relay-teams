/**
 * components/agentPanel/history.js
 * Subagent history loading into an existing panel.
 */
import { fetchAgentMessages, fetchAgentReflection, fetchRunTokenUsage } from '../../core/api.js';
import { state } from '../../core/state.js';
import { t } from '../../utils/i18n.js';
import {
    bindStreamOverlayToContainer,
    getInstanceStreamOverlay,
    renderHistoricalMessageList,
} from '../messageRenderer.js';
import {
    getActiveRoundRunId,
    getPanel,
    getPendingApprovalsForPanel,
} from './state.js';

function formatMessage(key, values = {}) {
    return Object.entries(values).reduce(
        (result, [name, value]) => result.replaceAll(`{${name}}`, String(value)),
        t(key),
    );
}

function renderTokenBadge(panelEl, instanceId, runUsage) {
    const badgeEl = panelEl.querySelector(
        `.agent-panel-topbar .agent-token-usage[data-instance-id="${instanceId}"]`
    );
    if (!badgeEl) return;
    let html = '';
    if (runUsage) {
        const agent = (runUsage.by_agent || []).find(a => a.instance_id === instanceId);
        if (agent && agent.total_tokens !== 0) {
            const fmt = n => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));
            html = `
        <span class="token-badge" title="${escapeAttribute(formatMessage('agent_panel.history.token_title', {
            input: agent.input_tokens,
            output: agent.output_tokens,
            requests: agent.requests,
        }))}">
            <span class="token-in">${escapeHtml(formatMessage('agent_panel.history.token_in', { count: fmt(agent.input_tokens) }))}</span>
            <span class="token-out">${escapeHtml(formatMessage('agent_panel.history.token_out', { count: fmt(agent.output_tokens) }))}</span>
        </span>
    `;
        }
    }
    badgeEl.innerHTML = html;
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
    metaEl.textContent = lineCount > 0
        ? t('subagent.prompt_lines').replace('{count}', String(lineCount))
        : t('subagent.no_snapshot');
    bodyEl.innerHTML = prompt
        ? `<pre class="agent-panel-runtime-pre">${escapeHtml(prompt)}</pre>`
        : t('subagent.no_runtime_prompt');
}

function renderRuntimeTools(panelEl, sessionAgent) {
    const metaEl = panelEl.querySelector('.agent-panel-runtime-tools-meta');
    const bodyEl = panelEl.querySelector('.agent-panel-runtime-tools-body');
    if (!metaEl || !bodyEl) return;

    const raw = String(sessionAgent?.runtime_tools_json || '').trim();
    if (!raw) {
        metaEl.textContent = t('subagent.no_snapshot');
        bodyEl.textContent = t('subagent.no_runtime_tools');
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
    metaEl.textContent = toolCount > 0
        ? t('subagent.tools_count').replace('{count}', String(toolCount))
        : t('subagent.json_snapshot');
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
    metaEl.textContent = updatedAt ? new Date(updatedAt).toLocaleString() : t('subagent.no_reflection');
    bodyEl.dataset.summary = summary;
    bodyEl.dataset.updatedAt = updatedAt;
    bodyEl.dataset.source = String(reflection?.source || 'stored');
    if (bodyEl.dataset.mode === 'editing') return;
    bodyEl.textContent = summary || t('subagent.no_reflection_memory');
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
            const taskInstanceId = String(
                task?.assigned_instance_id || task?.instance_id || '',
            ).trim();
            if (taskInstanceId && taskInstanceId === String(instanceId || '').trim()) return true;
            return !!roleId
                && String(task?.assigned_role_id || task?.role_id || '').trim()
                    === String(roleId || '').trim();
        })
        .slice()
        .sort((left, right) => compareTimelineDesc(left, right))
        .slice(0, 4);

    if (tasks.length === 0) {
        tasksEl.innerHTML = `<div class="agent-panel-summary-empty">${escapeHtml(t('subagent.no_tasks'))}</div>`;
        return;
    }

    tasksEl.innerHTML = tasks.map(task => `
        <div class="agent-panel-summary-task-row">
            <span class="agent-panel-summary-task-title">${escapeHtml(task.title || task.task_id || t('subagent.task'))}</span>
            <span class="agent-panel-summary-task-state is-${escapeAttribute(String(task.status || 'created'))}">
                ${escapeHtml(humanizeStatus(task.status || 'created'))}
            </span>
        </div>
    `).join('');
}

function renderPanelIdentity(panelEl, instanceId, roleId) {
    const roleEl = panelEl.querySelector('.agent-panel-role-label');
    const idEl = panelEl.querySelector('.agent-panel-instance-id');
    const statusEl = panelEl.querySelector('.agent-panel-top-status');
    if (!roleEl || !idEl || !statusEl) return;

    const sessionAgent = getSessionAgent(instanceId, roleId);
    const fallbackRole = roleId
        ? roleId.replace(/_/g, ' ').replace(/\b\w/g, letter => letter.toUpperCase())
        : instanceId.slice(0, 8);
    const statusValue = String(
        sessionAgent?.status
        || (
            state.activeSubagentSession?.instanceId === instanceId
                ? state.activeSubagentSession?.status
                : ''
        )
        || 'idle',
    ).trim() || 'idle';

    roleEl.textContent = fallbackRole;
    idEl.textContent = String(instanceId || '').slice(0, 8);
    statusEl.textContent = humanizeStatus(statusValue);
    statusEl.className = `agent-panel-top-status is-${escapeAttribute(statusValue)}`;
}

function humanizeStatus(value) {
    const safeValue = String(value || 'idle').trim();
    if (!safeValue) return t('subagent.status_idle');
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

export async function renderInstanceHistoryInto(container, options = {}) {
    if (!container) {
        return null;
    }
    const sessionId = String(options.sessionId || '').trim();
    const instanceId = String(options.instanceId || '').trim();
    const runId = String(options.runId || '').trim();
    const userRoleLabel = String(
        options.userRoleLabel || t('subagent.task_prompt'),
    ).trim();
    const emptyLabel = String(
        options.emptyLabel || t('agent_panel.history.empty'),
    ).trim();
    const loadFailedLabel = String(
        options.loadFailedLabel || t('agent_panel.history.load_failed'),
    ).trim();
    const pendingToolApprovals = Array.isArray(options.pendingToolApprovals)
        ? options.pendingToolApprovals
        : [];
    const overlayMode = String(options.overlayMode || 'render').trim().toLowerCase();
    const requireToolBoundary = options.requireToolBoundary === true;
    if (!sessionId || !instanceId) {
        container.innerHTML = `<div class="panel-empty">${escapeHtml(emptyLabel)}</div>`;
        return null;
    }
    try {
        const messages = await fetchAgentMessages(sessionId, instanceId);
        const overlayEntry = getInstanceStreamOverlay(runId, instanceId);
        const streamOverlayEntry = shouldRenderLiveOverlay(options, overlayEntry)
            ? overlayEntry
            : null;
        if (requireToolBoundary && hasPendingToolResults(messages)) {
            return {
                messages,
                streamOverlayEntry,
                deferred: true,
            };
        }
        container.innerHTML = '';
        if (
            messages.length === 0
            && pendingToolApprovals.length === 0
            && !streamOverlayEntry
        ) {
            container.innerHTML = `<div class="panel-empty">${escapeHtml(emptyLabel)}</div>`;
            return {
                messages,
                streamOverlayEntry,
            };
        }
        renderHistoricalMessageList(container, messages, {
            pendingToolApprovals,
            runId,
            streamOverlayEntry:
                overlayMode === 'bind' && messages.length > 0
                    ? null
                    : streamOverlayEntry,
            separateOverlayMessage: overlayMode === 'separate',
            userRoleLabel,
        });
        if (overlayMode === 'bind' && messages.length > 0 && streamOverlayEntry) {
            bindStreamOverlayToContainer(container, {
                instanceId,
                runId,
                roleId: streamOverlayEntry.roleId || options.roleId || '',
                label: streamOverlayEntry.label || '',
            });
        }
        return {
            messages,
            streamOverlayEntry,
        };
    } catch (e) {
        container.innerHTML =
            `<div class="panel-empty" style="color:var(--danger)">${escapeHtml(loadFailedLabel)}</div>`;
        throw e;
    }
}

function shouldRenderLiveOverlay(options = {}, streamOverlayEntry = null) {
    if (!streamOverlayEntry || typeof streamOverlayEntry !== 'object') {
        return false;
    }
    const explicitStates = [
        String(options.status || '').trim().toLowerCase(),
        String(options.runStatus || '').trim().toLowerCase(),
        String(options.runPhase || '').trim().toLowerCase(),
    ].filter(Boolean);
    if (explicitStates.length === 0) {
        return true;
    }
    if (explicitStates.some(state => isTerminalOverlayState(state))) {
        return false;
    }
    return true;
}

function isTerminalOverlayState(value) {
    return value === 'completed'
        || value === 'failed'
        || value === 'stopped'
        || value === 'terminal'
        || value === 'idle';
}

function hasPendingToolResults(messages) {
    const pending = new Set();
    (Array.isArray(messages) ? messages : []).forEach(item => {
        if (!item || String(item.entry_type || '') === 'marker') {
            return;
        }
        const parts = Array.isArray(item?.message?.parts) ? item.message.parts : [];
        parts.forEach(part => {
            const partKind = String(part?.part_kind || '').trim().toLowerCase();
            if (partKind === 'tool-call' || isLegacyToolCallPart(part)) {
                const key = toolPartKey(part);
                if (key) {
                    pending.add(key);
                }
                return;
            }
            if (partKind === 'tool-return' || isLegacyToolReturnPart(part)) {
                const key = toolPartKey(part);
                if (key) {
                    pending.delete(key);
                }
            }
        });
    });
    return pending.size > 0;
}

function isLegacyToolCallPart(part) {
    return !!(
        part
        && typeof part === 'object'
        && part.tool_name !== undefined
        && part.args !== undefined
    );
}

function isLegacyToolReturnPart(part) {
    return !!(
        part
        && typeof part === 'object'
        && part.tool_name !== undefined
        && part.content !== undefined
        && part.args === undefined
    );
}

function toolPartKey(part) {
    const toolCallId = String(part?.tool_call_id || '').trim();
    if (toolCallId) {
        return `id:${toolCallId}`;
    }
    const toolName = String(part?.tool_name || '').trim();
    return toolName ? `name:${toolName}` : '';
}

export function syncAgentPanelState(instanceId, roleId = null) {
    const panel = getPanel(instanceId);
    if (!panel) return;
    const sessionAgent = getSessionAgent(instanceId, roleId);
    const fallbackStatus = String(
        sessionAgent?.status
        || (
            state.activeSubagentSession?.instanceId === instanceId
                ? state.activeSubagentSession?.status
                : ''
        )
        || 'idle',
    ).trim() || 'idle';
    renderPanelIdentity(panel.panelEl, instanceId, roleId);
    renderRuntimePrompt(panel.panelEl, sessionAgent);
    renderRuntimeTools(panel.panelEl, sessionAgent);
    renderPanelSummary(panel.panelEl, instanceId, roleId);
    syncPanelExpansion(panel.panelEl, fallbackStatus);
}

export async function loadAgentHistory(instanceId, roleId = null, options = {}) {
    const panel = getPanel(instanceId);
    if (!panel) return;
    const scrollEl = panel.scrollEl;
    const runId = String(options.runId || state.activeRunId || getActiveRoundRunId() || '').trim();
    const sessionAgent = getSessionAgent(instanceId, roleId);
    try {
        scrollEl.innerHTML = `<div class="panel-loading">${escapeHtml(t('agent_panel.history.loading'))}</div>`;
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
        const [historyResult, runUsage, reflection] = await Promise.all([
            renderInstanceHistoryInto(scrollEl, {
                sessionId: state.currentSessionId,
                instanceId,
                runId,
                pendingToolApprovals,
                requireToolBoundary: options.requireToolBoundary === true,
                status: options.status || sessionAgent?.status || '',
                runStatus: options.runStatus || sessionAgent?.run_status || sessionAgent?.runStatus || '',
                runPhase: options.runPhase || sessionAgent?.run_phase || sessionAgent?.runPhase || '',
                emptyLabel: t('agent_panel.history.empty'),
                loadFailedLabel: t('agent_panel.history.load_failed'),
                userRoleLabel: t('subagent.task_prompt'),
            }),
            runId && runId !== '__live__'
                ? fetchRunTokenUsage(state.currentSessionId, runId)
                : Promise.resolve(null),
            fetchAgentReflection(state.currentSessionId, instanceId),
        ]);
        panel.loadedSessionId = state.currentSessionId || '';
        panel.loadedRunId = runId || '';
        renderTokenBadge(panel.panelEl, instanceId, runUsage);
        syncAgentPanelState(instanceId, roleId);
        renderReflection(panel.panelEl, reflection);
        renderPanelPreview(panel.panelEl, scrollEl);
        return historyResult || null;
    } catch (e) {
        scrollEl.innerHTML =
            `<div class="panel-empty" style="color:var(--danger)">${escapeHtml(t('agent_panel.history.load_failed'))}</div>`;
        renderPanelPreview(panel.panelEl, scrollEl);
        return null;
    }
}

function syncPanelExpansion(panelEl, status) {
    if (!panelEl) {
        return;
    }
    const panelDataset = panelEl.dataset && typeof panelEl.dataset === 'object'
        ? panelEl.dataset
        : null;
    if (String(panelDataset?.expansionMode || '') === 'manual') {
        return;
    }
    applyPanelExpandedState(panelEl, !isTerminalStatus(status));
}

function isTerminalStatus(status) {
    const safeStatus = String(status || '').trim().toLowerCase();
    return safeStatus === 'completed'
        || safeStatus === 'failed'
        || safeStatus === 'stopped';
}

function renderPanelPreview(panelEl, scrollEl) {
    const previewEl = panelEl?.querySelector('.agent-panel-preview');
    if (!previewEl || !scrollEl) {
        return;
    }
    const previewText = String(scrollEl.textContent || '')
        .replace(/\s+/g, ' ')
        .trim();
    if (!previewText) {
        previewEl.textContent = t('agent_panel.history.empty');
        return;
    }
    previewEl.textContent = previewText.length > 140
        ? `${previewText.slice(0, 140).trimEnd()}...`
        : previewText;
}

function applyPanelExpandedState(panelEl, expanded) {
    if (!panelEl) {
        return;
    }
    const isExpanded = expanded === true;
    if (panelEl.dataset && typeof panelEl.dataset === 'object') {
        panelEl.dataset.expanded = isExpanded ? 'true' : 'false';
    }
    const toggleBtn = panelEl.querySelector('.agent-panel-toggle');
    const contentEl = panelEl.querySelector('.agent-panel-content');
    if (toggleBtn && typeof toggleBtn.setAttribute === 'function') {
        toggleBtn.setAttribute('aria-expanded', isExpanded ? 'true' : 'false');
    }
    if (contentEl) {
        contentEl.hidden = !isExpanded;
    }
}
