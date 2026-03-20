/**
 * components/rounds/timeline.js
 * Session timeline rendering, scroll-sync, and paging orchestration.
 */
import { els } from '../../utils/dom.js';
import { isCoordinatorRoleId, getCoordinatorRoleId, state } from '../../core/state.js';
import { fetchRunTokenUsage } from '../../core/api.js';
import { setRoundPendingApprovals } from '../agentPanel.js';
import {
    clearAllStreamState,
    getCoordinatorStreamOverlay,
    renderHistoricalMessageList,
    getOrCreateStreamBlock,
    appendStreamChunk,
} from '../messageRenderer.js';
import { renderRoundNavigator, setActiveRoundNav } from './navigator.js';
import { applyRoundPage, fetchInitialRoundsPage, fetchOlderRoundsPage } from './paging.js';
import { roundsState } from './state.js';
import { roundSectionId, esc, roundStateLabel, roundStateTone } from './utils.js';
import { errorToPayload, logError } from '../../utils/logger.js';

export let currentRounds = [];
export let currentRound = null;
let retryTimelineTimerId = 0;

export async function loadSessionRounds(sessionId) {
    try {
        const page = await fetchInitialRoundsPage(sessionId);
        applyRoundPage(page, { prepend: false });
        syncExportedState();
        renderSessionTimeline(roundsState.currentRounds, { preserveScroll: false });
    } catch (e) {
        logError(
            'frontend.rounds.load_failed',
            'Failed loading rounds',
            errorToPayload(e, { session_id: sessionId }),
        );
    }
}

export function createLiveRound(runId, intentText) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) return;

    const existingIndex = roundsState.currentRounds.findIndex(round => round.run_id === safeRunId);
    if (existingIndex === -1) {
        roundsState.currentRounds = [
            ...roundsState.currentRounds,
            {
                run_id: safeRunId,
                created_at: new Date().toISOString(),
                intent: intentText,
                coordinator_messages: [],
                instance_role_map: {},
                role_instance_map: {},
                run_status: 'running',
                run_phase: 'running',
                is_recoverable: true,
                pending_tool_approval_count: 0,
            },
        ];
    } else {
        roundsState.currentRounds = roundsState.currentRounds.map(round =>
            round.run_id === safeRunId
                ? {
                    ...round,
                    run_status: round.run_status || 'running',
                    run_phase: round.run_phase || 'running',
                    is_recoverable: round.is_recoverable !== false,
                }
                : round,
        );
    }
    syncExportedState();
    renderSessionTimeline(roundsState.currentRounds, { preserveScroll: false });

    const section = document.getElementById(roundSectionId(safeRunId));
    if (section) {
        section.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}

export function appendRoundUserMessage(runId, text) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) return;
    const roundIndex = roundsState.currentRounds.findIndex(round => round.run_id === safeRunId);
    if (roundIndex >= 0) {
        roundsState.currentRounds = roundsState.currentRounds.map(round =>
            round.run_id === safeRunId
                ? { ...round, has_user_messages: true }
                : round,
        );
        if (roundsState.currentRound?.run_id === safeRunId) {
            roundsState.currentRound = roundsState.currentRounds[roundIndex];
        }
        syncExportedState();
    }
    const section = document.querySelector(`.session-round-section[data-run-id="${safeRunId}"]`);
    if (!section) return;
    const empty = section.querySelector('.panel-empty');
    if (empty) empty.remove();

    const coordinatorRoleId = getCoordinatorRoleId();
    getOrCreateStreamBlock(section, 'coordinator', coordinatorRoleId, 'Coordinator', safeRunId);
    appendStreamChunk('coordinator', '', safeRunId, coordinatorRoleId, 'Coordinator');

    els.chatMessages.scrollTop = els.chatMessages.scrollHeight;
}

