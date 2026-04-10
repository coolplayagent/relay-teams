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
import {
    clearActiveSubagentSession,
    ensureSessionSubagents,
    openSubagentSession,
} from '../components/subagentSessions.js';
import { fetchSessionHistory } from '../core/api.js';
import {
    clearSessionRecovery,
    hydrateSessionView,
    stopSessionContinuity,
} from './recovery.js';
import { applyCurrentSessionRecord, resetCurrentSessionTopology, state } from '../core/state.js';
import {
    detachActiveStreamForSessionSwitch,
    detachNormalModeSubagentStreamsForSessionSwitch,
} from '../core/stream.js';
import { els } from '../utils/dom.js';
import { formatMessage } from '../utils/i18n.js';
import { sysLog } from '../utils/logger.js';
import { refreshSessionTopologyControls } from './prompt.js';

export async function selectSession(sessionId) {
    const isSameSession = state.currentSessionId === sessionId && !state.activeSubagentSession;
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
        detachNormalModeSubagentStreamsForSessionSwitch(previousSessionId);
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
    clearActiveSubagentSession();
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
    scheduleCoordinatorContextPreview({ immediate: true });
    scheduleSessionTokenUsageRefresh({ immediate: true });
    void ensureSessionSubagents(sessionId, { force: true });
    document.dispatchEvent(
        new CustomEvent('agent-teams-session-selected', {
            detail: { sessionId },
        }),
    );
    sysLog(formatMessage(isSameSession ? 'session.reloaded' : 'session.switched', {
        session_id: sessionId,
    }));
}

export async function selectSubagentSession(sessionId, subagent) {
    const safeSessionId = String(sessionId || '').trim();
    const safeInstanceId = String(
        subagent?.instanceId || subagent?.instance_id || '',
    ).trim();
    if (!safeSessionId || !safeInstanceId) {
        return;
    }
    if (state.currentSessionId !== safeSessionId) {
        await selectSession(safeSessionId);
        if (state.currentSessionId !== safeSessionId) {
            return;
        }
    }
    const records = await ensureSessionSubagents(safeSessionId, { force: false });
    const resolved = records.find(item => item.instanceId === safeInstanceId)
        || {
            sessionId: safeSessionId,
            instanceId: safeInstanceId,
            roleId: String(subagent?.roleId || subagent?.role_id || '').trim(),
            runId: String(subagent?.runId || subagent?.run_id || '').trim(),
            title: String(subagent?.title || '').trim(),
            status: String(subagent?.status || 'idle').trim() || 'idle',
        };
    hideProjectView();
    setRoundsMode();
    await openSubagentSession(safeSessionId, resolved);
    document.dispatchEvent(
        new CustomEvent('agent-teams-subagent-session-selected', {
            detail: {
                sessionId: safeSessionId,
                instanceId: safeInstanceId,
            },
        }),
    );
}
