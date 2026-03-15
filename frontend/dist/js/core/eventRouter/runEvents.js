/**
 * core/eventRouter/runEvents.js
 * Handlers for run lifecycle and model-step events.
 */
import {
    getCoordinatorRoleId,
    isCoordinatorRoleId,
    state,
} from '../state.js';
import {
    markRunStreamConnected,
    markRunTerminalState,
} from '../../app/recovery.js';
import {
    markSubagentStatus,
    rememberLiveSubagent,
} from '../../components/subagentRail.js';
import { els } from '../../utils/dom.js';
import { sysLog } from '../../utils/logger.js';
import {
    appendStreamChunk,
    finalizeStream,
    getOrCreateStreamBlock,
} from '../../components/messageRenderer.js';
import {
    getActiveInstanceId,
    getPanelScrollContainer,
    openAgentPanel,
} from '../../components/agentPanel.js';
import {
    coordinatorContainerFor,
} from './utils.js';

export function handleRunStarted(eventMeta) {
    sysLog(`Run started (trace: ${eventMeta?.trace_id})`);
    const runId = eventMeta?.run_id || eventMeta?.trace_id || state.activeRunId;
    if (runId) {
        markRunStreamConnected(runId, { phase: 'running' });
    }
    state.activeAgentRoleId = getCoordinatorRoleId() || null;
    state.activeAgentInstanceId = null;
}

export function handleModelStepStarted(instanceId, roleId) {
    if (instanceId && roleId) {
        if (!state.instanceRoleMap) state.instanceRoleMap = {};
        if (!state.roleInstanceMap) state.roleInstanceMap = {};
        if (!state.autoSwitchedSubagentInstances) state.autoSwitchedSubagentInstances = {};
        state.instanceRoleMap[instanceId] = roleId;
        state.roleInstanceMap[roleId] = instanceId;
        if (!isCoordinatorRoleId(roleId)) {
            rememberLiveSubagent(instanceId, roleId);
            getPanelScrollContainer(instanceId, roleId);
            if (!state.autoSwitchedSubagentInstances[instanceId]) {
                state.autoSwitchedSubagentInstances[instanceId] = true;
                openAgentPanel(instanceId, roleId);
            }
        }
    }
    state.activeAgentRoleId = roleId;
    state.activeAgentInstanceId = instanceId || null;
}

export function handleTextDelta(payload, eventMeta, instanceId, roleId) {
    const coordinatorRoleId = getCoordinatorRoleId();
    const isCoordinator = !roleId || isCoordinatorRoleId(roleId);
    const label = isCoordinator ? 'Coordinator' : (roleId || 'Agent');
    const streamKey = isCoordinator ? 'coordinator' : (instanceId || roleId);
    const runId = eventMeta?.run_id || eventMeta?.trace_id || state.activeRunId || '';

    if (isCoordinator) {
        const container = coordinatorContainerFor(eventMeta);
        getOrCreateStreamBlock(container, streamKey, coordinatorRoleId, label, runId);
        appendStreamChunk(streamKey, payload.text || '', runId, coordinatorRoleId, label);
    } else {
        const container = getPanelScrollContainer(instanceId, roleId);
        // Do not keep stealing focus from user-selected panel during streaming.
        if (!getActiveInstanceId()) {
            openAgentPanel(instanceId, roleId);
        }
        getOrCreateStreamBlock(container, streamKey, roleId, label, runId);
        appendStreamChunk(streamKey, payload.text || '', runId, roleId, label);
    }
}

export function handleModelStepFinished(instanceId) {
    const roleId = state.instanceRoleMap?.[instanceId] || '';
    const isCoordinator = !instanceId || (!roleId && instanceId === 'coordinator') || isCoordinatorRoleId(roleId);
    const key = isCoordinator ? 'coordinator' : instanceId;
    finalizeStream(key, isCoordinator ? getCoordinatorRoleId() : roleId);
    if (instanceId && !isCoordinator) {
        markSubagentStatus(instanceId, 'completed');
    }
    if (!instanceId || state.activeAgentInstanceId === instanceId) {
        state.activeAgentInstanceId = null;
        state.activeAgentRoleId = null;
    }
}

export function handleRunCompleted() {
    sysLog('Run completed.');
    if (state.activeRunId) {
        markRunTerminalState(state.activeRunId, {
            status: 'completed',
            phase: 'terminal',
            recoverable: false,
        });
    }
    state.isGenerating = false;
    state.activeAgentRoleId = null;
    state.activeAgentInstanceId = null;
    if (els.sendBtn) els.sendBtn.disabled = false;
    if (els.stopBtn) {
        els.stopBtn.disabled = true;
        els.stopBtn.style.display = 'none';
    }
    if (els.promptInput) {
        els.promptInput.disabled = false;
        els.promptInput.focus();
    }
    finalizeStream('coordinator');
}

export function handleRunStopped(payload) {
    sysLog(`Run stopped: ${payload?.reason || 'stopped_by_user'}`, 'log-info');
    if (state.activeAgentInstanceId) {
        markSubagentStatus(state.activeAgentInstanceId, 'stopped');
    }
    if (state.activeRunId) {
        markRunTerminalState(state.activeRunId, {
            status: 'stopped',
            phase: 'stopped',
            recoverable: true,
        });
    }
    state.isGenerating = false;
    state.activeAgentRoleId = null;
    state.activeAgentInstanceId = null;
    state.pausedSubagent = null;
    if (els.sendBtn) els.sendBtn.disabled = false;
    if (els.stopBtn) {
        els.stopBtn.disabled = true;
        els.stopBtn.style.display = 'none';
    }
    if (els.promptInput) {
        els.promptInput.disabled = false;
        els.promptInput.focus();
    }
    finalizeStream('coordinator');
}

export function handleRunFailed(payload) {
    sysLog(`Run failed: ${payload?.error || ''}`, 'log-error');
    if (state.activeAgentInstanceId) {
        markSubagentStatus(state.activeAgentInstanceId, 'failed');
    }
    if (state.activeRunId) {
        markRunTerminalState(state.activeRunId, {
            status: 'failed',
            phase: 'terminal',
            recoverable: false,
        });
    }
    state.isGenerating = false;
    state.activeAgentRoleId = null;
    state.activeAgentInstanceId = null;
    if (els.sendBtn) els.sendBtn.disabled = false;
    if (els.stopBtn) {
        els.stopBtn.disabled = true;
        els.stopBtn.style.display = 'none';
    }
    if (els.promptInput) els.promptInput.disabled = false;
}
