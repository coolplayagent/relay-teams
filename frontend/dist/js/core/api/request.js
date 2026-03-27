/**
 * core/api/request.js
 * Shared HTTP request helper for JSON endpoints.
 */
import { markBackendOffline, markBackendOnline } from '../../utils/backendStatus.js';
import { errorToPayload, logError } from '../../utils/logger.js';

export async function requestJson(url, options, errorMessage) {
    const method = String(options?.method || 'GET').toUpperCase();
    const requestOptions = (method === 'GET' || method === 'HEAD') && options?.cache == null
        ? { ...options, cache: 'no-store' }
        : options;
    try {
        const res = await fetch(url, requestOptions);
        markBackendOnline();
        if (!res.ok) {
            let detail = errorMessage;
            try {
                const payload = await res.json();
                if (typeof payload?.detail === 'string' && payload.detail) {
                    detail = payload.detail;
                }
            } catch (_) {
                // keep fallback message
            }
            logError(
                'frontend.api.failed',
                detail,
                {
                    url,
                    method,
                    status: res.status,
                },
            );
            const error = new Error(detail);
            error.__agentTeamsLogged = true;
            error.status = res.status;
            error.detail = detail;
            error.url = url;
            error.method = method;
            throw error;
        }
        return res.json();
    } catch (error) {
        if (error?.__agentTeamsLogged === true) {
            throw error;
        }
        markBackendOffline();
        logError(
            'frontend.api.exception',
            errorMessage,
            errorToPayload(error, {
                url,
                method,
            }),
        );
        throw error;
    }
}
