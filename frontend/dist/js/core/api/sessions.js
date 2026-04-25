/**
 * core/api/sessions.js
 * Session and history related API wrappers.
 */
import { requestJson } from './request.js';

export async function fetchSessions() {
    return requestJson('/api/sessions', undefined, 'Failed to fetch sessions');
}

export async function startNewSession(workspaceId) {
    return requestJson(
        '/api/sessions',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ workspace_id: workspaceId }),
        },
        'Failed to create session',
    );
}

export async function fetchSessionHistory(sessionId) {
    return requestJson(`/api/sessions/${sessionId}`, undefined, 'Failed to fetch session history');
}

export async function updateSession(sessionId, patch) {
    return requestJson(
        `/api/sessions/${sessionId}`,
        {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(patch),
        },
        'Failed to update session',
    );
}

export async function updateSessionTopology(sessionId, payload) {
    return requestJson(
        `/api/sessions/${sessionId}/topology`,
        {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to update session topology',
    );
}

export async function fetchSessionRounds(sessionId, { limit = 8, cursorRunId = null, timeline = false } = {}) {
    const params = new URLSearchParams();
    if (timeline) {
        params.set('timeline', 'true');
    } else {
        params.set('limit', String(limit));
    }
    if (cursorRunId) params.set('cursor_run_id', cursorRunId);
    const data = await requestJson(
        `/api/sessions/${sessionId}/rounds?${params.toString()}`,
        undefined,
        'Failed to fetch session rounds',
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

export async function fetchSessionRecovery(sessionId) {
    return requestJson(
        `/api/sessions/${sessionId}/recovery`,
        undefined,
        'Failed to fetch session recovery state',
    );
}

export async function fetchSessionAgents(sessionId) {
    return requestJson(`/api/sessions/${sessionId}/agents`, undefined, 'Failed to fetch session agents');
}

export async function fetchSessionSubagents(sessionId) {
    return requestJson(
        `/api/sessions/${sessionId}/subagents`,
        undefined,
        'Failed to fetch session subagents',
    );
}

export async function fetchSessionTasks(sessionId) {
    return requestJson(`/api/sessions/${sessionId}/tasks`, undefined, 'Failed to fetch session tasks');
}

export async function fetchAgentMessages(sessionId, instanceId) {
    return requestJson(
        `/api/sessions/${sessionId}/agents/${instanceId}/messages`,
        undefined,
        'Failed to fetch agent messages',
    );
}

export async function fetchAgentReflection(sessionId, instanceId) {
    return requestJson(
        `/api/sessions/${sessionId}/agents/${instanceId}/reflection`,
        undefined,
        'Failed to fetch agent reflection',
    );
}

export async function refreshAgentReflection(sessionId, instanceId) {
    return requestJson(
        `/api/sessions/${sessionId}/agents/${instanceId}/reflection:refresh`,
        { method: 'POST' },
        'Failed to refresh agent reflection',
    );
}

export async function updateAgentReflection(sessionId, instanceId, summary) {
    return requestJson(
        `/api/sessions/${sessionId}/agents/${instanceId}/reflection`,
        {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ summary }),
        },
        'Failed to update agent reflection',
    );
}

export async function deleteAgentReflection(sessionId, instanceId) {
    return requestJson(
        `/api/sessions/${sessionId}/agents/${instanceId}/reflection`,
        { method: 'DELETE' },
        'Failed to delete agent reflection',
    );
}

export async function deleteSession(sessionId) {
    return requestJson(
        `/api/sessions/${sessionId}`,
        {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ force: true, cascade: true }),
        },
        'Failed to delete session',
    );
}

export async function deleteSessionSubagent(sessionId, instanceId) {
    return requestJson(
        `/api/sessions/${sessionId}/subagents/${instanceId}`,
        { method: 'DELETE' },
        'Failed to delete subagent session',
    );
}

