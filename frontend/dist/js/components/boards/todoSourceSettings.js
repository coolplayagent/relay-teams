/**
 * components/boards/todoSourceSettings.js
 * Source settings flow for the workspace TODO board.
 */
import {
    createBoardTodoSource,
    deleteBoardTodoSource,
    deleteSourceBoardTodoHandoffTemplate,
    fetchBoardTodoHandoffTemplates,
    fetchBoardTodoSources,
    updateBoardTodoSource,
    upsertSourceBoardTodoHandoffTemplate,
    upsertWorkspaceBoardTodoHandoffTemplate,
} from '../../core/api.js';
import { formatMessage, t } from '../../utils/i18n.js';
import { showConfirmDialog, showFormDialog, showToast } from '../../utils/feedback.js';
import { escapeHtml } from '../newSessionDraftIcons.js';

const DISPLAY_MODES = {
    GROUPED: 'grouped',
    MIXED: 'mixed',
};

export async function openBoardTodoSourceSettings({ workspaceId, displayMode = DISPLAY_MODES.GROUPED, onDisplayModeChange = null }) {
    const safeWorkspaceId = String(workspaceId || '').trim();
    if (!safeWorkspaceId) {
        return false;
    }
    const dialog = createSourceSettingsDialog({
        workspaceId: safeWorkspaceId,
        displayMode,
        onDisplayModeChange,
    });
    return dialog.open();
}

function createSourceSettingsDialog({ workspaceId, displayMode, onDisplayModeChange }) {
    const overlay = document.createElement('div');
    overlay.className = 'board-todo-source-settings-backdrop';
    overlay.setAttribute('role', 'presentation');
    const state = {
        settings: null,
        templates: null,
        sourcesChanged: false,
        displayMode: normalizeDisplayMode(displayMode),
        loading: true,
        error: '',
    };

    const render = () => {
        overlay.innerHTML = renderDialog(state);
    };

    const load = async () => {
        state.loading = true;
        state.error = '';
        render();
        try {
            const [settings, templates] = await Promise.all([
                fetchBoardTodoSources({ workspaceId }),
                fetchBoardTodoHandoffTemplates({ workspaceId }),
            ]);
            state.settings = settings;
            state.templates = templates;
        } catch (error) {
            state.error = error?.message || String(error);
        } finally {
            state.loading = false;
            render();
        }
    };

    const close = resolve => {
        overlay.remove();
        resolve(state.sourcesChanged);
    };

    return {
        open() {
            return new Promise(resolve => {
                overlay.addEventListener('click', event => {
                    if (event.target === overlay) {
                        close(resolve);
                    }
                });
                overlay.addEventListener('click', event => {
                    const action = event.target?.closest?.('[data-board-todo-source-action]');
                    if (!action) {
                        return;
                    }
                    const actionName = String(action.dataset.boardTodoSourceAction || '').trim();
                    const sourceId = String(action.dataset.sourceId || '').trim();
                    if (actionName === 'close') {
                        close(resolve);
                    } else if (actionName === 'view-mode') {
                        const mode = normalizeDisplayMode(action.dataset.mode);
                        state.displayMode = mode;
                        if (typeof onDisplayModeChange === 'function') {
                            onDisplayModeChange(mode);
                        }
                        render();
                    } else if (actionName === 'add') {
                        void handleAddSource({ workspaceId, state, render, load });
                    } else if (actionName === 'edit') {
                        void handleEditSource({ workspaceId, sourceId, state, render, load });
                    } else if (actionName === 'toggle') {
                        void handleToggleSource({ workspaceId, sourceId, state, render, load });
                    } else if (actionName === 'delete') {
                        void handleDeleteSource({ sourceId, state, load });
                    } else if (actionName === 'template-workspace') {
                        void handleWorkspaceTemplate({ workspaceId, kind: action.dataset.kind, state, load });
                    } else if (actionName === 'template-source') {
                        void handleSourceTemplate({ workspaceId, sourceId, kind: action.dataset.kind, state, load });
                    } else if (actionName === 'template-delete') {
                        void handleDeleteTemplate({ templateId: action.dataset.templateId, state, load });
                    }
                });
                render();
                document.body.appendChild(overlay);
                void load();
            });
        },
    };
}

