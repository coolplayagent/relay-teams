/**
 * components/settings/orchestrationSettings.js
 * Orchestration settings panel bindings.
 */
import {
    fetchOrchestrationConfig,
    fetchRoleConfigOptions,
    fetchRoleConfigs,
    saveOrchestrationConfig,
} from '../../core/api.js';
import { showConfirmDialog, showToast } from '../../utils/feedback.js';
import { errorToPayload, logError } from '../../utils/logger.js';

let orchestrationConfig = {
    default_orchestration_preset_id: '',
    presets: [],
};
let orchestrationRoleOptions = [];
let editingDraft = null;
let editingSourceId = '';
let handlersBound = false;

export function bindOrchestrationSettingsHandlers() {
    if (handlersBound) {
        return;
    }
    handlersBound = true;

    bindActionButton('add-orchestration-preset-btn', handleAddOrchestration);
    bindActionButton('delete-orchestration-preset-btn', handleDeleteOrchestration);
    bindActionButton('save-orchestration-btn', handleSaveOrchestration);
    bindActionButton('cancel-orchestration-btn', handleCancelOrchestrationEdit);
}

export async function loadOrchestrationSettingsPanel(preferredOrchestrationId = '') {
    try {
        const [config, roleSummaries, roleOptions] = await Promise.all([
            fetchOrchestrationConfig(),
            fetchRoleConfigs(),
            fetchRoleConfigOptions(),
        ]);
        orchestrationConfig = normalizeOrchestrationConfig(config);
        orchestrationRoleOptions = normalizeRoleOptions(roleSummaries, roleOptions);
        editingDraft = null;
        editingSourceId = '';
        renderOrchestrationList();
        renderStatus('', '');
        if (String(preferredOrchestrationId || '').trim()) {
            openOrchestrationEditor(preferredOrchestrationId);
            return;
        }
        showOrchestrationList();
    } catch (error) {
        logError(
            'frontend.orchestration_settings.load_failed',
            'Failed to load orchestration settings',
            errorToPayload(error),
        );
        renderLoadError(error);
    }
}

function bindActionButton(id, handler) {
    const button = document.getElementById(id);
    if (button) {
        button.onclick = handler;
    }
}

function renderOrchestrationList() {
    const host = document.getElementById('orchestration-preset-list');
    if (!host) {
        return;
    }
    const orchestrations = Array.isArray(orchestrationConfig.presets)
        ? orchestrationConfig.presets
        : [];
    if (orchestrations.length === 0) {
        host.innerHTML = `
            <div class="settings-empty-state">
                <h4>No orchestrations</h4>
                <p>Add an orchestration to choose roles and orchestration-specific coordinator instructions.</p>
            </div>
        `;
        return;
    }

    host.innerHTML = `
        <div class="role-records">
            ${orchestrations.map(orchestration => renderOrchestrationRecord(orchestration)).join('')}
        </div>
    `;

    host.querySelectorAll('.orchestration-edit-btn').forEach(button => {
        button.onclick = event => {
            event.stopPropagation();
            openOrchestrationEditor(button.dataset.orchestrationId);
        };
    });
    host.querySelectorAll('.role-record').forEach(button => {
        button.onclick = () => {
            openOrchestrationEditor(button.dataset.orchestrationId);
        };
    });
}

function renderOrchestrationRecord(orchestration) {
    const orchestrationId = String(orchestration?.preset_id || '').trim();
    const orchestrationName = String(
        orchestration?.name || orchestrationId || 'Orchestration',
    ).trim();
    const isDefault = orchestrationId === String(
        orchestrationConfig.default_orchestration_preset_id || '',
    ).trim();
    const roleCount = Array.isArray(orchestration?.role_ids)
        ? orchestration.role_ids.length
        : 0;
    const defaultChip = isDefault
        ? '<span class="profile-card-chip profile-card-chip-accent">Default</span>'
        : '';
    return `
        <div class="role-record" data-orchestration-id="${escapeHtml(orchestrationId)}">
            <div class="role-record-main">
                <div class="role-record-title-row">
                    <div class="role-record-title">${escapeHtml(orchestrationName)}</div>
                    <div class="role-record-id">${escapeHtml(orchestrationId)}</div>
                    <div class="profile-card-chips role-record-chips">${defaultChip}</div>
                </div>
                <div class="role-record-meta">
                    <span>${escapeHtml(roleCount)} role${roleCount === 1 ? '' : 's'}</span>
                    <span>${escapeHtml(String(orchestration?.description || '').trim() || 'No description')}</span>
                </div>
            </div>
            <div class="role-record-actions">
                <button class="settings-inline-action settings-list-action orchestration-edit-btn" data-orchestration-id="${escapeHtml(orchestrationId)}" type="button">Edit</button>
            </div>
        </div>
    `;
}

