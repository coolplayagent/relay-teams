/**
 * components/settings/triggerSettings.js
 */
import {
    createTrigger,
    disableTrigger,
    enableTrigger,
    fetchOrchestrationConfig,
    fetchRoleConfigOptions,
    fetchTriggers,
    fetchWorkspaces,
    updateTrigger,
} from '../../core/api.js';
import { showToast } from '../../utils/feedback.js';
import { t } from '../../utils/i18n.js';
import { errorToPayload, logError } from '../../utils/logger.js';

const FEISHU_PLATFORM = 'feishu';
const FEISHU_SOURCE_TYPE = 'im';
const DEFAULT_TRIGGER_RULE = 'mention_only';
const DEFAULT_WORKSPACE_ID = 'default';
const DEFAULT_SESSION_MODE = 'normal';
const DEFAULT_THINKING_EFFORT = 'medium';

let handlersBound = false;
let languageBound = false;
let state = createInitialState();

export function bindTriggerSettingsHandlers() {
    if (!handlersBound) {
        bindAction('add-trigger-btn', handleAddTrigger);
        bindAction('save-trigger-btn', handleSaveTriggerSettings);
        bindAction('cancel-trigger-btn', handleCancelTriggerSettings);
        handlersBound = true;
    }
    if (!languageBound && typeof document.addEventListener === 'function') {
        document.addEventListener('agent-teams-language-changed', renderTriggerSettingsPanel);
        languageBound = true;
    }
}

export async function loadTriggerSettingsPanel(options = {}) {
    try {
        const [triggers, workspaces, roleOptions, orchestrationConfig] = await Promise.all([
            fetchTriggers(),
            fetchWorkspaces(),
            fetchRoleConfigOptions(),
            fetchOrchestrationConfig(),
        ]);
        state = {
            ...createInitialState(),
            providerExpanded: options.openProvider === FEISHU_PLATFORM,
            feishuTriggers: normalizeFeishuTriggers(triggers),
            workspaces: normalizeWorkspaces(workspaces),
            normalRoles: normalizeNormalRoles(roleOptions),
            orchestrationPresets: normalizeOrchestrationPresets(orchestrationConfig),
        };
        renderTriggerSettingsPanel();
    } catch (error) {
        logError(
            'frontend.trigger_settings.load_failed',
            'Failed to load trigger settings',
            errorToPayload(error),
        );
        renderLoadError(error?.message || 'Unable to load trigger settings.');
    }
}

function createInitialState() {
    return {
        providerExpanded: false,
        feishuTriggers: [],
        editingTriggerId: '',
        editingTriggerDraft: null,
        workspaces: [],
        normalRoles: [],
        orchestrationPresets: [],
        statusMessage: '',
        statusTone: '',
    };
}

function bindAction(id, handler) {
    const button = document.getElementById(id);
    if (button) {
        button.onclick = handler;
    }
}

function normalizeFeishuTriggers(payload) {
    const rows = Array.isArray(payload) ? payload : [];
    return rows
        .filter(trigger => {
            const sourceType = String(trigger?.source_type || '').trim().toLowerCase();
            const provider = String(trigger?.source_config?.provider || '').trim().toLowerCase();
            return sourceType === FEISHU_SOURCE_TYPE && provider === FEISHU_PLATFORM;
        })
        .map(trigger => ({
            trigger_id: String(trigger?.trigger_id || '').trim(),
            name: String(trigger?.name || '').trim(),
            display_name: String(trigger?.display_name || '').trim(),
            status: String(trigger?.status || 'disabled').trim() || 'disabled',
            source_config: trigger?.source_config && typeof trigger.source_config === 'object' ? { ...trigger.source_config } : {},
            target_config: trigger?.target_config && typeof trigger.target_config === 'object' ? { ...trigger.target_config } : {},
            secret_status: trigger?.secret_status && typeof trigger.secret_status === 'object' ? { ...trigger.secret_status } : {},
        }));
}

