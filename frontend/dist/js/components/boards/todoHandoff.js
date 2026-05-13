/**
 * components/boards/todoHandoff.js
 * Prompt review flow for board TODO handoff.
 */
import {
    fetchOrchestrationConfig,
    fetchRoleConfigOptions,
    previewRequestChangesBoardTodo,
    previewStartBoardTodo,
    requestBoardTodoChanges,
    startBoardTodo,
} from '../../core/api.js';
import { t } from '../../utils/i18n.js';
import { showToast } from '../../utils/feedback.js';
import { escapeHtml } from '../newSessionDraftIcons.js';

export async function reviewAndStartBoardTodo({ todoId, workspaceId }) {
    const preview = await previewStartBoardTodo(todoId, {
        view_workspace_id: String(workspaceId || '').trim() || null,
        queue_if_full: true,
    });
    const runtimeOptions = await loadRuntimeOptions(preview);
    return openHandoffDialog({
        preview,
        runtimeOptions,
        actionLabelKey: 'board_todos.action.start',
        titleKey: 'board_todos.dialog.start_title',
        showTopology: true,
        buildPayload: (state, options) => normalizeStartPayload(state, options),
        submitPayload: payload => startBoardTodo(todoId, payload),
    });
}

export async function reviewAndRequestChangesBoardTodo({
    todoId,
    workspaceId,
    feedback,
}) {
    const normalizedFeedback = String(feedback || '').trim();
    if (!normalizedFeedback) {
        return null;
    }
    const preview = await previewRequestChangesBoardTodo(todoId, {
        feedback: normalizedFeedback,
        view_workspace_id: String(workspaceId || '').trim() || null,
        queue_if_full: true,
    });
    const runtimeOptions = await loadRuntimeOptions(preview);
    return openHandoffDialog({
        preview,
        runtimeOptions,
        actionLabelKey: 'board_todos.action.request_changes',
        titleKey: 'board_todos.dialog.request_changes_title',
        showTopology: false,
        buildPayload: state => normalizeRequestChangesPayload(state, normalizedFeedback),
        submitPayload: payload => requestBoardTodoChanges(todoId, payload),
    });
}

