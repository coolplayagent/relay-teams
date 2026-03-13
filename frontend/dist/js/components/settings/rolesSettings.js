/**
 * components/settings/rolesSettings.js
 * Role settings panel bindings.
 */
import {
    fetchModelProfiles,
    fetchRoleConfig,
    fetchRoleConfigOptions,
    fetchRoleConfigs,
    saveRoleConfig,
    validateRoleConfig,
} from '../../core/api.js';
import { parseMarkdown } from '../../utils/markdown.js';
import { showToast } from '../../utils/feedback.js';
import { errorToPayload, logError } from '../../utils/logger.js';

let roleSummaries = [];
let roleConfigOptions = {
    tools: [],
    mcp_servers: [],
    skills: [],
};
let availableModelProfiles = [];
let selectedRoleId = '';
let selectedSourceRoleId = '';
let promptPreviewMode = 'edit';
let currentMemoryProfile = { enabled: true, daily_enabled: true };
let currentSelections = {
    tools: [],
    mcp_servers: [],
    skills: [],
};

export function bindRoleSettingsHandlers() {
    bindActionButton('add-role-btn', handleAddRole);
    bindActionButton('save-role-btn', handleSaveRole);
    bindActionButton('validate-role-btn', handleValidateRole);
    bindActionButton('cancel-role-btn', handleCancelRoleEdit);
    bindActionButton('role-prompt-edit-tab', () => setPromptPreviewMode('edit'));
    bindActionButton('role-prompt-preview-tab', () => setPromptPreviewMode('preview'));

    const promptInput = document.getElementById('role-system-prompt-input');
    if (promptInput) {
        promptInput.oninput = () => {
            if (promptPreviewMode === 'preview') {
                renderPromptPreview();
            }
        };
    }
}

export async function loadRoleSettingsPanel(preferredRoleId = '') {
    try {
        const [summaries, options, modelProfiles] = await Promise.all([
            fetchRoleConfigs(),
            fetchRoleConfigOptions(),
            fetchModelProfiles(),
        ]);
        roleSummaries = Array.isArray(summaries) ? summaries : [];
        roleConfigOptions = normalizeRoleConfigOptions(options);
        availableModelProfiles = normalizeModelProfileNames(modelProfiles);
        renderRolesList();
        if (roleSummaries.length === 0) {
            showRolesList();
            renderEmptyRolesList();
            return;
        }
        if (preferredRoleId && roleSummaries.some(role => role.role_id === preferredRoleId)) {
            await loadRoleDocument(preferredRoleId);
            return;
        }
        showRolesList();
    } catch (error) {
        logError(
            'frontend.roles_settings.load_failed',
            'Failed to load role settings',
            errorToPayload(error),
        );
        showRolesList();
        renderEmptyRolesList('Failed to load roles', error.message || 'Unable to load role settings.');
    }
}

function bindActionButton(id, handler) {
    const button = document.getElementById(id);
    if (button) {
        button.onclick = handler;
    }
}

function normalizeRoleConfigOptions(options) {
    return {
        tools: Array.isArray(options?.tools) ? options.tools : [],
        mcp_servers: Array.isArray(options?.mcp_servers) ? options.mcp_servers : [],
        skills: Array.isArray(options?.skills) ? options.skills : [],
    };
}

function normalizeModelProfileNames(modelProfiles) {
    if (!modelProfiles || typeof modelProfiles !== 'object') {
        return [];
    }
    return Object.keys(modelProfiles)
        .map(name => String(name).trim())
        .filter(Boolean)
        .sort((left, right) => {
            if (left === right) return 0;
            if (left === 'default') return -1;
            if (right === 'default') return 1;
            return left.localeCompare(right);
        });
}

function renderRolesList() {
    const listEl = document.getElementById('roles-list');
    if (!listEl) return;

    if (!Array.isArray(roleSummaries) || roleSummaries.length === 0) {
        renderEmptyRolesList();
        return;
    }

    listEl.innerHTML = `
        <div class="role-records">
            ${roleSummaries
        .map(role => `
            <div class="role-record${role.role_id === selectedRoleId ? ' active' : ''}" data-role-id="${escapeHtml(role.role_id)}">
                <div class="role-record-main">
                    <div class="role-record-title-row">
                        <div class="role-record-title">${escapeHtml(role.name)}</div>
                        <div class="role-record-id">${escapeHtml(role.role_id)}</div>
                    </div>
                    <div class="role-record-meta">
                        <span>v${escapeHtml(role.version)}</span>
                        <span>${escapeHtml(role.model_profile)}</span>
                    </div>
                </div>
                <div class="role-record-actions">
                    <button class="settings-inline-action settings-list-action role-record-edit-btn" data-role-id="${escapeHtml(role.role_id)}" type="button">Edit</button>
                </div>
            </div>
        `)
        .join('')}
        </div>
    `;

    listEl.querySelectorAll('.role-record').forEach(button => {
        button.onclick = () => {
            const nextRoleId = String(button.dataset.roleId || '').trim();
            if (!nextRoleId) return;
            void loadRoleDocument(nextRoleId);
        };
    });
    listEl.querySelectorAll('.role-record-edit-btn').forEach(button => {
        button.onclick = event => {
            event.stopPropagation();
            const nextRoleId = String(button.dataset.roleId || '').trim();
            if (!nextRoleId) return;
            void loadRoleDocument(nextRoleId);
        };
    });
}

