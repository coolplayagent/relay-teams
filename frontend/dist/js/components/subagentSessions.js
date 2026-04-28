/**
 * components/subagentSessions.js
 * Normal-mode subagent child-session cache, navigation, and read-only view.
 */
import { fetchSessionSubagents } from '../core/api.js';
import { hydrateSessionView } from '../app/recovery.js';
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
let activeSubagentRenderSequence = 0;
let activeSubagentRenderController = null;
let mainSessionReturnController = null;
let mainSessionReturnToken = 0;
let subagentSessionsChangedFrame = 0;
let pendingSubagentSessionsChangedDetail = null;
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
        abortMainSessionReturn();
    }
    cancelTerminalRefreshForInstance(getActiveSubagentSession()?.instanceId || '');
    state.activeSubagentSession = null;
    state.activeView = 'main';
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
        const payload = await fetchSessionSubagents(safeSessionId, { signal });
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
    const normalized = normalizeSubagentSessions(payload, safeSessionId);
    applySessionSubagentRecords(safeSessionId, normalized, { emitChange });
    return getSessionSubagentSessions(safeSessionId);
}

export function rememberNormalModeSubagentSession(sessionId, record) {
    const safeSessionId = String(sessionId || '').trim();
    const normalized = normalizeSubagentSession(record, safeSessionId);
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
    const normalized = normalizeSubagentSession({
        ...payload,
        status: backgroundTaskStatusForEvent(payload, eventType),
        updated_at: payload?.updated_at || payload?.updatedAt || new Date().toISOString(),
    }, safeSessionId);
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
    const current = getSessionSubagentSessions(safeSessionId);
    let changed = false;
    let nextStatus = '';
    const next = current.map(item => (
        item.instanceId === safeInstanceId
            ? (() => {
                nextStatus = String(status || item.status || 'idle');
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
    if (!changed) {
        return;
    }
    applySessionSubagentRecords(safeSessionId, next, { emitChange: false });
    emitSubagentSessionStatusChanged(safeSessionId, safeInstanceId, nextStatus);
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

export async function openSubagentSession(sessionId, record) {
    const safeSessionId = String(sessionId || '').trim();
    const normalized = normalizeSubagentSession(record, safeSessionId);
    if (!safeSessionId || normalized === null) {
        return;
    }
    abortMainSessionReturn();
    state.activeSubagentSession = normalized;
    state.activeView = 'subagent-session';
    state.activeAgentRoleId = normalized.roleId;
    state.activeAgentInstanceId = normalized.instanceId;
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
    const returnRequest = resetMainSessionReturnController();
    const returnController = returnRequest.controller;
    const returnToken = returnRequest.token;
    const returnSignal = returnController.signal;
    clearActiveSubagentSession({ abortMainReturn: false });
    showMainSessionLoadingPlaceholder(sessionId);
    document.dispatchEvent(new CustomEvent('agent-teams-subagent-session-cleared', {
        detail: { sessionId },
    }));
    try {
        await hydrateSessionView(sessionId, {
            includeRounds: true,
            quiet: true,
            signal: returnSignal,
        });
        if (
            returnSignal.aborted
            || !isLatestMainSessionReturn(returnToken, returnController, sessionId)
            || String(state.currentSessionId || '').trim() !== sessionId
            || state.activeSubagentSession
        ) {
            return;
        }
        document.dispatchEvent(new CustomEvent('agent-teams-session-activated', {
            detail: { sessionId },
        }));
        document.dispatchEvent(new CustomEvent('agent-teams-session-selected', {
            detail: { sessionId },
        }));
    } catch (error) {
        if (error?.name === 'AbortError') {
            return;
        }
        showMainSessionLoadFailed(sessionId);
        sysLog(`Failed to return to main session: ${error.message || error}`, 'log-error');
    } finally {
        clearMainSessionReturnController(returnController);
    }
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
    syncNormalModeSubagentStreams(sessionId, getSessionSubagentSessions(sessionId));
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
    if (!safeSessionId || !instanceId || !roleId || !runId || !runId.startsWith('subagent_run_')) {
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

function resetMainSessionReturnController() {
    abortMainSessionReturn();
    mainSessionReturnController = new AbortController();
    mainSessionReturnToken += 1;
    return {
        controller: mainSessionReturnController,
        token: mainSessionReturnToken,
    };
}

function clearMainSessionReturnController(controller) {
    if (mainSessionReturnController === controller) {
        mainSessionReturnController = null;
    }
}

function abortMainSessionReturn() {
    if (!mainSessionReturnController) {
        return;
    }
    mainSessionReturnController.abort();
    mainSessionReturnController = null;
    mainSessionReturnToken += 1;
}

function isLatestMainSessionReturn(token, controller, sessionId) {
    return !!(
        mainSessionReturnController === controller
        && mainSessionReturnToken === token
        && !controller.signal.aborted
        && String(state.currentSessionId || '').trim() === String(sessionId || '').trim()
    );
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

function showMainSessionLoadingPlaceholder(sessionId) {
    if (!els.chatMessages) {
        return;
    }
    els.chatMessages.innerHTML = `
        <div class="subagent-main-session-loading" data-session-id="${escapeAttribute(sessionId)}" role="status" aria-live="polite">
            <span class="subagent-main-session-loading-spinner" aria-hidden="true"></span>
            <span>${escapeHtml(t('session.loading'))}</span>
        </div>
    `;
}

function showMainSessionLoadFailed(sessionId) {
    if (!els.chatMessages) {
        return;
    }
    els.chatMessages.innerHTML = `
        <div class="subagent-main-session-loading is-error" data-session-id="${escapeAttribute(sessionId)}" role="status">
            <span>${escapeHtml(t('subagent_session.load_failed'))}</span>
        </div>
    `;
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
        && left.lastEventId === right.lastEventId
        && left.checkpointEventId === right.checkpointEventId
        && left.streamConnected === right.streamConnected
        && left.createdAt === right.createdAt
        && left.updatedAt === right.updatedAt
        && left.conversationId === right.conversationId
    );
}
