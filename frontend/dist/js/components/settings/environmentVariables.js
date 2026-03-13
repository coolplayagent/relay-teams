/**
 * components/settings/environmentVariables.js
 * Windows environment variable settings panel bindings.
 */
import {
    deleteEnvironmentVariable,
    fetchEnvironmentVariables,
    saveEnvironmentVariable,
} from '../../core/api.js';
import { showConfirmDialog, showToast } from '../../utils/feedback.js';
import { errorToPayload, logError } from '../../utils/logger.js';

const DEFAULT_EXPANDED_SCOPES = {
    system: true,
    app: true,
};

let environmentState = {
    variablesByScope: {
        system: [],
        app: [],
    },
    expandedScopes: { ...DEFAULT_EXPANDED_SCOPES },
    editor: {
        visible: false,
        scope: 'app',
        sourceKey: '',
    },
};

export function bindEnvironmentVariableSettingsHandlers() {
    bindActionButton('add-env-btn', handleAddEnvironmentVariable);
    bindActionButton('save-env-btn', handleSaveEnvironmentVariable);
    bindActionButton('cancel-env-btn', handleCancelEnvironmentVariable);
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
                scope: 'app',
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
        renderEnvironmentFailure(error?.message || 'Unable to load environment variables.');
        showToast({
            title: 'Load Failed',
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
        system: sortRecords(Array.isArray(payload?.system) ? payload.system : [], 'system'),
        app: sortRecords(Array.isArray(payload?.app) ? payload.app : [], 'app'),
    };
}

function sortRecords(records, fallbackScope) {
    return records
        .map(record => ({
            key: String(record?.key || '').trim(),
            value: String(record?.value || ''),
            scope: String(record?.scope || fallbackScope).trim() || fallbackScope,
            value_kind: String(record?.value_kind || 'string').trim() || 'string',
        }))
        .filter(record => record.key)
        .sort((left, right) => left.key.localeCompare(right.key, undefined, { sensitivity: 'base' }));
}

function renderEnvironmentVariablesPanel() {
    renderEnvironmentHelp();
    renderEnvironmentEditor();
    renderEnvironmentGroups();
    renderEnvironmentActionMode(environmentState.editor.visible);
}

function renderEnvironmentHelp() {
    const helpEl = document.getElementById('env-variables-help');
    if (!helpEl) {
        return;
    }
    helpEl.textContent = 'System variables are read-only OS values. App variables are saved to ~/.config/agent-teams/.env and used by Agent Teams runtime processes.';
}

function renderEnvironmentEditor() {
    const shell = document.getElementById('env-editor-shell');
    if (!shell) {
        return;
    }

    if (!environmentState.editor.visible) {
        shell.style.display = 'none';
        setInputValue('env-scope-select', 'app');
        setInputValue('env-key-input', '');
        setInputValue('env-value-input', '');
        setInputValue('env-source-key-input', '');
        setTextContent('env-editor-title', 'Add Environment Variable');
        setTextContent('env-editor-meta', 'Save a key and value into the Agent Teams app environment.');
        return;
    }

    shell.style.display = 'block';
    setInputValue('env-scope-select', environmentState.editor.scope);
    setInputValue('env-key-input', environmentState.editor.key || '');
    setInputValue('env-value-input', environmentState.editor.value || '');
    setInputValue('env-source-key-input', environmentState.editor.sourceKey || '');
    setTextContent(
        'env-editor-title',
        environmentState.editor.sourceKey ? 'Edit Environment Variable' : 'Add Environment Variable',
    );
    setTextContent(
        'env-editor-meta',
        environmentState.editor.sourceKey
            ? 'Update the name, scope, or value before saving.'
            : 'Save a key and value into the Agent Teams app environment.',
    );
}

function renderEnvironmentGroups() {
    const groupsEl = document.getElementById('environment-variables-groups');
    if (!groupsEl) {
        return;
    }

    const totalCount = environmentState.variablesByScope.system.length + environmentState.variablesByScope.app.length;
    if (totalCount === 0) {
        groupsEl.innerHTML = `
            <div class="settings-empty-state settings-empty-state-compact">
                <h4>No environment variables managed here</h4>
                <p>Add an app-level variable to start managing Agent Teams runtime values.</p>
            </div>
        `;
        bindEnvironmentListHandlers();
        return;
    }

    groupsEl.innerHTML = ['system', 'app']
        .map(scope => renderEnvironmentScope(scope, environmentState.variablesByScope[scope]))
        .join('');
    bindEnvironmentListHandlers();
}

function renderEnvironmentScope(scope, records) {
    const expanded = environmentState.expandedScopes[scope] !== false;
    const scopeLabel = scope === 'system' ? 'System Variables' : 'App Variables';
    const scopeCopy = scope === 'system'
        ? 'Effective OS-visible environment variables. Read-only here.'
        : 'Saved to ~/.config/agent-teams/.env.';
    return `
        <section class="env-scope-card" data-env-scope="${escapeHtml(scope)}">
            <button class="env-scope-toggle" data-env-toggle-scope="${escapeHtml(scope)}" type="button" aria-expanded="${expanded ? 'true' : 'false'}">
                <span class="env-scope-toggle-title-wrap">
                    <span class="env-scope-toggle-title">${escapeHtml(scopeLabel)}</span>
                    <span class="env-scope-toggle-meta">${records.length} item${records.length === 1 ? '' : 's'} · ${escapeHtml(scopeCopy)}</span>
                </span>
                <span class="env-scope-toggle-icon">${expanded ? '−' : '+'}</span>
            </button>
            <div class="env-scope-body" style="display:${expanded ? 'block' : 'none'};">
                ${records.length === 0 ? `
                    <div class="env-scope-empty">No variables in this scope.</div>
                ` : `
                    <div class="env-records">
                        ${records.map(record => renderEnvironmentRecord(record)).join('')}
                    </div>
                `}
            </div>
        </section>
    `;
}

function renderEnvironmentRecord(record) {
    const valuePreview = record.value.length > 120
        ? `${record.value.slice(0, 117)}...`
        : record.value;
    const kindLabel = record.value_kind === 'expandable' ? 'Expandable' : 'String';
    const isEditable = record.scope === 'app';
    return `
        <div class="env-record" data-env-key="${escapeHtml(record.key)}" data-env-scope="${escapeHtml(record.scope)}">
            <div class="env-record-main">
                <div class="env-record-title-row">
                    <div class="env-record-key">${escapeHtml(record.key)}</div>
                    <span class="env-record-kind">${escapeHtml(kindLabel)}</span>
                </div>
                <div class="env-record-value" title="${escapeHtml(record.value)}">${escapeHtml(valuePreview)}</div>
            </div>
            ${isEditable ? `
                <div class="env-record-actions">
                    <button class="settings-inline-action env-edit-btn" data-env-edit="${escapeHtml(record.scope)}::${escapeHtml(record.key)}" type="button">Edit</button>
                    <button class="settings-inline-action env-delete-btn" data-env-delete="${escapeHtml(record.scope)}::${escapeHtml(record.key)}" type="button">Delete</button>
                </div>
            ` : ''}
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
            const scope = String(button.dataset.envToggleScope || '').trim();
            if (!scope) {
                return;
            }
            environmentState.expandedScopes[scope] = !(environmentState.expandedScopes[scope] !== false);
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
    if (!scope || !key) {
        return;
    }
    const record = findEnvironmentRecord(scope, key);
    if (!record) {
        return;
    }
    environmentState.editor = {
        visible: true,
        scope,
        key: record.key,
        value: record.value,
        sourceKey: record.key,
    };
    renderEnvironmentVariablesPanel();
}

function handleAddEnvironmentVariable() {
    environmentState.editor = {
        visible: true,
        scope: 'app',
        key: '',
        value: '',
        sourceKey: '',
    };
    renderEnvironmentVariablesPanel();
}

function handleCancelEnvironmentVariable() {
    environmentState.editor = {
        visible: false,
        scope: 'app',
        sourceKey: '',
    };
    renderEnvironmentVariablesPanel();
}

async function handleSaveEnvironmentVariable() {
    const scope = readInputValue('env-scope-select') || 'app';
    const key = readInputValue('env-key-input');
    const value = readInputValue('env-value-input', false);
    const sourceKey = readInputValue('env-source-key-input');

    if (!key) {
        showToast({
            title: 'Missing Key',
            message: 'Enter an environment variable key before saving.',
            tone: 'danger',
        });
        return;
    }

    try {
        await saveEnvironmentVariable(scope, key, {
            source_key: sourceKey || null,
            value,
        });
        showToast({
            title: 'Environment Variable Saved',
            message: `${key} saved in ${scope} scope.`,
            tone: 'success',
        });
        await loadEnvironmentVariablesPanel();
    } catch (error) {
        showToast({
            title: 'Save Failed',
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
        title: 'Delete Environment Variable',
        message: `Delete ${key} from ${scope} scope?`,
        tone: 'warning',
        confirmLabel: 'Delete',
    });
    if (!confirmed) {
        return;
    }

    try {
        await deleteEnvironmentVariable(scope, key);
        showToast({
            title: 'Environment Variable Deleted',
            message: `${key} removed from ${scope} scope.`,
            tone: 'success',
        });
        await loadEnvironmentVariablesPanel();
    } catch (error) {
        showToast({
            title: 'Delete Failed',
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
            <h4>Failed to load environment variables</h4>
            <p>${escapeHtml(message)}</p>
        </div>
    `;
    const shell = document.getElementById('env-editor-shell');
    if (shell) {
        shell.style.display = 'none';
    }
    renderEnvironmentActionMode(false);
}

function renderEnvironmentActionMode(isEditing) {
    const addBtn = document.getElementById('add-env-btn');
    const saveBtn = document.getElementById('save-env-btn');
    const cancelBtn = document.getElementById('cancel-env-btn');
    if (addBtn) {
        addBtn.style.display = 'inline-flex';
    }
    if (saveBtn) {
        saveBtn.style.display = isEditing ? 'inline-flex' : 'none';
    }
    if (cancelBtn) {
        cancelBtn.style.display = isEditing ? 'inline-flex' : 'none';
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
    return [
        recordRef.slice(0, separatorIndex),
        recordRef.slice(separatorIndex + 2),
    ];
}

function setInputValue(id, value) {
    const input = document.getElementById(id);
    if (input) {
        input.value = value || '';
    }
}

function readInputValue(id, trim = true) {
    const input = document.getElementById(id);
    if (!input) {
        return '';
    }
    return trim ? String(input.value || '').trim() : String(input.value || '');
}

function setTextContent(id, value) {
    const element = document.getElementById(id);
    if (element) {
        element.textContent = value || '';
    }
}

function escapeHtml(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
