/**
 * core/api/roles.js
 * Role document settings API wrappers.
 */
import { invalidateManagedRequests, requestJson, requestJsonManaged } from './request.js';

export async function fetchRoleConfigs(options = {}) {
    return requestJsonManaged(
        'roles:configs',
        '/api/roles/configs',
        { signal: options.signal },
        'Failed to fetch role configs',
        { ttlMs: 30000 },
    );
}

export async function fetchRoleConfigOptions(options = {}) {
    return requestJsonManaged(
        'roles:options',
        '/api/roles:options',
        { signal: options.signal },
        'Failed to fetch role options',
        { ttlMs: 30000 },
    );
}

export async function fetchRoleConfig(roleId) {
    return requestJson(`/api/roles/configs/${roleId}`, undefined, 'Failed to fetch role config');
}

export async function deleteRoleConfig(roleId) {
    const result = await requestJson(
        `/api/roles/configs/${roleId}`,
        {
            method: 'DELETE',
        },
        'Failed to delete role config',
    );
    invalidateManagedRequests('roles:');
    return result;
}

export async function saveRoleConfig(roleId, payload) {
    const result = await requestJson(
        `/api/roles/configs/${roleId}`,
        {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to save role config',
    );
    invalidateManagedRequests('roles:');
    return result;
}

export async function validateRoleConfig(payload) {
    return requestJson(
        '/api/roles:validate-config',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to validate role config',
    );
}
