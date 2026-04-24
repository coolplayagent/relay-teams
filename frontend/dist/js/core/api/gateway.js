/**
 * core/api/gateway.js
 * Gateway-related API wrappers.
 */
import { requestJson } from './request.js';

export async function fetchWeChatGatewayAccounts() {
    return requestJson('/api/gateway/wechat/accounts', undefined, 'Failed to fetch WeChat gateway accounts');
}

export async function fetchXiaolubanGatewayAccounts() {
    return requestJson('/api/gateway/xiaoluban/accounts', undefined, 'Failed to fetch Xiaoluban gateway accounts');
}

export async function createXiaolubanGatewayAccount(payload) {
    return requestJson(
        '/api/gateway/xiaoluban/accounts',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to create Xiaoluban account',
    );
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

export async function updateXiaolubanGatewayAccount(accountId, payload) {
    return requestJson(
        `/api/gateway/xiaoluban/accounts/${encodeURIComponent(accountId)}`,
        {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to update Xiaoluban account',
    );
}

export async function enableWeChatGatewayAccount(accountId) {
    return requestJson(
        `/api/gateway/wechat/accounts/${encodeURIComponent(accountId)}:enable`,
        { method: 'POST' },
        'Failed to enable WeChat account',
    );
}

export async function enableXiaolubanGatewayAccount(accountId) {
    return requestJson(
        `/api/gateway/xiaoluban/accounts/${encodeURIComponent(accountId)}:enable`,
        { method: 'POST' },
        'Failed to enable Xiaoluban account',
    );
}

export async function disableWeChatGatewayAccount(accountId) {
    return requestJson(
        `/api/gateway/wechat/accounts/${encodeURIComponent(accountId)}:disable`,
        { method: 'POST' },
        'Failed to disable WeChat account',
    );
}

export async function disableXiaolubanGatewayAccount(accountId) {
    return requestJson(
        `/api/gateway/xiaoluban/accounts/${encodeURIComponent(accountId)}:disable`,
        { method: 'POST' },
        'Failed to disable Xiaoluban account',
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

export async function deleteXiaolubanGatewayAccount(accountId) {
    return requestJson(
        `/api/gateway/xiaoluban/accounts/${encodeURIComponent(accountId)}`,
        {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ force: true }),
        },
        'Failed to delete Xiaoluban account',
    );
}

export async function reloadWeChatGateway() {
    return requestJson(
        '/api/gateway/wechat/reload',
        { method: 'POST' },
        'Failed to reload WeChat gateway',
    );
}