function normalizeWorkspaces(payload) {
    const rows = Array.isArray(payload) ? payload : [];
    return rows
        .map(workspace => ({
            workspace_id: String(workspace?.workspace_id || '').trim(),
            root_path: String(workspace?.root_path || '').trim(),
        }))
        .filter(workspace => workspace.workspace_id);
}

function normalizeNormalRoles(payload) {
    const rows = Array.isArray(payload?.normal_mode_roles) ? payload.normal_mode_roles : [];
    return rows
        .map(role => ({
            role_id: String(role?.role_id || '').trim(),
            name: String(role?.name || role?.role_id || '').trim(),
        }))
        .filter(role => role.role_id);
}

function normalizeOrchestrationPresets(payload) {
    const rows = Array.isArray(payload?.presets) ? payload.presets : [];
    return rows
        .map(preset => ({
            preset_id: String(preset?.preset_id || '').trim(),
            name: String(preset?.name || preset?.preset_id || '').trim(),
        }))
        .filter(preset => preset.preset_id);
}

function renderTriggerSettingsPanel() {
    renderPlatformList();
    renderEditorPanel();
    renderStatus();
    renderActions();
}

function renderPlatformList() {
    const host = document.getElementById('trigger-platform-list');
    if (!host) {
        return;
    }
    if (state.editingTriggerDraft) {
        host.style.display = 'none';
        host.innerHTML = '';
        return;
    }
    host.style.display = 'block';
    host.innerHTML = `
        <div class="trigger-platform-shell">
            <div class="role-record trigger-platform-record${state.providerExpanded ? ' trigger-platform-record-expanded' : ''}" data-trigger-platform="${FEISHU_PLATFORM}">
                <div class="role-record-main">
                    <div class="role-record-title-row trigger-platform-title-row">
                        <div class="trigger-platform-chevron" aria-hidden="true">${state.providerExpanded ? '&#9662;' : '&#9656;'}</div>
                        <div class="trigger-platform-title-block">
                            <div class="trigger-platform-title-line">
                                <div class="role-record-title">${escapeHtml(t('settings.triggers.feishu'))}</div>
                                <div class="profile-card-chips role-record-chips">
                                    <span class="profile-card-chip">${escapeHtml(credentialsReady() ? t('settings.triggers.ready') : t('settings.triggers.credentials_missing'))}</span>
                                </div>
                            </div>
                            <div class="trigger-platform-summary">
                                <span class="profile-card-chip">${escapeHtml(t('settings.triggers.trigger_count').replace('{count}', String(state.feishuTriggers.length)))}</span>
                                <span class="profile-card-chip">${escapeHtml(t('settings.triggers.enabled_count').replace('{count}', String(state.feishuTriggers.filter(isEnabled).length)))}</span>
                                <span class="profile-card-chip">${escapeHtml(formatCredentialSummary())}</span>
                            </div>
                        </div>
                    </div>
                </div>
                <div class="role-record-actions">
                    <button class="settings-inline-action settings-list-action trigger-platform-open-btn" data-trigger-platform="${FEISHU_PLATFORM}" type="button">${escapeHtml(state.providerExpanded ? t('settings.triggers.collapse') : t('settings.triggers.configure'))}</button>
                </div>
            </div>
            ${state.providerExpanded && !state.editingTriggerDraft ? `<div class="trigger-platform-body"><div class="trigger-platform-children">${renderRecords()}</div></div>` : ''}
        </div>
    `;
    host.querySelectorAll('.trigger-platform-open-btn').forEach(button => {
        button.onclick = event => {
            event?.stopPropagation?.();
            toggleProviderExpanded();
        };
    });
    host.querySelectorAll('.trigger-platform-record').forEach(record => {
        record.onclick = event => {
            if (state.editingTriggerDraft) {
                event?.stopPropagation?.();
                return;
            }
            toggleProviderExpanded();
        };
    });
    host.querySelectorAll('.trigger-record').forEach(button => {
        button.onclick = () => openTriggerEditor(button.dataset.triggerId);
    });
    host.querySelectorAll('.trigger-record-edit-btn').forEach(button => {
        button.onclick = event => {
            event?.stopPropagation?.();
            openTriggerEditor(button.dataset.triggerId);
        };
    });
    host.querySelectorAll('.trigger-record-toggle-btn').forEach(button => {
        button.onclick = async event => {
            event?.stopPropagation?.();
            await handleToggleTrigger(button.dataset.triggerId);
        };
    });
}

