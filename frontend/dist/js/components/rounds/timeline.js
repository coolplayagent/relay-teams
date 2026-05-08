/**
 * components/rounds/timeline.js
 * Session timeline rendering, scroll-sync, and paging orchestration.
 */
import { els } from '../../utils/dom.js';
import {
    getRunPrimaryRoleId,
    getRunPrimaryRoleLabel,
    isRunPrimaryRoleId,
    state,
} from '../../core/state.js';
import { fetchRunTokenUsage, fetchSessionRound } from '../../core/api.js';
import { setRoundPendingApprovals } from '../agentPanel.js';
import {
    clearAllStreamState,
    clearRunStreamState,
    reconcileTerminalRunStreamState,
    getCoordinatorStreamOverlay,
    renderHistoricalMessageList,
    bindCopyButton,
    syncLastAnswerCopyButton,
} from '../messageRenderer.js';
import {
    normalizePromptContentParts,
    renderPromptContentParts,
    summarizePromptContentParts,
} from '../messageRenderer/helpers/prompt.js';
import { renderPromptTokenizedText } from '../../utils/promptTokens.js';
import {
    clearRoundNavigator,
    patchRoundNavigatorTodo,
    renderRoundNavigator,
    setActiveRoundNav,
} from './navigator.js';
import {
    applyRoundPage,
    applyTimelineRoundPage,
    fetchInitialRoundsPage,
    fetchOlderRoundsPage,
    fetchTimelineRoundsPage,
    sortRoundsAscending,
} from './paging.js';
import {
    captureChatScrollAnchor,
    restoreChatScrollAnchor,
    shouldFollowLatestRoundAfterCompletion,
} from './scrollController.js';
import { roundsState } from './state.js';
import { areRoundTodoSnapshotsEqual, normalizeRoundTodoSnapshot } from './todo.js';
import { roundSectionId, esc, roundStateLabel, roundStateTone } from './utils.js';
import { errorToPayload, logError } from '../../utils/logger.js';
import { formatMessage, t } from '../../utils/i18n.js';

export let currentRounds = [];
export let currentRound = null;
let retryTimelineTimerId = 0;
const expandedHistorySegments = new Set();
const roundIntentOpenState = new Map();
const liveRoundsBySession = new Map();
const terminalRoundRefreshes = new Map();
const ROUND_PROGRAMMATIC_SCROLL_LOCK_MS = 450;
const ROUND_ACTIVE_LOCK_MS = 900;
const ROUND_HISTORY_LOAD_INTENT_DEBOUNCE_MS = 120;
const ROUND_HISTORY_LOAD_STEP_MIN_MS = 150;
const ROUND_SCROLL_ANIMATION_MIN_MS = 420;
const ROUND_SCROLL_ANIMATION_MAX_MS = 650;
const ROUND_TIMELINE_SCROLL_ANIMATION_MIN_MS = 520;
const ROUND_TIMELINE_SCROLL_ANIMATION_MAX_MS = 2400;
const ROUND_SCROLL_BOTTOM_THRESHOLD_PX = 96;
const TERMINAL_ROUND_REFRESH_DELAYS_MS = [0, 80, 140, 240, 380, 560];
const TERMINAL_ROUND_REFRESH_FOLLOWUP_DELAY_MS = 900;
const TERMINAL_ROUND_REFRESH_MAX_FOLLOWUPS = 3;
let olderRoundLoadFailed = false;
let olderRoundLoadIntentTimer = 0;
let olderRoundPagingIntentBound = false;
let olderRoundTouchStartY = null;
let chatScrollAnimationFrame = 0;
let chatScrollAnimationToken = 0;
let scrollToBottomControl = null;
let historyLoadMoreResizeBound = false;
let roundTokenUsageController = null;
const terminalRoundOverlays = new Map();

export function clearSessionTimeline() {
    abortRoundTokenUsageRequests();
    terminalRoundOverlays.clear();
    roundsState.currentRounds = [];
    roundsState.timelineRounds = [];
    roundsState.currentRound = null;
    roundsState.activeRunId = null;
    roundsState.activeVisibility = 0;
    roundsState.activeLockUntil = 0;
    roundsState.pendingScrollTargetRunId = null;
    roundsState.pendingScrollUnlockAt = 0;
    roundsState.programmaticScrollUnlockAt = 0;
    roundsState.paging = {
        hasMore: false,
        nextCursor: null,
        loading: false,
    };
    expandedHistorySegments.clear();
    olderRoundLoadFailed = false;
    clearOlderRoundLoadIntentTimer();
    olderRoundTouchStartY = null;
    cancelChatScrollAnimation();
    removeScrollToBottomControl();
    setRoundPendingApprovals('', [], {});
    clearRetryTimelineTimer();
    syncExportedState();
    clearRoundNavigator();
}

export async function loadSessionRounds(sessionId, options = {}) {
    const renderPlan = captureRoundRenderPlan(options);
    const signal = options.signal || null;
    throwIfAborted(signal);
    const priority = String(options.priority || '').trim();
    const timelineLoadMode = options.timelineLoadMode === 'background' ? 'background' : 'await';
    abortRoundTokenUsageRequests();
    try {
        const timelinePageResult = timelineLoadMode === 'await'
            ? fetchTimelineRoundPageResult(sessionId, { priority, signal })
            : null;
        const page = await fetchInitialRoundsPage(sessionId, {
            priority,
            signal,
            summary: timelineLoadMode === 'background',
        });
        throwIfAborted(signal);
        if (!isSessionLoadCurrent(sessionId)) {
            return;
        }
        applyRoundPage(page, {
            prepend: false,
            mergeExisting: renderPlan.preserveLoadedRounds,
        });
        applyTimelineRoundPage(page);
        reconcileTerminalRoundStreamState(page);
        mergeLiveRoundsForSession(sessionId);
        applyTerminalRoundOverlays();
        syncExportedState();
        renderLoadedRoundPage(sessionId, options, renderPlan);

        if (timelineLoadMode === 'background') {
            void applyFullRoundPageInBackground(sessionId, {
                options,
                renderPlan,
                signal,
            }).catch(error => {
                if (error?.name === 'AbortError') {
                    return;
                }
                logError(
                    'frontend.rounds.full_page_load_failed',
                    'Failed loading full round page',
                    errorToPayload(error, { session_id: sessionId }),
                );
            });
            void applyTimelineRoundPageInBackground(sessionId, {
                priority: '',
                renderPlan,
                signal,
            }).catch(error => {
                if (error?.name === 'AbortError') {
                    return;
                }
                logError(
                    'frontend.rounds.timeline_load_failed',
                    'Failed loading timeline rounds',
                    errorToPayload(error, { session_id: sessionId }),
                );
            });
            return;
        }

        const timelineResult = await timelinePageResult;
        throwIfAborted(signal);
        if (!isSessionLoadCurrent(sessionId)) {
            return;
        }
        applyTimelineRoundPageResult(sessionId, timelineResult, renderPlan);
    } catch (e) {
        if (e?.name === 'AbortError') {
            throw e;
        }
        logError(
            'frontend.rounds.load_failed',
            'Failed loading rounds',
            errorToPayload(e, { session_id: sessionId }),
        );
    }
}

function fetchTimelineRoundPageResult(sessionId, { priority = '', signal = null } = {}) {
    return fetchTimelineRoundsPage(sessionId, { priority, signal })
        .then(value => ({ status: 'fulfilled', value }))
        .catch(reason => ({ status: 'rejected', reason }));
}

async function applyTimelineRoundPageInBackground(sessionId, { priority, renderPlan, signal }) {
    const timelineResult = await fetchTimelineRoundPageResult(sessionId, { priority, signal });
    throwIfAborted(signal);
    if (!isSessionLoadCurrent(sessionId)) {
        return;
    }
    applyTimelineRoundPageResult(sessionId, timelineResult, renderPlan);
}

async function applyFullRoundPageInBackground(sessionId, { options, renderPlan, signal }) {
    const page = await fetchInitialRoundsPage(sessionId, { priority: '', signal });
    throwIfAborted(signal);
    if (!isSessionLoadCurrent(sessionId)) {
        return;
    }
    applyRoundPage(page, {
        prepend: false,
        mergeExisting: renderPlan.preserveLoadedRounds,
    });
    reconcileTerminalRoundStreamState(page);
    mergeLiveRoundsForSession(sessionId);
    applyTerminalRoundOverlays();
    syncExportedState();
    renderLoadedRoundPage(sessionId, options, renderPlan);
}

function applyTimelineRoundPageResult(sessionId, timelineResult, renderPlan) {
    if (timelineResult.status === 'fulfilled') {
        applyTimelineRoundPage(timelineResult.value);
        reconcileTerminalRoundStreamState(timelineResult.value);
        mergeLiveRoundsForSession(sessionId);
        applyTerminalRoundOverlays();
        syncExportedState();
        if (!shouldPreserveSubagentView(sessionId)) {
            renderNavigatorForTimeline({ layoutReason: renderPlan.navigatorLayoutReason });
        }
        return;
    }
    logError(
        'frontend.rounds.timeline_load_failed',
        'Failed loading timeline rounds',
        errorToPayload(timelineResult.reason, { session_id: sessionId }),
    );
}

function renderLoadedRoundPage(sessionId, options, renderPlan) {
    if (options.render !== false && !shouldPreserveSubagentView(sessionId)) {
        renderSessionTimeline(roundsState.currentRounds, {
            scrollPlan: renderPlan,
            navigatorLayoutReason: renderPlan.navigatorLayoutReason,
        });
        return;
    }
    if (!shouldPreserveSubagentView(sessionId)) {
        renderNavigatorForTimeline({ layoutReason: renderPlan.navigatorLayoutReason });
    }
}

function abortRoundTokenUsageRequests() {
    if (!roundTokenUsageController) {
        roundTokenUsageController = new AbortController();
        return;
    }
    roundTokenUsageController.abort();
    roundTokenUsageController = new AbortController();
}

function reconcileTerminalRoundStreamState(page) {
    const rawItems = Array.isArray(page?.items) ? page.items : [];
    rawItems.forEach(round => {
        const runId = String(round?.run_id || '').trim();
        if (!runId || !isTerminalRoundStatus(round?.run_status)) {
            return;
        }
        reconcileTerminalRunStreamState(runId);
    });
}

export async function refreshTerminalRoundFromHistory(sessionId, runId, options = {}) {
    const safeSessionId = String(sessionId || '').trim();
    const safeRunId = String(runId || '').trim();
    if (!safeSessionId || !safeRunId) {
        return null;
    }
    const expectedToolCallIds = normalizeExpectedToolCallIds(options.expectedToolCallIds);
    if (expectedToolCallIds.size === 0) {
        collectOverlayToolCallIds(safeRunId).forEach(toolCallId => {
            expectedToolCallIds.add(toolCallId);
        });
    }
    const refreshKey = `${safeSessionId}:${safeRunId}`;
    const existing = terminalRoundRefreshes.get(refreshKey);
    if (existing) {
        expectedToolCallIds.forEach(toolCallId => {
            existing.expectedToolCallIds.add(toolCallId);
        });
        return existing.promise;
    }
    const refreshEntry = {
        expectedToolCallIds,
        promise: null,
    };
    const refreshPromise = runTerminalRoundHistoryRefresh(safeSessionId, safeRunId, {
        ...options,
        expectedToolCallIds,
    })
        .finally(() => {
            terminalRoundRefreshes.delete(refreshKey);
        });
    refreshEntry.promise = refreshPromise;
    terminalRoundRefreshes.set(refreshKey, refreshEntry);
    return refreshPromise;
}

async function runTerminalRoundHistoryRefresh(sessionId, runId, options = {}) {
    const expectedToolCallIds = options.expectedToolCallIds instanceof Set
        ? options.expectedToolCallIds
        : normalizeExpectedToolCallIds(options.expectedToolCallIds);
    const refreshFollowups = Number.isFinite(Number(options.refreshFollowups))
        ? Math.max(0, Math.floor(Number(options.refreshFollowups)))
        : 0;
    let fetchedRound = null;
    let hasCompleteRound = false;
    for (let attempt = 0; attempt < TERMINAL_ROUND_REFRESH_DELAYS_MS.length; attempt += 1) {
        const delayMs = TERMINAL_ROUND_REFRESH_DELAYS_MS[attempt];
        if (delayMs > 0) {
            await delay(delayMs);
        }
        try {
            fetchedRound = await fetchSessionRound(sessionId, runId, {
                signal: options.signal,
            });
        } catch (error) {
            if (error?.name === 'AbortError' || options.signal?.aborted) {
                return null;
            }
            if (!isTerminalRoundRefreshRetryableError(error)) {
                throw error;
            }
            continue;
        }
        if (isRoundHistoryComplete(fetchedRound, expectedToolCallIds)) {
            hasCompleteRound = true;
            break;
        }
    }
    if (!fetchedRound || String(fetchedRound.run_id || '').trim() !== runId) {
        scheduleTerminalRoundHistoryFollowup(
            sessionId,
            runId,
            options,
            expectedToolCallIds,
            refreshFollowups,
        );
        return null;
    }
    if (!hasCompleteRound) {
        scheduleTerminalRoundHistoryFollowup(
            sessionId,
            runId,
            options,
            expectedToolCallIds,
            refreshFollowups,
        );
        return null;
    }
    applyTerminalHistoryRound(sessionId, runId, fetchedRound, options);
    return fetchedRound;
}

