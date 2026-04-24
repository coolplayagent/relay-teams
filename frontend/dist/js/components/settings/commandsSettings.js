/**
 * components/settings/commandsSettings.js
 * Command catalog and editor settings panel.
 */
import { createCommand, fetchCommandCatalog, updateCommand } from '../../core/api.js';
import { t } from '../../utils/i18n.js';
import { showToast } from '../../utils/feedback.js';
import { errorToPayload, logError } from '../../utils/logger.js';

const PROJECT_SOURCES = [
    { value: 'relay_teams', labelKey: 'settings.commands.source_relay_teams' },
    { value: 'claude', labelKey: 'settings.commands.source_claude' },
    { value: 'codex', labelKey: 'settings.commands.source_codex' },
    { value: 'opencode', labelKey: 'settings.commands.source_opencode' },
];

let activeLoadRequestId = 0;
let commandCatalog = normalizeCommandCatalog(null);
let commandEditorMode = 'list';
let editingCommand = null;
let commandPathTouched = false;
let commandPreviewVisible = false;
let commandSearchQuery = '';
let commandCatalogLoadErrorMessage = '';
const collapsedCommandGroups = new Map();

export function bindCommandsSettingsHandlers() {
    bindClick('refresh-commands-btn', () => {
        void loadCommandsSettingsPanel();
    });
    bindClick('refresh-command-catalog-btn', () => {
        void loadCommandsSettingsPanel();
    });
    bindClick('add-command-btn', () => {
        openCreateCommandEditor();
    });
    bindClick('cancel-command-btn', () => {
        commandEditorMode = 'list';
        editingCommand = null;
        commandPreviewVisible = false;
        renderCommandsPanel();
    });
    bindClick('preview-command-btn', () => {
        toggleCommandPreview();
    });
    bindClick('save-command-btn', () => {
        void handleSaveCommand();
    });
    for (const ref of catalogCommandRefs()) {
        bindClick(`edit-command-${ref.key}`, () => {
            openEditCommandEditor(ref);
        });
        bindClick(`copy-command-${ref.key}`, () => {
            void copyCommandPath(ref.command);
        });
    }
    for (const group of catalogGroups(commandCatalog)) {
        bindClick(`toggle-command-group-${group.key}`, () => {
            toggleCommandGroup(group.key);
        });
    }

    const searchInput = document.getElementById('command-search-input');
    if (searchInput) {
        searchInput.oninput = () => {
            commandSearchQuery = String(searchInput.value || '').trim();
            renderCommandsPanel();
        };
    }
    const scopeInput = document.getElementById('command-scope-input');
    if (scopeInput) {
        scopeInput.onchange = () => {
            syncCommandEditorFields();
        };
    }
    const sourceInput = document.getElementById('command-source-input');
    if (sourceInput) {
        sourceInput.onchange = () => {
            syncCommandEditorFields();
            suggestCommandPath({ force: !commandPathTouched });
        };
    }
    const nameInput = document.getElementById('command-name-input');
    if (nameInput) {
        nameInput.oninput = () => {
            suggestCommandPath({ force: !commandPathTouched });
        };
    }
    const pathInput = document.getElementById('command-path-input');
    if (pathInput) {
        pathInput.oninput = () => {
            commandPathTouched = true;
        };
    }
    syncCommandsSettingsActions();
}

export async function loadCommandsSettingsPanel() {
    const requestId = ++activeLoadRequestId;
    const root = document.getElementById('commands-status');
    if (!root) {
        return false;
    }
    commandEditorMode = 'list';
    editingCommand = null;
    commandPreviewVisible = false;
    commandCatalogLoadErrorMessage = '';
    root.innerHTML = renderLoading();
    syncCommandsSettingsActions();
    try {
        const response = await fetchCommandCatalog();
        if (requestId !== activeLoadRequestId) {
            return false;
        }
        commandCatalog = normalizeCommandCatalog(response);
        renderCommandsPanel();
        return true;
    } catch (error) {
        if (requestId !== activeLoadRequestId) {
            return false;
        }
        logError(
            'frontend.commands_settings.load_failed',
            'Failed to load commands',
            errorToPayload(error),
        );
        commandCatalogLoadErrorMessage = error.message || t('settings.commands.load_failed_copy');
        root.innerHTML = renderLoadFailedState(error);
        bindCommandsSettingsHandlers();
        return false;
    }
}

