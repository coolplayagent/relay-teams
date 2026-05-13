/**
 * core/api/sessions.js
 * Session and history related API wrappers.
 */
import {
    invalidateManagedRequestCache,
    invalidateManagedRequests,
    requestJson,
    requestJsonManaged,
} from './request.js';

export async function fetchSessions(options = {}) {
    const params = new URLSearchParams();
    if (options.forceRefresh === true) {
        params.set('force_refresh', 'true');
        invalidateManagedRequests('sessions:list');
    }
    const query = params.toString();
    return requestJsonManaged(
        'sessions:list',
        `/api/sessions${query ? `?${query}` : ''}`,
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
    invalidateManagedRequestCache('sessions:');
    return result;
}

export async function fetchSessionHistory(sessionId, options = {}) {
    return requestJsonManaged(
        `sessions:${sessionId}:record`,
        `/api/sessions/${sessionId}`,
        { signal: options.signal },
        'Failed to fetch session history',
        {
            lane: requestLaneForPriority(options.priority),
            priority: options.priority,
            ttlMs: 300,
        },
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
    invalidateManagedRequestCache('sessions:list');
    invalidateManagedRequestCache(`sessions:${safeSessionId}:record`);
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

export async function fetchSessionRounds(
    sessionId,
    {
        limit = 8,
        cursorRunId = null,
        priority = '',
        timeline = false,
        summary = false,
        signal = undefined,
        forceRefresh = false,
    } = {},
) {
    const params = new URLSearchParams();
    if (timeline) {
        params.set('timeline', 'true');
    } else {
        params.set('limit', String(limit));
    }
    if (summary) {
        params.set('summary', 'true');
    }
    if (cursorRunId) params.set('cursor_run_id', cursorRunId);
    if (forceRefresh === true) {
        params.set('force_refresh', 'true');
        invalidateManagedRequests(`sessions:${sessionId}:rounds:`);
    }
    const query = params.toString();
    const data = await requestJsonManaged(
        `sessions:${sessionId}:rounds:${query}`,
        `/api/sessions/${sessionId}/rounds?${query}`,
        { signal },
        'Failed to fetch session rounds',
        {
            lane: requestLaneForPriority(priority) || 'heavy',
            priority,
            ttlMs: 300,
        },
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

export async function fetchSessionRound(sessionId, runId, options = {}) {
    const safeSessionId = String(sessionId || '').trim();
    const safeRunId = String(runId || '').trim();
    return requestJson(
        `/api/sessions/${safeSessionId}/rounds/${safeRunId}`,
        { signal: options.signal },
        'Failed to fetch session round',
    );
}

export async function fetchSessionRecovery(sessionId, options = {}) {
    const params = new URLSearchParams();
    if (options.forceRefresh === true) {
        params.set('force_refresh', 'true');
        invalidateManagedRequests(`sessions:${sessionId}:recovery`);
    }
    const query = params.toString();
    return requestJsonManaged(
        `sessions:${sessionId}:recovery`,
        `/api/sessions/${sessionId}/recovery${query ? `?${query}` : ''}`,
        { signal: options.signal },
        'Failed to fetch session recovery state',
        {
            lane: requestLaneForPriority(options.priority) || 'heavy',
            priority: options.priority,
            ttlMs: 350,
        },
    );
}

export function invalidateSessionRecovery(sessionId) {
    const safeSessionId = String(sessionId || '').trim();
    if (!safeSessionId) return;
    invalidateManagedRequests(`sessions:${safeSessionId}:recovery`);
}

export async function fetchSessionAgents(sessionId, options = {}) {
    const params = new URLSearchParams();
    if (options.forceRefresh === true) {
        params.set('force_refresh', 'true');
        invalidateManagedRequests(`sessions:${sessionId}:agents`);
    }
    const query = params.toString();
    return requestJsonManaged(
        `sessions:${sessionId}:agents`,
        `/api/sessions/${sessionId}/agents${query ? `?${query}` : ''}`,
        { signal: options.signal },
        'Failed to fetch session agents',
        {
            lane: requestLaneForPriority(options.priority) || 'heavy',
            priority: options.priority,
            ttlMs: 500,
        },
    );
}

export async function fetchSessionSubagents(sessionId, options = {}) {
    const params = new URLSearchParams();
    if (options.forceRefresh === true) {
        params.set('force_refresh', 'true');
        invalidateManagedRequests(`sessions:${sessionId}:subagents`);
    }
    const query = params.toString();
    return requestJsonManaged(
        `sessions:${sessionId}:subagents`,
        `/api/sessions/${sessionId}/subagents${query ? `?${query}` : ''}`,
        { signal: options.signal },
        'Failed to fetch session subagents',
        {
            lane: requestLaneForPriority(options.priority) || 'heavy',
            priority: options.priority,
            ttlMs: 500,
        },
    );
}

export async function fetchSessionTasks(sessionId, options = {}) {
    const params = new URLSearchParams();
    if (options.forceRefresh === true) {
        params.set('force_refresh', 'true');
        invalidateManagedRequests(`sessions:${sessionId}:tasks`);
    }
    const query = params.toString();
    return requestJsonManaged(
        `sessions:${sessionId}:tasks`,
        `/api/sessions/${sessionId}/tasks${query ? `?${query}` : ''}`,
        { signal: options.signal },
        'Failed to fetch session tasks',
        {
            lane: requestLaneForPriority(options.priority) || 'heavy',
            priority: options.priority,
            ttlMs: 500,
        },
    );
}

function requestLaneForPriority(priority) {
    return String(priority || '').trim() === 'high' ? 'critical' : '';
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
