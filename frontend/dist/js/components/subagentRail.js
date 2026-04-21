/**
 * components/subagentRail.js
 * Backward-compatible no-op facade. Subagent navigation now lives in the
 * left sidebar + main chat subagent view.
 */
import { ensureSessionSubagents } from './subagentSessions.js';

export function initializeSubagentRail() {
    return undefined;
}

export async function refreshSubagentRail(
    sessionId,
    { preserveSelection = true } = {},
) {
    void preserveSelection;
    const safeSessionId = typeof sessionId === 'string' ? sessionId.trim() : '';
    if (!safeSessionId) {
        return [];
    }
    return ensureSessionSubagents(safeSessionId, {
        force: true,
        emitLoadingEvents: false,
    });
}

export function rememberLiveSubagent(instanceId, roleId) {
    void instanceId;
    void roleId;
}

export function markSubagentStatus(instanceId, status) {
    void instanceId;
    void status;
}

export function selectSubagentRole(roleId, options = {}) {
    void roleId;
    void options;
}

export function focusSubagent(instanceId, roleId) {
    void instanceId;
    void roleId;
}

export function syncSelectedRoleByInstance(instanceId, roleId) {
    void instanceId;
    void roleId;
}

export function setSubagentRailExpanded(expanded) {
    void expanded;
}
