/**
 * core/stream.js
 * Connects to the backend SSE EventSource and dispatches chunks to the router.
 * The GET /intent/stream endpoint starts the intent AND streams events.
 */
import { state } from './state.js';
import { els } from '../utils/dom.js';
import { sysLog } from '../utils/logger.js';
import { routeEvent } from './eventRouter.js';

export function startIntentStream(promptText, sessionId, executionMode, confirmationGate, onCompleted) {
    state.isGenerating = true;
    if (els.sendBtn) els.sendBtn.disabled = true;
    if (els.promptInput) els.promptInput.disabled = true;

    const panel = document.getElementById('workflow-panel');
    if (panel) panel.classList.add('generating');

    if (state.activeEventSource) {
        state.activeEventSource.close();
    }

    const encodedPrompt = encodeURIComponent(promptText);
    const url = `/api/v1/session/${sessionId}/intent/stream?intent=${encodedPrompt}&execution_mode=${executionMode}&confirmation_gate=${confirmationGate}`;

    sysLog(`Starting SSE (mode=${executionMode} gate=${confirmationGate})`);
    const es = new EventSource(url);
    state.activeEventSource = es;

    es.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            const evType = data.event_type;
            const payload = JSON.parse(data.payload_json || '{}');

            routeEvent(evType, payload, data);

            // End of run → reload history
            if (evType === 'run_completed' || evType === 'run_failed') {
                endStream();
                if (onCompleted) onCompleted(sessionId);
            }
        } catch (e) {
            console.error('SSE parse error', e, event.data);
        }
    };

    es.onerror = () => {
        sysLog('SSE connection closed.', 'log-error');
        endStream();
        if (onCompleted) onCompleted(sessionId);
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

    if (els.sendBtn) els.sendBtn.disabled = false;
    if (els.promptInput) {
        els.promptInput.disabled = false;
        els.promptInput.focus();
    }

    document.querySelectorAll('.typing-indicator').forEach(el => el.remove());
}