function renderRecords() {
    if (state.feishuTriggers.length === 0) {
        return `<div class="settings-empty-state"><h4>${escapeHtml(t('settings.triggers.none'))}</h4><p>${escapeHtml(t('settings.triggers.none_copy'))}</p></div>`;
    }
    return `
        <div class="role-records trigger-records">
            ${state.feishuTriggers.map(trigger => renderTriggerRecord(trigger)).join('')}
        </div>
    `;
}

function renderTriggerRecord(trigger) {
    const enabled = isEnabled(trigger);
    const appName = resolveAppName(trigger.source_config);
    const triggerId = escapeHtml(trigger.trigger_id);
    return `
        <div class="role-record trigger-record" data-trigger-id="${triggerId}">
            <div class="role-record-main">
                <div class="role-record-title-row trigger-record-title-row">
                    <div class="role-record-title">${escapeHtml(trigger.name || t('settings.triggers.unnamed'))}</div>
                    <div class="profile-card-chips role-record-chips">
                        <span class="profile-card-chip">${escapeHtml(enabled ? t('settings.field.enabled') : t('settings.roles.disabled'))}</span>
                        <span class="profile-card-chip">${escapeHtml(trigger.secret_status?.app_secret_configured ? t('settings.triggers.credentials_ready') : t('settings.triggers.credentials_missing'))}</span>
                    </div>
                </div>
                ${appName ? `<div class="role-record-meta trigger-record-meta"><span>${escapeHtml(appName)}</span></div>` : ''}
            </div>
            <div class="role-record-actions trigger-record-actions">
                <button class="settings-inline-action settings-list-action trigger-record-toggle-btn" data-trigger-id="${triggerId}" type="button">${escapeHtml(enabled ? t('settings.triggers.disable_trigger') : t('settings.triggers.enable_trigger'))}</button>
                <button class="settings-inline-action settings-list-action trigger-record-edit-btn" data-trigger-id="${triggerId}" type="button">${escapeHtml(t('settings.roles.edit'))}</button>
            </div>
        </div>
    `;
}

function renderEditorPanel() {
    const panel = document.getElementById('trigger-provider-detail-panel');
    const host = document.getElementById('trigger-provider-detail');
    if (!panel || !host) {
        return;
    }
    if (!state.editingTriggerDraft) {
        panel.style.display = 'none';
        host.innerHTML = '';
        return;
    }
    panel.style.display = 'block';
    host.innerHTML = renderEditor();
    bindDraftInputs();
}