function openHandoffDialog({
    preview,
    runtimeOptions,
    actionLabelKey,
    titleKey,
    showTopology,
    buildPayload,
    submitPayload,
}) {
    const overlay = document.createElement('div');
    overlay.className = 'board-todo-start-backdrop';
    overlay.setAttribute('role', 'presentation');
    const state = {
        prompt: String(preview?.prompt || ''),
        viewWorkspaceId: String(preview?.view_workspace_id || '').trim(),
        sessionMode: runtimeOptions.sessionMode,
        normalRootRoleId: runtimeOptions.normalRootRoleId,
        orchestrationPresetId: runtimeOptions.orchestrationPresetId,
        yolo: runtimeOptions.yolo,
        thinkingEnabled: runtimeOptions.thinkingEnabled,
        thinkingEffort: runtimeOptions.thinkingEffort,
        executionPolicy: String(preview?.execution_policy || 'fork_git_worktree'),
        runtimeTargetId: String(preview?.runtime_target_id || runtimeOptions.runtimeTargetId || '').trim(),
        queueIfFull: preview?.queue_preview?.queue_if_full !== false,
        error: '',
        submitting: false,
    };

    const readState = () => {
        state.prompt = String(overlay.querySelector('[data-board-todo-start-prompt]')?.value || '');
        state.normalRootRoleId = String(overlay.querySelector('[data-board-todo-normal-role]')?.value || '').trim();
        state.orchestrationPresetId = String(overlay.querySelector('[data-board-todo-orchestration-preset]')?.value || '').trim();
        state.yolo = overlay.querySelector('[data-board-todo-yolo]')?.checked === true;
        state.thinkingEnabled = overlay.querySelector('[data-board-todo-thinking-enabled]')?.checked === true;
        state.thinkingEffort = normalizeThinkingEffort(
            overlay.querySelector('[data-board-todo-thinking-effort]')?.value,
        );
        state.executionPolicy = normalizeExecutionPolicy(
            overlay.querySelector('[data-board-todo-execution-policy]')?.value,
        );
        state.runtimeTargetId = String(overlay.querySelector('[data-board-todo-runtime-target]')?.value || '').trim();
        state.queueIfFull = overlay.querySelector('[data-board-todo-queue-if-full]')?.checked === true;
    };

    const render = () => {
        overlay.innerHTML = renderHandoffDialog({
            preview,
            runtimeOptions,
            state,
            actionLabelKey,
            titleKey,
            showTopology,
        });
    };

    const close = (resolve, value = null) => {
        overlay.remove();
        resolve(value);
    };

    return new Promise(resolve => {
        overlay.addEventListener('keydown', event => {
            if (event.key === 'Escape' && !state.submitting) {
                close(resolve);
            }
        });
        overlay.addEventListener('click', async event => {
            if (state.submitting) {
                return;
            }
            if (event.target === overlay) {
                close(resolve);
                return;
            }
            const action = event.target?.closest?.('[data-board-todo-start-action]');
            if (!action) {
                return;
            }
            readState();
            state.error = '';
            const actionName = String(action.dataset.boardTodoStartAction || '').trim();
            if (actionName === 'cancel') {
                close(resolve);
                return;
            }
            if (actionName === 'mode-normal' || actionName === 'mode-orchestration') {
                state.sessionMode = actionName === 'mode-orchestration' ? 'orchestration' : 'normal';
                render();
                return;
            }
            if (actionName !== 'submit') {
                return;
            }
            const payload = buildPayload(state, runtimeOptions);
            if (!payload.final_prompt) {
                showToast({ tone: 'danger', message: t('board_todos.error.prompt_required') });
                render();
                return;
            }
            state.submitting = true;
            render();
            try {
                const item = await submitPayload(payload);
                close(resolve, item);
            } catch (error) {
                state.submitting = false;
                state.error = error?.message || String(error || '');
                showToast({ tone: 'danger', message: state.error });
                render();
            }
        });
        overlay.addEventListener('change', event => {
            if (event.target?.matches?.('[data-board-todo-normal-role]')) {
                readState();
                state.sessionMode = 'normal';
            }
            if (event.target?.matches?.('[data-board-todo-orchestration-preset]')) {
                readState();
                state.sessionMode = 'orchestration';
            }
            if (event.target?.matches?.('[data-board-todo-thinking-enabled]')) {
                readState();
                render();
            }
            if (event.target?.matches?.('[data-board-todo-execution-policy], [data-board-todo-runtime-target], [data-board-todo-queue-if-full]')) {
                readState();
                render();
            }
        });
        overlay.addEventListener('input', event => {
            if (event.target?.matches?.('[data-board-todo-start-prompt]')) {
                const hasPrompt = String(event.target.value || '').trim().length > 0;
                overlay.querySelector('[data-board-todo-mention-hint]')?.classList.toggle('is-hidden', hasPrompt);
            }
        });
        render();
        document.body.appendChild(overlay);
        overlay.querySelector('[data-board-todo-start-prompt]')?.focus();
    });
}

function renderHandoffDialog({
    preview,
    runtimeOptions,
    state,
    actionLabelKey,
    titleKey,
    showTopology,
}) {
    const normalActive = state.sessionMode !== 'orchestration';
    return `
        <div class="board-todo-start-modal" role="dialog" aria-modal="true" aria-labelledby="board-todo-start-title">
            <header class="board-todo-start-header">
                <div>
                    <h3 id="board-todo-start-title">${escapeHtml(t(titleKey))}</h3>
                    <p>${escapeHtml(formatPreviewMessage(preview))}</p>
                </div>
                <button class="board-todos-column-icon-btn" type="button" data-board-todo-start-action="cancel" aria-label="${escapeHtml(t('settings.action.cancel'))}">×</button>
            </header>
            <div class="board-todo-start-body">
                ${state.error ? `<div class="board-todo-handoff-error">${escapeHtml(state.error)}</div>` : ''}
                ${renderHandoffComposer({
                    preview,
                    runtimeOptions,
                    state,
                    normalActive,
                    actionLabelKey,
                    showTopology,
                })}
            </div>
        </div>
    `;
}

