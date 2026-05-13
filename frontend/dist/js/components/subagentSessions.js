/**
 * components/subagentSessions.js
 * Normal-mode subagent child-session cache, navigation, and read-only view.
 */
import { fetchSessionSubagents } from '../core/api.js';
import {
    abortMainSessionRestore,
    restoreMainSessionView,
} from '../app/sessionView.js';
import { syncNormalModeSubagentStreams } from '../core/stream.js';
import { clearAllPanels } from './agentPanel.js';
import { renderInstanceHistoryInto } from './agentPanel/history.js';
import { hideRoundNavigator } from './rounds/navigator.js';
import { getRoleDisplayName, state } from '../core/state.js';
import { els } from '../utils/dom.js';
import { t } from '../utils/i18n.js';
import { sysLog } from '../utils/logger.js';

const subagentSessionsBySessionId = new Map();
const loadingSessionIds = new Set();
const loadingPromisesBySessionId = new Map();
const expandedParentSessionIds = new Set();
const terminalRefreshTimers = new Map();
const statusRefreshTimers = new Map();
const recentParentStopCandidateTimestampsBySession = new Map();
const queuedSubagentSessionLoads = [];
const parentStoppedSessionIds = new Set();
const parentStoppedSubagentInstanceIdsBySession = new Map();
let activeSubagentRenderSequence = 0;
let activeSubagentRenderController = null;
let subagentSessionsChangedFrame = 0;
const RECENT_PARENT_STOP_CANDIDATE_WINDOW_MS = 5000;
let pendingSubagentSessionsChangedDetail = null;
let activeSubagentSessionLoadCount = 0;
const MAX_PARALLEL_SUBAGENT_SESSION_LOADS = 2;
const TERMINAL_REFRESH_DELAYS_MS = [120, 250, 500, 900, 1400];

export function getSessionSubagentSessions(sessionId) {
    return [...(subagentSessionsBySessionId.get(String(sessionId || '').trim()) || [])];
}

export function hasLoadedSessionSubagents(sessionId) {
    return subagentSessionsBySessionId.has(String(sessionId || '').trim());
}

export function isSubagentSessionListExpanded(sessionId) {
    return expandedParentSessionIds.has(String(sessionId || '').trim());
}

export function isSubagentSessionListLoading(sessionId) {
    return loadingSessionIds.has(String(sessionId || '').trim());
}

export function getActiveSubagentSession() {
    return state.activeSubagentSession && typeof state.activeSubagentSession === 'object'
        ? state.activeSubagentSession
        : null;
}

export function getNormalModeSubagentSessionByRunId(sessionId, runId) {
    const safeSessionId = String(sessionId || '').trim();
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) {
        return null;
    }
    const active = getActiveSubagentSession();
    if (
        active
        && active.runId === safeRunId
        && (!safeSessionId || active.sessionId === safeSessionId)
    ) {
        return active;
    }
    const sessionIds = safeSessionId
        ? [safeSessionId]
        : Array.from(subagentSessionsBySessionId.keys());
    for (const currentSessionId of sessionIds) {
        const match = getSessionSubagentSessions(currentSessionId)
            .find(item => item.runId === safeRunId);
        if (match) {
            return match;
        }
    }
    return null;
}

export function isActiveSubagentSession(sessionId, instanceId) {
    const active = getActiveSubagentSession();
    return !!(
        active
        && active.sessionId === String(sessionId || '').trim()
        && active.instanceId === String(instanceId || '').trim()
    );
}

export function clearActiveSubagentSession({ abortMainReturn = true } = {}) {
    activeSubagentRenderSequence += 1;
    if (activeSubagentRenderController) {
        activeSubagentRenderController.abort();
        activeSubagentRenderController = null;
    }
    if (abortMainReturn) {
        abortMainSessionRestore();
    }
    cancelTerminalRefreshForInstance(getActiveSubagentSession()?.instanceId || '');
    state.activeSubagentSession = null;
    state.activeView = 'main';
    setSubagentSessionChromeActive(false);
    setMainComposerVisible(true);
    if (!state.isGenerating) {
        if (els.promptInput) {
            els.promptInput.disabled = false;
        }
        if (els.sendBtn) {
            els.sendBtn.disabled = false;
        }
    }
    if (els.promptInputHint) {
        els.promptInputHint.textContent = '';
    }
}

export async function ensureSessionSubagents(
    sessionId,
    { force = false, emitLoadingEvents = true, signal = null } = {},
) {
    const safeSessionId = String(sessionId || '').trim();
    if (!safeSessionId) {
        return [];
    }
    if (!force && subagentSessionsBySessionId.has(safeSessionId)) {
        return getSessionSubagentSessions(safeSessionId);
    }
    if (loadingSessionIds.has(safeSessionId)) {
        const pending = loadingPromisesBySessionId.get(safeSessionId);
        if (pending) {
            return pending;
        }
        return getSessionSubagentSessions(safeSessionId);
    }
    throwIfAborted(signal);
    loadingSessionIds.add(safeSessionId);
    if (emitLoadingEvents) {
        emitSubagentSessionsChanged({
            forceRefresh: false,
            reason: 'loading',
            sessionId: safeSessionId,
        });
    }
    const loadPromise = (async () => {
        const payload = await runQueuedSubagentSessionLoad(
            () => fetchSessionSubagents(safeSessionId, {
                forceRefresh: force === true,
                signal,
            }),
            signal,
        );
        throwIfAborted(signal);
        replaceSessionSubagents(safeSessionId, payload, { emitChange: false });
        return getSessionSubagentSessions(safeSessionId);
    })();
    loadingPromisesBySessionId.set(safeSessionId, loadPromise);
    try {
        return await loadPromise;
    } catch (error) {
        if (error?.name !== 'AbortError') {
            sysLog(`Failed to load subagent sessions: ${error.message || error}`, 'log-error');
        }
        return getSessionSubagentSessions(safeSessionId);
    } finally {
        loadingPromisesBySessionId.delete(safeSessionId);
        const wasLoading = loadingSessionIds.delete(safeSessionId);
        if (emitLoadingEvents && wasLoading) {
            emitSubagentSessionsChanged({
                forceRefresh: false,
                reason: 'structure',
                sessionId: safeSessionId,
            });
        }
    }
}

