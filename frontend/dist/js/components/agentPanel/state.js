/**
 * components/agentPanel/state.js
 * Shared state for subagent drawer panels.
 */
const panels = new Map();
let activeInstanceId = null;
let activeRoundRunId = '';
let activeRoundPendingApprovals = [];

export function getPanels() {
    return panels;
}

export function getPanel(instanceId) {
    return panels.get(instanceId);
}

export function setPanel(instanceId, panel) {
    panels.set(instanceId, panel);
}

export function clearPanels() {
    panels.clear();
}

export function forEachPanel(cb) {
    panels.forEach(cb);
}

export function setActiveInstanceId(instanceId) {
    activeInstanceId = instanceId;
}

export function getActiveInstanceId() {
    return activeInstanceId;
}

export function setActiveRoundContext(runId, pendingApprovals) {
    activeRoundRunId = typeof runId === 'string' ? runId : '';
    activeRoundPendingApprovals = Array.isArray(pendingApprovals)
        ? pendingApprovals
        : [];
}

export function getActiveRoundRunId() {
    return activeRoundRunId;
}

export function getPendingApprovalsForPanel(instanceId, roleId) {
    return activeRoundPendingApprovals.filter(item => {
        const itemInstance = String(item?.instance_id || '');
        if (itemInstance && itemInstance === instanceId) return true;
        const itemRole = String(item?.assigned_role_id || item?.role_id || '');
        return !!roleId && itemRole === roleId;
    });
}