export function appendRoundRetryEvent(runId, retryEvent) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId || !retryEvent || typeof retryEvent !== 'object') return;

    roundsState.currentRounds = roundsState.currentRounds.map(round => {
        if (round.run_id !== safeRunId) {
            return round;
        }
        return {
            ...round,
            retry_events: [retryEvent],
        };
    });
    if (roundsState.currentRound?.run_id === safeRunId) {
        roundsState.currentRound = roundsState.currentRounds.find(round => round.run_id === safeRunId) || roundsState.currentRound;
    }
    syncExportedState();
    patchRoundRetryEvents(safeRunId);
    syncRetryTimelineTimer();
}

export function updateRoundRetryEvent(runId, retryEventId, updates) {
    const safeRunId = String(runId || '').trim();
    const safeRetryEventId = String(retryEventId || '').trim();
    if (!safeRunId || !safeRetryEventId || !updates || typeof updates !== 'object') return;

    roundsState.currentRounds = roundsState.currentRounds.map(round => {
        if (round.run_id !== safeRunId) {
            return round;
        }
        const existing = Array.isArray(round.retry_events) ? round.retry_events : [];
        return {
            ...round,
            retry_events: existing.map(event => (
                event?.event_id === safeRetryEventId
                    ? { ...event, ...updates }
                    : event
            )),
        };
    });
    if (roundsState.currentRound?.run_id === safeRunId) {
        roundsState.currentRound = roundsState.currentRounds.find(round => round.run_id === safeRunId) || roundsState.currentRound;
    }
    syncExportedState();
    patchRoundRetryEvents(safeRunId);
    syncRetryTimelineTimer();
}

export function removeRoundRetryEvent(runId, retryEventId) {
    const safeRunId = String(runId || '').trim();
    const safeRetryEventId = String(retryEventId || '').trim();
    if (!safeRunId || !safeRetryEventId) return;

    roundsState.currentRounds = roundsState.currentRounds.map(round => {
        if (round.run_id !== safeRunId) {
            return round;
        }
        const existing = Array.isArray(round.retry_events) ? round.retry_events : [];
        return {
            ...round,
            retry_events: existing.filter(event => event?.event_id !== safeRetryEventId),
        };
    });
    if (roundsState.currentRound?.run_id === safeRunId) {
        roundsState.currentRound = roundsState.currentRounds.find(round => round.run_id === safeRunId) || roundsState.currentRound;
    }
    syncExportedState();
    patchRoundRetryEvents(safeRunId);
    syncRetryTimelineTimer();
}

export function overlayRoundRecoveryState(runId, overlay = {}) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) return;

    const roundIndex = roundsState.currentRounds.findIndex(round => round.run_id === safeRunId);
    if (roundIndex === -1) return;

    const current = roundsState.currentRounds[roundIndex];
    const nextRound = {
        ...current,
        ...pickDefinedRoundOverlay(overlay),
    };
    roundsState.currentRounds = roundsState.currentRounds.map(round =>
        round.run_id === safeRunId ? nextRound : round,
    );
    if (roundsState.currentRound?.run_id === safeRunId) {
        roundsState.currentRound = nextRound;
    }
    syncExportedState();
    patchRoundHeader(nextRound, roundIndex);
    syncRetryTimelineTimer();
    renderRoundNavigator(roundsState.currentRounds, selectRound);
    setActiveRoundNav(roundsState.activeRunId);

    if (roundsState.currentRound?.run_id === safeRunId) {
        const pendingApprovals = Array.isArray(nextRound.pending_tool_approvals)
            ? nextRound.pending_tool_approvals
            : [];
        setRoundPendingApprovals(safeRunId, pendingApprovals);
    }
}

export function selectRound(round) {
    if (!round) return;
    const section = document.getElementById(roundSectionId(round.run_id));
    if (!section) return;
    roundsState.pendingScrollTargetRunId = round.run_id;
    roundsState.pendingScrollUnlockAt = Date.now() + 1600;
    roundsState.activeRunId = round.run_id;
    roundsState.activeVisibility = Number.POSITIVE_INFINITY;
    setActiveRoundNav(round.run_id);
    section.scrollIntoView({ behavior: 'smooth', block: 'start' });
    emphasizeRoundSection(section);
}

export function goBackToSessions() {
    // Legacy no-op: session list always visible now.
}

