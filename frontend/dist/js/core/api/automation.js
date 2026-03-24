/**
 * core/api/automation.js
 * Automation project related API wrappers.
 */
import { requestJson } from './request.js';

export async function fetchAutomationProjects() {
    return requestJson('/api/automation/projects', undefined, 'Failed to fetch automation projects');
}

export async function createAutomationProject(payload) {
    return requestJson(
        '/api/automation/projects',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to create automation project',
    );
}

export async function fetchAutomationProject(automationProjectId) {
    return requestJson(
        `/api/automation/projects/${encodeURIComponent(automationProjectId)}`,
        undefined,
        'Failed to fetch automation project',
    );
}

export async function updateAutomationProject(automationProjectId, payload) {
    return requestJson(
        `/api/automation/projects/${encodeURIComponent(automationProjectId)}`,
        {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to update automation project',
    );
}

export async function deleteAutomationProject(automationProjectId) {
    return requestJson(
        `/api/automation/projects/${encodeURIComponent(automationProjectId)}`,
        { method: 'DELETE' },
        'Failed to delete automation project',
    );
}

export async function runAutomationProject(automationProjectId) {
    return requestJson(
        `/api/automation/projects/${encodeURIComponent(automationProjectId)}:run`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
        },
        'Failed to run automation project',
    );
}

export async function enableAutomationProject(automationProjectId) {
    return requestJson(
        `/api/automation/projects/${encodeURIComponent(automationProjectId)}:enable`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
        },
        'Failed to enable automation project',
    );
}

export async function disableAutomationProject(automationProjectId) {
    return requestJson(
        `/api/automation/projects/${encodeURIComponent(automationProjectId)}:disable`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
        },
        'Failed to disable automation project',
    );
}

export async function fetchAutomationProjectSessions(automationProjectId) {
    return requestJson(
        `/api/automation/projects/${encodeURIComponent(automationProjectId)}/sessions`,
        undefined,
        'Failed to fetch automation project sessions',
    );
}
