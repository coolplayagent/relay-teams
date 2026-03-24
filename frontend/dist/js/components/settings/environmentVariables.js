/**
 * components/settings/environmentVariables.js
 * Runtime environment variable settings panel bindings.
 */
import {
    deleteEnvironmentVariable,
    fetchEnvironmentVariables,
    saveEnvironmentVariable,
} from '../../core/api.js';
import { showConfirmDialog, showToast } from '../../utils/feedback.js';
import { t } from '../../utils/i18n.js';
import { errorToPayload, logError } from '../../utils/logger.js';

const DEFAULT_EXPANDED_SCOPES = {
    system: false,
};

const HIDDEN_APP_ENV_KEYS = new Set([
    'HTTP_PROXY',
    'http_proxy',
    'HTTPS_PROXY',
    'https_proxy',
    'ALL_PROXY',
    'all_proxy',
    'NO_PROXY',
    'no_proxy',
    'SSL_VERIFY',
    'ssl_verify',
]);

let environmentState = {
    variablesByScope: {
        system: [],
        app: [],
    },
    expandedScopes: { ...DEFAULT_EXPANDED_SCOPES },
    editor: {
        visible: false,
        key: '',
        value: '',
        sourceKey: '',
    },
};
let languageBound = false;

export function bindEnvironmentVariableSettingsHandlers() {
    bindActionButton('add-env-btn', handleAddEnvironmentVariable);
    bindActionButton('save-env-btn', handleSaveEnvironmentVariable);
    bindActionButton('cancel-env-btn', handleCancelEnvironmentVariable);
    if (!languageBound && typeof document.addEventListener === 'function') {
        document.addEventListener('agent-teams-language-changed', () => {
            renderEnvironmentVariablesPanel();
        });
        languageBound = true;
    }
}

export async function loadEnvironmentVariablesPanel() {
    try {
        const payload = await fetchEnvironmentVariables();
        environmentState = {
            variablesByScope: normalizeEnvironmentPayload(payload),
            expandedScopes: {
                ...DEFAULT_EXPANDED_SCOPES,
                ...environmentState.expandedScopes,
            },
            editor: {
                visible: false,
                key: '',
                value: '',
                sourceKey: '',
            },
        };
        renderEnvironmentVariablesPanel();
    } catch (error) {
        logError(
            'frontend.environment_variables.load_failed',
            'Failed to load environment variables',
            errorToPayload(error),
        );
        renderEnvironmentFailure(
            error?.message || 'Unable to load environment variables.',
        );
        showToast({
            title: t('settings.proxy.load_failed'),
            message: `Failed to load environment variables: ${error.message}`,
            tone: 'danger',
        });
    }
}

function bindActionButton(id, handler) {
    const button = document.getElementById(id);
    if (button) {
        button.onclick = handler;
    }
}

function normalizeEnvironmentPayload(payload) {
    return {
        app: sortRecords(
            Array.isArray(payload?.app) ? payload.app : [],
            'app',
        ).filter(record => !HIDDEN_APP_ENV_KEYS.has(record.key)),
        system: sortRecords(
            Array.isArray(payload?.system) ? payload.system : [],
            'system',
        ),
    };
}

function sortRecords(records, fallbackScope) {
    return records
        .map(record => ({
            key: String(record?.key || '').trim(),
            value: String(record?.value || ''),
            scope: String(record?.scope || fallbackScope).trim() || fallbackScope,
            value_kind:
                String(record?.value_kind || 'string').trim() || 'string',
        }))
        .filter(record => record.key)
        .sort((left, right) =>
            left.key.localeCompare(right.key, undefined, {
                sensitivity: 'base',
            }),
        );
}

function renderEnvironmentVariablesPanel() {
    renderEnvironmentHelp();
    renderLegacyEditorShell();
    renderEnvironmentGroups();
    renderEnvironmentActionMode(environmentState.editor.visible);
    focusEnvironmentEditorIfNeeded();
}

function renderEnvironmentHelp() {
    const helpEl = document.getElementById('env-variables-help');
    if (!helpEl) {
        return;
    }
    helpEl.textContent = '';
    helpEl.style.display = 'none';
}

function renderLegacyEditorShell() {
    const shell = document.getElementById('env-editor-shell');
    if (shell) {
        shell.style.display = 'none';
        shell.innerHTML = '';
    }
}

function renderEnvironmentGroups() {
    const groupsEl = document.getElementById('environment-variables-groups');
    if (!groupsEl) {
        return;
    }

    groupsEl.innerHTML = [
        renderAppEnvironmentScope(environmentState.variablesByScope.app),
        renderSystemEnvironmentScope(environmentState.variablesByScope.system),
    ].join('');
    bindEnvironmentListHandlers();
}

