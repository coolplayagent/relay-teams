/**
 * app/prompt.js
 * Prompt send flow: live round bootstrap and SSE stream start.
 */
import { appendRoundUserMessage, createLiveRound } from '../components/rounds.js';
import { clearAllStreamState } from '../components/messageRenderer.js';
import {
    hydrateSessionView,
    startSessionContinuity,
} from './recovery.js';
import { state } from '../core/state.js';
import { startIntentStream } from '../core/stream.js';
import { els } from '../utils/dom.js';
import { sysLog } from '../utils/logger.js';

export async function handleSend() {
    const text = els.promptInput.value.trim();
    if (!text) return;
    if (state.isGenerating) {
        sysLog('A run is still in progress. Please wait for completion before sending the next message.', 'log-info');
        return;
    }
    if (!state.currentSessionId) {
        sysLog('No active session selected. Please select or create a session first.', 'log-error');
        return;
    }
    if (state.pausedSubagent) {
        const paused = state.pausedSubagent;
        sysLog(
            `Subagent is paused (${paused.roleId || paused.instanceId}). Send a follow-up in that subagent panel first.`,
            'log-error',
        );
        return;
    }

    els.promptInput.value = '';
    els.promptInput.style.height = 'auto';
    state.instanceRoleMap = {};
    state.roleInstanceMap = {};
    state.taskInstanceMap = {};
    state.activeAgentRoleId = null;
    state.activeAgentInstanceId = null;
    state.autoSwitchedSubagentInstances = {};
    state.activeRunId = null;
    state.isGenerating = true;
    if (els.sendBtn) els.sendBtn.disabled = true;
    if (els.promptInput) els.promptInput.disabled = true;
    if (els.stopBtn) {
        els.stopBtn.style.display = 'inline-flex';
        els.stopBtn.disabled = false;
    }
    clearAllStreamState();

    sysLog('Sending prompt');
    startSessionContinuity(state.currentSessionId);
    await startIntentStream(
        text,
        state.currentSessionId,
        async sid => hydrateSessionView(sid, { includeRounds: true, quiet: true }),
        {
            onRunCreated: (run) => {
                createLiveRound(run.run_id, text);
                appendRoundUserMessage(run.run_id, text);
            },
        },
    );
}