function renderHandoffComposer({
    preview,
    runtimeOptions,
    state,
    normalActive,
    actionLabelKey,
    showTopology,
}) {
    const hasPrompt = String(state.prompt || '').trim().length > 0;
    return `
        <div class="board-todo-start-composer input-container is-new-session-draft-composer">
            <div class="input-wrapper">
                <textarea
                    data-board-todo-start-prompt
                    placeholder="${escapeHtml(t('composer.placeholder'))}"
                    rows="1"
                    ${state.submitting ? 'disabled' : ''}
                >${escapeHtml(state.prompt)}</textarea>
                <div
                    class="new-session-draft-mention-hint ${hasPrompt ? 'is-hidden' : ''}"
                    data-board-todo-mention-hint
                >
                    ${renderMentionHint()}
                </div>
                <div class="composer-actions" aria-label="${escapeHtml(t('composer.send_title'))}">
                    <button
                        type="button"
                        class="board-todo-start-send-btn"
                        data-board-todo-start-action="submit"
                        title="${escapeHtml(t(actionLabelKey))}"
                        aria-label="${escapeHtml(t(actionLabelKey))}"
                        ${state.submitting ? 'disabled' : ''}
                    >
                        ${renderSendIcon()}
                        <span class="send-btn-label">${escapeHtml(t(actionLabelKey))}</span>
                    </button>
                </div>
                <div class="input-footer-hint">${escapeHtml(t('composer.hint'))}</div>
            </div>
            ${renderExecutionControls({ preview, runtimeOptions, state, showExecutionPolicy: showTopology })}
            <div class="input-controls">
                ${showTopology ? renderTopologyControls({ runtimeOptions, state, normalActive }) : ''}
                <label class="composer-mode-toggle" title="${escapeHtml(t('composer.yolo_title'))}">
                    <input type="checkbox" data-board-todo-yolo ${state.yolo ? 'checked' : ''} ${state.submitting ? 'disabled' : ''}>
                    <span class="composer-mode-check" aria-hidden="true"></span>
                    <span class="composer-mode-copy"><span class="composer-mode-title">YOLO</span></span>
                </label>
                <label class="composer-mode-toggle has-inline-select" title="${escapeHtml(t('composer.thinking_title'))}">
                    <input type="checkbox" data-board-todo-thinking-enabled ${state.thinkingEnabled ? 'checked' : ''} ${state.submitting ? 'disabled' : ''}>
                    <span class="composer-mode-check" aria-hidden="true"></span>
                    <span class="composer-mode-copy">
                        <span class="composer-mode-title">${escapeHtml(t('composer.thinking'))}</span>
                        <span class="composer-mode-inline" ${state.thinkingEnabled ? '' : 'hidden'}>
                            <span class="composer-mode-inline-label">${escapeHtml(t('composer.effort'))}</span>
                            <select class="composer-mode-inline-select" data-board-todo-thinking-effort ${state.submitting ? 'disabled' : ''}>
                                ${renderThinkingOptions(state.thinkingEffort)}
                            </select>
                        </span>
                    </span>
                </label>
            </div>
        </div>
    `;
}

