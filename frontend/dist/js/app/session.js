/**
 * app/session.js
 * Session selection state and UI synchronization.
 */
import { clearAllPanels } from '../components/agentPanel.js';
import { clearAllStreamState } from '../components/messageRenderer.js';
import { loadSessionRounds } from '../components/rounds.js';
import { setRoundsMode } from '../components/sidebar.js';
import { state } from '../core/state.js';
import { els } from '../utils/dom.js';
import { sysLog } from '../utils/logger.js';

export async function selectSession(sessionId) {
    const isSameSession = state.currentSessionId === sessionId;
    state.currentSessionId = sessionId;
    state.instanceRoleMap = {};

    document.querySelectorAll('.session-item').forEach(el => {
        const isActive = el.querySelector('.session-id')?.textContent === sessionId;
        el.classList.toggle('active', isActive);
    });

    setRoundsMode();
    state.agentViews = { main: els.chatMessages };
    state.activeView = 'main';
    clearAllPanels();
    clearAllStreamState();

    await loadSessionRounds(sessionId);
    sysLog(`${isSameSession ? 'Reloaded' : 'Switched to'} session: ${sessionId}`);
}
