/**
 * app/recovery.js
 * Session recovery snapshot loading, banner rendering, and explicit resume actions.
 */
import { focusSubagent, refreshSubagentRail } from '../components/subagentRail.js';
import {
    loadSessionRounds,
    overlayRoundRecoveryState,
    selectRound,
} from '../components/rounds.js';
import { scheduleSessionsRefresh } from '../components/sidebar.js';
import { fetchSessionRecovery, resolveToolApproval, resumeRun } from '../core/api.js';
import { humanizeRoleId, state } from '../core/state.js';
import { resumeRunStream } from '../core/stream.js';
import { els } from '../utils/dom.js';
import { sysLog } from '../utils/logger.js';

let recoveryActionBusy = false;
const approvalActionBusyIds = new Set();
const approvalActionErrors = new Map();
let recoveryBannerRenderSignature = '';
const CONTINUITY_POLL_ACTIVE_MS = 1500;
const CONTINUITY_POLL_IDLE_MS = 4000;
const continuity = {
    sessionId: '',
    pollTimer: null,
    refreshTimer: null,
    refreshPromise: null,
    pendingRefresh: null,
    listenersBound: false,
};

export async function hydrateSessionView(
    sessionId = state.currentSessionId,
    { includeRounds = true, quiet = true } = {},
) {
    const safeSessionId = typeof sessionId === 'string' ? sessionId.trim() : '';
    if (!safeSessionId) {
        stopSessionContinuity();
        clearSessionRecovery();
        return null;
    }

    startSessionContinuity(safeSessionId);
    const shouldSkipRoundsReload = !!(
        includeRounds
        && state.currentSessionId === safeSessionId
        && state.activeEventSource
        && state.isGenerating
    );
    if (includeRounds && !shouldSkipRoundsReload) {
        await loadSessionRounds(safeSessionId);
        if (state.currentSessionId !== safeSessionId) return null;
    }
    const snapshot = await refreshSessionRecovery(safeSessionId, { quiet });
    await refreshSubagentRail(safeSessionId, { preserveSelection: true });
    syncSessionContinuity();
    return snapshot;
}

export function startSessionContinuity(sessionId = state.currentSessionId) {
    const safeSessionId = typeof sessionId === 'string' ? sessionId.trim() : '';
    if (!safeSessionId) return;

    continuity.sessionId = safeSessionId;
    bindContinuityWindowEvents();
    syncSessionContinuity();
}

export function stopSessionContinuity(sessionId = null) {
    const safeSessionId = typeof sessionId === 'string' ? sessionId.trim() : '';
    if (safeSessionId && continuity.sessionId && continuity.sessionId !== safeSessionId) {
        return;
    }

    continuity.sessionId = '';
    continuity.pendingRefresh = null;
    if (continuity.refreshTimer) {
        clearTimeout(continuity.refreshTimer);
        continuity.refreshTimer = null;
    }
    if (continuity.pollTimer) {
        clearTimeout(continuity.pollTimer);
        continuity.pollTimer = null;
    }
}

export function scheduleRecoveryContinuityRefresh({
    sessionId = state.currentSessionId,
    delayMs = 0,
    includeRounds = false,
    quiet = true,
    reason = '',
} = {}) {
    const safeSessionId = typeof sessionId === 'string' ? sessionId.trim() : '';
    if (!safeSessionId) return;

    startSessionContinuity(safeSessionId);
    continuity.pendingRefresh = mergePendingRefresh(continuity.pendingRefresh, {
        sessionId: safeSessionId,
        includeRounds,
        quiet,
        reason,
    });
    if (continuity.refreshTimer) {
        clearTimeout(continuity.refreshTimer);
    }
    continuity.refreshTimer = setTimeout(() => {
        continuity.refreshTimer = null;
        void flushScheduledContinuityRefresh();
    }, Math.max(0, Number(delayMs) || 0));
}

export function clearSessionRecovery() {
    const hadSnapshot = !!state.currentRecoverySnapshot || !!state.pausedSubagent;
    state.currentRecoverySnapshot = null;
    state.pausedSubagent = null;
    approvalActionBusyIds.clear();
    approvalActionErrors.clear();
    recoveryBannerRenderSignature = '';
    if (!state.isGenerating) {
        state.activeRunId = null;
    }
    if (hadSnapshot) {
        scheduleSessionsRefresh();
        renderRecoveryBanner();
    }
    syncSessionContinuity();
}

export function applyRecoverySnapshot(snapshot) {
    const normalized = normalizeRecoverySnapshot(snapshot);
    const previous = state.currentRecoverySnapshot;
    if (areRecoverySnapshotsEquivalent(previous, normalized)) {
        if (normalized.activeRun?.run_id) {
            state.activeRunId = normalized.activeRun.run_id;
        } else if (!state.isGenerating) {
            state.activeRunId = null;
        }
        syncRecoveryRoundOverlay();
        renderRecoveryBanner();
        syncSessionContinuity();
        return previous || normalized;
    }

    reconcileApprovalActionState(normalized.pendingToolApprovals);
    state.currentRecoverySnapshot = normalized;
    state.pausedSubagent = normalized.pausedSubagent;
    if (normalized.activeRun?.run_id) {
        state.activeRunId = normalized.activeRun.run_id;
    } else if (!state.isGenerating) {
        state.activeRunId = null;
    }
    scheduleSessionsRefresh();
    syncRecoveryRoundOverlay();
    renderRecoveryBanner();
    syncSessionContinuity();
    return normalized;
}

