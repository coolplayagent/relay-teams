/**
 * core/api/workspaces.js
 * Workspace and project related API wrappers.
 */
import { requestJson } from './request.js';

export async function fetchWorkspaces() {
    return requestJson('/api/workspaces', undefined, 'Failed to fetch projects');
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
