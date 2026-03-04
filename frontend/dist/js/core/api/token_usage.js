/**
 * core/api/token_usage.js
 * Token usage API wrappers.
 */
import { requestJson } from './request.js';

export async function fetchRunTokenUsage(sessionId, runId) {
    try {
        return await requestJson(
            `/api/sessions/${sessionId}/runs/${runId}/token-usage`,
            undefined,
            'Failed to fetch run token usage',
        );
    } catch {
        return null;
    }
}

export async function fetchSessionTokenUsage(sessionId) {
    try {
        return await requestJson(
            `/api/sessions/${sessionId}/token-usage`,
            undefined,
            'Failed to fetch session token usage',
        );
    } catch {
        return null;
    }
}
