/**
 * core/eventRouter/runEvents.js
 * Handlers for run lifecycle and model-step events.
 */
import { state } from '../state.js';
import { els } from '../../utils/dom.js';
import { sysLog } from '../../utils/logger.js';
import { updateDagActiveNode } from '../../components/workflow.js';
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
    COORDINATOR_ROLE,
    coordinatorContainerFor,
} from './utils.js';

export function handleRunStarted(eventMeta) {
    sysLog(`Run started (trace: ${eventMeta?.trace_id})`);
    state.activeAgentRoleId = COORDINATOR_ROLE;
    state.activeAgentInstanceId = null;
    updateDagActiveNode();
}

export function handleModelStepStarted(instanceId, roleId) {
    if (instanceId && roleId) {
        if (!state.instanceRoleMap) state.instanceRoleMap = {};
        if (!state.roleInstanceMap) state.roleInstanceMap = {};
        if (!state.autoSwitchedSubagentInstances) state.autoSwitchedSubagentInstances = {};
        state.instanceRoleMap[instanceId] = roleId;
        state.roleInstanceMap[roleId] = instanceId;
        if (roleId !== COORDINATOR_ROLE) {
            getPanelScrollContainer(instanceId, roleId);
            if (!state.autoSwitchedSubagentInstances[instanceId]) {
                state.autoSwitchedSubagentInstances[instanceId] = true;
                openAgentPanel(instanceId, roleId);
            }
        }
    }
    state.activeAgentRoleId = roleId;
    state.activeAgentInstanceId = instanceId || null;
    updateDagActiveNode();
}

export function handleTextDelta(payload, eventMeta, instanceId, roleId) {
    const isCoordinator = !roleId || roleId === COORDINATOR_ROLE;
    const label = isCoordinator ? 'Coordinator' : (roleId || 'Agent');
    const streamKey = instanceId || (isCoordinator ? 'coordinator' : roleId);

    if (isCoordinator) {
        const container = coordinatorContainerFor(eventMeta);
        getOrCreateStreamBlock(container, streamKey, COORDINATOR_ROLE, label);
        appendStreamChunk(streamKey, payload.text || '');
    } else {
        const container = getPanelScrollContainer(instanceId, roleId);
        // Do not keep stealing focus from user-selected panel during streaming.
        if (!getActiveInstanceId()) {
            openAgentPanel(instanceId, roleId);
        }
        getOrCreateStreamBlock(container, instanceId, roleId, label);
        appendStreamChunk(instanceId, payload.text || '');
    }
}

export function handleModelStepFinished(instanceId) {
    const key = instanceId || 'coordinator';
    finalizeStream(key);
    if (!instanceId || state.activeAgentInstanceId === instanceId) {
        state.activeAgentInstanceId = null;
        state.activeAgentRoleId = null;
    }
    updateDagActiveNode();
}

export function handleRunCompleted() {
    sysLog('Run completed.');
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
    updateDagActiveNode();
}

export function handleRunStopped(payload) {
    sysLog(`Run stopped: ${payload?.reason || 'stopped_by_user'}`, 'log-info');
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
    updateDagActiveNode();
}

export function handleRunFailed(payload) {
    sysLog(`Run failed: ${payload?.error || ''}`, 'log-error');
    state.isGenerating = false;
    state.activeAgentRoleId = null;
    state.activeAgentInstanceId = null;
    if (els.sendBtn) els.sendBtn.disabled = false;
    if (els.stopBtn) {
        els.stopBtn.disabled = true;
        els.stopBtn.style.display = 'none';
    }
    if (els.promptInput) els.promptInput.disabled = false;
    updateDagActiveNode();
}