function renderDialog(state) {
    const settings = state.settings || {};
    const sources = sourceEntries(settings);
    return `
        <div class="board-todo-source-settings-modal" role="dialog" aria-modal="true" aria-labelledby="board-todo-source-settings-title">
            <header class="board-todo-source-settings-header">
                <div>
                    <h3 id="board-todo-source-settings-title">${escapeHtml(t('board_todos.settings.title'))}</h3>
                    <p>${escapeHtml(sourceSettingsSummary(settings, sources))}</p>
                </div>
                <button class="board-todo-source-settings-close" type="button" data-board-todo-source-action="close" aria-label="${escapeHtml(t('settings.action.cancel'))}">${renderCloseIcon()}</button>
            </header>
            <div class="board-todo-source-settings-body">
                ${renderDisplayModeSettings(state.displayMode)}
                ${renderSourceSettingsStatus(state)}
                ${renderSourceList(sources, state.loading)}
                ${renderTemplateSettings(state, sources)}
            </div>
            <footer class="board-todo-source-settings-footer">
                <button class="board-todos-tool-btn" type="button" data-board-todo-source-action="add">${escapeHtml(t('board_todos.sources.add'))}</button>
                <button class="board-todos-primary-btn" type="button" data-board-todo-source-action="close">${escapeHtml(t('settings.action.done'))}</button>
            </footer>
        </div>
    `;
}

function renderDisplayModeSettings(displayMode) {
    return `
        <section class="board-todo-settings-section board-todo-settings-view-row">
            <h4 class="board-todo-settings-section-title">${escapeHtml(t('board_todos.view.label'))}</h4>
            <div class="board-todos-view-toggle board-todo-settings-view-toggle" role="group" aria-label="${escapeHtml(t('board_todos.view.label'))}">
                ${renderDisplayModeButton({
                    mode: DISPLAY_MODES.GROUPED,
                    label: t('board_todos.view.grouped'),
                    currentMode: displayMode,
                })}
                ${renderDisplayModeButton({
                    mode: DISPLAY_MODES.MIXED,
                    label: t('board_todos.view.mixed'),
                    currentMode: displayMode,
                })}
            </div>
        </section>
    `;
}

function renderDisplayModeButton({ mode, label, currentMode }) {
    const active = mode === currentMode;
    return `
        <button
            type="button"
            class="${active ? 'is-active' : ''}"
            data-board-todo-source-action="view-mode"
            data-mode="${escapeHtml(mode)}"
            aria-pressed="${active ? 'true' : 'false'}"
        >${escapeHtml(label)}</button>
    `;
}

function renderSourceSettingsStatus(state) {
    if (state.loading) {
        return `<div class="board-todo-source-settings-status">${escapeHtml(t('board_todos.sources.loading'))}</div>`;
    }
    if (state.error) {
        return `<div class="board-todo-source-settings-status is-error">${escapeHtml(state.error)}</div>`;
    }
    const diagnostics = Array.isArray(state.settings?.diagnostics)
        ? state.settings.diagnostics.map(message => String(message || '').trim()).filter(Boolean)
        : [];
    if (!diagnostics.length) {
        return '';
    }
    return `
        <div class="board-todo-source-settings-status">
            ${diagnostics.map(message => `<span>${escapeHtml(message)}</span>`).join('')}
        </div>
    `;
}

function renderSourceList(sources, loading) {
    if (loading) {
        return '';
    }
    if (!sources.length) {
        return `
            <section class="board-todo-settings-section board-todo-source-list-section" aria-labelledby="board-todo-source-list-title">
                <div class="board-todo-settings-section-head">
                    <h4 id="board-todo-source-list-title">${escapeHtml(t('board_todos.sources.list_title'))}</h4>
                </div>
                <div class="settings-empty-state board-todo-source-empty">
                    <h4>${escapeHtml(t('board_todos.sources.empty_title'))}</h4>
                    <p>${escapeHtml(t('board_todos.sources.empty_copy'))}</p>
                </div>
            </section>
        `;
    }
    return `
        <section class="board-todo-settings-section board-todo-source-list-section" aria-labelledby="board-todo-source-list-title">
            <div class="board-todo-settings-section-head">
                <h4 id="board-todo-source-list-title">${escapeHtml(t('board_todos.sources.list_title'))}</h4>
                <span>${escapeHtml(formatMessage('board_todos.sources.summary', { count: sources.length }))}</span>
            </div>
            <div class="board-todo-source-records">
                ${sources.map(entry => renderSourceRecord(entry)).join('')}
            </div>
        </section>
    `;
}

