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

export async function createMemoryEvolutionDraft(workspaceId, payload, options = {}) {
    const safeWorkspaceId = String(workspaceId || '').trim();
    return requestJson(
        `/api/workspaces/${safeWorkspaceId}/memories/evolutions`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ...payload,
                workspace_id: safeWorkspaceId,
            }),
            signal: options.signal,
        },
        'Failed to create memory evolution draft',
    );
}

export async function applyMemoryEvolutionDraft(workspaceId, draftId, payload = {}, options = {}) {
    const safeWorkspaceId = String(workspaceId || '').trim();
    const safeDraftId = String(draftId || '').trim();
    return requestJson(
        `/api/workspaces/${safeWorkspaceId}/memories/evolutions/${safeDraftId}:apply`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            signal: options.signal,
        },
        'Failed to apply memory evolution draft',
    );
}

function buildSkillDraftQuery(filters = {}) {
    const params = new URLSearchParams();
    appendParam(params, 'scope_kind', filters.scopeKind || filters.scope_kind);
    appendParam(params, 'workspace_id', filters.workspaceId || filters.workspace_id);
    appendParam(params, 'status', filters.status);
    appendParam(params, 'draft_kind', filters.draftKind || filters.draft_kind);
    appendParam(params, 'text_query', filters.textQuery || filters.text_query);
    appendParam(params, 'limit', filters.limit);
    appendParam(params, 'offset', filters.offset);
    return params.toString();
}

export async function generateMemorySkillDrafts(payload, options = {}) {
    invalidateManagedRequests('memory-skill-drafts:');
    return requestJson(
        '/api/memories/skill-drafts:generate',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            signal: options.signal,
        },
        'Failed to generate memory skill drafts',
    );
}

export async function fetchMemorySkillDrafts(filters = {}, options = {}) {
    const query = buildSkillDraftQuery(filters);
    const url = query ? `/api/memories/skill-drafts?${query}` : '/api/memories/skill-drafts';
    return requestJsonManaged(
        `memory-skill-drafts:list:${query}`,
        url,
        { signal: options.signal },
        'Failed to fetch memory skill drafts',
        { ttlMs: 700, lane: 'heavy', priority: options.priority },
    );
}

export async function getMemorySkillDraft(draftId, options = {}) {
    const safeDraftId = String(draftId || '').trim();
    return requestJsonManaged(
        `memory-skill-drafts:entry:${safeDraftId}`,
        `/api/memories/skill-drafts/${encodeURIComponent(safeDraftId)}`,
        { signal: options.signal },
        'Failed to fetch memory skill draft',
        { ttlMs: 900, lane: 'heavy', priority: options.priority },
    );
}

export async function updateMemorySkillDraft(draftId, payload, options = {}) {
    const safeDraftId = String(draftId || '').trim();
    invalidateManagedRequests('memory-skill-drafts:');
    return requestJson(
        `/api/memories/skill-drafts/${encodeURIComponent(safeDraftId)}`,
        {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            signal: options.signal,
        },
        'Failed to update memory skill draft',
    );
}

export async function validateMemorySkillDraft(draftId, options = {}) {
    const safeDraftId = String(draftId || '').trim();
    invalidateManagedRequests('memory-skill-drafts:');
    return requestJson(
        `/api/memories/skill-drafts/${encodeURIComponent(safeDraftId)}:validate`,
        { method: 'POST', signal: options.signal },
        'Failed to validate memory skill draft',
    );
}

export async function applyMemorySkillDraft(draftId, options = {}) {
    const safeDraftId = String(draftId || '').trim();
    invalidateManagedRequests('memory-skill-drafts:');
    return requestJson(
        `/api/memories/skill-drafts/${encodeURIComponent(safeDraftId)}:apply`,
        { method: 'POST', signal: options.signal },
        'Failed to apply memory skill draft',
    );
}

export function invalidateMemoryCache() {
    invalidateManagedRequests('memories:');
    invalidateManagedRequests('memory-skill-drafts:');
}
