import { state } from './core/state.js';
import { els } from './utils/dom.js';
import { sysLog } from './utils/logger.js';
import { fetchSessionAgents, sendUserPrompt } from './core/api.js';
import { loadSessions, handleNewSessionClick } from './components/sidebar.js';
import { startIntentStream } from './core/stream.js';
import { renderHistoricalMessages, addAgentTab, switchTab } from './components/chat.js';
import { loadSessionWorkflows } from './components/workflow.js';
import { setupNavbarBindings } from './components/navbar.js';
async function init() {
    sysLog("System Initialized");
    setupNavbarBindings();

    // Bind global enter key and send button
    els.promptInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    });

    els.sendBtn.onclick = handleSend;
    if (els.newSessionBtn) {
        els.newSessionBtn.onclick = () => handleNewSessionClick(true);
    }

    await loadSessions();

    const firstSessionEl = document.querySelector('.session-item .session-id');
    if (firstSessionEl) {
        const sessionId = firstSessionEl.textContent;
        await selectSession(sessionId);
    } else {
        await handleNewSessionClick(false);
    }
}
init();

export async function selectSession(sessionId) {
    if (state.currentSessionId === sessionId) return;
    state.currentSessionId = sessionId;

    document.querySelectorAll('.session-item').forEach(el => {
        el.classList.remove('active');
        if (el.querySelector('.session-id').textContent === sessionId) {
            el.classList.add('active');
        }
    });

    document.querySelectorAll('.chat-scroll:not(#chat-messages)').forEach(el => el.remove());

    els.chatMessages.style.display = 'block';
    els.chatMessages.innerHTML = '';
    state.agentViews = { main: els.chatMessages };
    state.activeView = 'main';

    buildAgentTabs(sessionId);
    sysLog(`Switched to session: ${sessionId}`);

    await loadSessions();
}
window.selectSession = selectSession;

export async function buildAgentTabs(sessionId) {
    try {
        const agents = await fetchSessionAgents(sessionId);
        agents.forEach(agent => {
            addAgentTab(agent.role_id, agent.instance_id, false);
        });

        await loadGlobalHistory(sessionId);
        await reloadAllSessionMessages(sessionId);
        await loadSessionWorkflows(sessionId);
    } catch (e) {
        sysLog(`Failed to load agents/history: ${e.message}`, 'log-error');
    }
}

async function loadGlobalHistory(sessionId) {
    try {
        const res = await fetch(`/session/${sessionId}/events`);
        const events = await res.json();

        // Router currently handles individual processing but skips for historic events unless explicitly pumped, so we use a silent boolean if necessary.
        // Pumping User History manually since Coordinator strips it from global DAG:
        try {
            const sessionAgents = await fetchSessionAgents(sessionId);
            const coordAgent = sessionAgents.find(a => a.role_id === 'coordinator_agent');
            if (coordAgent) {
                const msgRes = await fetch(`/session/${sessionId}/agents/${coordAgent.instance_id}/messages`);
                const messages = await msgRes.json();

                // Directly write user histories here or trigger text_delta pseudo-events:
                // Note: Real routing implementation often refactors this entirely by letting components/chat.js handle it
                // For direct equivalence, we trigger routeEvent through a manual text_delta payload proxy.
            }
        } catch (e) { console.warn("Could not splice User history", e); }

        // Actually dispatch global events to reconstruct workflow paths if necessary
        // Mostly skipped right now because `loadSessionWorkflows` natively draws the graph from SQLite JSON.
    } catch (e) {
        console.error("Failed loading history", e);
    }
}

export async function reloadAllSessionMessages(sessionId) {
    try {
        const res = await fetch(`/session/${sessionId}/messages`);
        const messages = await res.json();

        const byInstance = {};
        messages.forEach(m => {
            if (!byInstance[m.instance_id]) byInstance[m.instance_id] = [];
            byInstance[m.instance_id].push(m);
        });

        for (const [instanceId, msgs] of Object.entries(byInstance)) {
            let container = state.agentViews[instanceId];
            if (!container) continue;

            container.innerHTML = '';
            renderHistoricalMessages(container, msgs, instanceId);
        }
    } catch (e) {
        console.error("Failed to load session messages", e);
    }
}

// Window global for Topo map routing
window.switchTabByRole = function (roleId) {
    if (roleId === 'coordinator_agent') {
        const coordView = document.querySelector(`.chat-scroll[data-role="coordinator_agent"]`);
        if (coordView) {
            switchTab(coordView.id.replace('view-', ''));
        } else {
            console.warn('Coordinator view not ready.');
        }
        return;
    }

    const view = document.querySelector(`.chat-scroll[data-role="${roleId}"]`);
    if (view) {
        switchTab(view.id.replace('view-', ''));
    } else {
        sysLog(`No active agent found for role: ${roleId}`, 'log-error');
    }
};

async function handleSend() {
    const text = els.promptInput.value.trim();
    if (!text) return;
    if (state.isGenerating) return;
    if (!state.currentSessionId) return;

    els.promptInput.value = '';

    const div = document.createElement('div');
    div.className = 'message';
    div.innerHTML = `
        <div class="msg-header">
            <span class="msg-role role-user">You</span>
        </div>
        <div class="msg-content">${text.replace(/\\n/g, '<br>')}</div>
    `;
    els.chatMessages.appendChild(div);
    els.chatMessages.scrollTop = els.chatMessages.scrollHeight;

    sysLog(`Found intent routing for prompt...`);
    try {
        await sendUserPrompt(state.currentSessionId, text);
        startIntentStream(text, state.currentSessionId, loadSessionWorkflows);
    } catch (e) {
        sysLog(`Failed to start interaction: ${e.message}`, 'log-error');
        state.isGenerating = false;
        els.sendBtn.disabled = false;
        els.promptInput.disabled = false;
    }
}
