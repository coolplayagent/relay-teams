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
    getOrCreateStreamBlock,
    appendStreamChunk,
} from '../messageRenderer.js';
import {
    buildStructuredUserPromptSummary,
    userPromptItemToStructuredPart,
} from '../messageRenderer/helpers/block.js';
import { appendStructuredContentPart } from '../messageRenderer/helpers/content.js';
import { renderRoundNavigator, setActiveRoundNav } from './navigator.js';
import { applyRoundPage, fetchInitialRoundsPage, fetchOlderRoundsPage } from './paging.js';
import { roundsState } from './state.js';
import { roundSectionId, esc, roundStateLabel, roundStateTone } from './utils.js';
import { errorToPayload, logError } from '../../utils/logger.js';
import { formatMessage, t } from '../../utils/i18n.js';

export let currentRounds = [];
export let currentRound = null;
let retryTimelineTimerId = 0;
const expandedHistorySegments = new Set();

export async function loadSessionRounds(sessionId, options = {}) {
    try {
        const page = await fetchInitialRoundsPage(sessionId);
        applyRoundPage(page, { prepend: false });
        syncExportedState();
        if (options.render !== false && !shouldPreserveSubagentView(sessionId)) {
            renderSessionTimeline(roundsState.currentRounds, { preserveScroll: false });
        }
    } catch (e) {
        logError(
            'frontend.rounds.load_failed',
            'Failed loading rounds',
            errorToPayload(e, { session_id: sessionId }),
        );
    }
}

export function createLiveRound(runId, intentText, intentParts = null) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) return;
    const normalizedIntent = normalizeRoundIntentText(intentText);
    const normalizedIntentParts = normalizeRoundIntentParts(intentParts);

    const existingIndex = roundsState.currentRounds.findIndex(round => round.run_id === safeRunId);
    if (existingIndex === -1) {
        roundsState.currentRounds = [
            ...roundsState.currentRounds,
            {
                run_id: safeRunId,
                created_at: new Date().toISOString(),
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
            },
        ];
    } else {
        roundsState.currentRounds = roundsState.currentRounds.map(round =>
            round.run_id === safeRunId
                ? {
                    ...round,
                    intent: normalizedIntent || round.intent,
                    intent_parts: normalizedIntentParts || round.intent_parts || null,
                    primary_role_id: round.primary_role_id || getRunPrimaryRoleId(safeRunId) || null,
                    run_status: round.run_status || 'running',
                    run_phase: round.run_phase || 'running',
                    is_recoverable: round.is_recoverable !== false,
                    has_user_messages: true,
                }
                : round,
        );
    }
    syncExportedState();
    if (!shouldPreserveSubagentView(state.currentSessionId)) {
        renderSessionTimeline(roundsState.currentRounds, { preserveScroll: false });
    }

    const section = document.getElementById(roundSectionId(safeRunId));
    if (section) {
        section.scrollIntoView({ behavior: 'smooth', block: 'start' });
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

export function appendRoundUserMessage(runId, text) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) return;
    const normalizedIntentParts = normalizeRoundIntentParts(text);
    const normalizedIntentText = buildRoundIntentPreviewText(text);
    const roundIndex = roundsState.currentRounds.findIndex(round => round.run_id === safeRunId);
    if (roundIndex >= 0) {
        roundsState.currentRounds = roundsState.currentRounds.map(round =>
            round.run_id === safeRunId
                ? {
                    ...round,
                    has_user_messages: true,
                    intent: normalizedIntentText || round.intent,
                    intent_parts: normalizedIntentParts || round.intent_parts || null,
                }
                : round,
        );
        if (roundsState.currentRound?.run_id === safeRunId) {
            roundsState.currentRound = roundsState.currentRounds[roundIndex];
        }
        syncExportedState();
        patchRoundHeader(roundsState.currentRounds[roundIndex], roundIndex);
    }
    const section = document.querySelector(`.session-round-section[data-run-id="${safeRunId}"]`);
    if (section) {
        const empty = section.querySelector('.panel-empty');
        if (empty) empty.remove();
    }
}