export async function refreshSessionRecovery(sessionId = state.currentSessionId, options = {}) {
    const safeSessionId = typeof sessionId === 'string' ? sessionId.trim() : '';
    if (!safeSessionId) {
        clearSessionRecovery();
        return null;
    }

    try {
        const snapshot = await fetchSessionRecovery(safeSessionId);
        if (state.currentSessionId !== safeSessionId) return null;
        const normalized = applyRecoverySnapshot(snapshot);
        syncSessionContinuity();
        return normalized;
    } catch (e) {
        if (!options.quiet) {
            sysLog(`Failed to load recovery state: ${e.message}`, 'log-error');
        }
        syncSessionContinuity();
        return null;
    }
}

export async function resumeRecoverableRun(
    runId,
    {
        sessionId = state.currentSessionId,
        reason = 'resume',
        onCompleted = null,
        quiet = false,
    } = {},
) {
    const safeRunId = typeof runId === 'string' ? runId.trim() : '';
    if (!safeRunId) return false;

    const safeSessionId = typeof sessionId === 'string' ? sessionId.trim() : '';
    recoveryActionBusy = true;
    renderRecoveryBanner();
    try {
        const payload = await resumeRun(safeRunId);
        const nextSessionId = payload?.session_id || safeSessionId || state.currentSessionId;
        const complete = typeof onCompleted === 'function'
            ? onCompleted
            : async sid => hydrateSessionView(sid, { includeRounds: true, quiet: true });
        markRunStreamConnected(safeRunId, { phase: 'running' });
        resumeRunStream(safeRunId, nextSessionId, complete, {
            reason,
            makeUiBusy: true,
        });
        return true;
    } catch (e) {
        if (!quiet) {
            sysLog(e.message || 'Failed to resume run', 'log-error');
        }
        if (safeSessionId) {
            await refreshSessionRecovery(safeSessionId, { quiet: true });
        }
        return false;
    } finally {
        recoveryActionBusy = false;
        renderRecoveryBanner();
        syncSessionContinuity();
    }
}

export function markRunStreamConnected(runId, { phase = 'running' } = {}) {
    const activeRun = getActiveRecoveryRun();
    if (!runId) return;
    state.pausedSubagent = null;
    approvalActionErrors.clear();
    if (!activeRun || activeRun.run_id !== runId) {
        state.currentRecoverySnapshot = normalizeRecoverySnapshot({
            active_run: {
                run_id: runId,
                status: 'running',
                phase,
                is_recoverable: true,
                checkpoint_event_id: 0,
                last_event_id: 0,
                pending_tool_approval_count: 0,
                stream_connected: true,
                should_show_recover: false,
            },
            pending_tool_approvals: [],
            paused_subagent: null,
            round_snapshot: null,
        });
    } else {
        state.currentRecoverySnapshot = {
            ...state.currentRecoverySnapshot,
            activeRun: {
                ...activeRun,
                status: 'running',
                phase,
                stream_connected: true,
                should_show_recover: false,
            },
            pausedSubagent: null,
        };
    }
    state.activeRunId = runId;
    syncRecoveryRoundOverlay();
    scheduleSessionsRefresh();
    renderRecoveryBanner();
    syncSessionContinuity();
}

export function markRunTerminalState(runId, { status, phase, recoverable } = {}) {
    const activeRun = getActiveRecoveryRun();
    if (!activeRun || activeRun.run_id !== runId) {
        if (!recoverable) {
            clearSessionRecovery();
        }
        return;
    }

    if (!recoverable) {
        overlayRoundRecoveryState(runId, {
            run_status: status || activeRun.status || 'completed',
            run_phase: phase || activeRun.phase || 'terminal',
            is_recoverable: false,
            pending_tool_approval_count: 0,
            pending_tool_approvals: [],
        });
        clearSessionRecovery();
        return;
    }

    state.currentRecoverySnapshot = {
        ...state.currentRecoverySnapshot,
        activeRun: {
            ...activeRun,
            status: status || activeRun.status || 'stopped',
            phase: phase || 'stopped',
            is_recoverable: true,
            stream_connected: false,
            should_show_recover: true,
        },
    };
    syncRecoveryRoundOverlay();
    scheduleSessionsRefresh();
    renderRecoveryBanner();
    syncSessionContinuity();
}

