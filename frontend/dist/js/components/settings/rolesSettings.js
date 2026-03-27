/**
 * components/settings/rolesSettings.js
 * Role settings panel bindings.
 */
import {
    deleteRoleConfig,
    fetchModelProfiles,
    fetchRoleConfig,
    fetchRoleConfigOptions,
    fetchRoleConfigs,
    saveRoleConfig,
    validateRoleConfig,
} from '../../core/api.js';
import { parseMarkdown } from '../../utils/markdown.js';
import { showConfirmDialog, showToast } from '../../utils/feedback.js';
import { t } from '../../utils/i18n.js';
import { errorToPayload, logError } from '../../utils/logger.js';

let roleSummaries = [];
let roleConfigOptions = {
    tools: [],
    mcp_servers: [],
    skills: [],
    agents: [],
    coordinator_role_id: '',
    main_agent_role_id: '',
};
let availableModelProfiles = [];
let defaultModelProfileName = '';
let selectedRoleId = '';
let selectedSourceRoleId = '';
let promptPreviewMode = 'edit';
let currentMemoryProfile = { enabled: true };
let currentSelections = {
    tools: [],
    mcp_servers: [],
    skills: [],
};
let currentBoundAgentId = '';
let languageBound = false;

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
    if (!languageBound && typeof document.addEventListener === 'function') {
        document.addEventListener('agent-teams-language-changed', () => {
            renderRolesList();
        });
        languageBound = true;
    }
}

