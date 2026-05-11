/**
 * core/api/connectors.js
 * Connector aggregation API wrappers.
 */
import { requestJson } from './request.js';

export async function fetchConnectors(options = {}) {
    return requestJson(
        '/api/connectors',
        { signal: options.signal },
        'Failed to fetch connectors',
    );
}

export async function testConnector(connectorId) {
    return requestJson(
        `/api/connectors/${encodeURIComponent(connectorId)}:test`,
        { method: 'POST' },
        'Failed to test connector',
    );
}

export async function fetchRuntimeTools(options = {}) {
    return requestJson(
        '/api/connectors/runtime-tools',
        { signal: options.signal },
        'Failed to fetch runtime tools',
    );
}

export async function startRuntimeToolDownload(toolId) {
    return requestJson(
        `/api/connectors/runtime-tools/${encodeURIComponent(toolId)}:download`,
        { method: 'POST' },
        'Failed to start runtime tool download',
    );
}

export async function fetchRuntimeToolDownload(jobId) {
    return requestJson(
        `/api/connectors/runtime-tools/downloads/${encodeURIComponent(jobId)}`,
        undefined,
        'Failed to fetch runtime tool download',
    );
}