function scheduleTerminalRoundHistoryFollowup(
    sessionId,
    runId,
    options,
    expectedToolCallIds,
    refreshFollowups,
) {
    if (
        refreshFollowups >= TERMINAL_ROUND_REFRESH_MAX_FOLLOWUPS
        || options.signal?.aborted
        || !isSessionLoadCurrent(sessionId)
    ) {
        return;
    }
    globalThis.setTimeout?.(() => {
        void refreshTerminalRoundFromHistory(sessionId, runId, {
            ...options,
            expectedToolCallIds: Array.from(expectedToolCallIds),
            refreshFollowups: refreshFollowups + 1,
        });
    }, TERMINAL_ROUND_REFRESH_FOLLOWUP_DELAY_MS);
}

function isTerminalRoundRefreshRetryableError(error) {
    const status = Number(error?.status || error?.response?.status || 0);
    return status === 404 || status === 408 || status === 409 || status === 425
        || status === 429 || status >= 500;
}

function applyTerminalHistoryRound(sessionId, runId, round, options = {}) {
    if (!isSessionLoadCurrent(sessionId)) {
        return;
    }
    clearRunStreamState(runId);
    forgetLiveRoundForSession(sessionId, runId);
    const persistedRound = {
        ...round,
        __liveOnly: false,
    };
    terminalRoundOverlays.delete(runId);
    roundsState.currentRounds = upsertRound(
        roundsState.currentRounds,
        runId,
        () => persistedRound,
        () => persistedRound,
    );
    roundsState.timelineRounds = upsertRound(
        roundsState.timelineRounds,
        runId,
        () => persistedRound,
        () => persistedRound,
    );
    if (roundsState.currentRound?.run_id === runId) {
        roundsState.currentRound = persistedRound;
    }
    syncExportedState();
    if (options.render === false) {
        return;
    }
    if (!shouldPreserveSubagentView(sessionId)) {
        renderSessionTimeline(roundsState.currentRounds, {
            scrollPolicy: options.scrollPolicy || 'completion-auto',
            navigatorLayoutReason: options.navigatorLayoutReason || 'terminal-history',
        });
        return;
    }
    renderNavigatorForTimeline({
        layoutReason: options.navigatorLayoutReason || 'terminal-history',
    });
}

function collectOverlayToolCallIds(runId) {
    const overlay = getCoordinatorStreamOverlay(runId);
    const ids = new Set();
    if (!overlay || typeof overlay !== 'object') {
        return ids;
    }
    const parts = Array.isArray(overlay.parts) ? overlay.parts : [];
    parts.forEach(part => {
        if (String(part?.kind || '').trim() !== 'tool') {
            return;
        }
        const toolCallId = String(part.tool_call_id || part.toolCallId || part.id || '').trim();
        if (toolCallId) {
            ids.add(toolCallId);
        }
    });
    return ids;
}

function normalizeExpectedToolCallIds(value) {
    const ids = new Set();
    const rawValues = value instanceof Set
        ? Array.from(value)
        : Array.isArray(value)
            ? value
            : [];
    rawValues.forEach(item => {
        const toolCallId = String(item || '').trim();
        if (toolCallId) {
            ids.add(toolCallId);
        }
    });
    return ids;
}

function isRoundHistoryComplete(round, expectedToolCallIds) {
    if (!round || typeof round !== 'object') {
        return false;
    }
    if (!isTerminalRoundStatus(round.run_status)) {
        return false;
    }
    if (!expectedToolCallIds || expectedToolCallIds.size === 0) {
        return true;
    }
    const actualToolCallIds = collectRoundToolCallIds(round);
    for (const expectedId of expectedToolCallIds) {
        if (!actualToolCallIds.has(expectedId)) {
            return false;
        }
    }
    return true;
}

function collectRoundToolCallIds(round) {
    const ids = new Set();
    const messages = Array.isArray(round?.coordinator_messages)
        ? round.coordinator_messages
        : [];
    messages.forEach(message => {
        const parts = Array.isArray(message?.parts)
            ? message.parts
            : Array.isArray(message?.message?.parts)
                ? message.message.parts
                : [];
        parts.forEach(part => {
            const kind = String(part?.kind || part?.part_kind || part?.type || '').trim();
            if (kind !== 'tool-call') {
                return;
            }
            const toolCallId = String(part.tool_call_id || part.toolCallId || part.id || '').trim();
            if (toolCallId) {
                ids.add(toolCallId);
            }
        });
    });
    return ids;
}

function forgetLiveRoundForSession(sessionId, runId) {
    const safeSessionId = String(sessionId || '').trim();
    const safeRunId = String(runId || '').trim();
    if (!safeSessionId || !safeRunId) {
        return;
    }
    const sessionRounds = liveRoundsBySession.get(safeSessionId);
    if (!sessionRounds) {
        return;
    }
    sessionRounds.delete(safeRunId);
    if (sessionRounds.size === 0) {
        liveRoundsBySession.delete(safeSessionId);
    }
}

function delay(ms) {
    return new Promise(resolve => {
        setTimeout(resolve, Math.max(0, ms));
    });
}

function isTerminalRoundStatus(status) {
    return [
        'completed',
        'failed',
        'stopped',
        'cancelled',
        'canceled',
        'terminal',
    ].includes(String(status || '').trim().toLowerCase());
}

function throwIfAborted(signal) {
    if (signal?.aborted) {
        throw new DOMException('The operation was aborted.', 'AbortError');
    }
}

function isSessionLoadCurrent(sessionId) {
    const requestedSessionId = String(sessionId || '').trim();
    const currentSessionId = String(state.currentSessionId || '').trim();
    if (!requestedSessionId) {
        return false;
    }
    if (state.pendingNewSessionActive === true || state.currentMainView === 'new-session-draft') {
        return false;
    }
    return currentSessionId === requestedSessionId;
}

export function createLiveRound(runId, intentText, intentParts = null) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) return;
    clearPendingRunStartPlaceholder();
    const normalizedIntent = normalizeRoundIntentText(intentText);
    const normalizedIntentParts = normalizeRoundIntentParts(intentParts);
    const createdAt = new Date().toISOString();
    const buildLiveRound = () => ({
        run_id: safeRunId,
        created_at: createdAt,
        intent: normalizedIntent,
        intent_parts: normalizedIntentParts,
        primary_role_id: getRunPrimaryRoleId(safeRunId) || null,
        coordinator_messages: [],
        injection_messages: [],
        instance_role_map: {},
        role_instance_map: {},
        run_status: 'running',
        run_phase: 'running',
        is_recoverable: true,
        pending_tool_approval_count: 0,
        has_user_messages: true,
        __liveOnly: true,
    });
    const updateLiveRound = round => ({
        ...round,
        intent: normalizedIntent || round.intent,
        intent_parts: normalizedIntentParts || round.intent_parts || null,
        primary_role_id: round.primary_role_id || getRunPrimaryRoleId(safeRunId) || null,
        run_status: round.run_status || 'running',
        run_phase: round.run_phase || 'running',
        is_recoverable: round.is_recoverable !== false,
        has_user_messages: true,
    });
    roundsState.currentRounds = upsertRound(
        roundsState.currentRounds,
        safeRunId,
        buildLiveRound,
        updateLiveRound,
    );
    roundsState.timelineRounds = upsertRound(
        roundsState.timelineRounds,
        safeRunId,
        buildLiveRound,
        updateLiveRound,
    );
    rememberLiveRoundForSession(state.currentSessionId, (
        roundsState.currentRounds.find(round => round.run_id === safeRunId)
        || buildLiveRound()
    ));
    syncExportedState();
    if (!shouldPreserveSubagentView(state.currentSessionId)) {
        renderSessionTimeline(roundsState.currentRounds, {
            scrollPolicy: 'follow-latest',
            navigatorLayoutReason: 'new-latest',
        });
    } else {
        renderNavigatorForTimeline({ layoutReason: 'new-latest' });
    }

    const section = document.getElementById(roundSectionId(safeRunId));
    if (section) {
        lockProgrammaticRoundScroll(650);
        void scrollRoundIntoViewWithContext(section);
    }
}

export function showPendingRunStartPlaceholder(sessionId, intentText, intentParts = null, options = {}) {
    const safeSessionId = String(sessionId || '').trim();
    const container = els.chatMessages;
    if (!container) {
        return;
    }
    const allowDraft = options?.allowDraft === true
        && !!container.querySelector?.('.new-session-draft-page');
    if (
        !allowDraft
        && (
            !safeSessionId
            || safeSessionId !== String(state.currentSessionId || '').trim()
        )
    ) {
        return;
    }
    renderRunStartPlaceholder(container, intentText, intentParts, {
        sessionId: safeSessionId,
    });
}

export function showDraftRunStartPlaceholder(intentText, intentParts = null) {
    const container = els.chatMessages;
    if (!container) {
        return;
    }
    renderRunStartPlaceholder(container, intentText, intentParts, {
        sessionId: String(state.currentSessionId || '').trim(),
    });
}

function renderRunStartPlaceholder(container, intentText, intentParts = null, options = {}) {
    const normalizedIntent = normalizeRoundIntentText(intentText)
        || buildRoundIntentPreviewText(intentParts)
        || '';
    const safeSessionId = String(options.sessionId || '').trim();
    let placeholder = container.querySelector?.('.session-run-start-placeholder') || null;
    if (!placeholder) {
        placeholder = document.createElement('section');
        placeholder.className = 'session-run-start-placeholder';
        placeholder.setAttribute('aria-live', 'polite');
        placeholder.innerHTML = `
            <div class="session-run-start-spinner" aria-hidden="true"></div>
            <div class="session-run-start-body">
                <div class="session-run-start-title">${esc(t('session.starting'))}</div>
                <div class="session-run-start-intent"></div>
            </div>
        `;
        container.appendChild(placeholder);
    }
    if (safeSessionId) {
        placeholder.setAttribute('data-session-id', safeSessionId);
    } else {
        placeholder.removeAttribute('data-session-id');
    }
    const intentEl = placeholder.querySelector?.('.session-run-start-intent') || null;
    if (intentEl) {
        intentEl.textContent = normalizedIntent;
        intentEl.hidden = !normalizedIntent;
    }
    container.scrollTop = container.scrollHeight;
}

export function clearPendingRunStartPlaceholder(sessionId = '') {
    const safeSessionId = String(sessionId || '').trim();
    const placeholders = els.chatMessages?.querySelectorAll?.('.session-run-start-placeholder') || [];
    Array.from(placeholders).forEach(placeholder => {
        const placeholderSessionId = String(placeholder?.getAttribute?.('data-session-id') || '').trim();
        if (!safeSessionId || !placeholderSessionId || placeholderSessionId === safeSessionId) {
            placeholder.remove?.();
        }
    });
}

function shouldPreserveSubagentView(sessionId) {
    const active = state.activeSubagentSession;
    const safeSessionId = String(sessionId || state.currentSessionId || '').trim();
    return !!(
        active
        && typeof active === 'object'
        && String(active.sessionId || '').trim() === safeSessionId
    );
}

function roundsForNavigator() {
    if (roundsState.timelineRounds.length === 0) {
        return roundsState.currentRounds;
    }
    const byRunId = new Map();
    roundsState.timelineRounds.forEach(round => {
        const runId = String(round?.run_id || '').trim();
        if (runId) byRunId.set(runId, round);
    });
    roundsState.currentRounds.forEach(round => {
        const runId = String(round?.run_id || '').trim();
        if (!runId) return;
        byRunId.set(runId, {
            ...(byRunId.get(runId) || {}),
            ...round,
        });
    });
    return sortRoundsAscending(Array.from(byRunId.values()));
}

function renderNavigatorForTimeline(options = {}) {
    renderRoundNavigator(roundsForNavigator(), selectRound, {
        activeRunId: roundsState.activeRunId,
        layoutReason: options.layoutReason || 'structure',
    });
}

function captureRoundRenderPlan(options = {}) {
    const explicitPolicy = String(options.scrollPolicy || '').trim();
    const fallbackPolicy = options.preserveScroll === true
        ? 'preserve-anchor'
        : 'session-load';
    const policy = explicitPolicy || fallbackPolicy;
    const container = els.chatMessages;
    const hasRenderedRounds = !!container?.querySelector?.('.session-round-section');

    if (!container || !hasRenderedRounds) {
        return {
            policy: 'follow-latest',
            anchor: null,
            navigatorLayoutReason: 'new-latest',
            preserveLoadedRounds: false,
        };
    }

    if (policy === 'completion-auto') {
        const latestRound = roundsState.currentRounds[roundsState.currentRounds.length - 1] || null;
        const latestRunId = String(latestRound?.run_id || state.activeRunId || '').trim();
        const shouldFollow = shouldFollowLatestRoundAfterCompletion(container, latestRunId);
        return {
            policy: shouldFollow ? 'follow-latest' : 'preserve-anchor',
            anchor: shouldFollow ? null : captureChatScrollAnchor(container, getVisibleRoundSections),
            navigatorLayoutReason: shouldFollow ? 'new-latest' : 'sync-visible-active',
            preserveLoadedRounds: !shouldFollow,
        };
    }

    if (policy === 'follow-latest' || policy === 'session-load') {
        return {
            policy: 'follow-latest',
            anchor: null,
            navigatorLayoutReason: options.navigatorLayoutReason || 'new-latest',
            preserveLoadedRounds: false,
        };
    }

    return {
        policy: 'preserve-anchor',
        anchor: captureChatScrollAnchor(container, getVisibleRoundSections),
        navigatorLayoutReason: options.navigatorLayoutReason || 'structure',
        preserveLoadedRounds: true,
    };
}