function renderExecutionControls({ preview, runtimeOptions, state, showExecutionPolicy }) {
    const queuePreview = preview?.queue_preview || {};
    const diagnostics = arrayOrEmpty(preview?.diagnostics)
        .map(message => String(message || '').trim())
        .filter(Boolean);
    const templateSource = String(preview?.template_source || '').trim();
    return `
        <div class="board-todo-handoff-execution">
            ${showExecutionPolicy ? `<label class="composer-preset-field">
                <span class="composer-preset-label">${escapeHtml(t('board_todos.handoff.execution_policy'))}</span>
                <select class="composer-preset-select" data-board-todo-execution-policy ${state.submitting ? 'disabled' : ''}>
                    <option value="fork_git_worktree" ${state.executionPolicy === 'fork_git_worktree' ? 'selected' : ''}>${escapeHtml(t('board_todos.handoff.execution_policy_fork'))}</option>
                    <option value="current_workspace" ${state.executionPolicy === 'current_workspace' ? 'selected' : ''}>${escapeHtml(t('board_todos.handoff.execution_policy_current'))}</option>
                </select>
            </label>` : ''}
            <label class="composer-preset-field">
                <span class="composer-preset-label">${escapeHtml(t('board_todos.handoff.runtime_target'))}</span>
                <select class="composer-preset-select" data-board-todo-runtime-target ${state.submitting ? 'disabled' : ''}>
                    ${runtimeOptions.runtimeTargetOptions.map(option => `
                        <option value="${escapeHtml(option.value)}" ${option.value === state.runtimeTargetId ? 'selected' : ''}>${escapeHtml(option.label)}</option>
                    `).join('')}
                </select>
            </label>
            <label class="composer-mode-toggle board-todo-handoff-queue-toggle">
                <input type="checkbox" data-board-todo-queue-if-full ${state.queueIfFull ? 'checked' : ''} ${state.submitting ? 'disabled' : ''}>
                <span class="composer-mode-check" aria-hidden="true"></span>
                <span class="composer-mode-copy"><span class="composer-mode-title">${escapeHtml(t('board_todos.handoff.queue_if_full'))}</span></span>
            </label>
            <div class="board-todo-handoff-meta">
                ${templateSource ? `<span>${escapeHtml(t('board_todos.handoff.template_source'))}: ${escapeHtml(templateSource)}</span>` : ''}
                <span>${escapeHtml(formatQueuePreview(queuePreview, state.queueIfFull))}</span>
            </div>
            ${diagnostics.length ? `
                <div class="board-todo-handoff-diagnostics">
                    ${diagnostics.map(message => `<div>${escapeHtml(message)}</div>`).join('')}
                </div>
            ` : ''}
        </div>
    `;
}

function renderTopologyControls({ runtimeOptions, state, normalActive }) {
    return `
        <div class="composer-topology" title="${escapeHtml(t('composer.session_mode_title'))}">
            <span class="composer-topology-label">${escapeHtml(normalActive ? t('composer.mode_normal') : t('composer.mode_orchestration'))}</span>
            <div class="composer-segmented" role="group" aria-label="${escapeHtml(t('composer.session_mode'))}">
                <button type="button" class="composer-segmented-btn ${normalActive ? 'active' : ''}" data-board-todo-start-action="mode-normal" ${state.submitting ? 'disabled' : ''}>${escapeHtml(t('composer.mode_normal'))}</button>
                <button type="button" class="composer-segmented-btn ${normalActive ? '' : 'active'}" data-board-todo-start-action="mode-orchestration" ${state.submitting ? 'disabled' : ''}>${escapeHtml(t('composer.mode_orchestration'))}</button>
            </div>
            ${renderRoleField({ runtimeOptions, state, normalActive })}
            ${renderPresetField({ runtimeOptions, state, normalActive })}
        </div>
    `;
}

function renderMentionHint() {
    return `
        <span class="new-session-mention-chip">
            <span>${escapeHtml(t('new_session_draft.mention.prefix'))}</span>
            <span class="new-session-mention-action">${escapeHtml(t('new_session_draft.mention.repository'))}</span>
            <span class="new-session-mention-separator" aria-hidden="true">/</span>
            <span>${escapeHtml(t('new_session_draft.mention.files'))}</span>
            <span class="new-session-mention-separator" aria-hidden="true">/</span>
            <span>${escapeHtml(t('new_session_draft.mention.skills'))}</span>
        </span>
        <span class="new-session-collab-chip">${escapeHtml(t('new_session_draft.mention.collaboration'))}</span>
    `;
}

function renderSendIcon() {
    return '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M22 2 11 13m11-11-7 20-4-9-9-4 20-7Z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>';
}

function renderRoleField({ runtimeOptions, state, normalActive }) {
    return `
        <label class="composer-preset-field" ${normalActive ? '' : 'hidden'}>
            <span class="composer-preset-label">${escapeHtml(t('composer.role'))}</span>
            <select class="composer-preset-select" data-board-todo-normal-role ${state.submitting ? 'disabled' : ''}>
                ${runtimeOptions.normalRoleOptions.map(option => `
                    <option value="${escapeHtml(option.value)}" ${option.value === state.normalRootRoleId ? 'selected' : ''}>${escapeHtml(option.label)}</option>
                `).join('')}
            </select>
        </label>
    `;
}