function renderSessionTimeline(rounds, opts = { preserveScroll: true }) {
    const container = els.chatMessages;
    if (!container) return;

    const oldScroll = container.scrollTop;
    container.innerHTML = '';

    clearAllStreamState();
    roundsState.activeRunId = null;
    roundsState.activeVisibility = 0;

    if (!rounds || rounds.length === 0) {
        roundsState.currentRound = null;
        syncExportedState();
        state.instanceRoleMap = {};
        state.roleInstanceMap = {};
        state.taskInstanceMap = {};
        state.taskStatusMap = {};
        roundsState.activeRunId = null;
        setRoundPendingApprovals('', [], {});
        renderRoundNavigator([], selectRound);
        syncRetryTimelineTimer();
        return;
    }

    rounds.forEach((round, index) => {
        const section = document.createElement('section');
        section.className = 'session-round-section';
        section.dataset.runId = round.run_id;
        section.id = roundSectionId(round.run_id);

        const time = new Date(round.created_at).toLocaleString();
        const stateLabel = roundStateLabel(round);
        const stateTone = roundStateTone(round);
        const approvalCount = Number(round.pending_tool_approval_count || 0);
        const header = document.createElement('div');
        header.className = 'round-detail-header';
        header.innerHTML = `
            <div class="round-detail-topline">
                <div class="round-detail-mainline">
                    <div class="round-detail-label">Round ${index + 1}${round.run_status === 'running' ? ' <span class="live-badge">LIVE</span>' : ''}</div>
                    <div class="round-detail-meta">
                        <div class="round-detail-time">${time}</div>
                        <div class="round-detail-token-host"></div>
                    </div>
                </div>
                <div class="round-detail-badges">${renderRoundBadges(round, stateLabel, stateTone, approvalCount)}</div>
            </div>
            <div class="round-detail-intent">${esc(round.intent || 'No intent')}</div>`;
        section.appendChild(header);
        renderRoundRetryEvents(section, round.retry_events || []);

        const pendingCoordinatorApprovals = (round.pending_tool_approvals || []).filter(item => {
            const roleId = item?.role_id || '';
            return roleId === '' || isCoordinatorRoleId(roleId);
        });
        const coordinatorOverlay = getCoordinatorStreamOverlay(round.run_id);

        if (round.coordinator_messages?.length > 0) {
            renderHistoricalMessageList(section, round.coordinator_messages, {
                pendingToolApprovals: pendingCoordinatorApprovals,
                runId: round.run_id,
                streamOverlayEntry: coordinatorOverlay,
            });
        } else if (pendingCoordinatorApprovals.length > 0 || coordinatorOverlay) {
            renderHistoricalMessageList(section, [], {
                pendingToolApprovals: pendingCoordinatorApprovals,
                runId: round.run_id,
                streamOverlayEntry: coordinatorOverlay,
            });
        } else if (!round.has_user_messages) {
            const empty = document.createElement('div');
            empty.className = 'panel-empty';
            empty.textContent = 'No coordinator messages in this round.';
            section.appendChild(empty);
        }

        container.appendChild(section);

        if (state.currentSessionId) {
            const headerEl = header;
            void fetchRunTokenUsage(state.currentSessionId, round.run_id).then(usage => {
                if (!usage || usage.total_tokens === 0) return;
                const fmt = n => n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
                const pill = document.createElement('div');
                pill.className = 'round-token-summary';
                pill.title = `Input: ${usage.total_input_tokens} | Output: ${usage.total_output_tokens} | Requests: ${usage.total_requests}`;
                pill.innerHTML = `
                    <span class="token-in">In ${fmt(usage.total_input_tokens)}</span>
                    <span class="token-out">Out ${fmt(usage.total_output_tokens)}</span>
                    ${usage.total_tool_calls > 0 ? `<span class="token-tools">Tools ${usage.total_tool_calls}</span>` : ''}
                `;
                const tokenHost = headerEl.querySelector('.round-detail-token-host');
                if (tokenHost) {
                    tokenHost.appendChild(pill);
                }
            });
        }
    });

    renderRoundNavigator(rounds, selectRound);
    bindScrollSync();

    if (opts.preserveScroll) {
        container.scrollTop = oldScroll;
        syncActiveRoundFromScroll();
    } else {
        container.scrollTop = container.scrollHeight;
        activateLatestRound(rounds);
    }
    schedulePostLayoutRoundSync(container);
    syncRetryTimelineTimer();
}