function openOrchestrationEditor(orchestrationId) {
    const safeId = String(orchestrationId || '').trim();
    if (!safeId) {
        return;
    }
    const source = orchestrationConfig.presets.find(item => item.preset_id === safeId);
    if (!source) {
        return;
    }
    editingSourceId = source.preset_id;
    editingDraft = cloneOrchestration(source);
    renderOrchestrationEditor();
}

function handleAddOrchestration() {
    if (orchestrationRoleOptions.length === 0) {
        showToast({
            title: 'No Roles Available',
            message: 'Create at least one normal role before adding an orchestration.',
            tone: 'warning',
        });
        return;
    }
    editingSourceId = '';
    editingDraft = {
        preset_id: createOrchestrationId(),
        name: 'New Orchestration',
        description: '',
        role_ids: [orchestrationRoleOptions[0].role_id],
        orchestration_prompt: '',
        is_default: orchestrationConfig.presets.length === 0,
    };
    renderStatus('', '');
    renderOrchestrationEditor();
}

function renderOrchestrationEditor() {
    const panel = document.getElementById('orchestration-editor-panel');
    const formEl = document.getElementById('orchestration-editor-form');
    const emptyEl = document.getElementById('orchestration-editor-empty');
    const host = document.getElementById('orchestration-preset-editor');
    const deleteButton = document.getElementById('delete-orchestration-preset-btn');
    const fileMeta = document.getElementById('orchestration-file-meta');
    if (!panel || !formEl || !emptyEl || !host) {
        return;
    }

    if (!editingDraft) {
        host.innerHTML = '';
        if (fileMeta) {
            fileMeta.textContent = 'Orchestration configuration';
        }
        if (deleteButton) {
            deleteButton.disabled = true;
            deleteButton.style.display = 'none';
        }
        showOrchestrationList();
        return;
    }

    if (fileMeta) {
        fileMeta.textContent = editingSourceId
            ? `Orchestration: ${editingSourceId}`
            : 'New orchestration';
    }
    if (deleteButton) {
        deleteButton.disabled = editingSourceId === '';
        deleteButton.style.display = editingSourceId ? 'inline-flex' : 'none';
    }
    panel.style.display = 'block';
    formEl.style.display = 'block';
    emptyEl.style.display = 'none';
    host.innerHTML = `
        <div class="role-editor-sections">
            <section class="role-editor-section">
                <div class="profile-editor-grid role-editor-grid">
                    <div class="form-group">
                        <label for="orchestration-id-input">Orchestration ID</label>
                        <input type="text" id="orchestration-id-input" value="${escapeHtml(editingDraft.preset_id)}" autocomplete="off">
                    </div>
                    <div class="form-group">
                        <label for="orchestration-name-input">Orchestration Name</label>
                        <input type="text" id="orchestration-name-input" value="${escapeHtml(editingDraft.name)}" autocomplete="off">
                    </div>
                    <div class="form-group form-group-span-2">
                        <label for="orchestration-description-input">Description</label>
                        <input type="text" id="orchestration-description-input" value="${escapeHtml(editingDraft.description)}" autocomplete="off">
                    </div>
                </div>
                <div class="profile-default-row orchestration-default-row">
                    <input type="checkbox" id="orchestration-default-input"${editingDraft.is_default ? ' checked' : ''}>
                    <label for="orchestration-default-input">Set as default orchestration</label>
                </div>
            </section>
            <section class="role-editor-section orchestration-role-section">
                <h5>Allowed Roles</h5>
                <div class="role-option-picker role-option-picker-single" id="orchestration-role-picker">
                    ${renderRolePickerOptions(editingDraft.role_ids)}
                </div>
            </section>
            <section class="role-editor-section">
                <div class="role-prompt-header">
                    <h5>Orchestration Prompt</h5>
                </div>
                <textarea id="orchestration-prompt-input" class="config-textarea orchestration-prompt-textarea" placeholder="Explain how Coordinator should split work, choose roles, and drive work to completion.">${escapeHtml(editingDraft.orchestration_prompt)}</textarea>
            </section>
        </div>
    `;
    showOrchestrationEditor();
}