function renderAppEnvironmentScope(records) {
    const editingExistingRecord = Boolean(environmentState.editor.sourceKey);
    const recordsMarkup =
        records.length === 0 && !environmentState.editor.visible
            ? `
                <div class="env-scope-empty">${escapeHtml(t('settings.env.no_app'))}</div>
            `
            : `
                <div class="env-records">
                    ${records
                        .map(record =>
                            environmentState.editor.visible &&
                            environmentState.editor.sourceKey === record.key
                                ? renderEnvironmentEditorRow()
                                : renderEnvironmentRecord(record),
                        )
                        .join('')}
                    ${
                        environmentState.editor.visible && !editingExistingRecord
                            ? renderEnvironmentEditorRow()
                            : ''
                    }
                </div>
            `;

    return `
        <section class="env-scope-section" data-env-scope="app">
            <div class="env-scope-heading">${escapeHtml(t('settings.env.app'))}</div>
            <div class="env-scope-content" style="display:block;">
                ${recordsMarkup}
            </div>
        </section>
    `;
}

function renderSystemEnvironmentScope(records) {
    const expanded = environmentState.expandedScopes.system === true;
    return `
        <section class="env-scope-section" data-env-scope="system">
            <button class="env-scope-toggle" data-env-toggle-scope="system" type="button" aria-expanded="${expanded ? 'true' : 'false'}">
                <span class="env-scope-toggle-title">${escapeHtml(t('settings.env.system'))}</span>
                <span class="env-scope-toggle-icon">${expanded ? escapeHtml(t('settings.env.hide')) : escapeHtml(t('settings.env.show'))}</span>
            </button>
            <div class="env-scope-content" style="display:${expanded ? 'block' : 'none'};">
                ${
                    records.length === 0
                        ? `
                    <div class="env-scope-empty">${escapeHtml(t('settings.env.no_system'))}</div>
                `
                        : `
                    <div class="env-records">
                        ${records.map(record => renderEnvironmentRecord(record)).join('')}
                    </div>
                `
                }
            </div>
        </section>
    `;
}

function renderEnvironmentEditorRow() {
    return `
        <div class="env-record env-record-editor" data-env-scope="app">
            <input type="hidden" id="env-source-key-input" value="${escapeHtml(environmentState.editor.sourceKey || '')}">
            <div class="env-record-editor-row">
                <input
                    type="text"
                    id="env-key-input"
                    class="env-record-editor-input env-record-editor-input-key"
                    placeholder="${escapeHtml(t('settings.env.key'))}"
                    autocomplete="off"
                    value="${escapeHtml(environmentState.editor.key || '')}"
                >
                <textarea
                    id="env-value-input"
                    class="env-record-editor-input env-record-editor-input-value"
                    placeholder="${escapeHtml(t('settings.env.value'))}"
                    rows="4"
                    spellcheck="false"
                >${escapeHtml(environmentState.editor.value || '')}</textarea>
            </div>
        </div>
    `;
}

function renderEnvironmentRecord(record) {
    const valuePreview = record.value;
    const isEditable = record.scope === 'app';
    return `
        <div class="env-record" data-env-key="${escapeHtml(record.key)}" data-env-scope="${escapeHtml(record.scope)}">
            <div class="env-record-main">
                <div class="env-record-title-row">
                    <div class="env-record-key" title="${escapeHtml(record.key)}">${escapeHtml(record.key)}</div>
                </div>
                <div class="env-record-value" title="${escapeHtml(record.value)}">${escapeHtml(valuePreview)}</div>
            </div>
            ${
                isEditable
                    ? `
                <div class="env-record-actions">
                    <button class="settings-inline-action settings-list-action env-edit-btn" data-env-edit="${escapeHtml(record.scope)}::${escapeHtml(record.key)}" type="button">${escapeHtml(t('settings.env.edit'))}</button>
                    <button class="settings-inline-action settings-list-action settings-list-action-danger env-delete-btn" data-env-delete="${escapeHtml(record.scope)}::${escapeHtml(record.key)}" type="button">${escapeHtml(t('settings.env.delete'))}</button>
                </div>
            `
                    : ''
            }
        </div>
    `;
}

function bindEnvironmentListHandlers() {
    const groupsEl = document.getElementById('environment-variables-groups');
    if (!groupsEl) {
        return;
    }

    groupsEl.querySelectorAll('.env-scope-toggle').forEach(button => {
        button.onclick = () => {
            environmentState.expandedScopes.system =
                environmentState.expandedScopes.system !== true;
            renderEnvironmentGroups();
        };
    });

    groupsEl.querySelectorAll('.env-edit-btn').forEach(button => {
        button.onclick = () => {
            const recordRef = String(button.dataset.envEdit || '').trim();
            openEditorForRecord(recordRef);
        };
    });

    groupsEl.querySelectorAll('.env-delete-btn').forEach(button => {
        button.onclick = () => {
            const recordRef = String(button.dataset.envDelete || '').trim();
            void handleDeleteEnvironmentVariable(recordRef);
        };
    });
}

function openEditorForRecord(recordRef) {
    const [scope, key] = parseRecordRef(recordRef);
    if (scope !== 'app' || !key) {
        return;
    }
    const record = findEnvironmentRecord(scope, key);
    if (!record) {
        return;
    }
    environmentState.editor = {
        visible: true,
        key: record.key,
        value: record.value,
        sourceKey: record.key,
    };
    renderEnvironmentVariablesPanel();
}

