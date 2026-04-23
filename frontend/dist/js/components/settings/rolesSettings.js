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
    reloadSkillsConfig,
    saveRoleConfig,
    validateRoleConfig,
} from '../../core/api.js';
import { parseMarkdown } from '../../utils/markdown.js';
import { showConfirmDialog, showToast } from '../../utils/feedback.js';
import { t } from '../../utils/i18n.js';
import { errorToPayload, logError } from '../../utils/logger.js';

let roleSummaries = [];
let roleConfigOptions = {
    tool_groups: [],
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
let hasLoadedRoleConfigOptions = false;
let currentSelections = {
    tools: [],
    mcp_servers: [],
    skills: [],
};
let toolGroupExpansionState = {};
let currentBoundAgentId = '';
let currentExecutionSurface = 'api';
let languageBound = false;
let modelProfilesUpdatedBound = false;
let roleActionPromise = null;
let roleActionRequestId = 0;
const DEFAULT_ROLE_TOOL = 'office_read_markdown';
const OTHER_TOOL_GROUP_ID = '__other_tools__';
const UNAVAILABLE_TOOL_GROUP_ID = '__unavailable_tools__';
const CAPABILITY_WILDCARD = '*';

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
    if (!modelProfilesUpdatedBound && typeof document.addEventListener === 'function') {
        document.addEventListener('agent-teams-model-profiles-updated', () => {
            void refreshRoleSettingsDependencies({ applyEditorState: true });
        });
        modelProfilesUpdatedBound = true;
    }
}

export async function loadRoleSettingsPanel(preferredRoleId = '') {
    try {
        const summaries = await fetchRoleConfigs();
        await refreshRoleSettingsDependencies();
        roleSummaries = Array.isArray(summaries) ? summaries.map(normalizeRoleSummary) : [];
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
        execution_surface: String(role?.execution_surface || 'api').trim() || 'api',
        source: String(role?.source || '').trim(),
        deletable: role?.deletable === true,
    };
}

