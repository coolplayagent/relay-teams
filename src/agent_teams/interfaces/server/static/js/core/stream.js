/**
 * core/stream.js
 * Connects to the SSE EventSource, dispatches events, handles lifecycle.
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
        state.activeEventSource = null;
    }

    const encodedPrompt = encodeURIComponent(promptText);
    const url = `/api/v1/session/${sessionId}/intent/stream?intent=${encodedPrompt}&execution_mode=${executionMode}&confirmation_gate=${confirmationGate}`;

    sysLog(`SSE start (mode=${executionMode} gate=${confirmationGate})`);
    const es = new EventSource(url);
    state.activeEventSource = es;

    // Guard: only call onCompleted once even if both message and onerror fire
    let _done = false;
    function _finish() {
        if (_done) return;
        _done = true;
        endStream();
        if (onCompleted) onCompleted(sessionId);
    }

    es.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            const evType = data.event_type;
            const payload = JSON.parse(data.payload_json || '{}');

            routeEvent(evType, payload, data);

            if (evType === 'run_completed' || evType === 'run_failed') {
                _finish();
            }
        } catch (e) {
            console.error('SSE parse error', e, event.data);
        }
    };

    // onerror fires when the server closes the connection after run_completed.
    // The _done guard prevents a second call to onCompleted.
    es.onerror = () => {
        sysLog('SSE closed.', 'log-error');
        _finish();
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
}