function upsertRound(rounds, runId, createRound, updateRound) {
    const existingIndex = rounds.findIndex(round => round.run_id === runId);
    if (existingIndex === -1) {
        const created = createRound();
        return sortRoundsAscending([...rounds, created]);
    }
    return rounds.map(round => (
        round.run_id === runId ? updateRound(round) : round
    ));
}

function patchRoundCollection(rounds, runId, patcher) {
    return rounds.map(round => (
        round.run_id === runId ? patcher(round) : round
    ));
}

export function appendRoundUserMessage(runId, promptPayload) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) return;
    const normalizedIntentParts = normalizeRoundIntentParts(promptPayload);
    const normalizedIntentText = buildRoundIntentPreviewText(promptPayload);
    const roundIndex = roundsState.currentRounds.findIndex(round => round.run_id === safeRunId);
    const patchIntent = round => ({
        ...round,
        has_user_messages: true,
        intent: normalizedIntentText || round.intent,
        intent_parts: normalizedIntentParts || round.intent_parts || null,
    });
    roundsState.timelineRounds = patchRoundCollection(
        roundsState.timelineRounds,
        safeRunId,
        patchIntent,
    );
    if (roundIndex >= 0) {
        roundsState.currentRounds = patchRoundCollection(
            roundsState.currentRounds,
            safeRunId,
            patchIntent,
        );
        if (roundsState.currentRound?.run_id === safeRunId) {
            roundsState.currentRound = roundsState.currentRounds.find(
                round => round.run_id === safeRunId,
            ) || roundsState.currentRound;
        }
        syncExportedState();
        rememberLiveRoundForSession(state.currentSessionId, (
            roundsState.currentRounds.find(round => round.run_id === safeRunId)
            || null
        ));
        if (!shouldPreserveSubagentView(state.currentSessionId)) {
            renderSessionTimeline(roundsState.currentRounds, {
                scrollPolicy: 'follow-latest',
                navigatorLayoutReason: 'new-latest',
            });
        } else {
            patchRoundHeader(roundsState.currentRounds[roundIndex], roundIndex);
            renderNavigatorForTimeline({ layoutReason: 'new-latest' });
        }
    } else {
        renderNavigatorForTimeline({ layoutReason: 'new-latest' });
    }
}

export function upsertRoundInjectionMessage(runId, injectionMessage, options = {}) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId || !injectionMessage || typeof injectionMessage !== 'object') return;
    const normalized = normalizeInjectionMessage(safeRunId, injectionMessage);
    const patchInjection = round => ({
        ...round,
        injection_messages: upsertInjectionMessages(round.injection_messages || [], normalized),
        has_user_messages: true,
    });
    const buildRound = () => ({
        run_id: safeRunId,
        created_at: normalized.applied_at || normalized.occurred_at || normalized.queued_at || new Date().toISOString(),
        intent: '',
        intent_parts: null,
        primary_role_id: getRunPrimaryRoleId(safeRunId) || null,
        coordinator_messages: [],
        injection_messages: [normalized],
        instance_role_map: {},
        role_instance_map: {},
        run_status: 'running',
        run_phase: 'running',
        is_recoverable: true,
        pending_tool_approval_count: 0,
        has_user_messages: true,
        __liveOnly: true,
    });
    roundsState.currentRounds = upsertRound(
        roundsState.currentRounds,
        safeRunId,
        buildRound,
        patchInjection,
    );
    roundsState.timelineRounds = upsertRound(
        roundsState.timelineRounds,
        safeRunId,
        buildRound,
        patchInjection,
    );
    if (roundsState.currentRound?.run_id === safeRunId) {
        roundsState.currentRound = roundsState.currentRounds.find(
            round => round.run_id === safeRunId,
        ) || roundsState.currentRound;
    }
    syncExportedState();
    rememberLiveRoundForSession(state.currentSessionId, (
        roundsState.currentRounds.find(round => round.run_id === safeRunId)
        || null
    ));
    if (options.render !== false && !shouldPreserveSubagentView(state.currentSessionId)) {
        renderSessionTimeline(roundsState.currentRounds, {
            scrollPolicy: options.scrollPolicy || 'preserve-anchor',
            navigatorLayoutReason: 'structure',
        });
    } else {
        renderNavigatorForTimeline({ layoutReason: 'structure' });
    }
}

function upsertInjectionMessages(currentMessages, nextMessage) {
    const messages = Array.isArray(currentMessages) ? currentMessages : [];
    const existingIndex = findInjectionMessageIndex(messages, nextMessage);
    if (existingIndex === -1) {
        return [...messages, nextMessage].sort(compareInjectionMessages);
    }
    return messages.map((item, index) => (
        index === existingIndex
            ? mergeInjectionMessage(item, nextMessage)
            : item
    )).sort(compareInjectionMessages);
}

function findInjectionMessageIndex(messages, nextMessage) {
    const nextId = String(nextMessage?.message_id || '').trim();
    const nextInjectionId = String(nextMessage?.injection_id || '').trim();
    const nextSortAt = injectionSortAt(nextMessage);
    const injectionIndex = messages.findIndex(item => {
        const itemInjectionId = String(item?.injection_id || '').trim();
        return itemInjectionId && nextInjectionId && itemInjectionId === nextInjectionId;
    });
    if (injectionIndex !== -1) {
        return injectionIndex;
    }
    const directIndex = messages.findIndex(item => {
        const itemId = String(item?.message_id || '').trim();
        return itemId && nextId && itemId === nextId;
    });
    if (directIndex !== -1) {
        return directIndex;
    }
    for (let index = messages.length - 1; index >= 0; index -= 1) {
        const item = messages[index];
        const itemRecipient = String(item?.recipient_instance_id || '');
        const nextRecipient = String(nextMessage?.recipient_instance_id || '');
        const itemSortAt = injectionSortAt(item);
        if (
            String(item?.content || '').trim() === String(nextMessage?.content || '').trim()
            && String(item?.source || 'user') === String(nextMessage?.source || 'user')
            && String(item?.mode || 'queued') === String(nextMessage?.mode || 'queued')
            && (!itemRecipient || !nextRecipient || itemRecipient === nextRecipient)
            && (!itemSortAt || !nextSortAt || itemSortAt === nextSortAt)
        ) {
            return index;
        }
    }
    return -1;
}

function mergeInjectionMessage(current, next) {
    return {
        ...current,
        ...next,
        message_id: preferredInjectionMessageId(current, next),
        injection_id: next.injection_id || current.injection_id,
        content: next.content || current.content,
        content_parts: next.content_parts?.length ? next.content_parts : current.content_parts || [],
        queued_at: current.queued_at || next.queued_at,
        applied_at: next.applied_at || current.applied_at,
        occurred_at: current.occurred_at || next.occurred_at,
    };
}

function preferredInjectionMessageId(current, next) {
    const currentId = String(current?.message_id || '').trim();
    const nextId = String(next?.message_id || '').trim();
    if (currentId.startsWith('local-') && nextId && !nextId.startsWith('local-')) {
        return nextId;
    }
    return currentId || nextId;
}

function compareInjectionMessages(a, b) {
    return injectionSortAt(a).localeCompare(injectionSortAt(b));
}

function normalizeInjectionMessage(runId, rawMessage) {
    const contentParts = normalizePromptContentParts(rawMessage.content_parts || rawMessage.content);
    const content = String(
        rawMessage.content
        || summarizePromptContentParts(contentParts)
        || '',
    ).trim();
    const queuedAt = injectionQueuedAt(rawMessage) || new Date().toISOString();
    const appliedAt = String(rawMessage.applied_at || '');
    const occurredAt = String(rawMessage.occurred_at || appliedAt || queuedAt);
    const recipient = String(rawMessage.recipient_instance_id || '').trim();
    const injectionId = String(rawMessage.injection_id || rawMessage.id || '').trim();
    const messageId = String(rawMessage.message_id || [
        injectionId,
        recipient,
        appliedAt || occurredAt || queuedAt,
        rawMessage.source || 'user',
        content,
    ].join('|')).trim();
    return {
        message_id: messageId,
        injection_id: injectionId || messageId,
        run_id: runId,
        source: String(rawMessage.source || 'user'),
        mode: String(rawMessage.mode || rawMessage.delivery_mode || 'queued'),
        status: String(rawMessage.status || 'queued'),
        content,
        content_parts: contentParts || [],
        recipient_instance_id: recipient,
        queued_at: queuedAt,
        applied_at: appliedAt,
        occurred_at: occurredAt,
        interrupted_current_step: rawMessage.interrupted_current_step === true,
    };
}

function injectionSortAt(message) {
    return String(
        message?.applied_at
        || message?.occurred_at
        || message?.queued_at
        || message?.created_at
        || '',
    );
}

function injectionQueuedAt(message) {
    return String(
        message?.queued_at
        || message?.created_at
        || message?.occurred_at
        || '',
    );
}

function rememberLiveRoundForSession(sessionId, round) {
    const safeSessionId = String(sessionId || '').trim();
    const safeRunId = String(round?.run_id || '').trim();
    if (!safeSessionId || !safeRunId || !round) {
        return;
    }
    let sessionRounds = liveRoundsBySession.get(safeSessionId);
    if (!sessionRounds) {
        sessionRounds = new Map();
        liveRoundsBySession.set(safeSessionId, sessionRounds);
    }
    sessionRounds.set(safeRunId, {
        ...round,
        is_recoverable: round.is_recoverable !== false,
    });
}

function mergeLiveRoundsForSession(sessionId) {
    const safeSessionId = String(sessionId || '').trim();
    const sessionRounds = liveRoundsBySession.get(safeSessionId);
    if (!safeSessionId || !sessionRounds || sessionRounds.size === 0) {
        return;
    }
    sessionRounds.forEach((liveRound, runId) => {
        if (findPersistedRoundSnapshot(runId)) {
            sessionRounds.delete(runId);
            return;
        }
        const buildRound = () => liveRound;
        const mergeRound = round => ({
            ...liveRound,
            ...round,
            intent: round.intent || liveRound.intent,
            intent_parts: round.intent_parts || liveRound.intent_parts || null,
            coordinator_messages: Array.isArray(round.coordinator_messages)
                ? round.coordinator_messages
                : liveRound.coordinator_messages || [],
            injection_messages: Array.isArray(round.injection_messages)
                ? round.injection_messages
                : liveRound.injection_messages || [],
        });
        roundsState.currentRounds = upsertRound(
            roundsState.currentRounds,
            runId,
            buildRound,
            mergeRound,
        );
        roundsState.timelineRounds = upsertRound(
            roundsState.timelineRounds,
            runId,
            buildRound,
            mergeRound,
        );
    });
    if (sessionRounds.size === 0) {
        liveRoundsBySession.delete(safeSessionId);
    }
}

function hasPersistedRoundSnapshot(round) {
    return !!round && round.__liveOnly !== true;
}

function findPersistedRoundSnapshot(runId) {
    return (
        roundsState.currentRounds.find(round => (
            round.run_id === runId && hasPersistedRoundSnapshot(round)
        ))
        || roundsState.timelineRounds.find(round => (
            round.run_id === runId && hasPersistedRoundSnapshot(round)
        ))
        || null
    );
}

function normalizeRoundIntentParts(promptPayload) {
    return normalizePromptContentParts(promptPayload);
}

function buildRoundIntentPreviewText(promptPayload) {
    const normalizedIntentParts = normalizeRoundIntentParts(promptPayload);
    if (normalizedIntentParts && normalizedIntentParts.length > 0) {
        const summary = summarizePromptContentParts(normalizedIntentParts);
        if (summary) {
            return summary;
        }
    }
    return normalizeRoundIntentText(promptPayload);
}

export function appendRoundRetryEvent(runId, retryEvent) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId || !retryEvent || typeof retryEvent !== 'object') return;

    const patchRetry = round => ({
        ...round,
        retry_events: [retryEvent],
    });
    roundsState.currentRounds = roundsState.currentRounds.map(round => {
        if (round.run_id !== safeRunId) {
            return round;
        }
        return patchRetry(round);
    });
    roundsState.timelineRounds = patchRoundCollection(roundsState.timelineRounds, safeRunId, patchRetry);
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

    const patchRetry = round => {
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
    };
    roundsState.currentRounds = roundsState.currentRounds.map(patchRetry);
    roundsState.timelineRounds = roundsState.timelineRounds.map(patchRetry);
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

    const patchRetry = round => {
        if (round.run_id !== safeRunId) {
            return round;
        }
        const existing = Array.isArray(round.retry_events) ? round.retry_events : [];
        return {
            ...round,
            retry_events: existing.filter(event => event?.event_id !== safeRetryEventId),
        };
    };
    roundsState.currentRounds = roundsState.currentRounds.map(patchRetry);
    roundsState.timelineRounds = roundsState.timelineRounds.map(patchRetry);
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

    const overlayPatch = pickDefinedRoundOverlay(overlay);
    syncTerminalRoundOverlay(safeRunId, overlayPatch);
    const roundIndex = roundsState.currentRounds.findIndex(round => round.run_id === safeRunId);
    const current = roundsState.currentRounds[roundIndex]
        || roundsState.timelineRounds.find(round => round.run_id === safeRunId);
    if (!current) {
        if (isTerminalRoundOverlay(overlayPatch)) {
            reconcileTerminalRunStreamState(safeRunId);
        }
        return;
    }

    const nextRound = { ...current, ...overlayPatch };
    roundsState.currentRounds = roundsState.currentRounds.map(round =>
        round.run_id === safeRunId ? { ...round, ...overlayPatch } : round,
    );
    roundsState.timelineRounds = patchRoundCollection(
        roundsState.timelineRounds,
        safeRunId,
        round => ({ ...round, ...overlayPatch }),
    );
    if (roundsState.currentRound?.run_id === safeRunId) {
        roundsState.currentRound = roundsState.currentRounds.find(
            round => round.run_id === safeRunId,
        ) || nextRound;
    }
    syncExportedState();
    if (roundIndex >= 0) {
        patchRoundHeader(nextRound, roundIndex);
    }
    if (isTerminalRoundOverlay(overlayPatch)) {
        reconcileTerminalRunStreamState(safeRunId);
    }
    syncRetryTimelineTimer();
    renderNavigatorForTimeline();
    setActiveRoundNav(roundsState.activeRunId);

    if (roundsState.currentRound?.run_id === safeRunId) {
        const pendingApprovals = Array.isArray(nextRound.pending_tool_approvals)
            ? nextRound.pending_tool_approvals
            : [];
        setRoundPendingApprovals(safeRunId, pendingApprovals);
    }
}