function runQueuedSubagentSessionLoad(operation, signal) {
    throwIfAborted(signal);
    if (activeSubagentSessionLoadCount < MAX_PARALLEL_SUBAGENT_SESSION_LOADS) {
        return startSubagentSessionLoad(operation, signal);
    }
    return new Promise((resolve, reject) => {
        const entry = {
            operation,
            signal,
            resolve,
            reject,
            abortHandler: null,
        };
        entry.abortHandler = () => {
            const index = queuedSubagentSessionLoads.indexOf(entry);
            if (index >= 0) {
                queuedSubagentSessionLoads.splice(index, 1);
            }
            reject(new DOMException('The operation was aborted.', 'AbortError'));
        };
        if (signal) {
            signal.addEventListener('abort', entry.abortHandler, { once: true });
        }
        queuedSubagentSessionLoads.push(entry);
        drainSubagentSessionLoadQueue();
    });
}

async function startSubagentSessionLoad(operation, signal) {
    throwIfAborted(signal);
    activeSubagentSessionLoadCount += 1;
    try {
        return await operation();
    } finally {
        activeSubagentSessionLoadCount = Math.max(0, activeSubagentSessionLoadCount - 1);
        drainSubagentSessionLoadQueue();
    }
}

function drainSubagentSessionLoadQueue() {
    while (
        activeSubagentSessionLoadCount < MAX_PARALLEL_SUBAGENT_SESSION_LOADS
        && queuedSubagentSessionLoads.length > 0
    ) {
        const entry = queuedSubagentSessionLoads.shift();
        if (!entry) {
            continue;
        }
        if (entry.signal && entry.abortHandler) {
            entry.signal.removeEventListener('abort', entry.abortHandler);
        }
        startSubagentSessionLoad(entry.operation, entry.signal)
            .then(entry.resolve)
            .catch(entry.reject);
    }
}

function throwIfAborted(signal) {
    if (signal?.aborted) {
        throw new DOMException('The operation was aborted.', 'AbortError');
    }
}

export function toggleSubagentSessionList(
    sessionId,
    { emitChange = true, load = true } = {},
) {
    const safeSessionId = String(sessionId || '').trim();
    if (!safeSessionId) {
        return;
    }
    if (expandedParentSessionIds.has(safeSessionId)) {
        expandedParentSessionIds.delete(safeSessionId);
        if (emitChange) {
            emitSubagentSessionsChanged({
                forceRefresh: false,
                reason: 'visibility',
                sessionId: safeSessionId,
            });
        }
        return;
    }
    expandedParentSessionIds.add(safeSessionId);
    if (load) {
        void ensureSessionSubagents(safeSessionId, { force: false });
    }
    if (emitChange) {
        emitSubagentSessionsChanged({
            forceRefresh: false,
            reason: 'visibility',
            sessionId: safeSessionId,
        });
    }
}

export function replaceSessionSubagents(
    sessionId,
    payload,
    { emitChange = true } = {},
) {
    const safeSessionId = String(sessionId || '').trim();
    if (!safeSessionId) {
        return [];
    }
    const normalized = normalizeSubagentSessions(payload, safeSessionId)
        .map(item => coerceParentStoppedSubagentSession(item));
    applySessionSubagentRecords(safeSessionId, normalized, { emitChange });
    return getSessionSubagentSessions(safeSessionId);
}

export function rememberNormalModeSubagentSession(sessionId, record) {
    const safeSessionId = String(sessionId || '').trim();
    const normalized = coerceParentStoppedSubagentSession(
        normalizeSubagentSession(record, safeSessionId),
    );
    if (!safeSessionId || normalized === null) {
        return false;
    }
    const current = getSessionSubagentSessions(safeSessionId);
    const next = upsertSubagentSessionRecord(current, normalized);
    applySessionSubagentRecords(safeSessionId, next);
    return true;
}

export function rememberOrchestrationSubagentSession(sessionId, record) {
    const safeSessionId = String(sessionId || record?.session_id || record?.sessionId || '').trim();
    const normalized = normalizeSubagentSession({
        ...record,
        subagent_kind: 'orchestration',
        interactive: true,
        deletable: false,
        updated_at: record?.updated_at || record?.updatedAt || new Date().toISOString(),
    }, safeSessionId);
    if (!safeSessionId || normalized === null) {
        return false;
    }
    const current = getSessionSubagentSessions(safeSessionId);
    const next = upsertSubagentSessionRecord(current, normalized);
    applySessionSubagentRecords(safeSessionId, next);
    return true;
}

export function rememberNormalModeSubagentFromBackgroundTask(sessionId, payload, eventType = '') {
    const safeSessionId = String(sessionId || payload?.session_id || payload?.sessionId || '').trim();
    if (!safeSessionId || !isSubagentBackgroundTask(payload)) {
        return false;
    }
    const normalized = coerceParentStoppedSubagentSession(normalizeSubagentSession({
        ...payload,
        status: backgroundTaskStatusForEvent(payload, eventType),
        updated_at: payload?.updated_at || payload?.updatedAt || new Date().toISOString(),
    }, safeSessionId));
    if (normalized === null) {
        return false;
    }
    const current = getSessionSubagentSessions(safeSessionId);
    const next = upsertSubagentSessionRecord(current, normalized);
    applySessionSubagentRecords(safeSessionId, next);
    return true;
}

