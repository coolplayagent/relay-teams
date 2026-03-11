/**
 * components/rounds/state.js
 * Shared state for rounds timeline modules.
 */
export const roundsState = {
    currentRounds: [],
    currentRound: null,
    scrollBound: false,
    activeRunId: null,
    activeVisibility: 0,
    pendingScrollTargetRunId: null,
    pendingScrollUnlockAt: 0,
    pageSize: 8,
    paging: {
        hasMore: false,
        nextCursor: null,
        loading: false,
    },
};
