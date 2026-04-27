/**
 * core/api/sessions.js
 * Session and history related API wrappers.
 */
import { invalidateManagedRequests, requestJson, requestJsonManaged } from './request.js';

export async function fetchSessions(options = {}) {
    if (options.forceRefresh === true) {
        invalidateManagedRequests('sessions:list');
    }
    return requestJsonManaged(
        'sessions:list',
        '/api/sessions',
        { signal: options.signal },
        'Failed to fetch sessions',
        { ttlMs: 500 },
    );
}

export async function startNewSession(workspaceId) {
    const result = await requestJson(
        '/api/sessions',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ workspace_id: workspaceId }),
        },
        'Failed to create session',
    );
    invalidateManagedRequests('sessions:');
    return result;
}

export async function fetchSessionHistory(sessionId, options = {}) {
    return requestJsonManaged(
        `sessions:${sessionId}:record`,
        `/api/sessions/${sessionId}`,
        { signal: options.signal },
        'Failed to fetch session history',
        { ttlMs: 300 },
    );
}

export async function markSessionTerminalRunViewed(sessionId, options = {}) {
    const safeSessionId = String(sessionId || '').trim();
    const result = await requestJson(
        `/api/sessions/${safeSessionId}/terminal-view`,
        {
            method: 'POST',
            signal: options.signal,
        },
        'Failed to mark session run viewed',
    );
    invalidateManagedRequests('sessions:list');
    invalidateManagedRequests(`sessions:${safeSessionId}:record`);
    return result;
}

export async function updateSession(sessionId, patch) {
    const result = await requestJson(
        `/api/sessions/${sessionId}`,
        {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(patch),
        },
        'Failed to update session',
    );
    invalidateManagedRequests('sessions:');
    return result;
}

export async function updateSessionTopology(sessionId, payload) {
    const result = await requestJson(
        `/api/sessions/${sessionId}/topology`,
        {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to update session topology',
    );
    invalidateManagedRequests('sessions:');
    return result;
}

export async function fetchSessionRounds(sessionId, { limit = 8, cursorRunId = null, timeline = false, signal = undefined } = {}) {
    const params = new URLSearchParams();
    if (timeline) {
        params.set('timeline', 'true');
    } else {
        params.set('limit', String(limit));
    }
    if (cursorRunId) params.set('cursor_run_id', cursorRunId);
    const query = params.toString();
    const data = await requestJsonManaged(
        `sessions:${sessionId}:rounds:${query}`,
        `/api/sessions/${sessionId}/rounds?${query}`,
        { signal },
        'Failed to fetch session rounds',
        { ttlMs: 300, lane: 'heavy' },
    );
    if (Array.isArray(data)) {
        return {
            items: data,
            has_more: false,
            next_cursor: null,
        };
    }
    return data;
}

export async function fetchSessionRecovery(sessionId, options = {}) {
    return requestJsonManaged(
        `sessions:${sessionId}:recovery`,
        `/api/sessions/${sessionId}/recovery`,
        { signal: options.signal },
        'Failed to fetch session recovery state',
        { ttlMs: 350, lane: 'heavy' },
    );
}

export function invalidateSessionRecovery(sessionId) {
    const safeSessionId = String(sessionId || '').trim();
    if (!safeSessionId) return;
    invalidateManagedRequests(`sessions:${safeSessionId}:recovery`);
}

export async function fetchSessionAgents(sessionId, options = {}) {
    return requestJsonManaged(
        `sessions:${sessionId}:agents`,
        `/api/sessions/${sessionId}/agents`,
        { signal: options.signal },
        'Failed to fetch session agents',
        { ttlMs: 500, lane: 'heavy' },
    );
}

export async function fetchSessionSubagents(sessionId, options = {}) {
    return requestJsonManaged(
        `sessions:${sessionId}:subagents`,
        `/api/sessions/${sessionId}/subagents`,
        { signal: options.signal },
        'Failed to fetch session subagents',
        { ttlMs: 500, lane: 'heavy' },
    );
}

export async function fetchSessionTasks(sessionId, options = {}) {
    return requestJsonManaged(
        `sessions:${sessionId}:tasks`,
        `/api/sessions/${sessionId}/tasks`,
        { signal: options.signal },
        'Failed to fetch session tasks',
        { ttlMs: 500, lane: 'heavy' },
    );
}

export async function fetchAgentMessages(sessionId, instanceId, options = {}) {
    return requestJsonManaged(
        `sessions:${sessionId}:agents:${instanceId}:messages`,
        `/api/sessions/${sessionId}/agents/${instanceId}/messages`,
        { signal: options.signal },
        'Failed to fetch agent messages',
        { ttlMs: 300, lane: 'heavy' },
    );
}

export async function fetchAgentReflection(sessionId, instanceId) {
    return requestJsonManaged(
        `sessions:${sessionId}:agents:${instanceId}:reflection`,
        `/api/sessions/${sessionId}/agents/${instanceId}/reflection`,
        undefined,
        'Failed to fetch agent reflection',
        { ttlMs: 1000, lane: 'heavy' },
    );
}

export async function refreshAgentReflection(sessionId, instanceId) {
    const result = await requestJson(
        `/api/sessions/${sessionId}/agents/${instanceId}/reflection:refresh`,
        { method: 'POST' },
        'Failed to refresh agent reflection',
    );
    invalidateManagedRequests(`sessions:${sessionId}:agents:${instanceId}:reflection`);
    return result;
}

export async function updateAgentReflection(sessionId, instanceId, summary) {
    const result = await requestJson(
        `/api/sessions/${sessionId}/agents/${instanceId}/reflection`,
        {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ summary }),
        },
        'Failed to update agent reflection',
    );
    invalidateManagedRequests(`sessions:${sessionId}:agents:${instanceId}:reflection`);
    return result;
}

export async function deleteAgentReflection(sessionId, instanceId) {
    const result = await requestJson(
        `/api/sessions/${sessionId}/agents/${instanceId}/reflection`,
        { method: 'DELETE' },
        'Failed to delete agent reflection',
    );
    invalidateManagedRequests(`sessions:${sessionId}:agents:${instanceId}:reflection`);
    return result;
}

export async function deleteSession(sessionId) {
    const result = await requestJson(
        `/api/sessions/${sessionId}`,
        {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ force: true, cascade: true }),
        },
        'Failed to delete session',
    );
    invalidateManagedRequests('sessions:');
    return result;
}

export async function deleteSessionSubagent(sessionId, instanceId) {
    const result = await requestJson(
        `/api/sessions/${sessionId}/subagents/${instanceId}`,
        { method: 'DELETE' },
        'Failed to delete subagent session',
    );
    invalidateManagedRequests('sessions:');
    return result;
}

