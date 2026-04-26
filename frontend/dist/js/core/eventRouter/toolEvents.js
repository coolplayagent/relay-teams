/**
 * core/eventRouter/toolEvents.js
 * Handlers for tool call/result/approval events.
 */
import { markLlmRetrySucceeded } from '../../app/retryStatus.js';
import { scheduleCurrentSessionSubagentDiscovery } from '../stream.js';
import {
    markToolApprovalRequested,
    markToolApprovalResolved as markRecoveryToolApprovalResolved,
} from '../../app/recovery.js';
import { sysLog } from '../../utils/logger.js';
import {
    applyStreamOverlayEvent,
    appendToolCallBlock,
    attachToolApprovalControls,
    markToolApprovalResolved,
    markToolInputValidationFailed,
    updateToolResult,
} from '../../components/messageRenderer.js';
import {
    getActiveInstanceId,
    getPanelScrollContainer,
    openAgentPanel,
} from '../../components/agentPanel.js';
import {
    getActiveSubagentSessionStreamContainer,
} from '../../components/subagentSessions.js';
import {
    getRunPrimaryRoleId,
    getRunPrimaryRoleLabel,
    isRunPrimaryRoleId,
    state,
} from '../state.js';
import { coordinatorContainerFor } from './utils.js';

export function handleToolCall(payload, eventMeta, instanceId, roleId) {
    markLlmRetrySucceeded();
    const runId = eventMeta?.run_id || eventMeta?.trace_id || '';
    const primaryRoleId = getRunPrimaryRoleId(runId);
    const { container, isCoordinator } = resolveToolEventTarget(instanceId, roleId, eventMeta);
    const isPrimary = !roleId || isRunPrimaryRoleId(roleId, runId);
    const normalModeSubagent = isNormalModeSubagentRun(runId, roleId);
    if (!isPrimary && !getActiveInstanceId()) {
        if (!normalModeSubagent) {
            openAgentPanel(instanceId, roleId);
        }
    }
    const streamKey = isPrimary ? 'primary' : (instanceId || roleId);
    const label = isPrimary ? getRunPrimaryRoleLabel(runId) : (roleId || 'Agent');
    if (!container) {
        applyStreamOverlayEvent('tool_call', payload, {
            runId,
            instanceId: isPrimary ? 'primary' : instanceId,
            roleId: isPrimary ? primaryRoleId : roleId,
            label,
            eventId: eventMeta?.event_id || '',
        });
        return;
    }
    appendToolCallBlock(
        container,
        streamKey,
        payload.tool_name,
        payload.args,
        payload.tool_call_id || null,
        { runId, roleId: isPrimary ? primaryRoleId : roleId, label },
    );
    if (
        isCoordinator
        && state.currentSessionMode === 'normal'
        && String(payload?.tool_name || '').trim() === 'spawn_subagent'
    ) {
        scheduleCurrentSessionSubagentDiscovery({ delayMs: 0 });
    }
    sysLog(`[Tool] ${payload.tool_name}`);
}

export function handleToolInputValidationFailed(payload, instanceId, eventMeta = null, roleId = '') {
    markLlmRetrySucceeded();
    const { container } = resolveToolEventTarget(instanceId, roleId, eventMeta);
    const runId = eventMeta?.run_id || eventMeta?.trace_id || '';
    const isPrimary = !roleId || isRunPrimaryRoleId(roleId, runId);
    const streamKey = isPrimary ? 'primary' : (instanceId || roleId);
    if (!container) {
        applyStreamOverlayEvent('tool_input_validation_failed', payload, {
            runId,
            instanceId: isPrimary ? 'primary' : instanceId,
            roleId,
            eventId: eventMeta?.event_id || '',
        });
        return;
    }
    const bound = markToolInputValidationFailed(streamKey, payload, {
        runId: eventMeta?.run_id || eventMeta?.trace_id || '',
        roleId,
        container,
    });
    if (!bound) {
        sysLog(
            `Tool input validation failed (not executed): ${payload.tool_name}`,
            'log-info',
        );
    }
}

