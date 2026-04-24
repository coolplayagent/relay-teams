/**
 * core/api/system.js
 * System configuration related API wrappers.
 */
import { requestJson } from './request.js';

export async function fetchConfigStatus() {
    return requestJson('/api/system/configs', undefined, 'Failed to fetch config status');
}

export async function fetchSshProfiles() {
    return requestJson(
        '/api/system/configs/workspace/ssh-profiles',
        undefined,
        'Failed to fetch SSH profiles',
    );
}

export async function saveSshProfile(sshProfileId, config) {
    return requestJson(
        `/api/system/configs/workspace/ssh-profiles/${encodeURIComponent(sshProfileId)}`,
        {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config }),
        },
        'Failed to save SSH profile',
    );
}

export async function revealSshProfilePassword(sshProfileId) {
    return requestJson(
        `/api/system/configs/workspace/ssh-profiles/${encodeURIComponent(sshProfileId)}:reveal-password`,
        { method: 'POST' },
        'Failed to reveal SSH profile password',
    );
}

export async function probeSshProfileConnection(payload) {
    return requestJson(
        '/api/system/configs/workspace/ssh-profiles:probe',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to test SSH profile',
    );
}

export async function deleteSshProfile(sshProfileId) {
    return requestJson(
        `/api/system/configs/workspace/ssh-profiles/${encodeURIComponent(sshProfileId)}`,
        {
            method: 'DELETE',
        },
        'Failed to delete SSH profile',
    );
}

export async function fetchUiLanguageSettings() {
    return requestJson('/api/system/configs/ui-language', undefined, 'Failed to fetch UI language');
}

