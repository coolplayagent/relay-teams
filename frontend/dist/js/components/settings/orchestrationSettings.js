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
import { showToast } from '../../utils/feedback.js';
import { errorToPayload, logError } from '../../utils/logger.js';

let orchestrationConfig = {
    main_agent_prompt: '',
    default_orchestration_preset_id: '',
    presets: [],
};
let orchestrationRoleOptions = [];
let mainAgentRoleId = 'MainAgent';
let coordinatorRoleId = 'Coordinator';
let selectedPresetId = '';
let handlersBound = false;

export function bindOrchestrationSettingsHandlers() {
    if (handlersBound) {
        return;
    }
    handlersBound = true;

    bindActionButton('add-orchestration-preset-btn', handleAddPreset);
    bindActionButton('delete-orchestration-preset-btn', handleDeletePreset);
    bindActionButton('save-orchestration-btn', handleSaveOrchestration);
}

export async function loadOrchestrationSettingsPanel(preferredPresetId = '') {
    try {
        const [config, roleSummaries, roleOptions] = await Promise.all([
            fetchOrchestrationConfig(),
            fetchRoleConfigs(),
            fetchRoleConfigOptions(),
        ]);
        orchestrationConfig = normalizeOrchestrationConfig(config);
        mainAgentRoleId = String(roleOptions?.main_agent_role_id || 'MainAgent').trim() || 'MainAgent';
        coordinatorRoleId = String(roleOptions?.coordinator_role_id || 'Coordinator').trim() || 'Coordinator';
        orchestrationRoleOptions = normalizeRoleOptions(roleSummaries);
        selectedPresetId = resolveSelectedPresetId(preferredPresetId);
        renderOrchestrationPanel();
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

function handlePresetSelection(nextPresetId) {
    syncSelectedPresetFromForm();
    selectedPresetId = String(nextPresetId || '').trim();
    renderPresetList();
    renderDefaultPresetSelect();
    renderPresetEditor();
}

function handleAddPreset() {
    if (orchestrationRoleOptions.length === 0) {
        showToast({
            title: 'No Roles Available',
            message: 'Create at least one normal role before adding an orchestration preset.',
            tone: 'warning',
        });
        return;
    }
    syncSelectedPresetFromForm();
    const nextPresetId = createPresetId();
    orchestrationConfig.presets.push({
        preset_id: nextPresetId,
        name: 'New Preset',
        description: '',
        role_ids: [orchestrationRoleOptions[0].role_id],
        orchestration_prompt: '',
    });
    if (!orchestrationConfig.default_orchestration_preset_id) {
        orchestrationConfig.default_orchestration_preset_id = nextPresetId;
    }
    selectedPresetId = nextPresetId;
    renderOrchestrationPanel();
}

function handleDeletePreset() {
    if (!selectedPresetId) {
        return;
    }
    if (orchestrationConfig.presets.length <= 1) {
        showToast({
            title: 'Preset Required',
            message: 'At least one orchestration preset must remain configured.',
            tone: 'warning',
        });
        return;
    }
    orchestrationConfig.presets = orchestrationConfig.presets.filter(
        preset => preset.preset_id !== selectedPresetId,
    );
    if (orchestrationConfig.default_orchestration_preset_id === selectedPresetId) {
        orchestrationConfig.default_orchestration_preset_id = orchestrationConfig.presets[0]?.preset_id || '';
    }
    selectedPresetId = orchestrationConfig.presets[0]?.preset_id || '';
    renderOrchestrationPanel();
}

async function handleSaveOrchestration() {
    try {
        const draft = buildDraft();
        await saveOrchestrationConfig(draft);
        showToast({
            title: 'Orchestration Saved',
            message: 'Orchestration settings were saved.',
            tone: 'success',
        });
        document.dispatchEvent(new CustomEvent('orchestration-settings-updated'));
        await loadOrchestrationSettingsPanel(selectedPresetId || draft.default_orchestration_preset_id);
        renderStatus('Saved orchestration settings.', 'success');
    } catch (error) {
        renderStatus(error.message || 'Failed to save orchestration settings.', 'danger');
        showToast({
            title: 'Save Failed',
            message: error.message || 'Failed to save orchestration settings.',
            tone: 'danger',
        });
    }
}

function buildDraft() {
    syncSelectedPresetFromForm();
    const mainAgentPrompt = String(
        document.getElementById('orchestration-main-agent-prompt')?.value || '',
    ).trim();
    if (!mainAgentPrompt) {
        throw new Error('Main agent prompt is required.');
    }
    const defaultPresetId = String(
        document.getElementById('orchestration-default-preset-select')?.value || '',
    ).trim();
    if (!defaultPresetId) {
        throw new Error('Default orchestration preset is required.');
    }
    const presets = orchestrationConfig.presets.map(preset => ({
        preset_id: String(preset.preset_id || '').trim(),
        name: String(preset.name || '').trim(),
        description: String(preset.description || '').trim(),
        role_ids: Array.isArray(preset.role_ids)
            ? preset.role_ids.map(roleId => String(roleId || '').trim()).filter(Boolean)
            : [],
        orchestration_prompt: String(preset.orchestration_prompt || '').trim(),
    }));
    if (presets.some(preset => !preset.preset_id || !preset.name || preset.role_ids.length === 0)) {
        throw new Error('Each orchestration preset requires an id, a name, and at least one role.');
    }
    if (!presets.some(preset => preset.preset_id === defaultPresetId)) {
        throw new Error('Default orchestration preset must match an existing preset.');
    }
    return {
        main_agent_prompt: mainAgentPrompt,
        default_orchestration_preset_id: defaultPresetId,
        presets,
    };
}

function renderOrchestrationPanel() {
    renderMainAgentCard();
    renderPresetList();
    renderDefaultPresetSelect();
    renderPresetEditor();
    renderStatus('', '');
}

function renderMainAgentCard() {
    const host = document.getElementById('orchestration-main-agent-card');
    if (!host) {
        return;
    }
    host.innerHTML = `
        <div class="orchestration-fixed-role">
            <div class="orchestration-fixed-role-head">
                <div class="orchestration-fixed-role-title">Main Agent</div>
                <div class="orchestration-fixed-role-meta">${escapeHtml(mainAgentRoleId)}</div>
            </div>
            <p class="orchestration-fixed-role-copy">Used by 普通模式. Identity stays fixed here; tool and model changes still belong in the Roles tab.</p>
            <div class="form-group">
                <label for="orchestration-main-agent-prompt">普通模式提示词</label>
                <textarea id="orchestration-main-agent-prompt" class="config-textarea orchestration-prompt-textarea" placeholder="Prompt used by the main agent in 普通模式.">${escapeHtml(orchestrationConfig.main_agent_prompt || '')}</textarea>
            </div>
        </div>
    `;
}

function renderPresetList() {
    const host = document.getElementById('orchestration-preset-list');
    if (!host) {
        return;
    }
    if (orchestrationConfig.presets.length === 0) {
        host.innerHTML = `
            <div class="settings-empty-state settings-empty-state-compact">
                <h4>No presets</h4>
                <p>Add an orchestration preset to choose roles and routing guidance.</p>
            </div>
        `;
        return;
    }
    host.innerHTML = `
        <div class="orchestration-preset-records">
            ${orchestrationConfig.presets.map(preset => `
                <button
                    type="button"
                    class="orchestration-preset-record${preset.preset_id === selectedPresetId ? ' active' : ''}"
                    data-preset-id="${escapeHtml(preset.preset_id)}"
                >
                    <span class="orchestration-preset-record-name">${escapeHtml(preset.name || preset.preset_id)}</span>
                    <span class="orchestration-preset-record-meta">${escapeHtml(preset.preset_id)}</span>
                </button>
            `).join('')}
        </div>
    `;
    host.querySelectorAll('[data-preset-id]').forEach(button => {
        button.onclick = () => {
            handlePresetSelection(button.getAttribute('data-preset-id'));
        };
    });
}

function renderDefaultPresetSelect() {
    const select = document.getElementById('orchestration-default-preset-select');
    if (!select) {
        return;
    }
    const defaultPresetId = String(orchestrationConfig.default_orchestration_preset_id || '').trim();
    select.innerHTML = orchestrationConfig.presets.map(preset => {
        const presetId = String(preset.preset_id || '').trim();
        const selected = presetId === defaultPresetId ? ' selected' : '';
        return `<option value="${escapeHtml(presetId)}"${selected}>${escapeHtml(preset.name || presetId)}</option>`;
    }).join('');
}

function renderPresetEditor() {
    const host = document.getElementById('orchestration-preset-editor');
    if (!host) {
        return;
    }
    const preset = orchestrationConfig.presets.find(item => item.preset_id === selectedPresetId) || null;
    const deleteButton = document.getElementById('delete-orchestration-preset-btn');
    if (deleteButton) {
        deleteButton.disabled = !preset || orchestrationConfig.presets.length <= 1;
    }
    if (!preset) {
        host.innerHTML = `
            <div class="settings-empty-state settings-empty-state-compact">
                <h4>No preset selected</h4>
                <p>Select a preset to edit its roles and orchestration prompt.</p>
            </div>
        `;
        return;
    }
    host.innerHTML = `
        <div class="orchestration-preset-form">
            <div class="profile-editor-grid role-editor-grid">
                <div class="form-group">
                    <label for="orchestration-preset-id-input">Preset ID</label>
                    <input type="text" id="orchestration-preset-id-input" value="${escapeHtml(preset.preset_id)}" autocomplete="off">
                </div>
                <div class="form-group">
                    <label for="orchestration-preset-name-input">Preset Name</label>
                    <input type="text" id="orchestration-preset-name-input" value="${escapeHtml(preset.name)}" autocomplete="off">
                </div>
                <div class="form-group form-group-span-2">
                    <label for="orchestration-preset-description-input">Description</label>
                    <input type="text" id="orchestration-preset-description-input" value="${escapeHtml(preset.description)}" autocomplete="off">
                </div>
            </div>
            <section class="role-editor-section orchestration-role-section">
                <h5>Allowed Roles</h5>
                <div class="role-option-picker role-option-picker-single" id="orchestration-role-picker">
                    ${renderRolePickerOptions(preset)}
                </div>
            </section>
            <section class="role-editor-section orchestration-role-section">
                <div class="form-group">
                    <label for="orchestration-preset-prompt-input">编排提示词</label>
                    <textarea id="orchestration-preset-prompt-input" class="config-textarea orchestration-prompt-textarea" placeholder="Explain how Coordinator should split work, how to select roles, and how to finish.">${escapeHtml(preset.orchestration_prompt)}</textarea>
                </div>
            </section>
        </div>
    `;
}

function renderRolePickerOptions(preset) {
    if (orchestrationRoleOptions.length === 0) {
        return '<div class="role-option-empty">No normal roles available.</div>';
    }
    const selectedRoleIds = new Set(
        Array.isArray(preset?.role_ids) ? preset.role_ids.map(roleId => String(roleId || '').trim()) : [],
    );
    return orchestrationRoleOptions.map(role => `
        <label class="role-option-item">
            <input type="checkbox" data-role-id="${escapeHtml(role.role_id)}"${selectedRoleIds.has(role.role_id) ? ' checked' : ''}>
            <span class="role-option-check" aria-hidden="true"></span>
            <span class="role-option-label">${escapeHtml(role.name)} <em>${escapeHtml(role.role_id)}</em></span>
        </label>
    `).join('');
}

function syncSelectedPresetFromForm() {
    const preset = orchestrationConfig.presets.find(item => item.preset_id === selectedPresetId);
    if (!preset) {
        return;
    }
    const nextPresetId = String(
        document.getElementById('orchestration-preset-id-input')?.value || preset.preset_id,
    ).trim();
    const nextName = String(
        document.getElementById('orchestration-preset-name-input')?.value || preset.name,
    ).trim();
    preset.preset_id = nextPresetId || preset.preset_id;
    preset.name = nextName || preset.name;
    preset.description = String(
        document.getElementById('orchestration-preset-description-input')?.value || '',
    ).trim();
    preset.orchestration_prompt = String(
        document.getElementById('orchestration-preset-prompt-input')?.value || '',
    ).trim();
    const rolePicker = document.getElementById('orchestration-role-picker');
    const nextRoleIds = [];
    rolePicker?.querySelectorAll('input[type="checkbox"]').forEach(input => {
        if (input.checked) {
            nextRoleIds.push(String(input.dataset.roleId || '').trim());
        }
    });
    preset.role_ids = nextRoleIds.filter(Boolean);
    if (orchestrationConfig.default_orchestration_preset_id === selectedPresetId) {
        orchestrationConfig.default_orchestration_preset_id = preset.preset_id;
    }
    selectedPresetId = preset.preset_id;
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
    const mainHost = document.getElementById('orchestration-main-agent-card');
    const editorHost = document.getElementById('orchestration-preset-editor');
    const message = error?.message || 'Unable to load orchestration settings.';
    if (mainHost) {
        mainHost.innerHTML = `
            <div class="settings-empty-state settings-empty-state-compact">
                <h4>Load failed</h4>
                <p>${escapeHtml(message)}</p>
            </div>
        `;
    }
    if (listHost) {
        listHost.innerHTML = '';
    }
    if (editorHost) {
        editorHost.innerHTML = '';
    }
    renderStatus(message, 'danger');
}

function normalizeOrchestrationConfig(config) {
    return {
        main_agent_prompt: String(config?.main_agent_prompt || '').trim(),
        default_orchestration_preset_id: String(config?.default_orchestration_preset_id || '').trim(),
        presets: Array.isArray(config?.presets)
            ? config.presets.map(preset => ({
                preset_id: String(preset?.preset_id || '').trim(),
                name: String(preset?.name || '').trim(),
                description: String(preset?.description || '').trim(),
                role_ids: Array.isArray(preset?.role_ids)
                    ? preset.role_ids.map(roleId => String(roleId || '').trim()).filter(Boolean)
                    : [],
                orchestration_prompt: String(preset?.orchestration_prompt || '').trim(),
            })).filter(preset => preset.preset_id)
            : [],
    };
}

function normalizeRoleOptions(roleSummaries) {
    const rows = Array.isArray(roleSummaries) ? roleSummaries : [];
    return rows
        .map(role => ({
            role_id: String(role?.role_id || '').trim(),
            name: String(role?.name || role?.role_id || '').trim(),
        }))
        .filter(role => role.role_id && role.role_id !== coordinatorRoleId && role.role_id !== mainAgentRoleId)
        .sort((left, right) => left.name.localeCompare(right.name));
}

function resolveSelectedPresetId(preferredPresetId = '') {
    const safePreferredPresetId = String(preferredPresetId || '').trim();
    if (safePreferredPresetId && orchestrationConfig.presets.some(preset => preset.preset_id === safePreferredPresetId)) {
        return safePreferredPresetId;
    }
    const currentDefaultPresetId = String(orchestrationConfig.default_orchestration_preset_id || '').trim();
    if (currentDefaultPresetId && orchestrationConfig.presets.some(preset => preset.preset_id === currentDefaultPresetId)) {
        return currentDefaultPresetId;
    }
    return orchestrationConfig.presets[0]?.preset_id || '';
}

function createPresetId() {
    const baseId = 'preset';
    const existingIds = new Set(orchestrationConfig.presets.map(preset => preset.preset_id));
    let suffix = orchestrationConfig.presets.length + 1;
    let candidate = `${baseId}_${suffix}`;
    while (existingIds.has(candidate)) {
        suffix += 1;
        candidate = `${baseId}_${suffix}`;
    }
    return candidate;
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
