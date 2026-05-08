/**
 * components/sessionSidebarStore.js
 * Small local cache for sidebar session rendering.
 */

const snapshot = {
    hasData: false,
    workspaces: [],
    sessions: [],
    automationProjects: [],
};
const optimisticSessions = new Map();
const viewedTerminalRunsBySessionId = new Map();

export function rememberSidebarDataSnapshot({ workspaces, sessions, automationProjects } = {}) {
    snapshot.hasData = true;
    snapshot.workspaces = cloneRows(workspaces);
    snapshot.sessions = cloneRows(sessions);
    snapshot.automationProjects = cloneRows(automationProjects);
    trimOptimisticSessions(snapshot.sessions);
}

export function hasSidebarDataSnapshot() {
    return snapshot.hasData === true;
}

export function getSidebarDataSnapshot() {
    return {
        workspaces: cloneRows(snapshot.workspaces),
        sessions: mergeOptimisticSessions(snapshot.sessions),
        automationProjects: cloneRows(snapshot.automationProjects),
    };
}

export function upsertOptimisticSession(record) {
    const normalized = normalizeSession(record);
    if (!normalized) {
        return null;
    }
    const current = optimisticSessions.get(normalized.session_id) || {};
    const merged = mergeSessionRecords(current, normalized);
    optimisticSessions.set(normalized.session_id, merged);
    return merged;
}

export function updateOptimisticSessionTitle(sessionId, title) {
    const safeSessionId = String(sessionId || '').trim();
    const safeTitle = String(title || '').trim();
    if (!safeSessionId || !safeTitle) {
        return null;
    }
    const current = optimisticSessions.get(safeSessionId) || {
        session_id: safeSessionId,
        metadata: {},
    };
    return upsertOptimisticSession({
        ...current,
        metadata: {
            ...(current.metadata && typeof current.metadata === 'object' ? current.metadata : {}),
            title: safeTitle,
        },
        updated_at: new Date().toISOString(),
    });
}

export function markSidebarSessionTerminalViewed(sessionId) {
    const safeSessionId = String(sessionId || '').trim();
    if (!safeSessionId) {
        return null;
    }
    const current = optimisticSessions.get(safeSessionId) || findStoredSession(safeSessionId) || {
        session_id: safeSessionId,
        metadata: {},
    };
    viewedTerminalRunsBySessionId.set(
        safeSessionId,
        String(current.latest_terminal_run_id || current.latestTerminalRunId || '').trim(),
    );
    return upsertOptimisticSession(applyViewedTerminalState(current));
}

export function markSidebarSessionRunActive(sessionId, { runId = '', status = 'running' } = {}) {
    const safeSessionId = String(sessionId || '').trim();
    if (!safeSessionId) {
        return null;
    }
    const current = optimisticSessions.get(safeSessionId) || findStoredSession(safeSessionId) || {
        session_id: safeSessionId,
        metadata: {},
    };
    const activeRunId = String(
        runId
        || current.active_run_id
        || current.activeRunId
        || '',
    ).trim();
    const activeStatus = String(status || 'running').trim() || 'running';
    return upsertOptimisticSession({
        ...current,
        has_active_run: true,
        hasActiveRun: true,
        active_run_id: activeRunId,
        activeRunId: activeRunId,
        active_run_status: activeStatus,
        activeRunStatus: activeStatus,
        has_unread_terminal_run: false,
        hasUnreadTerminalRun: false,
    });
}

export function markSidebarSessionRunTerminal(
    sessionId,
    { runId = '', status = '', viewed = false } = {},
) {
    const safeSessionId = String(sessionId || '').trim();
    if (!safeSessionId) {
        return null;
    }
    const current = optimisticSessions.get(safeSessionId) || findStoredSession(safeSessionId) || {
        session_id: safeSessionId,
        metadata: {},
    };
    const terminalRunId = String(
        runId
        || current.active_run_id
        || current.activeRunId
        || current.latest_terminal_run_id
        || current.latestTerminalRunId
        || '',
    ).trim();
    const terminalStatus = String(status || current.active_run_status || current.activeRunStatus || '').trim();
    const isViewed = viewed === true;
    if (isViewed && terminalRunId) {
        viewedTerminalRunsBySessionId.set(safeSessionId, terminalRunId);
    }
    return upsertOptimisticSession({
        ...current,
        has_active_run: false,
        hasActiveRun: false,
        has_unread_terminal_run: !isViewed,
        hasUnreadTerminalRun: !isViewed,
        active_run_id: '',
        activeRunId: '',
        active_run_status: '',
        activeRunStatus: '',
        latest_terminal_run_id: terminalRunId || current.latest_terminal_run_id || current.latestTerminalRunId || '',
        latestTerminalRunId: terminalRunId || current.latestTerminalRunId || current.latest_terminal_run_id || '',
        latest_terminal_run_status: terminalStatus || current.latest_terminal_run_status || current.latestTerminalRunStatus || '',
        latestTerminalRunStatus: terminalStatus || current.latestTerminalRunStatus || current.latest_terminal_run_status || '',
    });
}

