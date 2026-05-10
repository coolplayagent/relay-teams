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
