/**
 * core/eventRouter.js
 * Processes SSE RunEventType payloads and dispatches rendering to UI components.
 * Routes coordinator events to the main chat area; subagent events to agent panels.
 */
import { state } from './state.js';
import { els } from '../utils/dom.js';
import { sysLog } from '../utils/logger.js';
import { updateDagActiveNode, renderNativeDAG } from '../components/workflow.js';
import {
    getOrCreateStreamBlock,
    appendStreamChunk,
    finalizeStream,
    appendToolCallBlock,
    markToolInputValidationFailed,
    updateToolResult,
    attachToolApprovalControls,
    markToolApprovalResolved,
} from '../components/messageRenderer.js';
import {
    openAgentPanel,
    getPanelScrollContainer,
    showGateCard,
    removeGateCard,
} from '../components/agentPanel.js';
import { dispatchHumanTask, resolveToolApproval } from './api.js';

const COORDINATOR_ROLE = 'coordinator_agent';

export function routeEvent(evType, payload, eventMeta) {
    if (eventMeta?.run_id) state.activeRunId = eventMeta.run_id;
    if (eventMeta?.trace_id && !state.activeRunId) state.activeRunId = eventMeta.trace_id;

    const instanceId = payload?.instance_id || eventMeta?.instance_id || null;
    const roleId = payload?.role_id || eventMeta?.role_id || null;

    if (evType === 'run_started') {
        sysLog(`Run started (trace: ${eventMeta?.trace_id})`);
        state.activeAgentRoleId = COORDINATOR_ROLE;
        updateDagActiveNode();
    }

    else if (evType === 'model_step_started') {
        if (instanceId && roleId) {
            if (!state.instanceRoleMap) state.instanceRoleMap = {};
            state.instanceRoleMap[instanceId] = roleId;
            if (roleId !== COORDINATOR_ROLE) {
                getPanelScrollContainer(instanceId, roleId);
            }
        }
        state.activeAgentRoleId = roleId;
        updateDagActiveNode();
    }

    else if (evType === 'text_delta') {
        const isCoordinator = !roleId || roleId === COORDINATOR_ROLE;
        const label = isCoordinator ? 'Coordinator' : (roleId || 'Agent');
        const streamKey = instanceId || (isCoordinator ? 'coordinator' : roleId);

        if (isCoordinator) {
            const container = _coordinatorContainerFor(eventMeta);
            getOrCreateStreamBlock(container, streamKey, COORDINATOR_ROLE, label);
            appendStreamChunk(streamKey, payload.text || '');
        } else {
            const container = getPanelScrollContainer(instanceId, roleId);
            openAgentPanel(instanceId, roleId);
            getOrCreateStreamBlock(container, instanceId, roleId, label);
            appendStreamChunk(instanceId, payload.text || '');
        }
    }

    else if (evType === 'model_step_finished') {
        const key = instanceId || 'coordinator';
        finalizeStream(key);
    }

    else if (evType === 'run_completed') {
        sysLog('Run completed.');
        state.isGenerating = false;
        state.activeAgentRoleId = null;
        if (els.sendBtn) els.sendBtn.disabled = false;
        if (els.promptInput) {
            els.promptInput.disabled = false;
            els.promptInput.focus();
        }
        finalizeStream('coordinator');
        updateDagActiveNode();
    }

    else if (evType === 'run_failed') {
        sysLog(`Run failed: ${payload?.error || ''}`, 'log-error');
        state.isGenerating = false;
        if (els.sendBtn) els.sendBtn.disabled = false;
        if (els.promptInput) els.promptInput.disabled = false;
    }

    else if (evType === 'tool_call') {
        const isCoordinator = !roleId || roleId === COORDINATOR_ROLE;
        const container = isCoordinator
            ? _coordinatorContainerFor(eventMeta)
            : getPanelScrollContainer(instanceId, roleId);
        if (!isCoordinator) openAgentPanel(instanceId, roleId);
        const streamKey = instanceId || 'coordinator';
        appendToolCallBlock(
            container,
            streamKey,
            payload.tool_name,
            payload.args,
            payload.tool_call_id || null,
        );
        sysLog(`[Tool] ${payload.tool_name}`);
    }

    else if (evType === 'tool_input_validation_failed') {
        const streamKey = instanceId || 'coordinator';
        const bound = markToolInputValidationFailed(streamKey, payload);
        if (!bound) {
            sysLog(
                `Tool input validation failed (not executed): ${payload.tool_name}`,
                'log-info',
            );
        }
    }

    else if (evType === 'tool_result') {
        const streamKey = instanceId || 'coordinator';
        const resultEnvelope = payload.result || {};
        const isError = typeof resultEnvelope === 'object'
            ? resultEnvelope.ok === false
            : !!payload.error;
        updateToolResult(
            streamKey,
            payload.tool_name,
            resultEnvelope,
            isError,
            payload.tool_call_id || null,
        );

        if (payload.tool_name === 'create_workflow_graph' && resultEnvelope) {
            _tryRenderLiveDAG(resultEnvelope);
        }
    }

    else if (evType === 'tool_approval_requested') {
        const streamKey = instanceId || 'coordinator';
        const runId = eventMeta?.run_id || eventMeta?.trace_id || state.activeRunId;
        const bound = attachToolApprovalControls(streamKey, payload.tool_name, payload, {
            onApprove: async () => {
                await resolveToolApproval(runId, payload.tool_call_id, 'approve', '');
            },
            onDeny: async () => {
                await resolveToolApproval(runId, payload.tool_call_id, 'deny', '');
            },
            onError: (e) => {
                sysLog(`Tool approval failed: ${e.message}`, 'log-error');
            },
        });
        if (!bound) {
            sysLog(`Approval requested for ${payload.tool_name}`, 'log-info');
        }
    }

    else if (evType === 'tool_approval_resolved') {
        const streamKey = instanceId || 'coordinator';
        markToolApprovalResolved(streamKey, payload);
    }

    else if (evType === 'awaiting_human_dispatch') {
        _renderHumanDispatchPanel(payload);
    }

    else if (evType === 'human_task_dispatched') {
        document.querySelectorAll('.human-dispatch-panel').forEach(el => el.remove());
        sysLog(`Task dispatched: ${payload.task_id}`, 'log-info');
    }

    else if (evType === 'subagent_gate') {
        showGateCard(payload.instance_id, payload.role_id, {
            session_id: state.currentSessionId,
            run_id: state.activeRunId,
            task_id: payload.task_id,
            summary: payload.summary,
            role_id: payload.role_id,
        });
    }

    else if (evType === 'gate_resolved') {
        removeGateCard(payload.instance_id || instanceId || '', payload.task_id);
        sysLog(`Gate resolved: ${payload.action}`, 'log-info');
    }

    else {
        sysLog(`[evt] ${evType}`, 'log-info');
    }
}

