/**
 * core/api/observability.js
 */
import { requestJson } from './request.js';

export async function fetchObservabilityOverview({ scope = 'global', scopeId = '', timeWindowMinutes = 1440 } = {}) {
    const params = new URLSearchParams();
    params.set('scope', scope);
    params.set('scope_id', scopeId);
    params.set('time_window_minutes', String(timeWindowMinutes));
    return requestJson(
        `/api/observability/overview?${params.toString()}`,
        undefined,
        'Failed to fetch observability overview',
    );
}

export async function fetchObservabilityBreakdowns({ scope = 'global', scopeId = '', timeWindowMinutes = 1440 } = {}) {
    const params = new URLSearchParams();
    params.set('scope', scope);
    params.set('scope_id', scopeId);
    params.set('time_window_minutes', String(timeWindowMinutes));
    return requestJson(
        `/api/observability/breakdowns?${params.toString()}`,
        undefined,
        'Failed to fetch observability breakdowns',
    );
}
