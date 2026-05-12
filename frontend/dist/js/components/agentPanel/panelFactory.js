/**
 * components/agentPanel/panelFactory.js
 * Panel DOM factory and inject-message bindings.
 */
import {
    injectSubagentMessage,
    stopRun,
} from '../../core/api.js';
import { refreshSessionRecovery, resumeRecoverableRun } from '../../app/recovery.js';
import { bindPanelContextIndicator, schedulePanelContextPreview } from '../contextIndicators.js';
import { state } from '../../core/state.js';
import { formatMessage, t } from '../../utils/i18n.js';
import { sysLog } from '../../utils/logger.js';
import { getDrawer } from './dom.js';
import {
    getActiveSubagentSession,
    updateNormalModeSubagentSessionStatus,
} from '../subagentSessions.js';
import { markSubagentStatus } from '../subagentRail.js';

export function createPanel(instanceId, roleId, onClose) {
    const drawer = getDrawer();
    if (!drawer) return null;
    void onClose;

    const panelEl = document.createElement('div');
    panelEl.className = 'agent-panel';
    panelEl.dataset.instanceId = instanceId;
    panelEl.style.display = 'none';

    const friendlyRole = roleId
        ? roleId.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())
        : instanceId.slice(0, 8);

    panelEl.innerHTML = `
        <div class="agent-panel-controls" hidden>
            <div class="agent-token-usage" data-instance-id="${instanceId}"></div>
            <button class="agent-panel-stop" type="button" title="${t('subagent.stop_title')}">${t('subagent.stop')}</button>
        </div>
        <div class="agent-panel-tabbar" role="tablist" aria-label="${t('subagent.sections')}">
            <button class="agent-panel-tab" data-tab="prompt" role="tab" aria-selected="false">${t('subagent.prompt')}</button>
            <button class="agent-panel-tab" data-tab="tools" role="tab" aria-selected="false">${t('subagent.tools')}</button>
            <button class="agent-panel-tab" data-tab="memory" role="tab" aria-selected="false">${t('subagent.memory')}</button>
            <button class="agent-panel-tab" data-tab="tasks" role="tab" aria-selected="false">${t('subagent.tasks')}</button>
        </div>
        <div class="agent-panel-tabpane" data-tab="prompt" role="tabpanel" hidden>
            <div class="agent-panel-tabpane-header">
                <span class="agent-panel-runtime-prompt-meta agent-panel-section-meta"></span>
            </div>
            <div class="agent-panel-section-body agent-panel-runtime-prompt-body">${t('subagent.no_runtime_prompt')}</div>
        </div>
        <div class="agent-panel-tabpane" data-tab="tools" role="tabpanel" hidden>
            <div class="agent-panel-tabpane-header">
                <span class="agent-panel-runtime-tools-meta agent-panel-section-meta"></span>
            </div>
            <div class="agent-panel-section-body agent-panel-runtime-tools-body">${t('subagent.no_runtime_tools')}</div>
        </div>
        <div class="agent-panel-tabpane" data-tab="memory" role="tabpanel" hidden>
            <div class="agent-panel-tabpane-header">
                <span class="agent-panel-memory-meta agent-panel-section-meta"></span>
            </div>
            <div class="agent-panel-section-body agent-panel-memory-body">${t('subagent.memory_empty')}</div>
        </div>
        <div class="agent-panel-tabpane" data-tab="tasks" role="tabpanel" hidden>
            <div class="agent-panel-tabpane-header">
                <span class="agent-panel-summary-meta agent-panel-section-meta">
                    <span class="agent-panel-summary-status">${t('subagent.status_idle')}</span>
                    <span class="agent-panel-summary-updated"></span>
                </span>
            </div>
            <div class="agent-panel-section-body agent-panel-summary-body">
                <div class="agent-panel-summary-tasks">${t('subagent.no_tasks')}</div>
            </div>
        </div>
        <div class="agent-panel-scroll"></div>
        <div class="agent-panel-input">
            <div class="panel-input-wrapper">
                <textarea class="panel-inject-input" placeholder="${t('subagent.inject_placeholder')}" rows="1"></textarea>
                <div class="context-indicator panel-context-indicator" data-instance-id="${instanceId}" data-state="idle" title="${t('composer.context_title')}">-- / --</div>
                <button class="panel-send-btn" type="button" title="${t('composer.send_title')}">
                    <svg viewBox="0 0 24 24" fill="none"><path d="M22 2L11 13M22 2L15 22L11 13M11 13L2 9L22 2Z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>
                </button>
            </div>
        </div>
    `;

    const stopBtn = panelEl.querySelector('.agent-panel-stop');
    if (stopBtn) {
        stopBtn.onclick = async () => {
            const control = resolveSubagentControlTarget(instanceId);
            if (!control.runId) return;
            try {
                await stopRun(control.runId, control.stopOptions);
                markSubagentStatus(instanceId, 'stopped');
                updateNormalModeSubagentSessionStatus(state.currentSessionId, instanceId, 'stopped');
                state.pausedSubagent = { runId: control.runId, instanceId, roleId };
                sysLog(formatMessage('subagent.log.paused', { agent: roleId || instanceId }), 'log-info');
            } catch (e) {
                sysLog(formatMessage('subagent.error.pause_failed', { error: e.message }), 'log-error');
            }
        };
    }

    bindTabBar(panelEl);

    const textarea = panelEl.querySelector('.panel-inject-input');
    const sendBtn = panelEl.querySelector('.panel-send-btn');
    bindPanelContextIndicator(panelEl, instanceId);
    async function sendInject() {
        const text = textarea.value.trim();
        const controlRunId = resolveSubagentControlRunId(instanceId);
        if (!text || !controlRunId) return;
        const activeSubagent = getActiveSubagentSession();
        const activeSubagentStatus = String(
            activeSubagent?.instanceId === instanceId
                ? activeSubagent?.status || activeSubagent?.runStatus || ''
                : '',
        ).trim();
        const locallyPaused = (
            state.pausedSubagent?.instanceId === instanceId
            || ['paused', 'stopped'].includes(activeSubagentStatus)
        );
        const snapshotPaused = !!(
            state.currentRecoverySnapshot?.pausedSubagent
            && state.currentRecoverySnapshot?.activeRun?.run_id === controlRunId
        );
        const shouldResume = !!(
            locallyPaused
            || snapshotPaused
        );
        textarea.value = '';
        textarea.style.height = 'auto';
        try {
            await injectSubagentMessage(controlRunId, instanceId, text);
            markSubagentStatus(instanceId, 'running');
            updateNormalModeSubagentSessionStatus(state.currentSessionId, instanceId, 'running');
            if (state.pausedSubagent && state.pausedSubagent.instanceId === instanceId) {
                state.pausedSubagent = null;
            }
            if (shouldResume) {
                await resumeRecoverableRun(controlRunId, {
                    sessionId: state.currentSessionId,
                    reason: 'subagent follow-up',
                    quiet: true,
                });
            } else if (state.currentSessionId) {
                await refreshSessionRecovery(state.currentSessionId, { quiet: true });
            }
            schedulePanelContextPreview(instanceId, { immediate: true });
        } catch (e) {
            sysLog(formatMessage('subagent.error.message_failed', { error: e.message }), 'log-error');
        }
    }
    textarea.addEventListener('input', () => {
        textarea.style.height = 'auto';
        textarea.style.height = `${textarea.scrollHeight}px`;
    });
    sendBtn.onclick = sendInject;
    textarea.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendInject();
        }
    });

    drawer.appendChild(panelEl);
    return {
        panelEl,
        scrollEl: panelEl.querySelector('.agent-panel-scroll'),
        instanceId,
        roleId,
        loadedSessionId: '',
        loadedRunId: '',
    };
}

