/**
 * components/agentPanel/panelFactory.js
 * Panel DOM factory and inject-message bindings.
 */
import {
    deleteAgentReflection,
    injectSubagentMessage,
    refreshAgentReflection,
    stopRun,
    updateAgentReflection,
} from '../../core/api.js';
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
        <div class="agent-panel-controls" hidden>
            <div class="agent-token-usage" data-instance-id="${instanceId}"></div>
            <button class="agent-panel-refresh-reflection" type="button" title="Refresh reflection memory">Reflect</button>
            <button class="agent-panel-stop" type="button" title="Stop this subagent">Stop</button>
        </div>
        <div class="agent-panel-tabbar" role="tablist" aria-label="Agent sections">
            <button class="agent-panel-tab" data-tab="prompt" role="tab" aria-selected="false">Prompt</button>
            <button class="agent-panel-tab" data-tab="tools" role="tab" aria-selected="false">Tools</button>
            <button class="agent-panel-tab" data-tab="memory" role="tab" aria-selected="false">Memory</button>
            <button class="agent-panel-tab" data-tab="tasks" role="tab" aria-selected="false">Tasks</button>
        </div>
        <div class="agent-panel-tabpane" data-tab="prompt" role="tabpanel" hidden>
            <div class="agent-panel-tabpane-header">
                <span class="agent-panel-runtime-prompt-meta agent-panel-section-meta"></span>
            </div>
            <div class="agent-panel-section-body agent-panel-runtime-prompt-body">No runtime system prompt yet.</div>
        </div>
        <div class="agent-panel-tabpane" data-tab="tools" role="tabpanel" hidden>
            <div class="agent-panel-tabpane-header">
                <span class="agent-panel-runtime-tools-meta agent-panel-section-meta"></span>
            </div>
            <div class="agent-panel-section-body agent-panel-runtime-tools-body">No runtime tools snapshot yet.</div>
        </div>
        <div class="agent-panel-tabpane" data-tab="memory" role="tabpanel" hidden>
            <div class="agent-panel-tabpane-header">
                <span class="agent-panel-reflection-meta agent-panel-section-meta"></span>
                <div class="agent-panel-section-actions" aria-label="Reflection memory actions">
                    <button class="agent-panel-icon-btn agent-panel-reflection-edit" type="button" title="Edit reflection memory" aria-label="Edit reflection memory">
                        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                            <path d="M12 20h9" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
                            <path d="M16.5 3.5a2.12 2.12 0 113 3L7 19l-4 1 1-4 12.5-12.5z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>
                        </svg>
                    </button>
                    <button class="agent-panel-icon-btn agent-panel-reflection-delete" type="button" title="Delete reflection memory" aria-label="Delete reflection memory">
                        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                            <path d="M3 6h18" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
                            <path d="M8 6V4.5A1.5 1.5 0 019.5 3h5A1.5 1.5 0 0116 4.5V6" stroke="currentColor" stroke-width="1.8"/>
                            <path d="M6.5 6l1 13a1.5 1.5 0 001.5 1.4h6a1.5 1.5 0 001.5-1.4l1-13" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>
                            <path d="M10 10.5v6M14 10.5v6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
                        </svg>
                    </button>
                </div>
            </div>
            <div class="agent-panel-section-body agent-panel-reflection-body">No reflection memory yet.</div>
        </div>
        <div class="agent-panel-tabpane" data-tab="tasks" role="tabpanel" hidden>
            <div class="agent-panel-tabpane-header">
                <span class="agent-panel-summary-meta agent-panel-section-meta">
                    <span class="agent-panel-summary-status">Idle</span>
                    <span class="agent-panel-summary-updated"></span>
                </span>
            </div>
            <div class="agent-panel-section-body agent-panel-summary-body">
                <div class="agent-panel-summary-tasks">No delegated tasks yet.</div>
            </div>
        </div>
        <div class="agent-panel-scroll"></div>
        <div class="agent-panel-input">
            <div class="panel-input-wrapper">
                <textarea class="panel-inject-input" placeholder="Inject message to this agent..." rows="1"></textarea>
                <div class="context-indicator panel-context-indicator" data-instance-id="${instanceId}" data-state="idle" title="Latest provider context usage">-- / --</div>
                <button class="panel-send-btn" type="button" title="Send">
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
                await syncReflectionState(instanceId, roleId, reflection, panelEl);
                setReflectionButtonState(reflectionBtn, 'success');
                reflectionBtnResetTimer = scheduleReflectionButtonReset(reflectionBtn);
            } catch (e) {
                setReflectionButtonState(reflectionBtn, 'error');
                reflectionBtnResetTimer = scheduleReflectionButtonReset(reflectionBtn);
                sysLog(`Failed to refresh reflection: ${e.message}`, 'log-error');
            }
        };
    }

    const editReflectionBtn = panelEl.querySelector('.agent-panel-reflection-edit');
    if (editReflectionBtn) {
        editReflectionBtn.onclick = () => {
            openReflectionEditor(panelEl, instanceId, roleId);
        };
    }

    const deleteReflectionBtn = panelEl.querySelector('.agent-panel-reflection-delete');
    if (deleteReflectionBtn) {
        deleteReflectionBtn.onclick = async () => {
            if (!state.currentSessionId) return;
            const confirmed = typeof window.confirm === 'function'
                ? window.confirm('Delete reflection memory for this subagent role?')
                : true;
            if (!confirmed) return;
            setReflectionActionButtonsDisabled(panelEl, true);
            try {
                const reflection = await deleteAgentReflection(state.currentSessionId, instanceId);
                await syncReflectionState(instanceId, roleId, reflection, panelEl);
                sysLog(`Deleted reflection memory for ${roleId || instanceId}`, 'log-info');
            } catch (e) {
                sysLog(`Failed to delete reflection memory: ${e.message}`, 'log-error');
            } finally {
                setReflectionActionButtonsDisabled(panelEl, false);
            }
        };
    }

    bindTabBar(panelEl);

    const textarea = panelEl.querySelector('.panel-inject-input');
    const sendBtn = panelEl.querySelector('.panel-send-btn');
    bindPanelContextIndicator(panelEl, instanceId);
    async function sendInject() {
        const text = textarea.value.trim();
        if (!text || !state.activeRunId) return;
        const shouldResume = !!(
            state.currentRecoverySnapshot?.pausedSubagent
            && state.currentRecoverySnapshot?.activeRun?.run_id === state.activeRunId
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

async function syncReflectionState(instanceId, roleId, reflection, panelEl) {
    resetReflectionBody(panelEl);
    updateReflectionState(instanceId, reflection);
    await loadAgentHistory(instanceId, roleId);
    const rail = await import('../subagentRail.js');
    await rail.refreshSubagentRail(state.currentSessionId, { preserveSelection: true });
}

function updateReflectionState(instanceId, reflection) {
    state.sessionAgents = (state.sessionAgents || []).map(agent =>
        agent.instance_id === instanceId
            ? {
                ...agent,
                reflection_summary_preview: String(reflection?.preview || ''),
                reflection_updated_at: String(reflection?.updated_at || ''),
            }
            : agent,
    );
}

function openReflectionEditor(panelEl, instanceId, roleId) {
    const bodyEl = panelEl.querySelector('.agent-panel-reflection-body');
    if (!bodyEl) return;
    const currentSummary = String(bodyEl.dataset.summary || '').trim();

    activateTab(panelEl, 'memory');
    bodyEl.dataset.mode = 'editing';
    bodyEl.innerHTML = `
        <div class="agent-panel-reflection-editor">
            <textarea class="agent-panel-reflection-editor-input" rows="8" placeholder="Write long-term notes for this subagent role...">${escapeHtml(currentSummary)}</textarea>
            <div class="agent-panel-reflection-editor-actions">
                <button class="agent-panel-reflection-cancel" type="button">Cancel</button>
                <button class="agent-panel-reflection-save" type="button">Save</button>
            </div>
        </div>
    `;

    const editorInput = panelEl.querySelector('.agent-panel-reflection-editor-input');
    const cancelBtn = panelEl.querySelector('.agent-panel-reflection-cancel');
    const saveBtn = panelEl.querySelector('.agent-panel-reflection-save');
    if (!editorInput || !cancelBtn || !saveBtn) return;

    autosizeTextarea(editorInput);
    if (typeof editorInput.focus === 'function') {
        editorInput.focus();
    }
    if (typeof editorInput.setSelectionRange === 'function') {
        const end = editorInput.value.length;
        editorInput.setSelectionRange(end, end);
    }

    editorInput.addEventListener('input', () => {
        autosizeTextarea(editorInput);
    });
    editorInput.addEventListener('keydown', event => {
        if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
            event.preventDefault();
            saveBtn.click();
        }
    });

    cancelBtn.onclick = () => {
        resetReflectionBody(panelEl);
        bodyEl.textContent = currentSummary || 'No reflection memory yet.';
    };

    saveBtn.onclick = async () => {
        if (!state.currentSessionId) return;
        saveBtn.disabled = true;
        cancelBtn.disabled = true;
        try {
            const reflection = await updateAgentReflection(
                state.currentSessionId,
                instanceId,
                editorInput.value,
            );
            await syncReflectionState(instanceId, roleId, reflection, panelEl);
            sysLog(`Updated reflection memory for ${roleId || instanceId}`, 'log-info');
        } catch (e) {
            saveBtn.disabled = false;
            cancelBtn.disabled = false;
            sysLog(`Failed to update reflection memory: ${e.message}`, 'log-error');
        }
    };
}


function resetReflectionBody(panelEl) {
    const bodyEl = panelEl.querySelector('.agent-panel-reflection-body');
    if (!bodyEl) return;
    bodyEl.dataset.mode = '';
    bodyEl.innerHTML = '';
}

function setReflectionActionButtonsDisabled(panelEl, disabled) {
    const editBtn = panelEl.querySelector('.agent-panel-reflection-edit');
    const deleteBtn = panelEl.querySelector('.agent-panel-reflection-delete');
    if (editBtn) editBtn.disabled = disabled;
    if (deleteBtn) deleteBtn.disabled = disabled;
}

function autosizeTextarea(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.max(textarea.scrollHeight, 140)}px`;
}

function escapeHtml(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
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
