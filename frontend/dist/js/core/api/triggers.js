/**
 * core/api/triggers.js
 * Trigger and gateway-related API wrappers.
 */
import { requestJson } from './request.js';

export async function fetchTriggers() {
    const accounts = await requestJson(
        '/api/gateway/feishu/accounts',
        undefined,
        'Failed to fetch Feishu gateway accounts',
    );
    const rows = Array.isArray(accounts) ? accounts : [];
    return rows.map(normalizeFeishuGatewayAccount);
}

export async function createTrigger(payload) {
    return requestJson(
        '/api/gateway/feishu/accounts',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(toGatewayPayload(payload, { includeEnabled: true })),
        },
        'Failed to create Feishu gateway account',
    );
}

export async function updateTrigger(triggerId, payload) {
    return requestJson(
        `/api/gateway/feishu/accounts/${encodeURIComponent(triggerId)}`,
        {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(
                toGatewayPayload(payload, { includeEnabled: false }),
            ),
        },
        'Failed to update Feishu gateway account',
    );
}

export async function deleteTrigger(triggerId) {
    return requestJson(
        `/api/gateway/feishu/accounts/${encodeURIComponent(triggerId)}`,
        {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ force: true }),
        },
        'Failed to delete Feishu gateway account',
    );
}

export async function enableTrigger(triggerId) {
    return requestJson(
        `/api/gateway/feishu/accounts/${encodeURIComponent(triggerId)}:enable`,
        { method: 'POST' },
        'Failed to enable Feishu gateway account',
    );
}

export async function disableTrigger(triggerId) {
    return requestJson(
        `/api/gateway/feishu/accounts/${encodeURIComponent(triggerId)}:disable`,
        { method: 'POST' },
        'Failed to disable Feishu gateway account',
    );
}

export async function rotateTriggerToken(triggerId) {
    throw new Error(
        `Feishu gateway accounts do not support public token rotation: ${triggerId}`,
    );
}

export async function fetchGitHubTriggerAccounts() {
    return requestJson('/api/triggers/github/accounts', undefined, 'Failed to fetch GitHub trigger accounts');
}

export async function createGitHubTriggerAccount(payload) {
    return requestJson(
        '/api/triggers/github/accounts',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to create GitHub trigger account',
    );
}

