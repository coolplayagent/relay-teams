/**
 * components/rounds/paging.js
 * Data paging helpers for session rounds.
 */
import { fetchSessionRounds } from '../../core/api.js';
import { setRunPrimaryRole, state } from '../../core/state.js';
import { roundsState } from './state.js';

export async function fetchInitialRoundsPage(sessionId) {
    return fetchSessionRounds(sessionId, { limit: roundsState.pageSize });
}

export async function fetchOlderRoundsPage() {
    if (!state.currentSessionId) return null;
    return fetchSessionRounds(state.currentSessionId, {
        limit: roundsState.pageSize,
        cursorRunId: roundsState.paging.nextCursor,
    });
}

export function applyRoundPage(page, { prepend }) {
    const rawItems = Array.isArray(page?.items) ? page.items : [];
    rawItems.forEach(item => {
        setRunPrimaryRole(item?.run_id, item?.primary_role_id || null);
    });
    const sortedItems = rawItems.slice().sort((a, b) =>
        new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
    );

    if (!prepend) {
        roundsState.currentRounds = sortedItems;
    } else if (sortedItems.length > 0) {
        const existing = new Set(roundsState.currentRounds.map(r => r.run_id));
        const toAdd = sortedItems.filter(r => !existing.has(r.run_id));
        roundsState.currentRounds = [...toAdd, ...roundsState.currentRounds];
    }

    roundsState.paging = {
        hasMore: !!page?.has_more,
        nextCursor: page?.next_cursor || null,
        loading: false,
    };
}