export function handleToolResult(payload, instanceId, eventMeta = null, roleId = '') {
    markLlmRetrySucceeded();
    const { container, isCoordinator } = resolveToolEventTarget(instanceId, roleId, eventMeta);
    const runId = eventMeta?.run_id || eventMeta?.trace_id || '';
    const primaryRoleId = getRunPrimaryRoleId(runId);
    const isPrimary = !roleId || isRunPrimaryRoleId(roleId, runId);
    const streamKey = isPrimary ? 'primary' : (instanceId || roleId);
    const label = isPrimary ? getRunPrimaryRoleLabel(runId) : (roleId || 'Agent');
    const resultEnvelope = payload.result || {};
    const isError = typeof resultEnvelope === 'object'
        ? resultEnvelope.ok === false
        : !!payload.error;
    if (!container) {
        applyStreamOverlayEvent('tool_result', payload, {
            runId,
            instanceId: isPrimary ? 'primary' : instanceId,
            roleId: isPrimary ? primaryRoleId : roleId,
            label,
            eventId: eventMeta?.event_id || '',
        });
        return;
    }
    updateToolResult(
        streamKey,
        payload.tool_name,
        resultEnvelope,
        isError,
        payload.tool_call_id || null,
        {
            runId: eventMeta?.run_id || eventMeta?.trace_id || '',
            roleId: isPrimary ? primaryRoleId : roleId,
            label,
            container,
        },
    );
    if (
        isCoordinator
        && state.currentSessionMode === 'normal'
        && String(payload?.tool_name || '').trim() === 'spawn_subagent'
    ) {
        scheduleCurrentSessionSubagentDiscovery({ delayMs: 0 });
    }
}

export function handleToolApprovalRequested(payload, eventMeta, instanceId) {
    const roleId = payload?.role_id || '';
    const { container } = resolveToolEventTarget(instanceId, roleId, eventMeta);
    const runId = eventMeta?.run_id || eventMeta?.trace_id || '';
    const isPrimary = !roleId || isRunPrimaryRoleId(roleId, runId);
    const streamKey = isPrimary ? 'primary' : (instanceId || roleId);
    markToolApprovalRequested(payload);
    if (runId && payload?.tool_call_id) {
        document.dispatchEvent(
            new CustomEvent('tool-approval-requested', {
                detail: {
                    runId,
                    toolCallId: payload.tool_call_id,
                },
            }),
        );
    }
    if (!container) {
        applyStreamOverlayEvent('tool_approval_requested', payload, {
            runId,
            instanceId: isPrimary ? 'primary' : instanceId,
            roleId,
            eventId: eventMeta?.event_id || '',
        });
        return;
    }
    const bound = attachToolApprovalControls(streamKey, payload.tool_name, payload, {}, {
        runId,
        roleId,
        container,
    });
    if (!bound) {
        sysLog(`Approval requested for ${payload.tool_name}`, 'log-info');
    }
}

export function handleToolApprovalResolved(payload, instanceId, eventMeta = null, roleId = '') {
    const { container } = resolveToolEventTarget(instanceId, roleId, eventMeta);
    const runId = eventMeta?.run_id || eventMeta?.trace_id || '';
    const isPrimary = !roleId || isRunPrimaryRoleId(roleId, runId);
    const streamKey = isPrimary ? 'primary' : (instanceId || roleId);
    markRecoveryToolApprovalResolved(payload?.tool_call_id || '');
    if (!container) {
        applyStreamOverlayEvent('tool_approval_resolved', payload, {
            runId,
            instanceId: isPrimary ? 'primary' : instanceId,
            roleId,
            eventId: eventMeta?.event_id || '',
        });
        return;
    }
    markToolApprovalResolved(streamKey, payload, {
        runId: eventMeta?.run_id || eventMeta?.trace_id || '',
        roleId,
        container,
    });
}

function resolveToolEventTarget(instanceId, roleId, eventMeta) {
    const runId = eventMeta?.run_id || eventMeta?.trace_id || '';
    const isCoordinator = !roleId || isRunPrimaryRoleId(roleId, runId);
    return {
        isCoordinator,
        container: isCoordinator
            ? (state.activeSubagentSession ? null : coordinatorContainerFor(eventMeta))
            : (
                isNormalModeSubagentRun(runId, roleId)
                    ? getActiveSubagentSessionStreamContainer(instanceId)
                    : getPanelScrollContainer(instanceId, roleId)
            ),
    };
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
