/**
 * core/eventRouter/toolEvents.js
 * Handlers for tool call/result/approval events.
 */
import { markLlmRetrySucceeded } from '../../app/retryStatus.js';
import {
    markToolApprovalRequested,
    markToolApprovalResolved as markRecoveryToolApprovalResolved,
} from '../../app/recovery.js';
import { sysLog } from '../../utils/logger.js';
import {
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
    getPrimaryRoleId,
    getPrimaryRoleLabel,
    isPrimaryRoleId,
} from '../state.js';
import { coordinatorContainerFor } from './utils.js';

export function handleToolCall(payload, eventMeta, instanceId, roleId) {
    markLlmRetrySucceeded();
    const primaryRoleId = getPrimaryRoleId();
    const { container } = resolveToolEventTarget(instanceId, roleId, eventMeta);
    const isPrimary = !roleId || isPrimaryRoleId(roleId);
    if (!isPrimary && !getActiveInstanceId()) {
        openAgentPanel(instanceId, roleId);
    }
    const streamKey = isPrimary ? 'primary' : (instanceId || roleId);
    const runId = eventMeta?.run_id || eventMeta?.trace_id || '';
    const label = isPrimary ? getPrimaryRoleLabel() : (roleId || 'Agent');
    appendToolCallBlock(
        container,
        streamKey,
        payload.tool_name,
        payload.args,
        payload.tool_call_id || null,
        { runId, roleId: isPrimary ? primaryRoleId : roleId, label },
    );
    sysLog(`[Tool] ${payload.tool_name}`);
}

export function handleToolInputValidationFailed(payload, instanceId, eventMeta = null, roleId = '') {
    markLlmRetrySucceeded();
    const { container } = resolveToolEventTarget(instanceId, roleId, eventMeta);
    const isPrimary = !roleId || isPrimaryRoleId(roleId);
    const streamKey = isPrimary ? 'primary' : (instanceId || roleId);
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
    const { container } = resolveToolEventTarget(instanceId, roleId, eventMeta);
    const isPrimary = !roleId || isPrimaryRoleId(roleId);
    const streamKey = isPrimary ? 'primary' : (instanceId || roleId);
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
        {
            runId: eventMeta?.run_id || eventMeta?.trace_id || '',
            roleId,
            container,
        },
    );
}

export function handleToolApprovalRequested(payload, eventMeta, instanceId) {
    const roleId = payload?.role_id || '';
    const { container } = resolveToolEventTarget(instanceId, roleId, eventMeta);
    const isPrimary = !roleId || isPrimaryRoleId(roleId);
    const streamKey = isPrimary ? 'primary' : (instanceId || roleId);
    const runId = eventMeta?.run_id || eventMeta?.trace_id || '';
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
    const isPrimary = !roleId || isPrimaryRoleId(roleId);
    const streamKey = isPrimary ? 'primary' : (instanceId || roleId);
    markRecoveryToolApprovalResolved(payload?.tool_call_id || '');
    markToolApprovalResolved(streamKey, payload, {
        runId: eventMeta?.run_id || eventMeta?.trace_id || '',
        roleId,
        container,
    });
}

function resolveToolEventTarget(instanceId, roleId, eventMeta) {
    const isCoordinator = !roleId || isPrimaryRoleId(roleId);
    return {
        isCoordinator,
        container: isCoordinator
            ? coordinatorContainerFor(eventMeta)
            : getPanelScrollContainer(instanceId, roleId),
    };
}