function bindScrollSync() {
    if (roundsState.scrollBound || !els.chatMessages) return;
    els.chatMessages.addEventListener('scroll', syncActiveRoundFromScroll, { passive: true });
    roundsState.scrollBound = true;
}

function syncActiveRoundFromScroll() {
    const container = els.chatMessages;
    if (!container) return;

    const sections = Array.from(container.querySelectorAll('.session-round-section'));
    if (sections.length === 0) return;

    if (syncPendingRoundSelection(container)) {
        return;
    }

    const atTop = container.scrollTop <= 2;
    const atBottom = container.scrollTop + container.clientHeight >= container.scrollHeight - 2;
    if (atTop) {
        activateRoundSection(sections[0], Number.POSITIVE_INFINITY);
        void loadOlderRounds();
        return;
    }
    if (atBottom) {
        activateRoundSection(sections[sections.length - 1], Number.POSITIVE_INFINITY);
        return;
    }

    const containerRect = container.getBoundingClientRect();
    let best = null;
    let bestVisible = -1;

    sections.forEach(sec => {
        const rect = sec.getBoundingClientRect();
        const visibleTop = Math.max(rect.top, containerRect.top);
        const visibleBottom = Math.min(rect.bottom, containerRect.bottom);
        const visible = Math.max(0, visibleBottom - visibleTop);
        if (visible > bestVisible) {
            bestVisible = visible;
            best = sec;
        }
    });
    activateRoundSection(best, bestVisible);
}

function activateRoundSection(section, visibleScore) {
    const runId = section?.dataset?.runId || null;
    if (!runId) return;

    if (
        roundsState.activeRunId &&
        runId !== roundsState.activeRunId &&
        visibleScore < roundsState.activeVisibility * 1.08
    ) {
        return;
    }
    if (runId === roundsState.activeRunId) {
        roundsState.activeVisibility = visibleScore;
        return;
    }

    roundsState.activeRunId = runId;
    roundsState.activeVisibility = visibleScore;
    roundsState.currentRound = roundsState.currentRounds.find(r => r.run_id === runId) || null;
    const pendingApprovals = roundsState.currentRound?.pending_tool_approvals || [];
    setRoundPendingApprovals(runId, pendingApprovals);
    syncExportedState();

    setActiveRoundNav(runId);
}

function syncPendingRoundSelection(container) {
    const pendingRunId = String(roundsState.pendingScrollTargetRunId || '').trim();
    if (!pendingRunId) return false;

    const targetSection = container.querySelector(`.session-round-section[data-run-id="${pendingRunId}"]`);
    if (!targetSection) {
        clearPendingRoundSelection();
        return false;
    }

    if (hasPendingSelectionExpired() || isRoundSectionSettled(targetSection, container)) {
        activateRoundSection(targetSection, Number.POSITIVE_INFINITY);
        emphasizeRoundSection(targetSection);
        clearPendingRoundSelection();
        return true;
    }

    return true;
}

function isRoundSectionSettled(section, container) {
    const containerRect = container.getBoundingClientRect();
    const sectionRect = section.getBoundingClientRect();
    return Math.abs(sectionRect.top - containerRect.top) <= 28;
}

function hasPendingSelectionExpired() {
    return Date.now() >= Number(roundsState.pendingScrollUnlockAt || 0);
}

function clearPendingRoundSelection() {
    roundsState.pendingScrollTargetRunId = null;
    roundsState.pendingScrollUnlockAt = 0;
}

function activateLatestRound(rounds) {
    const latestRound = Array.isArray(rounds) && rounds.length > 0
        ? rounds[rounds.length - 1]
        : null;
    if (!latestRound?.run_id) {
        return;
    }
    roundsState.activeRunId = latestRound.run_id;
    roundsState.activeVisibility = Number.POSITIVE_INFINITY;
    roundsState.currentRound = latestRound;
    const pendingApprovals = latestRound.pending_tool_approvals || [];
    setRoundPendingApprovals(latestRound.run_id, pendingApprovals);
    syncExportedState();
    setActiveRoundNav(latestRound.run_id);
}