function syncTerminalRoundOverlay(runId, overlayPatch) {
    if (typeof overlayPatch !== 'object') {
        return;
    }
    if (isTerminalRoundOverlay(overlayPatch)) {
        terminalRoundOverlays.set(runId, {
            ...terminalRoundOverlays.get(runId),
            ...overlayPatch,
        });
        return;
    }
    if (String(overlayPatch.run_status || '').trim()) {
        terminalRoundOverlays.delete(runId);
    }
}

function isTerminalRoundOverlay(overlayPatch) {
    if (!overlayPatch || typeof overlayPatch !== 'object') {
        return false;
    }
    return (
        isTerminalRoundStatus(overlayPatch.run_status)
        || overlayPatch.is_recoverable === false
    );
}

function applyTerminalRoundOverlays() {
    if (terminalRoundOverlays.size === 0) {
        return;
    }
    terminalRoundOverlays.forEach((overlayPatch, runId) => {
        const safeRunId = String(runId || '').trim();
        if (!safeRunId) {
            return;
        }
        let patched = false;
        const patchCurrentRound = round => {
            patched = true;
            return { ...round, ...overlayPatch };
        };
        roundsState.currentRounds = patchRoundCollection(
            roundsState.currentRounds,
            safeRunId,
            patchCurrentRound,
        );
        roundsState.timelineRounds = patchRoundCollection(
            roundsState.timelineRounds,
            safeRunId,
            round => ({ ...round, ...overlayPatch }),
        );
        if (roundsState.currentRound?.run_id === safeRunId) {
            roundsState.currentRound = {
                ...roundsState.currentRound,
                ...overlayPatch,
            };
        }
        if (patched) {
            reconcileTerminalRunStreamState(safeRunId);
        }
    });
}

export function updateRoundTodo(runId, todoSnapshot) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) {
        return;
    }
    const normalizedTodo = normalizeRoundTodoSnapshot(todoSnapshot, safeRunId, state.currentSessionId);
    let changed = false;
    const patchTodo = round => {
        const previousTodo = normalizeRoundTodoSnapshot(round.todo, safeRunId, state.currentSessionId);
        if (areRoundTodoSnapshotsEqual(previousTodo, normalizedTodo)) {
            return round;
        }
        changed = true;
        if (normalizedTodo === null) {
            const { todo: _todo, ...rest } = round;
            return rest;
        }
        return {
            ...round,
            todo: normalizedTodo,
        };
    };
    roundsState.currentRounds = patchRoundCollection(
        roundsState.currentRounds,
        safeRunId,
        patchTodo,
    );
    roundsState.timelineRounds = patchRoundCollection(
        roundsState.timelineRounds,
        safeRunId,
        patchTodo,
    );
    if (!changed) {
        syncRoundTodoVisibility(safeRunId);
        return;
    }
    if (roundsState.currentRound?.run_id === safeRunId) {
        roundsState.currentRound = roundsState.currentRounds.find(
            round => round.run_id === safeRunId,
        ) || null;
    }
    syncExportedState();
    syncRoundTodoVisibility(safeRunId);
}

export function syncRoundTodoVisibility(runId = '') {
    const safeRunId = String(runId || '').trim();
    if (safeRunId) {
        const round = roundsState.timelineRounds.find(item => item.run_id === safeRunId)
            || roundsState.currentRounds.find(item => item.run_id === safeRunId)
            || null;
        if (round && patchRoundNavigatorTodo(safeRunId, round.todo || null)) {
            return;
        }
    }
    renderNavigatorForTimeline();
}

export async function selectRound(round) {
    if (!round) return;
    expandHistorySegmentForRun(round.run_id);
    let section = document.getElementById(roundSectionId(round.run_id));
    const needsHistoryLoad = !section;
    let revealedDuringHistoryLoad = false;
    if (!section) {
        revealedDuringHistoryLoad = await loadRoundsUntilRun(round.run_id);
        expandHistorySegmentForRun(round.run_id);
        section = document.getElementById(roundSectionId(round.run_id));
    }
    if (!section) return;
    roundsState.pendingScrollTargetRunId = round.run_id;
    const estimatedScrollMs = estimateScrollRoundIntoViewDuration(section, {
        source: 'timeline',
        loadedHistory: needsHistoryLoad,
    });
    const lockUntil = Date.now() + estimatedScrollMs + 520;
    roundsState.pendingScrollUnlockAt = lockUntil;
    roundsState.activeLockUntil = lockUntil;
    const nextRound = roundsState.currentRounds.find(item => item.run_id === round.run_id) || round;
    applyActiveRoundState(nextRound, estimateRoundVisibleScore(), {
        navigatorLayoutReason: 'sync-visible-active',
        lockActive: true,
        lockMs: estimatedScrollMs + 520,
    });
    if (!revealedDuringHistoryLoad) {
        await scrollRoundIntoViewWithContext(section, {
            source: 'timeline',
            loadedHistory: needsHistoryLoad,
        });
    }
    emphasizeRoundSection(section);
}

async function scrollRoundIntoViewWithContext(section, options = {}) {
    const container = els.chatMessages;
    if (!container || !isElementLike(section)) {
        section?.scrollIntoView?.({ block: 'start' });
        return;
    }
    const { nextTop, durationMs } = resolveRoundScrollTarget(section, container, options);
    await animateChatScrollTo(container, nextTop, {
        durationMs,
        lockMs: durationMs + 520,
        source: options.source || 'round',
    });
}

function estimateScrollRoundIntoViewDuration(section, options = {}) {
    const container = els.chatMessages;
    if (!container || !isElementLike(section)) {
        return ROUND_TIMELINE_SCROLL_ANIMATION_MIN_MS;
    }
    return resolveRoundScrollTarget(section, container, options).durationMs;
}

function resolveRoundScrollTarget(section, container, options = {}) {
    const containerRect = container.getBoundingClientRect();
    const sectionRect = section.getBoundingClientRect();
    const currentTop = Number(container.scrollTop || 0);
    const contextualOffset = Math.max(36, Math.min(160, containerRect.height * 0.28));
    const targetTop = currentTop + sectionRect.top - containerRect.top - contextualOffset;
    const maxTop = Math.max(0, Number(container.scrollHeight || 0) - Number(container.clientHeight || 0));
    const nextTop = Math.max(0, Math.min(maxTop, targetTop));
    return {
        nextTop,
        durationMs: resolveChatScrollDuration(currentTop, nextTop, options),
    };
}

async function animateChatScrollToBottom(options = {}) {
    const container = els.chatMessages;
    if (!container) return;
    const targetTop = Math.max(0, Number(container.scrollHeight || 0) - Number(container.clientHeight || 0));
    await animateChatScrollTo(container, targetTop, {
        durationMs: options.durationMs || resolveChatScrollDuration(container.scrollTop, targetTop, {
            source: 'bottom',
        }),
        lockMs: options.lockMs || 900,
        source: 'bottom',
        syncActiveDuringScroll: options.syncActiveDuringScroll === true,
    });
    container.scrollTop = Math.max(
        0,
        Number(container.scrollHeight || 0) - Number(container.clientHeight || 0),
    );
    activateLatestRound(roundsState.currentRounds, {
        navigatorLayoutReason: 'new-latest',
        lockActive: true,
    });
    syncScrollToBottomControl(container);
}

function animateChatScrollTo(container, targetTop, options = {}) {
    if (!container) return Promise.resolve();
    cancelChatScrollAnimation();
    const fromTop = Number(container.scrollTop || 0);
    const maxTop = Math.max(0, Number(container.scrollHeight || 0) - Number(container.clientHeight || 0));
    const toTop = Math.max(0, Math.min(maxTop, Number(targetTop || 0)));
    const distance = Math.abs(toTop - fromTop);
    if (distance < 1 || typeof window === 'undefined' || typeof window.requestAnimationFrame !== 'function') {
        container.scrollTop = toTop;
        syncScrollToBottomControl(container);
        return Promise.resolve();
    }

    const token = chatScrollAnimationToken + 1;
    chatScrollAnimationToken = token;
    const durationMs = Math.max(
        160,
        Number(options.durationMs || resolveChatScrollDuration(fromTop, toTop, options)),
    );
    const lockMs = Math.max(durationMs + 120, Number(options.lockMs || 0));
    lockProgrammaticRoundScroll(lockMs);
    const startedAt = window.performance?.now?.() || Date.now();

    return new Promise(resolve => {
        const step = nowValue => {
            if (token !== chatScrollAnimationToken) {
                resolve();
                return;
            }
            const now = Number(nowValue || Date.now());
            const progress = Math.min(1, Math.max(0, (now - startedAt) / durationMs));
            const eased = easeInOutCubic(progress);
            container.scrollTop = fromTop + ((toTop - fromTop) * eased);
            syncScrollToBottomControl(container);
            if (options.syncActiveDuringScroll === true) {
                syncActiveRoundFromScroll({ allowProgrammatic: true });
            }
            if (progress >= 1) {
                chatScrollAnimationFrame = 0;
                container.scrollTop = toTop;
                syncScrollToBottomControl(container);
                schedulePostLayoutRoundSync(container);
                resolve();
                return;
            }
            chatScrollAnimationFrame = window.requestAnimationFrame(step);
        };
        chatScrollAnimationFrame = window.requestAnimationFrame(step);
    });
}

function resolveChatScrollDuration(fromTop, toTop, options = {}) {
    const distance = Math.abs(Number(toTop || 0) - Number(fromTop || 0));
    if (options.source === 'timeline') {
        const base = options.loadedHistory
            ? ROUND_TIMELINE_SCROLL_ANIMATION_MIN_MS + 220
            : ROUND_TIMELINE_SCROLL_ANIMATION_MIN_MS;
        const scaled = base + Math.min(1700, distance / 3.6);
        return Math.round(Math.max(
            ROUND_TIMELINE_SCROLL_ANIMATION_MIN_MS,
            Math.min(ROUND_TIMELINE_SCROLL_ANIMATION_MAX_MS, scaled),
        ));
    }
    const scaled = ROUND_SCROLL_ANIMATION_MIN_MS + Math.min(230, distance / 8);
    return Math.round(Math.max(
        ROUND_SCROLL_ANIMATION_MIN_MS,
        Math.min(ROUND_SCROLL_ANIMATION_MAX_MS, scaled),
    ));
}

function easeInOutCubic(value) {
    const progress = Math.max(0, Math.min(1, Number(value || 0)));
    return progress < 0.5
        ? 4 * progress * progress * progress
        : 1 - ((-2 * progress + 2) ** 3) / 2;
}

function cancelChatScrollAnimation() {
    chatScrollAnimationToken += 1;
    if (chatScrollAnimationFrame && typeof window !== 'undefined') {
        window.cancelAnimationFrame?.(chatScrollAnimationFrame);
    }
    chatScrollAnimationFrame = 0;
}

export function goBackToSessions() {
    // Legacy no-op: session list always visible now.
}