function renderSourceRecord(entry) {
    const source = entry.source || {};
    const state = entry.state || null;
    const sourceId = String(source.source_id || '').trim();
    const repository = String(source.repository_full_name || '').trim() || t('board_todos.value.none');
    const enabled = source.enabled !== false;
    const syncStatus = String(state?.last_sync_status || 'idle').trim();
    const finishedAt = state?.last_sync_finished_at
        ? formatDateTime(state.last_sync_finished_at)
        : t('board_todos.value.none');
    return `
        <div class="board-todo-source-record" data-source-id="${escapeHtml(sourceId)}">
            <div class="board-todo-source-provider" aria-hidden="true">
                <img src="/assets/connectors/github.svg" alt="">
            </div>
            <div class="board-todo-source-main">
                <div class="board-todo-source-title-row">
                    <h4>${escapeHtml(source.display_name || repository)}</h4>
                    <div class="board-todo-source-chips">
                        <span class="board-todo-source-chip ${enabled ? 'is-enabled' : 'is-disabled'}">${escapeHtml(enabled ? t('settings.field.enabled') : t('settings.roles.disabled'))}</span>
                        <span class="board-todo-source-chip">${escapeHtml(t('board_todos.sources.github'))}</span>
                    </div>
                </div>
                <div class="board-todo-source-repo" title="${escapeHtml(repository)}">${escapeHtml(repository)}</div>
                <div class="board-todo-source-sync" title="${escapeHtml(syncStatus)}">
                    <span>${escapeHtml(formatMessage('board_todos.sources.sync_status', { status: syncStatus }))}</span>
                    <span class="board-todo-source-sync-separator">/</span>
                    <span>${escapeHtml(finishedAt)}</span>
                </div>
            </div>
            <div class="board-todo-source-actions">
                <button class="board-todo-source-inline-action" type="button" data-board-todo-source-action="toggle" data-source-id="${escapeHtml(sourceId)}">${escapeHtml(enabled ? t('board_todos.sources.disable') : t('board_todos.sources.enable'))}</button>
                <button class="board-todo-source-inline-action" type="button" data-board-todo-source-action="edit" data-source-id="${escapeHtml(sourceId)}">${escapeHtml(t('settings.action.edit'))}</button>
                <button class="board-todo-source-inline-action is-danger" type="button" data-board-todo-source-action="delete" data-source-id="${escapeHtml(sourceId)}">${escapeHtml(t('settings.action.delete'))}</button>
            </div>
        </div>
    `;
}

async function handleAddSource({ workspaceId, state, render, load }) {
    const values = await sourceFormValues();
    if (!values) {
        return;
    }
    try {
        await createBoardTodoSource({
            workspace_id: workspaceId,
            kind: 'github_issues',
            display_name: values.display_name,
            repository_full_name: values.repository_full_name,
            enabled: values.enabled,
        });
        state.sourcesChanged = true;
        render();
        await load();
    } catch (error) {
        showSourceMutationError({ error, state, render });
    }
}

function renderTemplateSettings(state, sources) {
    if (state.loading) {
        return '';
    }
    return `
        <section class="board-todo-settings-section board-todo-handoff-template-settings">
            <div class="board-todo-settings-section-head">
                <h4>${escapeHtml(t('board_todos.templates.title'))}</h4>
                <span>${escapeHtml(t('board_todos.templates.description'))}</span>
            </div>
            <div class="board-todo-template-actions">
                ${renderTemplateButton({
                    action: 'template-workspace',
                    kind: 'start',
                    label: t('board_todos.templates.workspace_start'),
                })}
                ${renderTemplateButton({
                    action: 'template-workspace',
                    kind: 'request_changes',
                    label: t('board_todos.templates.workspace_request_changes'),
                })}
            </div>
            ${sources.length ? `
                <div class="board-todo-template-source-list" role="table" aria-label="${escapeHtml(t('board_todos.templates.source_overrides'))}">
                    <div class="board-todo-template-source-head" role="row">
                        <span role="columnheader">${escapeHtml(t('board_todos.templates.source'))}</span>
                        <span role="columnheader">${escapeHtml(t('board_todos.templates.start'))}</span>
                        <span role="columnheader">${escapeHtml(t('board_todos.templates.request_changes'))}</span>
                    </div>
                    ${sources.map(entry => renderSourceTemplateRow(entry, state)).join('')}
                </div>
            ` : ''}
        </section>
    `;
}

