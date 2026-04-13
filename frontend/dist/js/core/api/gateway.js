/**
 * core/api/gateway.js
 * Gateway-related API wrappers.
 */
import { requestJson } from './request.js';

export async function fetchWeChatGatewayAccounts() {
    return requestJson('/api/gateway/wechat/accounts', undefined, 'Failed to fetch WeChat gateway accounts');
}

export async function startWeChatGatewayLogin(payload = {}) {
    return requestJson(
        '/api/gateway/wechat/login/start',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to start WeChat login',
    );
}

export async function waitWeChatGatewayLogin(payload) {
    return requestJson(
        '/api/gateway/wechat/login/wait',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to complete WeChat login',
    );
}

export async function updateWeChatGatewayAccount(accountId, payload) {
    return requestJson(
        `/api/gateway/wechat/accounts/${encodeURIComponent(accountId)}`,
        {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to update WeChat account',
    );
}

export async function enableWeChatGatewayAccount(accountId) {
    return requestJson(
        `/api/gateway/wechat/accounts/${encodeURIComponent(accountId)}:enable`,
        { method: 'POST' },
        'Failed to enable WeChat account',
    );
}

export async function disableWeChatGatewayAccount(accountId) {
    return requestJson(
        `/api/gateway/wechat/accounts/${encodeURIComponent(accountId)}:disable`,
        { method: 'POST' },
        'Failed to disable WeChat account',
    );
}

export async function deleteWeChatGatewayAccount(accountId) {
    return requestJson(
        `/api/gateway/wechat/accounts/${encodeURIComponent(accountId)}`,
        {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ force: true }),
        },
        'Failed to delete WeChat account',
    );
}

export async function reloadWeChatGateway() {
    return requestJson(
        '/api/gateway/wechat/reload',
        { method: 'POST' },
        'Failed to reload WeChat gateway',
    );
}
