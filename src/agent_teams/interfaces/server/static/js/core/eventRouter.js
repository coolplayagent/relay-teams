/**
 * core/eventRouter.js
 * Processes SSE RunEventType payloads and dispatches rendering to UI components.
 * Routes coordinator events to the main chat area; subagent events to agent panels.
 */
import { state } from './state.js';
import { els } from '../utils/dom.js';
import { sysLog } from '../utils/logger.js';
import { updateDagActiveNode } from '../components/workflow.js';
import {
    getOrCreateStreamBlock,
    appendStreamChunk,
    finalizeStream,
    appendToolCallBlock,
    updateToolResult,
    clearStreamState,
} from '../components/messageRenderer.js';
import {
    openAgentPanel,
    getPanelScrollContainer,
    showGateCard,
    removeGateCard,
} from '../components/agentPanel.js';
import { resolveGate, dispatchHumanTask } from './api.js';
import { parseMarkdown } from '../utils/markdown.js';

const COORDINATOR_ROLE = 'coordinator_agent';

// ─── Streaming state reset ────────────────────────────────────────────────────
export function resetDomStreams() {
    // individual streams are managed by messageRenderer
}

// ─── Main event dispatcher ────────────────────────────────────────────────────
export function routeEvent(evType, payload, eventMeta) {
    // Always track activeRunId for gate/dispatch calls
    if (eventMeta?.run_id) state.activeRunId = eventMeta.run_id;
    if (eventMeta?.trace_id && !state.activeRunId) state.activeRunId = eventMeta.trace_id;

    const instanceId = payload?.instance_id;
    const roleId = payload?.role_id;

    // ── Lifecycle ────────────────────────────────────────────────────────────
    if (evType === 'run_started') {
        sysLog(`Run started (trace: ${eventMeta?.trace_id})`);
        state.activeAgentRoleId = COORDINATOR_ROLE;
        updateDagActiveNode();
    }

    else if (evType === 'model_step_started') {
        if (instanceId && roleId) {
            // Register instance→role mapping in state for DAG click routing
            if (!state.instanceRoleMap) state.instanceRoleMap = {};
            state.instanceRoleMap[instanceId] = roleId;

            // If not coordinator → ensure panel exists (don't auto-open, wait for text)
            if (roleId !== COORDINATOR_ROLE) {
                getPanelScrollContainer(instanceId, roleId);
            }
        }
        state.activeAgentRoleId = roleId;
        updateDagActiveNode();
    }

    // ── Text streaming ───────────────────────────────────────────────────────
    else if (evType === 'text_delta') {
        const isCoordinator = !roleId || roleId === COORDINATOR_ROLE;
        const label = isCoordinator ? 'Coordinator' : (roleId || 'Agent');

        if (isCoordinator) {
            // Accumulate in the main coordinator chat area
            const container = els.chatMessages;
            const st = getOrCreateStreamBlock(container, instanceId || 'coordinator', label);
            appendStreamChunk(instanceId || 'coordinator', payload.text || '');
        } else {
            // Route to the subagent panel — auto-open it on first chunk
            let container = getPanelScrollContainer(instanceId, roleId);
            openAgentPanel(instanceId, roleId);
            const st = getOrCreateStreamBlock(container, instanceId, label);
            appendStreamChunk(instanceId, payload.text || '');
        }
    }

    else if (evType === 'run_finished' || evType === 'model_step_done') {
        if (instanceId) {
            finalizeStream(instanceId);
        } else {
            finalizeStream('coordinator');
        }

        if (evType === 'run_finished' && !eventMeta?.instance_id) {
            sysLog(`Run finished. (trace: ${eventMeta?.trace_id})`);
            state.activeAgentRoleId = null;
            state.isGenerating = false;
            if (els.sendBtn) els.sendBtn.disabled = false;
            if (els.promptInput) els.promptInput.disabled = false;
            updateDagActiveNode();
        }
    }

    else if (evType === 'run_completed') {
        sysLog(`Run completed.`);
        state.isGenerating = false;
        state.activeAgentRoleId = null;
        if (els.sendBtn) els.sendBtn.disabled = false;
        if (els.promptInput) { els.promptInput.disabled = false; els.promptInput.focus(); }
        finalizeStream('coordinator');
        updateDagActiveNode();
    }

    else if (evType === 'run_failed') {
        sysLog(`Run failed: ${payload?.error || ''}`, 'log-error');
        state.isGenerating = false;
        if (els.sendBtn) els.sendBtn.disabled = false;
        if (els.promptInput) els.promptInput.disabled = false;
    }

    // ── Tool calls ───────────────────────────────────────────────────────────
    else if (evType === 'tool_call') {
        const isCoordinator = !roleId || roleId === COORDINATOR_ROLE;
        const container = isCoordinator
            ? els.chatMessages
            : getPanelScrollContainer(instanceId, roleId);

        if (!isCoordinator) openAgentPanel(instanceId, roleId);

        appendToolCallBlock(container, instanceId || 'coordinator', payload.tool_name, payload.args);
        sysLog(`[Tool] ${payload.tool_name}…`);

        // Live graph update when coordinator creates workflow
        if (payload.tool_name === 'create_workflow_graph' && isCoordinator) {
            // The result event will trigger the DAG reload via tool_result below
        }
    }

    else if (evType === 'tool_result') {
        const isCoordinator = !roleId || roleId === COORDINATOR_ROLE;
        updateToolResult(
            instanceId || 'coordinator',
            payload.tool_name,
            payload.result,
            !!payload.error,
        );
    }

    // ── Human orchestration mode ─────────────────────────────────────────────
    else if (evType === 'awaiting_human_dispatch') {
        _renderHumanDispatchPanel(payload, eventMeta);
    }

    else if (evType === 'human_task_dispatched') {
        document.querySelectorAll('.human-dispatch-panel').forEach(el => el.remove());
        sysLog(`▶ Task dispatched: ${payload.task_id}`, 'log-info');
    }

    // ── Confirmation gate ────────────────────────────────────────────────────
    else if (evType === 'subagent_gate') {
        const gateInstanceId = payload.instance_id;
        const gateRoleId = payload.role_id;
        showGateCard(gateInstanceId, gateRoleId, {
            session_id: state.currentSessionId,
            run_id: state.activeRunId,
            task_id: payload.task_id,
            summary: payload.summary,
            role_id: gateRoleId,
        });
    }

    else if (evType === 'gate_resolved') {
        removeGateCard(payload.instance_id || '', payload.task_id);
        sysLog(`Gate resolved: ${payload.action}`, 'log-info');
    }

    else {
        sysLog(`Event: ${evType}`, 'log-info');
    }
}