export function mergeOptimisticSessions(sessions) {
    const byId = new Map();
    cloneRows(sessions).forEach(session => {
        const sessionId = String(session?.session_id || '').trim();
        if (sessionId) {
            byId.set(sessionId, applyViewedTerminalState(session));
        }
    });
    optimisticSessions.forEach((optimistic, sessionId) => {
        const current = byId.get(sessionId) || {};
        byId.set(sessionId, applyViewedTerminalState(mergeSessionRecords(optimistic, current)));
    });
    return Array.from(byId.values());
}

function trimOptimisticSessions(sessions) {
    const persistedById = new Map();
    cloneRows(sessions).forEach(session => {
        const sessionId = String(session?.session_id || '').trim();
        if (sessionId) {
            persistedById.set(sessionId, session);
        }
    });
    optimisticSessions.forEach((optimistic, sessionId) => {
        const persisted = persistedById.get(sessionId);
        if (!persisted) {
            optimisticSessions.delete(sessionId);
            viewedTerminalRunsBySessionId.delete(sessionId);
            return;
        }
        const persistedTitle = String(persisted?.metadata?.title || '').trim();
        if (viewedTerminalRunsBySessionId.has(sessionId)) {
            return;
        }
        if (shouldKeepTerminalOverride(optimistic, persisted)) {
            return;
        }
        if (persistedTitle) {
            optimisticSessions.delete(sessionId);
        }
    });
}

function findStoredSession(sessionId) {
    const safeSessionId = String(sessionId || '').trim();
    if (!safeSessionId) {
        return null;
    }
    return cloneRows(snapshot.sessions).find(
        session => String(session?.session_id || '').trim() === safeSessionId,
    ) || null;
}

function normalizeSession(record) {
    if (!record || typeof record !== 'object') {
        return null;
    }
    const sessionId = String(record.session_id || record.sessionId || '').trim();
    if (!sessionId) {
        return null;
    }
    const workspaceId = String(record.workspace_id || record.workspaceId || '').trim();
    const now = new Date().toISOString();
    return {
        ...record,
        session_id: sessionId,
        workspace_id: workspaceId,
        metadata: record.metadata && typeof record.metadata === 'object'
            ? { ...record.metadata }
            : {},
        created_at: String(record.created_at || record.createdAt || now).trim(),
        updated_at: String(record.updated_at || record.updatedAt || now).trim(),
    };
}

function mergeSessionRecords(preferred, current) {
    const preferredRecord = normalizeSession(preferred) || {};
    const currentRecord = normalizeSession(current) || {};
    const sessionId = String(currentRecord.session_id || preferredRecord.session_id || '').trim();
    const activeOverride = activeOverrideForMerge(preferredRecord, currentRecord);
    const terminalOverride = terminalOverrideForMerge(preferredRecord, currentRecord);
    const preferredMetadata = preferredRecord.metadata && typeof preferredRecord.metadata === 'object'
        ? preferredRecord.metadata
        : {};
    const currentMetadata = currentRecord.metadata && typeof currentRecord.metadata === 'object'
        ? currentRecord.metadata
        : {};
    const preferredTitle = String(preferredMetadata.title || '').trim();
    const currentTitle = String(currentMetadata.title || '').trim();
    const preferredUpdatedAt = String(preferredRecord.updated_at || '').trim();
    const currentUpdatedAt = String(currentRecord.updated_at || '').trim();
    const preferOptimisticTitle = !!(
        preferredTitle
        && (
            !currentTitle
            || timestampValue(preferredUpdatedAt) >= timestampValue(currentUpdatedAt)
        )
    );
    const updatedAt = timestampValue(preferredUpdatedAt) > timestampValue(currentUpdatedAt)
        ? preferredUpdatedAt
        : currentUpdatedAt || preferredUpdatedAt;
    return {
        ...preferredRecord,
        ...currentRecord,
        ...activeOverride,
        ...terminalOverride,
        session_id: sessionId,
        workspace_id: String(currentRecord.workspace_id || preferredRecord.workspace_id || '').trim(),
        metadata: {
            ...preferredMetadata,
            ...currentMetadata,
            ...(preferOptimisticTitle ? { title: preferredTitle } : {}),
        },
        updated_at: updatedAt || new Date().toISOString(),
        created_at: String(currentRecord.created_at || preferredRecord.created_at || '').trim(),
    };
}