function normalizeRoleConfigOptions(options) {
    return {
        coordinator_role_id: String(options?.coordinator_role_id || '').trim(),
        main_agent_role_id: String(options?.main_agent_role_id || '').trim(),
        tool_groups: normalizeToolGroupOptions(options?.tool_groups),
        tools: normalizeOptionNames(options?.tools),
        mcp_servers: normalizeOptionNames(options?.mcp_servers),
        skills: normalizeSkillOptions(options?.skills),
        execution_surfaces: normalizeOptionNames(
            options?.execution_surfaces || ['api', 'browser', 'desktop', 'hybrid'],
        ),
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

function normalizeCapabilitySelections(values) {
    const normalized = normalizeOptionNames(values);
    return normalized.includes(CAPABILITY_WILDCARD) ? [CAPABILITY_WILDCARD] : normalized;
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

function normalizeToolGroupOptions(values) {
    if (!Array.isArray(values)) {
        return [];
    }
    return values
        .map(normalizeToolGroupOption)
        .filter(option => option !== null);
}

function normalizeToolGroupOption(value) {
    const id = typeof value?.id === 'string' ? value.id.trim() : '';
    const name = typeof value?.name === 'string' ? value.name.trim() : '';
    const tools = normalizeOptionNames(value?.tools);
    if (!id || !name || tools.length === 0) {
        return null;
    }
    return {
        id,
        name: getLocalizedToolGroupText(id, 'name', name),
        description: getLocalizedToolGroupText(
            id,
            'description',
            typeof value?.description === 'string' ? value.description.trim() : '',
        ),
        tools,
    };
}

function getLocalizedToolGroupText(groupId, field, fallback) {
    const key = `settings.roles.tool_group.${String(groupId || '').trim()}.${field}`;
    const translated = t(key);
    if (!translated || translated === key) {
        return String(fallback || '').trim();
    }
    return translated;
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
        const ref = value.trim();
        if (!ref) {
            return null;
        }
        return {
            ref,
            name: ref,
            description: '',
            source: '',
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
        source: typeof value?.source === 'string'
            ? value.source.trim().toLowerCase()
            : (typeof value?.scope === 'string' ? value.scope.trim().toLowerCase() : ''),
    };
}

function normalizeSkillSelections(values) {
    if (!Array.isArray(values)) {
        return [];
    }
    const normalized = values
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
    return normalized.includes(CAPABILITY_WILDCARD) ? [CAPABILITY_WILDCARD] : normalized;
}

function compareSkillOptions(left, right) {
    const leftName = String(left?.name || '');
    const rightName = String(right?.name || '');
    if (leftName !== rightName) {
        return leftName.localeCompare(rightName);
    }
    return String(left?.ref || '').localeCompare(String(right?.ref || ''));
}

function formatSkillOptionLabel(option) {
    const name = String(option?.name || '').trim();
    const source = String(option?.source || '').trim().toUpperCase();
    const duplicateCount = roleConfigOptions.skills.filter(
        candidate => String(candidate?.name || '').trim() === name,
    ).length;
    if (!source || duplicateCount <= 1) {
        return name;
    }
    return `${name} · ${source}`;
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
                        <span>${escapeHtml(role.execution_surface || 'api')}</span>
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
        button.onclick = async () => {
            const nextRoleId = String(button.dataset.roleId || '').trim();
            if (!nextRoleId) return;
            await loadRoleDocument(nextRoleId);
        };
    });
    listEl.querySelectorAll('.role-record-edit-btn').forEach(button => {
        button.onclick = async event => {
            event.stopPropagation();
            const nextRoleId = String(button.dataset.roleId || '').trim();
            if (!nextRoleId) return;
            await loadRoleDocument(nextRoleId);
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
    const [record] = await Promise.all([
        fetchRoleConfig(roleId),
        refreshRoleSettingsDependencies(),
    ]);
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
    currentExecutionSurface = String(record.execution_surface || 'api').trim() || 'api';
    currentSelections = {
        tools: orderToolSelections(normalizeOptionNames(record.tools)),
        mcp_servers: normalizeCapabilitySelections(record.mcp_servers),
        skills: normalizeSkillSelections(record.skills),
    };
    resetToolGroupExpansionState(currentSelections.tools);

    setInputValue('role-id-input', record.role_id || '');
    setInputValue('role-name-input', record.name || '');
    setInputValue('role-description-input', record.description || '');
    setInputValue('role-version-input', record.version || '');
    renderModelProfileSelect(record.model_profile || 'default');
    renderBoundAgentSelect(currentBoundAgentId);
    renderExecutionSurfaceSelect(currentExecutionSurface);
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
    const wildcardSelected = selectedSet.has(CAPABILITY_WILDCARD);
    const availableList = Array.isArray(availableValues)
        ? availableValues.filter(value => value !== CAPABILITY_WILDCARD)
        : [];
    const invalidValues = wildcardSelected
        ? []
        : Array.from(selectedSet).filter(value => value !== CAPABILITY_WILDCARD && !availableList.includes(value));
    const emptyStateHtml = availableList.length === 0 && invalidValues.length === 0
        ? `<div class="role-option-empty">${escapeHtml(emptyMessage)}</div>`
        : '';

    container.innerHTML = [
        renderCapabilityWildcardOption({
            label: t('settings.roles.all_mcp_servers'),
            hint: t('settings.roles.all_mcp_servers_hint'),
            checked: wildcardSelected,
        }),
        emptyStateHtml,
        ...(wildcardSelected ? [] : availableList.map(value => `
            <label class="role-option-item">
                <input type="checkbox" data-option-value="${escapeHtml(value)}"${selectedSet.has(value) ? ' checked' : ''}>
                <span class="role-option-check" aria-hidden="true"></span>
                <span class="role-option-label">${escapeHtml(value)}</span>
            </label>
        `)),
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
    const wildcardSelected = selectedSet.has(CAPABILITY_WILDCARD);
    const availableList = Array.isArray(roleConfigOptions.skills)
        ? roleConfigOptions.skills.filter(option => option.ref !== CAPABILITY_WILDCARD)
        : [];
    const availableRefs = new Set(availableList.map(option => option.ref));
    const invalidValues = wildcardSelected
        ? []
        : Array.from(selectedSet).filter(value => value !== CAPABILITY_WILDCARD && !availableRefs.has(value));
    const emptyStateHtml = availableList.length === 0 && invalidValues.length === 0
        ? `<div class="role-option-empty">${escapeHtml(emptyMessage)}</div>`
        : '';

    container.innerHTML = [
        renderCapabilityWildcardOption({
            label: t('settings.roles.all_skills'),
            hint: t('settings.roles.all_skills_hint'),
            checked: wildcardSelected,
        }),
        emptyStateHtml,
        ...(wildcardSelected ? [] : availableList.map(option => `
            <label class="role-option-item">
                <input type="checkbox" data-option-value="${escapeHtml(option.ref)}"${selectedSet.has(option.ref) ? ' checked' : ''}>
                <span class="role-option-check" aria-hidden="true"></span>
                <span class="role-option-label">${escapeHtml(formatSkillOptionLabel(option))}</span>
            </label>
        `)),
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

function renderCapabilityWildcardOption({ label, hint, checked }) {
    return `
        <label class="role-option-item">
            <input type="checkbox" data-option-value="${CAPABILITY_WILDCARD}" data-option-wildcard="true"${checked ? ' checked' : ''}>
            <span class="role-option-check" aria-hidden="true"></span>
            <span class="role-option-copy">
                <span class="role-option-label">${escapeHtml(label)}</span>
                ${checked ? `<span class="role-option-hint">${escapeHtml(hint)}</span>` : ''}
            </span>
        </label>
    `;
}

function renderToolGroupPicker(selectedTools, emptyMessage) {
    const container = document.getElementById('role-tool-groups-picker');
    if (!container) return;

    const availableGroups = listRenderedToolGroups(selectedTools);
    if (availableGroups.length === 0) {
        container.innerHTML = `<div class="role-option-empty">${escapeHtml(emptyMessage)}</div>`;
        return;
    }
    syncToolGroupExpansionState(availableGroups, selectedTools);

    container.innerHTML = availableGroups.map(group => {
        const selectionState = getToolGroupSelectionState(group, selectedTools);
        const isExpanded = isToolGroupExpanded(group.id);
        return `
            <section class="role-tool-group${group.invalid === true ? ' role-tool-group-invalid' : ''}" data-group-id="${escapeHtml(group.id)}">
                <div class="role-tool-group-header">
                    <label class="role-tool-group-select">
                        <input
                            type="checkbox"
                            data-option-value="${escapeHtml(group.id)}"
                            data-group-id="${escapeHtml(group.id)}"
                            ${selectionState === 'all' ? ' checked' : ''}
                        >
                        <span class="role-option-check" aria-hidden="true"></span>
                        <span class="role-tool-group-copy">
                            <span class="role-tool-group-title-row">
                                <span class="role-tool-group-title">${escapeHtml(group.name)}</span>
                                <span class="role-tool-group-count">${escapeHtml(formatToolCountLabel(group.tools.length))}</span>
                            </span>
                            ${group.description ? `<span class="role-tool-group-description">${escapeHtml(group.description)}</span>` : ''}
                        </span>
                    </label>
                    <button
                        class="role-tool-group-toggle"
                        type="button"
                        data-group-toggle-id="${escapeHtml(group.id)}"
                        aria-label="${escapeHtml(isExpanded ? t('settings.roles.collapse_group') : t('settings.roles.expand_group'))}"
                    >
                        <span class="role-tool-group-toggle-label">${escapeHtml(isExpanded ? t('settings.roles.collapse') : t('settings.roles.expand'))}</span>
                        <span class="role-tool-group-toggle-icon${isExpanded ? ' is-expanded' : ''}" aria-hidden="true"></span>
                    </button>
                </div>
                <div class="role-tool-group-tools${isExpanded ? '' : ' role-tool-group-tools-collapsed'}">
                    ${group.tools.map(toolName => renderToolGroupToolOption({
            toolName,
            isChecked: Array.isArray(selectedTools) && selectedTools.includes(toolName),
            isInvalid: group.invalid === true,
        })).join('')}
                </div>
            </section>
        `;
    }).join('');

    container.querySelectorAll('input[type="checkbox"]').forEach(input => {
        const groupId = String(input.dataset.groupId || '').trim();
        const toolName = String(input.dataset.toolValue || '').trim();
        if (groupId) {
            const group = availableGroups.find(candidate => candidate.id === groupId);
            input.indeterminate = getToolGroupSelectionState(group, selectedTools) === 'partial';
            input.onchange = () => syncToolGroupSelection(groupId, input.checked);
            return;
        }
        if (!toolName) {
            return;
        }
        input.onchange = () => syncIndividualToolSelection(toolName, input.checked);
    });
    container.querySelectorAll('.role-tool-group-toggle').forEach(button => {
        const groupId = String(button.dataset.groupToggleId || '').trim();
        button.onclick = () => toggleToolGroupExpansion(groupId);
    });
}

function renderToolGroupToolOption({ toolName, isChecked, isInvalid }) {
    const unavailableSuffix = isInvalid === true
        ? ` <em>${escapeHtml(t('settings.system.unavailable_state'))}</em>`
        : '';
    return `
        <label class="role-option-item role-tool-option${isInvalid === true ? ' role-option-item-invalid' : ''}">
            <input
                type="checkbox"
                data-option-value="${escapeHtml(toolName)}"
                data-tool-value="${escapeHtml(toolName)}"
                ${isChecked ? ' checked' : ''}
            >
            <span class="role-option-check" aria-hidden="true"></span>
            <span class="role-option-label">${escapeHtml(toolName)}${unavailableSuffix}</span>
        </label>
    `;
}

function listRenderedToolGroups(selectedTools) {
    const configuredGroups = Array.isArray(roleConfigOptions.tool_groups) ? roleConfigOptions.tool_groups : [];
    const availableTools = Array.isArray(roleConfigOptions.tools) ? roleConfigOptions.tools : [];
    const availableToolSet = new Set(availableTools);
    const coveredTools = new Set();
    const groups = configuredGroups
        .map(group => {
            const visibleTools = Array.isArray(group.tools)
                ? group.tools.filter(toolName => availableToolSet.has(toolName))
                : [];
            visibleTools.forEach(toolName => coveredTools.add(toolName));
            if (visibleTools.length === 0) {
                return null;
            }
            return {
                id: group.id,
                name: group.name,
                description: group.description,
                tools: visibleTools,
                invalid: false,
            };
        })
        .filter(group => group !== null);
    const otherTools = availableTools.filter(toolName => !coveredTools.has(toolName));
    if (otherTools.length > 0) {
        groups.push({
            id: OTHER_TOOL_GROUP_ID,
            name: t('settings.roles.other_tools'),
            description: t('settings.roles.other_tools_description'),
            tools: otherTools,
            invalid: false,
        });
    }
    const invalidTools = orderToolSelections(
        Array.isArray(selectedTools)
            ? selectedTools.filter(toolName => !availableToolSet.has(toolName))
            : [],
    );
    if (invalidTools.length > 0) {
        groups.push({
            id: UNAVAILABLE_TOOL_GROUP_ID,
            name: t('settings.roles.unavailable_tools'),
            description: t('settings.roles.unavailable_tools_description'),
            tools: invalidTools,
            invalid: true,
        });
    }
    return groups;
}

function formatToolCountLabel(count) {
    const safeCount = Number.isFinite(count) ? Math.max(0, Math.trunc(count)) : 0;
    if (safeCount === 1) {
        return t('settings.roles.tool_count_one');
    }
    return t('settings.roles.tool_count_many').replace('{count}', String(safeCount));
}

function getToolGroupSelectionState(group, selectedTools) {
    if (!group || !Array.isArray(group.tools) || group.tools.length === 0) {
        return 'none';
    }
    const selectedSet = new Set(Array.isArray(selectedTools) ? selectedTools : []);
    const matchedTools = group.tools.filter(toolName => selectedSet.has(toolName));
    if (matchedTools.length === 0) {
        return 'none';
    }
    if (matchedTools.length === group.tools.length) {
        return 'all';
    }
    return 'partial';
}

function renderRoleOptionPickers() {
    renderToolGroupPicker(currentSelections.tools, t('settings.roles.no_tool_groups'));
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

function renderExecutionSurfaceSelect(selectedSurface) {
    const selectEl = document.getElementById('role-execution-surface-input');
    if (!selectEl) return;

    const safeSelectedSurface = String(selectedSurface || '').trim() || 'api';
    const availableSurfaces = Array.isArray(roleConfigOptions.execution_surfaces)
        ? roleConfigOptions.execution_surfaces
        : ['api', 'browser', 'desktop', 'hybrid'];
    const options = availableSurfaces.includes(safeSelectedSurface)
        ? availableSurfaces
        : [...availableSurfaces, safeSelectedSurface];
    selectEl.innerHTML = options.map(surface => {
        const selected = surface === safeSelectedSurface ? ' selected' : '';
        return `<option value="${escapeHtml(surface)}"${selected}>${escapeHtml(surface)}</option>`;
    }).join('');
    selectEl.onchange = event => {
        currentExecutionSurface = String(event?.target?.value || '').trim() || 'api';
    };
}

function renderSkillsShellAdvisory() {
    const container = document.getElementById('role-skills-picker');
    if (!container) return;
    const advisoryHtml = `
        <div class="role-option-empty role-option-advisory">${escapeHtml(t('settings.roles.skills_shell_advisory'))}</div>
    `;
    const existingAdvisory = typeof container.querySelector === 'function'
        ? container.querySelector('.role-option-advisory')
        : null;
    if (existingAdvisory) {
        if (typeof existingAdvisory.remove === 'function') {
            existingAdvisory.remove();
        } else if (existingAdvisory.parentNode && typeof existingAdvisory.parentNode.removeChild === 'function') {
            existingAdvisory.parentNode.removeChild(existingAdvisory);
        }
    }
    const hasSkills = Array.isArray(currentSelections.skills) && currentSelections.skills.length > 0;
    const hasExecCommand = Array.isArray(currentSelections.tools)
        && (
            currentSelections.tools.includes('shell')
            || currentSelections.tools.includes('shell')
        );
    if (!hasSkills || hasExecCommand) {
        return;
    }
    container.insertAdjacentHTML('beforeend', advisoryHtml);
}

function pickerHasInvalidOptions(container) {
    return typeof container?.innerHTML === 'string'
        && container.innerHTML.includes('role-option-item-invalid');
}

function refreshOptionPicker(containerId) {
    if (containerId === 'role-tool-groups-picker') {
        renderToolGroupPicker(currentSelections.tools, t('settings.roles.no_tool_groups'));
        return;
    }
    if (containerId === 'role-mcp-picker') {
        renderOptionPicker('role-mcp-picker', roleConfigOptions.mcp_servers, currentSelections.mcp_servers, t('settings.roles.no_mcp'));
        return;
    }
    if (containerId === 'role-skills-picker') {
        renderSkillOptionPicker(currentSelections.skills, t('settings.roles.no_skills'));
    }
}

function syncOptionSelection(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    const shouldRefreshPicker = pickerHasInvalidOptions(container);
    const nextValues = [];
    container.querySelectorAll('input[type="checkbox"]').forEach(input => {
        if (input.checked) {
            nextValues.push(String(input.dataset.optionValue || '').trim());
        }
    });
    const normalizedValues = nextValues.includes(CAPABILITY_WILDCARD)
        ? [CAPABILITY_WILDCARD]
        : nextValues;
    if (containerId === 'role-mcp-picker') {
        currentSelections.mcp_servers = normalizedValues;
    } else if (containerId === 'role-skills-picker') {
        currentSelections.skills = normalizedValues;
    }
    if (
        shouldRefreshPicker
        || containerId === 'role-mcp-picker'
        || containerId === 'role-skills-picker'
    ) {
        refreshOptionPicker(containerId);
    }
    if (containerId === 'role-skills-picker') {
        renderSkillsShellAdvisory();
    }
}

function syncToolGroupSelection(groupId, isChecked) {
    const toolGroup = listRenderedToolGroups(currentSelections.tools).find(group => group.id === groupId) || null;
    if (!toolGroup) {
        return;
    }
    const selectedTools = new Set(Array.isArray(currentSelections.tools) ? currentSelections.tools : []);
    toolGroup.tools.forEach(toolName => {
        if (isChecked) {
            selectedTools.add(toolName);
            return;
        }
        selectedTools.delete(toolName);
    });
    currentSelections.tools = orderToolSelections(Array.from(selectedTools));
    refreshOptionPicker('role-tool-groups-picker');
    renderSkillsShellAdvisory();
}

function syncIndividualToolSelection(toolName, isChecked) {
    const selectedTools = new Set(Array.isArray(currentSelections.tools) ? currentSelections.tools : []);
    if (isChecked) {
        selectedTools.add(toolName);
    } else {
        selectedTools.delete(toolName);
    }
    currentSelections.tools = orderToolSelections(Array.from(selectedTools));
    refreshOptionPicker('role-tool-groups-picker');
    renderSkillsShellAdvisory();
}

function resetToolGroupExpansionState(selectedTools) {
    const nextState = {};
    listRenderedToolGroups(selectedTools).forEach(group => {
        nextState[group.id] = false;
    });
    toolGroupExpansionState = nextState;
}

function syncToolGroupExpansionState(groups, selectedTools) {
    const nextState = { ...toolGroupExpansionState };
    const activeGroupIds = new Set();
    groups.forEach(group => {
        activeGroupIds.add(group.id);
        if (typeof nextState[group.id] === 'boolean') {
            return;
        }
        nextState[group.id] = getToolGroupSelectionState(group, selectedTools) !== 'none';
    });
    Object.keys(nextState).forEach(groupId => {
        if (!activeGroupIds.has(groupId)) {
            delete nextState[groupId];
        }
    });
    toolGroupExpansionState = nextState;
}

function isToolGroupExpanded(groupId) {
    return toolGroupExpansionState[groupId] === true;
}

function toggleToolGroupExpansion(groupId) {
    if (!groupId) {
        return;
    }
    toolGroupExpansionState = {
        ...toolGroupExpansionState,
        [groupId]: !isToolGroupExpanded(groupId),
    };
    refreshOptionPicker('role-tool-groups-picker');
}

function orderToolSelections(values) {
    const orderedSelections = [];
    const seen = new Set();
    const inputValues = Array.isArray(values) ? values : [];
    const availableTools = Array.isArray(roleConfigOptions.tools) ? roleConfigOptions.tools : [];

    availableTools.forEach(toolName => {
        if (!inputValues.includes(toolName) || seen.has(toolName)) {
            return;
        }
        seen.add(toolName);
        orderedSelections.push(toolName);
    });
    inputValues.forEach(toolName => {
        if (!toolName || seen.has(toolName)) {
            return;
        }
        seen.add(toolName);
        orderedSelections.push(toolName);
    });
    return orderedSelections;
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
        tools: getDefaultToolsForNewRole(),
        mcp_servers: [],
        skills: [],
        model_profile: 'default',
        bound_agent_id: null,
        execution_surface: 'api',
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

function getDefaultToolsForNewRole() {
    if (!Array.isArray(roleConfigOptions.tools)) {
        return [];
    }
    return roleConfigOptions.tools.includes(DEFAULT_ROLE_TOOL) ? [DEFAULT_ROLE_TOOL] : [];
}

async function handleValidateRole() {
    return runRoleEditorAction(async requestId => {
        try {
            await refreshRoleSettingsDependencies();
            const draft = buildDraftFromForm();
            const result = await performRoleRequestWithBuiltinSkillRecovery(
                () => validateRoleConfig(draft),
            );
            if (!isCurrentRoleActionRequest(requestId)) {
                return;
            }
            renderRoleStatus(t('settings.roles.validated_message'), 'success');
            showToast({
                title: t('settings.roles.validated'),
                message: t('settings.roles.validated_toast').replace('{role_id}', result.role.role_id),
                tone: 'success',
            });
        } catch (error) {
            if (!isCurrentRoleActionRequest(requestId)) {
                return;
            }
            renderRoleStatus(error.message || t('settings.roles.validation_failed_message'), 'danger');
            showToast({
                title: t('settings.roles.validation_failed'),
                message: error.message || t('settings.roles.validation_failed_toast'),
                tone: 'danger',
            });
        }
    });
}

async function handleSaveRole() {
    return runRoleEditorAction(async requestId => {
        try {
            await refreshRoleSettingsDependencies();
            const draft = buildDraftFromForm();
            const saved = await performRoleRequestWithBuiltinSkillRecovery(
                () => saveRoleConfig(draft.role_id, draft),
            );
            if (!isCurrentRoleActionRequest(requestId)) {
                return;
            }
            selectedRoleId = saved.role_id;
            selectedSourceRoleId = saved.role_id;
            await loadRoleSettingsPanel(saved.role_id);
            if (!isCurrentRoleActionRequest(requestId)) {
                return;
            }
            renderRoleStatus(t('settings.roles.saved_message'), 'success');
            showToast({
                title: t('settings.roles.saved'),
                message: t('settings.roles.saved_toast').replace('{role_id}', saved.role_id),
                tone: 'success',
            });
        } catch (error) {
            if (!isCurrentRoleActionRequest(requestId)) {
                return;
            }
            renderRoleStatus(error.message || t('settings.roles.save_failed_message'), 'danger');
            showToast({
                title: t('settings.roles.save_failed'),
                message: error.message || t('settings.roles.save_failed_toast'),
                tone: 'danger',
            });
        }
    });
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
    const selectedExecutionSurface = String(getInputValue('role-execution-surface-input')).trim() || 'api';
    currentExecutionSurface = selectedExecutionSurface;
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
        execution_surface: selectedExecutionSurface,
        tools: [...currentSelections.tools],
        mcp_servers: [...currentSelections.mcp_servers],
        skills: [...currentSelections.skills],
        memory_profile: memoryProfile,
        system_prompt: systemPrompt,
    };
}

async function refreshRoleSettingsDependencies({ applyEditorState = false } = {}) {
    const selectedModelProfile = resolveSelectedModelProfile();
    const selectedBoundAgentId = currentBoundAgentId || String(getInputValue('role-bound-agent-input')).trim();
    const selectedExecutionSurface = String(getInputValue('role-execution-surface-input')).trim()
        || currentExecutionSurface
        || 'api';
    let roleOptionsReady = hasLoadedRoleConfigOptions;
    let usedCachedRoleOptions = false;
    const [optionsResult, modelProfilesResult] = await Promise.allSettled([
        fetchRoleConfigOptions(),
        fetchModelProfiles(),
    ]);
    if (optionsResult.status === 'fulfilled') {
        roleConfigOptions = normalizeRoleConfigOptions(optionsResult.value);
        hasLoadedRoleConfigOptions = true;
        roleOptionsReady = true;
    } else {
        if (!hasLoadedRoleConfigOptions) {
            roleConfigOptions = normalizeRoleConfigOptions(null);
            roleOptionsReady = false;
        } else {
            usedCachedRoleOptions = true;
        }
        logError(
            'frontend.roles_settings.dependencies.role_options_failed',
            'Failed to load role options',
            errorToPayload(optionsResult.reason),
        );
    }
    if (modelProfilesResult.status === 'fulfilled') {
        const normalizedModelProfiles = normalizeModelProfiles(modelProfilesResult.value);
        availableModelProfiles = normalizedModelProfiles.names;
        defaultModelProfileName = normalizedModelProfiles.defaultName;
    } else {
        availableModelProfiles = [];
        defaultModelProfileName = '';
        logError(
            'frontend.roles_settings.dependencies.model_profiles_failed',
            'Failed to load model profiles',
            errorToPayload(modelProfilesResult.reason),
        );
    }
    const dependencyState = {
        roleOptionsReady,
        usedCachedRoleOptions,
        modelProfilesReady: modelProfilesResult.status === 'fulfilled',
    };
    if (!applyEditorState) {
        return dependencyState;
    }
    renderModelProfileSelect(selectedModelProfile);
    renderBoundAgentSelect(selectedBoundAgentId);
    currentExecutionSurface = selectedExecutionSurface;
    renderExecutionSurfaceSelect(selectedExecutionSurface);
    renderRoleOptionPickers();
    return dependencyState;
}

function runRoleEditorAction(action) {
    if (roleActionPromise) {
        return roleActionPromise;
    }
    const requestId = ++roleActionRequestId;
    setRoleEditorActionBusy(true);
    roleActionPromise = (async () => {
        try {
            return await action(requestId);
        } finally {
            if (roleActionRequestId === requestId) {
                roleActionPromise = null;
                setRoleEditorActionBusy(false);
            }
        }
    })();
    return roleActionPromise;
}

function isCurrentRoleActionRequest(requestId) {
    return roleActionRequestId === requestId;
}

function setRoleEditorActionBusy(isBusy) {
    [
        'add-role-btn',
        'save-role-btn',
        'validate-role-btn',
        'cancel-role-btn',
    ].forEach(id => {
        const button = document.getElementById(id);
        if (button) {
            button.disabled = isBusy;
            button.dataset.busy = isBusy ? 'true' : 'false';
        }
    });
}

async function performRoleRequestWithBuiltinSkillRecovery(request) {
    try {
        return await request();
    } catch (error) {
        if (!shouldRetryBuiltinSkillRecovery(error)) {
            throw error;
        }
        await reloadSkillsConfig();
        await refreshRoleSettingsDependencies();
        return request();
    }
}

function shouldRetryBuiltinSkillRecovery(error) {
    const message = String(error?.message || '').trim();
    return message.includes('Unknown skills:');
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
    if (!safeRoleId) {
        return false;
    }
    const coordinatorRoleId = String(roleConfigOptions.coordinator_role_id || '').trim();
    const mainAgentRoleId = String(roleConfigOptions.main_agent_role_id || '').trim();
    return (coordinatorRoleId !== '' && safeRoleId === coordinatorRoleId)
        || (mainAgentRoleId !== '' && safeRoleId === mainAgentRoleId);
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
