/**
 * components/subagentSessions.js
 * Normal-mode subagent child-session cache, navigation, and read-only view.
 */
import { fetchSessionSubagents } from '../core/api.js';
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
const expandedParentSessionIds = new Set();
const terminalRefreshTimers = new Map();
let activeSubagentRenderSequence = 0;
const TERMINAL_REFRESH_DELAYS_MS = [120, 250, 500, 900, 1400];

export function getSessionSubagentSessions(sessionId) {
    return [...(subagentSessionsBySessionId.get(String(sessionId || '').trim()) || [])];
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

export function clearActiveSubagentSession() {
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
    { force = false, emitLoadingEvents = true } = {},
) {
    const safeSessionId = String(sessionId || '').trim();
    if (!safeSessionId) {
        return [];
    }
    if (!force && subagentSessionsBySessionId.has(safeSessionId)) {
        return getSessionSubagentSessions(safeSessionId);
    }
    if (loadingSessionIds.has(safeSessionId)) {
        return getSessionSubagentSessions(safeSessionId);
    }
    loadingSessionIds.add(safeSessionId);
    if (emitLoadingEvents) {
        emitSubagentSessionsChanged();
    }
    try {
        const payload = await fetchSessionSubagents(safeSessionId);
        replaceSessionSubagents(safeSessionId, payload, { emitChange: false });
        return getSessionSubagentSessions(safeSessionId);
    } catch (error) {
        sysLog(`Failed to load subagent sessions: ${error.message || error}`, 'log-error');
        return getSessionSubagentSessions(safeSessionId);
    } finally {
        const wasLoading = loadingSessionIds.delete(safeSessionId);
        if (emitLoadingEvents && wasLoading) {
            emitSubagentSessionsChanged();
        }
    }
}

export function toggleSubagentSessionList(sessionId) {
    const safeSessionId = String(sessionId || '').trim();
    if (!safeSessionId) {
        return;
    }
    if (expandedParentSessionIds.has(safeSessionId)) {
        expandedParentSessionIds.delete(safeSessionId);
        emitSubagentSessionsChanged();
        return;
    }
    expandedParentSessionIds.add(safeSessionId);
    void ensureSessionSubagents(safeSessionId, { force: false });
    emitSubagentSessionsChanged();
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
        return;
    }
    const current = getSessionSubagentSessions(safeSessionId);
    const next = upsertSubagentSessionRecord(current, normalized);
    applySessionSubagentRecords(safeSessionId, next);
}

export function updateNormalModeSubagentSessionStatus(sessionId, instanceId, status) {
    const safeSessionId = String(sessionId || '').trim();
    const safeInstanceId = String(instanceId || '').trim();
    if (!safeSessionId || !safeInstanceId) {
        return;
    }
    const current = getSessionSubagentSessions(safeSessionId);
    const nowIso = new Date().toISOString();
    let changed = false;
    const next = current.map(item => (
        item.instanceId === safeInstanceId
            ? (() => {
                const nextStatus = String(status || item.status || 'idle');
                if (item.status === nextStatus) {
                    return item;
                }
                changed = true;
                return {
                    ...item,
                    status: nextStatus,
                    updatedAt: nowIso,
                };
            })()
            : item
    ));
    if (!changed) {
        return;
    }
    applySessionSubagentRecords(safeSessionId, next);
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
    await renderActiveSubagentSession();
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
    const requestId = ++activeSubagentRenderSequence;
    hideRoundNavigator();
    const body = ensureSubagentSessionView(active);
    if (!body || typeof body !== 'object' || !('innerHTML' in body)) {
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
        });
        if (!isStillActiveSubagentRender(active, requestId)) {
            return { rendered: false, deferred: false };
        }
        return {
            rendered: result?.deferred !== true,
            deferred: result?.deferred === true,
        };
    } catch (error) {
        sysLog(`Failed to load subagent session: ${error.message || error}`, 'log-error');
        return { rendered: false, deferred: false };
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
    const changed = !areSubagentSessionRowsEqual(previousRows, nextRows);
    subagentSessionsBySessionId.set(sessionId, nextRows);
    syncNormalModeSubagentStreams(sessionId, getSessionSubagentSessions(sessionId));
    syncActiveSubagentSessionFromCache(sessionId);
    if (emitChange && changed) {
        emitSubagentSessionsChanged();
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
    const instanceId = String(record.instance_id || record.instanceId || '').trim();
    const roleId = String(record.role_id || record.roleId || '').trim();
    const runId = String(record.run_id || record.runId || '').trim();
    const safeSessionId = String(sessionId || record.session_id || record.sessionId || '').trim();
    if (!safeSessionId || !instanceId || !roleId || !runId || !runId.startsWith('subagent_run_')) {
        return null;
    }
    return {
        sessionId: safeSessionId,
        instanceId,
        roleId,
        runId,
        title: String(record.title || '').trim(),
        status: String(record.status || 'idle').trim() || 'idle',
        runStatus: String(record.run_status || record.runStatus || '').trim(),
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

function ensureSubagentSessionView(active) {
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
                    <div class="subagent-session-title"></div>
                    <div class="subagent-session-badge"></div>
                </div>
                <div class="subagent-session-meta"></div>
            </header>
            <div class="subagent-session-body"></div>
        `;
        chatEl.appendChild(wrapper);
    }
    syncSubagentSessionViewChrome(active, wrapper);
    const body = wrapper.querySelector('.subagent-session-body');
    if (body && typeof body === 'object' && body.dataset) {
        body.dataset.instanceId = safeInstanceId;
        body.dataset.roleId = String(active?.roleId || '').trim();
        body.dataset.runId = String(active?.runId || '').trim();
    }
    return body;
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

function emitSubagentSessionsChanged() {
    if (typeof document?.dispatchEvent !== 'function') {
        return;
    }
    document.dispatchEvent(new CustomEvent('agent-teams-subagent-sessions-changed'));
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
