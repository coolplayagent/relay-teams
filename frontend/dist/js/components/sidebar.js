/**
 * components/sidebar.js
 * Handles the session list rendering and sidebar toggle interactions.
 */
import { els } from '../utils/dom.js';
import { sysLog } from '../utils/logger.js';
import { fetchSessions, startNewSession, deleteSession } from '../core/api.js';
import { state } from '../core/state.js';

let selectSessionHandler = null;

export function setSelectSessionHandler(handler) {
    selectSessionHandler = handler;
}

async function selectSessionById(sessionId) {
    if (!selectSessionHandler) {
        throw new Error('selectSession handler is not configured');
    }
    await selectSessionHandler(sessionId);
}

export async function loadSessions() {
    try {
        const sessions = await fetchSessions();

        els.sessionsList.innerHTML = '';
        if (sessions.length === 0) {
            els.sessionsList.innerHTML = '<div style="padding:1rem; color:var(--text-secondary); font-size:0.8rem; text-align:center;">No previous sessions</div>';
            return;
        }

        sessions.forEach(s => {
            const div = document.createElement('div');
            div.className = 'session-item';
            div.onclick = () => { void selectSessionById(s.session_id); };
            if (s.session_id === state.currentSessionId) div.classList.add('active');

            const time = new Date(s.updated_at).toLocaleString();
            div.innerHTML = `
                <span class="session-id">${s.session_id}</span>
                <span class="session-time">${time}</span>
                <button class="session-delete-btn" title="Delete session">
                    <svg viewBox="0 0 24 24" fill="none" class="icon-sm">
                        <path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                </button>
            `;

            const deleteBtn = div.querySelector('.session-delete-btn');
            deleteBtn.onclick = async (e) => {
                e.stopPropagation();
                if (confirm(`Delete session ${s.session_id}?`)) {
                    try {
                        await deleteSession(s.session_id);
                        if (s.session_id === state.currentSessionId) {
                            const remaining = sessions.filter(sess => sess.session_id !== s.session_id);
                            if (remaining.length > 0) {
                                await selectSessionById(remaining[0].session_id);
                            } else {
                                await handleNewSessionClick(false);
                            }
                        }
                        await loadSessions();
                    } catch (e) {
                        sysLog(`Error deleting session: ${e.message}`, 'log-error');
                    }
                }
            };

            els.sessionsList.appendChild(div);
        });
    } catch (e) {
        sysLog(`Error loading sessions: ${e.message}`, 'log-error');
    }
}

export function setSessionMode() {
    els.sessionsList.style.display = 'block';
    els.roundsList.style.display = 'none';
    els.backBtn.style.display = 'none';
}

export function setRoundsMode() {
    // Session list stays visible; rounds are shown as a floating navigator in main area.
    els.sessionsList.style.display = 'block';
    els.roundsList.style.display = 'none';
    els.backBtn.style.display = 'none';
}

export async function handleNewSessionClick(manualClick = true) {
    try {
        const data = await startNewSession();
        sysLog(`Created new session: ${data.session_id}`);

        if (manualClick) {
            els.chatMessages.innerHTML = '';
        }

        await loadSessions();
        await selectSessionById(data.session_id);
    } catch (e) {
        sysLog(`Error creating session: ${e.message}`, 'log-error');
    }
}
