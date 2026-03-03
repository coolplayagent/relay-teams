/**
 * core/api.js
 * Centralized REST API fetching wrappers.
 */

export async function fetchSessions() {
    const res = await fetch('/api/sessions');
    if (!res.ok) throw new Error('Failed to fetch sessions');
    return res.json();
}

export async function startNewSession() {
    const res = await fetch('/api/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
    });
    if (!res.ok) throw new Error('Failed to create session');
    return res.json();
}

export async function fetchSessionHistory(sessionId) {
    const res = await fetch(`/api/sessions/${sessionId}`);
    if (!res.ok) throw new Error('Failed to fetch session history');
    return res.json();
}

export async function fetchSessionWorkflows(sessionId) {
    const res = await fetch(`/api/sessions/${sessionId}/workflows`);
    if (!res.ok) throw new Error('Failed to fetch session workflows');
    return res.json();
}

export async function fetchSessionRounds(sessionId, { limit = 8, cursorRunId = null } = {}) {
    const params = new URLSearchParams();
    params.set('limit', String(limit));
    if (cursorRunId) params.set('cursor_run_id', cursorRunId);
    const res = await fetch(`/api/sessions/${sessionId}/rounds?${params.toString()}`);
    if (!res.ok) throw new Error('Failed to fetch session rounds');
    const data = await res.json();
    if (Array.isArray(data)) {
        return {
            items: data,
            has_more: false,
            next_cursor: null,
        };
    }
    return data;
}

export async function fetchSessionAgents(sessionId) {
    const res = await fetch(`/api/sessions/${sessionId}/agents`);
    if (!res.ok) throw new Error('Failed to fetch session agents');
    return res.json();
}

export async function fetchAgentMessages(sessionId, instanceId) {
    const res = await fetch(`/api/sessions/${sessionId}/agents/${instanceId}/messages`);
    if (!res.ok) throw new Error('Failed to fetch agent messages');
    return res.json();
}

export async function sendUserPrompt(sessionId, prompt, { executionMode = 'ai', confirmationGate = false } = {}) {
    const res = await fetch('/api/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            intent: prompt,
            session_id: sessionId,
            execution_mode: executionMode,
            confirmation_gate: confirmationGate,
        }),
    });
    if (!res.ok) throw new Error('Failed to create run');
    return res.json();
}

export async function resolveGate(runId, taskId, action, feedback = '') {
    const res = await fetch(`/api/runs/${runId}/gates/${taskId}/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, feedback }),
    });
    if (!res.ok) throw new Error('Failed to resolve gate');
    return res.json();
}

export async function resolveToolApproval(runId, toolCallId, action, feedback = '') {
    const res = await fetch(`/api/runs/${runId}/tool-approvals/${toolCallId}/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, feedback }),
    });
    if (!res.ok) throw new Error('Failed to resolve tool approval');
    return res.json();
}

export async function dispatchHumanTask(sessionId, runId, taskId) {
    const res = await fetch(`/api/runs/${runId}/dispatch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_id: taskId, session_id: sessionId }),
    });
    if (!res.ok) throw new Error('Failed to dispatch task');
    return res.json();
}

export async function injectMessage(runId, content) {
    const res = await fetch(`/api/runs/${runId}/inject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
    });
    if (!res.ok) throw new Error('Failed to inject message');
    return res.json();
}

export async function deleteSession(sessionId) {
    const res = await fetch(`/api/sessions/${sessionId}`, {
        method: 'DELETE',
    });
    if (!res.ok) throw new Error('Failed to delete session');
    return res.json();
}

export async function fetchConfigStatus() {
    const res = await fetch('/api/system/configs');
    if (!res.ok) throw new Error('Failed to fetch config status');
    return res.json();
}

export async function fetchModelConfig() {
    const res = await fetch('/api/system/configs/model');
    if (!res.ok) throw new Error('Failed to fetch model config');
    return res.json();
}

export async function fetchModelProfiles() {
    const res = await fetch('/api/system/configs/model/profiles');
    if (!res.ok) throw new Error('Failed to fetch model profiles');
    return res.json();
}

export async function saveModelProfile(name, profile) {
    const res = await fetch(`/api/system/configs/model/profiles/${name}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(profile),
    });
    if (!res.ok) throw new Error('Failed to save model profile');
    return res.json();
}

export async function deleteModelProfile(name) {
    const res = await fetch(`/api/system/configs/model/profiles/${name}`, {
        method: 'DELETE',
    });
    if (!res.ok) throw new Error('Failed to delete model profile');
    return res.json();
}

export async function saveModelConfig(config) {
    const res = await fetch('/api/system/configs/model', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config }),
    });
    if (!res.ok) throw new Error('Failed to save model config');
    return res.json();
}

export async function reloadModelConfig() {
    const res = await fetch('/api/system/configs/model:reload', {
        method: 'POST',
    });
    if (!res.ok) throw new Error('Failed to reload model config');
    return res.json();
}

export async function reloadMcpConfig() {
    const res = await fetch('/api/system/configs/mcp:reload', {
        method: 'POST',
    });
    if (!res.ok) throw new Error('Failed to reload MCP config');
    return res.json();
}

export async function reloadSkillsConfig() {
    const res = await fetch('/api/system/configs/skills:reload', {
        method: 'POST',
    });
    if (!res.ok) throw new Error('Failed to reload skills config');
    return res.json();
}