export function markPausedSubagent(payload = {}) {
    const pausedSubagent = normalizePausedSubagent(payload);
    state.pausedSubagent = pausedSubagent;

    const snapshot = state.currentRecoverySnapshot || {
        activeRun: state.activeRunId
            ? {
                run_id: state.activeRunId,
                status: 'paused',
                phase: 'awaiting_subagent_followup',
                is_recoverable: true,
                checkpoint_event_id: 0,
                last_event_id: 0,
                pending_tool_approval_count: 0,
                stream_connected: false,
                should_show_recover: false,
            }
            : null,
        pendingToolApprovals: [],
        pausedSubagent: null,
        roundSnapshot: null,
    };
    const activeRun = snapshot.activeRun;
    state.currentRecoverySnapshot = {
        ...snapshot,
        activeRun: activeRun
            ? {
                ...activeRun,
                status: 'paused',
                phase: 'awaiting_subagent_followup',
                stream_connected: false,
                should_show_recover: false,
            }
            : activeRun,
        pausedSubagent,
    };
    syncRecoveryRoundOverlay();
    renderRecoveryBanner();
    syncSessionContinuity();
}

export function clearPausedSubagent(instanceId = null) {
    const paused = state.pausedSubagent || state.currentRecoverySnapshot?.pausedSubagent || null;
    if (!paused) return;
    if (instanceId && paused.instanceId !== instanceId) return;

    state.pausedSubagent = null;
    if (state.currentRecoverySnapshot) {
        state.currentRecoverySnapshot = {
            ...state.currentRecoverySnapshot,
            pausedSubagent: null,
        };
    }
    syncRecoveryRoundOverlay();
    renderRecoveryBanner();
    syncSessionContinuity();
}

export function markToolApprovalRequested(payload = {}) {
    const snapshot = state.currentRecoverySnapshot || {
        activeRun: state.activeRunId
            ? {
                run_id: state.activeRunId,
                status: 'paused',
                phase: 'awaiting_tool_approval',
                is_recoverable: true,
                checkpoint_event_id: 0,
                last_event_id: 0,
                pending_tool_approval_count: 0,
                stream_connected: false,
                should_show_recover: false,
            }
            : null,
        pendingToolApprovals: [],
        pausedSubagent: null,
        roundSnapshot: null,
    };

    const nextApprovals = dedupeApprovals([
        ...snapshot.pendingToolApprovals,
        payload,
    ]);
    approvalActionErrors.clear();
    state.currentRecoverySnapshot = {
        ...snapshot,
        activeRun: snapshot.activeRun
            ? {
                ...snapshot.activeRun,
                status: 'paused',
                phase: 'awaiting_tool_approval',
                pending_tool_approval_count: nextApprovals.length,
                stream_connected: false,
                should_show_recover: false,
            }
            : snapshot.activeRun,
        pendingToolApprovals: nextApprovals,
    };
    syncRecoveryRoundOverlay();
    scheduleSessionsRefresh();
    renderRecoveryBanner();
    syncSessionContinuity();
}

export function markToolApprovalResolved(toolCallId) {
    const snapshot = state.currentRecoverySnapshot;
    if (!snapshot) return;

    const safeToolCallId = typeof toolCallId === 'string' ? toolCallId.trim() : '';
    if (safeToolCallId) {
        approvalActionBusyIds.delete(safeToolCallId);
        approvalActionErrors.delete(safeToolCallId);
    }
    const nextApprovals = safeToolCallId
        ? snapshot.pendingToolApprovals.filter(item => item.tool_call_id !== safeToolCallId)
        : snapshot.pendingToolApprovals;
    const hasPending = nextApprovals.length > 0;
    state.currentRecoverySnapshot = {
        ...snapshot,
        activeRun: snapshot.activeRun
            ? {
                ...snapshot.activeRun,
                status: hasPending || snapshot.pausedSubagent ? 'paused' : snapshot.activeRun.status,
                pending_tool_approval_count: nextApprovals.length,
                phase: hasPending
                    ? 'awaiting_tool_approval'
                    : snapshot.pausedSubagent
                        ? 'awaiting_subagent_followup'
                        : snapshot.activeRun.status === 'stopped'
                            ? 'stopped'
                            : snapshot.activeRun.phase,
            }
            : snapshot.activeRun,
        pendingToolApprovals: nextApprovals,
    };
    syncRecoveryRoundOverlay();
    scheduleSessionsRefresh();
    renderRecoveryBanner();
    syncSessionContinuity();
}

function normalizeRecoverySnapshot(snapshot) {
    const activeRun = snapshot?.active_run && typeof snapshot.active_run === 'object'
        ? { ...snapshot.active_run }
        : null;
    const pendingToolApprovals = Array.isArray(snapshot?.pending_tool_approvals)
        ? snapshot.pending_tool_approvals.map(item => ({ ...item }))
        : [];
    const pausedSubagent = normalizePausedSubagent(snapshot?.paused_subagent, activeRun?.run_id || null);
    const roundSnapshot = snapshot?.round_snapshot && typeof snapshot.round_snapshot === 'object'
        ? { ...snapshot.round_snapshot }
        : null;
    return {
        activeRun,
        pendingToolApprovals,
        pausedSubagent,
        roundSnapshot,
    };
}