function renderSourceTemplateRow(entry, state) {
    const source = entry.source || {};
    const sourceId = String(source.source_id || '').trim();
    const displayName = String(source.display_name || source.repository_full_name || sourceId);
    return `
        <div class="board-todo-template-source-row" role="row">
            <span class="board-todo-template-source-name" role="cell">${escapeHtml(displayName)}</span>
            <div class="board-todo-template-source-cell" role="cell">
                ${renderSourceTemplateCell({ state, sourceId, kind: 'start', label: t('board_todos.templates.start') })}
            </div>
            <div class="board-todo-template-source-cell" role="cell">
                ${renderSourceTemplateCell({
                    state,
                    sourceId,
                    kind: 'request_changes',
                    label: t('board_todos.templates.request_changes'),
                })}
            </div>
        </div>
    `;
}

function renderSourceTemplateCell({ state, sourceId, kind, label }) {
    const template = findTemplate(state.templates, { scope: 'source', sourceId, kind });
    return `
        ${renderTemplateButton({
            action: 'template-source',
            kind,
            sourceId,
            label,
        })}
        ${template ? `
            <button
                class="board-todo-source-inline-action is-danger"
                type="button"
                data-board-todo-source-action="template-delete"
                data-template-id="${escapeHtml(template.template_id)}"
            >${escapeHtml(t('settings.action.delete'))}</button>
        ` : ''}
    `;
}

function renderTemplateButton({ action, kind, label, sourceId = '' }) {
    return `
        <button
            class="board-todo-source-inline-action"
            type="button"
            data-board-todo-source-action="${escapeHtml(action)}"
            data-kind="${escapeHtml(kind)}"
            ${sourceId ? `data-source-id="${escapeHtml(sourceId)}"` : ''}
        >${escapeHtml(label)}</button>
    `;
}

function renderCloseIcon() {
    return `
        <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
            <path d="M4.3 4.3 8 8m3.7 3.7L8 8m0 0 3.7-3.7M8 8l-3.7 3.7" fill="none" stroke="currentColor" stroke-linecap="round" stroke-width="1.7"/>
        </svg>
    `;
}

async function handleEditSource({ workspaceId, sourceId, state, render, load }) {
    const source = sourceEntries(state.settings)
        .map(entry => entry.source)
        .find(entry => String(entry?.source_id || '').trim() === sourceId);
    if (!source) {
        return;
    }
    const values = await sourceFormValues(source);
    if (!values) {
        return;
    }
    try {
        await updateBoardTodoSource(sourceId, {
            workspace_id: workspaceId,
            display_name: values.display_name,
            repository_full_name: values.repository_full_name,
            enabled: values.enabled,
        });
        state.sourcesChanged = true;
        render();
        await load();
    } catch (error) {
        showSourceMutationError({ error, state, render });
    }
}

async function handleToggleSource({ workspaceId, sourceId, state, render, load }) {
    const source = sourceEntries(state.settings)
        .map(entry => entry.source)
        .find(entry => String(entry?.source_id || '').trim() === sourceId);
    if (!source) {
        return;
    }
    try {
        await updateBoardTodoSource(sourceId, {
            workspace_id: workspaceId,
            enabled: source.enabled === false,
        });
        state.sourcesChanged = true;
        await load();
    } catch (error) {
        showSourceMutationError({ error, state, render });
    }
}

async function handleDeleteSource({ sourceId, state, load }) {
    const confirmed = await showConfirmDialog({
        title: t('board_todos.sources.delete_title'),
        message: t('board_todos.sources.delete_message'),
        confirmLabel: t('settings.action.delete'),
        tone: 'warning',
    });
    if (!confirmed) {
        return;
    }
    try {
        await deleteBoardTodoSource(sourceId);
        state.sourcesChanged = true;
        await load();
    } catch (error) {
        showToast({
            tone: 'danger',
            message: error?.message || String(error),
        });
    }
}

async function handleWorkspaceTemplate({ workspaceId, kind, state, load }) {
    const values = await templateFormValues(
        findTemplate(state.templates, { scope: 'workspace', kind })?.template || '',
    );
    if (!values) {
        return;
    }
    try {
        await upsertWorkspaceBoardTodoHandoffTemplate({
            workspace_id: workspaceId,
            template_kind: normalizeTemplateKind(kind),
            template: values.template,
        });
        showToast({ tone: 'success', message: t('board_todos.templates.saved') });
        await load();
    } catch (error) {
        showSourceMutationError({ error, state, render: () => {} });
    }
}