async function loadRoleDocument(roleId) {
    selectedRoleId = roleId;
    renderRolesList();
    const record = await fetchRoleConfig(roleId);
    selectedRoleId = record.role_id;
    selectedSourceRoleId = record.source_role_id || record.role_id;
    applyRoleRecord(record);
    renderRolesList();
    showRoleEditor();
}

function applyRoleRecord(record) {
    const formEl = document.getElementById('role-editor-form');
    const emptyEl = document.getElementById('roles-editor-empty');
    const editorPanel = document.getElementById('role-editor-panel');
    if (editorPanel) editorPanel.style.display = 'block';
    if (formEl) formEl.style.display = 'block';
    if (emptyEl) emptyEl.style.display = 'none';

    currentMemoryProfile = normalizeMemoryProfile(record.memory_profile);
    currentSelections = {
        tools: Array.isArray(record.tools) ? [...record.tools] : [],
        mcp_servers: Array.isArray(record.mcp_servers) ? [...record.mcp_servers] : [],
        skills: Array.isArray(record.skills) ? [...record.skills] : [],
    };

    setInputValue('role-id-input', record.role_id || '');
    setInputValue('role-name-input', record.name || '');
    setInputValue('role-version-input', record.version || '');
    renderModelProfileSelect(record.model_profile || 'default');
    renderOptionPicker('role-tools-picker', roleConfigOptions.tools, currentSelections.tools, 'No tools loaded.');
    renderOptionPicker('role-mcp-picker', roleConfigOptions.mcp_servers, currentSelections.mcp_servers, 'No MCP servers loaded.');
    renderOptionPicker('role-skills-picker', roleConfigOptions.skills, currentSelections.skills, 'No skills loaded.');
    renderMemoryProfileSelects(currentMemoryProfile);
    setInputValue('role-system-prompt-input', record.system_prompt || '');
    setPromptPreviewMode('edit');

    const fileMetaEl = document.getElementById('role-file-meta');
    if (fileMetaEl) {
        fileMetaEl.textContent = record.file_name ? `File: ${record.file_name}` : 'New role';
    }
    renderRoleStatus('', '');
}

function renderOptionPicker(containerId, availableValues, selectedValues, emptyMessage) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const selectedSet = new Set(Array.isArray(selectedValues) ? selectedValues : []);
    const availableList = Array.isArray(availableValues) ? availableValues : [];
    const invalidValues = Array.from(selectedSet).filter(value => !availableList.includes(value));

    if (availableList.length === 0 && invalidValues.length === 0) {
        container.innerHTML = `<div class="role-option-empty">${escapeHtml(emptyMessage)}</div>`;
        return;
    }

    container.innerHTML = [
        ...availableList.map(value => `
            <label class="role-option-item">
                <input type="checkbox" data-option-value="${escapeHtml(value)}"${selectedSet.has(value) ? ' checked' : ''}>
                <span class="role-option-check" aria-hidden="true"></span>
                <span class="role-option-label">${escapeHtml(value)}</span>
            </label>
        `),
        ...invalidValues.map(value => `
            <label class="role-option-item role-option-item-invalid">
                <input type="checkbox" data-option-value="${escapeHtml(value)}" checked>
                <span class="role-option-check" aria-hidden="true"></span>
                <span class="role-option-label">${escapeHtml(value)} <em>Unavailable</em></span>
            </label>
        `),
    ].join('');

    container.querySelectorAll('input[type="checkbox"]').forEach(input => {
        input.onchange = () => syncOptionSelection(containerId);
    });
}