function renderEditor() {
    const draft = state.editingTriggerDraft;
    if (!draft) {
        return '';
    }
    const secretStatus = draft.secret_status || {};
    const sessionMode = resolveSessionMode(draft.target_config);
    const thinkingEnabled = resolveThinkingEnabled(draft.target_config);
    return `
        <div class="role-editor-panel">
            <div class="role-editor-form">
                <div class="role-editor-header">
                    <div>
                        <h4>${escapeHtml(t('settings.triggers.editor'))}</h4>
                    </div>
                </div>
                <div class="role-editor-sections">
                    <section class="role-editor-section">
                        <h5>${escapeHtml(t('settings.triggers.bot_configuration'))}</h5>
                        <div class="form-row">
                            <div class="form-group form-group-span-2">
                                <label for="feishu-trigger-name-input">${escapeHtml(t('settings.triggers.trigger_name'))}</label>
                                <input id="feishu-trigger-name-input" value="${escapeHtml(draft.name)}">
                            </div>
                        </div>
                        <div class="form-row trigger-bot-config-row">
                            <div class="form-group">
                                <label for="feishu-app-name-input">${escapeHtml(t('settings.triggers.feishu_app_name'))}</label>
                                <input id="feishu-app-name-input" placeholder="${escapeHtml(t('settings.triggers.feishu_app_name_placeholder'))}" value="${escapeHtml(resolveAppName(draft.source_config))}">
                            </div>
                            <div class="form-group">
                                <label for="feishu-app-id-input">${escapeHtml(t('settings.triggers.feishu_app_id'))}</label>
                                <input id="feishu-app-id-input" placeholder="${escapeHtml(t('settings.triggers.feishu_app_id_placeholder'))}" value="${escapeHtml(resolveAppId(draft.source_config))}">
                            </div>
                            <div class="form-group">
                                <label for="feishu-app-secret-input">${escapeHtml(t('settings.triggers.feishu_app_secret'))}</label>
                                <input id="feishu-app-secret-input" type="password" placeholder="${escapeHtml(secretStatus.app_secret_configured ? t('settings.triggers.secret_keep_placeholder') : t('settings.triggers.feishu_app_secret_placeholder'))}" value="">
                            </div>
                        </div>
                    </section>
                    <section class="role-editor-section">
                        <h5>${escapeHtml(t('settings.triggers.session_configuration'))}</h5>
                        <div class="form-row">
                            <div class="form-group">
                                <label for="feishu-trigger-workspace-id-input">${escapeHtml(t('settings.triggers.workspace'))}</label>
                                <select id="feishu-trigger-workspace-id-input">
                                    ${renderWorkspaceOptions(resolveWorkspaceId(draft.target_config))}
                                </select>
                            </div>
                            <div class="form-group">
                                <label for="feishu-trigger-rule-input">${escapeHtml(t('settings.triggers.rule'))}</label>
                                <select id="feishu-trigger-rule-input">
                                    <option value="mention_only"${resolveRule(draft.source_config) === 'mention_only' ? ' selected' : ''}>mention_only</option>
                                    <option value="all_messages"${resolveRule(draft.source_config) === 'all_messages' ? ' selected' : ''}>all_messages</option>
                                </select>
                            </div>
                        </div>
                        <div class="form-row trigger-session-config-row">
                            <div class="form-group">
                                <label for="feishu-session-mode-input">${escapeHtml(t('settings.triggers.mode'))}</label>
                                <select id="feishu-session-mode-input">
                                    <option value="normal"${sessionMode === 'normal' ? ' selected' : ''}>${escapeHtml(t('composer.mode_normal'))}</option>
                                    <option value="orchestration"${sessionMode === 'orchestration' ? ' selected' : ''}>${escapeHtml(t('composer.mode_orchestration'))}</option>
                                </select>
                            </div>
                            <div class="form-group" id="feishu-normal-role-field"${sessionMode === 'normal' ? '' : ' style="display:none;"'}>
                                <label for="feishu-normal-root-role-id-input">${escapeHtml(t('settings.triggers.normal_root_role_id'))}</label>
                                <select id="feishu-normal-root-role-id-input">
                                    ${renderNormalRoleOptions(resolveNormalRootRoleId(draft.target_config))}
                                </select>
                            </div>
                            <div class="form-group" id="feishu-preset-field"${sessionMode === 'orchestration' ? '' : ' style="display:none;"'}>
                                <label for="feishu-orchestration-preset-id-input">${escapeHtml(t('settings.triggers.orchestration_preset_id'))}</label>
                                <select id="feishu-orchestration-preset-id-input">
                                    ${renderOrchestrationPresetOptions(resolveOrchestrationPresetId(draft.target_config))}
                                </select>
                            </div>
                            <div class="form-group">
                                <label for="feishu-trigger-yolo-input">${escapeHtml(t('settings.triggers.yolo'))}</label>
                                <select id="feishu-trigger-yolo-input">
                                    ${renderBooleanOptions(resolveYolo(draft.target_config))}
                                </select>
                            </div>
                            <div class="form-group">
                                <label for="feishu-trigger-thinking-enabled-input">${escapeHtml(t('settings.triggers.thinking_enabled'))}</label>
                                <select id="feishu-trigger-thinking-enabled-input">
                                    ${renderBooleanOptions(thinkingEnabled)}
                                </select>
                            </div>
                            <div class="form-group" id="feishu-thinking-effort-field"${thinkingEnabled ? '' : ' style="display:none;"'}>
                                <label for="feishu-thinking-effort-input">${escapeHtml(t('settings.triggers.thinking_effort'))}</label>
                                <select id="feishu-thinking-effort-input">
                                    ${['minimal', 'low', 'medium', 'high'].map(effort => `<option value="${effort}"${resolveThinkingEffort(draft.target_config) === effort ? ' selected' : ''}>${effort}</option>`).join('')}
                                </select>
                            </div>
                        </div>
                    </section>
                </div>
            </div>
        </div>
    `;
}

