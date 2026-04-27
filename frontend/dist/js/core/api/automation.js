/**
 * core/api/automation.js
 * Automation project related API wrappers.
 */
import { invalidateManagedRequests, requestJson, requestJsonManaged } from './request.js';

export async function fetchAutomationDeliveryBindings() {
    return requestJson(
        '/api/automation/delivery-bindings',
        undefined,
        'Failed to fetch automation delivery bindings',
    );
}

export async function fetchAutomationFeishuBindings() {
    return fetchAutomationDeliveryBindings();
}

export async function fetchAutomationProjects(options = {}) {
    return requestJsonManaged(
        'automation:projects',
        '/api/automation/projects',
        { signal: options.signal },
        'Failed to fetch automation projects',
        { ttlMs: 800 },
    );
}

export async function createAutomationProject(payload) {
    const result = await requestJson(
        '/api/automation/projects',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to create automation project',
    );
    invalidateManagedRequests('automation:');
    return result;
}

export async function fetchAutomationProject(automationProjectId) {
    return requestJson(
        `/api/automation/projects/${encodeURIComponent(automationProjectId)}`,
        undefined,
        'Failed to fetch automation project',
    );
}

export async function updateAutomationProject(automationProjectId, payload) {
    const result = await requestJson(
        `/api/automation/projects/${encodeURIComponent(automationProjectId)}`,
        {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to update automation project',
    );
    invalidateManagedRequests('automation:');
    return result;
}

export async function deleteAutomationProject(automationProjectId) {
    const result = await requestJson(
        `/api/automation/projects/${encodeURIComponent(automationProjectId)}`,
        {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ force: true, cascade: true }),
        },
        'Failed to delete automation project',
    );
    invalidateManagedRequests('automation:');
    return result;
}

export async function runAutomationProject(automationProjectId) {
    const result = await requestJson(
        `/api/automation/projects/${encodeURIComponent(automationProjectId)}:run`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
        },
        'Failed to run automation project',
    );
    invalidateManagedRequests('automation:');
    invalidateManagedRequests('sessions:');
    return result;
}

export async function enableAutomationProject(automationProjectId) {
    const result = await requestJson(
        `/api/automation/projects/${encodeURIComponent(automationProjectId)}:enable`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
        },
        'Failed to enable automation project',
    );
    invalidateManagedRequests('automation:');
    return result;
}

export async function disableAutomationProject(automationProjectId) {
    const result = await requestJson(
        `/api/automation/projects/${encodeURIComponent(automationProjectId)}:disable`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
        },
        'Failed to disable automation project',
    );
    invalidateManagedRequests('automation:');
    return result;
}

export async function fetchAutomationProjectSessions(automationProjectId) {
    return requestJson(
        `/api/automation/projects/${encodeURIComponent(automationProjectId)}/sessions`,
        undefined,
        'Failed to fetch automation project sessions',
    );
}