export function updateNormalModeSubagentSessionStatus(sessionId, instanceId, status) {
    const safeSessionId = String(sessionId || '').trim();
    const safeInstanceId = String(instanceId || '').trim();
    if (!safeSessionId || !safeInstanceId) {
        return;
    }
    const normalizedStatus = normalizeSubagentRunStatus(status);
    const current = getSessionSubagentSessions(safeSessionId);
    let changed = false;
    let nextStatus = '';
    const next = current.map(item => (
        item.instanceId === safeInstanceId
            ? (() => {
                nextStatus = normalizedStatus || item.status || 'idle';
                if (item.status === nextStatus && item.runStatus === nextStatus) {
                    return item;
                }
                changed = true;
                return {
                    ...item,
                    status: nextStatus,
                    runStatus: nextStatus,
                };
            })()
            : item
    ));
    const active = getActiveSubagentSession();
    if (
        active
        && active.sessionId === safeSessionId
        && active.instanceId === safeInstanceId
        && (active.status !== normalizedStatus || active.runStatus !== normalizedStatus)
    ) {
        state.activeSubagentSession = {
            ...active,
            status: normalizedStatus,
            runStatus: normalizedStatus,
        };
        syncSubagentSessionViewChrome(state.activeSubagentSession);
    }
    if (!changed) {
        if (active && active.sessionId === safeSessionId && active.instanceId === safeInstanceId) {
            emitSubagentSessionStatusChanged(safeSessionId, safeInstanceId, normalizedStatus);
        }
        return;
    }
    applySessionSubagentRecords(safeSessionId, next, { emitChange: false });
    emitSubagentSessionStatusChanged(safeSessionId, safeInstanceId, nextStatus || normalizedStatus);
}

export function updateNormalModeSubagentSessionStatusByRunId(sessionId, runId, status) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) {
        return false;
    }
    const match = getNormalModeSubagentSessionByRunId(sessionId, safeRunId);
    if (!match) {
        return false;
    }
    updateNormalModeSubagentSessionStatus(
        match.sessionId || sessionId,
        match.instanceId,
        status,
    );
    return true;
}

export function applySubagentSessionStatusEvent(payload, eventMeta = null) {
    const safeSessionId = String(
        payload?.parent_session_id
        || payload?.parentSessionId
        || payload?.session_id
        || payload?.sessionId
        || eventMeta?.session_id
        || eventMeta?.sessionId
        || state.currentSessionId
        || '',
    ).trim();
    if (!safeSessionId || !payload || typeof payload !== 'object') {
        return false;
    }
    const normalized = coerceParentStoppedSubagentSession(normalizeSubagentSession({
        ...payload,
        session_id: safeSessionId,
        run_id: payload.subagent_run_id || payload.subagentRunId || payload.run_id || payload.runId,
        status: payload.status || payload.run_status || payload.runStatus || 'idle',
        run_status: payload.run_status || payload.runStatus || payload.status || 'idle',
        updated_at: payload.updated_at || payload.updatedAt || new Date().toISOString(),
    }, safeSessionId));
    if (normalized === null) {
        return false;
    }
    const current = getSessionSubagentSessions(safeSessionId);
    const nextRecord = mergeSubagentSessionStatusRecord(current, normalized, payload);
    const next = upsertSubagentSessionRecord(current, nextRecord);
    applySessionSubagentRecords(safeSessionId, next, { emitChange: false });
    emitSubagentSessionStatusChanged(
        safeSessionId,
        nextRecord.instanceId,
        nextRecord.status,
    );
    updateRecentParentStopCandidateFromStatusEvent(
        safeSessionId,
        nextRecord,
        payload,
        eventMeta,
    );
    scheduleSubagentSessionStatusRefresh(safeSessionId);
    return true;
}

export function markNormalModeSubagentSessionsStoppedForParent(sessionId) {
    const safeSessionId = String(sessionId || '').trim();
    if (!safeSessionId) {
        return [];
    }
    parentStoppedSessionIds.add(safeSessionId);
    const recentlyStopped = collectRecentParentStopCandidateInstanceIds(safeSessionId);
    const changed = updateNormalModeSubagentSessionsMatching(
        safeSessionId,
        item => isRunningSubagentSessionStatus(item.status),
        'stopped',
    );
    const stoppedByParent = new Set([
        ...(parentStoppedSubagentInstanceIdsBySession.get(safeSessionId) || []),
        ...recentlyStopped,
        ...changed,
    ]);
    parentStoppedSubagentInstanceIdsBySession.set(safeSessionId, stoppedByParent);
    return Array.from(stoppedByParent);
}

export function clearNormalModeSubagentParentStopState(sessionId) {
    const safeSessionId = String(sessionId || '').trim();
    if (!safeSessionId) {
        return;
    }
    parentStoppedSessionIds.delete(safeSessionId);
    parentStoppedSubagentInstanceIdsBySession.delete(safeSessionId);
    recentParentStopCandidateTimestampsBySession.delete(safeSessionId);
}

export function markNormalModeSubagentSessionsRunningForParent(sessionId) {
    const safeSessionId = String(sessionId || '').trim();
    if (!safeSessionId) {
        return [];
    }
    const stoppedByParent = parentStoppedSubagentInstanceIdsBySession.get(safeSessionId);
    const changed = updateNormalModeSubagentSessionsMatching(
        safeSessionId,
        item => (
            isStoppedSubagentSessionStatus(item.status)
            && (
                stoppedByParent === undefined
                || stoppedByParent.has(item.instanceId)
            )
        ),
        'running',
    );
    clearNormalModeSubagentParentStopState(safeSessionId);
    return changed;
}

export function removeSessionSubagent(sessionId, instanceId) {
    const safeSessionId = String(sessionId || '').trim();
    const safeInstanceId = String(instanceId || '').trim();
    if (!safeSessionId || !safeInstanceId) {
        return null;
    }
    const current = getSessionSubagentSessions(safeSessionId);
    const removed = current.find(item => item.instanceId === safeInstanceId) || null;
    if (removed === null) {
        return null;
    }
    const next = current.filter(item => item.instanceId !== safeInstanceId);
    applySessionSubagentRecords(safeSessionId, next);
    const active = getActiveSubagentSession();
    if (
        active
        && active.sessionId === safeSessionId
        && active.instanceId === safeInstanceId
    ) {
        clearActiveSubagentSession();
    }
    return removed;
}

