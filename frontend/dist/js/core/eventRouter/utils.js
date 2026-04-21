/**
 * core/eventRouter/utils.js
 * Shared helpers used by SSE event handlers.
 */
import { state } from '../state.js';
import { els } from '../../utils/dom.js';

export function coordinatorContainerFor(eventMeta) {
    const runId = eventMeta?.trace_id || eventMeta?.run_id || state.activeRunId;
    if (runId) {
        const section = document.querySelector(`.session-round-section[data-run-id="${runId}"]`);
        if (section) return section;
    }
    const latest = els.chatMessages?.querySelector('.session-round-section:last-of-type');
    if (latest) return latest;
    return els.chatMessages;
}

export function renderHumanDispatchPanel(payload) {
    document.querySelectorAll('.human-dispatch-panel').forEach(element => element.remove());

    const container = coordinatorContainerFor(payload);
    if (!container) {
        return;
    }

    const panel = document.createElement('div');
    panel.className = 'human-dispatch-panel';

    const summary = String(payload?.summary || payload?.prompt || '').trim();
    panel.textContent = summary || 'Awaiting human dispatch.';

    container.appendChild(panel);
}
