/**
 * core/api/roles.js
 * Role document settings API wrappers.
 */
import { requestJson } from './request.js';

export async function fetchRoleConfigs() {
    return requestJson('/api/roles/configs', undefined, 'Failed to fetch role configs');
}

export async function fetchRoleConfigOptions() {
    return requestJson('/api/roles:options', undefined, 'Failed to fetch role options');
}

export async function fetchRoleConfig(roleId) {
    return requestJson(`/api/roles/configs/${roleId}`, undefined, 'Failed to fetch role config');
}

export async function saveRoleConfig(roleId, payload) {
    return requestJson(
        `/api/roles/configs/${roleId}`,
        {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to save role config',
    );
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