export function syncCommandsSettingsActions() {
    if (!isCommandsPanelActive()) {
        return;
    }
    const isEditorOpen = commandEditorMode !== 'list';
    setActionDisplay('add-command-btn', !isEditorOpen);
    setActionDisplay('cancel-command-btn', isEditorOpen);
    setActionDisplay('preview-command-btn', isEditorOpen);
    setActionDisplay('save-command-btn', isEditorOpen);
}

function openCreateCommandEditor() {
    commandEditorMode = 'create';
    editingCommand = null;
    commandPathTouched = false;
    commandPreviewVisible = false;
    renderCommandsPanel();
    clearCommandEditor();
    setInputValue('command-source-input', 'relay_teams');
    suggestCommandPath({ force: true });
    syncCommandEditorFields();
}

function openEditCommandEditor(ref) {
    commandEditorMode = 'edit';
    editingCommand = ref.command;
    commandPathTouched = true;
    commandPreviewVisible = false;
    renderCommandsPanel();
    populateCommandEditor(ref.command);
}

function renderCommandsPanel() {
    const root = document.getElementById('commands-status');
    if (!root) {
        return;
    }
    root.innerHTML = commandEditorMode === 'list'
        ? renderCommandCatalog(commandCatalog)
        : renderCommandEditor();
    bindCommandsSettingsHandlers();
}

async function handleSaveCommand() {
    const isUpdate = commandEditorMode === 'edit';
    try {
        const payload = readCommandEditorPayload();
        if (isUpdate) {
            await updateCommand(payload);
        } else {
            await createCommand(payload);
        }
        showToast({
            title: isUpdate ? t('settings.commands.updated') : t('settings.commands.created'),
            message: isUpdate
                ? t('settings.commands.updated_copy')
                : t('settings.commands.created_copy'),
            tone: 'success',
        });
        notifyCommandsUpdated();
    } catch (error) {
        logError(
            'frontend.commands_settings.save_failed',
            'Failed to save command',
            errorToPayload(error),
        );
        showToast({
            title: isUpdate
                ? t('settings.commands.update_failed')
                : t('settings.commands.create_failed'),
            message: error.message || t('settings.commands.save_failed_copy'),
            tone: 'danger',
        });
        return;
    }
    const refreshed = await loadCommandsSettingsPanel();
    if (!refreshed) {
        logError(
            'frontend.commands_settings.refresh_failed_after_save',
            'Failed to refresh command catalog after save',
            {},
        );
        showToast({
            title: t('settings.commands.load_failed'),
            message: commandCatalogLoadErrorMessage || t('settings.commands.load_failed_copy'),
            tone: 'warning',
        });
    }
}

function notifyCommandsUpdated() {
    if (typeof document === 'undefined' || typeof document.dispatchEvent !== 'function') {
        return;
    }
    const event = typeof CustomEvent === 'function'
        ? new CustomEvent('agent-teams-commands-updated')
        : { type: 'agent-teams-commands-updated' };
    document.dispatchEvent(event);
}

function readCommandEditorPayload() {
    const fields = readCommandEditorFields();
    if (commandEditorMode === 'edit') {
        const sourcePath = String(editingCommand?.source_path || '').trim();
        if (!sourcePath) {
            throw new Error(t('settings.commands.source_path_required'));
        }
        return {
            source_path: sourcePath,
            name: fields.name,
            aliases: fields.aliases,
            description: fields.description,
            argument_hint: fields.argumentHint,
            allowed_modes: fields.allowedModes,
            template: fields.template,
        };
    }

    const scope = readInputValue('command-scope-input') === 'global' ? 'global' : 'project';
    const workspaceId = readInputValue('command-workspace-input');
    if (scope === 'project' && !workspaceId) {
        throw new Error(t('settings.commands.workspace_required'));
    }
    return {
        scope,
        workspace_id: scope === 'project' ? workspaceId : null,
        source: scope === 'project' ? readInputValue('command-source-input') || 'relay_teams' : null,
        relative_path: normalizeCommandPathForSubmit(readInputValue('command-path-input')),
        name: fields.name,
        aliases: fields.aliases,
        description: fields.description,
        argument_hint: fields.argumentHint,
        allowed_modes: fields.allowedModes,
        template: fields.template,
    };
}

