/**
 * components/subagentSessions.js
 * Normal-mode subagent child-session cache, navigation, and read-only view.
 */
import { fetchSessionSubagents } from '../core/api.js';
import { syncNormalModeSubagentStreams } from '../core/stream.js';
import { clearAllPanels, loadAgentHistory, openAgentPanel } from './agentPanel.js';
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
    removeSubagentSessionInlineView();
    state.activeSubagentSession = null;
    state.activeView = 'main';
    state.activeAgentRoleId = null;
    state.activeAgentInstanceId = null;
    clearAllPanels();
    syncPromptComposerHint();
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
    rememberSessionSubagent(sessionId, record);
}

export function rememberSessionSubagent(
    sessionId,
    record,
    { autoActivate = false } = {},
) {
    const safeSessionId = String(sessionId || '').trim();
    const normalized = normalizeSubagentSession(record, safeSessionId);
    if (!safeSessionId || normalized === null) {
        return;
    }
    const current = getSessionSubagentSessions(safeSessionId);
    const next = upsertSubagentSessionRecord(current, normalized);
    applySessionSubagentRecords(safeSessionId, next);
    if (
        autoActivate
        && String(state.currentSessionId || '').trim() === safeSessionId
    ) {
        void activatePreferredSubagentSession(safeSessionId, {
            preferredInstanceId: normalized.instanceId,
            preserveSelection: true,
        });
    }
}

export function updateNormalModeSubagentSessionStatus(sessionId, instanceId, status) {
    updateSessionSubagentStatus(sessionId, instanceId, status);
}

