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
import {
    getActiveSubagentSession,
    getActiveSubagentSessionStreamContainer,
    rememberNormalModeSubagentSession,
    renderActiveSubagentSession,
    settleActiveSubagentSessionAfterTerminal,
    updateNormalModeSubagentSessionStatus,
} from '../../components/subagentSessions.js';
import { els } from '../../utils/dom.js';
import { sysLog } from '../../utils/logger.js';
import {
    applyStreamOverlayEvent,
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

export function handleLlmFallbackActivated(payload) {
    const fromProfile = String(payload?.from_profile_id || '').trim();
    const toProfile = String(payload?.to_profile_id || '').trim();
    if (fromProfile && toProfile) {
        sysLog(`Fallback activated: ${fromProfile} -> ${toProfile}`, 'log-info');
        return;
    }
    sysLog('Fallback activated.', 'log-info');
}

export function handleLlmFallbackExhausted(payload) {
    const fromProfile = String(payload?.from_profile_id || '').trim();
    if (fromProfile) {
        sysLog(`Fallback exhausted for ${fromProfile}.`, 'log-error');
        return;
    }
    sysLog('Fallback exhausted.', 'log-error');
}

export function handleModelStepStarted(eventMeta, instanceId, roleId) {
    beginLlmRetryAttempt();
    const runId = eventMeta?.run_id || eventMeta?.trace_id || state.activeRunId || '';
    const normalModeSubagent = isNormalModeSubagentRun(runId, roleId);
    if (instanceId && roleId) {
        if (!state.instanceRoleMap) state.instanceRoleMap = {};
        if (!state.roleInstanceMap) state.roleInstanceMap = {};
        if (!state.autoSwitchedSubagentInstances) state.autoSwitchedSubagentInstances = {};
        state.instanceRoleMap[instanceId] = roleId;
        state.roleInstanceMap[roleId] = instanceId;
        if (!isRunPrimaryRoleId(roleId, runId) && !normalModeSubagent) {
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
        if (normalModeSubagent) {
            rememberNormalModeSubagentSession(state.currentSessionId, {
                instance_id: instanceId,
                role_id: roleId,
                run_id: runId,
                status: 'running',
            });
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
    const normalModeSubagent = isNormalModeSubagentRun(runId, roleId);

    if (isPrimary) {
        if (state.activeSubagentSession) {
            applyStreamOverlayEvent('text_delta', payload, {
                runId,
                instanceId: 'primary',
                roleId: primaryRoleId,
                label,
            });
            return;
        }
        const container = coordinatorContainerFor(eventMeta);
        getOrCreateStreamBlock(container, streamKey, primaryRoleId, label, runId);
        appendStreamChunk(streamKey, payload.text || '', runId, primaryRoleId, label);
    } else {
        const container = normalModeSubagent
            ? getActiveSubagentSessionStreamContainer(instanceId)
            : getPanelScrollContainer(instanceId, roleId);
        if (normalModeSubagent && !container) {
            applyStreamOverlayEvent('text_delta', payload, {
                runId,
                instanceId,
                roleId,
                label,
            });
            return;
        }
        if (!normalModeSubagent && !getActiveInstanceId()) {
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
    const normalModeSubagent = isNormalModeSubagentRun(runId, roleId);

    if (isPrimary) {
        if (state.activeSubagentSession) {
            for (const part of output) {
                if (part?.kind === 'text') {
                    applyStreamOverlayEvent('text_delta', { text: String(part.text || '') }, {
                        runId,
                        instanceId: 'primary',
                        roleId: primaryRoleId,
                        label,
                    });
                }
            }
            return;
        }
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

    const container = normalModeSubagent
        ? getActiveSubagentSessionStreamContainer(instanceId)
        : getPanelScrollContainer(instanceId, roleId);
    if (normalModeSubagent && !container) {
        for (const part of output) {
            if (part?.kind === 'text') {
                applyStreamOverlayEvent('text_delta', { text: String(part.text || '') }, {
                    runId,
                    instanceId,
                    roleId,
                    label,
                });
            }
        }
        return;
    }
    if (!normalModeSubagent && !getActiveInstanceId()) {
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
    const normalModeSubagent = isNormalModeSubagentRun(runId, roleId);

    if (isPrimary) {
        if (state.activeSubagentSession) {
            applyStreamOverlayEvent('thinking_started', payload, {
                runId,
                instanceId: 'primary',
                roleId: primaryRoleId,
                label,
            });
            return;
        }
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

    const container = normalModeSubagent
        ? getActiveSubagentSessionStreamContainer(instanceId)
        : getPanelScrollContainer(instanceId, roleId);
    if (normalModeSubagent && !container) {
        applyStreamOverlayEvent('thinking_started', payload, {
            runId,
            instanceId,
            roleId,
            label,
        });
        return;
    }
    if (!normalModeSubagent && !getActiveInstanceId()) {
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
    const normalModeSubagent = isNormalModeSubagentRun(runId, roleId);

    if (isPrimary) {
        if (state.activeSubagentSession) {
            applyStreamOverlayEvent('thinking_delta', payload, {
                runId,
                instanceId: 'primary',
                roleId: primaryRoleId,
                label,
            });
            return;
        }
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

    const container = normalModeSubagent
        ? getActiveSubagentSessionStreamContainer(instanceId)
        : getPanelScrollContainer(instanceId, roleId);
    if (normalModeSubagent && !container) {
        applyStreamOverlayEvent('thinking_delta', payload, {
            runId,
            instanceId,
            roleId,
            label,
        });
        return;
    }
    if (!normalModeSubagent && !getActiveInstanceId()) {
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
    const normalModeSubagent = isNormalModeSubagentRun(runId, roleId);

    if (isPrimary && state.activeSubagentSession) {
        applyStreamOverlayEvent('thinking_finished', payload, {
            runId,
            instanceId: 'primary',
            roleId: primaryRoleId,
        });
        return;
    }
    if (!isPrimary && normalModeSubagent && !getActiveSubagentSessionStreamContainer(instanceId)) {
        applyStreamOverlayEvent('thinking_finished', payload, {
            runId,
            instanceId,
            roleId,
        });
        return;
    }

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
    const normalModeSubagent = isNormalModeSubagentRun(runId, roleId);
    if (isPrimary && state.activeSubagentSession) {
        applyStreamOverlayEvent('model_step_finished', {}, {
            runId,
            instanceId: 'primary',
            roleId: getRunPrimaryRoleId(runId),
            cleanupDelayMs: 1200,
        });
        return;
    }
    const key = isPrimary ? 'primary' : instanceId;
    if (!isPrimary && normalModeSubagent) {
        updateNormalModeSubagentSessionStatus(state.currentSessionId, instanceId, 'completed');
        if (!getActiveSubagentSessionStreamContainer(instanceId)) {
            applyStreamOverlayEvent('model_step_finished', {}, {
                runId,
                instanceId,
                roleId,
                cleanupDelayMs: 1200,
            });
            return;
        }
    }
    finalizeStream(key, isPrimary ? getRunPrimaryRoleId(runId) : roleId, { runId });
    if (instanceId && !isPrimary) {
        markSubagentStatus(instanceId, 'completed');
    }
    if (!instanceId || state.activeAgentInstanceId === instanceId) {
        state.activeAgentInstanceId = null;
        state.activeAgentRoleId = null;
    }
}

export function handleSubagentRunTerminal(instanceId, status, eventMeta = null, roleIdOverride = '') {
    const safeInstanceId = String(instanceId || '').trim();
    if (!safeInstanceId) {
        return;
    }
    const roleId = String(roleIdOverride || state.instanceRoleMap?.[safeInstanceId] || '').trim();
    const runId = String(eventMeta?.run_id || eventMeta?.trace_id || '').trim();
    finalizeStream(safeInstanceId, roleId, { runId });
    updateNormalModeSubagentSessionStatus(state.currentSessionId, safeInstanceId, status);
    markSubagentStatus(safeInstanceId, status);
    if (getActiveSubagentSession()?.instanceId === safeInstanceId) {
        settleActiveSubagentSessionAfterTerminal(safeInstanceId);
    }
    if (state.activeAgentInstanceId === safeInstanceId) {
        state.activeAgentInstanceId = null;
        state.activeAgentRoleId = null;
    }
}

export function handleRunCompleted(eventMeta) {
    sysLog('Run completed.');
    markLlmRetrySucceeded();
    const runId = eventMeta?.run_id || eventMeta?.trace_id || state.activeRunId || '';
    if (runId) {
        markRunTerminalState(runId, {
            status: 'completed',
            phase: 'terminal',
            recoverable: false,
        });
    }
    state.isGenerating = false;
    state.activeAgentRoleId = null;
    state.activeAgentInstanceId = null;
    if (els.sendBtn) els.sendBtn.disabled = !!state.activeSubagentSession;
    if (els.stopBtn) {
        els.stopBtn.disabled = true;
        els.stopBtn.style.display = 'none';
    }
    if (els.promptInput) {
        els.promptInput.disabled = !!state.activeSubagentSession;
        if (!state.activeSubagentSession) {
            els.promptInput.focus();
        }
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
    if (runId) {
        markRunTerminalState(runId, {
            status: 'stopped',
            phase: 'stopped',
            recoverable: true,
        });
    }
    state.isGenerating = false;
    state.activeAgentRoleId = null;
    state.activeAgentInstanceId = null;
    state.pausedSubagent = null;
    if (els.sendBtn) els.sendBtn.disabled = !!state.activeSubagentSession;
    if (els.stopBtn) {
        els.stopBtn.disabled = true;
        els.stopBtn.style.display = 'none';
    }
    if (els.promptInput) {
        els.promptInput.disabled = !!state.activeSubagentSession;
        if (!state.activeSubagentSession) {
            els.promptInput.focus();
        }
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
    if (runId) {
        markRunTerminalState(runId, {
            status: 'failed',
            phase: 'terminal',
            recoverable: false,
        });
    }
    state.isGenerating = false;
    state.activeAgentRoleId = null;
    state.activeAgentInstanceId = null;
    if (els.sendBtn) els.sendBtn.disabled = !!state.activeSubagentSession;
    if (els.stopBtn) {
        els.stopBtn.disabled = true;
        els.stopBtn.style.display = 'none';
    }
    if (els.promptInput) els.promptInput.disabled = !!state.activeSubagentSession;
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

function isNormalModeSubagentRun(runId, roleId) {
    const safeRunId = String(runId || '').trim();
    const safeRoleId = String(roleId || '').trim();
    return !!(
        state.currentSessionMode === 'normal'
        && safeRunId.startsWith('subagent_run_')
        && safeRoleId
        && !isRunPrimaryRoleId(safeRoleId, safeRunId)
    );
}
