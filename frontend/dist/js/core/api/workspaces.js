/**
 * core/api/workspaces.js
 * Workspace and project related API wrappers.
 */
import { requestJson } from './request.js';

export async function fetchWorkspaces() {
    return requestJson('/api/workspaces', undefined, 'Failed to fetch projects');
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

export async function fetchWorkspaceSnapshot(workspaceId) {
    return requestJson(
        `/api/workspaces/${encodeURIComponent(workspaceId)}/snapshot`,
        undefined,
        'Failed to fetch project workspace snapshot',
    );
}

export async function openWorkspaceRoot(workspaceId) {
    return requestJson(
        `/api/workspaces/${encodeURIComponent(workspaceId)}:open-root`,
        {
            method: 'POST',
        },
        'Failed to open project folder',
    );
}

export async function fetchWorkspaceTree(workspaceId, path = '.') {
    const query = new URLSearchParams({ path: String(path || '.').trim() || '.' });
    return requestJson(
        `/api/workspaces/${encodeURIComponent(workspaceId)}/tree?${query.toString()}`,
        undefined,
        'Failed to fetch project workspace tree',
    );
}

export async function fetchWorkspaceDiffs(workspaceId) {
    return requestJson(
        `/api/workspaces/${encodeURIComponent(workspaceId)}/diffs`,
        undefined,
        'Failed to fetch project workspace diffs',
    );
}

export async function fetchWorkspaceDiffFile(workspaceId, path) {
    const query = new URLSearchParams({ path: String(path || '.').trim() || '.' });
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
    return requestJson(
        '/api/workspaces/pick',
        options,
        'Failed to choose project directory',
    );
}

// Keep the legacy workspace picker name available for stale frontend imports.
export const openWorkspace = pickWorkspace;

export async function forkWorkspace(workspaceId, name) {
    return requestJson(
        `/api/workspaces/${encodeURIComponent(workspaceId)}:fork`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: String(name || '').trim() }),
        },
        'Failed to fork project',
    );
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
    return requestJson(
        `/api/workspaces/${encodeURIComponent(workspaceId)}${query}`,
        requestOptions,
        'Failed to remove project',
    );
}