export async function loadRoleSettingsPanel(preferredRoleId = '') {
    try {
        const [summaries, options, modelProfiles] = await Promise.all([
            fetchRoleConfigs(),
            fetchRoleConfigOptions(),
            fetchModelProfiles(),
        ]);
        roleSummaries = Array.isArray(summaries) ? summaries.map(normalizeRoleSummary) : [];
        roleConfigOptions = normalizeRoleConfigOptions(options);
        const normalizedModelProfiles = normalizeModelProfiles(modelProfiles);
        availableModelProfiles = normalizedModelProfiles.names;
        defaultModelProfileName = normalizedModelProfiles.defaultName;
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

function normalizeRoleSummary(role) {
    return {
        role_id: String(role?.role_id || '').trim(),
        name: String(role?.name || '').trim(),
        description: String(role?.description || '').trim(),
        version: String(role?.version || '').trim(),
        model_profile: String(role?.model_profile || 'default').trim() || 'default',
        bound_agent_id: role?.bound_agent_id == null ? null : String(role.bound_agent_id).trim(),
        source: String(role?.source || '').trim(),
        deletable: role?.deletable === true,
    };
}

function normalizeRoleConfigOptions(options) {
    return {
        coordinator_role_id: String(options?.coordinator_role_id || '').trim(),
        main_agent_role_id: String(options?.main_agent_role_id || '').trim(),
        tools: normalizeOptionNames(options?.tools),
        mcp_servers: normalizeOptionNames(options?.mcp_servers),
        skills: normalizeSkillOptions(options?.skills),
        agents: Array.isArray(options?.agents) ? options.agents.map(agent => ({
            agent_id: String(agent?.agent_id || '').trim(),
            name: String(agent?.name || '').trim(),
            transport: String(agent?.transport || '').trim(),
        })).filter(agent => agent.agent_id) : [],
    };
}

function normalizeModelProfiles(modelProfiles) {
    if (!modelProfiles || typeof modelProfiles !== 'object') {
        return {
            names: [],
            defaultName: '',
        };
    }
    const entries = Object.entries(modelProfiles)
        .map(([name, profile]) => ({
            name: String(name).trim(),
            isDefault: profile?.is_default === true,
        }))
        .filter(entry => Boolean(entry.name));
    const defaultName = entries.find(entry => entry.isDefault)?.name
        || (entries.some(entry => entry.name === 'default') ? 'default' : entries[0]?.name || '');
    return {
        names: entries
            .map(entry => entry.name)
            .sort((left, right) => {
                if (left === right) return 0;
                if (left === defaultName) return -1;
                if (right === defaultName) return 1;
                return left.localeCompare(right);
            }),
        defaultName,
    };
}

function normalizeOptionNames(values) {
    if (!Array.isArray(values)) {
        return [];
    }
    return values
        .map(normalizeOptionName)
        .filter(value => Boolean(value));
}

function normalizeOptionName(value) {
    if (typeof value === 'string') {
        return value.trim();
    }
    if (typeof value?.name === 'string') {
        return value.name.trim();
    }
    return '';
}

function normalizeSkillOptions(values) {
    if (!Array.isArray(values)) {
        return [];
    }
    return values
        .map(normalizeSkillOption)
        .filter(option => option !== null)
        .sort(compareSkillOptions);
}

function normalizeSkillOption(value) {
    if (typeof value === 'string') {
        const parsed = parseSkillRef(value);
        const ref = value.trim();
        if (!ref) {
            return null;
        }
        return {
            ref,
            name: parsed ? parsed.name : ref,
            description: '',
            scope: parsed ? parsed.scope : '',
        };
    }
    const ref = typeof value?.ref === 'string' ? value.ref.trim() : '';
    const name = typeof value?.name === 'string' ? value.name.trim() : '';
    if (!ref || !name) {
        return null;
    }
    return {
        ref,
        name,
        description: typeof value?.description === 'string' ? value.description.trim() : '',
        scope: typeof value?.scope === 'string' ? value.scope.trim().toLowerCase() : '',
    };
}

function normalizeSkillSelections(values) {
    if (!Array.isArray(values)) {
        return [];
    }
    return values
        .map(value => {
            if (typeof value === 'string') {
                return value.trim();
            }
            if (typeof value?.ref === 'string') {
                return value.ref.trim();
            }
            return '';
        })
        .filter(value => Boolean(value));
}

function compareSkillOptions(left, right) {
    const leftName = String(left?.name || '');
    const rightName = String(right?.name || '');
    if (leftName !== rightName) {
        return leftName.localeCompare(rightName);
    }
    const leftPriority = left?.scope === 'app' ? 0 : 1;
    const rightPriority = right?.scope === 'app' ? 0 : 1;
    if (leftPriority !== rightPriority) {
        return leftPriority - rightPriority;
    }
    return String(left?.ref || '').localeCompare(String(right?.ref || ''));
}

function parseSkillRef(value) {
    const normalized = String(value || '').trim();
    const delimiterIndex = normalized.indexOf(':');
    if (delimiterIndex <= 0 || delimiterIndex >= normalized.length - 1) {
        return null;
    }
    const scope = normalized.slice(0, delimiterIndex).trim().toLowerCase();
    const name = normalized.slice(delimiterIndex + 1).trim();
    if (!name || (scope !== 'app' && scope !== 'builtin')) {
        return null;
    }
    return { scope, name };
}

function formatSkillOptionLabel(option) {
    const name = String(option?.name || '').trim();
    const scope = String(option?.scope || '').trim().toUpperCase();
    const duplicateCount = roleConfigOptions.skills.filter(
        candidate => String(candidate?.name || '').trim() === name,
    ).length;
    if (!scope || duplicateCount <= 1) {
        return name;
    }
    return `${name} · ${scope}`;
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
                        ${renderRoleUsageChips(role.role_id)}
                    </div>
                    <div class="role-record-meta">
                        <span>v${escapeHtml(role.version)}</span>
                        <span>${escapeHtml(role.model_profile)}</span>
                        ${renderRoleBoundAgentMeta(role.bound_agent_id)}
                        ${renderRoleUsageMeta(role.role_id)}
                    </div>
                </div>
                <div class="role-record-actions">
                    <button class="settings-inline-action settings-list-action role-record-edit-btn" data-role-id="${escapeHtml(role.role_id)}" type="button">${escapeHtml(t('settings.roles.edit'))}</button>
                    ${role.deletable === true
            ? `<button class="settings-inline-action settings-list-action role-record-delete-btn" data-role-id="${escapeHtml(role.role_id)}" type="button">${escapeHtml(t('settings.action.delete'))}</button>`
            : ''}
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
    listEl.querySelectorAll('.role-record-delete-btn').forEach(button => {
        button.onclick = async event => {
            event.stopPropagation();
            const nextRoleId = String(button.dataset.roleId || '').trim();
            if (!nextRoleId) return;
            await handleDeleteRole(nextRoleId);
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
    currentBoundAgentId = String(record.bound_agent_id || '').trim();
    currentSelections = {
        tools: normalizeOptionNames(record.tools),
        mcp_servers: normalizeOptionNames(record.mcp_servers),
        skills: normalizeSkillSelections(record.skills),
    };

    setInputValue('role-id-input', record.role_id || '');
    setInputValue('role-name-input', record.name || '');
    setInputValue('role-description-input', record.description || '');
    setInputValue('role-version-input', record.version || '');
    renderModelProfileSelect(record.model_profile || 'default');
    renderBoundAgentSelect(currentBoundAgentId);
    renderRoleOptionPickers();
    renderMemoryProfileSelects(currentMemoryProfile);
    setInputValue('role-system-prompt-input', record.system_prompt || '');
    setPromptPreviewMode('edit');

    const fileMetaEl = document.getElementById('role-file-meta');
    if (fileMetaEl) {
        fileMetaEl.textContent = record.file_name
            ? t('settings.roles.file_label').replace('{file}', record.file_name)
            : t('settings.roles.new_role');
    }
    renderRoleStatus('', '');
    applyReservedRoleUi(record);
}

function applyReservedRoleUi(record) {
    const roleId = String(record?.role_id || '').trim();
    const isReservedRole = isReservedSystemRoleId(roleId);
    setReadonly('role-id-input', isReservedRole);
    setReadonly('role-name-input', isReservedRole);
    setReadonly('role-description-input', isReservedRole);
    setReadonly('role-version-input', isReservedRole);
    setReadonly('role-system-prompt-input', false);

    const promptInput = document.getElementById('role-system-prompt-input');
    if (promptInput) {
        promptInput.title = buildReservedPromptTitle(roleId);
    }
    const statusEl = document.getElementById('role-editor-status');
    if (!statusEl || !isReservedRole) {
        return;
    }
    statusEl.style.display = 'block';
    statusEl.className = 'role-editor-status';
    statusEl.textContent = roleId === roleConfigOptions.main_agent_role_id
        ? t('settings.roles.main_agent_fixed')
        : t('settings.roles.coordinator_fixed');
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

function renderSkillOptionPicker(selectedValues, emptyMessage) {
    const container = document.getElementById('role-skills-picker');
    if (!container) return;

    const selectedSet = new Set(Array.isArray(selectedValues) ? selectedValues : []);
    const availableList = Array.isArray(roleConfigOptions.skills) ? roleConfigOptions.skills : [];
    const availableRefs = new Set(availableList.map(option => option.ref));
    const invalidValues = Array.from(selectedSet).filter(value => !availableRefs.has(value));

    if (availableList.length === 0 && invalidValues.length === 0) {
        container.innerHTML = `<div class="role-option-empty">${escapeHtml(emptyMessage)}</div>`;
        return;
    }

    container.innerHTML = [
        ...availableList.map(option => `
            <label class="role-option-item">
                <input type="checkbox" data-option-value="${escapeHtml(option.ref)}"${selectedSet.has(option.ref) ? ' checked' : ''}>
                <span class="role-option-check" aria-hidden="true"></span>
                <span class="role-option-label">${escapeHtml(formatSkillOptionLabel(option))}</span>
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
        input.onchange = () => syncOptionSelection('role-skills-picker');
    });
}

function renderRoleOptionPickers() {
    renderOptionPicker('role-tools-picker', roleConfigOptions.tools, currentSelections.tools, t('settings.roles.no_tools'));
    renderOptionPicker('role-mcp-picker', roleConfigOptions.mcp_servers, currentSelections.mcp_servers, t('settings.roles.no_mcp'));
    renderSkillOptionPicker(currentSelections.skills, t('settings.roles.no_skills'));
    renderSkillsShellAdvisory();
}

function renderBoundAgentSelect(selectedAgentId) {
    const selectEl = document.getElementById('role-bound-agent-input');
    if (!selectEl) return;

    const safeSelectedId = String(selectedAgentId || '').trim();
    const availableAgents = Array.isArray(roleConfigOptions.agents) ? roleConfigOptions.agents : [];
    const options = [
        {
            value: '',
            label: 'Local runtime',
            unavailable: false,
        },
        ...availableAgents.map(agent => ({
            value: agent.agent_id,
            label: agent.name || agent.agent_id,
            unavailable: false,
        })),
    ];
    if (safeSelectedId && !options.some(option => option.value === safeSelectedId)) {
        options.push({
            value: safeSelectedId,
            label: safeSelectedId,
            unavailable: true,
        });
    }
    selectEl.innerHTML = options.map(option => {
        const selected = option.value === safeSelectedId ? ' selected' : '';
        const suffix = option.unavailable ? ' (Unavailable)' : '';
        return `<option value="${escapeHtml(option.value)}"${selected}>${escapeHtml(option.label + suffix)}</option>`;
    }).join('');
    selectEl.onchange = event => {
        currentBoundAgentId = String(event?.target?.value || '').trim();
    };
}

function renderSkillsShellAdvisory() {
    const container = document.getElementById('role-skills-picker');
    if (!container) return;
    const hasSkills = Array.isArray(currentSelections.skills) && currentSelections.skills.length > 0;
    const hasShell = Array.isArray(currentSelections.tools) && currentSelections.tools.includes('shell');
    if (!hasSkills || hasShell) {
        return;
    }
    container.innerHTML += `
        <div class="role-option-empty role-option-advisory">${escapeHtml(t('settings.roles.skills_shell_advisory'))}</div>
    `;
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
    if (containerId === 'role-tools-picker' || containerId === 'role-skills-picker') {
        renderRoleOptionPickers();
    }
}

function renderMemoryProfileSelects(memoryProfile) {
    renderBooleanSelect(
        'role-memory-enabled-input',
        memoryProfile.enabled !== false,
    );
}

function renderBooleanSelect(id, selected) {
    const selectEl = document.getElementById(id);
    if (!selectEl) return;
    selectEl.innerHTML = [
        `<option value="true"${selected ? ' selected' : ''}>${escapeHtml(t('settings.field.enabled'))}</option>`,
        `<option value="false"${selected ? '' : ' selected'}>${escapeHtml(t('settings.roles.disabled'))}</option>`,
    ].join('');
}

function renderModelProfileSelect(selectedProfile) {
    const selectEl = document.getElementById('role-model-profile-input');
    if (!selectEl) return;

    const selectedValue = String(selectedProfile || '').trim() || 'default';
    const availableProfiles = Array.isArray(availableModelProfiles) ? [...availableModelProfiles] : [];
    const filteredProfiles = availableProfiles.filter(profileName => {
        if (profileName !== 'default') {
            return true;
        }
        return !defaultModelProfileName || defaultModelProfileName === 'default';
    });
    const hasSelectedValue = selectedValue === 'default' || filteredProfiles.includes(selectedValue);
    const optionValues = hasSelectedValue ? filteredProfiles : [...filteredProfiles, selectedValue];

    if (optionValues.length === 0) {
        optionValues.push('default');
    }

    const options = [];
    options.push({
        value: 'default',
        label: formatDefaultProfileOptionLabel(),
        unavailable: false,
    });
    optionValues.forEach(profileName => {
        if (profileName === 'default') {
            return;
        }
        options.push({
            value: profileName,
            label: profileName,
            unavailable: !filteredProfiles.includes(profileName),
        });
    });

    selectEl.innerHTML = options
        .map(option => {
            const suffix = option.unavailable ? ' (Unavailable)' : '';
            const selected = option.value === selectedValue ? ' selected' : '';
            return `<option value="${escapeHtml(option.value)}"${selected}>${escapeHtml(option.label + suffix)}</option>`;
        })
        .join('');
}

function renderEmptyRolesList(
    title = t('settings.roles.none'),
    description = t('settings.roles.none_copy'),
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
        description: '',
        version: '1.0.0',
        tools: [],
        mcp_servers: [],
        skills: [],
        model_profile: 'default',
        bound_agent_id: null,
        memory_profile: { enabled: true },
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
        renderRoleStatus(t('settings.roles.validated_message'), 'success');
        showToast({
            title: t('settings.roles.validated'),
            message: t('settings.roles.validated_toast').replace('{role_id}', result.role.role_id),
            tone: 'success',
        });
    } catch (error) {
        renderRoleStatus(error.message || t('settings.roles.validation_failed_message'), 'danger');
        showToast({
            title: t('settings.roles.validation_failed'),
            message: error.message || t('settings.roles.validation_failed_toast'),
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
            title: t('settings.roles.saved'),
            message: t('settings.roles.saved_toast').replace('{role_id}', saved.role_id),
            tone: 'success',
        });
        await loadRoleSettingsPanel(saved.role_id);
        renderRoleStatus(t('settings.roles.saved_message'), 'success');
    } catch (error) {
        renderRoleStatus(error.message || t('settings.roles.save_failed_message'), 'danger');
        showToast({
            title: t('settings.roles.save_failed'),
            message: error.message || t('settings.roles.save_failed_toast'),
            tone: 'danger',
        });
    }
}

async function handleDeleteRole(roleId) {
    const summary = roleSummaries.find(role => role.role_id === roleId) || null;
    const roleLabel = summary?.name || roleId;
    const confirmed = await showConfirmDialog({
        title: t('settings.roles.delete_confirm_title'),
        message: t('settings.roles.delete_confirm_message').replace('{name}', roleLabel),
        tone: 'warning',
        confirmLabel: t('settings.action.delete'),
        cancelLabel: t('settings.action.cancel'),
    });
    if (!confirmed) {
        return;
    }
    try {
        await deleteRoleConfig(roleId);
        if (selectedRoleId === roleId || selectedSourceRoleId === roleId) {
            selectedRoleId = '';
            selectedSourceRoleId = '';
        }
        showToast({
            title: t('settings.roles.deleted'),
            message: t('settings.roles.deleted_message').replace('{role_id}', roleId),
            tone: 'success',
        });
        await loadRoleSettingsPanel();
    } catch (error) {
        showToast({
            title: t('settings.roles.delete_failed'),
            message: error.message || t('settings.roles.delete_failed_message'),
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
    const description = String(getInputValue('role-description-input')).trim();
    if (!description) {
        throw new Error('Description is required.');
    }

    const selectedModelProfile = resolveSelectedModelProfile();
    const selectedBoundAgentId = String(getInputValue('role-bound-agent-input')).trim();
    currentBoundAgentId = selectedBoundAgentId;
    const memoryProfile = {
        ...(currentMemoryProfile || {}),
        enabled: getBooleanSelectValue('role-memory-enabled-input', true),
    };

    return {
        source_role_id: selectedSourceRoleId || null,
        role_id: roleId,
        name: String(getInputValue('role-name-input')).trim(),
        description,
        version: String(getInputValue('role-version-input')).trim(),
        model_profile: selectedModelProfile,
        bound_agent_id: selectedBoundAgentId || null,
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

function setReadonly(id, readonly) {
    const element = document.getElementById(id);
    if (element) {
        element.readOnly = readonly === true;
    }
}

function resolveSelectedModelProfile() {
    const selectedValue = String(getInputValue('role-model-profile-input')).trim();
    if (selectedValue) {
        return selectedValue;
    }
    return 'default';
}

function getBooleanSelectValue(id, defaultValue) {
    const rawValue = String(getInputValue(id)).trim().toLowerCase();
    if (rawValue === 'true') return true;
    if (rawValue === 'false') return false;
    return Boolean(defaultValue);
}

function normalizeMemoryProfile(memoryProfile) {
    if (!memoryProfile || typeof memoryProfile !== 'object') {
        return { enabled: true };
    }
    return {
        enabled: memoryProfile.enabled !== false,
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

function formatDefaultProfileOptionLabel() {
    if (!defaultModelProfileName || defaultModelProfileName === 'default') {
        return 'default';
    }
    return t('settings.roles.default_current').replace('{profile}', defaultModelProfileName);
}

function isReservedSystemRoleId(roleId) {
    const safeRoleId = String(roleId || '').trim();
    return safeRoleId === String(roleConfigOptions.coordinator_role_id || '').trim()
        || safeRoleId === String(roleConfigOptions.main_agent_role_id || '').trim();
}

function renderRoleUsageChips(roleId) {
    const safeRoleId = String(roleId || '').trim();
    if (safeRoleId === String(roleConfigOptions.main_agent_role_id || '').trim()) {
        return `<div class="profile-card-chips role-record-chips"><span class="profile-card-chip profile-card-chip-accent">${escapeHtml(t('composer.mode_normal'))}</span></div>`;
    }
    if (safeRoleId === String(roleConfigOptions.coordinator_role_id || '').trim()) {
        return `<div class="profile-card-chips role-record-chips"><span class="profile-card-chip">${escapeHtml(t('settings.tab.orchestration'))}</span></div>`;
    }
    return '';
}

function renderRoleUsageMeta(roleId) {
    const safeRoleId = String(roleId || '').trim();
    if (safeRoleId === String(roleConfigOptions.main_agent_role_id || '').trim()) {
        return `<span>${escapeHtml(t('settings.roles.main_agent_only'))}</span>`;
    }
    if (safeRoleId === String(roleConfigOptions.coordinator_role_id || '').trim()) {
        return `<span>${escapeHtml(t('settings.roles.coordinator_root'))}</span>`;
    }
    return '';
}

function renderRoleBoundAgentMeta(boundAgentId) {
    const safeAgentId = String(boundAgentId || '').trim();
    if (!safeAgentId) {
        return '';
    }
    const agent = roleConfigOptions.agents.find(item => item.agent_id === safeAgentId) || null;
    const label = agent?.name || safeAgentId;
    return `<span>ACP: ${escapeHtml(label)}</span>`;
}

function buildReservedPromptTitle(roleId) {
    const safeRoleId = String(roleId || '').trim();
    if (safeRoleId === String(roleConfigOptions.main_agent_role_id || '').trim()) {
        return t('settings.roles.main_agent_title');
    }
    if (safeRoleId === String(roleConfigOptions.coordinator_role_id || '').trim()) {
        return t('settings.roles.coordinator_title');
    }
    return '';
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