function bindDraftInputs() {
    [
        'feishu-trigger-name-input',
        'feishu-app-name-input',
        'feishu-app-id-input',
        'feishu-app-secret-input',
        'feishu-trigger-workspace-id-input',
        'feishu-trigger-rule-input',
        'feishu-session-mode-input',
        'feishu-normal-root-role-id-input',
        'feishu-orchestration-preset-id-input',
        'feishu-trigger-thinking-enabled-input',
        'feishu-thinking-effort-input',
        'feishu-trigger-yolo-input',
    ].forEach(id => {
        const input = document.getElementById(id);
        if (!input) {
            return;
        }
        input.oninput = syncEditorVisibility;
        input.onchange = syncEditorVisibility;
    });
}

function syncEditorVisibility() {
    renderActions();
    syncSessionModeVisibility();
    syncThinkingVisibility();
}

function toggleProviderExpanded() {
    state.providerExpanded = !state.providerExpanded;
    if (!state.providerExpanded) {
        state.editingTriggerId = '';
        state.editingTriggerDraft = null;
        state.statusMessage = '';
        state.statusTone = '';
    }
    renderTriggerSettingsPanel();
}

function handleAddTrigger() {
    if (!state.providerExpanded) {
        return;
    }
    state.statusMessage = '';
    state.statusTone = '';
    state.editingTriggerId = '';
    state.editingTriggerDraft = {
        trigger_id: '',
        name: `feishu_trigger_${state.feishuTriggers.length + 1}`,
        display_name: '',
        status: 'enabled',
        source_config: {
            provider: FEISHU_PLATFORM,
            trigger_rule: DEFAULT_TRIGGER_RULE,
            app_id: '',
            app_name: '',
        },
        target_config: {
            workspace_id: resolveDefaultWorkspaceOption(),
            session_mode: DEFAULT_SESSION_MODE,
            normal_root_role_id: resolveDefaultNormalRoleOption(),
            yolo: true,
            thinking: { enabled: false, effort: null },
        },
        secret_status: {},
    };
    renderTriggerSettingsPanel();
}

function openTriggerEditor(triggerId) {
    const trigger = state.feishuTriggers.find(item => item.trigger_id === String(triggerId || '').trim());
    if (!trigger) {
        return;
    }
    state.statusMessage = '';
    state.statusTone = '';
    state.editingTriggerId = trigger.trigger_id;
    state.editingTriggerDraft = {
        trigger_id: trigger.trigger_id,
        name: trigger.name,
        display_name: trigger.display_name,
        status: trigger.status,
        source_config: { ...trigger.source_config },
        target_config: { ...trigger.target_config },
        secret_status: { ...trigger.secret_status },
    };
    renderTriggerSettingsPanel();
}

