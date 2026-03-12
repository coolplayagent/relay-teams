/**
 * core/api/system.js
 * System configuration related API wrappers.
 */
import { requestJson } from './request.js';

export async function fetchConfigStatus() {
    return requestJson('/api/system/configs', undefined, 'Failed to fetch config status');
}

export async function fetchProxyConfig() {
    return requestJson('/api/system/configs/proxy', undefined, 'Failed to fetch proxy config');
}

export async function fetchSystemHealth() {
    return requestJson('/api/system/health', undefined, 'Failed to fetch system health');
}

export async function fetchModelConfig() {
    return requestJson('/api/system/configs/model', undefined, 'Failed to fetch model config');
}

export async function fetchModelProfiles() {
    return requestJson('/api/system/configs/model/profiles', undefined, 'Failed to fetch model profiles');
}

export async function probeModelConnection(payload) {
    return requestJson(
        '/api/system/configs/model:probe',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to probe model connectivity',
    );
}

export async function saveModelProfile(name, profile) {
    return requestJson(
        `/api/system/configs/model/profiles/${name}`,
        {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(profile),
        },
        'Failed to save model profile',
    );
}

export async function deleteModelProfile(name) {
    return requestJson(
        `/api/system/configs/model/profiles/${name}`,
        { method: 'DELETE' },
        'Failed to delete model profile',
    );
}

export async function saveModelConfig(config) {
    return requestJson(
        '/api/system/configs/model',
        {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config }),
        },
        'Failed to save model config',
    );
}

export async function reloadModelConfig() {
    return requestJson(
        '/api/system/configs/model:reload',
        { method: 'POST' },
        'Failed to reload model config',
    );
}

export async function reloadProxyConfig() {
    return requestJson(
        '/api/system/configs/proxy:reload',
        { method: 'POST' },
        'Failed to reload proxy config',
    );
}

export async function saveProxyConfig(config) {
    return requestJson(
        '/api/system/configs/proxy',
        {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config),
        },
        'Failed to save proxy config',
    );
}

export async function reloadMcpConfig() {
    return requestJson(
        '/api/system/configs/mcp:reload',
        { method: 'POST' },
        'Failed to reload MCP config',
    );
}

export async function fetchMcpServerTools(serverName) {
    return requestJson(
        `/api/mcp/servers/${encodeURIComponent(serverName)}/tools`,
        undefined,
        `Failed to fetch MCP tools for ${serverName}`,
    );
}

export async function reloadSkillsConfig() {
    return requestJson(
        '/api/system/configs/skills:reload',
        { method: 'POST' },
        'Failed to reload skills config',
    );
}

export async function fetchNotificationConfig() {
    return requestJson('/api/system/configs/notifications', undefined, 'Failed to fetch notification config');
}

export async function saveNotificationConfig(config) {
    return requestJson(
        '/api/system/configs/notifications',
        {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config }),
        },
        'Failed to save notification config',
    );
}

export async function probeWebConnectivity(payload) {
    return requestJson(
        '/api/system/configs/web:probe',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to probe web connectivity',
    );
}
