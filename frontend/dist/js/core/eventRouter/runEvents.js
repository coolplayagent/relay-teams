/**
 * core/eventRouter/runEvents.js
 * Handlers for run lifecycle and model-step events.
 */
import {
    getPrimaryRoleId,
    getPrimaryRoleLabel,
    isPrimaryRoleId,
    state,
} from '../state.js';
import {
    markRunStreamConnected,
    markRunTerminalState,
} from '../../app/recovery.js';
import {
    beginLlmRetryAttempt,
    clearLlmRetryStatus,
    markLlmRetryFailed,
    markLlmRetrySucceeded,
    showLlmRetryStatus,
} from '../../app/retryStatus.js';
import {
    markSubagentStatus,
    rememberLiveSubagent,
} from '../../components/subagentRail.js';
import { els } from '../../utils/dom.js';
import { sysLog } from '../../utils/logger.js';
import {
    appendThinkingChunk,
    appendStreamChunk,
    finalizeThinking,
    finalizeStream,
    getOrCreateStreamBlock,
    startThinkingBlock,
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
    state.activeAgentRoleId = getPrimaryRoleId() || null;
    state.activeAgentInstanceId = null;
}

export function handleModelStepStarted(instanceId, roleId) {
    beginLlmRetryAttempt();
    if (instanceId && roleId) {
        if (!state.instanceRoleMap) state.instanceRoleMap = {};
        if (!state.roleInstanceMap) state.roleInstanceMap = {};
        if (!state.autoSwitchedSubagentInstances) state.autoSwitchedSubagentInstances = {};
        state.instanceRoleMap[instanceId] = roleId;
        state.roleInstanceMap[roleId] = instanceId;
        if (!isPrimaryRoleId(roleId)) {
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
    markLlmRetrySucceeded();
    const primaryRoleId = getPrimaryRoleId();
    const primaryLabel = getPrimaryRoleLabel();
    const isPrimary = !roleId || isPrimaryRoleId(roleId);
    const label = isPrimary ? primaryLabel : (roleId || 'Agent');
    const streamKey = isPrimary ? 'primary' : (instanceId || roleId);
    const runId = eventMeta?.run_id || eventMeta?.trace_id || state.activeRunId || '';

    if (isPrimary) {
        const container = coordinatorContainerFor(eventMeta);
        getOrCreateStreamBlock(container, streamKey, primaryRoleId, label, runId);
        appendStreamChunk(streamKey, payload.text || '', runId, primaryRoleId, label);
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

export function handleThinkingStarted(payload, eventMeta, instanceId, roleId) {
    markLlmRetrySucceeded();
    const primaryRoleId = getPrimaryRoleId();
    const isPrimary = !roleId || isPrimaryRoleId(roleId);
    const label = isPrimary ? getPrimaryRoleLabel() : (roleId || 'Agent');
    const runId = eventMeta?.run_id || eventMeta?.trace_id || state.activeRunId || '';
    const partIndex = payload?.part_index ?? 0;

    if (isPrimary) {
        const container = coordinatorContainerFor(eventMeta);
        const streamKey = 'primary';
        getOrCreateStreamBlock(container, streamKey, primaryRoleId, label, runId);
        startThinkingBlock(streamKey, partIndex, {
            container,
            runId,
            roleId: primaryRoleId,
            label,
        });
        return;
    }

    const container = getPanelScrollContainer(instanceId, roleId);
    if (!getActiveInstanceId()) {
        openAgentPanel(instanceId, roleId);
    }
    getOrCreateStreamBlock(container, instanceId, roleId, label, runId);
    startThinkingBlock(instanceId, partIndex, {
        container,
        runId,
        roleId,
        label,
    });
}

export function handleThinkingDelta(payload, eventMeta, instanceId, roleId) {
    const primaryRoleId = getPrimaryRoleId();
    const isPrimary = !roleId || isPrimaryRoleId(roleId);
    const label = isPrimary ? getPrimaryRoleLabel() : (roleId || 'Agent');
    const runId = eventMeta?.run_id || eventMeta?.trace_id || state.activeRunId || '';
    const partIndex = payload?.part_index ?? 0;
    const text = payload?.text || '';

    if (isPrimary) {
        const container = coordinatorContainerFor(eventMeta);
        const streamKey = 'primary';
        getOrCreateStreamBlock(container, streamKey, primaryRoleId, label, runId);
        appendThinkingChunk(streamKey, partIndex, text, {
            container,
            runId,
            roleId: primaryRoleId,
            label,
        });
        return;
    }

    const container = getPanelScrollContainer(instanceId, roleId);
    if (!getActiveInstanceId()) {
        openAgentPanel(instanceId, roleId);
    }
    getOrCreateStreamBlock(container, instanceId, roleId, label, runId);
    appendThinkingChunk(instanceId, partIndex, text, {
        container,
        runId,
        roleId,
        label,
    });
}

export function handleThinkingFinished(payload, eventMeta, instanceId, roleId) {
    const primaryRoleId = getPrimaryRoleId();
    const isPrimary = !roleId || isPrimaryRoleId(roleId);
    const runId = eventMeta?.run_id || eventMeta?.trace_id || state.activeRunId || '';
    const partIndex = payload?.part_index ?? 0;

    const streamKey = isPrimary ? 'primary' : instanceId;
    finalizeThinking(streamKey, partIndex, {
        runId,
        roleId: isPrimary ? primaryRoleId : roleId,
    });
}

export function handleModelStepFinished(instanceId) {
    const roleId = state.instanceRoleMap?.[instanceId] || '';
    const isPrimary = !instanceId || (!roleId && instanceId === 'primary') || isPrimaryRoleId(roleId);
    const key = isPrimary ? 'primary' : instanceId;
    finalizeStream(key, isPrimary ? getPrimaryRoleId() : roleId);
    if (instanceId && !isPrimary) {
        markSubagentStatus(instanceId, 'completed');
    }
    if (!instanceId || state.activeAgentInstanceId === instanceId) {
        state.activeAgentInstanceId = null;
        state.activeAgentRoleId = null;
    }
}

export function handleRunCompleted() {
    sysLog('Run completed.');
    markLlmRetrySucceeded();
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
    finalizeStream('primary', getPrimaryRoleId());
}

export function handleRunStopped(payload) {
    sysLog(`Run stopped: ${payload?.reason || 'stopped_by_user'}`, 'log-info');
    clearLlmRetryStatus();
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
    finalizeStream('primary', getPrimaryRoleId());
}

export function handleRunFailed(payload) {
    sysLog(`Run failed: ${payload?.error || ''}`, 'log-error');
    markLlmRetryFailed(payload?.error || '');
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

export function handleLlmRetryScheduled(payload, eventMeta) {
    const delaySeconds = Number(payload?.retry_in_ms || 0) / 1000;
    sysLog(
        `Model retry scheduled: attempt ${payload?.attempt_number || '?'} of ${payload?.total_attempts || '?'} in ${delaySeconds.toFixed(delaySeconds >= 10 ? 0 : 1)}s`,
        'log-info',
    );
    showLlmRetryStatus(payload, eventMeta);
}

export function handleLlmRetryExhausted(payload, eventMeta) {
    sysLog(
        `Model retries exhausted: attempt ${payload?.attempt_number || '?'} of ${payload?.total_attempts || '?'}`,
        'log-error',
    );
    showLlmRetryStatus({
        ...payload,
        retry_in_ms: 0,
    }, eventMeta);
    markLlmRetryFailed(payload?.error_message || '');
}