function normalizePausedSubagent(raw, runId = null) {
    if (!raw || typeof raw !== 'object') return null;
    const instanceId = typeof raw.instance_id === 'string'
        ? raw.instance_id
        : typeof raw.instanceId === 'string'
            ? raw.instanceId
            : '';
    const roleId = typeof raw.role_id === 'string'
        ? raw.role_id
        : typeof raw.roleId === 'string'
            ? raw.roleId
            : '';
    const taskId = typeof raw.task_id === 'string'
        ? raw.task_id
        : typeof raw.taskId === 'string'
            ? raw.taskId
            : null;
    if (!instanceId && !roleId) return null;
    return {
        runId: runId || state.activeRunId,
        instanceId,
        roleId,
        taskId,
    };
}

function dedupeApprovals(items) {
    const seen = new Map();
    items.forEach(item => {
        const toolCallId = typeof item?.tool_call_id === 'string' ? item.tool_call_id : '';
        if (!toolCallId) return;
        seen.set(toolCallId, { ...item });
    });
    return Array.from(seen.values());
}

function getActiveRecoveryRun() {
    return state.currentRecoverySnapshot?.activeRun || null;
}

function syncRecoveryRoundOverlay() {
    const activeRun = getActiveRecoveryRun();
    const pausedSubagent = state.pausedSubagent || state.currentRecoverySnapshot?.pausedSubagent || null;
    const approvals = state.currentRecoverySnapshot?.pendingToolApprovals || [];
    const runId = String(activeRun?.run_id || state.activeRunId || '').trim();
    if (!runId) return;

    const runStatus = activeRun?.status
        || (pausedSubagent || approvals.length > 0 ? 'paused' : '');
    const runPhase = activeRun?.phase
        || (approvals.length > 0
            ? 'awaiting_tool_approval'
            : pausedSubagent
                ? 'awaiting_subagent_followup'
                : '');
    if (!runStatus && !runPhase && approvals.length === 0) return;

    overlayRoundRecoveryState(runId, {
        run_status: runStatus || undefined,
        run_phase: runPhase || undefined,
        is_recoverable: activeRun ? activeRun.is_recoverable !== false : true,
        pending_tool_approval_count: approvals.length,
        pending_tool_approvals: approvals,
    });
}

function bindContinuityWindowEvents() {
    if (continuity.listenersBound) return;
    continuity.listenersBound = true;
    window.addEventListener('focus', handleContinuityFocus, { passive: true });
    document.addEventListener('visibilitychange', handleContinuityVisibilityChange);
}

function handleContinuityFocus() {
    if (!continuity.sessionId || continuity.sessionId !== state.currentSessionId) return;
    scheduleRecoveryContinuityRefresh({
        sessionId: continuity.sessionId,
        delayMs: 0,
        includeRounds: false,
        quiet: true,
        reason: 'window-focus',
    });
}

function handleContinuityVisibilityChange() {
    if (document.visibilityState !== 'visible') return;
    handleContinuityFocus();
}

function syncSessionContinuity() {
    if (continuity.pollTimer) {
        clearTimeout(continuity.pollTimer);
        continuity.pollTimer = null;
    }
    if (!shouldPollContinuity()) return;

    continuity.pollTimer = setTimeout(() => {
        continuity.pollTimer = null;
        scheduleRecoveryContinuityRefresh({
            sessionId: continuity.sessionId,
            delayMs: 0,
            includeRounds: false,
            quiet: true,
            reason: 'continuity-poll',
        });
    }, nextContinuityPollDelay());
}

function shouldPollContinuity() {
    if (!continuity.sessionId || continuity.sessionId !== state.currentSessionId) return false;

    const activeRun = getActiveRecoveryRun();
    const hasApprovals = (state.currentRecoverySnapshot?.pendingToolApprovals || []).length > 0;
    const hasPausedSubagent = !!(state.pausedSubagent || state.currentRecoverySnapshot?.pausedSubagent);
    return !!(
        state.isGenerating
        || state.activeEventSource
        || hasApprovals
        || hasPausedSubagent
        || activeRun?.is_recoverable
    );
}

function nextContinuityPollDelay() {
    if (state.isGenerating || state.activeEventSource) {
        return CONTINUITY_POLL_ACTIVE_MS;
    }
    return CONTINUITY_POLL_IDLE_MS;
}

function mergePendingRefresh(current, next) {
    if (!current) return { ...next };
    return {
        sessionId: next.sessionId || current.sessionId,
        includeRounds: current.includeRounds || next.includeRounds,
        quiet: current.quiet && next.quiet,
        reason: next.reason || current.reason,
    };
}

async function flushScheduledContinuityRefresh() {
    if (continuity.refreshPromise) return;

    const request = continuity.pendingRefresh;
    continuity.pendingRefresh = null;
    if (!request) {
        syncSessionContinuity();
        return;
    }

    continuity.refreshPromise = runScheduledContinuityRefresh(request)
        .catch(() => null)
        .finally(() => {
            continuity.refreshPromise = null;
            syncSessionContinuity();
            if (continuity.pendingRefresh) {
                void flushScheduledContinuityRefresh();
            }
        });
    await continuity.refreshPromise;
}

