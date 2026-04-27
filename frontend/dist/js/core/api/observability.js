/**
 * core/api/observability.js
 */
import { requestJsonManaged } from './request.js';

export async function fetchObservabilityOverview({ scope = 'global', scopeId = '', timeWindowMinutes = 1440 } = {}) {
    const params = new URLSearchParams();
    params.set('scope', scope);
    params.set('scope_id', scopeId);
    params.set('time_window_minutes', String(timeWindowMinutes));
    return requestJsonManaged(
        `observability:overview:${scope}:${scopeId}:${timeWindowMinutes}`,
        `/api/observability/overview?${params.toString()}`,
        undefined,
        'Failed to fetch observability overview',
        { ttlMs: 1000, lane: 'heavy' },
    );
}

export async function fetchObservabilityBreakdowns({ scope = 'global', scopeId = '', timeWindowMinutes = 1440 } = {}) {
    const params = new URLSearchParams();
    params.set('scope', scope);
    params.set('scope_id', scopeId);
    params.set('time_window_minutes', String(timeWindowMinutes));
    return requestJsonManaged(
        `observability:breakdowns:${scope}:${scopeId}:${timeWindowMinutes}`,
        `/api/observability/breakdowns?${params.toString()}`,
        undefined,
        'Failed to fetch observability breakdowns',
        { ttlMs: 1000, lane: 'heavy' },
    );
}
