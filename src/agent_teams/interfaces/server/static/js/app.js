/**
 * app.js — Main entry point.
 * Wires up event bindings and orchestrates navigation between sessions and rounds.
 */
import { state } from './core/state.js';
import { els } from './utils/dom.js';
import { sysLog } from './utils/logger.js';
import { loadSessions, handleNewSessionClick, setRoundsMode } from './components/sidebar.js';
import { startIntentStream } from './core/stream.js';
import { loadSessionRounds, toggleWorkflow, goBackToSessions, currentRounds, selectRound } from './components/rounds.js';
import { setupNavbarBindings } from './components/navbar.js';
import { clearAllPanels } from './components/agentPanel.js';

// ─── Init ────────────────────────────────────────────────────────────────────
async function init() {
    sysLog('System Initialized');
    setupNavbarBindings();
    setupEventBindings();
    await loadSessions();

    const firstSessionEl = document.querySelector('.session-item .session-id');
    if (firstSessionEl) {
        await selectSession(firstSessionEl.textContent);
    } else {
        await handleNewSessionClick(false);
    }
}

function setupEventBindings() {
    els.promptInput.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
    });
    els.sendBtn.onclick = handleSend;
    if (els.newSessionBtn) els.newSessionBtn.onclick = () => handleNewSessionClick(true);
    if (els.backBtn) els.backBtn.onclick = () => { goBackToSessions(); loadSessions(); };
    if (els.workflowCollapsed) els.workflowCollapsed.onclick = toggleWorkflow;
    if (els.collapseWorkflowBtn) els.collapseWorkflowBtn.onclick = toggleWorkflow;
}

init();

// ─── Session selection ───────────────────────────────────────────────────────
export async function selectSession(sessionId) {
    if (state.currentSessionId === sessionId) return;
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

    await loadSessionRounds(sessionId);
    sysLog(`Switched to session: ${sessionId}`);
}

window.selectSession = selectSession;

// ─── Send / handle prompt ────────────────────────────────────────────────────
async function handleSend() {
    const text = els.promptInput.value.trim();
    if (!text || state.isGenerating || !state.currentSessionId) return;

    const modeEl = document.getElementById('execution-mode-select');
    const gateEl = document.getElementById('confirmation-gate-check');
    const executionMode = modeEl ? modeEl.value : 'ai';
    const confirmationGate = gateEl ? gateEl.checked : false;

    els.promptInput.value = '';
    state.instanceRoleMap = {};

    // Show user message immediately in coordinator chat
    const um = document.createElement('div');
    um.className = 'message';
    um.dataset.role = 'user';
    um.innerHTML = `
        <div class="msg-header"><span class="msg-role role-user">YOU</span></div>
        <div class="msg-content"><div class="msg-text">${text.replace(/</g, '&lt;')}</div></div>`;
    els.chatMessages.appendChild(um);
    els.chatMessages.scrollTop = els.chatMessages.scrollHeight;

    sysLog(`Sending (mode=${executionMode} gate=${confirmationGate})`);
    startIntentStream(
        text,
        state.currentSessionId,
        executionMode,
        confirmationGate,
        async (sid) => {
            await loadSessionRounds(sid);
            if (currentRounds.length > 0) selectRound(currentRounds[0]);
        },
    );
}