function renderSessionTimeline(rounds, opts = { preserveScroll: true }) {
    const container = els.chatMessages;
    if (!container) return;

    const renderPlan = opts.scrollPlan || captureRoundRenderPlan(opts);

    // Hide container during render to prevent flash of content at wrong
    // scroll position before we reposition.
    const shouldHideDuringRender = renderPlan.policy === 'follow-latest';
    if (shouldHideDuringRender) {
        container.style.visibility = 'hidden';
    }

    container.innerHTML = '';

    clearAllStreamState({ preserveOverlay: true });
    roundsState.activeRunId = null;
    roundsState.activeVisibility = 0;
    roundsState.activeLockUntil = 0;

    if (!rounds || rounds.length === 0) {
        roundsState.currentRound = null;
        syncExportedState();
        state.instanceRoleMap = {};
        state.roleInstanceMap = {};
        state.taskInstanceMap = {};
        state.taskStatusMap = {};
        roundsState.activeRunId = null;
        setRoundPendingApprovals('', [], {});
        renderNavigatorForTimeline({ layoutReason: renderPlan.navigatorLayoutReason || 'structure' });
        syncRetryTimelineTimer();
        if (shouldHideDuringRender) {
            container.style.visibility = '';
        }
        ensureScrollToBottomControl(container);
        syncScrollToBottomControl(container);
        return;
    }

    const segments = splitRoundsByHistoryMarkers(rounds);
    const segmentIds = new Set(segments.map(segment => segment.segmentId));
    Array.from(expandedHistorySegments).forEach(segmentId => {
        if (!segmentIds.has(segmentId)) {
            expandedHistorySegments.delete(segmentId);
        }
    });

    const historyLoadMore = renderHistoryLoadMoreControl();
    if (historyLoadMore) {
        container.appendChild(historyLoadMore);
    }

    segments.forEach(segment => {
        const segmentEl = document.createElement('div');
        segmentEl.className = 'round-history-segment';
        segmentEl.dataset.segmentId = segment.segmentId;

        const body = document.createElement('div');
        body.className = 'round-history-segment-body';
        const isExpanded = segment.isLatest || expandedHistorySegments.has(segment.segmentId);
        body.hidden = !isExpanded;
        segmentEl.dataset.expanded = isExpanded ? 'true' : 'false';

        segment.rounds.forEach(item => {
            const section = renderRoundSection(item.round, item.index);
            body.appendChild(section);
        });
        segmentEl.appendChild(body);

        if (segment.clearMarker) {
            segmentEl.appendChild(renderClearDivider(segment, isExpanded));
        }

        container.appendChild(segmentEl);
    });

    renderNavigatorForTimeline({
        layoutReason: opts.navigatorLayoutReason
            || renderPlan.navigatorLayoutReason
            || 'structure',
    });
    bindScrollSync();

    if (renderPlan.policy === 'preserve-anchor') {
        restoreChatScrollAnchor(container, renderPlan.anchor);
        syncActiveRoundFromScroll();
    } else {
        lockProgrammaticRoundScroll();
        container.scrollTop = container.scrollHeight;
        activateLatestRound(rounds, {
            navigatorLayoutReason: opts.navigatorLayoutReason
                || renderPlan.navigatorLayoutReason
                || 'new-latest',
        });
    }

    if (shouldHideDuringRender) {
        container.style.visibility = '';
    }

    schedulePostLayoutRoundSync(container);
    syncHistoryLoadMoreAlignment(container);
    ensureScrollToBottomControl(container);
    syncScrollToBottomControl(container);
    syncLastAnswerCopyButton(container);
    syncRetryTimelineTimer();
}

export function renderCurrentSessionTimeline(opts = {}) {
    if (!Array.isArray(roundsState.currentRounds) || roundsState.currentRounds.length === 0) {
        return false;
    }
    renderSessionTimeline(roundsState.currentRounds, opts);
    return true;
}

function bindScrollSync() {
    const container = els.chatMessages;
    if (!container) return;
    if (!roundsState.scrollBound) {
        container.addEventListener('scroll', syncActiveRoundFromScroll, { passive: true });
        roundsState.scrollBound = true;
    }
    if (
        !historyLoadMoreResizeBound
        && typeof window !== 'undefined'
        && typeof window.addEventListener === 'function'
    ) {
        window.addEventListener('resize', syncHistoryLoadMoreAlignmentFromViewport, { passive: true });
        historyLoadMoreResizeBound = true;
    }
    if (!olderRoundPagingIntentBound) {
        container.addEventListener('wheel', handleOlderRoundWheelIntent, { passive: true });
        container.addEventListener('touchstart', handleOlderRoundTouchStart, { passive: true });
        container.addEventListener('touchmove', handleOlderRoundTouchMove, { passive: true });
        container.addEventListener('keydown', handleOlderRoundKeyIntent);
        olderRoundPagingIntentBound = true;
    }
    ensureScrollToBottomControl(container);
}

function syncHistoryLoadMoreAlignmentFromViewport() {
    syncHistoryLoadMoreAlignment(els.chatMessages);
}

function handleOlderRoundWheelIntent(event) {
    const container = els.chatMessages;
    if (!container || Number(event?.deltaY || 0) >= -2) return;
    if (!isChatScrolledToTop(container)) return;
    requestOlderRoundLoadFromUserIntent('scroll');
}

function handleOlderRoundTouchStart(event) {
    const touch = event?.touches?.[0] || null;
    olderRoundTouchStartY = touch ? Number(touch.clientY || 0) : null;
}

function handleOlderRoundTouchMove(event) {
    const container = els.chatMessages;
    const touch = event?.touches?.[0] || null;
    if (!container || !touch || olderRoundTouchStartY === null) return;
    const deltaY = Number(touch.clientY || 0) - olderRoundTouchStartY;
    if (deltaY <= 18 || !isChatScrolledToTop(container)) return;
    requestOlderRoundLoadFromUserIntent('touch');
}

function handleOlderRoundKeyIntent(event) {
    const key = String(event?.key || '');
    if (key !== 'PageUp' && key !== 'Home') return;
    if (isEditableEventTarget(event?.target)) return;
    const container = els.chatMessages;
    if (!container || !isChatScrolledToTop(container)) return;
    requestOlderRoundLoadFromUserIntent('keyboard');
}

function isEditableEventTarget(target) {
    const tagName = String(target?.tagName || '').toLowerCase();
    return tagName === 'input'
        || tagName === 'textarea'
        || tagName === 'select'
        || target?.isContentEditable === true;
}

function isChatScrolledToTop(container) {
    return Number(container?.scrollTop || 0) <= 2;
}

function requestOlderRoundLoadFromUserIntent(source) {
    if (!canRequestOlderRounds()) return;
    clearOlderRoundLoadIntentTimer();
    olderRoundLoadIntentTimer = window.setTimeout(() => {
        olderRoundLoadIntentTimer = 0;
        void loadOlderRounds({ source });
    }, ROUND_HISTORY_LOAD_INTENT_DEBOUNCE_MS);
}

function canRequestOlderRounds() {
    return roundsState.paging.hasMore === true
        && roundsState.paging.loading !== true
        && !!state.currentSessionId;
}

function clearOlderRoundLoadIntentTimer() {
    if (!olderRoundLoadIntentTimer) return;
    window.clearTimeout(olderRoundLoadIntentTimer);
    olderRoundLoadIntentTimer = 0;
}

function ensureScrollToBottomControl(container = els.chatMessages) {
    if (!container) return null;
    const host = container.closest?.('.chat-container') || container.parentElement || null;
    if (!host) return null;
    if (scrollToBottomControl && scrollToBottomControl.isConnected) {
        syncScrollToBottomControl(container);
        return scrollToBottomControl;
    }
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'round-scroll-bottom-btn';
    button.setAttribute('aria-label', t('rounds.scroll_bottom.label'));
    button.dataset.visible = 'false';
    button.innerHTML = renderScrollToBottomIcon();
    button.addEventListener('click', () => {
        void animateChatScrollToBottom({ durationMs: 520, lockMs: 900, syncActiveDuringScroll: true });
    });
    host.appendChild(button);
    scrollToBottomControl = button;
    syncScrollToBottomControl(container);
    return button;
}

function removeScrollToBottomControl() {
    scrollToBottomControl?.remove?.();
    scrollToBottomControl = null;
}

function renderScrollToBottomIcon() {
    return `
        <svg class="round-scroll-bottom-icon" viewBox="0 0 20 20" focusable="false" aria-hidden="true">
            <path d="M10 4.5v10"></path>
            <path d="M5.75 10.25 10 14.5l4.25-4.25"></path>
        </svg>
    `;
}

function syncScrollToBottomControl(container = els.chatMessages) {
    if (!container || !scrollToBottomControl?.isConnected) return;
    const hasScrollableContent = Number(container.scrollHeight || 0) > Number(container.clientHeight || 0) + 2;
    const visible = hasScrollableContent && !isChatScrollNearBottom(container);
    scrollToBottomControl.dataset.visible = visible ? 'true' : 'false';
    scrollToBottomControl.disabled = !visible;
}

function isChatScrollNearBottom(container) {
    const remaining = Number(container?.scrollHeight || 0)
        - Number(container?.clientHeight || 0)
        - Number(container?.scrollTop || 0);
    return remaining <= ROUND_SCROLL_BOTTOM_THRESHOLD_PX;
}

