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

export async function deleteWorkspace(workspaceId) {
    return requestJson(
        `/api/workspaces/${encodeURIComponent(workspaceId)}`,
        {
            method: 'DELETE',
        },
        'Failed to remove project',
    );
}
