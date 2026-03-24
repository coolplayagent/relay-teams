/**
 * core/api/workspaces.js
 * Workspace and project related API wrappers.
 */
import { requestJson } from './request.js';

export async function fetchWorkspaces() {
    return requestJson('/api/workspaces', undefined, 'Failed to fetch projects');
}

export async function fetchWorkspaceSnapshot(workspaceId) {
    return requestJson(
        `/api/workspaces/${encodeURIComponent(workspaceId)}/snapshot`,
        undefined,
        'Failed to fetch project workspace snapshot',
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
    const removeWorktree = options?.removeWorktree === true;
    const query = removeWorktree ? '?remove_worktree=true' : '';
    return requestJson(
        `/api/workspaces/${encodeURIComponent(workspaceId)}${query}`,
        {
            method: 'DELETE',
        },
        'Failed to remove project',
    );
}
