/**
 * components/boards/todoHandoff.js
 * Prompt review flow for board TODO handoff.
 */
import {
    fetchOrchestrationConfig,
    fetchRoleConfigOptions,
    previewStartBoardTodo,
    startBoardTodo,
} from '../../core/api.js';
import { t } from '../../utils/i18n.js';
import { showToast } from '../../utils/feedback.js';
import { escapeHtml } from '../newSessionDraftIcons.js';

export async function reviewAndStartBoardTodo({ todoId, workspaceId }) {
    const preview = await previewStartBoardTodo(todoId, {
        view_workspace_id: String(workspaceId || '').trim() || null,
    });
    const runtimeOptions = await loadRuntimeOptions(preview);
    const values = await openStartDialog({ preview, runtimeOptions });
    if (!values?.final_prompt) {
        return null;
    }
    return startBoardTodo(todoId, values);
}

function openStartDialog({ preview, runtimeOptions }) {
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
    };

    const render = () => {
        overlay.innerHTML = renderStartDialog({ preview, runtimeOptions, state });
    };

    const close = (resolve, value = null) => {
        overlay.remove();
        resolve(value);
    };

    return new Promise(resolve => {
        overlay.addEventListener('keydown', event => {
            if (event.key === 'Escape') {
                close(resolve);
            }
        });
        overlay.addEventListener('click', event => {
            if (event.target === overlay) {
                close(resolve);
                return;
            }
            const action = event.target?.closest?.('[data-board-todo-start-action]');
            if (!action) {
                return;
            }
            readState();
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
            if (actionName === 'submit') {
                const payload = normalizeStartPayload(state, runtimeOptions);
                if (!payload.final_prompt) {
                    showToast({ tone: 'danger', message: t('board_todos.error.prompt_required') });
                    render();
                    return;
                }
                close(resolve, payload);
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

function renderStartDialog({ preview, runtimeOptions, state }) {
    const normalActive = state.sessionMode !== 'orchestration';
    return `
        <div class="board-todo-start-modal" role="dialog" aria-modal="true" aria-labelledby="board-todo-start-title">
            <header class="board-todo-start-header">
                <div>
                    <h3 id="board-todo-start-title">${escapeHtml(t('board_todos.dialog.start_title'))}</h3>
                    <p>${escapeHtml(formatPreviewMessage(preview))}</p>
                </div>
                <button class="board-todos-column-icon-btn" type="button" data-board-todo-start-action="cancel" aria-label="${escapeHtml(t('settings.action.cancel'))}">×</button>
            </header>
            <div class="board-todo-start-body">
                ${renderStartComposer({ runtimeOptions, state, normalActive })}
            </div>
        </div>
    `;
}

function renderStartComposer({ runtimeOptions, state, normalActive }) {
    const hasPrompt = String(state.prompt || '').trim().length > 0;
    return `
        <div class="board-todo-start-composer input-container is-new-session-draft-composer">
            <div class="input-wrapper">
                <textarea
                    data-board-todo-start-prompt
                    placeholder="${escapeHtml(t('composer.placeholder'))}"
                    rows="1"
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
                        title="${escapeHtml(t('board_todos.action.start'))}"
                        aria-label="${escapeHtml(t('board_todos.action.start'))}"
                    >
                        ${renderSendIcon()}
                        <span class="send-btn-label">${escapeHtml(t('board_todos.action.start'))}</span>
                    </button>
                </div>
                <div class="input-footer-hint">${escapeHtml(t('composer.hint'))}</div>
            </div>
            <div class="input-controls">
                <div class="composer-topology" title="${escapeHtml(t('composer.session_mode_title'))}">
                    <span class="composer-topology-label">${escapeHtml(normalActive ? t('composer.mode_normal') : t('composer.mode_orchestration'))}</span>
                    <div class="composer-segmented" role="group" aria-label="${escapeHtml(t('composer.session_mode'))}">
                        <button type="button" class="composer-segmented-btn ${normalActive ? 'active' : ''}" data-board-todo-start-action="mode-normal">${escapeHtml(t('composer.mode_normal'))}</button>
                        <button type="button" class="composer-segmented-btn ${normalActive ? '' : 'active'}" data-board-todo-start-action="mode-orchestration">${escapeHtml(t('composer.mode_orchestration'))}</button>
                    </div>
                    ${renderRoleField({ runtimeOptions, state, normalActive })}
                    ${renderPresetField({ runtimeOptions, state, normalActive })}
                </div>
                <label class="composer-mode-toggle" title="${escapeHtml(t('composer.yolo_title'))}">
                    <input type="checkbox" data-board-todo-yolo ${state.yolo ? 'checked' : ''}>
                    <span class="composer-mode-check" aria-hidden="true"></span>
                    <span class="composer-mode-copy"><span class="composer-mode-title">YOLO</span></span>
                </label>
                <label class="composer-mode-toggle has-inline-select" title="${escapeHtml(t('composer.thinking_title'))}">
                    <input type="checkbox" data-board-todo-thinking-enabled ${state.thinkingEnabled ? 'checked' : ''}>
                    <span class="composer-mode-check" aria-hidden="true"></span>
                    <span class="composer-mode-copy">
                        <span class="composer-mode-title">${escapeHtml(t('composer.thinking'))}</span>
                        <span class="composer-mode-inline" ${state.thinkingEnabled ? '' : 'hidden'}>
                            <span class="composer-mode-inline-label">${escapeHtml(t('composer.effort'))}</span>
                            <select class="composer-mode-inline-select" data-board-todo-thinking-effort>
                                ${renderThinkingOptions(state.thinkingEffort)}
                            </select>
                        </span>
                    </span>
                </label>
            </div>
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
            <select class="composer-preset-select" data-board-todo-normal-role>
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
            <select class="composer-preset-select" data-board-todo-orchestration-preset>
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
    const thinkingEnabled = state.thinkingEnabled === true;
    return {
        view_workspace_id: String(state.viewWorkspaceId || '').trim() || null,
        final_prompt: finalPrompt,
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
