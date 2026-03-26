/**
 * core/eventRouter/runEvents.js
 * Handlers for run lifecycle and model-step events.
 */
import {
    clearRunPrimaryRole,
    getPrimaryRoleId,
    getRunPrimaryRoleId,
    getRunPrimaryRoleLabel,
    isRunPrimaryRoleId,
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
    refreshSubagentRail,
    rememberLiveSubagent,
} from '../../components/subagentRail.js';
import { els } from '../../utils/dom.js';
import { sysLog } from '../../utils/logger.js';
import {
    appendThinkingChunk,
    appendStreamChunk,
    appendStreamOutputParts,
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
    state.activeAgentRoleId = getRunPrimaryRoleId(runId) || getPrimaryRoleId() || null;
    state.activeAgentInstanceId = null;
}

export function handleModelStepStarted(eventMeta, instanceId, roleId) {
    beginLlmRetryAttempt();
    const runId = eventMeta?.run_id || eventMeta?.trace_id || state.activeRunId || '';
    if (instanceId && roleId) {
        if (!state.instanceRoleMap) state.instanceRoleMap = {};
        if (!state.roleInstanceMap) state.roleInstanceMap = {};
        if (!state.autoSwitchedSubagentInstances) state.autoSwitchedSubagentInstances = {};
        state.instanceRoleMap[instanceId] = roleId;
        state.roleInstanceMap[roleId] = instanceId;
        if (!isRunPrimaryRoleId(roleId, runId)) {
            rememberLiveSubagent(instanceId, roleId);
            void refreshSubagentRail(state.currentSessionId, {
                preserveSelection: true,
            });
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
    const runId = eventMeta?.run_id || eventMeta?.trace_id || state.activeRunId || '';
    const primaryRoleId = getRunPrimaryRoleId(runId);
    const primaryLabel = getRunPrimaryRoleLabel(runId);
    const isPrimary = !roleId || isRunPrimaryRoleId(roleId, runId);
    const label = isPrimary ? primaryLabel : (roleId || 'Agent');
    const streamKey = isPrimary ? 'primary' : (instanceId || roleId);

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

export function handleOutputDelta(payload, eventMeta, instanceId, roleId) {
    const runId = eventMeta?.run_id || eventMeta?.trace_id || state.activeRunId || '';
    const primaryRoleId = getRunPrimaryRoleId(runId);
    const primaryLabel = getRunPrimaryRoleLabel(runId);
    const isPrimary = !roleId || isRunPrimaryRoleId(roleId, runId);
    const label = isPrimary ? primaryLabel : (roleId || 'Agent');
    const streamKey = isPrimary ? 'primary' : (instanceId || roleId);
    const output = Array.isArray(payload?.output) ? payload.output : [];

    if (isPrimary) {
        const container = coordinatorContainerFor(eventMeta);
        getOrCreateStreamBlock(container, streamKey, primaryRoleId, label, runId);
        appendStreamOutputParts(streamKey, output, {
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
    getOrCreateStreamBlock(container, streamKey, roleId, label, runId);
    appendStreamOutputParts(streamKey, output, {
        container,
        runId,
        roleId,
        label,
    });
}

export function handleGenerationProgress(payload) {
    const runKind = String(payload?.run_kind || 'generation');
    const phase = String(payload?.phase || 'running');
    if (phase === 'started') {
        sysLog(`${runKind} started.`, 'log-info');
        return;
    }
    if (phase === 'completed') {
        sysLog(`${runKind} completed.`, 'log-info');
        return;
    }
    if (phase === 'failed') {
        sysLog(`${runKind} failed.`, 'log-error');
    }
}

export function handleThinkingStarted(payload, eventMeta, instanceId, roleId) {
    markLlmRetrySucceeded();
    const runId = eventMeta?.run_id || eventMeta?.trace_id || state.activeRunId || '';
    const primaryRoleId = getRunPrimaryRoleId(runId);
    const isPrimary = !roleId || isRunPrimaryRoleId(roleId, runId);
    const label = isPrimary ? getRunPrimaryRoleLabel(runId) : (roleId || 'Agent');
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
    const runId = eventMeta?.run_id || eventMeta?.trace_id || state.activeRunId || '';
    const primaryRoleId = getRunPrimaryRoleId(runId);
    const isPrimary = !roleId || isRunPrimaryRoleId(roleId, runId);
    const label = isPrimary ? getRunPrimaryRoleLabel(runId) : (roleId || 'Agent');
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
    const runId = eventMeta?.run_id || eventMeta?.trace_id || state.activeRunId || '';
    const primaryRoleId = getRunPrimaryRoleId(runId);
    const isPrimary = !roleId || isRunPrimaryRoleId(roleId, runId);
    const partIndex = payload?.part_index ?? 0;

    const streamKey = isPrimary ? 'primary' : instanceId;
    finalizeThinking(streamKey, partIndex, {
        runId,
        roleId: isPrimary ? primaryRoleId : roleId,
    });
}

export function handleModelStepFinished(eventMeta, instanceId) {
    const roleId = state.instanceRoleMap?.[instanceId] || '';
    const runId = eventMeta?.run_id || eventMeta?.trace_id || state.activeRunId || '';
    const isPrimary = !instanceId || (!roleId && instanceId === 'primary') || isRunPrimaryRoleId(roleId, runId);
    const key = isPrimary ? 'primary' : instanceId;
    finalizeStream(key, isPrimary ? getRunPrimaryRoleId(runId) : roleId);
    if (instanceId && !isPrimary) {
        markSubagentStatus(instanceId, 'completed');
    }
    if (!instanceId || state.activeAgentInstanceId === instanceId) {
        state.activeAgentInstanceId = null;
        state.activeAgentRoleId = null;
    }
}

export function handleRunCompleted(eventMeta) {
    sysLog('Run completed.');
    markLlmRetrySucceeded();
    const runId = eventMeta?.run_id || eventMeta?.trace_id || state.activeRunId || '';
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
    finalizeStream('primary', getRunPrimaryRoleId(runId));
    clearRunPrimaryRole(runId);
}

export function handleRunStopped(eventMeta, payload) {
    sysLog(`Run stopped: ${payload?.reason || 'stopped_by_user'}`, 'log-info');
    clearLlmRetryStatus();
    const runId = eventMeta?.run_id || eventMeta?.trace_id || state.activeRunId || '';
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
    finalizeStream('primary', getRunPrimaryRoleId(runId));
    clearRunPrimaryRole(runId);
}

export function handleRunFailed(eventMeta, payload) {
    sysLog(`Run failed: ${payload?.error || ''}`, 'log-error');
    markLlmRetryFailed(payload?.error || '');
    const runId = eventMeta?.run_id || eventMeta?.trace_id || state.activeRunId || '';
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
    clearRunPrimaryRole(runId);
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