function resolveSubagentControlTarget(instanceId) {
    const safeInstanceId = String(instanceId || '').trim();
    const activeSubagent = getActiveSubagentSession();
    const subagentRunId = String(
        activeSubagent?.instanceId === safeInstanceId
            ? activeSubagent?.runId || ''
            : '',
    ).trim();
    if (subagentRunId) {
        return {
            runId: subagentRunId,
            stopOptions: { scope: 'main' },
        };
    }
    return {
        runId: String(state.activeRunId || '').trim(),
        stopOptions: { scope: 'subagent', instanceId: safeInstanceId },
    };
}

function resolveSubagentControlRunId(instanceId) {
    return resolveSubagentControlTarget(instanceId).runId;
}


function bindTabBar(panelEl) {
    const tabs = panelEl.querySelectorAll('.agent-panel-tab[data-tab]');
    tabs.forEach(tab => {
        tab.onclick = () => {
            const tabName = tab.dataset.tab;
            const isSelected = tab.getAttribute('aria-selected') === 'true';
            if (isSelected) {
                tab.setAttribute('aria-selected', 'false');
                const paneEl = panelEl.querySelector(`.agent-panel-tabpane[data-tab="${tabName}"]`);
                if (paneEl) paneEl.hidden = true;
            } else {
                activateTab(panelEl, tabName);
            }
        };
    });
}

function activateTab(panelEl, tabName) {
    const tabs = panelEl.querySelectorAll('.agent-panel-tab[data-tab]');
    const panes = panelEl.querySelectorAll('.agent-panel-tabpane[data-tab]');
    tabs.forEach(t => t.setAttribute('aria-selected', t.dataset.tab === tabName ? 'true' : 'false'));
    panes.forEach(p => { p.hidden = p.dataset.tab !== tabName; });
}

function escapeHtml(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