async function handleToggleTrigger(triggerId) {
    const trigger = state.feishuTriggers.find(item => item.trigger_id === String(triggerId || '').trim());
    if (!trigger) {
        return;
    }
    try {
        if (isEnabled(trigger)) {
            await disableTrigger(trigger.trigger_id);
        } else {
            await enableTrigger(trigger.trigger_id);
        }
        await loadTriggerSettingsPanel({ openProvider: FEISHU_PLATFORM });
    } catch (error) {
        state.statusMessage = error?.message || 'Failed to update trigger status.';
        state.statusTone = 'danger';
        renderStatus();
    }
}

async function handleSaveTriggerSettings() {
    try {
        const draft = readDraftFromInputs();
        if (draft.trigger_id) {
            await updateTrigger(draft.trigger_id, draft.update_payload);
        } else {
            await createTrigger(draft.payload);
        }
        showToast({
            title: t('settings.triggers.saved'),
            message: t('settings.triggers.saved_message'),
            tone: 'success',
        });
        await loadTriggerSettingsPanel({ openProvider: FEISHU_PLATFORM });
    } catch (error) {
        state.statusMessage = error?.message || 'Failed to save trigger settings.';
        state.statusTone = 'danger';
        renderStatus();
        showToast({
            title: t('settings.triggers.save_failed'),
            message: state.statusMessage,
            tone: 'danger',
        });
    }
}

function handleCancelTriggerSettings() {
    state.editingTriggerId = '';
    state.editingTriggerDraft = null;
    state.statusMessage = '';
    state.statusTone = '';
    renderTriggerSettingsPanel();
}

function readDraftFromInputs() {
    if (!state.editingTriggerDraft) {
        throw new Error(t('settings.triggers.missing_name'));
    }
    const appId = readValue('feishu-app-id-input');
    const appName = readValue('feishu-app-name-input');
    const appSecret = readValue('feishu-app-secret-input');
    const name = readValue('feishu-trigger-name-input');
    const workspaceId = readValue('feishu-trigger-workspace-id-input');
    const sessionMode = readValue('feishu-session-mode-input') || DEFAULT_SESSION_MODE;
    const orchestrationPresetId = readValue('feishu-orchestration-preset-id-input');
    const thinkingEnabled = readBooleanSelect('feishu-trigger-thinking-enabled-input', false);

    if (!name) {
        throw new Error(t('settings.triggers.missing_name'));
    }
    if (!workspaceId) {
        throw new Error(t('settings.triggers.missing_workspace'));
    }
    if (!appId) {
        throw new Error(t('settings.triggers.missing_app_id'));
    }
    if (!appName) {
        throw new Error(t('settings.triggers.missing_app_name'));
    }
    if (!state.editingTriggerId && !appSecret) {
        throw new Error(t('settings.triggers.missing_app_secret'));
    }
    if (sessionMode === 'orchestration' && !orchestrationPresetId) {
        throw new Error(t('settings.triggers.missing_orchestration_preset_id'));
    }

    const payload = {
        name,
        display_name: null,
        source_type: FEISHU_SOURCE_TYPE,
        source_config: {
            provider: FEISHU_PLATFORM,
            trigger_rule: readValue('feishu-trigger-rule-input') || DEFAULT_TRIGGER_RULE,
            app_id: appId,
            app_name: appName,
        },
        auth_policies: [],
        target_config: {
            workspace_id: workspaceId,
            session_mode: sessionMode,
            yolo: readBooleanSelect('feishu-trigger-yolo-input', true),
            thinking: {
                enabled: thinkingEnabled,
                effort: thinkingEnabled
                    ? (readValue('feishu-thinking-effort-input') || DEFAULT_THINKING_EFFORT)
                    : null,
            },
        },
        enabled: true,
    };

    const normalRootRoleId = readValue('feishu-normal-root-role-id-input');
    if (sessionMode === 'normal' && normalRootRoleId) {
        payload.target_config.normal_root_role_id = normalRootRoleId;
    }
    if (sessionMode === 'orchestration' && orchestrationPresetId) {
        payload.target_config.orchestration_preset_id = orchestrationPresetId;
    }

    const secretConfig = {};
    if (appSecret) {
        secretConfig.app_secret = appSecret;
    }
    if (Object.keys(secretConfig).length > 0) {
        payload.secret_config = secretConfig;
    }

    const updatePayload = {
        name: payload.name,
        display_name: payload.display_name,
        source_config: payload.source_config,
        auth_policies: payload.auth_policies,
        target_config: payload.target_config,
    };
    if (payload.secret_config) {
        updatePayload.secret_config = payload.secret_config;
    }

    return {
        trigger_id: state.editingTriggerId,
        payload,
        update_payload: updatePayload,
    };
}