function syncOptionSelection(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    const nextValues = [];
    container.querySelectorAll('input[type="checkbox"]').forEach(input => {
        if (input.checked) {
            nextValues.push(String(input.dataset.optionValue || '').trim());
        }
    });
    if (containerId === 'role-tools-picker') {
        currentSelections.tools = nextValues;
    } else if (containerId === 'role-mcp-picker') {
        currentSelections.mcp_servers = nextValues;
    } else if (containerId === 'role-skills-picker') {
        currentSelections.skills = nextValues;
    }
}

function renderMemoryProfileSelects(memoryProfile) {
    renderBooleanSelect(
        'role-memory-enabled-input',
        memoryProfile.enabled !== false,
    );
    renderBooleanSelect(
        'role-memory-daily-enabled-input',
        memoryProfile.daily_enabled !== false,
    );
}

function renderBooleanSelect(id, selected) {
    const selectEl = document.getElementById(id);
    if (!selectEl) return;
    selectEl.innerHTML = [
        `<option value="true"${selected ? ' selected' : ''}>Enabled</option>`,
        `<option value="false"${selected ? '' : ' selected'}>Disabled</option>`,
    ].join('');
}

function renderModelProfileSelect(selectedProfile) {
    const selectEl = document.getElementById('role-model-profile-input');
    if (!selectEl) return;

    const selectedValue = String(selectedProfile || '').trim() || 'default';
    const availableProfiles = Array.isArray(availableModelProfiles)
        ? [...availableModelProfiles]
        : [];
    const hasSelectedValue = availableProfiles.includes(selectedValue);
    const optionValues = hasSelectedValue ? availableProfiles : [...availableProfiles, selectedValue];

    if (optionValues.length === 0) {
        selectEl.innerHTML = '';
        selectEl.value = '';
        return;
    }

    selectEl.innerHTML = optionValues
        .map(profileName => {
            const suffix = availableProfiles.includes(profileName) ? '' : ' (Unavailable)';
            const selected = profileName === selectedValue ? ' selected' : '';
            return `<option value="${escapeHtml(profileName)}"${selected}>${escapeHtml(profileName + suffix)}</option>`;
        })
        .join('');
}

function renderEmptyRolesList(
    title = 'No roles found',
    description = 'Add a role to edit its metadata and prompt.',
) {
    const listEl = document.getElementById('roles-list');
    const editorPanel = document.getElementById('role-editor-panel');
    if (editorPanel) {
        editorPanel.style.display = 'none';
    }
    if (listEl) {
        listEl.innerHTML = `
            <div class="settings-empty-state">
            <h4>${escapeHtml(title)}</h4>
            <p>${escapeHtml(description)}</p>
            </div>
        `;
        listEl.style.display = 'block';
    }
    renderRoleStatus('', '');
}

function handleAddRole() {
    selectedRoleId = '';
    selectedSourceRoleId = '';
    renderRolesList();
    applyRoleRecord({
        source_role_id: null,
        role_id: '',
        name: '',
        version: '1.0.0',
        tools: [],
        mcp_servers: [],
        skills: [],
        model_profile: 'default',
        memory_profile: { enabled: true, daily_enabled: true },
        system_prompt: '',
        file_name: '',
    });
    showRoleEditor();
    const roleIdInput = document.getElementById('role-id-input');
    if (roleIdInput?.focus) {
        roleIdInput.focus();
    }
}

async function handleValidateRole() {
    try {
        const draft = buildDraftFromForm();
        const result = await validateRoleConfig(draft);
        renderRoleStatus('Validated successfully.', 'success');
        showToast({
            title: 'Role Validated',
            message: `${result.role.role_id} passed validation.`,
            tone: 'success',
        });
    } catch (error) {
        renderRoleStatus(error.message || 'Validation failed.', 'danger');
        showToast({
            title: 'Validation Failed',
            message: error.message || 'Failed to validate role config.',
            tone: 'danger',
        });
    }
}

async function handleSaveRole() {
    try {
        const draft = buildDraftFromForm();
        const saved = await saveRoleConfig(draft.role_id, draft);
        selectedRoleId = saved.role_id;
        selectedSourceRoleId = saved.role_id;
        showToast({
            title: 'Role Saved',
            message: `${saved.role_id} saved and reloaded.`,
            tone: 'success',
        });
        await loadRoleSettingsPanel(saved.role_id);
        renderRoleStatus('Saved and validated.', 'success');
    } catch (error) {
        renderRoleStatus(error.message || 'Save failed.', 'danger');
        showToast({
            title: 'Save Failed',
            message: error.message || 'Failed to save role config.',
            tone: 'danger',
        });
    }
}