async function runScheduledContinuityRefresh(request) {
    const safeSessionId = typeof request?.sessionId === 'string' ? request.sessionId.trim() : '';
    if (!safeSessionId || state.currentSessionId !== safeSessionId) return null;

    const canRefreshRounds = request.includeRounds && !state.isGenerating && !state.activeEventSource;
    if (canRefreshRounds) {
        await loadSessionRounds(safeSessionId);
        if (state.currentSessionId !== safeSessionId) return null;
    }
    return refreshSessionRecovery(safeSessionId, { quiet: request.quiet !== false });
}

function reconcileApprovalActionState(approvals) {
    const pendingIds = new Set(
        Array.isArray(approvals)
            ? approvals
                .map(item => String(item?.tool_call_id || '').trim())
                .filter(Boolean)
            : [],
    );
    Array.from(approvalActionBusyIds).forEach(toolCallId => {
        if (!pendingIds.has(toolCallId)) {
            approvalActionBusyIds.delete(toolCallId);
        }
    });
    Array.from(approvalActionErrors.keys()).forEach(toolCallId => {
        if (!pendingIds.has(toolCallId)) {
            approvalActionErrors.delete(toolCallId);
        }
    });
}

function areRecoverySnapshotsEquivalent(left, right) {
    return recoverySnapshotSignature(left) === recoverySnapshotSignature(right);
}

function recoverySnapshotSignature(snapshot) {
    if (!snapshot || typeof snapshot !== 'object') return '';
    return JSON.stringify({
        activeRun: signatureActiveRun(snapshot.activeRun),
        pendingToolApprovals: Array.isArray(snapshot.pendingToolApprovals)
            ? snapshot.pendingToolApprovals.map(signatureApproval)
            : [],
        pausedSubagent: signaturePausedSubagent(snapshot.pausedSubagent),
        roundSnapshotRunId: String(snapshot.roundSnapshot?.run_id || ''),
    });
}

function signatureActiveRun(activeRun) {
    if (!activeRun || typeof activeRun !== 'object') return null;
    return {
        run_id: String(activeRun.run_id || ''),
        status: String(activeRun.status || ''),
        phase: String(activeRun.phase || ''),
        is_recoverable: activeRun.is_recoverable !== false,
        checkpoint_event_id: Number(activeRun.checkpoint_event_id || 0),
        last_event_id: Number(activeRun.last_event_id || 0),
        pending_tool_approval_count: Number(activeRun.pending_tool_approval_count || 0),
        stream_connected: !!activeRun.stream_connected,
        should_show_recover: !!activeRun.should_show_recover,
    };
}

function signatureApproval(approval) {
    if (!approval || typeof approval !== 'object') return null;
    return {
        tool_call_id: String(approval.tool_call_id || ''),
        tool_name: String(approval.tool_name || ''),
        role_id: String(approval.role_id || ''),
        instance_id: String(approval.instance_id || ''),
        args_preview: String(approval.args_preview || ''),
    };
}

function signaturePausedSubagent(pausedSubagent) {
    if (!pausedSubagent || typeof pausedSubagent !== 'object') return null;
    return {
        runId: String(pausedSubagent.runId || ''),
        instanceId: String(pausedSubagent.instanceId || ''),
        roleId: String(pausedSubagent.roleId || ''),
        taskId: String(pausedSubagent.taskId || ''),
    };
}

function isLocallyStreaming(runId) {
    return !!(
        runId &&
        state.activeEventSource &&
        state.isGenerating &&
        state.activeRunId === runId
    );
}