function buildRoundUserPromptParts(promptPayload) {
    const normalizedContent = normalizeRoundUserPromptContent(promptPayload);
    if (Array.isArray(normalizedContent)) {
        if (normalizedContent.length === 0) {
            return [];
        }
        return [{ part_kind: 'user-prompt', content: normalizedContent }];
    }
    const promptText = String(normalizedContent || '').trim();
    if (!promptText) {
        return [];
    }
    return [{ part_kind: 'user-prompt', content: promptText }];
}

function normalizeRoundUserPromptContent(promptPayload) {
    if (!Array.isArray(promptPayload)) {
        return String(promptPayload || '').trim();
    }
    const normalizedItems = promptPayload
        .map(part => {
            if (typeof part === 'string') {
                const text = String(part || '').trim();
                return text || null;
            }
            return normalizeRoundUserPromptItem(part);
        })
        .filter(item => item !== null);
    if (normalizedItems.length === 0) {
        return '';
    }
    if (normalizedItems.every(item => typeof item === 'string')) {
        return normalizedItems.join('\n\n').trim();
    }
    return normalizedItems;
}

function normalizeRoundIntentParts(promptPayload) {
    const normalizedContent = normalizeRoundUserPromptContent(promptPayload);
    if (Array.isArray(normalizedContent)) {
        return normalizedContent.length > 0 ? normalizedContent : null;
    }
    const promptText = String(normalizedContent || '').trim();
    if (!promptText) {
        return null;
    }
    return [{ kind: 'text', text: promptText }];
}

function buildRoundIntentPreviewText(promptPayload) {
    const normalizedIntentParts = normalizeRoundIntentParts(promptPayload);
    if (normalizedIntentParts && normalizedIntentParts.length > 0) {
        const summary = buildStructuredUserPromptSummary(normalizedIntentParts);
        const preview = String(summary.preview || '').trim();
        if (preview) {
            return preview;
        }
    }
    return normalizeRoundIntentText(promptPayload);
}

