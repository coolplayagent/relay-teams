/**
 * core/api/memories.js
 * Memory Bank API wrappers.
 */
import {
    invalidateManagedRequests,
    requestJson,
    requestJsonManaged,
} from './request.js';

function appendParam(params, key, value) {
    const text = String(value || '').trim();
    if (text) {
        params.set(key, text);
    }
}

function buildMemoryQuery(filters = {}) {
    const params = new URLSearchParams();
    appendParam(params, 'workspace_id', filters.workspaceId || filters.workspace_id);
    appendParam(params, 'tier', filters.tier);
    appendParam(params, 'scope', filters.scope);
    appendParam(params, 'session_id', filters.sessionId || filters.session_id);
    appendParam(params, 'role_id', filters.roleId || filters.role_id);
    appendParam(params, 'kind', filters.kind);
    appendParam(params, 'status', filters.status);
    appendParam(params, 'tags', filters.tags);
    appendParam(params, 'min_confidence', filters.minConfidence || filters.min_confidence);
    appendParam(params, 'limit', filters.limit);
    appendParam(params, 'offset', filters.offset);
    return params.toString();
}

export async function fetchMemories(filters = {}, options = {}) {
    const query = buildMemoryQuery(filters);
    const url = query ? `/api/memories?${query}` : '/api/memories';
    return requestJsonManaged(
        `memories:list:${query}`,
        url,
        { signal: options.signal },
        'Failed to fetch memories',
        { ttlMs: 700, lane: 'heavy', priority: options.priority },
    );
}

export async function searchMemories(payload, options = {}) {
    return requestJson(
        '/api/memories/search',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            signal: options.signal,
        },
        'Failed to search memories',
    );
}

export async function getMemory(workspaceId, memoryId, options = {}) {
    const safeWorkspaceId = String(workspaceId || '').trim();
    const safeMemoryId = String(memoryId || '').trim();
    return requestJsonManaged(
        `memories:entry:${safeWorkspaceId}:${safeMemoryId}`,
        `/api/workspaces/${safeWorkspaceId}/memories/${safeMemoryId}`,
        { signal: options.signal },
        'Failed to fetch memory',
        { ttlMs: 900, lane: 'heavy', priority: options.priority },
    );
}

export function invalidateMemoryCache() {
    invalidateManagedRequests('memories:');
}