function renderRecoveryBanner() {
    const host = ensureRecoveryBannerHost();
    if (!host) return;

    const snapshot = state.currentRecoverySnapshot;
    const activeRun = getActiveRecoveryRun();
    const pausedSubagent = state.pausedSubagent || snapshot?.pausedSubagent || null;
    const approvals = snapshot?.pendingToolApprovals || [];
    const hideBanner = (
        !activeRun
        || (isLocallyStreaming(activeRun.run_id) && approvals.length === 0 && !pausedSubagent)
    );
    const nextSignature = recoveryBannerSignature({
        hideBanner,
        activeRun,
        approvals,
        pausedSubagent,
    });
    if (nextSignature === recoveryBannerRenderSignature) {
        return;
    }
    recoveryBannerRenderSignature = nextSignature;

    if (hideBanner) {
        host.style.display = 'none';
        host.innerHTML = '';
        syncRecoveryRailMode({ approvals: [], pausedSubagent: null });
        return;
    }

    const footerActions = getFooterActions(activeRun, approvals, pausedSubagent);
    const hasBody = approvals.length > 0 || !!pausedSubagent;
    const pillTone = stateTone(activeRun);
    host.style.display = 'block';
    host.innerHTML = `
        <div class="recovery-banner recovery-tone-${pillTone}">
            <div class="recovery-banner-copy">
                <div class="recovery-banner-label">Session Recovery</div>
                <div class="recovery-banner-title">
                    <span>Run ${shortRunId(activeRun.run_id)}</span>
                    <span class="recovery-status-pill recovery-status-${pillTone}">
                        ${stateLabel(activeRun)}
                    </span>
                </div>
                <div class="recovery-banner-text">${describeRecoveryState(activeRun, approvals, pausedSubagent)}</div>
            </div>
            ${hasBody
        ? `<div class="recovery-banner-body">
                    ${approvals.length > 0 ? renderApprovalList(activeRun, approvals) : ''}
                    ${pausedSubagent ? renderPausedSubagentCallout(pausedSubagent) : ''}
                </div>`
        : ''
    }
            ${footerActions.length > 0
        ? `<div class="recovery-banner-actions">
                    ${footerActions
            .map(action => `
                            <button
                                type="button"
                                class="${action.kind === 'primary' ? 'primary-btn' : 'secondary-btn'} recovery-action-btn"
                                data-recovery-action="${action.action}"
                                ${recoveryActionBusy ? 'disabled' : ''}
                            >
                                ${action.label}
                            </button>
                        `)
            .join('')}
                </div>`
        : ''
    }
        </div>
    `;

    host.querySelectorAll('[data-recovery-action]').forEach(button => {
        const action = footerActions.find(item => item.action === button.dataset.recoveryAction);
        if (!action) return;
        button.onclick = () => {
            void handleRecoveryAction(action, activeRun, pausedSubagent);
        };
    });

    host.querySelectorAll('[data-approval-action]').forEach(button => {
        const toolCallId = String(button.dataset.toolCallId || '');
        const action = String(button.dataset.approvalAction || '');
        if (!toolCallId || !action) return;
        const approval = approvals.find(item => item.tool_call_id === toolCallId);
        if (!approval) return;
        button.onclick = () => {
            void handleApprovalAction(activeRun.run_id, approval, action);
        };
    });

    syncRecoveryRailMode({ approvals, pausedSubagent });
}

function recoveryBannerSignature({ hideBanner, activeRun, approvals, pausedSubagent }) {
    const busyIds = Array.from(approvalActionBusyIds).sort();
    const errorEntries = Array.from(approvalActionErrors.entries())
        .map(([toolCallId, message]) => [String(toolCallId), String(message || '')])
        .sort((left, right) => left[0].localeCompare(right[0]));
    return JSON.stringify({
        hidden: !!hideBanner,
        activeRun: signatureActiveRun(activeRun),
        approvals: Array.isArray(approvals) ? approvals.map(signatureApproval) : [],
        pausedSubagent: signaturePausedSubagent(pausedSubagent),
        recoveryActionBusy: !!recoveryActionBusy,
        approvalBusyIds: busyIds,
        approvalErrors: errorEntries,
        localStreamingRunId: activeRun && isLocallyStreaming(activeRun.run_id)
            ? String(activeRun.run_id || '')
            : '',
    });
}

function ensureRecoveryBannerHost() {
    if (els.recoveryBannerHost) return els.recoveryBannerHost;
    const inputContainer = document.querySelector('.input-container');
    if (!inputContainer) return null;
    const host = document.createElement('div');
    host.id = 'recovery-banner-host';
    host.className = 'recovery-banner-host';
    host.style.display = 'none';
    inputContainer.insertBefore(host, inputContainer.firstChild);
    els.recoveryBannerHost = host;
    return host;
}

function syncRecoveryRailMode({ approvals = [], pausedSubagent = null } = {}) {
    const rightRail = els.rightRail || document.getElementById('right-rail');
    if (!rightRail) return;
    const hasPendingApprovals = Array.isArray(approvals) && approvals.length > 0;
    const hasPausedSubagent = !!pausedSubagent;

    rightRail.classList.toggle('right-rail-recovery-priority', hasPendingApprovals);
    rightRail.classList.toggle('right-rail-followup-priority', !hasPendingApprovals && hasPausedSubagent);
}

function getFooterActions(activeRun, approvals, pausedSubagent) {
    const actions = [];
    if (!activeRun?.is_recoverable) return actions;
    if (isLocallyStreaming(activeRun.run_id)) return actions;
    if (activeRun.status === 'running' || activeRun.status === 'queued') {
        actions.push({
            action: 'resume-run',
            label: 'Connect Stream',
            kind: 'primary',
        });
    } else if (activeRun.status === 'stopped' || activeRun.phase === 'stopped') {
        actions.push({
            action: 'resume-run',
            label: 'Resume Run',
            kind: 'primary',
        });
    }
    if (pausedSubagent?.instanceId) {
        actions.push({
            action: 'open-subagent',
            label: 'Open Subagent',
            kind: 'secondary',
        });
    }
    if (approvals.length > 0) {
        actions.push({
            action: 'review-round',
            label: 'View Round',
            kind: 'secondary',
        });
    }
    return actions;
}