export async function updateGitHubTriggerAccount(accountId, payload) {
    return requestJson(
        `/api/triggers/github/accounts/${encodeURIComponent(accountId)}`,
        {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to update GitHub trigger account',
    );
}

export async function deleteGitHubTriggerAccount(accountId) {
    return requestJson(
        `/api/triggers/github/accounts/${encodeURIComponent(accountId)}`,
        { method: 'DELETE' },
        'Failed to delete GitHub trigger account',
    );
}

export async function enableGitHubTriggerAccount(accountId) {
    return requestJson(
        `/api/triggers/github/accounts/${encodeURIComponent(accountId)}:enable`,
        { method: 'POST' },
        'Failed to enable GitHub trigger account',
    );
}

export async function disableGitHubTriggerAccount(accountId) {
    return requestJson(
        `/api/triggers/github/accounts/${encodeURIComponent(accountId)}:disable`,
        { method: 'POST' },
        'Failed to disable GitHub trigger account',
    );
}

export async function fetchGitHubRepoSubscriptions() {
    return requestJson('/api/triggers/github/repos', undefined, 'Failed to fetch GitHub repo subscriptions');
}

export async function fetchGitHubAccountRepositories(accountId, query = '') {
    const params = new URLSearchParams();
    const normalizedQuery = String(query || '').trim();
    if (normalizedQuery) {
        params.set('query', normalizedQuery);
    }
    const suffix = params.size > 0 ? `?${params.toString()}` : '';
    return requestJson(
        `/api/triggers/github/accounts/${encodeURIComponent(accountId)}/repositories${suffix}`,
        undefined,
        'Failed to fetch GitHub repositories',
    );
}

export async function createGitHubRepoSubscription(payload) {
    return requestJson(
        '/api/triggers/github/repos',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to create GitHub repo subscription',
    );
}

export async function updateGitHubRepoSubscription(repoSubscriptionId, payload) {
    return requestJson(
        `/api/triggers/github/repos/${encodeURIComponent(repoSubscriptionId)}`,
        {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to update GitHub repo subscription',
    );
}

export async function deleteGitHubRepoSubscription(repoSubscriptionId) {
    return requestJson(
        `/api/triggers/github/repos/${encodeURIComponent(repoSubscriptionId)}`,
        { method: 'DELETE' },
        'Failed to delete GitHub repo subscription',
    );
}

export async function enableGitHubRepoSubscription(repoSubscriptionId) {
    return requestJson(
        `/api/triggers/github/repos/${encodeURIComponent(repoSubscriptionId)}:enable`,
        { method: 'POST' },
        'Failed to enable GitHub repo subscription',
    );
}

export async function disableGitHubRepoSubscription(repoSubscriptionId) {
    return requestJson(
        `/api/triggers/github/repos/${encodeURIComponent(repoSubscriptionId)}:disable`,
        { method: 'POST' },
        'Failed to disable GitHub repo subscription',
    );
}

export async function fetchGitHubTriggerRules() {
    return requestJson('/api/triggers/github/rules', undefined, 'Failed to fetch GitHub trigger rules');
}

export async function createGitHubTriggerRule(payload) {
    return requestJson(
        '/api/triggers/github/rules',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to create GitHub trigger rule',
    );
}

export async function updateGitHubTriggerRule(triggerRuleId, payload) {
    return requestJson(
        `/api/triggers/github/rules/${encodeURIComponent(triggerRuleId)}`,
        {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to update GitHub trigger rule',
    );
}

export async function deleteGitHubTriggerRule(triggerRuleId) {
    return requestJson(
        `/api/triggers/github/rules/${encodeURIComponent(triggerRuleId)}`,
        { method: 'DELETE' },
        'Failed to delete GitHub trigger rule',
    );
}

export async function enableGitHubTriggerRule(triggerRuleId) {
    return requestJson(
        `/api/triggers/github/rules/${encodeURIComponent(triggerRuleId)}:enable`,
        { method: 'POST' },
        'Failed to enable GitHub trigger rule',
    );
}

export async function disableGitHubTriggerRule(triggerRuleId) {
    return requestJson(
        `/api/triggers/github/rules/${encodeURIComponent(triggerRuleId)}:disable`,
        { method: 'POST' },
        'Failed to disable GitHub trigger rule',
    );
}

function toGatewayPayload(payload, { includeEnabled = true } = {}) {
    const gatewayPayload = {
        name: String(payload?.name || '').trim(),
        display_name: payload?.display_name ?? null,
        source_config:
            payload?.source_config && typeof payload.source_config === 'object'
                ? { ...payload.source_config }
                : {},
        target_config:
            payload?.target_config && typeof payload.target_config === 'object'
                ? { ...payload.target_config }
                : {},
        secret_config:
            payload?.secret_config && typeof payload.secret_config === 'object'
                ? { ...payload.secret_config }
                : undefined,
    };
    if (includeEnabled) {
        gatewayPayload.enabled = payload?.enabled !== false;
    }
    return gatewayPayload;
}

function normalizeFeishuGatewayAccount(account) {
    return {
        trigger_id: String(account?.account_id || '').trim(),
        name: String(account?.name || '').trim(),
        display_name: String(account?.display_name || account?.name || '').trim(),
        source_type: 'im',
        status: String(account?.status || 'disabled').trim() || 'disabled',
        source_config:
            account?.source_config && typeof account.source_config === 'object'
                ? { ...account.source_config }
                : {},
        target_config:
            account?.target_config && typeof account.target_config === 'object'
                ? { ...account.target_config }
                : {},
        secret_config:
            account?.secret_config && typeof account.secret_config === 'object'
                ? { ...account.secret_config }
                : {},
        secret_status:
            account?.secret_status && typeof account.secret_status === 'object'
                ? { ...account.secret_status }
                : {},
    };
}
