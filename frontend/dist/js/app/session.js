/**
 * app/session.js
 * Session selection state and UI synchronization.
 */
import { clearAllPanels } from '../components/agentPanel.js';
import { clearAllStreamState } from '../components/messageRenderer.js';
import { setRoundsMode } from '../components/sidebar.js';
import {
    clearSessionRecovery,
    hydrateSessionView,
    stopSessionContinuity,
} from './recovery.js';
import { state } from '../core/state.js';
import { endStream } from '../core/stream.js';
import { els } from '../utils/dom.js';
import { sysLog } from '../utils/logger.js';

export async function selectSession(sessionId) {
    const isSameSession = state.currentSessionId === sessionId;
    const previousSessionId = state.currentSessionId;
    const selectedSessionEl = document.querySelector(
        `.session-item[data-session-id="${sessionId}"]`,
    );
    const selectedWorkspaceId = String(
        selectedSessionEl?.getAttribute('data-workspace-id') || '',
    ).trim();
    if (selectedWorkspaceId) {
        state.currentWorkspaceId = selectedWorkspaceId;
    }
    if (isSameSession && (state.isGenerating || state.activeEventSource)) {
        await hydrateSessionView(sessionId, { includeRounds: false, quiet: true });
        sysLog(`Synced live session: ${sessionId}`);
        return;
    }
    if (!isSameSession && state.activeEventSource) {
        endStream();
    }
    if (!isSameSession && previousSessionId) {
        stopSessionContinuity(previousSessionId);
    }
    state.currentSessionId = sessionId;
    state.instanceRoleMap = {};
    state.roleInstanceMap = {};
    state.taskInstanceMap = {};
    state.taskStatusMap = {};
    state.activeAgentRoleId = null;
    state.activeAgentInstanceId = null;
    state.autoSwitchedSubagentInstances = {};
    state.pausedSubagent = null;
    state.sessionAgents = [];
    state.sessionTasks = [];
    state.selectedRoleId = null;
    clearSessionRecovery();

    document.querySelectorAll('.session-item').forEach(el => {
        const isActive = el.getAttribute('data-session-id') === sessionId;
        el.classList.toggle('active', isActive);
    });

    setRoundsMode();
    state.agentViews = { main: els.chatMessages };
    state.activeView = 'main';
    clearAllPanels();
    clearAllStreamState();

    await hydrateSessionView(sessionId, { includeRounds: true, quiet: true });
    sysLog(`${isSameSession ? 'Reloaded' : 'Switched to'} session: ${sessionId}`);
}
