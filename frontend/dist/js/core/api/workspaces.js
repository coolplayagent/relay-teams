/**
 * core/api/workspaces.js
 * Workspace and project related API wrappers.
 */
import { requestJson } from './request.js';

export async function fetchWorkspaces() {
    return requestJson('/api/workspaces', undefined, 'Failed to fetch projects');
}

export async function pickWorkspace() {
    return requestJson(
        '/api/workspaces/pick',
        {
            method: 'POST',
        },
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