async function handleRecoveryAction(actionDef, activeRun, pausedSubagent) {
    if (!actionDef || !activeRun) return;
    if (actionDef.action === 'resume-run') {
        await resumeRecoverableRun(activeRun.run_id, {
            sessionId: state.currentSessionId,
            reason: `recovery ${activeRun.status || activeRun.phase || 'resume'}`,
        });
        return;
    }
    if (actionDef.action === 'review-round') {
        if (snapshotRoundFor(activeRun.run_id)) {
            selectRound(snapshotRoundFor(activeRun.run_id));
        } else {
            document
                .querySelector(`.session-round-section[data-run-id="${activeRun.run_id}"]`)
                ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
        return;
    }
    if (actionDef.action === 'open-subagent' && pausedSubagent?.instanceId) {
        focusSubagent(
            pausedSubagent.instanceId,
            pausedSubagent.roleId || pausedSubagent.instanceId,
        );
    }
}

async function handleApprovalAction(runId, approval, action) {
    const safeRunId = String(runId || '').trim();
    const safeToolCallId = String(approval?.tool_call_id || '').trim();
    const safeAction = String(action || '').trim().toLowerCase();
    if (!safeRunId || !safeToolCallId || !safeAction) return;
    if (approvalActionBusyIds.has(safeToolCallId)) return;

    approvalActionBusyIds.add(safeToolCallId);
    approvalActionErrors.delete(safeToolCallId);
    renderRecoveryBanner();

    try {
        const activeRun = getActiveRecoveryRun();
        const needsResumeBeforeApproval = (
            activeRun?.run_id === safeRunId &&
            (activeRun.status === 'stopped' || activeRun.phase === 'stopped')
        );
        let approvalWillResolveViaLiveRun = false;
        if (needsResumeBeforeApproval) {
            const resumed = await resumeRecoverableRun(safeRunId, {
                sessionId: state.currentSessionId,
                reason: 'resume before tool approval',
                quiet: true,
            });
            if (!resumed) {
                throw new Error('Failed to resume run before approval');
            }
            approvalWillResolveViaLiveRun = true;
            await waitForFreshApprovalRequest(safeRunId, safeToolCallId);
        }

        await resolveToolApproval(safeRunId, safeToolCallId, safeAction, '');
        if (!approvalWillResolveViaLiveRun) {
            markToolApprovalResolved(safeToolCallId);
        }
        const remainingApprovals = state.currentRecoverySnapshot?.pendingToolApprovals || [];
        if (!approvalWillResolveViaLiveRun && remainingApprovals.length === 0) {
            document.dispatchEvent(
                new CustomEvent('run-approval-resolved', {
                    detail: { runId: safeRunId },
                }),
            );
        }
    } catch (e) {
        approvalActionBusyIds.delete(safeToolCallId);
        approvalActionErrors.set(
            safeToolCallId,
            e?.message || 'Failed to resolve tool approval',
        );
        sysLog(e?.message || 'Failed to resolve tool approval', 'log-error');
        renderRecoveryBanner();
    }
}

async function waitForFreshApprovalRequest(runId, toolCallId, timeoutMs = 3000) {
    const safeRunId = String(runId || '').trim();
    const safeToolCallId = String(toolCallId || '').trim();
    if (!safeRunId || !safeToolCallId) return;

    await new Promise(resolve => {
        let settled = false;
        const cleanup = () => {
            if (settled) return;
            settled = true;
            clearTimeout(timer);
            document.removeEventListener('tool-approval-requested', onRequested);
            resolve();
        };
        const onRequested = (event) => {
            const detail = event?.detail || {};
            if (detail.runId !== safeRunId || detail.toolCallId !== safeToolCallId) {
                return;
            }
            cleanup();
        };
        const timer = setTimeout(cleanup, timeoutMs);
        document.addEventListener('tool-approval-requested', onRequested);
    });
}

function snapshotRoundFor(runId) {
    const round = state.currentRecoverySnapshot?.roundSnapshot;
    if (round?.run_id === runId) return round;
    return null;
}

function describeRecoveryState(activeRun, approvals, pausedSubagent) {
    if (pausedSubagent) {
        return `Paused at ${pausedSubagent.roleId || pausedSubagent.instanceId}. Open that subagent and send a follow-up to continue this run.`;
    }
    if (approvals.length > 0) {
        const noun = approvals.length === 1 ? 'approval' : 'approvals';
        return `Waiting for ${approvals.length} tool ${noun}. Resolve them here, then the run will continue from the latest checkpoint.`;
    }
    if (activeRun.status === 'running' || activeRun.status === 'queued') {
        return isLocallyStreaming(activeRun.run_id)
            ? 'This tab is following the live stream.'
            : 'A recoverable run is active for this session. Connect the stream to keep following progress.';
    }
    if (activeRun.status === 'stopped') {
        return 'Execution stopped at a durable checkpoint. Resume from the latest persisted state.';
    }
    return 'A recoverable run is available for this session.';
}

function stateLabel(activeRun) {
    if (!activeRun) return 'Unknown';
    switch (activeRun.phase) {
        case 'awaiting_tool_approval':
            return 'Awaiting Approval';
        case 'awaiting_subagent_followup':
            return 'Awaiting Follow-up';
        case 'running':
            return 'Running';
        case 'stopped':
            return 'Stopped';
        case 'queued':
            return 'Queued';
        default:
            break;
    }
    switch (activeRun.status) {
        case 'running':
            return 'Running';
        case 'paused':
            return 'Paused';
        case 'stopped':
            return 'Stopped';
        case 'queued':
            return 'Queued';
        case 'completed':
            return 'Completed';
        case 'failed':
            return 'Failed';
        default:
            return 'Recoverable';
    }
}

function stateTone(activeRun) {
    if (!activeRun) return 'idle';
    if (activeRun.phase === 'awaiting_tool_approval') return 'warning';
    if (activeRun.phase === 'awaiting_subagent_followup') return 'warning';
    switch (activeRun.status) {
        case 'running':
            return 'running';
        case 'stopped':
            return 'stopped';
        case 'failed':
            return 'danger';
        case 'completed':
            return 'success';
        default:
            return 'idle';
    }
}

function shortRunId(runId) {
    const safe = String(runId || '');
    return safe.length > 16 ? `${safe.slice(0, 8)}...${safe.slice(-4)}` : safe;
}

function renderApprovalList(activeRun, approvals) {
    return `
        <div class="recovery-approval-list">
            ${approvals.map(item => renderApprovalItem(activeRun, item)).join('')}
        </div>
    `;
}

function renderApprovalItem(activeRun, approval) {
    const toolCallId = String(approval?.tool_call_id || '');
    const busy = approvalActionBusyIds.has(toolCallId);
    const error = approvalActionErrors.get(toolCallId) || '';
    const statusClass = error ? 'is-error' : busy ? 'is-busy' : '';
    const statusText = error || (busy ? 'Applying...' : '');
    const actor = humanizeRoleLabel(approval?.role_id || approval?.instance_id || 'Agent');
    const title = approvalTitle(approval);
    const subtitle = `Requested by ${actor}`;

    return `
        <section class="recovery-approval-item">
            <div class="recovery-approval-copy">
                <div class="recovery-approval-title">${escapeHtml(title)}</div>
                <div class="recovery-approval-text">${escapeHtml(subtitle)}</div>
            </div>
            <div class="recovery-approval-actions">
                ${statusText
        ? `<span class="recovery-approval-status ${statusClass}">${escapeHtml(statusText)}</span>`
        : '<span class="recovery-approval-status"></span>'
    }
                <div class="recovery-approval-buttons">
                    <button
                        type="button"
                        class="recovery-choice-btn recovery-choice-approve"
                        data-approval-action="approve"
                        data-tool-call-id="${escapeAttribute(toolCallId)}"
                        ${busy || recoveryActionBusy ? 'disabled' : ''}
                    >
                        Approve
                    </button>
                    <button
                        type="button"
                        class="recovery-choice-btn recovery-choice-deny"
                        data-approval-action="deny"
                        data-tool-call-id="${escapeAttribute(toolCallId)}"
                        ${busy || recoveryActionBusy ? 'disabled' : ''}
                    >
                        Deny
                    </button>
                </div>
            </div>
        </section>
    `;
}

function renderPausedSubagentCallout(pausedSubagent) {
    const actor = pausedSubagent.roleId || pausedSubagent.instanceId;
    return `
        <div class="recovery-subagent-callout">
            <div class="recovery-subagent-copy">
                <div class="recovery-subagent-title">${escapeHtml(actor || 'Paused subagent')}</div>
                <div class="recovery-subagent-text">This run is waiting for a follow-up inside the paused subagent panel.</div>
            </div>
        </div>
    `;
}

function approvalTitle(approval) {
    const toolName = String(approval?.tool_name || '');
    const args = parseApprovalArgs(approval?.args_preview);
    if (toolName === 'list_available_roles') {
        return 'List available roles';
    }
    if (toolName === 'create_tasks') {
        const taskCount = normalizeCount(args.task_count);
        return taskCount > 0
            ? `Create ${taskCount} delegated task${taskCount === 1 ? '' : 's'}`
            : 'Create delegated tasks';
    }
    if (toolName === 'dispatch_task') {
        return 'Dispatch task';
    }
    return `Run ${humanizeToolName(toolName || 'tool')}`;
}

function parseApprovalArgs(argsPreview) {
    if (!argsPreview) return {};
    try {
        const parsed = JSON.parse(String(argsPreview));
        return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (e) {
        return {};
    }
}

function normalizeCount(value) {
    const num = Number(value || 0);
    return Number.isFinite(num) ? num : 0;
}

function humanizeRoleLabel(value) {
    return humanizeRoleId(value, { coordinatorLabel: 'Coordinator Agent' });
}

function humanizeToolName(value) {
    const safe = String(value || '').trim();
    if (!safe) return 'Tool';
    return safe
        .split('_')
        .filter(Boolean)
        .map(part => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ');
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function escapeAttribute(value) {
    return escapeHtml(value);
}