function syncActiveRoundFromScroll(options = {}) {
    const container = els.chatMessages;
    if (!container) return;
    syncScrollToBottomControl(container);

    if (
        options.allowProgrammatic !== true
        && isProgrammaticRoundScrollLocked()
        && !roundsState.pendingScrollTargetRunId
    ) {
        return;
    }

    const sections = getVisibleRoundSections(container);
    if (sections.length === 0) return;

    if (syncPendingRoundSelection(container)) {
        return;
    }

    const atTop = container.scrollTop <= 2;
    const atBottom = isChatScrollNearBottom(container);
    if (atTop) {
        activateRoundSection(sections[0], estimateRoundVisibleScore());
        return;
    }
    if (atBottom) {
        activateRoundSection(sections[sections.length - 1], estimateRoundVisibleScore());
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

    if (runId !== roundsState.activeRunId && isActiveRoundLocked()) {
        return;
    }
    if (runId === roundsState.activeRunId) {
        roundsState.activeVisibility = visibleScore;
        return;
    }

    const nextRound = roundsState.currentRounds.find(round => round.run_id === runId) || null;
    applyActiveRoundState(nextRound, visibleScore);
}

function renderRoundSection(round, index) {
    const section = document.createElement('section');
    section.className = 'session-round-section';
    section.dataset.runId = round.run_id;
    section.id = roundSectionId(round.run_id);
    if (round.created_at) section.dataset.roundCreatedAt = round.created_at;
    if (round.run_started_at) section.dataset.roundStartedAt = round.run_started_at;
    if (round.run_updated_at) section.dataset.roundUpdatedAt = round.run_updated_at;

    const time = new Date(round.created_at).toLocaleString();
    const stateLabel = roundStateLabel(round);
    const stateTone = roundStateTone(round);
    const approvalCount = Number(round.pending_tool_approval_count || 0);
    const header = document.createElement('div');
    header.className = 'round-detail-header';
    header.innerHTML = `
        <div class="round-detail-topline">
            <div class="round-detail-mainline">
                <div class="round-detail-meta">
                    <div class="round-detail-time">${time}</div>
                    ${round.run_status === 'running' ? '<span class="live-badge">LIVE</span>' : ''}
                    <div class="round-detail-token-host"></div>
                </div>
            </div>
            <div class="round-detail-badges">${renderRoundBadges(round, stateLabel, stateTone, approvalCount)}</div>
        </div>`;
    header.appendChild(buildRoundIntentBlock(round.run_id, round.intent, round.intent_parts));
    section.appendChild(header);
    renderRoundRetryEvents(section, round.retry_events || []);
    if (round.compaction_marker_before) {
        section.appendChild(renderRoundHistoryDivider(round.compaction_marker_before));
    }
    const pendingCoordinatorApprovals = (round.pending_tool_approvals || []).filter(item => {
        const roleId = item?.role_id || '';
        return roleId === '' || isRunPrimaryRoleId(roleId, round.run_id);
    });
    const coordinatorOverlay = getCoordinatorStreamOverlay(round.run_id);
    const primaryRoleLabel = getRunPrimaryRoleLabel(round.run_id);
    const isLatestRound = index === roundsState.currentRounds.length - 1;

    const mainMessages = mergeRoundMessagesAndInjectionMessages(
        round.coordinator_messages || [],
        round.injection_messages || [],
    );
    if (mainMessages.length > 0) {
        renderHistoricalMessageList(section, mainMessages, {
            collapsibleUserPrompts: true,
            pendingToolApprovals: pendingCoordinatorApprovals,
            primaryRoleLabel,
            runId: round.run_id,
            runStatus: round.run_status,
            runPhase: round.run_phase,
            hasFinalOutput: round.has_final_output === true,
            isLatestRound,
            streamOverlayEntry: coordinatorOverlay,
            timelineView: 'main',
            canonicalStreamKey: 'primary',
        });
    } else if (pendingCoordinatorApprovals.length > 0 || coordinatorOverlay) {
        renderHistoricalMessageList(section, [], {
            collapsibleUserPrompts: true,
            pendingToolApprovals: pendingCoordinatorApprovals,
            primaryRoleLabel,
            runId: round.run_id,
            runStatus: round.run_status,
            runPhase: round.run_phase,
            hasFinalOutput: round.has_final_output === true,
            isLatestRound,
            streamOverlayEntry: coordinatorOverlay,
            timelineView: 'main',
            canonicalStreamKey: 'primary',
        });
    } else if (!round.has_user_messages) {
        const empty = document.createElement('div');
        empty.className = 'panel-empty';
        empty.textContent = formatMessage('rounds.no_messages', {
            role: primaryRoleLabel.toLowerCase(),
        });
        section.appendChild(empty);
    }

    if (state.currentSessionId) {
        const headerEl = header;
        const tokenSessionId = String(state.currentSessionId || '').trim();
        const tokenRunId = String(round.run_id || '').trim();
        const tokenSignal = roundTokenUsageController?.signal || null;
        const renderedToolCallCount = countRoundToolCalls(round);
        void fetchRunTokenUsage(tokenSessionId, tokenRunId, { signal: tokenSignal }).then(usage => {
            if (
                tokenSignal?.aborted
                || String(state.currentSessionId || '').trim() !== tokenSessionId
            ) {
                return;
            }
            if (!usage || usage.total_tokens === 0) return;
            const fmt = n => n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
            const toolCallCount = Math.max(
                Number(usage.total_tool_calls || 0),
                renderedToolCallCount,
            );
            const pill = document.createElement('div');
            pill.className = 'round-token-summary';
            pill.title = formatMessage('rounds.token_title', {
                input: usage.total_input_tokens,
                output: usage.total_output_tokens,
                requests: usage.total_requests,
            });
            pill.innerHTML = `
                <span class="token-in">${esc(formatMessage('rounds.token_in', { value: fmt(usage.total_input_tokens) }))}</span>
                <span class="token-out">${esc(formatMessage('rounds.token_out', { value: fmt(usage.total_output_tokens) }))}</span>
                ${toolCallCount > 0 ? `<span class="token-tools">${esc(formatMessage('rounds.token_tools', { value: toolCallCount }))}</span>` : ''}
            `;
            const tokenHost = headerEl.querySelector('.round-detail-token-host');
            if (tokenHost) {
                tokenHost.appendChild(pill);
            }
        }).catch(error => {
            if (error?.name === 'AbortError') {
                return;
            }
        });
    }

    return section;
}

function countRoundToolCalls(round) {
    const messages = Array.isArray(round?.coordinator_messages)
        ? round.coordinator_messages
        : [];
    const toolCallIds = new Set();
    let anonymousCount = 0;
    messages.forEach(message => {
        const parts = Array.isArray(message?.message?.parts)
            ? message.message.parts
            : [];
        parts.forEach(part => {
            const kind = String(part?.part_kind || part?.kind || '').trim();
            if (kind !== 'tool-call' && !(part?.tool_name && part?.args !== undefined)) {
                return;
            }
            const toolCallId = String(part?.tool_call_id || '').trim();
            if (toolCallId) {
                toolCallIds.add(toolCallId);
            } else {
                anonymousCount += 1;
            }
        });
    });
    return toolCallIds.size + anonymousCount;
}

function mergeRoundMessagesAndInjectionMessages(messages, injectionMessages) {
    const normalizedMessages = Array.isArray(messages)
        ? messages.map((message, index) => ({
            ...message,
            __timelineSortAt: String(message?.created_at || ''),
            __timelineSortIndex: index,
        }))
        : [];
    const syntheticInjectionMessages = Array.isArray(injectionMessages)
        ? injectionMessages
            .map((message, index) => injectionMessageToHistoryMessage(message, index))
            .filter(Boolean)
        : [];
    return [...normalizedMessages, ...syntheticInjectionMessages]
        .sort(compareTimelineMessages)
        .map(message => {
            const cleaned = { ...message };
            delete cleaned.__timelineSortAt;
            delete cleaned.__timelineSortIndex;
            return cleaned;
        });
}

function injectionMessageToHistoryMessage(rawMessage, index) {
    if (!rawMessage || typeof rawMessage !== 'object') {
        return null;
    }
    const contentParts = normalizePromptContentParts(rawMessage.content_parts || rawMessage.content);
    const content = String(
        rawMessage.content
        || summarizePromptContentParts(contentParts)
        || '',
    ).trim();
    if (!content) {
        return null;
    }
    const occurredAt = injectionSortAt(rawMessage);
    const source = String(rawMessage.source || 'user');
    return {
        entry_type: 'injection',
        message_id: String(rawMessage.message_id || rawMessage.injection_id || occurredAt || ''),
        injection_id: String(rawMessage.injection_id || rawMessage.message_id || ''),
        source,
        status: String(rawMessage.status || 'queued'),
        content,
        content_parts: contentParts || [],
        role: 'user',
        role_id: '',
        instance_id: String(rawMessage.recipient_instance_id || ''),
        label: source === 'subagent'
            ? t('inject.message.subagent_label')
            : t('inject.message.label'),
        created_at: occurredAt,
        injection_status: String(rawMessage.status || 'queued'),
        message: {
            parts: [
                {
                    part_kind: 'text',
                    content,
                },
            ],
        },
        __timelineSortAt: occurredAt,
        __timelineSortIndex: 100000 + index,
    };
}

function compareTimelineMessages(a, b) {
    const leftAt = Date.parse(String(a?.__timelineSortAt || ''));
    const rightAt = Date.parse(String(b?.__timelineSortAt || ''));
    if (Number.isFinite(leftAt) && Number.isFinite(rightAt) && leftAt !== rightAt) {
        return leftAt - rightAt;
    }
    if (Number.isFinite(leftAt) && !Number.isFinite(rightAt)) {
        return 1;
    }
    if (!Number.isFinite(leftAt) && Number.isFinite(rightAt)) {
        return -1;
    }
    return Number(a?.__timelineSortIndex || 0) - Number(b?.__timelineSortIndex || 0);
}

function buildRoundIntentBlock(runId, intentText, intentParts = null, options = {}) {
    const normalizedParts = normalizeRoundIntentParts(intentParts);
    const normalized = normalizedParts && normalizedParts.length > 0
        ? summarizePromptContentParts(normalizedParts)
        : normalizeRoundIntentText(intentText);
    const intentKey = roundIntentKeyFromNormalized(normalized, normalizedParts);
    const stateKey = roundIntentOpenStateKey(runId, intentKey);
    const storedOpen = stateKey ? roundIntentOpenState.get(stateKey) : undefined;
    const shouldRestoreOpen = storedOpen === true || (storedOpen === undefined && options.initialOpen === true);

    const block = document.createElement('details');
    block.className = 'round-detail-intent';
    block.dataset.intentKey = intentKey;
    block.dataset.hasStructuredContent = hasStructuredIntentContent(normalizedParts) ? 'true' : 'false';
    block.dataset.intentTextLength = String(normalized.length);
    block.dataset.intentLineCount = String(normalized.split('\n').length);
    if (stateKey) {
        block.dataset.intentStateKey = stateKey;
    }
    block.dataset.overflow = 'pending';
    if (shouldRestoreOpen) {
        block.dataset.restoreOpen = 'true';
        block.open = true;
    }
    block.innerHTML = `
        <summary class="round-detail-intent-summary">
            <span class="round-detail-intent-preview"></span>
            <span class="round-detail-intent-summary-actions">
                <button type="button" class="round-detail-intent-copy" data-intent-copy-placement="summary"></button>
                <span class="round-detail-intent-toggle"></span>
            </span>
        </summary>
        <div class="round-detail-intent-body">
            <div class="round-detail-intent-content"></div>
            <div class="round-detail-intent-actions">
                <button type="button" class="round-detail-intent-copy" data-intent-copy-placement="body"></button>
                <button type="button" class="round-detail-intent-collapse"></button>
            </div>
        </div>
    `;

    const previewEl = block.querySelector('.round-detail-intent-preview');
    const toggleEl = block.querySelector('.round-detail-intent-toggle');
    const bodyEl = block.querySelector('.round-detail-intent-content');
    const collapseBtn = block.querySelector('.round-detail-intent-collapse');
    const summaryEl = block.querySelector('.round-detail-intent-summary');
    block.querySelectorAll('.round-detail-intent-copy').forEach(button => {
        bindCopyButton(button, normalized);
    });
    if (previewEl) {
        renderPromptTokenizedText(previewEl, normalized);
    }
    if (toggleEl) {
        toggleEl.textContent = t('rounds.expand');
    }
    if (bodyEl) {
        if (normalizedParts && normalizedParts.length > 0) {
            renderRoundIntentStructuredContent(bodyEl, normalizedParts);
        } else {
            bodyEl.textContent = normalized;
        }
    }
    if (collapseBtn) {
        collapseBtn.textContent = t('rounds.collapse');
        collapseBtn.addEventListener('click', event => {
            event.preventDefault();
            event.stopPropagation();
            block.open = false;
            rememberRoundIntentOpenState(block);
            syncRoundIntentToggleText(block);
        });
    }
    if (summaryEl) {
        summaryEl.addEventListener('click', event => {
            if (isRoundIntentSummaryInteractiveTarget(event.target, summaryEl)) {
                return;
            }
            event.preventDefault();
            if (block.dataset.overflow !== 'true') {
                block.open = false;
                syncRoundIntentToggleText(block);
                return;
            }
            block.open = !block.open;
            rememberRoundIntentOpenState(block);
            syncRoundIntentToggleText(block);
        });
        summaryEl.addEventListener('keydown', event => {
            const key = String(event.key || '');
            if (key !== 'Enter' && key !== ' ') return;
            if (event.target !== summaryEl) return;
            event.preventDefault();
            if (block.dataset.overflow !== 'true') {
                block.open = false;
                syncRoundIntentToggleText(block);
                return;
            }
            block.open = !block.open;
            rememberRoundIntentOpenState(block);
            syncRoundIntentToggleText(block);
        });
    }
    block.addEventListener('toggle', () => {
        if (block.dataset.overflow === 'false') {
            block.open = false;
        }
        syncRoundIntentToggleText(block);
    });
    syncRoundIntentToggleText(block);
    scheduleRoundIntentOverflowMeasure(block);
    return block;
}

function isRoundIntentSummaryInteractiveTarget(target, summaryEl) {
    if (!target || target === summaryEl) return false;
    const interactive = target.closest?.('button, a, input, textarea, select, [contenteditable="true"]');
    return !!interactive && summaryEl.contains(interactive);
}

function getRoundIntentKey(intentText, intentParts = null) {
    const normalizedParts = normalizeRoundIntentParts(intentParts);
    const normalized = normalizedParts && normalizedParts.length > 0
        ? summarizePromptContentParts(normalizedParts)
        : normalizeRoundIntentText(intentText);
    return roundIntentKeyFromNormalized(normalized, normalizedParts);
}

function roundIntentKeyFromNormalized(normalizedText, normalizedParts = null) {
    if (normalizedParts && normalizedParts.length > 0) {
        return `parts:${JSON.stringify(normalizedParts)}`;
    }
    return `text:${String(normalizedText || '')}`;
}

function roundIntentOpenStateKey(runId, intentKey) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) return '';
    return safeRunId;
}

function hasStructuredIntentContent(parts) {
    const normalizedParts = Array.isArray(parts) ? parts : [];
    return normalizedParts.some(part => String(part?.kind || '') !== 'text')
        || normalizedParts.length > 1;
}

function rememberRoundIntentOpenState(block) {
    const stateKey = String(block?.dataset?.intentStateKey || '').trim();
    if (!stateKey) return;
    roundIntentOpenState.set(stateKey, block.open === true);
}

function scheduleRoundIntentOverflowMeasure(block, attempt = 0) {
    const measure = () => updateRoundIntentOverflowState(block, attempt);
    if (typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function') {
        window.requestAnimationFrame(measure);
        return;
    }
    measure();
}

function updateRoundIntentOverflowState(block, attempt = 0) {
    const previewEl = block.querySelector('.round-detail-intent-preview');
    if (!previewEl) {
        applyRoundIntentOverflowState(block, false);
        return;
    }

    const clientHeight = Number(previewEl.clientHeight || 0);
    const scrollHeight = Number(previewEl.scrollHeight || 0);
    const clientWidth = Number(previewEl.clientWidth || 0);
    const scrollWidth = Number(previewEl.scrollWidth || 0);
    const isMeasurable = clientHeight > 0 || clientWidth > 0;
    if (!isMeasurable && attempt < 2) {
        scheduleRoundIntentOverflowMeasure(block, attempt + 1);
        return;
    }

    const hasStructuredContent = block.dataset.hasStructuredContent === 'true';
    const fallbackOverflow = hasRoundIntentFallbackOverflow(block);
    const hasOverflow = hasStructuredContent
        || scrollHeight > clientHeight + 1
        || scrollWidth > clientWidth + 1
        || (!isMeasurable && fallbackOverflow)
        || fallbackOverflow;
    applyRoundIntentOverflowState(block, hasOverflow);
}

function hasRoundIntentFallbackOverflow(block) {
    const textLength = Number(block?.dataset?.intentTextLength || 0);
    const lineCount = Number(block?.dataset?.intentLineCount || 0);
    return block?.dataset?.hasStructuredContent === 'true'
        || lineCount > 2
        || textLength > 160;
}

function applyRoundIntentOverflowState(block, hasOverflow) {
    const summaryEl = block.querySelector('.round-detail-intent-summary');
    const shouldRestoreOpen = block.dataset.restoreOpen === 'true';
    block.dataset.overflow = hasOverflow ? 'true' : 'false';
    if (summaryEl) {
        summaryEl.tabIndex = hasOverflow ? 0 : -1;
        summaryEl.setAttribute('aria-disabled', hasOverflow ? 'false' : 'true');
    }
    if (hasOverflow && shouldRestoreOpen) {
        block.open = true;
    }
    if (!hasOverflow) {
        block.open = false;
    }
    delete block.dataset.restoreOpen;
    syncRoundIntentToggleText(block);
}

function syncRoundIntentToggleText(block) {
    const toggleEl = block.querySelector('.round-detail-intent-toggle');
    if (toggleEl) {
        toggleEl.textContent = block.open ? t('rounds.collapse') : t('rounds.expand');
    }
}

function renderRoundIntentStructuredContent(bodyEl, parts) {
    bodyEl.replaceChildren();
    const normalizedParts = Array.isArray(parts) ? parts : [];
    normalizedParts.forEach(part => {
        if (String(part?.kind || '') === 'text') {
            const textEl = document.createElement('div');
            textEl.className = 'msg-text round-detail-intent-text';
            renderPromptTokenizedText(textEl, String(part.text || ''));
            bodyEl.appendChild(textEl);
            return;
        }
        const partHost = document.createElement('div');
        renderPromptContentParts(partHost, [part], {
            enableWorkspaceImagePreview: false,
        });
        if (partHost.childNodes.length > 0) {
            bodyEl.appendChild(partHost);
        }
    });
}

function normalizeRoundIntentText(intentText) {
    const normalized = String(intentText || '').replace(/\r\n?/g, '\n').trim();
    return normalized || t('rounds.no_intent');
}