function activeOverrideForMerge(preferredRecord, currentRecord) {
    const preferredActiveRunId = String(
        preferredRecord.active_run_id || preferredRecord.activeRunId || '',
    ).trim();
    const hasPreferredActiveRun = (
        (preferredRecord.has_active_run || preferredRecord.hasActiveRun)
        && preferredActiveRunId
    );
    if (!hasPreferredActiveRun) {
        return {};
    }
    const currentTerminalRunId = String(
        currentRecord.latest_terminal_run_id || currentRecord.latestTerminalRunId || '',
    ).trim();
    const currentTerminalStatus = String(
        currentRecord.latest_terminal_run_status
        || currentRecord.latestTerminalRunStatus
        || '',
    ).trim().toLowerCase();
    if (
        currentTerminalRunId === preferredActiveRunId
        && ['completed', 'failed', 'stopped'].includes(currentTerminalStatus)
    ) {
        return {};
    }
    const activeStatus = String(
        preferredRecord.active_run_status || preferredRecord.activeRunStatus || 'running',
    ).trim() || 'running';
    return {
        has_active_run: true,
        hasActiveRun: true,
        active_run_id: preferredActiveRunId,
        activeRunId: preferredActiveRunId,
        active_run_status: activeStatus,
        activeRunStatus: activeStatus,
        has_unread_terminal_run: false,
        hasUnreadTerminalRun: false,
    };
}

function terminalOverrideForMerge(preferredRecord, currentRecord) {
    const preferredTerminalRunId = String(
        preferredRecord.latest_terminal_run_id || preferredRecord.latestTerminalRunId || '',
    ).trim();
    if (!preferredTerminalRunId) {
        return {};
    }
    const currentTerminalRunId = String(
        currentRecord.latest_terminal_run_id || currentRecord.latestTerminalRunId || '',
    ).trim();
    if (currentTerminalRunId && currentTerminalRunId !== preferredTerminalRunId) {
        return {};
    }
    const preferredStatus = String(
        preferredRecord.latest_terminal_run_status
        || preferredRecord.latestTerminalRunStatus
        || '',
    ).trim();
    const hasUnreadTerminalRun = preferredRecord.has_unread_terminal_run !== false
        && preferredRecord.hasUnreadTerminalRun !== false;
    return {
        has_active_run: false,
        hasActiveRun: false,
        active_run_id: '',
        activeRunId: '',
        active_run_status: '',
        activeRunStatus: '',
        has_unread_terminal_run: hasUnreadTerminalRun,
        hasUnreadTerminalRun: hasUnreadTerminalRun,
        latest_terminal_run_id: preferredTerminalRunId,
        latestTerminalRunId: preferredTerminalRunId,
        latest_terminal_run_status: preferredStatus
            || currentRecord.latest_terminal_run_status
            || currentRecord.latestTerminalRunStatus
            || '',
        latestTerminalRunStatus: preferredStatus
            || currentRecord.latestTerminalRunStatus
            || currentRecord.latest_terminal_run_status
            || '',
    };
}

function shouldKeepTerminalOverride(optimistic, persisted) {
    const terminalRunId = String(
        optimistic?.latest_terminal_run_id || optimistic?.latestTerminalRunId || '',
    ).trim();
    if (!terminalRunId) {
        return false;
    }
    const persistedTerminalRunId = String(
        persisted?.latest_terminal_run_id || persisted?.latestTerminalRunId || '',
    ).trim();
    if (persistedTerminalRunId) {
        return persistedTerminalRunId === terminalRunId;
    }
    const activeRunId = String(
        persisted?.active_run_id || persisted?.activeRunId || '',
    ).trim();
    return !activeRunId || activeRunId === terminalRunId;
}

function applyViewedTerminalState(record) {
    const normalized = normalizeSession(record);
    if (!normalized) {
        return record;
    }
    if (!viewedTerminalRunsBySessionId.has(normalized.session_id)) {
        return normalized;
    }
    const viewedRunId = String(viewedTerminalRunsBySessionId.get(normalized.session_id) || '').trim();
    const latestRunId = String(
        normalized.latest_terminal_run_id || normalized.latestTerminalRunId || '',
    ).trim();
    if (latestRunId && viewedRunId && latestRunId !== viewedRunId) {
        viewedTerminalRunsBySessionId.delete(normalized.session_id);
        return normalized;
    }
    if (latestRunId && !viewedRunId && normalized.has_unread_terminal_run === true) {
        viewedTerminalRunsBySessionId.delete(normalized.session_id);
        return normalized;
    }
    return {
        ...normalized,
        has_unread_terminal_run: false,
        hasUnreadTerminalRun: false,
    };
}

function cloneRows(rows) {
    return Array.isArray(rows) ? rows.map(row => cloneRecord(row)) : [];
}

function cloneRecord(row) {
    if (!row || typeof row !== 'object') {
        return row;
    }
    return {
        ...row,
        metadata: row.metadata && typeof row.metadata === 'object'
            ? { ...row.metadata }
            : row.metadata,
    };
}

function timestampValue(value) {
    const parsed = Date.parse(String(value || ''));
    return Number.isNaN(parsed) ? 0 : parsed;
}