function renderActions() {
    const bar = document.getElementById('settings-actions-bar');
    if (bar) {
        bar.style.display = state.providerExpanded ? 'flex' : 'none';
    }
    setVisible('add-trigger-btn', state.providerExpanded && state.editingTriggerDraft === null);
    setVisible('save-trigger-btn', state.editingTriggerDraft !== null);
    setVisible('cancel-trigger-btn', state.editingTriggerDraft !== null);
}

function renderStatus() {
    const statusEl = document.getElementById('trigger-editor-status');
    if (!statusEl) {
        return;
    }
    statusEl.className = 'role-editor-status';
    if (!state.statusMessage) {
        statusEl.style.display = 'none';
        statusEl.textContent = '';
        return;
    }
    statusEl.style.display = 'block';
    if (state.statusTone) {
        statusEl.classList.add(`role-editor-status-${state.statusTone}`);
    }
    statusEl.textContent = state.statusMessage;
}

function renderLoadError(message) {
    const listHost = document.getElementById('trigger-platform-list');
    const panel = document.getElementById('trigger-provider-detail-panel');
    if (listHost) {
        listHost.style.display = 'block';
        listHost.innerHTML = `<div class="settings-empty-state"><h4>${escapeHtml(t('settings.triggers.load_failed'))}</h4><p>${escapeHtml(message)}</p></div>`;
    }
    if (panel) {
        panel.style.display = 'none';
    }
    renderActions();
}

function isEnabled(trigger) {
    return String(trigger?.status || '').trim().toLowerCase() === 'enabled';
}

function credentialsReady() {
    return state.feishuTriggers.length > 0 && state.feishuTriggers.every(trigger => trigger.secret_status?.app_secret_configured === true);
}

function formatCredentialSummary() {
    const missing = state.feishuTriggers.filter(trigger => trigger.secret_status?.app_secret_configured !== true).length;
    if (missing === 0 && state.feishuTriggers.length > 0) {
        return t('settings.triggers.credentials_ready');
    }
    return t('settings.triggers.credentials_missing_count').replace('{count}', String(missing));
}

function resolveWorkspaceId(targetConfig) {
    return String(targetConfig?.workspace_id || DEFAULT_WORKSPACE_ID).trim() || DEFAULT_WORKSPACE_ID;
}

function resolveSessionMode(targetConfig) {
    return String(targetConfig?.session_mode || DEFAULT_SESSION_MODE).trim() || DEFAULT_SESSION_MODE;
}

function resolveNormalRootRoleId(targetConfig) {
    return String(targetConfig?.normal_root_role_id || '').trim();
}

function resolveOrchestrationPresetId(targetConfig) {
    return String(targetConfig?.orchestration_preset_id || '').trim();
}

function resolveThinkingEnabled(targetConfig) {
    return targetConfig?.thinking?.enabled === true;
}

function resolveThinkingEffort(targetConfig) {
    return String(targetConfig?.thinking?.effort || DEFAULT_THINKING_EFFORT).trim() || DEFAULT_THINKING_EFFORT;
}

function resolveYolo(targetConfig) {
    return targetConfig?.yolo !== false;
}

function resolveRule(sourceConfig) {
    return String(sourceConfig?.trigger_rule || DEFAULT_TRIGGER_RULE).trim() || DEFAULT_TRIGGER_RULE;
}