function renderRolePickerOptions(selectedRoleIds) {
    if (orchestrationRoleOptions.length === 0) {
        return '<div class="role-option-empty">No normal roles available.</div>';
    }
    const selectedSet = new Set(
        Array.isArray(selectedRoleIds)
            ? selectedRoleIds.map(roleId => String(roleId || '').trim()).filter(Boolean)
            : [],
    );
    return orchestrationRoleOptions.map(role => `
        <label class="role-option-item">
            <input type="checkbox" data-role-id="${escapeHtml(role.role_id)}"${selectedSet.has(role.role_id) ? ' checked' : ''}>
            <span class="role-option-check" aria-hidden="true"></span>
            <span class="role-option-label">${escapeHtml(role.name)} <em>${escapeHtml(role.role_id)}</em></span>
        </label>
    `).join('');
}

async function handleSaveOrchestration() {
    try {
        const draft = readDraftFromForm();
        const nextConfig = buildSavedConfig(draft);
        await saveOrchestrationConfig(nextConfig);
        showToast({
            title: 'Orchestration Saved',
            message: 'Orchestration settings were saved.',
            tone: 'success',
        });
        document.dispatchEvent(new CustomEvent('orchestration-settings-updated'));
        await loadOrchestrationSettingsPanel();
    } catch (error) {
        renderStatus(error.message || 'Failed to save orchestration settings.', 'danger');
        showToast({
            title: 'Save Failed',
            message: error.message || 'Failed to save orchestration settings.',
            tone: 'danger',
        });
    }
}

async function handleDeleteOrchestration() {
    if (!editingSourceId) {
        return;
    }
    const confirmed = await showConfirmDialog({
        title: 'Delete Orchestration',
        message: `Delete orchestration "${editingSourceId}"?`,
        tone: 'warning',
        confirmLabel: 'Delete',
        cancelLabel: 'Cancel',
    });
    if (!confirmed) {
        return;
    }

    const nextPresets = orchestrationConfig.presets.filter(
        item => item.preset_id !== editingSourceId,
    );
    if (nextPresets.length === 0) {
        showToast({
            title: 'Orchestration Required',
            message: 'At least one orchestration must remain configured.',
            tone: 'warning',
        });
        return;
    }

    const currentDefaultId = String(orchestrationConfig.default_orchestration_preset_id || '').trim();
    const nextDefaultId = currentDefaultId === editingSourceId
        ? nextPresets[0]?.preset_id || ''
        : currentDefaultId;
    try {
        await saveOrchestrationConfig({
            default_orchestration_preset_id: nextDefaultId,
            presets: nextPresets.map(item => serializeOrchestration(item)),
        });
        showToast({
            title: 'Orchestration Deleted',
            message: 'The orchestration was deleted.',
            tone: 'success',
        });
        document.dispatchEvent(new CustomEvent('orchestration-settings-updated'));
        await loadOrchestrationSettingsPanel();
    } catch (error) {
        renderStatus(error.message || 'Failed to delete orchestration.', 'danger');
        showToast({
            title: 'Delete Failed',
            message: error.message || 'Failed to delete orchestration.',
            tone: 'danger',
        });
    }
}

function handleCancelOrchestrationEdit() {
    editingDraft = null;
    editingSourceId = '';
    renderStatus('', '');
    showOrchestrationList();
}

