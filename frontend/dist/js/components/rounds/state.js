/**
 * components/rounds/state.js
 * Shared state for rounds timeline modules.
 */
export const roundsState = {
    currentRounds: [],
    timelineRounds: [],
    currentRound: null,
    scrollBound: false,
    activeRunId: null,
    activeVisibility: 0,
    activeLockUntil: 0,
    pendingScrollTargetRunId: null,
    pendingScrollUnlockAt: 0,
    programmaticScrollUnlockAt: 0,
    suppressNavigatorFollow: false,
    pageSize: 3,
    paging: {
        hasMore: false,
        nextCursor: null,
        loading: false,
    },
};
