/**
 * components/agentPanel/panelFactory.js
 * Panel DOM factory and inject-message bindings.
 */
import {
    deleteAgentReflection,
    refreshAgentReflection,
    stopRun,
    updateAgentReflection,
} from '../../core/api.js';
import { state } from '../../core/state.js';
import { formatMessage, t } from '../../utils/i18n.js';
import { sysLog } from '../../utils/logger.js';
import { getDrawer } from './dom.js';
import { loadAgentHistory } from './history.js';

const REFLECTION_BUTTON_RESET_MS = 2400;

export function createPanel(
    instanceId,
    roleId,
    onClose,
    { host = null, inline = false } = {},
) {
    const mountTarget = host || getDrawer();
    if (!mountTarget) return null;
    void onClose;

    const panelEl = document.createElement('div');
    panelEl.className = inline ? 'agent-panel is-inline' : 'agent-panel';
    panelEl.dataset.instanceId = instanceId;
    panelEl.style.display = 'none';

    panelEl.innerHTML = `
        <div class="agent-panel-topbar">
            <button class="agent-panel-toggle" type="button" aria-expanded="true">
                <div class="agent-panel-title">
                    <div class="panel-role-stack">
                        <span class="agent-panel-role-label panel-role"></span>
                        <span class="agent-panel-instance-id panel-id"></span>
                    </div>
                    <div class="agent-panel-preview"></div>
                </div>
            </button>
            <div class="agent-panel-topbar-meta">
                <span class="agent-panel-top-status"></span>
                <div class="agent-token-usage" data-instance-id="${instanceId}"></div>
            </div>
        </div>
        <div class="agent-panel-content">
            <div class="agent-panel-scroll"></div>
            <details class="agent-panel-diagnostics">
                <summary class="agent-panel-diagnostics-summary">
                    <span class="agent-panel-diagnostics-title">${t('subagent.sections')}</span>
                    <span class="agent-panel-diagnostics-toggle">${t('rounds.expand')}</span>
                </summary>
                <div class="agent-panel-diagnostics-body">
                    <section class="agent-panel-diagnostic-section" data-section="prompt">
                        <div class="agent-panel-tabpane-header">
                            <span class="agent-panel-diagnostic-label">${t('subagent.prompt')}</span>
                            <span class="agent-panel-runtime-prompt-meta agent-panel-section-meta"></span>
                        </div>
                        <div class="agent-panel-section-body agent-panel-runtime-prompt-body">${t('subagent.no_runtime_prompt')}</div>
                    </section>
                    <section class="agent-panel-diagnostic-section" data-section="tools">
                        <div class="agent-panel-tabpane-header">
                            <span class="agent-panel-diagnostic-label">${t('subagent.tools')}</span>
                            <span class="agent-panel-runtime-tools-meta agent-panel-section-meta"></span>
                        </div>
                        <div class="agent-panel-section-body agent-panel-runtime-tools-body">${t('subagent.no_runtime_tools')}</div>
                    </section>
                    <section class="agent-panel-diagnostic-section" data-section="memory">
                        <div class="agent-panel-tabpane-header">
                            <span class="agent-panel-diagnostic-label">${t('subagent.memory')}</span>
                            <span class="agent-panel-reflection-meta agent-panel-section-meta"></span>
                            <div class="agent-panel-section-actions" aria-label="${t('subagent.reflection_actions')}">
                                <button class="agent-panel-icon-btn agent-panel-reflection-edit" type="button" title="${t('subagent.edit_reflection')}" aria-label="${t('subagent.edit_reflection')}">
                                    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                                        <path d="M12 20h9" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
                                        <path d="M16.5 3.5a2.12 2.12 0 113 3L7 19l-4 1 1-4 12.5-12.5z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>
                                    </svg>
                                </button>
                                <button class="agent-panel-icon-btn agent-panel-reflection-delete" type="button" title="${t('subagent.delete_reflection')}" aria-label="${t('subagent.delete_reflection')}">
                                    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                                        <path d="M3 6h18" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
                                        <path d="M8 6V4.5A1.5 1.5 0 019.5 3h5A1.5 1.5 0 0116 4.5V6" stroke="currentColor" stroke-width="1.8"/>
                                        <path d="M6.5 6l1 13a1.5 1.5 0 001.5 1.4h6a1.5 1.5 0 001.5-1.4l1-13" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>
                                        <path d="M10 10.5v6M14 10.5v6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
                                    </svg>
                                </button>
                            </div>
                        </div>
                        <div class="agent-panel-section-body agent-panel-reflection-body">${t('subagent.no_reflection_memory')}</div>
                    </section>
                    <section class="agent-panel-diagnostic-section" data-section="tasks">
                        <div class="agent-panel-tabpane-header">
                            <span class="agent-panel-diagnostic-label">${t('subagent.tasks')}</span>
                            <span class="agent-panel-summary-meta agent-panel-section-meta">
                                <span class="agent-panel-summary-status">${t('subagent.status_idle')}</span>
                                <span class="agent-panel-summary-updated"></span>
                            </span>
                        </div>
                        <div class="agent-panel-section-body agent-panel-summary-body">
                            <div class="agent-panel-summary-tasks">${t('subagent.no_tasks')}</div>
                        </div>
                    </section>
                </div>
            </details>
        </div>
        <div class="agent-panel-bottom-actions">
            <button class="agent-panel-refresh-reflection" type="button" title="${t('subagent.reflect_title')}">${t('subagent.reflect')}</button>
            <button class="agent-panel-stop" type="button" title="${t('subagent.stop_title')}">${t('subagent.stop')}</button>
        </div>
    `;

    const stopBtn = panelEl.querySelector('.agent-panel-stop');
    if (stopBtn) {
        stopBtn.onclick = async () => {
            if (!state.activeRunId) return;
            try {
                await stopRun(state.activeRunId, { scope: 'subagent', instanceId });
                state.pausedSubagent = { runId: state.activeRunId, instanceId, roleId };
                sysLog(formatMessage('subagent.log.paused', { agent: roleId || instanceId }), 'log-info');
            } catch (e) {
                sysLog(formatMessage('subagent.error.pause_failed', { error: e.message }), 'log-error');
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
                ? window.confirm(t('subagent.delete_reflection_confirm'))
                : true;
            if (!confirmed) return;
            setReflectionActionButtonsDisabled(panelEl, true);
            try {
                const reflection = await deleteAgentReflection(state.currentSessionId, instanceId);
                await syncReflectionState(instanceId, roleId, reflection, panelEl);
                sysLog(formatMessage('subagent.log.deleted_reflection', { agent: roleId || instanceId }), 'log-info');
            } catch (e) {
                sysLog(formatMessage('subagent.error.delete_reflection_failed', { error: e.message }), 'log-error');
            } finally {
                setReflectionActionButtonsDisabled(panelEl, false);
            }
        };
    }

    const toggleBtn = panelEl.querySelector('.agent-panel-toggle');
    if (toggleBtn) {
        toggleBtn.onclick = () => {
            const expanded = panelEl.dataset.expanded !== 'true';
            setPanelExpandedState(panelEl, expanded, { manual: true });
        };
    }
    const diagnostics = panelEl.querySelector('.agent-panel-diagnostics');
    if (diagnostics) {
        diagnostics.addEventListener('toggle', () => {
            syncDiagnosticsToggleLabel(panelEl);
        });
    }
    setPanelExpandedState(panelEl, true);
    syncDiagnosticsToggleLabel(panelEl);

    mountTarget.appendChild(panelEl);
    return {
        panelEl,
        scrollEl: panelEl.querySelector('.agent-panel-scroll'),
        instanceId,
        roleId,
        hostEl: mountTarget,
        inline,
        loadedSessionId: '',
        loadedRunId: '',
    };
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
    bodyEl.dataset.mode = 'editing';
    bodyEl.innerHTML = `
        <div class="agent-panel-reflection-editor">
            <textarea class="agent-panel-reflection-editor-input" rows="8" placeholder="${t('subagent.reflection_placeholder')}">${escapeHtml(currentSummary)}</textarea>
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
            bodyEl.textContent = currentSummary || t('subagent.no_reflection_memory');
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
            sysLog(formatMessage('subagent.log.updated_reflection', { agent: roleId || instanceId }), 'log-info');
        } catch (e) {
            saveBtn.disabled = false;
            cancelBtn.disabled = false;
            sysLog(formatMessage('subagent.error.update_reflection_failed', { error: e.message }), 'log-error');
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
        button.textContent = t('subagent.reflecting');
        button.title = t('subagent.reflecting_title');
        return;
    }
    if (nextState === 'success') {
        button.disabled = false;
        button.textContent = t('subagent.reflected');
        button.title = t('subagent.reflected_title');
        return;
    }
    if (nextState === 'error') {
        button.disabled = false;
        button.textContent = t('subagent.retry_reflect');
        button.title = t('subagent.reflect_failed_title');
        return;
    }
      button.disabled = false;
      button.textContent = t('subagent.reflect');
      button.title = t('subagent.reflect_title');
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

export function setPanelExpandedState(panelEl, expanded, { manual = false } = {}) {
    if (!panelEl) {
        return;
    }
    const isExpanded = expanded === true;
    panelEl.dataset.expanded = isExpanded ? 'true' : 'false';
    if (manual) {
        panelEl.dataset.expansionMode = 'manual';
    }
    const toggleBtn = panelEl.querySelector('.agent-panel-toggle');
    const contentEl = panelEl.querySelector('.agent-panel-content');
    if (toggleBtn) {
        toggleBtn.setAttribute('aria-expanded', isExpanded ? 'true' : 'false');
    }
    if (contentEl) {
        contentEl.hidden = !isExpanded;
    }
}

function syncDiagnosticsToggleLabel(panelEl) {
    const diagnostics = panelEl?.querySelector('.agent-panel-diagnostics');
    const toggleEl = panelEl?.querySelector('.agent-panel-diagnostics-toggle');
    if (!diagnostics || !toggleEl) {
        return;
    }
    toggleEl.textContent = diagnostics.open ? t('rounds.collapse') : t('rounds.expand');
}