function schedulePostLayoutRoundSync(container) {
    if (!container || typeof window.requestAnimationFrame !== 'function') {
        syncActiveRoundFromScroll();
        return;
    }
    window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => {
            if (!container.isConnected) return;
            syncActiveRoundFromScroll();
        });
    });
}

function emphasizeRoundSection(section) {
    if (!(section instanceof HTMLElement)) return;
    section.classList.remove('round-section-emphasis');
    void section.offsetWidth;
    section.classList.add('round-section-emphasis');
    window.setTimeout(() => {
        section.classList.remove('round-section-emphasis');
    }, 1600);
}

async function loadOlderRounds() {
    if (!roundsState.paging.hasMore || roundsState.paging.loading || !state.currentSessionId) return;

    const container = els.chatMessages;
    if (!container) return;

    roundsState.paging.loading = true;
    const oldHeight = container.scrollHeight;
    const oldTop = container.scrollTop;
    try {
        const page = await fetchOlderRoundsPage();
        if (!page) {
            roundsState.paging.loading = false;
            return;
        }
        applyRoundPage(page, { prepend: true });
        syncExportedState();
        renderSessionTimeline(roundsState.currentRounds, { preserveScroll: true });
        const newHeight = container.scrollHeight;
        container.scrollTop = newHeight - oldHeight + oldTop;
    } catch (e) {
        logError(
            'frontend.rounds.load_older_failed',
            'Failed loading older rounds',
            errorToPayload(e, { session_id: sessionId }),
        );
        roundsState.paging.loading = false;
    }
}

function syncExportedState() {
    currentRounds = roundsState.currentRounds;
    currentRound = roundsState.currentRound;
}

function pickDefinedRoundOverlay(overlay) {
    const next = {};
    [
        'run_status',
        'run_phase',
        'is_recoverable',
        'pending_tool_approval_count',
        'pending_tool_approvals',
    ].forEach(key => {
        if (Object.prototype.hasOwnProperty.call(overlay, key)) {
            next[key] = overlay[key];
        }
    });
    return next;
}

function patchRoundHeader(round, roundIndex) {
    const section = document.querySelector(`.session-round-section[data-run-id="${round.run_id}"]`);
    if (!section) return;

    const labelEl = section.querySelector('.round-detail-label');
    if (labelEl) {
        labelEl.innerHTML = `Round ${roundIndex + 1}${round.run_status === 'running' ? ' <span class="live-badge">LIVE</span>' : ''}`;
    }

    const badgesEl = section.querySelector('.round-detail-badges');
    if (badgesEl) {
        const stateLabel = roundStateLabel(round);
        const stateTone = roundStateTone(round);
        const approvalCount = Number(round.pending_tool_approval_count || 0);
        badgesEl.innerHTML = renderRoundBadges(round, stateLabel, stateTone, approvalCount);
    }
}

function patchRoundRetryEvents(runId) {
    const round = roundsState.currentRounds.find(item => item.run_id === runId);
    const section = document.querySelector(`.session-round-section[data-run-id="${runId}"]`);
    if (!round || !section) return;
    const existing = section.querySelector('.round-retry-timeline');
    if (existing) {
        existing.remove();
    }
    renderRoundRetryEvents(section, round.retry_events || []);
}

function syncRetryTimelineTimer() {
    const hasActiveRetry = roundsState.currentRounds.some(round =>
        (Array.isArray(round.retry_events) ? round.retry_events : []).some(isScheduledRetryEvent),
    );
    if (!hasActiveRetry) {
        clearRetryTimelineTimer();
        return;
    }
    if (retryTimelineTimerId) {
        return;
    }
    retryTimelineTimerId = window.setInterval(() => {
        patchAllActiveRetryEvents();
    }, 200);
}

function clearRetryTimelineTimer() {
    if (!retryTimelineTimerId) {
        return;
    }
    clearInterval(retryTimelineTimerId);
    retryTimelineTimerId = 0;
}