function updateNormalModeSubagentSessionsMatching(sessionId, predicate, status) {
    const safeSessionId = String(sessionId || '').trim();
    if (!safeSessionId || typeof predicate !== 'function') {
        return [];
    }
    const normalizedStatus = normalizeSubagentRunStatus(status);
    const current = getSessionSubagentSessions(safeSessionId);
    const changedInstanceIds = [];
    const next = current.map(item => {
        if (!predicate(item)) {
            return item;
        }
        if (item.status === normalizedStatus && item.runStatus === normalizedStatus) {
            return item;
        }
        changedInstanceIds.push(item.instanceId);
        return {
            ...item,
            status: normalizedStatus,
            runStatus: normalizedStatus,
        };
    });
    const active = getActiveSubagentSession();
    const activeShouldChange = !!(
        active
        && active.sessionId === safeSessionId
        && predicate(active)
        && (active.status !== normalizedStatus || active.runStatus !== normalizedStatus)
    );
    if (
        activeShouldChange
        || (
            active
            && active.sessionId === safeSessionId
            && changedInstanceIds.includes(active.instanceId)
        )
    ) {
        state.activeSubagentSession = {
            ...active,
            status: normalizedStatus,
            runStatus: normalizedStatus,
        };
        syncSubagentSessionViewChrome(state.activeSubagentSession);
    }
    if (changedInstanceIds.length === 0) {
        if (activeShouldChange) {
            emitSubagentSessionStatusChanged(safeSessionId, active.instanceId, normalizedStatus);
            return [active.instanceId];
        }
        return [];
    }
    applySessionSubagentRecords(safeSessionId, next, { emitChange: false });
    changedInstanceIds.forEach(instanceId => {
        emitSubagentSessionStatusChanged(safeSessionId, instanceId, normalizedStatus);
    });
    if (activeShouldChange && !changedInstanceIds.includes(active.instanceId)) {
        emitSubagentSessionStatusChanged(safeSessionId, active.instanceId, normalizedStatus);
        changedInstanceIds.push(active.instanceId);
    }
    return changedInstanceIds;
}

export async function openSubagentSession(sessionId, record) {
    const safeSessionId = String(sessionId || '').trim();
    const normalized = coerceParentStoppedSubagentSession(
        normalizeSubagentSession(record, safeSessionId),
    );
    if (!safeSessionId || normalized === null) {
        return;
    }
    abortMainSessionRestore();
    state.activeSubagentSession = normalized;
    state.activeView = 'subagent-session';
    state.activeAgentRoleId = normalized.roleId;
    state.activeAgentInstanceId = normalized.instanceId;
    setSubagentSessionChromeActive(true);
    clearAllPanels();
    hideRoundNavigator();
    setMainComposerVisible(false);
    if (els.promptInput) {
        els.promptInput.disabled = true;
    }
    if (els.sendBtn) {
        els.sendBtn.disabled = true;
    }
    if (els.promptInputHint) {
        els.promptInputHint.textContent = t('subagent_session.read_only');
    }
    cancelTerminalRefreshForInstance(normalized.instanceId);
    await renderActiveSubagentSession({ showLoading: true });
}

export async function returnToMainSessionView() {
    const sessionId = String(state.currentSessionId || '').trim();
    if (!sessionId) {
        clearActiveSubagentSession();
        return;
    }
    clearActiveSubagentSession({ abortMainReturn: false });
    await restoreMainSessionView(sessionId, { quiet: true });
}

export async function renderActiveSubagentSession(options = {}) {
    const active = getActiveSubagentSession();
    if (!active || !els.chatMessages) {
        return { rendered: false, deferred: false };
    }
    const currentSessionId = String(state.currentSessionId || '').trim();
    if (!currentSessionId || currentSessionId !== active.sessionId) {
        return { rendered: false, deferred: false };
    }
    const showLoading = options.showLoading === true;
    const requestId = ++activeSubagentRenderSequence;
    if (activeSubagentRenderController) {
        activeSubagentRenderController.abort();
    }
    const renderController = new AbortController();
    activeSubagentRenderController = renderController;
    hideRoundNavigator();
    const body = ensureSubagentSessionView(active, { showLoading });
    if (!body || typeof body !== 'object' || !('innerHTML' in body)) {
        if (activeSubagentRenderController === renderController) {
            activeSubagentRenderController = null;
        }
        return { rendered: false, deferred: false };
    }
    try {
        if (!isStillActiveSubagentRender(active, requestId)) {
            return { rendered: false, deferred: false };
        }
        const result = await renderInstanceHistoryInto(body, {
            sessionId: active.sessionId,
            instanceId: active.instanceId,
            runId: active.runId,
            roleId: active.roleId,
            status: active.status,
            runStatus: active.runStatus,
            runPhase: active.runPhase,
            userRoleLabel: t('subagent.task_prompt'),
            emptyLabel: t('subagent_session.empty'),
            loadFailedLabel: t('subagent_session.load_failed'),
            overlayMode: 'separate',
            requireToolBoundary: options.requireToolBoundary === true,
            replaceWhenReady: true,
            signal: renderController.signal,
        });
        if (!isStillActiveSubagentRender(active, requestId)) {
            return { rendered: false, deferred: false };
        }
        return {
            rendered: result?.deferred !== true,
            deferred: result?.deferred === true,
        };
    } catch (error) {
        if (error?.name === 'AbortError') {
            return { rendered: false, deferred: false };
        }
        sysLog(`Failed to load subagent session: ${error.message || error}`, 'log-error');
        return { rendered: false, deferred: false };
    } finally {
        if (isStillActiveSubagentRender(active, requestId)) {
            setSubagentSessionLoading(active.instanceId, false);
        }
        if (activeSubagentRenderController === renderController) {
            activeSubagentRenderController = null;
        }
    }
}