function readCommandEditorFields() {
    return {
        name: readInputValue('command-name-input'),
        aliases: parseAliases(readInputValue('command-aliases-input')),
        description: readInputValue('command-description-input'),
        argumentHint: readInputValue('command-argument-hint-input'),
        allowedModes: parseAllowedModes(readInputValue('command-allowed-modes-input')),
        template: readInputValue('command-template-input'),
    };
}

function normalizeCommandPathForSubmit(value) {
    const path = String(value || '').trim().replaceAll('\\', '/');
    if (!path || path.toLowerCase().endsWith('.md')) {
        return path;
    }
    return `${path}.md`;
}

function parseAliases(value) {
    return String(value || '')
        .split(',')
        .map(item => item.trim().replace(/^\/+/, ''))
        .filter(Boolean);
}

function parseAllowedModes(value) {
    const modes = String(value || '')
        .split(',')
        .map(item => item.trim())
        .filter(Boolean);
    return modes.length > 0 ? modes : ['normal'];
}

function populateCommandEditor(command) {
    setInputValue('command-name-input', command?.name || '');
    setInputValue('command-aliases-input', formatAliasesInput(command?.aliases));
    setInputValue('command-description-input', command?.description || '');
    setInputValue('command-argument-hint-input', command?.argument_hint || '');
    setInputValue(
        'command-allowed-modes-input',
        Array.isArray(command?.allowed_modes) ? command.allowed_modes.join(', ') : 'normal',
    );
    setInputValue('command-template-input', command?.template || '');
}

function clearCommandEditor() {
    setInputValue('command-name-input', '');
    setInputValue('command-aliases-input', '');
    setInputValue('command-description-input', '');
    setInputValue('command-argument-hint-input', '');
    setInputValue('command-allowed-modes-input', 'normal');
    setInputValue('command-template-input', '');
}

function syncCommandEditorFields() {
    if (commandEditorMode !== 'create') {
        return;
    }
    const scope = readInputValue('command-scope-input') === 'global' ? 'global' : 'project';
    setElementDisplay('command-workspace-field', scope === 'project');
    setElementDisplay('command-source-field', scope === 'project');
}

function suggestCommandPath({ force = false } = {}) {
    if (commandEditorMode !== 'create') {
        return;
    }
    const pathInput = document.getElementById('command-path-input');
    if (!pathInput || (!force && commandPathTouched)) {
        return;
    }
    const name = readInputValue('command-name-input');
    pathInput.value = buildSuggestedPath(name);
}

function buildSuggestedPath(name) {
    const safeName = String(name || '')
        .trim()
        .replace(/^\/+/, '')
        .replaceAll(':', '/')
        .replace(/[^A-Za-z0-9._/-]+/g, '-')
        .replace(/\/+/g, '/')
        .replace(/^-+|-+$/g, '');
    return `${safeName || 'new-command'}.md`;
}

function renderLoading() {
    return `
        <div class="commands-shell">
            <div class="commands-panel-toolbar">
                <span>${escapeHtml(t('settings.commands.loading'))}</span>
            </div>
        </div>
    `;
}

function renderCommandCatalog(catalog) {
    const groups = catalogGroups(catalog);
    const filteredGroups = filterCatalogGroups(groups, commandSearchQuery);
    const total = groups.reduce((count, group) => {
        return count + group.refs.length;
    }, 0);
    const visibleTotal = filteredGroups.reduce((count, group) => count + group.refs.length, 0);
    if (total === 0) {
        return renderCatalogEmptyState(total);
    }
    return `
        <div class="commands-shell">
            ${renderCatalogToolbar({
                total,
                workspaceCount: groups.filter(group => group.kind === 'workspace').length,
            })}
            ${renderCommandSearch()}
            <div class="commands-catalog">
                ${
                    visibleTotal > 0 || filteredGroups.length > 0
                        ? filteredGroups.map(group => renderCommandGroup(group)).join('')
                        : renderSearchEmpty()
                }
            </div>
        </div>
    `;
}