function _tryRenderLiveDAG(result) {
    try {
        let envelope = typeof result === 'string' ? JSON.parse(result) : result;
        if (!envelope) return;
        if (envelope.ok === false) return;
        let data = envelope.data !== undefined ? envelope.data : envelope;
        if (typeof data === 'string') {
            data = JSON.parse(data);
        }
        if (!data || !data.ok || !data.tasks) return;

        const taskMap = {};
        const tasksArr = Array.isArray(data.tasks) ? data.tasks : Object.values(data.tasks);
        for (const t of tasksArr) {
            taskMap[t.task_name || t.task_id] = {
                task_id: t.task_id,
                role_id: t.role_id,
                depends_on: t.depends_on || [],
            };
        }

        const workflow = { tasks: taskMap, workflow_id: data.workflow_id };

        const panel = document.getElementById('workflow-panel');
        if (panel) panel.style.display = 'flex';
        const collapsed = document.getElementById('workflow-collapsed');
        if (collapsed) collapsed.style.display = 'none';

        renderNativeDAG(workflow);
        sysLog(`Live DAG rendered (${Object.keys(taskMap).length} tasks)`);
    } catch (e) {
        console.error('Failed to render live DAG', e);
    }
}

function _renderHumanDispatchPanel(payload) {
    document.querySelectorAll('.human-dispatch-panel').forEach(el => el.remove());
    const container = _coordinatorContainerFor({ trace_id: state.activeRunId });
    if (!container) return;

    const panel = document.createElement('div');
    panel.className = 'human-dispatch-panel';

    const tasks = payload.pending_tasks || [];
    const taskRows = tasks.map(t => `
        <div class="dispatch-task-row">
            <span class="dispatch-task-obj">${t.objective || t.task_id}</span>
            <span class="dispatch-task-role">${t.role_id || ''}</span>
            <button class="dispatch-btn" data-task-id="${t.task_id}">&#x25B6; Run</button>
        </div>
    `).join('');

    panel.innerHTML = `
        <div class="dispatch-header">Human orchestration - choose next sub-task</div>
        ${taskRows || '<div class="dispatch-empty">(No pending tasks)</div>'}
    `;

    panel.querySelectorAll('.dispatch-btn').forEach(btn => {
        btn.onclick = async () => {
            const taskId = btn.dataset.taskId;
            if (!state.activeRunId || !state.currentSessionId) return;
            btn.disabled = true;
            btn.textContent = 'Dispatching...';
            try {
                await dispatchHumanTask(state.currentSessionId, state.activeRunId, taskId);
            } catch (e) {
                sysLog(`Dispatch failed: ${e.message}`, 'log-error');
                btn.disabled = false;
                btn.textContent = 'Run';
            }
        };
    });

    container.appendChild(panel);
    container.scrollTop = container.scrollHeight;
}

function _coordinatorContainerFor(eventMeta) {
    const runId = eventMeta?.trace_id || eventMeta?.run_id || state.activeRunId;
    if (runId) {
        const section = document.querySelector(`.session-round-section[data-run-id=\"${runId}\"]`);
        if (section) return section;
    }
    const live = document.querySelector('.session-round-section[data-run-id=\"__live__\"]');
    if (live) return live;
    return els.chatMessages;
}
