/**
 * core/eventRouter/humanEvents.js
 * Handlers for gate and subagent control events.
 */
import { state } from '../state.js';
import {
    clearPausedSubagent,
    markPausedSubagent,
} from '../../app/recovery.js';
import { sysLog } from '../../utils/logger.js';
import {
    removeGateCard,
    showGateCard,
} from '../../components/agentPanel.js';
import { markSubagentStatus } from '../../components/subagentRail.js';

export function handleSubagentGate(payload) {
    showGateCard(payload.instance_id, payload.role_id, {
        session_id: state.currentSessionId,
        run_id: state.activeRunId,
        task_id: payload.task_id,
        summary: payload.summary,
        role_id: payload.role_id,
    });
}

export function handleGateResolved(payload, instanceId) {
    removeGateCard(payload.instance_id || instanceId || '', payload.task_id);
    sysLog(`Gate resolved: ${payload.action}`, 'log-info');
}

export function handleSubagentStopped(payload) {
    markSubagentStatus(payload.instance_id, 'stopped');
    state.pausedSubagent = {
        runId: state.activeRunId,
        instanceId: payload.instance_id,
        roleId: payload.role_id,
        taskId: payload.task_id || null,
    };
    markPausedSubagent(payload);
    sysLog(
        `Subagent paused: ${payload.role_id || payload.instance_id}. Send follow-up in subagent panel.`,
        'log-info',
    );
}

export function handleSubagentResumed(payload) {
    markSubagentStatus(payload.instance_id, 'running');
    if (state.pausedSubagent && state.pausedSubagent.instanceId === payload.instance_id) {
        state.pausedSubagent = null;
    }
    clearPausedSubagent(payload.instance_id);
    sysLog(`Subagent resumed: ${payload.role_id || payload.instance_id}`, 'log-info');
}