function readDraftFromForm() {
    if (!editingDraft) {
        throw new Error('No orchestration is currently being edited.');
    }
    const orchestrationId = String(
        document.getElementById('orchestration-id-input')?.value || editingDraft.preset_id,
    ).trim();
    const orchestrationName = String(
        document.getElementById('orchestration-name-input')?.value || editingDraft.name,
    ).trim();
    const description = String(
        document.getElementById('orchestration-description-input')?.value || '',
    ).trim();
    const orchestrationPrompt = String(
        document.getElementById('orchestration-prompt-input')?.value || '',
    ).trim();
    const roleIds = [];
    document.getElementById('orchestration-role-picker')
        ?.querySelectorAll('input[type="checkbox"]')
        .forEach(input => {
            if (input.checked) {
                roleIds.push(String(input.dataset.roleId || '').trim());
            }
        });
    const isDefault = document.getElementById('orchestration-default-input')?.checked === true;

    if (!orchestrationId) {
        throw new Error('Orchestration ID is required.');
    }
    if (!orchestrationName) {
        throw new Error('Orchestration name is required.');
    }
    if (roleIds.length === 0) {
        throw new Error('At least one role is required.');
    }
    if (!orchestrationPrompt) {
        throw new Error('Orchestration prompt is required.');
    }

    editingDraft = {
        preset_id: orchestrationId,
        name: orchestrationName,
        description,
        role_ids: roleIds.filter(Boolean),
        orchestration_prompt: orchestrationPrompt,
        is_default: isDefault,
    };
    return { ...editingDraft };
}

function buildSavedConfig(draft) {
    const nextPresets = orchestrationConfig.presets
        .filter(item => item.preset_id !== editingSourceId)
        .map(item => cloneOrchestration(item));
    nextPresets.push(cloneOrchestration(draft));

    const normalizedPresets = nextPresets.map(item => serializeOrchestration(item));
    const defaultOrchestrationId = draft.is_default
        ? draft.preset_id
        : resolveDefaultOrchestrationId({
            presets: nextPresets,
            editingSourceId,
            fallbackId: draft.preset_id,
        });

    if (!defaultOrchestrationId) {
        throw new Error('Default orchestration is required.');
    }
    if (!normalizedPresets.some(item => item.preset_id === defaultOrchestrationId)) {
        throw new Error('Default orchestration must match an existing orchestration.');
    }
    if (hasDuplicateIds(normalizedPresets)) {
        throw new Error('Orchestration IDs must be unique.');
    }
    return {
        default_orchestration_preset_id: defaultOrchestrationId,
        presets: normalizedPresets,
    };
}

function resolveDefaultOrchestrationId({ presets, editingSourceId, fallbackId }) {
    const currentDefaultId = String(orchestrationConfig.default_orchestration_preset_id || '').trim();
    if (currentDefaultId && currentDefaultId !== editingSourceId && presets.some(item => item.preset_id === currentDefaultId)) {
        return currentDefaultId;
    }
    if (currentDefaultId === editingSourceId && editingDraft && presets.some(item => item.preset_id === editingDraft.preset_id)) {
        return editingDraft.preset_id;
    }
    if (fallbackId && presets.some(item => item.preset_id === fallbackId)) {
        return fallbackId;
    }
    return presets[0]?.preset_id || '';
}

function normalizeOrchestrationConfig(config) {
    return {
        default_orchestration_preset_id: String(config?.default_orchestration_preset_id || '').trim(),
        presets: Array.isArray(config?.presets)
            ? config.presets.map(item => cloneOrchestration(item))
            : [],
    };
}

function normalizeRoleOptions(roleSummaries, roleOptions) {
    const coordinatorRoleId = String(roleOptions?.coordinator_role_id || 'Coordinator').trim();
    const mainAgentRoleId = String(roleOptions?.main_agent_role_id || 'MainAgent').trim();
    const rows = Array.isArray(roleSummaries) ? roleSummaries : [];
    return rows
        .map(role => ({
            role_id: String(role?.role_id || '').trim(),
            name: String(role?.name || role?.role_id || '').trim(),
        }))
        .filter(role => role.role_id && role.role_id !== coordinatorRoleId && role.role_id !== mainAgentRoleId)
        .sort((left, right) => left.name.localeCompare(right.name));
}

