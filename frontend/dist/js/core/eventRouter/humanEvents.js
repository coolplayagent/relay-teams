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
import { updateNormalModeSubagentSessionStatus } from '../../components/subagentSessions.js';

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

export function handleSubagentStopped(payload, eventMeta = null) {
    const instanceId = payload.instance_id;
    const runId = String(
        eventMeta?.run_id
        || eventMeta?.trace_id
        || payload.run_id
        || state.activeRunId
        || '',
    ).trim();
    markSubagentStatus(instanceId, 'stopped');
    updateNormalModeSubagentSessionStatus(state.currentSessionId, instanceId, 'stopped');
    state.pausedSubagent = {
        runId,
        instanceId,
        roleId: payload.role_id,
        taskId: payload.task_id || null,
    };
    markPausedSubagent({ ...payload, run_id: runId });
    sysLog(
        `Subagent paused: ${payload.role_id || payload.instance_id}. Send follow-up in subagent panel.`,
        'log-info',
    );
}

export function handleSubagentResumed(payload, eventMeta = null) {
    void eventMeta;
    const instanceId = payload.instance_id;
    markSubagentStatus(instanceId, 'running');
    updateNormalModeSubagentSessionStatus(state.currentSessionId, instanceId, 'running');
    if (state.pausedSubagent && state.pausedSubagent.instanceId === instanceId) {
        state.pausedSubagent = null;
    }
    clearPausedSubagent(instanceId);
    sysLog(`Subagent resumed: ${payload.role_id || payload.instance_id}`, 'log-info');
}