export async function saveUiLanguageSettings(payload) {
    return requestJson(
        '/api/system/configs/ui-language',
        {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to save UI language',
    );
}

export async function fetchEnvironmentVariables() {
    return requestJson(
        '/api/system/configs/environment-variables',
        undefined,
        'Failed to fetch environment variables',
    );
}

export async function fetchHookRuntimeView() {
    return requestJson(
        '/api/system/configs/hooks/runtime',
        undefined,
        'Failed to fetch loaded hooks',
    );
}

export async function fetchHooksConfig() {
    return requestJson(
        '/api/system/configs/hooks',
        undefined,
        'Failed to fetch hooks config',
    );
}

export async function saveHooksConfig(payload) {
    return requestJson(
        '/api/system/configs/hooks',
        {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to save hooks config',
    );
}

export async function validateHooksConfig(payload) {
    return requestJson(
        '/api/system/configs/hooks:validate',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to validate hooks config',
    );
}

export async function saveEnvironmentVariable(scope, key, payload) {
    return requestJson(
        `/api/system/configs/environment-variables/${encodeURIComponent(scope)}/${encodeURIComponent(key)}`,
        {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to save environment variable',
    );
}

export async function deleteEnvironmentVariable(scope, key) {
    return requestJson(
        `/api/system/configs/environment-variables/${encodeURIComponent(scope)}/${encodeURIComponent(key)}`,
        { method: 'DELETE' },
        'Failed to delete environment variable',
    );
}

export async function fetchProxyConfig() {
    return requestJson('/api/system/configs/proxy', undefined, 'Failed to fetch proxy config');
}

export async function fetchExternalAgents() {
    return requestJson('/api/system/configs/agents', undefined, 'Failed to fetch agents');
}

export async function fetchExternalAgent(agentId) {
    return requestJson(
        `/api/system/configs/agents/${encodeURIComponent(agentId)}`,
        undefined,
        'Failed to fetch agent config',
    );
}

export async function saveExternalAgent(agentId, payload) {
    return requestJson(
        `/api/system/configs/agents/${encodeURIComponent(agentId)}`,
        {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to save agent config',
    );
}

export async function deleteExternalAgent(agentId) {
    return requestJson(
        `/api/system/configs/agents/${encodeURIComponent(agentId)}`,
        { method: 'DELETE' },
        'Failed to delete agent config',
    );
}

export async function testExternalAgent(agentId) {
    return requestJson(
        `/api/system/configs/agents/${encodeURIComponent(agentId)}:test`,
        { method: 'POST' },
        'Failed to test agent config',
    );
}

export async function fetchWebConfig() {
    return requestJson('/api/system/configs/web', undefined, 'Failed to fetch web config');
}

export async function fetchGitHubConfig() {
    return requestJson('/api/system/configs/github', undefined, 'Failed to fetch GitHub config');
}

export async function revealGitHubToken() {
    return requestJson(
        '/api/system/configs/github:reveal',
        { method: 'POST' },
        'Failed to reveal GitHub token',
    );
}

export async function fetchGitHubWebhookTunnelStatus() {
    return requestJson(
        '/api/system/configs/github/webhook/tunnel',
        undefined,
        'Failed to fetch GitHub webhook tunnel status',
    );
}

export async function fetchClawHubConfig() {
    return requestJson('/api/system/configs/clawhub', undefined, 'Failed to fetch ClawHub config');
}

export async function saveClawHubConfig(config) {
    return requestJson(
        '/api/system/configs/clawhub',
        {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config),
        },
        'Failed to save ClawHub config',
    );
}

export async function fetchClawHubSkills() {
    return requestJson('/api/system/configs/clawhub/skills', undefined, 'Failed to fetch ClawHub skills');
}

export async function fetchClawHubSkill(skillId) {
    return requestJson(
        `/api/system/configs/clawhub/skills/${encodeURIComponent(skillId)}`,
        undefined,
        'Failed to fetch ClawHub skill',
    );
}

export async function saveClawHubSkill(skillId, payload) {
    return requestJson(
        `/api/system/configs/clawhub/skills/${encodeURIComponent(skillId)}`,
        {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to save ClawHub skill',
    );
}

export async function deleteClawHubSkill(skillId) {
    return requestJson(
        `/api/system/configs/clawhub/skills/${encodeURIComponent(skillId)}`,
        { method: 'DELETE' },
        'Failed to delete ClawHub skill',
    );
}

export async function fetchSystemHealth() {
    return requestJson('/api/system/health', undefined, 'Failed to fetch system health');
}

export async function fetchModelConfig() {
    return requestJson('/api/system/configs/model', undefined, 'Failed to fetch model config');
}

export async function fetchModelProfiles(options = {}) {
    return requestJson(
        '/api/system/configs/model/profiles',
        {
            signal: options.signal,
        },
        'Failed to fetch model profiles',
    );
}

export async function fetchModelFallbackConfig() {
    return requestJson(
        '/api/system/configs/model-fallback',
        undefined,
        'Failed to fetch model fallback config',
    );
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

export async function discoverModelCatalog(payload) {
    return requestJson(
        '/api/system/configs/model:discover',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to fetch available models',
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

export async function saveWebConfig(config) {
    return requestJson(
        '/api/system/configs/web',
        {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config),
        },
        'Failed to save web config',
    );
}

export async function saveGitHubConfig(config) {
    return requestJson(
        '/api/system/configs/github',
        {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config),
        },
        'Failed to save GitHub config',
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

export async function fetchOrchestrationConfig() {
    return requestJson('/api/system/configs/orchestration', undefined, 'Failed to fetch orchestration config');
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

export async function saveOrchestrationConfig(config) {
    return requestJson(
        '/api/system/configs/orchestration',
        {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config }),
        },
        'Failed to save orchestration config',
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

export async function probeGitHubConnectivity(payload) {
    return requestJson(
        '/api/system/configs/github:probe',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to probe GitHub connectivity',
    );
}

export async function probeGitHubWebhookConnectivity(payload) {
    return requestJson(
        '/api/system/configs/github/webhook:probe',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to probe GitHub webhook connectivity',
    );
}

export async function startGitHubWebhookTunnel(payload = {}) {
    return requestJson(
        '/api/system/configs/github/webhook/tunnel:start',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to start GitHub webhook tunnel',
    );
}

export async function stopGitHubWebhookTunnel(payload = {}) {
    return requestJson(
        '/api/system/configs/github/webhook/tunnel:stop',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to stop GitHub webhook tunnel',
    );
}

export async function probeClawHubConnectivity(payload) {
    return requestJson(
        '/api/system/configs/clawhub:probe',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to probe ClawHub connectivity',
    );
}
