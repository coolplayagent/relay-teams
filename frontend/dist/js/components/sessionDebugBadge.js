/**
 * components/sessionDebugBadge.js
 * Low-noise session identifier badge for debugging active conversations.
 */
import { state } from '../core/state.js';
import { els } from '../utils/dom.js';

export function initializeSessionDebugBadge() {
    syncSessionDebugBadge();
}

export function syncSessionDebugBadge(sessionId = state.currentSessionId || '') {
    const badge = els.currentSessionIdBadge;
    if (!badge) {
        return;
    }
    const safeSessionId = String(sessionId || '').trim();
    if (!safeSessionId) {
        badge.hidden = true;
        badge.textContent = '';
        badge.removeAttribute?.('title');
        return;
    }
    badge.hidden = false;
    badge.textContent = safeSessionId;
    badge.removeAttribute?.('title');
}