export function updateSessionSubagentStatus(sessionId, instanceId, status) {
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
    state.activeView = 'main';
    state.activeAgentRoleId = normalized.roleId;
    state.activeAgentInstanceId = normalized.instanceId;
    syncPromptComposerHint();
    clearAllPanels();
    cancelTerminalRefreshForInstance(normalized.instanceId);
    await renderActiveSubagentSession();
    scrollActiveSubagentSessionIntoView(normalized.instanceId);
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
    const host = ensureSubagentSessionInlineHost(active);
    if (!host || typeof host !== 'object') {
        return { rendered: false, deferred: false };
    }
    try {
        if (!isStillActiveSubagentRender(active, requestId)) {
            return { rendered: false, deferred: false };
        }
        openAgentPanel(active.instanceId, active.roleId, {
            host,
            inline: true,
            forceRefresh: true,
            skipHistoryLoad: true,
        });
        const result = await loadAgentHistory(active.instanceId, active.roleId, {
            runId: active.runId,
            status: active.status,
            runStatus: active.runStatus,
            runPhase: active.runPhase,
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
    return els.chatMessages?.querySelector?.(
        '.subagent-inline-panel-host .agent-panel-scroll',
    ) || null;
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
        syncSubagentSessionInlineChrome(current);
        syncPromptComposerHint();
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
    reconcileActiveSubagentSession(sessionId, nextRows);
    if (
        String(state.currentSessionMode || '').trim() === 'orchestration'
        && String(state.currentSessionId || '').trim() === String(sessionId || '').trim()
        && nextRows.length > 0
        && !getActiveSubagentSession()
    ) {
        void activatePreferredSubagentSession(sessionId, { preserveSelection: false });
    }
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
    if (
        !safeSessionId
        || !instanceId
        || !roleId
        || !runId
    ) {
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

async function activatePreferredSubagentSession(
    sessionId,
    {
        preferredInstanceId = '',
        preserveSelection = true,
    } = {},
) {
    const rows = getSessionSubagentSessions(sessionId);
    const next = choosePreferredSubagentSession(rows, {
        preferredInstanceId,
        preserveSelection,
    });
    if (!next) {
        return;
    }
    const active = getActiveSubagentSession();
    if (
        active
        && active.sessionId === sessionId
        && active.instanceId === next.instanceId
    ) {
        state.activeSubagentSession = next;
        await renderActiveSubagentSession();
        return;
    }
    await openSubagentSession(sessionId, next);
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

function ensureSubagentSessionInlineHost(active) {
    const targetSection = resolveSubagentSessionRoundSection(active);
    if (!targetSection) {
        return null;
    }
    const safeInstanceId = String(active?.instanceId || '').trim();
    const safeRunId = String(active?.runId || '').trim();
    let mount = targetSection.querySelector?.('.round-subagent-inline-mount') || null;
    let wrapper = mount?.querySelector?.('.subagent-inline-session') || null;
    const wrapperMoved = !!(
        wrapper
        && (
            String(wrapper.dataset.instanceId || '').trim() !== safeInstanceId
            || String(wrapper.dataset.runId || '').trim() !== safeRunId
        )
    );
    if (!wrapper || wrapperMoved) {
        removeSubagentSessionInlineView();
        mount = targetSection.querySelector?.('.round-subagent-inline-mount') || null;
        if (!mount) {
            mount = document.createElement('div');
            mount.className = 'round-subagent-inline-mount';
            targetSection.appendChild(mount);
        }
        wrapper = document.createElement('section');
        wrapper.className = 'subagent-inline-session';
        wrapper.dataset.instanceId = safeInstanceId;
        wrapper.dataset.runId = safeRunId;
        wrapper.innerHTML = `
            <div class="subagent-inline-panel-host"></div>
        `;
        mount.appendChild(wrapper);
    }
    syncSubagentSessionInlineChrome(active, wrapper);
    const host = wrapper.querySelector('.subagent-inline-panel-host');
    if (host && typeof host === 'object' && host.dataset) {
        host.dataset.instanceId = safeInstanceId;
        host.dataset.roleId = String(active?.roleId || '').trim();
        host.dataset.runId = String(active?.runId || '').trim();
    }
    return host;
}

function resolveSubagentSessionRoundSection(active) {
    const chatEl = els.chatMessages;
    if (!chatEl) {
        return null;
    }
    const safeRunId = String(active?.runId || '').trim();
    if (safeRunId) {
        const exact = chatEl.querySelector?.(`.session-round-section[data-run-id="${safeRunId}"]`) || null;
        if (exact) {
            return exact;
        }
    }
    const sections = Array.from(chatEl.querySelectorAll?.('.session-round-section') || []);
    if (sections.length > 0) {
        return sections[sections.length - 1];
    }
    return null;
}

function removeSubagentSessionInlineView() {
    document?.querySelectorAll?.('.subagent-inline-session')?.forEach?.(node => node.remove());
    document?.querySelectorAll?.('.round-subagent-inline-mount')?.forEach?.(node => {
        if (node.childElementCount === 0) {
            node.remove();
        }
    });
}

function scrollActiveSubagentSessionIntoView(instanceId) {
    const safeInstanceId = String(instanceId || '').trim();
    if (!safeInstanceId) {
        return;
    }
    const wrapper = Array.from(
        document?.querySelectorAll?.('.subagent-inline-session') || [],
    ).find(node => String(node?.dataset?.instanceId || '').trim() === safeInstanceId);
    wrapper?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' });
}

function reconcileActiveSubagentSession(sessionId, rows) {
    const active = getActiveSubagentSession();
    if (!active || active.sessionId !== sessionId) {
        return;
    }
    const replacement = rows.find(item => item.instanceId === active.instanceId) || null;
    if (replacement) {
        state.activeSubagentSession = replacement;
        syncPromptComposerHint();
        return;
    }
    if (rows.length === 0) {
        clearActiveSubagentSession();
    }
}

function syncSubagentSessionInlineChrome(active, wrapper = null) {
    const activeView = wrapper
        || els.chatMessages?.querySelector?.('.subagent-inline-session')
        || null;
    if (!activeView || String(activeView.dataset.instanceId || '').trim() !== String(active?.instanceId || '').trim()) {
        return;
    }
    activeView.dataset.label = active?.title || buildSubagentSessionLabel(active);
    activeView.dataset.status = String(active?.status || 'idle');
}

function emitSubagentSessionsChanged() {
    if (typeof document?.dispatchEvent !== 'function') {
        return;
    }
    document.dispatchEvent(new CustomEvent('agent-teams-subagent-sessions-changed'));
}

function syncPromptComposerHint() {
    if (!els.promptInputHint) {
        return;
    }
    const active = resolveInjectableComposerSubagent();
    if (!active) {
        els.promptInputHint.textContent = t('composer.hint');
        return;
    }
    const roleLabel = getRoleDisplayName(active.roleId, {
        fallback: t('composer.target_subagent'),
    });
    const shortInstanceId = String(active.instanceId || '').trim().slice(0, 8);
    const targetLabel = shortInstanceId ? `${roleLabel} · ${shortInstanceId}` : roleLabel;
    const template = String(t('composer.hint_targeted') || '').trim();
    els.promptInputHint.textContent = template
        ? template.replace('{target}', targetLabel)
        : targetLabel;
}

function resolveInjectableComposerSubagent() {
    const active = getActiveSubagentSession();
    if (!active) {
        return null;
    }
    const activeRunId = String(state.activeRunId || active.runId || '').trim();
    const pausedSubagent = state.pausedSubagent;
    if (
        pausedSubagent
        && typeof pausedSubagent === 'object'
        && String(pausedSubagent.instanceId || '').trim() === active.instanceId
        && String(pausedSubagent.runId || activeRunId).trim() === activeRunId
    ) {
        return active;
    }
    const recoveryPausedSubagent = state.currentRecoverySnapshot?.pausedSubagent;
    const recoveryRunId = String(
        state.currentRecoverySnapshot?.activeRun?.run_id || activeRunId,
    ).trim();
    if (
        recoveryPausedSubagent
        && typeof recoveryPausedSubagent === 'object'
        && String(recoveryPausedSubagent.instanceId || '').trim() === active.instanceId
        && recoveryRunId === activeRunId
    ) {
        return active;
    }
    return null;
}

function choosePreferredSubagentSession(
    rows,
    {
        preferredInstanceId = '',
        preserveSelection = true,
    } = {},
) {
    const candidates = Array.isArray(rows) ? rows : [];
    if (candidates.length === 0) {
        return null;
    }
    const safePreferredInstanceId = String(preferredInstanceId || '').trim();
    const active = preserveSelection ? getActiveSubagentSession() : null;
    if (active) {
        const matchingActive = candidates.find(item => item.instanceId === active.instanceId);
        if (matchingActive) {
            return matchingActive;
        }
    }
    if (safePreferredInstanceId) {
        const matchingPreferred = candidates.find(
            item => item.instanceId === safePreferredInstanceId,
        );
        if (matchingPreferred) {
            return matchingPreferred;
        }
    }
    return candidates.find(item => item.status === 'running') || candidates[0] || null;
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
