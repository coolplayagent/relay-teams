/**
 * app/prompt.js
 * Prompt send flow: live round bootstrap and SSE stream start.
 */
import { createLiveRound, loadSessionRounds } from '../components/rounds.js';
import { clearAllStreamState } from '../components/messageRenderer.js';
import { state } from '../core/state.js';
import { startIntentStream } from '../core/stream.js';
import { els } from '../utils/dom.js';
import { sysLog } from '../utils/logger.js';

export async function handleSend() {
    const text = els.promptInput.value.trim();
    if (!text || state.isGenerating || !state.currentSessionId) return;
    if (state.pausedSubagent) {
        const paused = state.pausedSubagent;
        sysLog(
            `Subagent is paused (${paused.roleId || paused.instanceId}). Send a follow-up in that subagent panel first.`,
            'log-error',
        );
        return;
    }

    const modeEl = document.getElementById('execution-mode-select');
    const executionMode = modeEl ? modeEl.value : 'ai';

    els.promptInput.value = '';
    els.promptInput.style.height = 'auto';
    state.instanceRoleMap = {};
    state.roleInstanceMap = {};
    state.taskInstanceMap = {};
    state.activeAgentRoleId = null;
    state.activeAgentInstanceId = null;
    state.autoSwitchedSubagentInstances = {};
    clearAllStreamState();

    createLiveRound(text);

    const um = document.createElement('div');
    um.className = 'message';
    um.dataset.role = 'user';
    um.innerHTML = `
        <div class="msg-header"><span class="msg-role role-user">YOU</span></div>
        <div class="msg-content"><div class="msg-text">${text.replace(/</g, '&lt;')}</div></div>`;
    const liveSection = els.chatMessages.querySelector('.session-round-section[data-run-id="__live__"]');
    if (liveSection) {
        liveSection.appendChild(um);
    } else {
        els.chatMessages.appendChild(um);
    }
    els.chatMessages.scrollTop = els.chatMessages.scrollHeight;

    sysLog(`Sending (mode=${executionMode})`);
    await startIntentStream(
        text,
        state.currentSessionId,
        executionMode,
        async (sid) => {
            await loadSessionRounds(sid);
        },
    );
}