export function settleActiveSubagentSessionAfterTerminal(instanceId) {
    const safeInstanceId = String(instanceId || '').trim();
    if (!safeInstanceId) {
        return;
    }
    cancelTerminalRefreshForInstance(safeInstanceId);
    void runTerminalRefreshAttempt(safeInstanceId, 0);
}

export function getActiveSubagentSessionStreamContainer(instanceId) {
    const active = getActiveSubagentSession();
    const safeInstanceId = String(instanceId || '').trim();
    if (!active || active.instanceId !== safeInstanceId) {
        return null;
    }
    return els.chatMessages?.querySelector?.('.subagent-session-body') || null;
}

export function buildSubagentSessionLabel(record) {
    const roleId = String(record?.roleId || record?.role_id || '').trim();
    const instanceId = String(record?.instanceId || record?.instance_id || '').trim();
    const roleLabel = getRoleDisplayName(roleId, { fallback: roleId || 'Agent' });
    return `${roleLabel} - ${shortInstanceId(instanceId)}`;
}

function syncActiveSubagentSessionFromCache(sessionId) {
    const active = getActiveSubagentSession();
    if (!active || active.sessionId !== sessionId) {
        return;
    }
    const current = getSessionSubagentSessions(sessionId).find(
        item => item.instanceId === active.instanceId,
    );
    if (current) {
        state.activeSubagentSession = current;
        syncSubagentSessionViewChrome(current);
    }
}

function applySessionSubagentRecords(
    sessionId,
    rows,
    { emitChange = true } = {},
) {
      const nextRows = Array.isArray(rows) ? rows : [];
      const previousRows = getSessionSubagentSessions(sessionId);
      const listChanged = !areSubagentSessionListsEqual(previousRows, nextRows);
      const structureChanged = !areSubagentSessionStructuresEqual(previousRows, nextRows);
      subagentSessionsBySessionId.set(sessionId, nextRows);
      syncNormalModeSubagentStreams(
          sessionId,
          getSessionSubagentSessions(sessionId).filter(item => item.subagentKind === 'normal'),
      );
      syncActiveSubagentSessionFromCache(sessionId);
    if (emitChange && structureChanged) {
        emitSubagentSessionsChanged({
            forceRefresh: false,
            reason: 'structure',
            sessionId,
        });
    } else if (emitChange && listChanged) {
        emitSubagentSessionStatusChanged(sessionId, '', 'updated');
    }
}

function normalizeSubagentSessions(payload, sessionId) {
    const rows = Array.isArray(payload) ? payload : [];
    return rows
        .map(item => normalizeSubagentSession(item, sessionId))
        .filter(item => item !== null)
        .sort((left, right) => String(right.updatedAt || '').localeCompare(String(left.updatedAt || '')));
}

function coerceParentStoppedSubagentSession(record) {
    if (!record || typeof record !== 'object') {
        return record;
    }
    const safeSessionId = String(record.sessionId || record.session_id || '').trim();
    if (!parentStoppedSessionIds.has(safeSessionId)) {
        return record;
    }
    if (
        !isRunningSubagentSessionStatus(record.status)
        && !isRunningSubagentSessionStatus(record.runStatus || record.run_status)
    ) {
        return record;
    }
    rememberParentStoppedSubagentInstance(safeSessionId, record.instanceId || record.instance_id);
    return {
        ...record,
        status: 'stopped',
        runStatus: 'stopped',
    };
}

function rememberParentStoppedSubagentInstance(sessionId, instanceId) {
    const safeSessionId = String(sessionId || '').trim();
    const safeInstanceId = String(instanceId || '').trim();
    if (!safeSessionId || !safeInstanceId) {
        return;
    }
    const current = parentStoppedSubagentInstanceIdsBySession.get(safeSessionId) || new Set();
    current.add(safeInstanceId);
    parentStoppedSubagentInstanceIdsBySession.set(safeSessionId, current);
}

function rememberRecentParentStopCandidate(sessionId, instanceId, status) {
    const safeSessionId = String(sessionId || '').trim();
    const safeInstanceId = String(instanceId || '').trim();
    if (!safeSessionId || !safeInstanceId) {
        return;
    }
    const normalizedStatus = normalizeSubagentRunStatus(status);
    const current = recentParentStopCandidateTimestampsBySession.get(safeSessionId) || new Map();
    if (isStoppedSubagentSessionStatus(normalizedStatus)) {
        current.set(safeInstanceId, Date.now());
        recentParentStopCandidateTimestampsBySession.set(safeSessionId, current);
        return;
    }
    if (current.delete(safeInstanceId) && current.size === 0) {
        recentParentStopCandidateTimestampsBySession.delete(safeSessionId);
    }
}

function updateRecentParentStopCandidateFromStatusEvent(sessionId, record, payload, eventMeta) {
    const safeSessionId = String(sessionId || '').trim();
    const safeInstanceId = String(record?.instanceId || '').trim();
    if (!safeSessionId || !safeInstanceId) {
        return;
    }
    if (shouldTrackParentStopCandidate(record, payload, eventMeta)) {
        rememberRecentParentStopCandidate(safeSessionId, safeInstanceId, record.status);
        return;
    }
    rememberRecentParentStopCandidate(safeSessionId, safeInstanceId, '');
}

function shouldTrackParentStopCandidate(record, payload, eventMeta) {
    if (!isStoppedSubagentSessionStatus(record?.status)) {
        return false;
    }
    if (!(payload?.parent_stop_candidate || payload?.parentStopCandidate)) {
        return false;
    }
    const eventRunId = String(
        payload?.parent_run_id
        || payload?.parentRunId
        || eventMeta?.run_id
        || eventMeta?.trace_id
        || '',
    ).trim();
    const subagentRunId = String(
        record?.runId
        || payload?.subagent_run_id
        || payload?.subagentRunId
        || payload?.run_id
        || payload?.runId
        || '',
    ).trim();
    return !!(eventRunId && subagentRunId && eventRunId !== subagentRunId);
}