function buildDraftFromForm() {
    const roleId = String(getInputValue('role-id-input')).trim();
    if (!roleId) {
        throw new Error('Role ID is required.');
    }

    const systemPrompt = String(getInputValue('role-system-prompt-input')).trim();
    if (!systemPrompt) {
        throw new Error('System prompt is required.');
    }

    const selectedModelProfile = resolveSelectedModelProfile();
    const memoryProfile = {
        ...(currentMemoryProfile || {}),
        enabled: getBooleanSelectValue('role-memory-enabled-input', true),
        daily_enabled: getBooleanSelectValue('role-memory-daily-enabled-input', true),
    };

    return {
        source_role_id: selectedSourceRoleId || null,
        role_id: roleId,
        name: String(getInputValue('role-name-input')).trim(),
        version: String(getInputValue('role-version-input')).trim(),
        model_profile: selectedModelProfile,
        tools: [...currentSelections.tools],
        mcp_servers: [...currentSelections.mcp_servers],
        skills: [...currentSelections.skills],
        memory_profile: memoryProfile,
        system_prompt: systemPrompt,
    };
}

function setPromptPreviewMode(mode) {
    promptPreviewMode = mode === 'preview' ? 'preview' : 'edit';
    const editTab = document.getElementById('role-prompt-edit-tab');
    const previewTab = document.getElementById('role-prompt-preview-tab');
    const textarea = document.getElementById('role-system-prompt-input');
    const preview = document.getElementById('role-system-prompt-preview');
    if (editTab?.classList) {
        editTab.classList.toggle('active', promptPreviewMode === 'edit');
    }
    if (previewTab?.classList) {
        previewTab.classList.toggle('active', promptPreviewMode === 'preview');
    }
    if (textarea) {
        textarea.style.display = promptPreviewMode === 'edit' ? 'block' : 'none';
    }
    if (preview) {
        preview.style.display = promptPreviewMode === 'preview' ? 'block' : 'none';
    }
    if (promptPreviewMode === 'preview') {
        renderPromptPreview();
    }
}

function renderPromptPreview() {
    const preview = document.getElementById('role-system-prompt-preview');
    if (!preview) return;
    preview.innerHTML = parseMarkdown(String(getInputValue('role-system-prompt-input') || ''));
}

function handleCancelRoleEdit() {
    showRolesList();
}

function showRolesList() {
    const listEl = document.getElementById('roles-list');
    const editorPanel = document.getElementById('role-editor-panel');
    if (listEl) listEl.style.display = 'block';
    if (editorPanel) editorPanel.style.display = 'none';
    toggleRoleActions({
        add: true,
        validate: false,
        cancel: false,
        save: false,
    });
}

function showRoleEditor() {
    const listEl = document.getElementById('roles-list');
    const editorPanel = document.getElementById('role-editor-panel');
    if (listEl) listEl.style.display = 'none';
    if (editorPanel) editorPanel.style.display = 'block';
    toggleRoleActions({
        add: false,
        validate: true,
        cancel: true,
        save: true,
    });
}

function renderRoleStatus(message, tone) {
    const statusEl = document.getElementById('role-editor-status');
    if (!statusEl) return;
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

function getInputValue(id) {
    const element = document.getElementById(id);
    return element ? element.value || '' : '';
}

function setInputValue(id, value) {
    const element = document.getElementById(id);
    if (element) {
        element.value = value;
    }
}

function resolveSelectedModelProfile() {
    const selectedValue = String(getInputValue('role-model-profile-input')).trim();
    if (selectedValue) {
        return selectedValue;
    }
    if (availableModelProfiles.includes('default')) {
        return 'default';
    }
    return availableModelProfiles[0] || 'default';
}

function getBooleanSelectValue(id, defaultValue) {
    const rawValue = String(getInputValue(id)).trim().toLowerCase();
    if (rawValue === 'true') return true;
    if (rawValue === 'false') return false;
    return Boolean(defaultValue);
}

function normalizeMemoryProfile(memoryProfile) {
    if (!memoryProfile || typeof memoryProfile !== 'object') {
        return { enabled: true, daily_enabled: true };
    }
    return {
        enabled: memoryProfile.enabled !== false,
        daily_enabled: memoryProfile.daily_enabled !== false,
    };
}

function escapeHtml(value) {
    return String(value)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function toggleRoleActions(visibility) {
    setActionDisplay('add-role-btn', visibility.add);
    setActionDisplay('validate-role-btn', visibility.validate);
    setActionDisplay('cancel-role-btn', visibility.cancel);
    setActionDisplay('save-role-btn', visibility.save);
}

function setActionDisplay(id, visible) {
    const button = document.getElementById(id);
    if (button) {
        button.style.display = visible ? 'inline-flex' : 'none';
    }
}