function renderCatalogToolbar({ total, workspaceCount }) {
    return `
        <div class="commands-panel-toolbar">
            <strong class="commands-total">
                ${escapeHtml(formatCount(total))}
                <span aria-hidden="true">&middot;</span>
                ${escapeHtml(formatWorkspaceCount(workspaceCount))}
            </strong>
            <button class="secondary-btn section-action-btn commands-refresh-btn" id="refresh-command-catalog-btn" type="button">
                ${escapeHtml(t('settings.commands.refresh'))}
            </button>
        </div>
    `;
}

function renderCommandSearch() {
    return `
        <div class="commands-search-row">
            <input
                type="search"
                id="command-search-input"
                value="${escapeHtml(commandSearchQuery)}"
                placeholder="${escapeHtml(t('settings.commands.search_placeholder'))}"
                autocomplete="off"
                spellcheck="false"
            >
        </div>
    `;
}

function renderCatalogEmptyState(total) {
    return `
        <div class="commands-shell">
            ${renderCatalogToolbar({ total, workspaceCount: 0 })}
            <div class="commands-empty-card">
                <div class="commands-empty-icon" aria-hidden="true">/_</div>
                <h4>${escapeHtml(t('settings.commands.empty'))}</h4>
                <p>${escapeHtml(t('settings.commands.empty_copy'))}</p>
                <p>${escapeHtml(t('settings.commands.empty_hint'))}</p>
            </div>
        </div>
    `;
}

function renderCommandGroup(group) {
    const rows = Array.isArray(group.refs) ? group.refs : [];
    const isSearchActive = Boolean(commandSearchQuery);
    const isCollapsed = !isSearchActive && isCommandGroupCollapsed(group);
    const hintKey = isCollapsed
        ? 'settings.commands.expand_hint'
        : 'settings.commands.collapse_hint';
    return `
        <section class="commands-group ${isCollapsed ? 'commands-group-collapsed' : ''}">
            <button class="commands-group-header" id="toggle-command-group-${escapeHtml(group.key)}" type="button" aria-expanded="${isCollapsed ? 'false' : 'true'}">
                <div class="commands-group-title">
                    <span class="commands-group-chevron" aria-hidden="true"></span>
                    <h4>${escapeHtml(group.title)}</h4>
                    <span class="commands-count-pill">${escapeHtml(String(rows.length))}</span>
                    ${group.subtitle ? `<span class="commands-group-path">${escapeHtml(group.subtitle)}</span>` : ''}
                </div>
                <span class="commands-group-hint">${escapeHtml(t(hintKey))}</span>
            </button>
            ${
                isCollapsed
                    ? renderCollapsedSummary(rows, group)
                    : renderExpandedGroupRows(rows, group)
            }
        </section>
    `;
}

function renderCollapsedSummary(rows, group) {
    if (rows.length === 0) {
        return renderCommandGroupEmpty(group.emptyCopy);
    }
    return `
        <div class="commands-group-summary">
            ${escapeHtml(formatCount(rows.length))}
            <span aria-hidden="true">&middot;</span>
            ${escapeHtml(t('settings.commands.expand_hint'))}
        </div>
    `;
}

function renderSearchEmpty() {
    return `
        <div class="commands-search-empty">
            ${escapeHtml(t('settings.commands.no_matches'))}
        </div>
    `;
}

function renderExpandedGroupRows(rows, group) {
    if (rows.length === 0) {
        return renderCommandGroupEmpty(group.emptyCopy);
    }
    return `
        <div class="commands-table">
            ${renderCommandTableHead()}
            <div class="commands-list">${rows.map(ref => renderCommandRow(ref)).join('')}</div>
        </div>
    `;
}