function applyActiveRoundState(round, visibleScore, options = {}) {
    const safeRunId = String(round?.run_id || '').trim();
    if (!safeRunId) {
        return;
    }
    roundsState.activeRunId = safeRunId;
    roundsState.activeVisibility = normalizeActiveVisibleScore(visibleScore);
    if (options.lockActive === true) {
        roundsState.activeLockUntil = Date.now() + Number(options.lockMs || ROUND_ACTIVE_LOCK_MS);
    }
    roundsState.currentRound = round;
    const pendingApprovals = Array.isArray(round.pending_tool_approvals)
        ? round.pending_tool_approvals
        : [];
    setRoundPendingApprovals(safeRunId, pendingApprovals);
    syncExportedState();
    if (roundsState.suppressNavigatorFollow !== true) {
        setActiveRoundNav(safeRunId, {
            layoutReason: options.navigatorLayoutReason || 'follow-active',
        });
    }
}

function normalizeActiveVisibleScore(visibleScore) {
    const numeric = Number(visibleScore || 0);
    return Number.isFinite(numeric) ? Math.max(0, numeric) : estimateRoundVisibleScore();
}

function estimateRoundVisibleScore() {
    const container = els.chatMessages;
    return Math.max(1, Number(container?.clientHeight || 1));
}

function isActiveRoundLocked() {
    return Date.now() < Number(roundsState.activeLockUntil || 0);
}

function lockProgrammaticRoundScroll(durationMs = ROUND_PROGRAMMATIC_SCROLL_LOCK_MS) {
    roundsState.programmaticScrollUnlockAt = Date.now() + Number(durationMs || 0);
}

function isProgrammaticRoundScrollLocked() {
    return Date.now() < Number(roundsState.programmaticScrollUnlockAt || 0);
}

function splitRoundsByHistoryMarkers(rounds) {
    const items = Array.isArray(rounds) ? rounds : [];
    if (items.length === 0) return [];

    const segments = [];
    let currentSegmentRounds = [];
    items.forEach((round, index) => {
        if (round?.clear_marker_before && currentSegmentRounds.length > 0) {
            const marker = round.clear_marker_before;
            segments.push({
                segmentId: `segment-before-${String(marker.marker_id || segments.length)}`,
                rounds: currentSegmentRounds,
                clearMarker: marker,
                isLatest: false,
            });
            currentSegmentRounds = [];
        }
        currentSegmentRounds.push({ round, index });
    });

    const latestSource = currentSegmentRounds[0]?.round?.clear_marker_before;
    segments.push({
        segmentId: latestSource
            ? `segment-after-${String(latestSource.marker_id || segments.length)}`
            : 'segment-current',
        rounds: currentSegmentRounds,
        clearMarker: null,
        isLatest: true,
    });
    return segments;
}

function renderClearDivider(segment, isExpanded) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'round-clear-divider';
    button.dataset.segmentId = segment.segmentId;
    button.setAttribute('aria-expanded', isExpanded ? 'true' : 'false');
    const markerLabel = String(segment.clearMarker?.label || 'History cleared');
    const roundCount = segment.rounds.length;
    const roundLabel = roundCount === 1 ? '1 round' : `${roundCount} rounds`;
    button.innerHTML = `
        <span class="round-clear-divider-line" aria-hidden="true"></span>
        <span class="round-clear-divider-chip">
            <span class="round-clear-divider-title">${esc(markerLabel)}</span>
            <span class="round-clear-divider-copy">${isExpanded ? `Hide ${roundLabel}` : `Show ${roundLabel}`}</span>
        </span>
        <span class="round-clear-divider-line" aria-hidden="true"></span>
    `;
    button.addEventListener('click', () => {
        toggleHistorySegment(segment.segmentId);
    });
    return button;
}

function renderHistoryLoadMoreControl() {
    if (!shouldShowHistoryLoadMoreControl()) {
        return null;
    }
    const wrapper = document.createElement('div');
    wrapper.className = 'round-history-load-more';
    wrapper.dataset.loading = roundsState.paging.loading ? 'true' : 'false';
    wrapper.dataset.failed = olderRoundLoadFailed ? 'true' : 'false';

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'round-history-load-more-btn';
    button.disabled = roundsState.paging.loading === true;
    button.setAttribute('aria-live', 'polite');
    button.setAttribute('aria-label', resolveHistoryLoadMoreLabel());
    button.innerHTML = renderHistoryLoadMoreButtonContent();
    button.addEventListener('click', () => {
        void loadOlderRounds({ source: 'button' });
    });

    wrapper.appendChild(button);
    return wrapper;
}

function shouldShowHistoryLoadMoreControl() {
    return roundsState.paging.hasMore === true
        || roundsState.paging.loading === true
        || olderRoundLoadFailed === true;
}

function renderHistoryLoadMoreButtonContent() {
    const label = resolveHistoryLoadMoreLabel();
    return `
        <span class="round-history-load-more-icon" aria-hidden="true">${renderHistoryLoadMoreIcon()}</span>
        <span class="round-history-load-more-label">${esc(label)}</span>
    `;
}

function renderHistoryLoadMoreIcon() {
    if (roundsState.paging.loading === true) {
        return `
            <svg class="round-history-load-more-spinner" viewBox="0 0 16 16" focusable="false">
                <circle class="round-history-load-more-spinner-track" cx="8" cy="8" r="5.5"></circle>
                <path class="round-history-load-more-spinner-mark" d="M8 2.5a5.5 5.5 0 0 1 5.5 5.5"></path>
            </svg>
        `;
    }
    return `
        <svg class="round-history-load-more-arrow" viewBox="0 0 16 16" focusable="false">
            <path d="M8 12.5V3.5"></path>
            <path d="M4.5 7 8 3.5 11.5 7"></path>
        </svg>
    `;
}

function resolveHistoryLoadMoreLabel() {
    if (roundsState.paging.loading === true) {
        return t('rounds.load_more.loading');
    }
    if (olderRoundLoadFailed) {
        return t('rounds.load_more.retry');
    }
    return t('rounds.load_more.label');
}

function syncHistoryLoadMoreControl(container) {
    const control = container?.querySelector?.('.round-history-load-more') || null;
    if (!control) {
        return;
    }
    if (!shouldShowHistoryLoadMoreControl()) {
        control.remove?.();
        return;
    }
    control.dataset.loading = roundsState.paging.loading ? 'true' : 'false';
    control.dataset.failed = olderRoundLoadFailed ? 'true' : 'false';
    const button = control.querySelector?.('.round-history-load-more-btn') || null;
    if (button) {
        button.disabled = roundsState.paging.loading === true;
        button.setAttribute('aria-label', resolveHistoryLoadMoreLabel());
        button.innerHTML = renderHistoryLoadMoreButtonContent();
    }
    syncHistoryLoadMoreAlignment(container);
}

function syncHistoryLoadMoreAlignment(container = els.chatMessages) {
    const control = container?.querySelector?.('.round-history-load-more') || null;
    if (!container || !control) return;
    const anchor = container.querySelector('.session-round-section .round-detail-header')
        || container.querySelector('.session-round-section');
    if (!isElementLike(anchor)) {
        control.style.removeProperty('width');
        control.style.removeProperty('margin-left');
        control.style.removeProperty('margin-right');
        return;
    }
    const anchorRect = anchor.getBoundingClientRect?.() || null;
    const containerRect = container.getBoundingClientRect?.() || null;
    if (!anchorRect || !containerRect || Number(anchorRect.width || 0) <= 0) return;
    control.style.width = `${Math.round(anchorRect.width)}px`;
    control.style.marginLeft = 'auto';
    control.style.marginRight = 'auto';
}

function renderRoundHistoryDivider(marker) {
    const divider = document.createElement('div');
    divider.className = 'round-history-divider';
    divider.dataset.markerType = String(marker?.marker_type || '');
    divider.innerHTML = `
        <span class="round-history-divider-line" aria-hidden="true"></span>
        <span class="round-history-divider-chip">${esc(String(marker?.label || t('rounds.history_compacted')))}</span>
        <span class="round-history-divider-line" aria-hidden="true"></span>
    `;
    return divider;
}

function toggleHistorySegment(segmentId, expanded) {
    const segment = document.querySelector(`.round-history-segment[data-segment-id="${segmentId}"]`);
    if (!segment) return;
    const body = segment.querySelector('.round-history-segment-body');
    const trigger = segment.querySelector('.round-clear-divider');
    if (!body || !trigger) return;

    const nextExpanded = typeof expanded === 'boolean'
        ? expanded
        : body.hidden;
    body.hidden = !nextExpanded;
    segment.dataset.expanded = nextExpanded ? 'true' : 'false';
    trigger.setAttribute('aria-expanded', nextExpanded ? 'true' : 'false');

    const copy = trigger.querySelector('.round-clear-divider-copy');
    const roundCount = Number(segment.querySelectorAll('.session-round-section').length || 0);
    const roundLabel = roundCount === 1 ? '1 round' : `${roundCount} rounds`;
    if (copy) {
        copy.textContent = nextExpanded ? `Hide ${roundLabel}` : `Show ${roundLabel}`;
    }

    if (nextExpanded) {
        expandedHistorySegments.add(segmentId);
    } else {
        expandedHistorySegments.delete(segmentId);
    }
}

function expandHistorySegmentForRun(runId) {
    const section = document.getElementById(roundSectionId(runId));
    if (!section) return;
    const segment = section.closest('.round-history-segment');
    if (!segment) return;
    const segmentId = String(segment.dataset.segmentId || '').trim();
    if (!segmentId || segment.dataset.expanded === 'true') return;
    toggleHistorySegment(segmentId, true);
}

function getVisibleRoundSections(container) {
    return Array.from(container.querySelectorAll('.session-round-section'))
        .filter(isVisibleRoundSection);
}

function isVisibleRoundSection(section) {
    if (!isElementLike(section)) {
        return false;
    }
    if (hasHiddenAncestor(section)) {
        return false;
    }
    if ('offsetParent' in section && section.offsetParent === null) {
        return false;
    }
    const rect = section.getBoundingClientRect?.() || null;
    if (!rect) {
        return false;
    }
    return Number(rect.width || 0) > 0 || Number(rect.height || 0) > 0;
}

function isElementLike(element) {
    return !!element
        && typeof element === 'object'
        && typeof element.querySelector === 'function'
        && typeof element.getBoundingClientRect === 'function';
}

function hasHiddenAncestor(element) {
    let current = element;
    while (current && current !== document.body) {
        const hiddenAttr = typeof current.getAttribute === 'function'
            ? current.getAttribute('hidden')
            : null;
        if (current.hidden === true || (hiddenAttr !== null && hiddenAttr !== undefined)) {
            return true;
        }
        current = current.parentNode;
    }
    return false;
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
        const nextRound = roundsState.currentRounds.find(
            round => round.run_id === pendingRunId,
        ) || null;
        if (nextRound) {
            applyActiveRoundState(nextRound, estimateRoundVisibleScore(), {
                navigatorLayoutReason: 'sync-visible-active',
            });
        } else {
            activateRoundSection(targetSection, estimateRoundVisibleScore());
            setActiveRoundNav(pendingRunId, { layoutReason: 'sync-visible-active' });
        }
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
    roundsState.activeLockUntil = 0;
}

function activateLatestRound(rounds, options = {}) {
    const latestRound = Array.isArray(rounds) && rounds.length > 0
        ? rounds[rounds.length - 1]
        : null;
    applyActiveRoundState(latestRound, estimateRoundVisibleScore(), {
        ...options,
        lockActive: options.lockActive === true,
    });
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
    if (!isElementLike(section)) return;
    section.classList.remove('round-section-emphasis');
    void section.offsetWidth;
    section.classList.add('round-section-emphasis');
    window.setTimeout(() => {
        section.classList.remove('round-section-emphasis');
    }, 1600);
}

async function loadOlderRounds(options = {}) {
    await loadOlderRoundPage({
        source: options.source || 'button',
        navigatorLayoutReason: 'sync-visible-active',
    });
}

async function loadOlderRoundPage(options = {}) {
    if (!roundsState.paging.hasMore || roundsState.paging.loading || !state.currentSessionId) {
        return false;
    }

    const container = els.chatMessages;
    if (!container) return false;

    roundsState.paging.loading = true;
    roundsState.suppressNavigatorFollow = true;
    olderRoundLoadFailed = false;
    clearOlderRoundLoadIntentTimer();
    let anchor = captureChatScrollAnchor(container, getVisibleRoundSections);
    const startedAt = Date.now();
    setOlderRoundLoading(container, true);
    syncHistoryLoadMoreControl(container);
    try {
        if (options.source === 'timeline' && options.revealLoadControl === true) {
            await revealHistoryLoadMoreControl(container);
            anchor = captureHistoryLoadTopAnchor();
        }
        const page = await fetchOlderRoundsPage();
        if (!page) {
            roundsState.paging.loading = false;
            olderRoundLoadFailed = true;
            setOlderRoundLoading(container, false);
            syncHistoryLoadMoreControl(container);
            return false;
        }
        const loadedRunIds = getRoundPageRunIds(page);
        applyRoundPage(page, { prepend: true });
        olderRoundLoadFailed = false;
        syncExportedState();
        renderSessionTimeline(roundsState.currentRounds, {
            scrollPlan: {
                policy: 'preserve-anchor',
                anchor,
                navigatorLayoutReason: options.navigatorLayoutReason || 'sync-visible-active',
                preserveLoadedRounds: true,
            },
            navigatorLayoutReason: options.navigatorLayoutReason || 'sync-visible-active',
        });
        markPrependedRounds(container);
        return {
            loaded: true,
            loadedRunIds,
        };
    } catch (e) {
        logError(
            'frontend.rounds.load_older_failed',
            'Failed loading older rounds',
            errorToPayload(e, {
                session_id: state.currentSessionId,
                source: options.source || 'unknown',
            }),
        );
        olderRoundLoadFailed = true;
        roundsState.paging.loading = false;
        return false;
    } finally {
        roundsState.paging.loading = false;
        roundsState.suppressNavigatorFollow = false;
        setOlderRoundLoading(container, false);
        syncHistoryLoadMoreControl(container);
        await waitForHistoryLoadPaint(startedAt);
        if (options.source === 'timeline') {
            setActiveRoundNav(roundsState.activeRunId, {
                layoutReason: options.navigatorLayoutReason || 'sync-visible-active',
            });
        }
    }
}