function collectRecentParentStopCandidateInstanceIds(sessionId) {
    const safeSessionId = String(sessionId || '').trim();
    const current = recentParentStopCandidateTimestampsBySession.get(safeSessionId);
    if (!current) {
        return [];
    }
    const cutoff = Date.now() - RECENT_PARENT_STOP_CANDIDATE_WINDOW_MS;
    const recent = [];
    for (const [instanceId, timestamp] of current.entries()) {
        if (timestamp >= cutoff) {
            recent.push(instanceId);
            continue;
        }
        current.delete(instanceId);
    }
    if (current.size === 0) {
        recentParentStopCandidateTimestampsBySession.delete(safeSessionId);
    }
    return recent;
}

function normalizeSubagentSession(record, sessionId) {
    if (!record || typeof record !== 'object') {
        return null;
    }
    const instanceId = String(
        record.subagent_instance_id
        || record.subagentInstanceId
        || record.instance_id
        || record.instanceId
        || '',
    ).trim();
    const roleId = String(
        record.subagent_role_id
        || record.subagentRoleId
        || record.role_id
        || record.roleId
        || '',
    ).trim();
    const runId = String(
        record.subagent_run_id
        || record.subagentRunId
        || record.run_id
        || record.runId
        || '',
    ).trim();
    const safeSessionId = String(sessionId || record.session_id || record.sessionId || '').trim();
    const subagentKind = normalizeSubagentKind(record);
    const interactive = record.interactive === true || subagentKind === 'orchestration';
    const deletable = record.deletable === true || subagentKind === 'normal';
    if (!safeSessionId || !instanceId || !roleId || !runId) {
        return null;
    }
    if (subagentKind === 'normal' && !runId.startsWith('subagent_run_')) {
        return null;
    }
    const normalizedStatus = normalizeSubagentRunStatus(record.status);
    const normalizedRunStatus = normalizeSubagentRunStatus(
        record.run_status || record.runStatus || record.status,
    );
    return {
        sessionId: safeSessionId,
        instanceId,
        roleId,
        runId,
        title: String(record.title || '').trim(),
        status: normalizedStatus,
          runStatus: normalizedRunStatus,
          runPhase: String(record.run_phase || record.runPhase || '').trim(),
          subagentKind,
          interactive,
          deletable,
          lastEventId: Number(record.last_event_id || record.lastEventId || 0),
        checkpointEventId: Number(
            record.checkpoint_event_id || record.checkpointEventId || 0,
        ),
        streamConnected: record.stream_connected === true || record.streamConnected === true,
        createdAt: String(record.created_at || record.createdAt || '').trim(),
        updatedAt: String(record.updated_at || record.updatedAt || record.created_at || '').trim(),
        conversationId: String(record.conversation_id || record.conversationId || '').trim(),
    };
  }

  function normalizeSubagentKind(record) {
      const explicit = String(
          record?.subagent_kind
          || record?.subagentKind
          || record?.kind
          || '',
      ).trim().toLowerCase();
      if (explicit === 'orchestration' || explicit === 'live') {
          return 'orchestration';
      }
      if (explicit === 'normal' || explicit === 'session') {
          return 'normal';
      }
      const runId = String(record?.run_id || record?.runId || record?.subagent_run_id || '').trim();
      return runId.startsWith('subagent_run_') ? 'normal' : 'orchestration';
  }

function isSubagentBackgroundTask(payload) {
    return !!(
        payload
        && typeof payload === 'object'
        && (
            String(payload.kind || '').trim() === 'subagent'
            || String(payload.subagent_run_id || payload.subagentRunId || '').trim().startsWith('subagent_run_')
        )
    );
}

function backgroundTaskStatusForEvent(payload, eventType) {
    const safeEventType = String(eventType || '').trim();
    if (safeEventType === 'background_task_completed') {
        const payloadStatus = normalizeSubagentRunStatus(payload?.status || '');
        return payloadStatus === 'idle' ? 'completed' : payloadStatus;
    }
    if (safeEventType === 'background_task_stopped') {
        return 'stopped';
    }
    return normalizeSubagentRunStatus(payload?.status || 'running');
}

function normalizeSubagentRunStatus(status) {
    const safeStatus = String(status || '').trim();
    if (!safeStatus) {
        return 'idle';
    }
    if (safeStatus === 'started' || safeStatus === 'pending') {
        return 'running';
    }
    return safeStatus;
}

function isRunningSubagentSessionStatus(status) {
    return ['running', 'queued', 'starting', 'pending'].includes(
        normalizeSubagentRunStatus(status),
    );
}

function isStoppedSubagentSessionStatus(status) {
    return ['stopped', 'cancelled', 'canceled'].includes(
        normalizeSubagentRunStatus(status),
    );
}

function upsertSubagentSessionRecord(current, nextRecord) {
    const next = [...current];
    const index = next.findIndex(item => item.instanceId === nextRecord.instanceId);
    if (index >= 0) {
        next[index] = {
            ...next[index],
            ...nextRecord,
        };
    } else {
        next.push(nextRecord);
    }
    next.sort((left, right) => String(right.updatedAt || '').localeCompare(String(left.updatedAt || '')));
    return next;
}

function mergeSubagentSessionStatusRecord(current, normalized, payload) {
    const existing = current.find(item => (
        item.instanceId === normalized.instanceId
        || (normalized.runId && item.runId === normalized.runId)
    ));
    if (!existing) {
        return normalized;
    }
    return {
        ...existing,
        ...normalized,
        conversationId: hasSubagentSessionPayloadField(payload, 'conversation_id', 'conversationId')
            ? normalized.conversationId
            : existing.conversationId,
        checkpointEventId: hasSubagentSessionPayloadField(payload, 'checkpoint_event_id', 'checkpointEventId')
            ? normalized.checkpointEventId
            : existing.checkpointEventId,
        lastEventId: hasSubagentSessionPayloadField(payload, 'last_event_id', 'lastEventId')
            ? normalized.lastEventId
            : existing.lastEventId,
    };
}