function renderCommandTableHead() {
    return `
        <div class="commands-table-head" role="row">
            <span>${escapeHtml(t('settings.commands.table_command'))}</span>
            <span>${escapeHtml(t('settings.commands.table_description'))}</span>
            <span>${escapeHtml(t('settings.commands.table_argument'))}</span>
            <span>${escapeHtml(t('settings.commands.table_scope'))}</span>
            <span>${escapeHtml(t('settings.commands.table_source_path'))}</span>
            <span>${escapeHtml(t('settings.commands.table_actions'))}</span>
        </div>
    `;
}

function renderCommandGroupEmpty(copy) {
    return `
        <div class="commands-group-empty">
            <span aria-hidden="true">[]</span>
            ${escapeHtml(copy)}
        </div>
    `;
}

function renderCommandRow(ref) {
    const command = ref.command;
    const name = String(command?.name || '').trim();
    const aliases = normalizeAliases(command?.aliases);
    const description = String(command?.description || '').trim();
    const hint = String(command?.argument_hint || '').trim();
    const sourcePath = String(command?.source_path || '').trim();
    const scope = String(command?.scope || '').trim();
    return `
        <article class="command-row" role="row">
            <div class="command-row-name">
                <strong>${escapeHtml(formatCommandName(name))}</strong>
                <span>${escapeHtml(aliases.length > 0
                    ? `${t('settings.commands.alias_label')} ${formatAliasList(aliases)}`
                    : t('settings.commands.no_aliases'))}</span>
            </div>
            <div class="command-row-description">${escapeHtml(description || t('settings.commands.no_description'))}</div>
            <div class="command-row-hint">${hint ? `<span>${escapeHtml(hint)}</span>` : '<span>-</span>'}</div>
            <div class="command-row-scope"><span>${escapeHtml(scope)}</span></div>
            <div class="command-row-path" title="${escapeHtml(sourcePath)}">${escapeHtml(compactPath(sourcePath))}</div>
            <div class="command-row-actions">
                <button class="command-copy-btn" id="copy-command-${escapeHtml(ref.key)}" type="button" title="${escapeHtml(t('settings.commands.copy_path'))}" aria-label="${escapeHtml(t('settings.commands.copy_path'))}">
                    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
                        <path d="M8 8h10v12H8z" stroke="currentColor" stroke-width="1.7"></path>
                        <path d="M6 16H4V4h10v2" stroke="currentColor" stroke-width="1.7"></path>
                    </svg>
                </button>
                <button class="secondary-btn section-action-btn command-row-action" id="edit-command-${escapeHtml(ref.key)}" type="button">
                    ${escapeHtml(t('settings.commands.edit'))}
                </button>
            </div>
        </article>
    `;
}

function renderCommandEditor() {
    const workspaces = Array.isArray(commandCatalog.workspaces) ? commandCatalog.workspaces : [];
    const workspaceOptions = workspaces.filter(workspaceHasWritableRoot).map(workspace => {
        const workspaceId = String(workspace?.workspace_id || '').trim();
        const rootPath = String(workspace?.root_path || '').trim();
        const label = rootPath ? `${workspaceId} - ${rootPath}` : workspaceId;
        return `<option value="${escapeHtml(workspaceId)}">${escapeHtml(label)}</option>`;
    }).join('');
    const isUpdate = commandEditorMode === 'edit';
    return `
        <div class="command-editor-panel">
            <section class="command-form-card">
                <div class="command-form-head">
                    <h4>${escapeHtml(isUpdate ? t('settings.commands.editor_edit_title') : t('settings.commands.editor_create_title'))}</h4>
                    <p>${escapeHtml(isUpdate ? t('settings.commands.editor_edit_copy') : t('settings.commands.editor_create_copy'))}</p>
                </div>
                <div class="command-editor-grid">
                    ${isUpdate ? renderUpdateSourceFields() : renderCreateLocationFields(workspaceOptions)}
                    ${renderCommandTextFields(isUpdate)}
                </div>
                ${renderCommandPreviewBlock()}
            </section>
        </div>
    `;
}

function workspaceHasWritableRoot(workspace) {
    return String(workspace?.root_path || '').trim().length > 0
        && workspace?.can_create_commands !== false;
}