// ─── Human dispatch panel ─────────────────────────────────────────────────────

function _renderHumanDispatchPanel(payload, eventMeta) {
    document.querySelectorAll('.human-dispatch-panel').forEach(el => el.remove());
    const container = els.chatMessages;
    if (!container) return;

    const panel = document.createElement('div');
    panel.className = 'human-dispatch-panel';

    const tasks = payload.pending_tasks || [];
    const taskRows = tasks.map(t => `
        <div class="dispatch-task-row">
            <span class="dispatch-task-obj">${t.objective || t.task_id}</span>
            <span class="dispatch-task-role">${t.role_id || ''}</span>
            <button class="dispatch-btn" data-task-id="${t.task_id}">&#x25B6; 执行</button>
        </div>
    `).join('');

    panel.innerHTML = `
        <div class="dispatch-header">&#x1F9D1;&#x200D;&#x1F4BC; 人工编排 — 请选择要执行的子任务</div>
        ${taskRows || '<div class="dispatch-empty">（无待执行任务）</div>'}
    `;

    panel.querySelectorAll('.dispatch-btn').forEach(btn => {
        btn.onclick = async () => {
            const taskId = btn.dataset.taskId;
            if (!state.activeRunId || !state.currentSessionId) return;
            btn.disabled = true;
            btn.textContent = '派发中…';
            try {
                await dispatchHumanTask(state.currentSessionId, state.activeRunId, taskId);
            } catch (e) {
                sysLog(`Dispatch failed: ${e.message}`, 'log-error');
                btn.disabled = false;
                btn.textContent = '▶ 执行';
            }
        };
    });

    container.appendChild(panel);
    container.scrollTop = container.scrollHeight;
}
