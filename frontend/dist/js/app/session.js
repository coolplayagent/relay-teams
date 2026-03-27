/**
 * app/session.js
 * Session selection state and UI synchronization.
 */
import { clearAllPanels } from '../components/agentPanel.js';
import { clearContextIndicators, scheduleCoordinatorContextPreview } from '../components/contextIndicators.js';
import { clearAllStreamState } from '../components/messageRenderer.js';
import { clearSessionTokenUsage, scheduleSessionTokenUsageRefresh } from '../components/sessionTokenUsage.js';
import { hideProjectView } from '../components/projectView.js';
import { setRoundsMode } from '../components/sidebar.js';
import { fetchSessionHistory } from '../core/api.js';
import {
    clearSessionRecovery,
    hydrateSessionView,
    markRunStreamConnected,
    stopSessionContinuity,
} from './recovery.js';
import { applyCurrentSessionRecord, resetCurrentSessionTopology, state } from '../core/state.js';
import {
    detachActiveStreamForSessionSwitch,
    resumeRunStream,
} from '../core/stream.js';
import { els } from '../utils/dom.js';
import { formatMessage } from '../utils/i18n.js';
import { sysLog } from '../utils/logger.js';
import { refreshSessionTopologyControls } from './prompt.js';

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
        scheduleSessionTokenUsageRefresh({ immediate: true });
        sysLog(`Synced live session: ${sessionId}`);
        return;
    }
    if (!isSameSession && state.activeEventSource) {
        detachActiveStreamForSessionSwitch({ focusPrompt: false });
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
    resetCurrentSessionTopology();
    clearSessionRecovery();

    document.querySelectorAll('.session-item').forEach(el => {
        const isActive = el.getAttribute('data-session-id') === sessionId;
        el.classList.toggle('active', isActive);
    });

    hideProjectView();
    setRoundsMode();
    state.agentViews = { main: els.chatMessages };
    state.activeView = 'main';
    clearAllPanels();
    clearContextIndicators();
    clearSessionTokenUsage();
    clearAllStreamState({ preserveOverlay: true });
    refreshSessionTopologyControls();

    const sessionRecord = await fetchSessionHistory(sessionId);
    if (state.currentSessionId !== sessionId) {
        return;
    }
    applyCurrentSessionRecord(sessionRecord);
    refreshSessionTopologyControls();
    await hydrateSessionView(sessionId, { includeRounds: true, quiet: true });
    if (state.currentSessionId !== sessionId) {
        return;
    }
    autoConnectRunningStream(sessionId);
    scheduleCoordinatorContextPreview({ immediate: true });
    scheduleSessionTokenUsageRefresh({ immediate: true });
    document.dispatchEvent(
        new CustomEvent('agent-teams-session-selected', {
            detail: { sessionId },
        }),
    );
    sysLog(formatMessage(isSameSession ? 'session.reloaded' : 'session.switched', {
        session_id: sessionId,
    }));
}

function autoConnectRunningStream(sessionId) {
    if (state.activeEventSource || state.isGenerating) return;
    const snapshot = state.currentRecoverySnapshot;
    const activeRun = snapshot?.activeRun;
    if (!activeRun?.run_id) return;
    if (!activeRun.is_recoverable) return;
    if (activeRun.status !== 'running' && activeRun.status !== 'queued') return;

    const afterEventId = Number(activeRun.checkpoint_event_id) || 0;
    const runId = activeRun.run_id;
    markRunStreamConnected(runId, { phase: activeRun.phase || 'running' });
    resumeRunStream(
        runId,
        sessionId,
        async sid => {
            if (sid) {
                await hydrateSessionView(sid, { includeRounds: true, quiet: true });
            }
        },
        {
            reason: 'session-switch-auto-connect',
            makeUiBusy: true,
            afterEventId,
        },
    );
    sysLog(`Auto-connected to running stream: run=${runId}`);
}
