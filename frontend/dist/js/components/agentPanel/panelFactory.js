/**
 * components/agentPanel/panelFactory.js
 * Panel DOM factory and inject-message bindings.
 */
import { injectSubagentMessage, refreshAgentReflection, stopRun } from '../../core/api.js';
import { refreshSessionRecovery, resumeRecoverableRun } from '../../app/recovery.js';
import { bindPanelContextIndicator, schedulePanelContextPreview } from '../contextIndicators.js';
import { state } from '../../core/state.js';
import { sysLog } from '../../utils/logger.js';
import { getDrawer } from './dom.js';
import { loadAgentHistory } from './history.js';

const REFLECTION_BUTTON_RESET_MS = 2400;

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
        <div class="agent-panel-header">
            <div class="agent-panel-title">
                <div class="panel-role-stack">
                    <span class="panel-role">${friendlyRole}</span>
                    <span class="panel-id">${instanceId.slice(0, 8)}</span>
                </div>
            </div>
            <div class="agent-token-usage" data-instance-id="${instanceId}"></div>
            <button class="agent-panel-refresh-reflection" title="Refresh reflection memory">Reflect</button>
            <button class="agent-panel-stop" title="Stop this subagent">Stop</button>
        </div>
        <div class="agent-panel-section agent-panel-reflection" data-collapsed="true">
            <button class="agent-panel-section-toggle agent-panel-reflection-toggle" type="button" aria-expanded="false">
                <span class="agent-panel-section-heading">
                    <span class="agent-panel-section-chevron" aria-hidden="true">></span>
                    <span class="agent-panel-section-title">Reflection memory</span>
                </span>
                <span class="agent-panel-section-meta agent-panel-reflection-meta"></span>
            </button>
            <div class="agent-panel-section-body agent-panel-reflection-body" hidden>No reflection memory yet.</div>
        </div>
        <div class="agent-panel-section agent-panel-summary" data-collapsed="true">
            <button class="agent-panel-section-toggle agent-panel-summary-toggle" type="button" aria-expanded="false">
                <span class="agent-panel-section-heading">
                    <span class="agent-panel-section-chevron" aria-hidden="true">></span>
                    <span class="agent-panel-section-title">Completed tasks</span>
                </span>
                <span class="agent-panel-section-meta agent-panel-summary-meta">
                    <span class="agent-panel-summary-status">Idle</span>
                    <span class="agent-panel-summary-updated"></span>
                </span>
            </button>
            <div class="agent-panel-section-body agent-panel-summary-body" hidden>
                <div class="agent-panel-summary-tasks">No delegated tasks yet.</div>
            </div>
        </div>
        <div class="agent-panel-scroll"></div>
        <div class="agent-panel-input">
            <div class="panel-input-wrapper">
                <textarea class="panel-inject-input" placeholder="Inject message to this agent..." rows="1"></textarea>
                <div class="context-indicator panel-context-indicator" data-instance-id="${instanceId}" data-state="idle" title="Latest provider context usage">-- / --</div>
                <button class="panel-send-btn" title="Send">
                    <svg viewBox="0 0 24 24" fill="none"><path d="M22 2L11 13M22 2L15 22L11 13M11 13L2 9L22 2Z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>
                </button>
            </div>
        </div>
    `;

    const stopBtn = panelEl.querySelector('.agent-panel-stop');
    if (stopBtn) {
        stopBtn.onclick = async () => {
            if (!state.activeRunId) return;
            try {
                await stopRun(state.activeRunId, { scope: 'subagent', instanceId });
                state.pausedSubagent = { runId: state.activeRunId, instanceId, roleId };
                sysLog(`Subagent paused: ${roleId || instanceId}`, 'log-info');
            } catch (e) {
                sysLog(`Failed to pause subagent: ${e.message}`, 'log-error');
            }
        };
    }

    const reflectionBtn = panelEl.querySelector('.agent-panel-refresh-reflection');
    let reflectionBtnResetTimer = 0;
    if (reflectionBtn) {
        setReflectionButtonState(reflectionBtn, 'idle');
        reflectionBtn.onclick = async () => {
            if (!state.currentSessionId) return;
            clearReflectionButtonTimer(reflectionBtnResetTimer);
            reflectionBtnResetTimer = 0;
            setReflectionButtonState(reflectionBtn, 'loading');
            try {
                const reflection = await refreshAgentReflection(state.currentSessionId, instanceId);
                updateReflectionState(instanceId, reflection);
                await loadAgentHistory(instanceId, roleId);
                const rail = await import('../subagentRail.js');
                await rail.refreshSubagentRail(state.currentSessionId, { preserveSelection: true });
                setReflectionButtonState(reflectionBtn, 'success');
                reflectionBtnResetTimer = scheduleReflectionButtonReset(reflectionBtn);
            } catch (e) {
                setReflectionButtonState(reflectionBtn, 'error');
                reflectionBtnResetTimer = scheduleReflectionButtonReset(reflectionBtn);
                sysLog(`Failed to refresh reflection: ${e.message}`, 'log-error');
            }
        };
    }

    bindCollapsibleSection(panelEl, {
        sectionSelector: '.agent-panel-reflection',
        toggleSelector: '.agent-panel-reflection-toggle',
        bodySelector: '.agent-panel-reflection-body',
        expanded: false,
    });
    bindCollapsibleSection(panelEl, {
        sectionSelector: '.agent-panel-summary',
        toggleSelector: '.agent-panel-summary-toggle',
        bodySelector: '.agent-panel-summary-body',
        expanded: false,
    });

    const textarea = panelEl.querySelector('.panel-inject-input');
    const sendBtn = panelEl.querySelector('.panel-send-btn');
    bindPanelContextIndicator(panelEl, instanceId);
    async function sendInject() {
        const text = textarea.value.trim();
        if (!text || !state.activeRunId) return;
        const shouldResume = !!(
            state.currentRecoverySnapshot?.pausedSubagent &&
            state.currentRecoverySnapshot?.activeRun?.run_id === state.activeRunId
        );
        textarea.value = '';
        textarea.style.height = 'auto';
        try {
            await injectSubagentMessage(state.activeRunId, instanceId, text);
            if (state.pausedSubagent && state.pausedSubagent.instanceId === instanceId) {
                state.pausedSubagent = null;
            }
            if (shouldResume) {
                await resumeRecoverableRun(state.activeRunId, {
                    sessionId: state.currentSessionId,
                    reason: 'subagent follow-up',
                    quiet: true,
                });
            } else if (state.currentSessionId) {
                await refreshSessionRecovery(state.currentSessionId, { quiet: true });
            }
            schedulePanelContextPreview(instanceId, { immediate: true });
        } catch (e) {
            sysLog(`Failed to message subagent: ${e.message}`, 'log-error');
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


function bindCollapsibleSection(panelEl, { sectionSelector, toggleSelector, bodySelector, expanded = false }) {
    const sectionEl = panelEl.querySelector(sectionSelector);
    const toggleEl = panelEl.querySelector(toggleSelector);
    const bodyEl = panelEl.querySelector(bodySelector);
    if (!sectionEl || !toggleEl || !bodyEl) return;

    setCollapsibleSectionState(sectionEl, toggleEl, bodyEl, expanded);
    toggleEl.onclick = () => {
        const isExpanded = String(toggleEl.getAttribute('aria-expanded') || 'false') === 'true';
        setCollapsibleSectionState(sectionEl, toggleEl, bodyEl, !isExpanded);
    };
}

function setCollapsibleSectionState(sectionEl, toggleEl, bodyEl, expanded) {
    const nextExpanded = expanded === true;
    sectionEl.dataset.collapsed = nextExpanded ? 'false' : 'true';
    toggleEl.setAttribute('aria-expanded', nextExpanded ? 'true' : 'false');
    bodyEl.hidden = !nextExpanded;
}

function updateReflectionState(instanceId, reflection) {
    state.sessionAgents = (state.sessionAgents || []).map(agent =>
        agent.instance_id === instanceId
            ? {
                ...agent,
                reflection_summary_preview: String(reflection?.preview || agent.reflection_summary_preview || ''),
                reflection_updated_at: String(reflection?.updated_at || agent.reflection_updated_at || ''),
            }
            : agent,
    );
}

function setReflectionButtonState(button, stateName) {
    if (!button) return;
    const nextState = String(stateName || 'idle');
    button.dataset.state = nextState;
    if (nextState === 'loading') {
        button.disabled = true;
        button.textContent = 'Reflecting...';
        button.title = 'Refreshing reflection memory';
        return;
    }
    if (nextState === 'success') {
        button.disabled = false;
        button.textContent = 'Reflected';
        button.title = 'Reflection memory refreshed';
        return;
    }
    if (nextState === 'error') {
        button.disabled = false;
        button.textContent = 'Retry Reflect';
        button.title = 'Reflection refresh failed';
        return;
    }
    button.disabled = false;
    button.textContent = 'Reflect';
    button.title = 'Refresh reflection memory';
}

function scheduleReflectionButtonReset(button) {
    return window.setTimeout(() => {
        setReflectionButtonState(button, 'idle');
    }, REFLECTION_BUTTON_RESET_MS);
}

function clearReflectionButtonTimer(timerId) {
    if (!timerId) return;
    window.clearTimeout(timerId);
}