function hasSubagentSessionPayloadField(payload, ...keys) {
    if (!payload || typeof payload !== 'object') {
        return false;
    }
    return keys.some(key => Object.prototype.hasOwnProperty.call(payload, key));
}

function shortInstanceId(instanceId) {
    const safe = String(instanceId || '').trim();
    if (!safe) {
        return 'unknown';
    }
    return safe.length > 8 ? safe.slice(0, 8) : safe;
}

async function runTerminalRefreshAttempt(instanceId, attemptIndex) {
    const safeInstanceId = String(instanceId || '').trim();
    const active = getActiveSubagentSession();
    if (!safeInstanceId || !active || active.instanceId !== safeInstanceId) {
        cancelTerminalRefreshForInstance(safeInstanceId);
        return;
    }
    const result = await renderActiveSubagentSession({ requireToolBoundary: true });
    if (result?.deferred === true) {
        if (attemptIndex >= TERMINAL_REFRESH_DELAYS_MS.length) {
            await renderActiveSubagentSession();
            cancelTerminalRefreshForInstance(safeInstanceId);
            return;
        }
        const delayMs = TERMINAL_REFRESH_DELAYS_MS[attemptIndex];
        const timerId = setTimeout(() => {
            terminalRefreshTimers.delete(safeInstanceId);
            void runTerminalRefreshAttempt(safeInstanceId, attemptIndex + 1);
        }, delayMs);
        terminalRefreshTimers.set(safeInstanceId, timerId);
        return;
    }
    cancelTerminalRefreshForInstance(safeInstanceId);
}

function cancelTerminalRefreshForInstance(instanceId) {
    const safeInstanceId = String(instanceId || '').trim();
    if (!safeInstanceId) {
        return;
    }
    const timerId = terminalRefreshTimers.get(safeInstanceId);
    if (timerId) {
        clearTimeout(timerId);
    }
    terminalRefreshTimers.delete(safeInstanceId);
}

function scheduleSubagentSessionStatusRefresh(sessionId) {
    const safeSessionId = String(sessionId || '').trim();
    if (!safeSessionId) {
        return;
    }
    const existing = statusRefreshTimers.get(safeSessionId);
    if (existing) {
        clearTimeout(existing);
    }
    const timerId = setTimeout(() => {
        statusRefreshTimers.delete(safeSessionId);
        void ensureSessionSubagents(safeSessionId, {
            force: true,
            emitLoadingEvents: false,
        }).then(() => {
            emitSubagentSessionsChanged({
                forceRefresh: false,
                reason: 'status',
                sessionId: safeSessionId,
            });
        });
    }, 500);
    statusRefreshTimers.set(safeSessionId, timerId);
}

function isStillActiveSubagentRender(active, requestId) {
    return !!(
        requestId === activeSubagentRenderSequence
        && getActiveSubagentSession()
        && getActiveSubagentSession()?.instanceId === active.instanceId
        && String(state.currentSessionId || '').trim() === active.sessionId
    );
}

function setMainComposerVisible(visible) {
    if (!els.inputContainer) {
        return;
    }
    els.inputContainer.style.display = visible ? '' : 'none';
}

function setSubagentSessionChromeActive(active) {
    els.chatContainer?.classList?.toggle?.(
        'is-subagent-session-active',
        active === true,
    );
}

function ensureSubagentSessionView(active, { showLoading = false } = {}) {
    const chatEl = els.chatMessages;
    if (!chatEl) {
        return null;
    }
    let wrapper = chatEl.querySelector?.('.subagent-session-view') || null;
    const safeInstanceId = String(active?.instanceId || '').trim();
    if (!wrapper || String(wrapper.dataset.instanceId || '').trim() !== safeInstanceId) {
        chatEl.innerHTML = '';
        wrapper = document.createElement('section');
        wrapper.className = 'subagent-session-view';
        wrapper.dataset.instanceId = safeInstanceId;
        wrapper.innerHTML = `
            <header class="subagent-session-header">
                <div class="subagent-session-title-row">
                    <button class="subagent-session-back-btn" type="button">${escapeHtml(t('subagent_session.back'))}</button>
                    <div class="subagent-session-title"></div>
                    <div class="subagent-session-badge"></div>
                </div>
                <div class="subagent-session-meta"></div>
            </header>
            <div class="subagent-session-loading" role="status" aria-live="polite" hidden>
                <span class="subagent-session-loading-spinner" aria-hidden="true"></span>
                <span>${escapeHtml(t('session.loading'))}</span>
            </div>
            <div class="subagent-session-body"></div>
        `;
        chatEl.appendChild(wrapper);
        wrapper.querySelector?.('.subagent-session-back-btn')?.addEventListener('click', () => {
            void returnToMainSessionView();
        });
    }
    syncSubagentSessionViewChrome(active, wrapper);
    setSubagentSessionLoading(active.instanceId, showLoading, wrapper);
    const body = wrapper.querySelector('.subagent-session-body');
    if (body && typeof body === 'object' && body.dataset) {
        body.dataset.instanceId = safeInstanceId;
        body.dataset.roleId = String(active?.roleId || '').trim();
        body.dataset.runId = String(active?.runId || '').trim();
    }
    return body;
}

function setSubagentSessionLoading(instanceId, loading, wrapper = null) {
    const safeInstanceId = String(instanceId || '').trim();
    const activeView = wrapper
        || els.chatMessages?.querySelector?.('.subagent-session-view')
        || null;
    if (
        !activeView
        || String(activeView.dataset?.instanceId || '').trim() !== safeInstanceId
    ) {
        return;
    }
    setElementClassFlag(activeView, 'is-loading', loading === true);
    const loadingEl = activeView.querySelector?.('.subagent-session-loading') || null;
    if (loadingEl) {
        loadingEl.hidden = loading !== true;
    }
}

