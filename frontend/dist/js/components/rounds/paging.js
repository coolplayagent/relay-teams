/**
 * components/rounds/paging.js
 * Data paging helpers for session rounds.
 */
import { fetchSessionRounds } from '../../core/api.js';
import { setRunPrimaryRole, state } from '../../core/state.js';
import { roundsState } from './state.js';

export async function fetchInitialRoundsPage(sessionId, options = {}) {
    return fetchSessionRounds(sessionId, {
        limit: roundsState.pageSize,
        signal: options.signal,
    });
}

export async function fetchTimelineRoundsPage(sessionId, options = {}) {
    return fetchSessionRounds(sessionId, {
        timeline: true,
        signal: options.signal,
    });
}

export async function fetchOlderRoundsPage() {
    if (!state.currentSessionId) return null;
    return fetchSessionRounds(state.currentSessionId, {
        limit: roundsState.pageSize,
        cursorRunId: roundsState.paging.nextCursor,
    });
}

export function applyRoundPage(page, { prepend, mergeExisting = false }) {
    const rawItems = Array.isArray(page?.items) ? page.items : [];
    rawItems.forEach(item => {
        setRunPrimaryRole(item?.run_id, item?.primary_role_id || null);
    });
    const sortedItems = sortRoundsAscending(rawItems);
    const previousPaging = {
        hasMore: !!roundsState.paging?.hasMore,
        nextCursor: roundsState.paging?.nextCursor || null,
    };
    const preserveExistingPaging = !prepend
        && mergeExisting === true
        && Array.isArray(roundsState.currentRounds)
        && roundsState.currentRounds.length > 0;

    let mergedRunIds = null;
    if (!prepend && mergeExisting) {
        const byRunId = new Map();
        roundsState.currentRounds.forEach(round => {
            const runId = String(round?.run_id || '').trim();
            if (runId) byRunId.set(runId, round);
        });
        sortedItems.forEach(round => {
            const runId = String(round?.run_id || '').trim();
            if (!runId) return;
            byRunId.set(runId, {
                ...(byRunId.get(runId) || {}),
                ...round,
            });
        });
        roundsState.currentRounds = sortRoundsAscending(Array.from(byRunId.values()));
        mergedRunIds = new Set(roundsState.currentRounds.map(round => String(round?.run_id || '').trim()).filter(Boolean));
    } else if (!prepend) {
        roundsState.currentRounds = sortedItems;
    } else if (sortedItems.length > 0) {
        const existing = new Set(roundsState.currentRounds.map(r => r.run_id));
        const toAdd = sortedItems.filter(r => !existing.has(r.run_id));
        roundsState.currentRounds = [...toAdd, ...roundsState.currentRounds];
    }

    const canPreserveExistingPaging = preserveExistingPaging
        && (
            !previousPaging.nextCursor
            || (mergedRunIds instanceof Set && mergedRunIds.has(previousPaging.nextCursor))
        );
    roundsState.paging = canPreserveExistingPaging
        ? {
            ...previousPaging,
            loading: false,
        }
        : {
            hasMore: !!page?.has_more,
            nextCursor: page?.next_cursor || null,
            loading: false,
        };
}

export function applyTimelineRoundPage(page) {
    const rawItems = Array.isArray(page?.items) ? page.items : [];
    rawItems.forEach(item => {
        setRunPrimaryRole(item?.run_id, item?.primary_role_id || null);
    });
    roundsState.timelineRounds = sortRoundsAscending(rawItems);
}

export function sortRoundsAscending(rounds) {
    return (Array.isArray(rounds) ? rounds : []).slice().sort((a, b) =>
        new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
    );
}
