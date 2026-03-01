/**
 * core/stream.js
 * Connects to the backend EventSource and dispatches chunks to the router.
 */
import { state } from './state.js';
import { els } from '../utils/dom.js';
import { sysLog } from '../utils/logger.js';
import { routeEvent } from './eventRouter.js';

export function startIntentStream(promptText, sessionId, onGraphSpawned) {
    state.isGenerating = true;

    // We defer UI disables to components/chat.js but manage the panel here
    const panel = document.getElementById('workflow-panel');
    if (panel) panel.classList.add('generating');

    if (state.activeEventSource) {
        state.activeEventSource.close();
    }

    const encodedPrompt = encodeURIComponent(promptText);
    const url = `/api/v1/session/${sessionId}/intent/stream?intent=${encodedPrompt}`;

    sysLog(`Starting SSE connection to ${url}`);
    const es = new EventSource(url);
    state.activeEventSource = es;

    es.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            const evType = data.event_type;
            const payload = JSON.parse(data.payload_json || '{}');

            // Delegate dom writing to the router
            routeEvent(evType, payload, data);

            // Live graph update if Coordinator spawned the DAG
            if (evType === 'tool_result' && payload.tool_name === 'create_workflow_graph') {
                if (onGraphSpawned) onGraphSpawned(sessionId);
            }
        } catch (e) {
            console.error("Failed to parse SSE event", event.data, e);
        }
    };

    es.onerror = (err) => {
        sysLog(`SSE Connection error.Stream closed.`, 'log-error');
        endStream();
    };
}

export function endStream() {
    if (state.activeEventSource) {
        state.activeEventSource.close();
        state.activeEventSource = null;
    }
    state.isGenerating = false;

    const panel = document.getElementById('workflow-panel');
    if (panel) panel.classList.remove('generating');

    // UI enables handled elsewhere via state.isGenerating observing
    document.querySelectorAll('.typing-indicator').forEach(el => el.remove());
}
