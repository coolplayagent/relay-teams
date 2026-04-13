/**
 * core/api/triggers.js
 * Backward-compatible Feishu gateway API wrappers.
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