function normalizeRoundUserPromptItem(part) {
    if (!part || typeof part !== 'object') {
        return null;
    }
    const kind = String(part.kind || '').trim();
    if (kind === 'text') {
        const text = String(part.text || '').trim();
        return text || null;
    }
    if (kind === 'binary') {
        const mediaType = String(part.media_type || '').trim();
        const data = String(part.data || '').trim();
        if (!mediaType || !data) {
            return null;
        }
        return {
            kind: 'binary',
            media_type: mediaType,
            data,
            name: String(part.name || '').trim(),
        };
    }
    if (kind === 'inline_media') {
        const mediaType = String(part.mime_type || '').trim();
        const data = String(part.base64_data || '').trim();
        if (!mediaType || !data) {
            return null;
        }
        return {
            kind: 'binary',
            media_type: mediaType,
            data,
            name: String(part.name || '').trim(),
        };
    }
    if (kind === 'media_ref') {
        const modality = String(part.modality || '').trim().toLowerCase();
        const url = String(part.url || '').trim();
        if (!modality || !url) {
            return null;
        }
        return {
            kind: `${modality}-url`,
            url,
            media_type: String(part.mime_type || '').trim(),
            name: String(part.name || '').trim(),
        };
    }
    return null;
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
    expandHistorySegmentForRun(round.run_id);
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

    // Hide container during render to prevent flash of content at wrong
    // scroll position before we reposition.
    const shouldHideDuringRender = !opts.preserveScroll;
    if (shouldHideDuringRender) {
        container.style.visibility = 'hidden';
    }

    container.innerHTML = '';

    clearAllStreamState({ preserveOverlay: true });
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
        if (shouldHideDuringRender) {
            container.style.visibility = '';
        }
        return;
    }

    const segments = splitRoundsByHistoryMarkers(rounds);
    const segmentIds = new Set(segments.map(segment => segment.segmentId));
    Array.from(expandedHistorySegments).forEach(segmentId => {
        if (!segmentIds.has(segmentId)) {
            expandedHistorySegments.delete(segmentId);
        }
    });

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

    renderRoundNavigator(rounds, selectRound);
    bindScrollSync();

    if (opts.preserveScroll) {
        container.scrollTop = oldScroll;
        syncActiveRoundFromScroll();
    } else {
        container.scrollTop = container.scrollHeight;
        activateLatestRound(rounds);
    }

    if (shouldHideDuringRender) {
        container.style.visibility = '';
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

    const sections = getVisibleRoundSections(container);
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
                <div class="round-detail-label">Round ${index + 1}${round.run_status === 'running' ? ' <span class="live-badge">LIVE</span>' : ''}</div>
                <div class="round-detail-meta">
                <div class="round-detail-time">${time}</div>
                    <div class="round-detail-token-host"></div>
                </div>
            </div>
            <div class="round-detail-badges">${renderRoundBadges(round, stateLabel, stateTone, approvalCount)}</div>
        </div>`;
    header.appendChild(buildRoundIntentBlock(round.intent, round.intent_parts));
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

function buildRoundIntentBlock(intentText, intentParts) {
    const normalized = normalizeRoundIntentText(intentText);
    const normalizedIntentParts = normalizeRoundIntentParts(intentParts);

    const block = document.createElement('details');
    block.className = 'round-detail-intent';
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
    if (previewEl) {
        previewEl.textContent = resolveRoundIntentPreview(normalized, normalizedIntentParts);
    }
    if (toggleEl) {
        toggleEl.textContent = t('rounds.expand');
    }
    if (bodyEl) {
        renderRoundIntentBody(bodyEl, normalized, normalizedIntentParts);
    }
    if (collapseBtn) {
        collapseBtn.textContent = t('rounds.collapse');
        collapseBtn.addEventListener('click', event => {
            event.preventDefault();
            block.open = false;
        });
    }
    block.addEventListener('toggle', () => {
        if (toggleEl) {
            toggleEl.textContent = t('rounds.expand');
        }
    });
    return block;
}

function normalizeRoundIntentText(intentText) {
    const normalized = String(intentText || '').replace(/\r\n?/g, '\n').trim();
    return normalized || t('rounds.no_intent');
}

function resolveRoundIntentPreview(intentText, intentParts) {
    if (!Array.isArray(intentParts) || intentParts.length === 0) {
        return intentText;
    }
    const summary = buildStructuredUserPromptSummary(intentParts);
    const preview = String(summary.preview || '').trim();
    return preview || intentText;
}

function renderRoundIntentBody(targetEl, fallbackText, intentParts) {
    if (!targetEl) {
        return;
    }
    if (targetEl.replaceChildren) {
        targetEl.replaceChildren();
    } else {
        targetEl.innerHTML = '';
    }
    if (!Array.isArray(intentParts) || intentParts.length === 0) {
        targetEl.textContent = fallbackText;
        return;
    }
    let rendered = 0;
    intentParts
        .map(userPromptItemToStructuredPart)
        .filter(Boolean)
        .forEach(structuredPart => {
            const renderedNode = appendStructuredContentPart(targetEl, structuredPart);
            if (renderedNode) {
                rendered += 1;
            }
        });
    if (rendered === 0) {
        targetEl.textContent = fallbackText;
    }
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
        .filter(section => section instanceof HTMLElement && section.offsetParent !== null);
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
        container.style.visibility = 'hidden';
        renderSessionTimeline(roundsState.currentRounds, { preserveScroll: true });
        const newHeight = container.scrollHeight;
        container.scrollTop = newHeight - oldHeight + oldTop;
        container.style.visibility = '';
    } catch (e) {
        logError(
            'frontend.rounds.load_older_failed',
            'Failed loading older rounds',
            errorToPayload(e, { session_id: state.currentSessionId }),
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

    const intentEl = section.querySelector('.round-detail-intent');
    if (intentEl) {
        intentEl.replaceWith(buildRoundIntentBlock(round.intent, round.intent_parts));
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
