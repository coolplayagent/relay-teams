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
import { fetchRunTokenUsage } from '../../core/api.js';
import { setRoundPendingApprovals } from '../agentPanel.js';
import {
    clearAllStreamState,
    getCoordinatorStreamOverlay,
    renderHistoricalMessageList,
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
const ROUND_PROGRAMMATIC_SCROLL_LOCK_MS = 450;
const ROUND_ACTIVE_LOCK_MS = 900;
const ROUND_HISTORY_LOAD_INTENT_DEBOUNCE_MS = 120;
const ROUND_HISTORY_LOAD_STEP_MIN_MS = 150;
const ROUND_SCROLL_ANIMATION_MIN_MS = 420;
const ROUND_SCROLL_ANIMATION_MAX_MS = 650;
const ROUND_TIMELINE_SCROLL_ANIMATION_MIN_MS = 520;
const ROUND_TIMELINE_SCROLL_ANIMATION_MAX_MS = 2400;
const ROUND_SCROLL_BOTTOM_THRESHOLD_PX = 96;
let olderRoundLoadFailed = false;
let olderRoundLoadIntentTimer = 0;
let olderRoundPagingIntentBound = false;
let olderRoundTouchStartY = null;
let chatScrollAnimationFrame = 0;
let chatScrollAnimationToken = 0;
let scrollToBottomControl = null;
let historyLoadMoreResizeBound = false;

export function clearSessionTimeline() {
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
    const timelinePageResult = fetchTimelineRoundsPage(sessionId)
        .then(value => ({ status: 'fulfilled', value }))
        .catch(reason => ({ status: 'rejected', reason }));
    try {
        const page = await fetchInitialRoundsPage(sessionId);
        if (!isSessionLoadCurrent(sessionId)) {
            return;
        }
        applyRoundPage(page, {
            prepend: false,
            mergeExisting: renderPlan.preserveLoadedRounds,
        });
        applyTimelineRoundPage(page);
        mergeLiveRoundsForSession(sessionId);
        syncExportedState();
        if (options.render !== false && !shouldPreserveSubagentView(sessionId)) {
            renderSessionTimeline(roundsState.currentRounds, {
                scrollPlan: renderPlan,
                navigatorLayoutReason: renderPlan.navigatorLayoutReason,
            });
        } else {
            renderNavigatorForTimeline({ layoutReason: renderPlan.navigatorLayoutReason });
        }

        const timelineResult = await timelinePageResult;
        if (!isSessionLoadCurrent(sessionId)) {
            return;
        }
        if (timelineResult.status === 'fulfilled') {
            applyTimelineRoundPage(timelineResult.value);
            mergeLiveRoundsForSession(sessionId);
            syncExportedState();
            renderNavigatorForTimeline({ layoutReason: renderPlan.navigatorLayoutReason });
        } else {
            logError(
                'frontend.rounds.timeline_load_failed',
                'Failed loading timeline rounds',
                errorToPayload(timelineResult.reason, { session_id: sessionId }),
            );
        }
    } catch (e) {
        logError(
            'frontend.rounds.load_failed',
            'Failed loading rounds',
            errorToPayload(e, { session_id: sessionId }),
        );
    }
}

function isSessionLoadCurrent(sessionId) {
    const requestedSessionId = String(sessionId || '').trim();
    const currentSessionId = String(state.currentSessionId || '').trim();
    return !currentSessionId || currentSessionId === requestedSessionId;
}

export function createLiveRound(runId, intentText, intentParts = null) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) return;
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
    const current = roundsState.currentRounds[roundIndex]
        || roundsState.timelineRounds.find(round => round.run_id === safeRunId);
    if (!current) return;

    const overlayPatch = pickDefinedRoundOverlay(overlay);
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
    syncRetryTimelineTimer();
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

    if (round.coordinator_messages?.length > 0) {
        renderHistoricalMessageList(section, round.coordinator_messages, {
            collapsibleUserPrompts: true,
            pendingToolApprovals: pendingCoordinatorApprovals,
            primaryRoleLabel,
            runId: round.run_id,
            runStatus: round.run_status,
            runPhase: round.run_phase,
            isLatestRound,
            streamOverlayEntry: coordinatorOverlay,
            timelineView: 'main',
        });
    } else if (pendingCoordinatorApprovals.length > 0 || coordinatorOverlay) {
        renderHistoricalMessageList(section, [], {
            collapsibleUserPrompts: true,
            pendingToolApprovals: pendingCoordinatorApprovals,
            primaryRoleLabel,
            runId: round.run_id,
            runStatus: round.run_status,
            runPhase: round.run_phase,
            isLatestRound,
            streamOverlayEntry: coordinatorOverlay,
            timelineView: 'main',
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
        void fetchRunTokenUsage(state.currentSessionId, round.run_id).then(usage => {
            if (!usage || usage.total_tokens === 0) return;
            const fmt = n => n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
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
                ${usage.total_tool_calls > 0 ? `<span class="token-tools">${esc(formatMessage('rounds.token_tools', { value: usage.total_tool_calls }))}</span>` : ''}
            `;
            const tokenHost = headerEl.querySelector('.round-detail-token-host');
            if (tokenHost) {
                tokenHost.appendChild(pill);
            }
        });
    }

    return section;
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
    if (stateKey) {
        block.dataset.intentStateKey = stateKey;
    }
    block.dataset.overflow = 'pending';
    if (shouldRestoreOpen) {
        block.dataset.restoreOpen = 'true';
    }
    block.innerHTML = `
        <summary class="round-detail-intent-summary">
            <span class="round-detail-intent-preview"></span>
            <span class="round-detail-intent-toggle"></span>
        </summary>
        <div class="round-detail-intent-body">
            <div class="round-detail-intent-content"></div>
            <div class="round-detail-intent-actions">
                <button type="button" class="round-detail-intent-collapse"></button>
            </div>
        </div>
    `;

    const previewEl = block.querySelector('.round-detail-intent-preview');
    const toggleEl = block.querySelector('.round-detail-intent-toggle');
    const bodyEl = block.querySelector('.round-detail-intent-content');
    const collapseBtn = block.querySelector('.round-detail-intent-collapse');
    const summaryEl = block.querySelector('.round-detail-intent-summary');
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
            block.open = false;
            rememberRoundIntentOpenState(block);
        });
    }
    if (summaryEl) {
        summaryEl.addEventListener('click', event => {
            if (block.dataset.overflow !== 'true') {
                event.preventDefault();
                block.open = false;
            }
        });
    }
    block.addEventListener('toggle', () => {
        if (block.dataset.overflow !== 'true') {
            block.open = false;
            forgetRoundIntentOpenState(block);
            return;
        }
        rememberRoundIntentOpenState(block);
        if (toggleEl) {
            toggleEl.textContent = block.open ? t('rounds.collapse') : t('rounds.expand');
        }
    });
    scheduleRoundIntentOverflowMeasure(block);
    return block;
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

function forgetRoundIntentOpenState(block) {
    const stateKey = String(block?.dataset?.intentStateKey || '').trim();
    if (!stateKey) return;
    roundIntentOpenState.delete(stateKey);
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
        block.dataset.overflow = 'false';
        block.open = false;
        forgetRoundIntentOpenState(block);
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
    const hasOverflow = hasStructuredContent
        || scrollHeight > clientHeight + 1
        || scrollWidth > clientWidth + 1;
    const summaryEl = block.querySelector('.round-detail-intent-summary');
    const toggleEl = block.querySelector('.round-detail-intent-toggle');
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
        forgetRoundIntentOpenState(block);
    }
    delete block.dataset.restoreOpen;
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
    section.appendChild(host);
}

function renderRetryEventMarkup(event, nowMs) {
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
        ? 'No fallback candidate succeeded'
        : fallbackTarget
            ? `Switched to ${fallbackTarget}`
            : 'Fallback activated';
    const retryCopy = phase === 'retrying'
        ? `Attempt ${attemptNumber}/${totalAttempts} in progress`
        : phase === 'failed'
            ? `Attempt ${attemptNumber}/${totalAttempts} failed`
            : `Attempt ${attemptNumber}/${totalAttempts} in ${retrySeconds}`;
    const copy = kind === 'fallback' ? fallbackCopy : retryCopy;
    const fallbackLabel = phase === 'failed' ? 'Fallback failed' : 'Fallback';
    const retryLabel = phase === 'retrying'
        ? 'Retrying'
        : phase === 'failed'
            ? 'Retry failed'
            : 'Retry scheduled';
    const label = kind === 'fallback' ? fallbackLabel : retryLabel;
    return `
        <div class="round-retry-item${isActive ? ' round-retry-item-active' : ''}${phase === 'failed' ? ' round-retry-item-failed' : ''}">
            <div class="round-retry-main">
                <span class="round-retry-label">${label}</span>
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