async function handleSourceTemplate({ workspaceId, sourceId, kind, state, load }) {
    const values = await templateFormValues(
        findTemplate(state.templates, { scope: 'source', sourceId, kind })?.template || '',
    );
    if (!values) {
        return;
    }
    try {
        await upsertSourceBoardTodoHandoffTemplate(sourceId, {
            workspace_id: workspaceId,
            template_kind: normalizeTemplateKind(kind),
            template: values.template,
        });
        showToast({ tone: 'success', message: t('board_todos.templates.saved') });
        await load();
    } catch (error) {
        showSourceMutationError({ error, state, render: () => {} });
    }
}

async function handleDeleteTemplate({ templateId, state, load }) {
    const safeTemplateId = String(templateId || '').trim();
    if (!safeTemplateId) {
        return;
    }
    try {
        await deleteSourceBoardTodoHandoffTemplate(safeTemplateId);
        showToast({ tone: 'success', message: t('board_todos.templates.saved') });
        await load();
    } catch (error) {
        showToast({ tone: 'danger', message: error?.message || String(error) });
    }
}

function showSourceMutationError({ error, state, render }) {
    const message = error?.message || String(error);
    state.error = message;
    render();
    showToast({
        tone: 'danger',
        message,
    });
}

async function templateFormValues(template = '') {
    return showFormDialog({
        title: t('board_todos.templates.edit_title'),
        confirmLabel: t('settings.action.save'),
        fields: [
            {
                id: 'template',
                label: t('board_todos.templates.template'),
                type: 'textarea',
                rows: 10,
                value: template,
            },
        ],
        submitHandler: payload => {
            const value = String(payload?.template || '').trim();
            if (!value) {
                throw new Error(t('board_todos.error.prompt_required'));
            }
            return { template: value };
        },
    });
}

async function sourceFormValues(source = null) {
    return showFormDialog({
        title: source ? t('board_todos.sources.edit_title') : t('board_todos.sources.add_title'),
        confirmLabel: source ? t('board_todos.sources.save') : t('board_todos.sources.create'),
        fields: [
            {
                id: 'display_name',
                label: t('board_todos.sources.display_name'),
                value: source?.display_name || '',
            },
            {
                id: 'repository_full_name',
                label: t('board_todos.sources.repository'),
                value: source?.repository_full_name || '',
                placeholder: 'owner/repo',
            },
            {
                id: 'enabled',
                label: t('board_todos.sources.enabled'),
                type: 'checkbox',
                value: source ? source.enabled !== false : true,
                description: t('board_todos.sources.enabled_description'),
            },
        ],
        submitHandler: payload => validateSourceForm(payload),
    });
}

function validateSourceForm(payload) {
    const repository = String(payload?.repository_full_name || '').trim();
    if (!/^[^/\s]+\/[^/\s]+$/.test(repository)) {
        throw new Error(t('board_todos.sources.repository_required'));
    }
    const displayName = String(payload?.display_name || '').trim() || repository;
    return {
        display_name: displayName,
        repository_full_name: repository,
        enabled: payload?.enabled === true,
    };
}

function sourceEntries(settings) {
    return (Array.isArray(settings?.sources) ? settings.sources : [])
        .filter(entry => String(entry?.source?.kind || '') === 'github_issues');
}

function templateEntries(settings) {
    return Array.isArray(settings?.templates) ? settings.templates : [];
}

function findTemplate(settings, { scope, sourceId = '', kind }) {
    const normalizedKind = normalizeTemplateKind(kind);
    const normalizedSourceId = String(sourceId || '').trim();
    return templateEntries(settings).find(template => (
        String(template?.scope || '') === scope
        && String(template?.template_kind || '') === normalizedKind
        && String(template?.source_id || '').trim() === normalizedSourceId
    ));
}

function normalizeTemplateKind(kind) {
    return String(kind || '').trim() === 'request_changes' ? 'request_changes' : 'start';
}

function normalizeDisplayMode(value) {
    return value === DISPLAY_MODES.MIXED ? DISPLAY_MODES.MIXED : DISPLAY_MODES.GROUPED;
}

function sourceSettingsSummary(settings, sources) {
    const boardWorkspace = String(settings?.board_workspace_id || '').trim();
    const lines = [
        formatMessage('board_todos.sources.summary', { count: sources.length }),
    ];
    if (boardWorkspace) {
        lines.push(`${t('board_todos.detail.board_workspace')}: ${boardWorkspace}`);
    }
    if (settings?.is_fork_view === true) {
        lines.push(t('board_todos.sources.shared_with_root'));
    }
    return lines.join(' · ');
}

function formatDateTime(value) {
    const date = new Date(value || '');
    if (Number.isNaN(date.getTime())) {
        return t('board_todos.value.none');
    }
    return date.toLocaleString();
}
