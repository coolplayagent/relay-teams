/**
 * core/api/triggers.js
 * Trigger-related API wrappers.
 */
import { requestJson } from './request.js';

export async function fetchTriggers() {
    return requestJson('/api/triggers', undefined, 'Failed to fetch triggers');
}

export async function createTrigger(payload) {
    return requestJson(
        '/api/triggers',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to create trigger',
    );
}

export async function updateTrigger(triggerId, payload) {
    return requestJson(
        `/api/triggers/${encodeURIComponent(triggerId)}`,
        {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to update trigger',
    );
}

export async function deleteTrigger(triggerId) {
    return requestJson(
        `/api/triggers/${encodeURIComponent(triggerId)}`,
        { method: 'DELETE' },
        'Failed to delete trigger',
    );
}

export async function enableTrigger(triggerId) {
    return requestJson(
        `/api/triggers/${encodeURIComponent(triggerId)}:enable`,
        { method: 'POST' },
        'Failed to enable trigger',
    );
}

export async function disableTrigger(triggerId) {
    return requestJson(
        `/api/triggers/${encodeURIComponent(triggerId)}:disable`,
        { method: 'POST' },
        'Failed to disable trigger',
    );
}

export async function rotateTriggerToken(triggerId) {
    return requestJson(
        `/api/triggers/${encodeURIComponent(triggerId)}:rotate-token`,
        { method: 'POST' },
        'Failed to rotate trigger token',
    );
}