function renderCreateLocationFields(workspaceOptions) {
    return `
        <div class="form-group">
            <label for="command-scope-input">${escapeHtml(t('settings.commands.scope'))}</label>
            <select id="command-scope-input">
                <option value="project">${escapeHtml(t('settings.commands.scope_project'))}</option>
                <option value="global">${escapeHtml(t('settings.commands.scope_global'))}</option>
            </select>
        </div>
        <div class="form-group" id="command-workspace-field">
            <label for="command-workspace-input">${escapeHtml(t('settings.commands.workspace'))}</label>
            <select id="command-workspace-input">
                ${workspaceOptions || `<option value="">${escapeHtml(t('settings.commands.no_workspaces'))}</option>`}
            </select>
        </div>
        <div class="form-group" id="command-source-field">
            <label for="command-source-input">${escapeHtml(t('settings.commands.source'))}</label>
            <select id="command-source-input">
                ${PROJECT_SOURCES.map(source => (
                    `<option value="${escapeHtml(source.value)}">${escapeHtml(t(source.labelKey))}</option>`
                )).join('')}
            </select>
        </div>
        <div class="form-group">
            <label for="command-path-input">${escapeHtml(t('settings.commands.path'))}</label>
            <input type="text" id="command-path-input" placeholder="opsx/propose.md" autocomplete="off" spellcheck="false">
        </div>
    `;
}

function renderUpdateSourceFields() {
    const sourcePath = String(editingCommand?.source_path || '').trim();
    return `
        <div class="form-group form-group-span-2">
            <label for="command-source-path-input">${escapeHtml(t('settings.commands.source_path'))}</label>
            <input type="text" id="command-source-path-input" value="${escapeHtml(sourcePath)}" readonly spellcheck="false">
        </div>
        <div class="command-source-meta form-group-span-2">
            <span>${escapeHtml(t('settings.commands.source_meta'))}</span>
            <strong>${escapeHtml(String(editingCommand?.scope || ''))}</strong>
            <strong>${escapeHtml(commandSourceFolder(sourcePath))}</strong>
        </div>
    `;
}

function renderCommandTextFields(isUpdate) {
    return `
        <div class="form-group">
            <label for="command-name-input">${escapeHtml(t('settings.commands.name'))}</label>
            <input type="text" id="command-name-input" placeholder="opsx:propose" autocomplete="off" spellcheck="false">
        </div>
        <div class="form-group">
            <label for="command-description-input">${escapeHtml(t('settings.commands.description'))}</label>
            <input type="text" id="command-description-input" autocomplete="off">
        </div>
        <div class="form-group">
            <label for="command-argument-hint-input">${escapeHtml(t('settings.commands.argument_hint'))}</label>
            <input type="text" id="command-argument-hint-input" placeholder="<change-id>" autocomplete="off" spellcheck="false">
        </div>
        <div class="form-group">
            <label for="command-allowed-modes-input">${escapeHtml(t('settings.commands.allowed_modes'))}</label>
            <input type="text" id="command-allowed-modes-input" value="normal" autocomplete="off" spellcheck="false">
        </div>
        <div class="form-group form-group-span-2">
            <label for="command-aliases-input">${escapeHtml(t('settings.commands.aliases'))}</label>
            <input type="text" id="command-aliases-input" placeholder="opsx/propose, ops/propose" autocomplete="off" spellcheck="false">
        </div>
        <div class="form-group form-group-span-2">
            <label for="command-template-input">${escapeHtml(t('settings.commands.template'))}</label>
            <textarea class="config-textarea command-template-textarea" id="command-template-input" spellcheck="false"></textarea>
        </div>
    `;
}

function renderCommandPreviewBlock() {
    return `
        <div class="command-preview-panel" id="command-preview-panel" style="display:none;">
            <div class="command-preview-label">${escapeHtml(t('settings.commands.preview'))}</div>
            <pre id="command-preview-output"></pre>
        </div>
    `;
}

function toggleCommandPreview() {
    const panel = document.getElementById('command-preview-panel');
    const output = document.getElementById('command-preview-output');
    if (!panel || !output) {
        return;
    }
    commandPreviewVisible = !commandPreviewVisible;
    panel.style.display = commandPreviewVisible ? 'block' : 'none';
    if (commandPreviewVisible) {
        output.textContent = buildCommandPreview();
    }
}