function setElementClassFlag(element, className, enabled) {
    if (!element) {
        return;
    }
    if (element.classList?.toggle) {
        element.classList.toggle(className, enabled === true);
        return;
    }
    const classes = new Set(
        String(element.className || '')
            .split(/\s+/)
            .filter(Boolean),
    );
    if (enabled) {
        classes.add(className);
    } else {
        classes.delete(className);
    }
    element.className = [...classes].join(' ');
}

function syncSubagentSessionViewChrome(active, wrapper = null) {
    const activeView = wrapper
        || els.chatMessages?.querySelector?.('.subagent-session-view')
        || null;
    if (!activeView || String(activeView.dataset.instanceId || '').trim() !== String(active?.instanceId || '').trim()) {
        return;
    }
    const titleEl = activeView.querySelector('.subagent-session-title');
    const badgeEl = activeView.querySelector('.subagent-session-badge');
    const metaEl = activeView.querySelector('.subagent-session-meta');
    if (titleEl) {
        titleEl.textContent = active?.title || buildSubagentSessionLabel(active);
    }
    if (badgeEl) {
        const status = String(active?.status || 'idle');
        badgeEl.className = `subagent-session-badge is-${escapeAttribute(status)}`;
        badgeEl.textContent = status;
    }
    if (metaEl) {
        metaEl.textContent = buildSubagentSessionLabel(active);
    }
}

function emitSubagentSessionsChanged(detail = {}) {
    if (typeof document?.dispatchEvent !== 'function') {
        return;
    }
    pendingSubagentSessionsChangedDetail = mergeSubagentSessionChangeDetail(
        pendingSubagentSessionsChangedDetail,
        detail,
    );
    if (subagentSessionsChangedFrame) {
        return;
    }
    const dispatch = () => {
        subagentSessionsChangedFrame = 0;
        const nextDetail = pendingSubagentSessionsChangedDetail || {};
        pendingSubagentSessionsChangedDetail = null;
        document.dispatchEvent(new CustomEvent('agent-teams-subagent-sessions-changed', {
            detail: nextDetail,
        }));
    };
    if (typeof globalThis.requestAnimationFrame === 'function') {
        subagentSessionsChangedFrame = globalThis.requestAnimationFrame(dispatch);
        return;
    }
    subagentSessionsChangedFrame = globalThis.setTimeout(dispatch, 16);
}

function mergeSubagentSessionChangeDetail(previous, next) {
    if (!previous) {
        return { ...(next || {}) };
    }
    const current = next || {};
    return {
        ...previous,
        ...current,
        forceRefresh: previous.forceRefresh === true || current.forceRefresh === true,
        reason: current.reason || previous.reason || '',
    };
}

function emitSubagentSessionStatusChanged(sessionId, instanceId, status) {
    if (typeof document?.dispatchEvent !== 'function') {
        return;
    }
    document.dispatchEvent(new CustomEvent('agent-teams-subagent-session-status-changed', {
        detail: { sessionId, instanceId, status },
    }));
}

function escapeHtml(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function escapeAttribute(value) {
    return escapeHtml(value).replaceAll('`', '&#96;');
}

function areSubagentSessionRowsEqual(leftRows, rightRows) {
    if (leftRows.length !== rightRows.length) {
        return false;
    }
    return leftRows.every((left, index) => areSubagentSessionRecordsEqual(left, rightRows[index]));
}

function areSubagentSessionListsEqual(leftRows, rightRows) {
    if (leftRows.length !== rightRows.length) {
        return false;
    }
    return leftRows.every((left, index) => areSubagentSessionListRecordsEqual(left, rightRows[index]));
}

function areSubagentSessionStructuresEqual(leftRows, rightRows) {
    if (leftRows.length !== rightRows.length) {
        return false;
    }
    return leftRows.every((left, index) => areSubagentSessionStructureRecordsEqual(left, rightRows[index]));
}

function areSubagentSessionStructureRecordsEqual(left, right) {
    return !!(
        right
        && left.sessionId === right.sessionId
        && left.instanceId === right.instanceId
          && left.roleId === right.roleId
          && left.runId === right.runId
          && left.title === right.title
          && left.subagentKind === right.subagentKind
          && left.conversationId === right.conversationId
    );
}

function areSubagentSessionListRecordsEqual(left, right) {
    return !!(
        right
        && left.sessionId === right.sessionId
        && left.instanceId === right.instanceId
        && left.roleId === right.roleId
        && left.runId === right.runId
        && left.title === right.title
        && left.status === right.status
          && left.runStatus === right.runStatus
          && left.runPhase === right.runPhase
          && left.subagentKind === right.subagentKind
          && left.interactive === right.interactive
          && left.deletable === right.deletable
          && left.lastEventId === right.lastEventId
        && left.checkpointEventId === right.checkpointEventId
        && left.streamConnected === right.streamConnected
        && left.createdAt === right.createdAt
        && left.updatedAt === right.updatedAt
        && left.conversationId === right.conversationId
    );
}

function areSubagentSessionRecordsEqual(left, right) {
    return !!(
        right
        && left.sessionId === right.sessionId
        && left.instanceId === right.instanceId
        && left.roleId === right.roleId
        && left.runId === right.runId
        && left.title === right.title
        && left.status === right.status
          && left.runStatus === right.runStatus
          && left.runPhase === right.runPhase
          && left.subagentKind === right.subagentKind
          && left.interactive === right.interactive
          && left.deletable === right.deletable
          && left.lastEventId === right.lastEventId
        && left.checkpointEventId === right.checkpointEventId
        && left.streamConnected === right.streamConnected
        && left.createdAt === right.createdAt
        && left.updatedAt === right.updatedAt
        && left.conversationId === right.conversationId
    );
}