function resolveAppId(sourceConfig) {
    return String(sourceConfig?.app_id || '').trim();
}

function resolveAppName(sourceConfig) {
    return String(sourceConfig?.app_name || '').trim();
}

function resolveDefaultWorkspaceOption() {
    return String(state.workspaces[0]?.workspace_id || DEFAULT_WORKSPACE_ID).trim() || DEFAULT_WORKSPACE_ID;
}

function resolveDefaultNormalRoleOption() {
    return String(state.normalRoles[0]?.role_id || '').trim();
}

function readValue(id) {
    return String(document.getElementById(id)?.value || '').trim();
}

function readBooleanSelect(id, fallback) {
    const value = readValue(id).toLowerCase();
    if (value === 'true') {
        return true;
    }
    if (value === 'false') {
        return false;
    }
    return fallback;
}

function setVisible(id, visible) {
    const element = document.getElementById(id);
    if (element) {
        element.style.display = visible ? 'inline-flex' : 'none';
    }
}

function renderWorkspaceOptions(selectedWorkspaceId) {
    if (state.workspaces.length === 0) {
        return `<option value="">${escapeHtml(t('settings.triggers.no_workspaces'))}</option>`;
    }
    return state.workspaces.map(workspace => {
        const selected = workspace.workspace_id === selectedWorkspaceId ? ' selected' : '';
        return `<option value="${escapeHtml(workspace.workspace_id)}"${selected}>${escapeHtml(formatWorkspaceLabel(workspace))}</option>`;
    }).join('');
}

function renderNormalRoleOptions(selectedRoleId) {
    if (state.normalRoles.length === 0) {
        return `<option value="">${escapeHtml(t('composer.no_roles'))}</option>`;
    }
    return state.normalRoles.map(role => {
        const selected = role.role_id === selectedRoleId ? ' selected' : '';
        return `<option value="${escapeHtml(role.role_id)}"${selected}>${escapeHtml(role.name)}</option>`;
    }).join('');
}

function renderOrchestrationPresetOptions(selectedPresetId) {
    if (state.orchestrationPresets.length === 0) {
        return `<option value="">${escapeHtml(t('composer.no_presets'))}</option>`;
    }
    return state.orchestrationPresets.map(preset => {
        const selected = preset.preset_id === selectedPresetId ? ' selected' : '';
        return `<option value="${escapeHtml(preset.preset_id)}"${selected}>${escapeHtml(preset.name)}</option>`;
    }).join('');
}

function renderBooleanOptions(selected) {
    return `
        <option value="true"${selected ? ' selected' : ''}>${escapeHtml(t('settings.triggers.option_enabled'))}</option>
        <option value="false"${selected ? '' : ' selected'}>${escapeHtml(t('settings.triggers.option_disabled'))}</option>
    `;
}

function formatWorkspaceLabel(workspace) {
    const workspaceId = String(workspace?.workspace_id || '').trim();
    const rootPath = String(workspace?.root_path || '').trim();
    if (!rootPath) {
        return workspaceId;
    }
    const parts = rootPath.split(/[\\/]/).filter(Boolean);
    return parts.at(-1) || workspaceId;
}

function syncSessionModeVisibility() {
    const mode = readValue('feishu-session-mode-input') || DEFAULT_SESSION_MODE;
    const normalField = document.getElementById('feishu-normal-role-field');
    const presetField = document.getElementById('feishu-preset-field');
    if (normalField) {
        normalField.style.display = mode === 'normal' ? 'block' : 'none';
    }
    if (presetField) {
        presetField.style.display = mode === 'orchestration' ? 'block' : 'none';
    }
}

function syncThinkingVisibility() {
    const enabled = readBooleanSelect('feishu-trigger-thinking-enabled-input', false);
    const effortField = document.getElementById('feishu-thinking-effort-field');
    if (effortField) {
        effortField.style.display = enabled ? 'block' : 'none';
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