function buildCommandPreview() {
    const fields = readCommandEditorFields();
    const frontMatter = [
        `name: ${fields.name}`,
        fields.aliases.length > 0 ? `aliases: [${fields.aliases.join(', ')}]` : '',
        `description: ${fields.description}`,
        `argument_hint: ${fields.argumentHint}`,
        `allowed_modes: [${fields.allowedModes.join(', ')}]`,
    ].filter(Boolean).join('\n');
    return `---\n${frontMatter}\n---\n${fields.template}`;
}

async function copyCommandPath(command) {
    const sourcePath = String(command?.source_path || '').trim();
    if (!sourcePath) {
        return;
    }
    try {
        if (!globalThis.navigator?.clipboard?.writeText) {
            throw new Error('clipboard unavailable');
        }
        await globalThis.navigator.clipboard.writeText(sourcePath);
        showToast({
            title: t('settings.commands.copy_path_done'),
            message: compactPath(sourcePath),
            tone: 'success',
        });
    } catch (error) {
        logError(
            'frontend.commands_settings.copy_failed',
            'Failed to copy command path',
            errorToPayload(error),
        );
        showToast({
            title: t('settings.commands.copy_path_failed'),
            message: sourcePath,
            tone: 'danger',
        });
    }
}

function renderLoadFailedState(error) {
    return `
        <div class="commands-shell">
            <div class="commands-empty-card commands-empty-card-compact">
                <h4>${escapeHtml(t('settings.commands.load_failed'))}</h4>
                <p>${escapeHtml(error.message || t('settings.commands.load_failed_copy'))}</p>
                <button class="secondary-btn section-action-btn" id="refresh-commands-btn" type="button">
                    ${escapeHtml(t('settings.commands.refresh'))}
                </button>
            </div>
        </div>
    `;
}

function normalizeCommandCatalog(response) {
    return {
        app_commands: normalizeCommandResponse(response?.app_commands),
        workspaces: Array.isArray(response?.workspaces)
            ? response.workspaces.map(workspace => ({
                workspace_id: String(workspace?.workspace_id || '').trim(),
                root_path: String(workspace?.root_path || '').trim(),
                can_create_commands: workspace?.can_create_commands !== false,
                commands: normalizeCommandResponse(workspace?.commands),
            })).filter(workspace => workspace.workspace_id)
            : [],
    };
}

function normalizeCommandResponse(response) {
    if (Array.isArray(response)) {
        return response;
    }
    if (Array.isArray(response?.commands)) {
        return response.commands;
    }
    return [];
}

function catalogGroups(catalog) {
    return [
        {
            key: 'app',
            kind: 'app',
            title: t('settings.commands.global_group'),
            subtitle: '',
            refs: catalogCommandRefsForApp(catalog),
            emptyCopy: t('settings.commands.global_empty'),
        },
        ...catalogCommandRefsForWorkspaces(catalog).map(group => {
            const workspace = group.workspace;
            const workspaceId = String(workspace?.workspace_id || '').trim();
            const rootPath = String(workspace?.root_path || '').trim();
            return {
                key: `workspace-${safeDomKey(workspaceId)}`,
                kind: 'workspace',
                title: workspaceId || t('settings.commands.workspace_group'),
                subtitle: rootPath || t('settings.commands.workspace_no_root'),
                refs: group.refs,
                emptyCopy: t('settings.commands.workspace_empty'),
            };
        }),
    ];
}

function filterCatalogGroups(groups, query) {
    const normalizedQuery = normalizeSearchText(query);
    if (!normalizedQuery) {
        return groups;
    }
    return groups.map(group => {
        const groupMatches = normalizeSearchText(`${group.title} ${group.subtitle}`)
            .includes(normalizedQuery);
        const refs = groupMatches
            ? group.refs
            : group.refs.filter(ref => commandMatchesQuery(ref.command, normalizedQuery));
        return { ...group, refs };
    }).filter(group => group.refs.length > 0);
}

function commandMatchesQuery(command, normalizedQuery) {
    return normalizeSearchText([
        command?.name,
        normalizeAliases(command?.aliases).join(' '),
        command?.description,
        command?.argument_hint,
        command?.scope,
        command?.source_path,
    ].join(' ')).includes(normalizedQuery);
}