function cloneOrchestration(source) {
    const orchestrationId = String(source?.preset_id || '').trim();
    return {
        preset_id: orchestrationId,
        name: String(source?.name || orchestrationId || '').trim(),
        description: String(source?.description || '').trim(),
        role_ids: Array.isArray(source?.role_ids)
            ? source.role_ids.map(roleId => String(roleId || '').trim()).filter(Boolean)
            : [],
        orchestration_prompt: String(source?.orchestration_prompt || '').trim(),
        is_default: orchestrationId === String(orchestrationConfig.default_orchestration_preset_id || '').trim()
            || source?.is_default === true,
    };
}

function serializeOrchestration(orchestration) {
    return {
        preset_id: String(orchestration?.preset_id || '').trim(),
        name: String(orchestration?.name || '').trim(),
        description: String(orchestration?.description || '').trim(),
        role_ids: Array.isArray(orchestration?.role_ids)
            ? orchestration.role_ids.map(roleId => String(roleId || '').trim()).filter(Boolean)
            : [],
        orchestration_prompt: String(orchestration?.orchestration_prompt || '').trim(),
    };
}

function hasDuplicateIds(orchestrations) {
    const ids = orchestrations.map(item => String(item?.preset_id || '').trim()).filter(Boolean);
    return ids.length !== new Set(ids).size;
}

function createOrchestrationId() {
    const baseId = 'orchestration';
    const existingIds = new Set(
        orchestrationConfig.presets.map(item => String(item.preset_id || '').trim()),
    );
    let suffix = orchestrationConfig.presets.length + 1;
    let candidate = `${baseId}_${suffix}`;
    while (existingIds.has(candidate)) {
        suffix += 1;
        candidate = `${baseId}_${suffix}`;
    }
    return candidate;
}

function renderStatus(message, tone) {
    const statusEl = document.getElementById('orchestration-editor-status');
    if (!statusEl) {
        return;
    }
    statusEl.className = 'role-editor-status';
    if (!message) {
        statusEl.style.display = 'none';
        statusEl.textContent = '';
        return;
    }
    statusEl.style.display = 'block';
    if (tone) {
        statusEl.classList.add(`role-editor-status-${tone}`);
    }
    statusEl.textContent = message;
}

function renderLoadError(error) {
    const listHost = document.getElementById('orchestration-preset-list');
    const panel = document.getElementById('orchestration-editor-panel');
    const message = error?.message || 'Unable to load orchestration settings.';
    if (listHost) {
        listHost.innerHTML = `
            <div class="settings-empty-state">
                <h4>Load failed</h4>
                <p>${escapeHtml(message)}</p>
            </div>
        `;
    }
    if (panel) {
        panel.style.display = 'none';
    }
    renderStatus('', '');
    toggleOrchestrationActions({
        add: true,
        save: false,
        cancel: false,
    });
}

function showOrchestrationList() {
    const listEl = document.getElementById('orchestration-preset-list');
    const editorPanel = document.getElementById('orchestration-editor-panel');
    if (listEl) {
        listEl.style.display = 'block';
    }
    if (editorPanel) {
        editorPanel.style.display = 'none';
    }
    toggleOrchestrationActions({
        add: true,
        save: false,
        cancel: false,
    });
}

function showOrchestrationEditor() {
    const listEl = document.getElementById('orchestration-preset-list');
    const editorPanel = document.getElementById('orchestration-editor-panel');
    if (listEl) {
        listEl.style.display = 'none';
    }
    if (editorPanel) {
        editorPanel.style.display = 'block';
    }
    toggleOrchestrationActions({
        add: false,
        save: true,
        cancel: true,
    });
}

function toggleOrchestrationActions(visibility) {
    setActionDisplay('add-orchestration-preset-btn', visibility.add);
    setActionDisplay('save-orchestration-btn', visibility.save);
    setActionDisplay('cancel-orchestration-btn', visibility.cancel);
}

function setActionDisplay(id, visible) {
    const button = document.getElementById(id);
    if (button) {
        button.style.display = visible ? 'inline-flex' : 'none';
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
