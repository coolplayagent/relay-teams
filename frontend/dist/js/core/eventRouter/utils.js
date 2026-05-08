/**
 * core/eventRouter/utils.js
 * Shared helpers used by SSE event handlers.
 */
import { state } from '../state.js';
import { els } from '../../utils/dom.js';
import { recordUiDiagnostic } from '../uiDiagnostics.js';

export function eventSessionId(eventMeta) {
    return String(eventMeta?.session_id || eventMeta?.sessionId || '').trim();
}

export function isCurrentRootEvent(eventMeta) {
    const currentSessionId = String(state.currentSessionId || '').trim();
    const metaSessionId = eventSessionId(eventMeta);
    if (!metaSessionId) {
        return !state.activeSubagentSession;
    }
    return !!(
        currentSessionId
        && !state.activeSubagentSession
        && metaSessionId === currentSessionId
    );
}

export function coordinatorContainerFor(eventMeta) {
    if (!isCurrentRootEvent(eventMeta)) {
        if (eventSessionId(eventMeta)) {
            recordUiDiagnostic('wrong_target_render_count');
        }
        return null;
    }
    const runId = eventMeta?.trace_id || eventMeta?.run_id || state.activeRunId;
    if (runId) {
        const section = document.querySelector(`.session-round-section[data-run-id="${runId}"]`);
        if (section) return section;
        return els.chatMessages || null;
    }
    const latest = els.chatMessages?.querySelector('.session-round-section:last-of-type');
    if (latest) return latest;
    return els.chatMessages;
}