function normalizeSearchText(value) {
    return String(value || '').trim().toLowerCase();
}

function toggleCommandGroup(key) {
    const current = collapsedCommandGroups.get(key);
    const next = current === undefined ? !defaultCommandGroupCollapsed(key) : !current;
    collapsedCommandGroups.set(key, next);
    renderCommandsPanel();
}

function isCommandGroupCollapsed(group) {
    if (collapsedCommandGroups.has(group.key)) {
        return collapsedCommandGroups.get(group.key) === true;
    }
    return defaultCommandGroupCollapsed(group.key);
}

function defaultCommandGroupCollapsed(key) {
    return key !== 'app';
}

function catalogCommandRefs() {
    return [
        ...catalogCommandRefsForApp(commandCatalog),
        ...catalogCommandRefsForWorkspaces(commandCatalog).flatMap(group => group.refs),
    ];
}

function safeDomKey(value) {
    return String(value || 'workspace')
        .trim()
        .replace(/[^A-Za-z0-9_-]+/g, '-')
        .replace(/^-+|-+$/g, '') || 'workspace';
}

function catalogCommandRefsForApp(catalog) {
    const commands = Array.isArray(catalog.app_commands) ? catalog.app_commands : [];
    return commands.map((command, index) => ({
        key: `app-${index}`,
        command,
    }));
}

function catalogCommandRefsForWorkspaces(catalog) {
    const workspaces = Array.isArray(catalog.workspaces) ? catalog.workspaces : [];
    return workspaces.map((workspace, workspaceIndex) => ({
        workspace,
        refs: normalizeCommandResponse(workspace?.commands).map((command, commandIndex) => ({
            key: `workspace-${workspaceIndex}-${commandIndex}`,
            command,
        })),
    }));
}

function normalizeAliases(value) {
    return Array.isArray(value)
        ? value.map(item => String(item || '').trim()).filter(Boolean)
        : [];
}

function formatCommandName(name) {
    return name ? `/${name}` : '/';
}

function formatAliasList(aliases) {
    return aliases.map(alias => formatCommandName(alias)).join(', ');
}

function formatAliasesInput(value) {
    return normalizeAliases(value).map(alias => formatCommandName(alias)).join(', ');
}

function formatCount(count) {
    return count === 1
        ? t('settings.commands.count_one')
        : t('settings.commands.count_many').replace('{count}', String(count));
}

function formatWorkspaceCount(count) {
    return count === 1
        ? t('settings.commands.workspace_count_one')
        : t('settings.commands.workspace_count_many').replace('{count}', String(count));
}

function compactPath(path) {
    const safePath = String(path || '').trim().replaceAll('\\', '/');
    if (safePath.length <= 42) {
        return safePath;
    }
    return `...${safePath.slice(-39)}`;
}

function commandSourceFolder(sourcePath) {
    const normalized = String(sourcePath || '').replaceAll('\\', '/');
    for (const marker of [
        '.relay-teams/commands',
        '.claude/commands',
        '.codex/commands',
        '.opencode/command',
        '.opencode/commands',
        'commands',
    ]) {
        if (normalized.includes(marker)) {
            return marker;
        }
    }
    return '';
}

function isCommandsPanelActive() {
    const panel = document.getElementById('commands-panel');
    return !panel || panel.style.display !== 'none';
}

function bindClick(id, handler) {
    const element = document.getElementById(id);
    if (element) {
        element.onclick = handler;
    }
}

function setElementDisplay(id, visible) {
    const element = document.getElementById(id);
    if (element) {
        element.style.display = visible ? '' : 'none';
    }
}

function setActionDisplay(id, visible) {
    const element = document.getElementById(id);
    if (element) {
        element.style.display = visible ? 'inline-flex' : 'none';
    }
}

function readInputValue(id) {
    const element = document.getElementById(id);
    return String(element?.value || '').trim();
}

function setInputValue(id, value) {
    const element = document.getElementById(id);
    if (element) {
        element.value = String(value || '');
    }
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