function patchAllActiveRetryEvents() {
    roundsState.currentRounds.forEach(round => {
        const retryEvents = Array.isArray(round.retry_events) ? round.retry_events : [];
        if (retryEvents.some(isScheduledRetryEvent)) {
            patchRoundRetryEvents(round.run_id);
        }
    });
}

function renderRoundBadges(round, stateLabel, stateTone, approvalCount) {
    return `
        ${stateLabel ? `<span class="round-state-pill round-state-${stateTone}">${esc(stateLabel)}</span>` : ''}
        ${approvalCount > 0 ? `<span class="round-state-pill round-state-warning">${approvalCount} approval${approvalCount === 1 ? '' : 's'}</span>` : ''}
    `;
}

function renderRoundRetryEvents(section, retryEvents) {
    const items = Array.isArray(retryEvents) ? retryEvents : [];
    if (items.length === 0) {
        return;
    }
    const host = document.createElement('div');
    host.className = 'round-retry-timeline';
    const nowMs = Date.now();
    host.innerHTML = items.map(event => renderRetryEventMarkup(event, nowMs)).join('');
    section.appendChild(host);
}

function renderRetryEventMarkup(event, nowMs) {
    const attemptNumber = Number(event?.attempt_number || 0);
    const totalAttempts = Number(event?.total_attempts || 0);
    const isActive = event?.is_active === true;
    const phase = String(event?.phase || '').trim() || 'scheduled';
    const retryInMs = Number(event?.retry_in_ms || 0);
    const displayRetryMs = isScheduledRetryEvent(event)
        ? computeRetryRemainingMs(event, nowMs)
        : retryInMs;
    const retrySeconds = displayRetryMs > 0
        ? `${(displayRetryMs / 1000).toFixed(displayRetryMs >= 10000 ? 0 : 1)}s`
        : '0s';
    const errorCode = String(event?.error_code || '').trim();
    const errorMessage = String(event?.error_message || '').trim();
    const occurredAt = String(event?.occurred_at || '').trim();
    const occurredLabel = occurredAt ? new Date(occurredAt).toLocaleTimeString() : '';
    const copy = phase === 'retrying'
        ? `Attempt ${attemptNumber}/${totalAttempts} in progress`
        : phase === 'failed'
            ? `Attempt ${attemptNumber}/${totalAttempts} failed`
            : `Attempt ${attemptNumber}/${totalAttempts} in ${retrySeconds}`;
    const label = phase === 'retrying'
        ? 'Retrying'
        : phase === 'failed'
            ? 'Retry failed'
            : 'Retry scheduled';
    return `
        <div class="round-retry-item${isActive ? ' round-retry-item-active' : ''}${phase === 'failed' ? ' round-retry-item-failed' : ''}">
            <div class="round-retry-main">
                <span class="round-retry-label">${label}</span>
                <span class="round-retry-copy">${copy}</span>
            </div>
            <div class="round-retry-meta">
                ${errorCode ? `<span class="round-retry-code">${esc(errorCode)}</span>` : ''}
                ${occurredLabel ? `<span class="round-retry-time">${esc(occurredLabel)}</span>` : ''}
            </div>
            ${errorMessage ? `<div class="round-retry-detail">${esc(errorMessage)}</div>` : ''}
        </div>
    `;
}

function isScheduledRetryEvent(event) {
    return String(event?.phase || 'scheduled').trim() === 'scheduled'
        && Number(event?.retry_in_ms || 0) > 0;
}

function computeRetryRemainingMs(event, nowMs = Date.now()) {
    const retryInMs = Math.max(0, Number(event?.retry_in_ms || 0));
    if (retryInMs === 0) {
        return 0;
    }
    const occurredAt = String(event?.occurred_at || '').trim();
    const occurredAtMs = Date.parse(occurredAt);
    if (Number.isFinite(occurredAtMs)) {
        return Math.max(0, occurredAtMs + retryInMs - nowMs);
    }
    const fallbackRemainingMs = Number(event?.remaining_ms || retryInMs);
    return Math.max(0, fallbackRemainingMs);
}
