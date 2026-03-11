/**
 * core/api/request.js
 * Shared HTTP request helper for JSON endpoints.
 */
import { markBackendOffline, markBackendOnline } from '../../utils/backendStatus.js';
import { errorToPayload, logError } from '../../utils/logger.js';

export async function requestJson(url, options, errorMessage) {
    try {
        const res = await fetch(url, options);
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
                    method: options?.method || 'GET',
                    status: res.status,
                },
            );
            const error = new Error(detail);
            error.__agentTeamsLogged = true;
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
                method: options?.method || 'GET',
            }),
        );
        throw error;
    }
}