function captureHistoryLoadTopAnchor(container = els.chatMessages) {
    return {
        scrollTop: Number(container?.scrollTop || 0),
        visibleRunId: '',
        visibleTopOffset: 0,
    };
}

async function revealHistoryLoadMoreControl(container = els.chatMessages) {
    const control = container?.querySelector?.('.round-history-load-more') || null;
    if (!isElementLike(control)) {
        await waitForHistoryLoadPaint(Date.now());
        return;
    }
    const containerRect = container.getBoundingClientRect();
    const controlRect = control.getBoundingClientRect();
    const currentTop = Number(container.scrollTop || 0);
    const targetTop = Math.max(0, currentTop + controlRect.top - containerRect.top - 28);
    await animateChatScrollTo(container, targetTop, {
        durationMs: resolveChatScrollDuration(currentTop, targetTop, {
            source: 'timeline',
            loadedHistory: true,
        }),
        lockMs: 1300,
        source: 'timeline',
    });
    await waitForHistoryLoadPaint(Date.now());
}

async function loadRoundsUntilRun(runId) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId || !state.currentSessionId) return;
    if (roundsState.currentRounds.some(round => round.run_id === safeRunId)) {
        return;
    }
    const container = els.chatMessages;
    if (!container) return;

    while (
        !roundsState.currentRounds.some(round => round.run_id === safeRunId)
        && roundsState.paging.hasMore
        && !roundsState.paging.loading
    ) {
        const result = await loadOlderRoundPage({
            source: 'timeline',
            navigatorLayoutReason: 'structure',
            revealLoadControl: true,
        });
        if (!result?.loaded) return false;
        const targetSection = document.getElementById(roundSectionId(safeRunId));
        if (targetSection) {
            await scrollRoundIntoViewWithContext(targetSection, {
                source: 'timeline',
                loadedHistory: true,
            });
            return true;
        }
    }
    const targetSection = document.getElementById(roundSectionId(safeRunId));
    if (targetSection) {
        await scrollRoundIntoViewWithContext(targetSection, {
            source: 'timeline',
            loadedHistory: true,
        });
        return true;
    }
    return false;
}

function getRoundPageRunIds(page) {
    return (Array.isArray(page?.items) ? page.items : [])
        .map(round => String(round?.run_id || '').trim())
        .filter(Boolean);
}

function findRoundSectionForProgressiveHistoryPage(runIds) {
    const ids = Array.isArray(runIds) ? runIds : [];
    for (const runId of ids) {
        const section = document.getElementById(roundSectionId(runId));
        if (section) return section;
    }
    return null;
}

async function waitForHistoryLoadPaint(startedAt = 0) {
    await waitForAnimationFrame();
    await waitForAnimationFrame();
    const elapsed = Date.now() - Number(startedAt || Date.now());
    const remaining = Math.max(0, ROUND_HISTORY_LOAD_STEP_MIN_MS - elapsed);
    if (remaining > 0) {
        await new Promise(resolve => {
            const timer = typeof window !== 'undefined' && typeof window.setTimeout === 'function'
                ? window.setTimeout
                : setTimeout;
            timer(resolve, remaining);
        });
    }
}

function waitForAnimationFrame() {
    return new Promise(resolve => {
        if (typeof window === 'undefined' || typeof window.requestAnimationFrame !== 'function') {
            resolve();
            return;
        }
        window.requestAnimationFrame(() => {
            resolve();
        });
    });
}

function setOlderRoundLoading(container, loading) {
    if (!container) {
        return;
    }
    container.classList.toggle('round-history-loading-older', loading === true);
}

function markPrependedRounds(container) {
    if (!container || typeof window === 'undefined') {
        return;
    }
    container.classList.add('round-history-prepended');
    window.setTimeout(() => {
        container.classList.remove('round-history-prepended');
    }, 260);
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

function patchRoundHeader(round, _roundIndex) {
    const section = document.querySelector(`.session-round-section[data-run-id="${round.run_id}"]`);
    if (!section) return;

    const metaEl = section.querySelector('.round-detail-meta');
    const liveBadge = metaEl?.querySelector?.('.live-badge');
    if (metaEl && round.run_status === 'running' && !liveBadge) {
        const badge = document.createElement('span');
        badge.className = 'live-badge';
        badge.textContent = 'LIVE';
        const tokenHost = metaEl.querySelector('.round-detail-token-host');
        metaEl.insertBefore(badge, tokenHost || null);
    } else if (liveBadge && round.run_status !== 'running') {
        liveBadge.remove();
    }

    const badgesEl = section.querySelector('.round-detail-badges');
    if (badgesEl) {
        const stateLabel = roundStateLabel(round);
        const stateTone = roundStateTone(round);
        const approvalCount = Number(round.pending_tool_approval_count || 0);
        badgesEl.innerHTML = renderRoundBadges(round, stateLabel, stateTone, approvalCount);
    }

    const intentEl = section.querySelector('.round-detail-intent');
    if (intentEl) {
        const nextIntentKey = getRoundIntentKey(round.intent, round.intent_parts);
        if (intentEl.dataset.intentKey === nextIntentKey) {
            scheduleRoundIntentOverflowMeasure(intentEl);
            return;
        }
        const initialOpen = intentEl.open === true || intentEl.dataset.restoreOpen === 'true';
        intentEl.replaceWith(buildRoundIntentBlock(round.run_id, round.intent, round.intent_parts, { initialOpen }));
    }
}

function patchRoundRetryEvents(runId) {
    const round = roundsState.currentRounds.find(item => item.run_id === runId);
    const section = document.querySelector(`.session-round-section[data-run-id="${runId}"]`);
    if (!round || !section) return;
    const retryEvents = Array.isArray(round.retry_events) ? round.retry_events : [];
    const existing = section.querySelector('.round-retry-timeline');
    if (retryEvents.length === 0) {
        if (existing) {
            existing.remove();
        }
        return;
    }
    if (!existing) {
        renderRoundRetryEvents(section, retryEvents);
        return;
    }
    const nowMs = Date.now();
    existing.innerHTML = retryEvents.map(event => renderRetryEventMarkup(event, nowMs)).join('');
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
            updateScheduledRetryCountdowns(round.run_id, retryEvents);
        }
    });
}

function renderRoundBadges(round, stateLabel, stateTone, approvalCount) {
    const microcompactBadge = renderMicrocompactBadge(round?.microcompact);
    return `
        ${stateLabel ? `<span class="round-state-pill round-state-${stateTone}">${esc(stateLabel)}</span>` : ''}
        ${approvalCount > 0 ? `<span class="round-state-pill round-state-warning">${esc(t('rounds.pending_approvals').replace('{count}', String(approvalCount)))}</span>` : ''}
        ${microcompactBadge}
    `;
}

function renderMicrocompactBadge(microcompact) {
    if (!microcompact || microcompact.applied !== true) {
        return '';
    }
    const before = formatRoundTokenCount(microcompact.estimated_tokens_before);
    const after = formatRoundTokenCount(microcompact.estimated_tokens_after);
    const messageCount = Number(microcompact.compacted_message_count || 0);
    const partCount = Number(microcompact.compacted_part_count || 0);
    const label = formatMessage('rounds.microcompact_badge', { before, after });
    const title = formatMessage('rounds.microcompact_title', {
        before: String(Number(microcompact.estimated_tokens_before || 0)),
        after: String(Number(microcompact.estimated_tokens_after || 0)),
        messages: String(messageCount),
        parts: String(partCount),
    });
    return `<span class="round-state-pill round-state-idle" title="${esc(title)}">${esc(label)}</span>`;
}

function formatRoundTokenCount(value) {
    const normalized = Math.max(0, Number(value || 0));
    return normalized >= 1000 ? `${(normalized / 1000).toFixed(1)}k` : String(normalized);
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
    insertRoundRetryTimeline(section, host);
}

function renderRetryEventMarkup(event, nowMs) {
    const eventId = retryEventDomId(event);
    const kind = String(event?.kind || 'retry').trim() || 'retry';
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
    const fallbackTarget = String(event?.to_profile_id || event?.to_model || '').trim();
    const fallbackCopy = phase === 'failed'
        ? formatMessage('rounds.retry.fallback_failed_copy', {})
        : fallbackTarget
            ? formatMessage('rounds.retry.fallback_switched_copy', { target: fallbackTarget })
            : formatMessage('rounds.retry.fallback_activated_copy', {});
    const retryCopy = phase === 'retrying'
        ? formatMessage('rounds.retry.retrying_copy', {
            attempt: attemptNumber,
            total: totalAttempts,
        })
        : phase === 'failed'
            ? formatMessage('rounds.retry.failed_copy', {
                attempt: attemptNumber,
                total: totalAttempts,
            })
            : phase === 'succeeded'
                ? formatMessage('rounds.retry.succeeded_copy', {
                    attempt: attemptNumber,
                    total: totalAttempts,
                })
                : formatMessage('rounds.retry.scheduled_copy', {
                    attempt: attemptNumber,
                    total: totalAttempts,
                    seconds: retrySeconds,
                });
    const copy = kind === 'fallback' ? fallbackCopy : retryCopy;
    const fallbackLabel = phase === 'failed'
        ? t('rounds.retry.fallback_failed_label')
        : t('rounds.retry.fallback_label');
    const retryLabel = phase === 'retrying'
        ? t('rounds.retry.retrying_label')
        : phase === 'failed'
            ? t('rounds.retry.failed_label')
            : phase === 'succeeded'
                ? t('rounds.retry.succeeded_label')
                : t('rounds.retry.scheduled_label');
    const label = kind === 'fallback' ? fallbackLabel : retryLabel;
    const spinner = kind === 'retry' && (phase === 'scheduled' || phase === 'retrying')
        ? '<span class="round-retry-spinner" aria-hidden="true"></span>'
        : '';
    const activeClass = isActive ? ' round-retry-item-active' : '';
    const phaseClass = ` round-retry-item-${escAttributeToken(phase)}`;
    const failedClass = phase === 'failed' ? ' round-retry-item-failed' : '';
    return `
        <div class="round-retry-item${activeClass}${phaseClass}${failedClass}" data-retry-event-id="${esc(eventId)}">
            <div class="round-retry-main">
                <span class="round-retry-label">${spinner}<span>${esc(label)}</span></span>
                <span class="round-retry-copy">${esc(copy)}</span>
            </div>
            <div class="round-retry-meta">
                ${errorCode ? `<span class="round-retry-code">${esc(errorCode)}</span>` : ''}
                ${occurredLabel ? `<span class="round-retry-time">${esc(occurredLabel)}</span>` : ''}
            </div>
            ${errorMessage ? `<div class="round-retry-detail">${esc(errorMessage)}</div>` : ''}
        </div>
    `;
}

function insertRoundRetryTimeline(section, host) {
    const header = section.querySelector('.round-detail-header');
    if (header?.nextSibling) {
        section.insertBefore(host, header.nextSibling);
        return;
    }
    section.appendChild(host);
}

function updateScheduledRetryCountdowns(runId, retryEvents) {
    const section = document.querySelector(`.session-round-section[data-run-id="${runId}"]`);
    const host = section?.querySelector('.round-retry-timeline');
    if (!section || !host) {
        patchRoundRetryEvents(runId);
        return;
    }
    const nowMs = Date.now();
    retryEvents.filter(isScheduledRetryEvent).forEach(event => {
        const eventId = retryEventDomId(event);
        const item = Array.from(host.querySelectorAll('.round-retry-item')).find(
            element => element.dataset.retryEventId === eventId,
        );
        const copyEl = item?.querySelector('.round-retry-copy');
        if (!copyEl) {
            patchRoundRetryEvents(runId);
            return;
        }
        copyEl.textContent = retryEventCopy(event, nowMs);
    });
}

function retryEventCopy(event, nowMs) {
    const attemptNumber = Number(event?.attempt_number || 0);
    const totalAttempts = Number(event?.total_attempts || 0);
    const displayRetryMs = isScheduledRetryEvent(event)
        ? computeRetryRemainingMs(event, nowMs)
        : Number(event?.retry_in_ms || 0);
    const retrySeconds = displayRetryMs > 0
        ? `${(displayRetryMs / 1000).toFixed(displayRetryMs >= 10000 ? 0 : 1)}s`
        : '0s';
    return formatMessage('rounds.retry.scheduled_copy', {
        attempt: attemptNumber,
        total: totalAttempts,
        seconds: retrySeconds,
    });
}

function retryEventDomId(event) {
    const rawId = String(event?.event_id || '').trim();
    if (rawId) {
        return rawId;
    }
    const kind = String(event?.kind || 'retry').trim() || 'retry';
    const occurredAt = String(event?.occurred_at || '').trim();
    const attemptNumber = String(event?.attempt_number || 0);
    return `${kind}-${occurredAt}-${attemptNumber}`;
}

function escAttributeToken(value) {
    return String(value || '')
        .trim()
        .toLowerCase()
        .replaceAll(/[^a-z0-9_-]/g, '-')
        || 'scheduled';
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