function renderPresetField({ runtimeOptions, state, normalActive }) {
    return `
        <label class="composer-preset-field" ${normalActive ? 'hidden' : ''}>
            <span class="composer-preset-label">${escapeHtml(t('composer.preset'))}</span>
            <select class="composer-preset-select" data-board-todo-orchestration-preset ${state.submitting ? 'disabled' : ''}>
                ${runtimeOptions.orchestrationPresetOptions.map(option => `
                    <option value="${escapeHtml(option.value)}" ${option.value === state.orchestrationPresetId ? 'selected' : ''}>${escapeHtml(option.label)}</option>
                `).join('')}
            </select>
        </label>
    `;
}

function renderThinkingOptions(value) {
    return ['minimal', 'low', 'medium', 'high']
        .map(effort => `<option value="${effort}" ${effort === value ? 'selected' : ''}>${escapeHtml(t(`composer.effort.${effort}`))}</option>`)
        .join('');
}

function formatPreviewMessage(preview) {
    const parts = [];
    const boardWorkspaceId = String(preview?.board_workspace_id || '').trim();
    if (boardWorkspaceId) {
        parts.push(`${t('board_todos.detail.board_workspace')}: ${boardWorkspaceId}`);
    }
    const viewWorkspaceId = String(preview?.view_workspace_id || '').trim();
    if (viewWorkspaceId && viewWorkspaceId !== boardWorkspaceId) {
        parts.push(`${t('board_todos.detail.view_workspace')}: ${viewWorkspaceId}`);
    }
    const runId = String(preview?.run_id || '').trim();
    if (runId) {
        parts.push(`${t('board_todos.detail.run')}: ${runId}`);
    }
    return parts.join(' · ');
}

async function loadRuntimeOptions(preview) {
    const roleOptions = await fetchRoleConfigOptions().catch(() => null);
    const orchestrationConfig = await fetchOrchestrationConfig().catch(() => null);
    const normalRoleOptions = normalizeRoleOptions(
        arrayOrEmpty(preview?.normal_mode_roles).length
            ? preview.normal_mode_roles
            : roleOptions?.normal_mode_roles,
    );
    const orchestrationPresetOptions = normalizePresetOptions(
        arrayOrEmpty(preview?.orchestration_presets).length
            ? preview.orchestration_presets
            : orchestrationConfig?.presets,
    );
    const sessionMode = normalizeOptionalSessionMode(preview?.session_mode);
    const thinking = preview?.thinking && typeof preview.thinking === 'object'
        ? preview.thinking
        : {};
    return {
        sessionMode,
        normalRoleOptions: normalRoleOptions.length
            ? normalRoleOptions
            : [{ value: '', label: t('composer.no_roles') }],
        normalRootRoleId: String(
            preview?.normal_root_role_id
            || roleOptions?.main_agent_role_id
            || normalRoleOptions[0]?.value
            || '',
        ),
        orchestrationPresetOptions: orchestrationPresetOptions.length
            ? orchestrationPresetOptions
            : [{ value: '', label: t('composer.no_presets') }],
        orchestrationPresetId: String(
            preview?.orchestration_preset_id
            || orchestrationConfig?.default_orchestration_preset_id
            || orchestrationPresetOptions[0]?.value
            || '',
        ),
        yolo: preview?.yolo !== false,
        thinkingEnabled: thinking.enabled === true,
        thinkingEffort: normalizeThinkingEffort(thinking.effort),
        runtimeTargetId: String(preview?.runtime_target_id || '').trim(),
        runtimeTargetOptions: normalizeRuntimeTargetOptions(preview?.runtime_target_options),
    };
}

