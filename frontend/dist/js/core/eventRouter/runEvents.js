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
    openSubagentSession,
    rememberSessionSubagent,
    settleActiveSubagentSessionAfterTerminal,
    updateSessionSubagentStatus,
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
    const fromProfile = escapeLogLabel(payload?.from_profile_id);
    const toProfile = escapeLogLabel(payload?.to_profile_id);
    if (fromProfile && toProfile) {
        sysLog(`Fallback activated: ${fromProfile} -> ${toProfile}`, 'log-info');
        return;
    }
    sysLog('Fallback activated.', 'log-info');
}

export function handleLlmFallbackExhausted(payload) {
    const fromProfile = escapeLogLabel(payload?.from_profile_id);
    if (fromProfile) {
        sysLog(`Fallback exhausted for ${fromProfile}.`, 'log-error');
        return;
    }
    sysLog('Fallback exhausted.', 'log-error');
}

export function handleModelStepStarted(eventMeta, instanceId, roleId) {
    beginLlmRetryAttempt();
    const runId = eventMeta?.run_id || eventMeta?.trace_id || state.activeRunId || '';
    const sessionSubagent = isSessionSubagentRun(runId, roleId);
    if (instanceId && roleId) {
        if (!state.instanceRoleMap) state.instanceRoleMap = {};
        if (!state.roleInstanceMap) state.roleInstanceMap = {};
        if (!state.autoSwitchedSubagentInstances) state.autoSwitchedSubagentInstances = {};
        state.instanceRoleMap[instanceId] = roleId;
        state.roleInstanceMap[roleId] = instanceId;
        if (!isRunPrimaryRoleId(roleId, runId) && !sessionSubagent) {
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
        if (sessionSubagent) {
            rememberSessionSubagent(state.currentSessionId, {
                instance_id: instanceId,
                role_id: roleId,
                run_id: runId,
                status: 'running',
            }, {
                autoActivate: state.currentSessionMode === 'orchestration',
            });
            if (state.currentSessionMode === 'orchestration') {
                rememberLiveSubagent(instanceId, roleId);
                void refreshSubagentRail(state.currentSessionId, {
                    preserveSelection: true,
                });
                if (!state.autoSwitchedSubagentInstances[instanceId]) {
                    state.autoSwitchedSubagentInstances[instanceId] = true;
                    void openSubagentSession(state.currentSessionId, {
                        sessionId: state.currentSessionId,
                        instanceId,
                        roleId,
                        runId,
                        status: 'running',
                    });
                }
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
    const sessionSubagent = isSessionSubagentRun(runId, roleId);

    if (isPrimary) {
        const container = coordinatorContainerFor(eventMeta);
        getOrCreateStreamBlock(container, streamKey, primaryRoleId, label, runId);
        appendStreamChunk(streamKey, payload.text || '', runId, primaryRoleId, label);
    } else {
        const container = sessionSubagent
            ? getActiveSubagentSessionStreamContainer(instanceId)
            : getPanelScrollContainer(instanceId, roleId);
        if (sessionSubagent && !container) {
            maybeAutoOpenSessionSubagent(runId, instanceId, roleId);
            applyStreamOverlayEvent('text_delta', payload, {
                runId,
                instanceId,
                roleId,
                label,
            });
            return;
        }
        if (!sessionSubagent && !getActiveInstanceId()) {
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
    const sessionSubagent = isSessionSubagentRun(runId, roleId);

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

    const container = sessionSubagent
        ? getActiveSubagentSessionStreamContainer(instanceId)
        : getPanelScrollContainer(instanceId, roleId);
    if (sessionSubagent && !container) {
        maybeAutoOpenSessionSubagent(runId, instanceId, roleId);
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
    if (!sessionSubagent && !getActiveInstanceId()) {
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
    const sessionSubagent = isSessionSubagentRun(runId, roleId);

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

    const container = sessionSubagent
        ? getActiveSubagentSessionStreamContainer(instanceId)
        : getPanelScrollContainer(instanceId, roleId);
    if (sessionSubagent && !container) {
        maybeAutoOpenSessionSubagent(runId, instanceId, roleId);
        applyStreamOverlayEvent('thinking_started', payload, {
            runId,
            instanceId,
            roleId,
            label,
        });
        return;
    }
    if (!sessionSubagent && !getActiveInstanceId()) {
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
    const sessionSubagent = isSessionSubagentRun(runId, roleId);

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

    const container = sessionSubagent
        ? getActiveSubagentSessionStreamContainer(instanceId)
        : getPanelScrollContainer(instanceId, roleId);
    if (sessionSubagent && !container) {
        maybeAutoOpenSessionSubagent(runId, instanceId, roleId);
        applyStreamOverlayEvent('thinking_delta', payload, {
            runId,
            instanceId,
            roleId,
            label,
        });
        return;
    }
    if (!sessionSubagent && !getActiveInstanceId()) {
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
    const sessionSubagent = isSessionSubagentRun(runId, roleId);

    if (!isPrimary && sessionSubagent && !getActiveSubagentSessionStreamContainer(instanceId)) {
        maybeAutoOpenSessionSubagent(runId, instanceId, roleId);
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
    const sessionSubagent = isSessionSubagentRun(runId, roleId);
    const key = isPrimary ? 'primary' : instanceId;
    if (!isPrimary && sessionSubagent) {
        updateSessionSubagentStatus(state.currentSessionId, instanceId, 'completed');
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
    updateSessionSubagentStatus(state.currentSessionId, safeInstanceId, status);
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

function isSessionSubagentRun(runId, roleId) {
    const safeRunId = String(runId || '').trim();
    const safeRoleId = String(roleId || '').trim();
    return !!(
        safeRoleId
        && !isRunPrimaryRoleId(safeRoleId, safeRunId)
        && (
            safeRunId.startsWith('subagent_run_')
            || state.currentSessionMode === 'orchestration'
        )
    );
}

function maybeAutoOpenSessionSubagent(runId, instanceId, roleId) {
    if (state.currentSessionMode !== 'orchestration') {
        return;
    }
    const safeSessionId = String(state.currentSessionId || '').trim();
    const safeInstanceId = String(instanceId || '').trim();
    const safeRoleId = String(roleId || '').trim();
    const safeRunId = String(runId || '').trim();
    if (!safeSessionId || !safeInstanceId || !safeRoleId || !safeRunId) {
        return;
    }
    if (!state.autoSwitchedSubagentInstances) {
        state.autoSwitchedSubagentInstances = {};
    }
    if (state.autoSwitchedSubagentInstances[safeInstanceId]) {
        return;
    }
    state.autoSwitchedSubagentInstances[safeInstanceId] = true;
    void openSubagentSession(safeSessionId, {
        sessionId: safeSessionId,
        instanceId: safeInstanceId,
        roleId: safeRoleId,
        runId: safeRunId,
        status: 'running',
    });
}

function escapeLogLabel(value) {
    return String(value || '')
        .trim()
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