function handleAddEnvironmentVariable() {
    environmentState.editor = {
        visible: true,
        key: '',
        value: '',
        sourceKey: '',
    };
    renderEnvironmentVariablesPanel();
}

function handleCancelEnvironmentVariable() {
    environmentState.editor = {
        visible: false,
        key: '',
        value: '',
        sourceKey: '',
    };
    renderEnvironmentVariablesPanel();
}

async function handleSaveEnvironmentVariable() {
    const key = readInputValue('env-key-input');
    const value = readInputValue('env-value-input', false);
    const sourceKey = readInputValue('env-source-key-input');

    if (!key) {
        showToast({
            title: t('settings.env.missing_key'),
            message: t('settings.env.missing_key_message'),
            tone: 'danger',
        });
        return;
    }

    try {
        await saveEnvironmentVariable('app', key, {
            source_key: sourceKey || null,
            value,
        });
        showToast({
            title: t('settings.env.saved'),
            message: `${key} saved in app scope.`,
            tone: 'success',
        });
        await loadEnvironmentVariablesPanel();
    } catch (error) {
        showToast({
            title: t('settings.env.save_failed'),
            message: `Failed to save environment variable: ${error.message}`,
            tone: 'danger',
        });
    }
}

async function handleDeleteEnvironmentVariable(recordRef) {
    const [scope, key] = parseRecordRef(recordRef);
    if (!scope || !key) {
        return;
    }

    const confirmed = await showConfirmDialog({
        title: t('settings.env.delete_title'),
        message: `Delete ${key} from ${scope} scope?`,
        tone: 'warning',
        confirmLabel: t('settings.env.delete'),
    });
    if (!confirmed) {
        return;
    }

    try {
        await deleteEnvironmentVariable(scope, key);
        showToast({
            title: t('settings.env.deleted'),
            message: `${key} removed from ${scope} scope.`,
            tone: 'success',
        });
        await loadEnvironmentVariablesPanel();
    } catch (error) {
        showToast({
            title: t('settings.env.delete_failed'),
            message: `Failed to delete environment variable: ${error.message}`,
            tone: 'danger',
        });
    }
}

function renderEnvironmentFailure(message) {
    const groupsEl = document.getElementById('environment-variables-groups');
    if (!groupsEl) {
        return;
    }
    groupsEl.innerHTML = `
        <div class="settings-empty-state settings-empty-state-compact">
            <h4>${escapeHtml(t('settings.env.load_failed_title'))}</h4>
            <p>${escapeHtml(message)}</p>
        </div>
    `;
    renderEnvironmentActionMode(false);
}

function renderEnvironmentActionMode(isEditing) {
    const addBtn = document.getElementById('add-env-btn');
    const saveBtn = document.getElementById('save-env-btn');
    const cancelBtn = document.getElementById('cancel-env-btn');
    if (addBtn) {
        addBtn.style.display = isEditing ? 'none' : 'inline-flex';
    }
    if (saveBtn) {
        saveBtn.style.display = isEditing ? 'inline-flex' : 'none';
    }
    if (cancelBtn) {
        cancelBtn.style.display = isEditing ? 'inline-flex' : 'none';
    }
}

function autosizeEnvironmentValueInput() {
    const valueInput = document.getElementById('env-value-input');
    if (!(valueInput instanceof HTMLTextAreaElement)) {
        return;
    }
    valueInput.style.height = 'auto';
    valueInput.style.height = `${Math.max(valueInput.scrollHeight, 88)}px`;
}

function focusEnvironmentEditorIfNeeded() {
    if (!environmentState.editor.visible) {
        return;
    }
    autosizeEnvironmentValueInput();
    const valueInput = document.getElementById('env-value-input');
    if (valueInput instanceof HTMLTextAreaElement) {
        valueInput.addEventListener('input', autosizeEnvironmentValueInput);
    }
    const keyInput = document.getElementById('env-key-input');
    if (!keyInput || typeof keyInput.focus !== 'function') {
        return;
    }
    if (typeof keyInput.scrollIntoView === 'function') {
        keyInput.scrollIntoView({
            block: 'nearest',
            inline: 'nearest',
        });
    }
    try {
        keyInput.focus({ preventScroll: true });
    } catch {
        keyInput.focus();
    }
}

function findEnvironmentRecord(scope, key) {
    const records = Array.isArray(environmentState.variablesByScope[scope])
        ? environmentState.variablesByScope[scope]
        : [];
    return records.find(record => record.key === key) || null;
}

function parseRecordRef(recordRef) {
    const separatorIndex = recordRef.indexOf('::');
    if (separatorIndex < 0) {
        return ['', ''];
    }
    return [recordRef.slice(0, separatorIndex), recordRef.slice(separatorIndex + 2)];
}

function readInputValue(id, trim = true) {
    const input = document.getElementById(id);
    if (!input) {
        return '';
    }
    return trim ? String(input.value || '').trim() : String(input.value || '');
}

function escapeHtml(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
