/**
 * core/api/token_usage.js
 * Token usage API wrappers.
 */
import { invalidateManagedRequests, requestJsonManaged } from './request.js';

export async function fetchRunTokenUsage(sessionId, runId, options = {}) {
    try {
        return await requestJsonManaged(
            `sessions:${sessionId}:runs:${runId}:token-usage`,
            `/api/sessions/${sessionId}/runs/${runId}/token-usage`,
            {
                signal: options.signal,
            },
            'Failed to fetch run token usage',
            { ttlMs: 1500, lane: 'heavy' },
        );
    } catch (error) {
        if (error?.name === 'AbortError') {
            throw error;
        }
        return null;
    }
}

export async function fetchSessionTokenUsage(sessionId, options = {}) {
    try {
        const params = new URLSearchParams();
        if (options.forceRefresh === true) {
            params.set('force_refresh', 'true');
            invalidateManagedRequests(`sessions:${sessionId}:token-usage`);
        }
        const query = params.toString();
        return await requestJsonManaged(
            `sessions:${sessionId}:token-usage`,
            `/api/sessions/${sessionId}/token-usage${query ? `?${query}` : ''}`,
            {
                signal: options.signal,
            },
            'Failed to fetch session token usage',
            { ttlMs: 1500, lane: 'heavy' },
        );
    } catch (error) {
        if (error?.name === 'AbortError') {
            throw error;
        }
        return null;
    }
}