function normalizeStartPayload(state, runtimeOptions) {
    const finalPrompt = String(state.prompt || '').trim();
    const explicitNormalRoleId = String(state.normalRootRoleId || '').trim();
    const defaultNormalRoleId = String(runtimeOptions.normalRootRoleId || '').trim();
    const explicitPresetId = String(state.orchestrationPresetId || '').trim();
    const defaultPresetId = String(runtimeOptions.orchestrationPresetId || '').trim();
    const selectedNormalRole = explicitNormalRoleId || defaultNormalRoleId;
    const selectedPreset = explicitPresetId || defaultPresetId;
    let sessionMode = normalizeOptionalSessionMode(state.sessionMode);
    if (sessionMode === null && explicitNormalRoleId && explicitNormalRoleId !== defaultNormalRoleId) {
        sessionMode = 'normal';
    }
    if (sessionMode === null && explicitPresetId && explicitPresetId !== defaultPresetId) {
        sessionMode = 'orchestration';
    }
    const selectedRuntimeTargetId = (() => {
        if (sessionMode === 'normal' && selectedNormalRole) {
            return `role:${selectedNormalRole}`;
        }
        if (sessionMode === 'orchestration' && selectedPreset) {
            return `preset:${selectedPreset}`;
        }
        return String(state.runtimeTargetId || runtimeOptions.runtimeTargetId || '').trim();
    })();
    const thinkingEnabled = state.thinkingEnabled === true;
    return {
        view_workspace_id: String(state.viewWorkspaceId || '').trim() || null,
        final_prompt: finalPrompt,
        execution_policy: normalizeExecutionPolicy(state.executionPolicy),
        runtime_target_id: selectedRuntimeTargetId || null,
        queue_if_full: state.queueIfFull === true,
        session_mode: sessionMode,
        normal_root_role_id: sessionMode === 'normal'
            ? selectedNormalRole || null
            : null,
        orchestration_preset_id: sessionMode === 'orchestration'
            ? selectedPreset || null
            : null,
        yolo: state.yolo === true,
        thinking: {
            enabled: thinkingEnabled,
            effort: thinkingEnabled
                ? normalizeThinkingEffort(state.thinkingEffort)
                : null,
        },
    };
}

function normalizeRequestChangesPayload(state, feedback) {
    const thinkingEnabled = state.thinkingEnabled === true;
    return {
        view_workspace_id: String(state.viewWorkspaceId || '').trim() || null,
        feedback: String(feedback || '').trim(),
        final_prompt: String(state.prompt || '').trim(),
        execution_policy: normalizeExecutionPolicy(state.executionPolicy),
        runtime_target_id: String(state.runtimeTargetId || '').trim() || null,
        queue_if_full: state.queueIfFull === true,
        yolo: state.yolo === true,
        thinking: {
            enabled: thinkingEnabled,
            effort: thinkingEnabled
                ? normalizeThinkingEffort(state.thinkingEffort)
                : null,
        },
    };
}

function normalizeRuntimeTargetOptions(options) {
    const normalized = arrayOrEmpty(options)
        .map(option => {
            const value = String(option?.target_id || option?.value || '').trim();
            if (!value) {
                return null;
            }
            return {
                value,
                label: String(option?.label || value),
            };
        })
        .filter(Boolean);
    return normalized.length
        ? normalized
        : [
            { value: 'role:main_agent', label: 'Main Agent' },
            { value: 'preset:default', label: 'Default orchestration' },
        ];
}

function normalizeExecutionPolicy(value) {
    const policy = String(value || '').trim();
    return policy === 'current_workspace' ? 'current_workspace' : 'fork_git_worktree';
}

function formatQueuePreview(queuePreview, queueIfFull) {
    if (queuePreview?.slot_available !== false) {
        return t('board_todos.handoff.slot_available');
    }
    if (queueIfFull) {
        return t('board_todos.handoff.will_queue');
    }
    return t('board_todos.handoff.slot_full');
}

function normalizeRoleOptions(options) {
    return arrayOrEmpty(options)
        .map(option => {
            const value = String(option?.role_id || option?.id || option?.value || '').trim();
            if (!value) {
                return null;
            }
            return {
                value,
                label: String(option?.name || option?.label || value),
            };
        })
        .filter(Boolean);
}

function normalizePresetOptions(options) {
    return arrayOrEmpty(options)
        .map(option => {
            const value = String(option?.preset_id || option?.id || option?.value || '').trim();
            if (!value) {
                return null;
            }
            return {
                value,
                label: String(option?.name || option?.label || value),
            };
        })
        .filter(Boolean);
}

function normalizeOptionalSessionMode(value) {
    const mode = String(value || '').trim();
    if (mode === 'normal' || mode === 'orchestration') {
        return mode;
    }
    return null;
}

function normalizeThinkingEffort(value) {
    const effort = String(value || '').trim();
    return ['minimal', 'low', 'medium', 'high'].includes(effort) ? effort : 'medium';
}

function arrayOrEmpty(value) {
    return Array.isArray(value) ? value : [];
}
