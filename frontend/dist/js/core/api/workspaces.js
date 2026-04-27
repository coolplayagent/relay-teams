/**
 * core/api/workspaces.js
 * Workspace and project related API wrappers.
 */
import { invalidateManagedRequests, requestJson, requestJsonManaged } from './request.js';

export async function fetchWorkspaces(options = {}) {
    return requestJsonManaged(
        'workspaces:list',
        '/api/workspaces',
        { signal: options.signal },
        'Failed to fetch projects',
        { ttlMs: 800 },
    );
}

export async function updateWorkspace(workspaceId, payload) {
    const result = await requestJson(
        `/api/workspaces/${encodeURIComponent(workspaceId)}`,
        {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to update project workspace',
    );
    invalidateManagedRequests('workspaces:');
    return result;
}

export function buildWorkspaceImagePreviewUrl(workspaceId, path) {
    const safeWorkspaceId = String(workspaceId || '').trim();
    const safePath = String(path || '').trim();
    if (!safeWorkspaceId || !safePath) {
        return '';
    }
    const query = new URLSearchParams({ path: safePath });
    return `/api/workspaces/${encodeURIComponent(safeWorkspaceId)}/preview-file?${query.toString()}`;
}

export async function fetchWorkspaceSnapshot(workspaceId, options = {}) {
    return requestJsonManaged(
        `workspaces:snapshot:${workspaceId}`,
        `/api/workspaces/${encodeURIComponent(workspaceId)}/snapshot`,
        { signal: options.signal },
        'Failed to fetch project workspace snapshot',
        { ttlMs: 800, lane: 'heavy' },
    );
}

export async function openWorkspaceRoot(workspaceId, mount = null) {
    const query = new URLSearchParams();
    const safeMount = String(mount || '').trim();
    if (safeMount) {
        query.set('mount', safeMount);
    }
    return requestJson(
        `/api/workspaces/${encodeURIComponent(workspaceId)}:open-root${query.toString() ? `?${query.toString()}` : ''}`,
        {
            method: 'POST',
        },
        'Failed to open project folder',
    );
}

export async function fetchWorkspaceTree(workspaceId, path = '.', mount = null) {
    const query = new URLSearchParams({ path: String(path || '.').trim() || '.' });
    const safeMount = String(mount || '').trim();
    if (safeMount) {
        query.set('mount', safeMount);
    }
    return requestJson(
        `/api/workspaces/${encodeURIComponent(workspaceId)}/tree?${query.toString()}`,
        undefined,
        'Failed to fetch project workspace tree',
    );
}

export async function searchWorkspacePaths(workspaceId, query = '', limit = 40, mount = null) {
    const params = new URLSearchParams({
        query: String(query || '').trim(),
        limit: String(Math.max(1, Math.min(Number(limit) || 40, 500))),
    });
    const safeMount = String(mount || '').trim();
    if (safeMount) {
        params.set('mount', safeMount);
    }
    return requestJson(
        `/api/workspaces/${encodeURIComponent(workspaceId)}/search?${params.toString()}`,
        undefined,
        'Failed to search project workspace',
    );
}

export async function fetchWorkspaceDiffs(workspaceId, mount = null) {
    const query = new URLSearchParams();
    const safeMount = String(mount || '').trim();
    if (safeMount) {
        query.set('mount', safeMount);
    }
    return requestJson(
        `/api/workspaces/${encodeURIComponent(workspaceId)}/diffs${query.toString() ? `?${query.toString()}` : ''}`,
        undefined,
        'Failed to fetch project workspace diffs',
    );
}

export async function fetchWorkspaceDiffFile(workspaceId, path, mount = null) {
    const query = new URLSearchParams({ path: String(path || '.').trim() || '.' });
    const safeMount = String(mount || '').trim();
    if (safeMount) {
        query.set('mount', safeMount);
    }
    return requestJson(
        `/api/workspaces/${encodeURIComponent(workspaceId)}/diff?${query.toString()}`,
        undefined,
        'Failed to fetch project workspace diff file',
    );
}

export async function pickWorkspace(rootPath = null) {
    const options = {
        method: 'POST',
    };
    if (typeof rootPath === 'string' && rootPath.trim()) {
        options.headers = { 'Content-Type': 'application/json' };
        options.body = JSON.stringify({ root_path: rootPath.trim() });
    }
    const result = await requestJson(
        '/api/workspaces/pick',
        options,
        'Failed to choose project directory',
    );
    invalidateManagedRequests('workspaces:');
    return result;
}

// Keep the legacy workspace picker name available for stale frontend imports.
export const openWorkspace = pickWorkspace;

export async function forkWorkspace(workspaceId, name) {
    const result = await requestJson(
        `/api/workspaces/${encodeURIComponent(workspaceId)}:fork`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: String(name || '').trim() }),
        },
        'Failed to fork project',
    );
    invalidateManagedRequests('workspaces:');
    return result;
}

export async function deleteWorkspace(workspaceId, options = {}) {
    const removeDirectory = options?.removeDirectory === true || options?.removeWorktree === true;
    const query = removeDirectory ? '?remove_directory=true' : '';
    const requestOptions = {
        method: 'DELETE',
    };
    if (removeDirectory) {
        requestOptions.headers = { 'Content-Type': 'application/json' };
        requestOptions.body = JSON.stringify({ force: true });
    }
    const result = await requestJson(
        `/api/workspaces/${encodeURIComponent(workspaceId)}${query}`,
        requestOptions,
        'Failed to remove project',
    );
    invalidateManagedRequests('workspaces:');
    return result;
}
