/**
 * components/projectView.js
 * Renders the main workspace snapshot for a selected project.
 */
import {
    createAutomationProject,
    createGitHubRepoSubscription,
    createGitHubTriggerAccount,
    createGitHubTriggerRule,
    createTrigger,
    deleteAutomationProject,
    deleteGitHubRepoSubscription,
    deleteGitHubTriggerAccount,
    deleteGitHubTriggerRule,
    deleteTrigger,
    deleteWeChatGatewayAccount,
    disableAutomationProject,
    disableGitHubRepoSubscription,
    disableGitHubTriggerAccount,
    disableGitHubTriggerRule,
    disableTrigger,
    disableWeChatGatewayAccount,
    enableGitHubRepoSubscription,
    enableGitHubTriggerAccount,
    enableGitHubTriggerRule,
    enableTrigger,
    enableWeChatGatewayAccount,
    enableAutomationProject,
    fetchAutomationFeishuBindings,
    fetchAutomationProjects,
    fetchAutomationProject,
    fetchAutomationProjectSessions,
    fetchConfigStatus,
    fetchGitHubAccountRepositories,
    fetchGitHubRepoSubscriptions,
    fetchGitHubTriggerAccounts,
    fetchGitHubTriggerRules,
    fetchOrchestrationConfig,
    fetchRoleConfigOptions,
    fetchSshProfiles,
    fetchTriggers,
    fetchWeChatGatewayAccounts,
    fetchWorkspaceDiffFile,
    fetchWorkspaces,
    fetchWorkspaceDiffs,
    fetchWorkspaceSnapshot,
    fetchWorkspaceTree,
    openWorkspaceRoot,
    reloadSkillsConfig,
    runAutomationProject,
    startWeChatGatewayLogin,
    updateWorkspace,
    updateAutomationProject,
    updateGitHubRepoSubscription,
    updateGitHubTriggerAccount,
    updateGitHubTriggerRule,
    updateTrigger,
    updateWeChatGatewayAccount,
    waitWeChatGatewayLogin,
} from '../core/api.js';
import { clearAllPanels } from './agentPanel.js';
import { clearNewSessionDraft } from './newSessionDraft.js';
import { hideRoundNavigator } from './rounds/navigator.js';
import { setSubagentRailExpanded } from './subagentRail.js';
import {
    bindClawHubSettingsHandlers,
    loadClawHubSettingsPanel,
} from './settings/clawhubSettings.js';
import {
    bindGitHubSettingsHandlers,
    loadGitHubSettingsPanel,
    renderGitHubAccessPanelMarkup,
} from './settings/githubSettings.js';
import { state } from '../core/state.js';
import { els } from '../utils/dom.js';
import { t } from '../utils/i18n.js';
import { showConfirmDialog, showFormDialog, showToast } from '../utils/feedback.js';
import { logWarn, sysLog } from '../utils/logger.js';

let currentWorkspace = null;
let currentAutomationProject = null;
let currentProjectViewMode = 'workspace';
let currentFeatureViewId = '';
let currentAutomationProjects = [];
let selectedAutomationHomeProjectId = '';
let currentAutomationHomeDetail = createInitialAutomationHomeDetail();
let currentAutomationFeatureSection = 'schedules';
let currentGitHubFeatureState = createInitialGitHubFeatureState();
let currentGitHubFeatureNodeKey = 'access';
let currentSkillsStatus = null;
let currentGatewayFeatureState = createInitialGatewayFeatureState();
let currentAutomationEditorState = createInitialAutomationEditorState();
let currentSnapshot = null;
let currentSnapshotWorkspaceId = null;
let currentMountName = null;
let currentLoadToken = 0;
let languageBound = false;
let gatewayModalRoot = null;
let automationEditorModalRoot = null;
let selectedTreePath = null;
let currentDiffState = createInitialDiffState();
const currentMountTrees = new Map();
const expandedTreePaths = new Set();
const loadingTreePaths = new Set();
const treeLoadErrors = new Map();
const workspaceViewCache = new Map();
const FEATURE_VIEW_IDS = Object.freeze({
    skills: 'skills',
    automation: 'automation',
    gateway: 'gateway',
});
const FEATURE_CLAWHUB_FIELD_IDS = Object.freeze({
    saveButtonId: 'feature-save-clawhub-token-btn',
    probeButtonId: 'feature-test-clawhub-btn',
    tokenInputId: 'feature-clawhub-token',
    toggleTokenButtonId: 'feature-toggle-clawhub-token-btn',
    statusId: 'feature-clawhub-probe-status',
});
const FEATURE_GITHUB_FIELD_IDS = Object.freeze({
    saveButtonId: 'feature-save-github-btn',
    probeButtonId: 'feature-test-github-btn',
    tokenInputId: 'feature-github-token',
    webhookSaveButtonId: 'feature-save-github-webhook-btn',
    webhookProbeButtonId: 'feature-test-github-webhook-btn',
    webhookBaseUrlInputId: 'feature-github-webhook-base-url',
    callbackPreviewId: 'feature-github-callback-preview',
    tunnelStartButtonId: 'feature-start-github-webhook-tunnel-btn',
    tunnelStopButtonId: 'feature-stop-github-webhook-tunnel-btn',
    tunnelStatusId: 'feature-github-webhook-tunnel-status',
    toggleTokenButtonId: 'feature-toggle-github-token-btn',
    statusId: 'feature-github-probe-status',
    webhookStatusId: 'feature-github-webhook-probe-status',
});
const FEISHU_PLATFORM = 'feishu';
const WECHAT_PLATFORM = 'wechat';
const DEFAULT_TRIGGER_RULE = 'mention_only';
const DEFAULT_SESSION_MODE = 'normal';
const DEFAULT_THINKING_EFFORT = 'medium';
const DEFAULT_AUTOMATION_TIMEZONE = 'Asia/Shanghai';
const THINKING_EFFORT_OPTIONS = ['minimal', 'low', 'medium', 'high'];
const AUTOMATION_SCHEDULE_KINDS = Object.freeze({
    daily: 'daily',
    weekdays: 'weekdays',
    weekly: 'weekly',
    monthly: 'monthly',
    oneShot: 'one_shot',
    unsupported: 'unsupported',
});

function createInitialAutomationHomeDetail() {
    return {
        project: null,
        sessions: [],
        workspace: null,
        feishuBindings: [],
        normalRoles: [],
        orchestrationPresets: [],
    };
}

function createInitialGitHubFeatureState() {
    return {
        accounts: [],
        repos: [],
        rules: [],
        workspaces: [],
    };
}

function createInitialAutomationEditorState() {
    return {
        open: false,
        mode: 'create',
        projectId: '',
        project: null,
        title: '',
        message: '',
        confirmLabel: '',
        workspaces: [],
        feishuBindings: [],
        normalRoles: [],
        orchestrationPresets: [],
        draft: null,
        resolve: null,
        errorMessage: '',
    };
}

function createInitialGatewayFeatureState() {
    return {
        feishuTriggers: [],
        feishuEditingTriggerId: '',
        feishuDraft: null,
        wechatAccounts: [],
        workspaces: [],
        normalRoles: [],
        orchestrationPresets: [],
        wechatLoginRequestId: 0,
        wechatModalOpen: false,
        wechatLoginSession: null,
        wechatStatusMessage: '',
        wechatStatusTone: '',
        wechatConnecting: false,
    };
}

function formatMessage(key, values = {}) {
    return Object.entries(values).reduce(
        (result, [name, value]) => result.replaceAll(`{${name}}`, String(value)),
        t(key),
    );
}

function createFeishuTriggerDraft(trigger = null) {
    const sourceConfig = trigger?.source_config && typeof trigger.source_config === 'object' ? trigger.source_config : {};
    const targetConfig = trigger?.target_config && typeof trigger.target_config === 'object' ? trigger.target_config : {};
    const secretStatus = trigger?.secret_status && typeof trigger.secret_status === 'object' ? trigger.secret_status : {};
    const firstWorkspaceId = String(currentGatewayFeatureState.workspaces[0]?.workspace_id || '').trim();
    const firstRoleId = String(currentGatewayFeatureState.normalRoles[0]?.role_id || '').trim();
    return {
        trigger_id: String(trigger?.trigger_id || '').trim(),
        name: String(trigger?.name || 'feishu-main').trim(),
        display_name: String(trigger?.display_name || '').trim(),
        status: String(trigger?.status || 'enabled').trim() || 'enabled',
        source_config: {
            provider: FEISHU_PLATFORM,
            trigger_rule: String(sourceConfig?.trigger_rule || DEFAULT_TRIGGER_RULE).trim() || DEFAULT_TRIGGER_RULE,
            app_id: String(sourceConfig?.app_id || '').trim(),
            app_name: String(sourceConfig?.app_name || '').trim(),
        },
        target_config: {
            workspace_id: String(targetConfig?.workspace_id || firstWorkspaceId).trim(),
            session_mode: String(targetConfig?.session_mode || DEFAULT_SESSION_MODE).trim() || DEFAULT_SESSION_MODE,
            normal_root_role_id: String(targetConfig?.normal_root_role_id || firstRoleId).trim(),
            orchestration_preset_id: String(targetConfig?.orchestration_preset_id || '').trim(),
            yolo: targetConfig?.yolo !== false,
            thinking: {
                enabled: targetConfig?.thinking?.enabled === true,
                effort: String(targetConfig?.thinking?.effort || DEFAULT_THINKING_EFFORT).trim() || DEFAULT_THINKING_EFFORT,
            },
        },
        secret_config: {},
        secret_status: { ...secretStatus },
        pending_app_secret: '',
    };
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

function renderGatewayWorkspaceOptions(selectedWorkspaceId) {
    if (currentGatewayFeatureState.workspaces.length === 0) {
        return `<option value="">${escapeHtml(t('settings.triggers.no_workspaces'))}</option>`;
    }
    return currentGatewayFeatureState.workspaces.map(workspace => {
        const workspaceId = String(workspace?.workspace_id || '').trim();
        const selected = workspaceId === selectedWorkspaceId ? ' selected' : '';
        return `<option value="${escapeHtml(workspaceId)}"${selected}>${escapeHtml(formatWorkspaceOptionLabel(workspace))}</option>`;
    }).join('');
}

function renderGatewayRoleOptions(selectedRoleId) {
    if (currentGatewayFeatureState.normalRoles.length === 0) {
        return `<option value="">${escapeHtml(t('composer.no_roles'))}</option>`;
    }
    return currentGatewayFeatureState.normalRoles.map(role => {
        const roleId = String(role?.role_id || '').trim();
        const selected = roleId === selectedRoleId ? ' selected' : '';
        return `<option value="${escapeHtml(roleId)}"${selected}>${escapeHtml(String(role?.name || roleId))}</option>`;
    }).join('');
}

function renderGatewayPresetOptions(selectedPresetId) {
    if (currentGatewayFeatureState.orchestrationPresets.length === 0) {
        return `<option value="">${escapeHtml(t('composer.no_presets'))}</option>`;
    }
    return currentGatewayFeatureState.orchestrationPresets.map(preset => {
        const presetId = String(preset?.preset_id || '').trim();
        const selected = presetId === selectedPresetId ? ' selected' : '';
        return `<option value="${escapeHtml(presetId)}"${selected}>${escapeHtml(String(preset?.name || presetId))}</option>`;
    }).join('');
}

function lookupDocumentElement(id) {
    if (!document?.getElementById) {
        return null;
    }
    try {
        return document.getElementById(id);
    } catch {
        return null;
    }
}

function readEditorValue(id) {
    return String(lookupDocumentElement(id)?.value || '').trim();
}

function readEditorChecked(id, fallback = false) {
    const element = lookupDocumentElement(id);
    return typeof element?.checked === 'boolean' ? element.checked : fallback;
}

function syncFeishuDraftFromEditor() {
    const draft = currentGatewayFeatureState.feishuDraft;
    if (!draft) {
        return null;
    }
    const sessionMode = readEditorValue('feishu-session-mode-input') || resolveSessionMode(draft.target_config);
    const thinkingEnabled = readEditorChecked('feishu-trigger-thinking-enabled-input', resolveThinkingEnabled(draft.target_config));
    const nextDraft = {
        ...draft,
        name: readEditorValue('feishu-trigger-name-input') || draft.name,
        display_name: readEditorValue('feishu-display-name-input'),
        status: String(draft.status || '').trim() || 'enabled',
        source_config: {
            ...draft.source_config,
            trigger_rule: readEditorValue('feishu-trigger-rule-input') || resolveRule(draft.source_config),
            app_name: readEditorValue('feishu-app-name-input'),
            app_id: readEditorValue('feishu-app-id-input'),
        },
        target_config: {
            ...draft.target_config,
            workspace_id: readEditorValue('feishu-trigger-workspace-id-input') || String(draft.target_config?.workspace_id || '').trim(),
            session_mode: sessionMode,
            normal_root_role_id: sessionMode === 'normal' ? readEditorValue('feishu-normal-root-role-id-input') : '',
            orchestration_preset_id: sessionMode === 'orchestration' ? readEditorValue('feishu-orchestration-preset-id-input') : '',
            yolo: readEditorChecked('feishu-trigger-yolo-input', resolveYolo(draft.target_config)),
            thinking: {
                enabled: thinkingEnabled,
                effort: thinkingEnabled
                    ? (readEditorValue('feishu-thinking-effort-input') || resolveThinkingEffort(draft.target_config))
                    : DEFAULT_THINKING_EFFORT,
            },
        },
        pending_app_secret: readEditorValue('feishu-app-secret-input'),
    };
    currentGatewayFeatureState = {
        ...currentGatewayFeatureState,
        feishuDraft: nextDraft,
    };
    return nextDraft;
}

function buildFeishuTriggerPayload(draft, { requireSecret = false } = {}) {
    const name = String(draft?.name || '').trim();
    const workspaceId = String(draft?.target_config?.workspace_id || '').trim();
    const appId = String(draft?.source_config?.app_id || '').trim();
    const appName = String(draft?.source_config?.app_name || '').trim();
    const appSecret = String(draft?.pending_app_secret || '').trim();
    const nextSessionMode = resolveSessionMode(draft?.target_config);
    const orchestrationPresetId = resolveOrchestrationPresetId(draft?.target_config);
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
    if (requireSecret && !appSecret) {
        throw new Error(t('settings.triggers.missing_app_secret'));
    }
    if (nextSessionMode === 'orchestration' && !orchestrationPresetId) {
        throw new Error(t('settings.triggers.missing_orchestration_preset_id'));
    }
    const payload = {
        name,
        display_name: String(draft?.display_name || '').trim() || null,
        source_config: {
            provider: FEISHU_PLATFORM,
            trigger_rule: resolveRule(draft?.source_config),
            app_id: appId,
            app_name: appName,
        },
        target_config: {
            workspace_id: workspaceId,
            session_mode: nextSessionMode,
            yolo: resolveYolo(draft?.target_config),
            thinking: {
                enabled: resolveThinkingEnabled(draft?.target_config),
                effort: resolveThinkingEnabled(draft?.target_config) ? resolveThinkingEffort(draft?.target_config) : null,
            },
        },
        enabled: String(draft?.status || '').trim() === 'enabled',
    };
    const normalRootRoleId = resolveNormalRootRoleId(draft?.target_config);
    if (nextSessionMode === 'normal' && normalRootRoleId) {
        payload.target_config.normal_root_role_id = normalRootRoleId;
    }
    if (nextSessionMode === 'orchestration' && orchestrationPresetId) {
        payload.target_config.orchestration_preset_id = orchestrationPresetId;
    }
    if (appSecret) {
        payload.secret_config = { app_secret: appSecret };
    }
    return payload;
}

function renderFeishuEditor() {
    const draft = currentGatewayFeatureState.feishuDraft;
    if (!draft) {
        return '';
    }
    const secretStatus = draft.secret_status && typeof draft.secret_status === 'object' ? draft.secret_status : {};
    const sessionMode = resolveSessionMode(draft.target_config);
    const thinkingEnabled = resolveThinkingEnabled(draft.target_config);
    return `
        <div class="gateway-feishu-editor">
            <div class="role-editor-panel">
                <div class="role-editor-form">
                    <div class="role-editor-sections">
                        <section class="role-editor-section">
                            <h5>${escapeHtml(t('settings.triggers.bot_configuration'))}</h5>
                            <div class="gateway-field-grid gateway-field-grid-2">
                                <div class="form-group">
                                    <label for="feishu-trigger-name-input">${escapeHtml(t('settings.triggers.trigger_name'))}</label>
                                    <input id="feishu-trigger-name-input" value="${escapeHtml(String(draft.name || ''))}">
                                </div>
                                <div class="form-group">
                                    <label for="feishu-display-name-input">${escapeHtml(t('settings.triggers.display_name'))}</label>
                                    <input id="feishu-display-name-input" value="${escapeHtml(String(draft.display_name || ''))}">
                                </div>
                            </div>
                            <div class="gateway-field-grid gateway-field-grid-3 gateway-field-grid-compact">
                                <div class="form-group">
                                    <label for="feishu-app-name-input">${escapeHtml(t('settings.triggers.feishu_app_name'))}</label>
                                    <input id="feishu-app-name-input" placeholder="${escapeHtml(t('settings.triggers.feishu_app_name_placeholder'))}" value="${escapeHtml(String(draft.source_config?.app_name || ''))}">
                                </div>
                                <div class="form-group">
                                    <label for="feishu-app-id-input">${escapeHtml(t('settings.triggers.feishu_app_id'))}</label>
                                    <input id="feishu-app-id-input" placeholder="${escapeHtml(t('settings.triggers.feishu_app_id_placeholder'))}" value="${escapeHtml(String(draft.source_config?.app_id || ''))}">
                                </div>
                                <div class="form-group">
                                    <label for="feishu-app-secret-input">${escapeHtml(t('settings.triggers.feishu_app_secret'))}</label>
                                    <input id="feishu-app-secret-input" type="password" placeholder="${escapeHtml(secretStatus?.app_secret_configured ? t('settings.triggers.secret_keep_placeholder') : t('settings.triggers.feishu_app_secret_placeholder'))}" value="">
                                </div>
                            </div>
                        </section>
                        <section class="role-editor-section">
                            <h5>${escapeHtml(t('settings.triggers.session_configuration'))}</h5>
                            <div class="gateway-session-core-grid">
                                <div class="form-group">
                                    <label for="feishu-trigger-workspace-id-input">${escapeHtml(t('settings.triggers.workspace'))}</label>
                                    <select id="feishu-trigger-workspace-id-input">
                                        ${renderGatewayWorkspaceOptions(String(draft.target_config?.workspace_id || '').trim())}
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
                            <div class="gateway-session-mode-row">
                                <div class="form-group gateway-session-mode-field">
                                    <label for="feishu-session-mode-input">${escapeHtml(t('settings.triggers.mode'))}</label>
                                    <select id="feishu-session-mode-input">
                                        <option value="normal"${sessionMode === 'normal' ? ' selected' : ''}>${escapeHtml(t('composer.mode_normal'))}</option>
                                        <option value="orchestration"${sessionMode === 'orchestration' ? ' selected' : ''}>${escapeHtml(t('composer.mode_orchestration'))}</option>
                                    </select>
                                </div>
                                <div class="form-group gateway-session-mode-detail" id="feishu-normal-role-field"${sessionMode === 'normal' ? '' : ' style="display:none;"'}>
                                    <label for="feishu-normal-root-role-id-input">${escapeHtml(t('settings.triggers.normal_root_role_id'))}</label>
                                    <select id="feishu-normal-root-role-id-input">
                                        ${renderGatewayRoleOptions(resolveNormalRootRoleId(draft.target_config))}
                                    </select>
                                </div>
                                <div class="form-group gateway-session-mode-detail" id="feishu-preset-field"${sessionMode === 'orchestration' ? '' : ' style="display:none;"'}>
                                    <label for="feishu-orchestration-preset-id-input">${escapeHtml(t('settings.triggers.orchestration_preset_id'))}</label>
                                    <select id="feishu-orchestration-preset-id-input">
                                        ${renderGatewayPresetOptions(resolveOrchestrationPresetId(draft.target_config))}
                                    </select>
                                </div>
                            </div>
                            <div class="gateway-toggle-grid">
                                <div class="gateway-setting-panel">
                                    <label class="gateway-setting-toggle-row" for="feishu-trigger-yolo-input">
                                            <span class="gateway-setting-toggle-copy">${escapeHtml(t('settings.triggers.yolo'))}</span>
                                            <input id="feishu-trigger-yolo-input" type="checkbox"${resolveYolo(draft.target_config) ? ' checked' : ''}>
                                            <span class="gateway-editor-toggle-switch" aria-hidden="true">
                                                <span class="gateway-editor-toggle-thumb"></span>
                                            </span>
                                    </label>
                                </div>
                                <div class="gateway-setting-panel gateway-thinking-panel${thinkingEnabled ? ' is-expanded' : ''}" id="feishu-thinking-panel">
                                    <label class="gateway-setting-toggle-row" for="feishu-trigger-thinking-enabled-input">
                                            <span class="gateway-setting-toggle-copy">${escapeHtml(t('settings.triggers.thinking_enabled'))}</span>
                                            <input id="feishu-trigger-thinking-enabled-input" type="checkbox"${thinkingEnabled ? ' checked' : ''}>
                                            <span class="gateway-editor-toggle-switch" aria-hidden="true">
                                                <span class="gateway-editor-toggle-thumb"></span>
                                            </span>
                                    </label>
                                        <div class="gateway-thinking-panel-body" id="feishu-thinking-effort-field"${thinkingEnabled ? '' : ' style="display:none;"'}>
                                            <label class="gateway-thinking-panel-label" for="feishu-thinking-effort-input">${escapeHtml(t('settings.triggers.thinking_effort'))}</label>
                                            <select id="feishu-thinking-effort-input">
                                                ${THINKING_EFFORT_OPTIONS.map(effort => `<option value="${effort}"${resolveThinkingEffort(draft.target_config) === effort ? ' selected' : ''}>${effort}</option>`).join('')}
                                            </select>
                                        </div>
                                </div>
                            </div>
                        </section>
                    </div>
                    <div class="gateway-editor-actions">
                        <button class="secondary-btn gateway-editor-action-btn gateway-editor-cancel-btn" type="button" data-feature-feishu-cancel>${escapeHtml(t('settings.action.cancel'))}</button>
                        <button class="primary-btn gateway-editor-action-btn gateway-editor-save-btn" type="button" data-feature-feishu-save>${escapeHtml(t('settings.action.save'))}</button>
                    </div>
                </div>
            </div>
        </div>
    `;
}

function bindFeishuEditorInputs() {
    if (!currentGatewayFeatureState.feishuDraft) {
        return;
    }
    [
        'feishu-trigger-name-input',
        'feishu-display-name-input',
        'feishu-app-name-input',
        'feishu-app-id-input',
        'feishu-app-secret-input',
        'feishu-trigger-workspace-id-input',
        'feishu-trigger-rule-input',
        'feishu-normal-root-role-id-input',
        'feishu-orchestration-preset-id-input',
        'feishu-thinking-effort-input',
        'feishu-trigger-yolo-input',
    ].forEach(id => {
        const element = lookupDocumentElement(id);
        if (!element) {
            return;
        }
        element.oninput = () => {
            syncFeishuDraftFromEditor();
        };
        element.onchange = () => {
            syncFeishuDraftFromEditor();
        };
    });
    const sessionModeInput = lookupDocumentElement('feishu-session-mode-input');
    if (sessionModeInput) {
        sessionModeInput.onchange = () => {
            syncFeishuDraftFromEditor();
            syncFeishuSessionFieldVisibility();
        };
    }
    const thinkingEnabledInput = lookupDocumentElement('feishu-trigger-thinking-enabled-input');
    if (thinkingEnabledInput) {
        thinkingEnabledInput.onchange = () => {
            syncFeishuDraftFromEditor();
            syncFeishuThinkingEffortVisibility();
        };
    }
    syncFeishuSessionFieldVisibility();
    syncFeishuThinkingEffortVisibility();
}

function syncFeishuSessionFieldVisibility() {
    const mode = readEditorValue('feishu-session-mode-input') || resolveSessionMode(currentGatewayFeatureState.feishuDraft?.target_config);
    const normalField = lookupDocumentElement('feishu-normal-role-field');
    const presetField = lookupDocumentElement('feishu-preset-field');
    if (normalField?.style) {
        normalField.style.display = mode === 'normal' ? '' : 'none';
    }
    if (presetField?.style) {
        presetField.style.display = mode === 'orchestration' ? '' : 'none';
    }
}

function syncFeishuThinkingEffortVisibility() {
    const enabled = readEditorChecked('feishu-trigger-thinking-enabled-input', resolveThinkingEnabled(currentGatewayFeatureState.feishuDraft?.target_config));
    const effortField = lookupDocumentElement('feishu-thinking-effort-field');
    const thinkingPanel = lookupDocumentElement('feishu-thinking-panel');
    if (effortField?.style) {
        effortField.style.display = enabled ? '' : 'none';
    }
    if (thinkingPanel?.classList) {
        if (enabled) {
            thinkingPanel.classList.add('is-expanded');
        } else {
            thinkingPanel.classList.remove('is-expanded');
        }
    }
}


function findWorkspaceById(workspaces, workspaceId) {
    const safeWorkspaceId = String(workspaceId || '').trim();
    return (Array.isArray(workspaces) ? workspaces : []).find(workspace => String(workspace?.workspace_id || '').trim() === safeWorkspaceId) || null;
}

function formatWorkspaceOptionLabel(workspace) {
    const workspaceId = String(workspace?.workspace_id || '').trim();
    const rootPath = String(workspace?.root_path || '').trim();
    if (workspaceId && rootPath) {
        return `${workspaceId} - ${rootPath}`;
    }
    return workspaceId || rootPath;
}

function formatWorkspaceOptionDescription(workspace) {
    const rootPath = String(workspace?.root_path || '').trim();
    return rootPath || t('automation.workspace.help');
}

const AUTOMATION_TIMEZONE_OPTIONS = [
    { value: 'UTC', label: 'UTC' },
    { value: 'Asia/Shanghai', label: 'Asia/Shanghai' },
    { value: 'America/Los_Angeles', label: 'America/Los_Angeles' },
    { value: 'America/New_York', label: 'America/New_York' },
    { value: 'Europe/London', label: 'Europe/London' },
];

function buildFeishuBindingKey(binding) {
    const triggerId = String(binding?.trigger_id || '').trim();
    const tenantKey = String(binding?.tenant_key || '').trim();
    const chatId = String(binding?.chat_id || '').trim();
    const sessionId = String(binding?.session_id || '').trim();
    if (!triggerId || !tenantKey || !chatId || !sessionId) {
        return '';
    }
    return `${triggerId}::${tenantKey}::${chatId}::${sessionId}`;
}

function buildFeishuBindingOptions(bindings) {
    const safeBindings = Array.isArray(bindings) ? bindings : [];
    const options = [
        {
            value: '',
            label: t('sidebar.feishu_delivery_none'),
            description: t('sidebar.feishu_delivery_none_copy'),
        },
    ];
    safeBindings.forEach(binding => {
        const bindingKey = buildFeishuBindingKey(binding);
        if (!bindingKey) {
            return;
        }
        const triggerName = String(binding?.trigger_name || '').trim();
        const sourceLabel = String(binding?.source_label || '').trim();
        const chatType = String(binding?.chat_type || '').trim();
        const sessionTitle = String(binding?.session_title || '').trim();
        options.push({
            value: bindingKey,
            label: sessionTitle || sourceLabel || bindingKey,
            description: [triggerName, chatType].filter(Boolean).join(' - '),
        });
    });
    return options;
}

function resolveFeishuBindingDisplayName(binding, bindings) {
    const bindingKey = buildFeishuBindingKey(binding);
    const candidate = (Array.isArray(bindings) ? bindings : []).find(
        item => buildFeishuBindingKey(item) === bindingKey,
    );
    const sessionTitle = String(candidate?.session_title || '').trim();
    if (sessionTitle) {
        return sessionTitle;
    }
    const sourceLabel = String(binding?.source_label || '').trim();
    if (sourceLabel) {
        return sourceLabel;
    }
    return String(binding?.chat_id || '').trim();
}

function formatAutomationRunLogMessage(result) {
    const sessionId = String(result?.session_id || '').trim();
    if (result?.queued === true) {
        return formatMessage('sidebar.log.queued_bound_session', { session_id: sessionId });
    }
    if (result?.reused_bound_session === true) {
        return formatMessage('sidebar.log.started_bound_session', { session_id: sessionId });
    }
    return formatMessage('sidebar.log.started_automation_run', { session_id: sessionId });
}

async function fetchAutomationSessionConfigDependencies(context) {
    const [roleOptions, orchestrationConfig] = await Promise.all([
        fetchRoleConfigOptions().catch(error => {
            logWarn(
                'frontend.automation.role_options_failed',
                'Failed to fetch automation role options',
                {
                    context,
                    error_message: String(error?.message || error || ''),
                },
            );
            return { normal_mode_roles: [] };
        }),
        fetchOrchestrationConfig().catch(error => {
            logWarn(
                'frontend.automation.orchestration_config_failed',
                'Failed to fetch automation orchestration config',
                {
                    context,
                    error_message: String(error?.message || error || ''),
                },
            );
            return { presets: [] };
        }),
    ]);
    return {
        normalRoles: normalizeRoleOptions(roleOptions),
        orchestrationPresets: normalizeOrchestrationPresets(orchestrationConfig),
    };
}

export async function requestAutomationProjectInput(project = {}, dialogOptions = {}) {
    const [workspaces, feishuBindings, sessionConfigDependencies] = await Promise.all([
        fetchWorkspaces(),
        fetchAutomationFeishuBindings(),
        fetchAutomationSessionConfigDependencies('editor'),
    ]);
    const workspaceList = Array.isArray(workspaces) ? workspaces : [];
    if (workspaceList.length === 0) {
        return null;
    }
    const normalRoles = Array.isArray(sessionConfigDependencies?.normalRoles)
        ? sessionConfigDependencies.normalRoles
        : [];
    const orchestrationPresets = Array.isArray(sessionConfigDependencies?.orchestrationPresets)
        ? sessionConfigDependencies.orchestrationPresets
        : [];
    const isEditing = String(project?.automation_project_id || '').trim().length > 0;
    const draft = createAutomationEditorDraft(
        project,
        workspaceList,
        normalRoles,
        orchestrationPresets,
    );
    const defaultTitle = isEditing ? t('automation.edit.title') : t('sidebar.new_automation_title');
    const defaultMessage = isEditing ? t('automation.edit.message') : t('sidebar.new_automation_message');
    const defaultConfirmLabel = isEditing ? t('automation.edit.save') : t('sidebar.new_automation_create');
    return await new Promise(resolve => {
        currentAutomationEditorState = {
            open: true,
            mode: isEditing ? 'edit' : 'create',
            projectId: String(project?.name || '').trim(),
            project,
            title: String(dialogOptions?.title || defaultTitle).trim() || defaultTitle,
            message: String(dialogOptions?.message || defaultMessage).trim() || defaultMessage,
            confirmLabel: String(dialogOptions?.confirmLabel || defaultConfirmLabel).trim() || defaultConfirmLabel,
            workspaces: workspaceList,
            feishuBindings,
            normalRoles,
            orchestrationPresets,
            draft,
            resolve,
            errorMessage: '',
        };
        renderAutomationEditorModal();
    });
}

function buildAutomationDeliveryEvents(project) {
    const deliveryEvents = Array.isArray(project?.delivery_events) ? project.delivery_events : [];
    return {
        started: deliveryEvents.includes('started'),
        completed: deliveryEvents.includes('completed'),
        failed: deliveryEvents.includes('failed'),
    };
}

function splitTimeValue(value) {
    const match = /^(\d{1,2}):(\d{2})$/.exec(String(value || '').trim());
    if (!match) {
        return null;
    }
    const hour = Number.parseInt(match[1], 10);
    const minute = Number.parseInt(match[2], 10);
    if (Number.isNaN(hour) || Number.isNaN(minute) || hour < 0 || hour > 23 || minute < 0 || minute > 59) {
        return null;
    }
    return {
        hour,
        minute,
        normalized: `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`,
    };
}

function getFormatterParts(date, timezone) {
    try {
        return new Intl.DateTimeFormat('en-CA', {
            timeZone: timezone || DEFAULT_AUTOMATION_TIMEZONE,
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            hourCycle: 'h23',
        }).formatToParts(date).reduce((result, part) => {
            if (part.type !== 'literal') {
                result[part.type] = part.value;
            }
            return result;
        }, {});
    } catch {
        return {};
    }
}

function formatIsoToLocalDate(isoValue, timezone) {
    const date = new Date(String(isoValue || '').trim());
    if (Number.isNaN(date.getTime())) {
        return '';
    }
    const parts = getFormatterParts(date, timezone);
    if (!parts.year || !parts.month || !parts.day) {
        return '';
    }
    return `${parts.year}-${parts.month}-${parts.day}`;
}

function formatIsoToLocalTime(isoValue, timezone) {
    const date = new Date(String(isoValue || '').trim());
    if (Number.isNaN(date.getTime())) {
        return '09:00';
    }
    const parts = getFormatterParts(date, timezone);
    if (!parts.hour || !parts.minute) {
        return '09:00';
    }
    return `${parts.hour}:${parts.minute}`;
}

function createDefaultOneShotDate(timezone) {
    const now = new Date();
    const parts = getFormatterParts(now, timezone);
    const year = Number.parseInt(parts.year || '', 10);
    const month = Number.parseInt(parts.month || '', 10);
    const day = Number.parseInt(parts.day || '', 10);
    if (!year || !month || !day) {
        const fallback = new Date(Date.now() + 24 * 60 * 60 * 1000);
        return fallback.toISOString().slice(0, 10);
    }
    const nextDay = new Date(Date.UTC(year, month - 1, day + 1));
    const nextParts = getFormatterParts(nextDay, timezone);
    if (!nextParts.year || !nextParts.month || !nextParts.day) {
        return nextDay.toISOString().slice(0, 10);
    }
    return `${nextParts.year}-${nextParts.month}-${nextParts.day}`;
}

function zonedDateTimeToIso(dateValue, timeValue, timezone) {
    const dateMatch = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(dateValue || '').trim());
    const timeParts = splitTimeValue(timeValue);
    if (!dateMatch || !timeParts) {
        return '';
    }
    const year = Number.parseInt(dateMatch[1], 10);
    const month = Number.parseInt(dateMatch[2], 10);
    const day = Number.parseInt(dateMatch[3], 10);
    let guessUtc = Date.UTC(year, month - 1, day, timeParts.hour, timeParts.minute);
    for (let index = 0; index < 3; index += 1) {
        const parts = getFormatterParts(new Date(guessUtc), timezone);
        const actualYear = Number.parseInt(parts.year || '', 10);
        const actualMonth = Number.parseInt(parts.month || '', 10);
        const actualDay = Number.parseInt(parts.day || '', 10);
        const actualHour = Number.parseInt(parts.hour || '', 10);
        const actualMinute = Number.parseInt(parts.minute || '', 10);
        if ([actualYear, actualMonth, actualDay, actualHour, actualMinute].some(Number.isNaN)) {
            break;
        }
        const targetMillis = Date.UTC(year, month - 1, day, timeParts.hour, timeParts.minute);
        const actualMillis = Date.UTC(actualYear, actualMonth - 1, actualDay, actualHour, actualMinute);
        const diff = targetMillis - actualMillis;
        if (diff === 0) {
            break;
        }
        guessUtc += diff;
    }
    return new Date(guessUtc).toISOString();
}

function parseAutomationScheduleDraft(project, timezone) {
    const scheduleMode = String(project?.schedule_mode || 'cron').trim() || 'cron';
    const selectedTimezone = String(timezone || DEFAULT_AUTOMATION_TIMEZONE).trim() || DEFAULT_AUTOMATION_TIMEZONE;
    const fallback = {
        kind: AUTOMATION_SCHEDULE_KINDS.daily,
        time: '09:00',
        weekday: '1',
        dayOfMonth: '1',
        runDate: createDefaultOneShotDate(selectedTimezone),
        unsupportedExpression: '',
        requiresReset: false,
    };
    if (scheduleMode === 'one_shot' || scheduleMode === 'one-shot') {
        return {
            ...fallback,
            kind: AUTOMATION_SCHEDULE_KINDS.oneShot,
            time: formatIsoToLocalTime(project?.run_at, selectedTimezone),
            runDate: formatIsoToLocalDate(project?.run_at, selectedTimezone) || createDefaultOneShotDate(selectedTimezone),
        };
    }
    const cron = String(project?.cron_expression || '').trim();
    if (!cron) {
        return fallback;
    }
    const parts = cron.split(/\s+/);
    if (parts.length !== 5) {
        return {
            ...fallback,
            kind: AUTOMATION_SCHEDULE_KINDS.unsupported,
            unsupportedExpression: cron,
            requiresReset: true,
        };
    }
    const [minute, hour, dayOfMonth, month, dayOfWeek] = parts;
    const time = splitTimeValue(`${hour}:${minute}`)?.normalized || '09:00';
    if (month === '*' && dayOfMonth === '*' && dayOfWeek === '*') {
        return { ...fallback, kind: AUTOMATION_SCHEDULE_KINDS.daily, time };
    }
    if (month === '*' && dayOfMonth === '*' && dayOfWeek === '1-5') {
        return { ...fallback, kind: AUTOMATION_SCHEDULE_KINDS.weekdays, time };
    }
    if (month === '*' && dayOfMonth === '*' && /^(0|1|2|3|4|5|6|7)$/.test(dayOfWeek)) {
        return { ...fallback, kind: AUTOMATION_SCHEDULE_KINDS.weekly, time, weekday: dayOfWeek };
    }
    if (month === '*' && /^\d+$/.test(dayOfMonth) && dayOfWeek === '*') {
        return { ...fallback, kind: AUTOMATION_SCHEDULE_KINDS.monthly, time, dayOfMonth };
    }
    return {
        ...fallback,
        kind: AUTOMATION_SCHEDULE_KINDS.unsupported,
        unsupportedExpression: cron,
        requiresReset: true,
    };
}

function createAutomationEditorDraft(project, workspaces, normalRoles = [], orchestrationPresets = []) {
    const workspaceList = Array.isArray(workspaces) ? workspaces : [];
    const firstWorkspaceId = String(workspaceList[0]?.workspace_id || '').trim();
    const runConfig = project?.run_config && typeof project.run_config === 'object' ? project.run_config : {};
    const sessionMode = String(runConfig?.session_mode || DEFAULT_SESSION_MODE).trim() || DEFAULT_SESSION_MODE;
    const defaultNormalRootRoleId = String(normalRoles[0]?.role_id || '').trim();
    const defaultOrchestrationPresetId = String(orchestrationPresets[0]?.preset_id || '').trim();
    const timezone = String(project?.timezone || DEFAULT_AUTOMATION_TIMEZONE).trim() || DEFAULT_AUTOMATION_TIMEZONE;
    const schedule = parseAutomationScheduleDraft(project, timezone);
    const deliveryEvents = buildAutomationDeliveryEvents(project);
    return {
        display_name: String(project?.display_name || project?.name || '').trim(),
        workspace_id: String(project?.workspace_id || firstWorkspaceId).trim(),
        prompt: String(project?.prompt || '').trim(),
        timezone,
        session_mode: sessionMode,
        normal_root_role_id: String(runConfig?.normal_root_role_id || defaultNormalRootRoleId).trim(),
        orchestration_preset_id: String(
            runConfig?.orchestration_preset_id
            || (sessionMode === 'orchestration' ? defaultOrchestrationPresetId : '')
            || '',
        ).trim(),
        execution_mode: String(runConfig?.execution_mode || 'ai').trim() || 'ai',
        yolo: runConfig?.yolo !== false,
        thinking_enabled: runConfig?.thinking?.enabled === true,
        thinking_effort: String(runConfig?.thinking?.effort || DEFAULT_THINKING_EFFORT).trim() || DEFAULT_THINKING_EFFORT,
        delivery_binding_key: buildFeishuBindingKey(project?.delivery_binding),
        delivery_event_started: deliveryEvents.started || !project?.automation_project_id,
        delivery_event_completed: deliveryEvents.completed || !project?.automation_project_id,
        delivery_event_failed: deliveryEvents.failed || !project?.automation_project_id,
        schedule_kind: schedule.kind,
        time_of_day: schedule.time,
        weekly_day: schedule.weekday,
        monthly_day: schedule.dayOfMonth,
        run_date: schedule.runDate,
        unsupported_expression: schedule.unsupportedExpression,
        requires_schedule_reset: schedule.requiresReset,
    };
}

function resolveAutomationScheduleSummary(draft) {
    const time = splitTimeValue(draft?.time_of_day)?.normalized || '09:00';
    if (draft?.schedule_kind === AUTOMATION_SCHEDULE_KINDS.weekdays) {
        return formatMessage('automation.schedule.summary.weekdays', { time });
    }
    if (draft?.schedule_kind === AUTOMATION_SCHEDULE_KINDS.weekly) {
        return formatMessage('automation.schedule.summary.weekly', {
            weekday: formatCronWeekday(draft?.weekly_day || '1'),
            time,
        });
    }
    if (draft?.schedule_kind === AUTOMATION_SCHEDULE_KINDS.monthly) {
        return formatMessage('automation.schedule.summary.monthly', {
            day: String(draft?.monthly_day || '1'),
            time,
        });
    }
    if (draft?.schedule_kind === AUTOMATION_SCHEDULE_KINDS.oneShot) {
        return formatMessage('automation.schedule.summary.one_shot', {
            date: String(draft?.run_date || ''),
            time,
        });
    }
    return formatMessage('automation.schedule.summary.daily', { time });
}

function buildAutomationSchedulePayload(draft) {
    const time = splitTimeValue(draft?.time_of_day);
    if (!time) {
        throw new Error(t('automation.schedule.validation.time'));
    }
    if (draft?.schedule_kind === AUTOMATION_SCHEDULE_KINDS.unsupported) {
        throw new Error(t('automation.schedule.validation.reset_required'));
    }
    if (draft?.schedule_kind === AUTOMATION_SCHEDULE_KINDS.oneShot) {
        const runDate = String(draft?.run_date || '').trim();
        if (!runDate) {
            throw new Error(t('automation.schedule.validation.date'));
        }
        const runAt = zonedDateTimeToIso(runDate, time.normalized, draft?.timezone || DEFAULT_AUTOMATION_TIMEZONE);
        if (!runAt) {
            throw new Error(t('automation.schedule.validation.date'));
        }
        return {
            schedule_mode: 'one_shot',
            cron_expression: null,
            run_at: runAt,
        };
    }
    if (draft?.schedule_kind === AUTOMATION_SCHEDULE_KINDS.weekly) {
        const weekday = String(draft?.weekly_day || '').trim();
        if (!/^(0|1|2|3|4|5|6|7)$/.test(weekday)) {
            throw new Error(t('automation.schedule.validation.weekday'));
        }
        return {
            schedule_mode: 'cron',
            cron_expression: `${time.minute} ${time.hour} * * ${weekday}`,
            run_at: null,
        };
    }
    if (draft?.schedule_kind === AUTOMATION_SCHEDULE_KINDS.monthly) {
        const monthlyDay = Number.parseInt(String(draft?.monthly_day || '').trim(), 10);
        if (Number.isNaN(monthlyDay) || monthlyDay < 1 || monthlyDay > 31) {
            throw new Error(t('automation.schedule.validation.monthly_day'));
        }
        return {
            schedule_mode: 'cron',
            cron_expression: `${time.minute} ${time.hour} ${monthlyDay} * *`,
            run_at: null,
        };
    }
    if (draft?.schedule_kind === AUTOMATION_SCHEDULE_KINDS.weekdays) {
        return {
            schedule_mode: 'cron',
            cron_expression: `${time.minute} ${time.hour} * * 1-5`,
            run_at: null,
        };
    }
    return {
        schedule_mode: 'cron',
        cron_expression: `${time.minute} ${time.hour} * * *`,
        run_at: null,
    };
}

function buildAutomationProjectPayload(draft, feishuBindings, project) {
    const displayName = String(draft?.display_name || '').trim();
    const workspaceId = String(draft?.workspace_id || '').trim();
    const prompt = String(draft?.prompt || '').trim();
    const timezone = String(draft?.timezone || DEFAULT_AUTOMATION_TIMEZONE).trim() || DEFAULT_AUTOMATION_TIMEZONE;
    const sessionMode = String(draft?.session_mode || DEFAULT_SESSION_MODE).trim() || DEFAULT_SESSION_MODE;
    const normalRootRoleId = String(draft?.normal_root_role_id || '').trim();
    const orchestrationPresetId = String(draft?.orchestration_preset_id || '').trim();
    if (!displayName) {
        throw new Error(t('automation.schedule.validation.name'));
    }
    if (!workspaceId) {
        throw new Error(t('automation.schedule.validation.workspace'));
    }
    if (!prompt) {
        throw new Error(t('automation.schedule.validation.prompt'));
    }
    if (sessionMode === 'orchestration' && !orchestrationPresetId) {
        throw new Error(t('settings.triggers.missing_orchestration_preset_id'));
    }
    const schedulePayload = buildAutomationSchedulePayload({ ...draft, timezone });
    const selectedBindingKey = String(draft?.delivery_binding_key || '').trim();
    const selectedBinding = (Array.isArray(feishuBindings) ? feishuBindings : []).find(
        binding => buildFeishuBindingKey(binding) === selectedBindingKey,
    ) || null;
    const nextDeliveryEvents = selectedBinding ? [
        draft?.delivery_event_started === true ? 'started' : null,
        draft?.delivery_event_completed === true ? 'completed' : null,
        draft?.delivery_event_failed === true ? 'failed' : null,
    ].filter(Boolean) : [];
    const slug = displayName.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || String(project?.name || 'automation-project');
    const hasExistingProject = String(project?.automation_project_id || '').trim().length > 0;
    const preservedEnabled = typeof project?.enabled === 'boolean'
        ? project.enabled
        : String(project?.status || 'enabled').trim() !== 'disabled';
    return {
        name: slug,
        display_name: displayName,
        workspace_id: workspaceId,
        prompt,
        timezone,
        enabled: hasExistingProject ? preservedEnabled : true,
        run_config: {
            session_mode: sessionMode,
            normal_root_role_id: sessionMode === 'normal' ? (normalRootRoleId || null) : null,
            orchestration_preset_id: sessionMode === 'orchestration' ? orchestrationPresetId : null,
            execution_mode: String(draft?.execution_mode || 'ai').trim() || 'ai',
            yolo: draft?.yolo !== false,
            thinking: {
                enabled: draft?.thinking_enabled === true,
                effort: draft?.thinking_enabled === true
                    ? (String(draft?.thinking_effort || DEFAULT_THINKING_EFFORT).trim() || DEFAULT_THINKING_EFFORT)
                    : null,
            },
        },
        ...schedulePayload,
        delivery_binding: selectedBinding ? {
            provider: 'feishu',
            trigger_id: String(selectedBinding.trigger_id || '').trim(),
            tenant_key: String(selectedBinding.tenant_key || '').trim(),
            chat_id: String(selectedBinding.chat_id || '').trim(),
            session_id: String(selectedBinding.session_id || '').trim(),
            chat_type: String(selectedBinding.chat_type || '').trim(),
            source_label: String(selectedBinding.source_label || '').trim(),
        } : null,
        delivery_events: nextDeliveryEvents,
    };
}

function lookupAutomationEditorElement(id) {
    if (!document?.getElementById) {
        return null;
    }
    try {
        return document.getElementById(id);
    } catch {
        return null;
    }
}

function readAutomationEditorValue(id, fallback = '') {
    const element = lookupAutomationEditorElement(id);
    return element?.value != null ? String(element.value).trim() : fallback;
}

function readAutomationEditorChecked(id, fallback = false) {
    const element = lookupAutomationEditorElement(id);
    return typeof element?.checked === 'boolean' ? element.checked : fallback;
}

function syncAutomationEditorDraftFromDom() {
    if (!currentAutomationEditorState.draft) {
        return null;
    }
    const nextDraft = {
        ...currentAutomationEditorState.draft,
        display_name: readAutomationEditorValue('automation-editor-display-name-input', currentAutomationEditorState.draft.display_name),
        workspace_id: readAutomationEditorValue('automation-editor-workspace-id-input', currentAutomationEditorState.draft.workspace_id),
        prompt: readAutomationEditorValue('automation-editor-prompt-input', currentAutomationEditorState.draft.prompt),
        timezone: readAutomationEditorValue('automation-editor-timezone-input', currentAutomationEditorState.draft.timezone),
        session_mode: readAutomationEditorValue('automation-editor-session-mode-input', currentAutomationEditorState.draft.session_mode),
        normal_root_role_id: readAutomationEditorValue('automation-editor-normal-root-role-id-input', currentAutomationEditorState.draft.normal_root_role_id),
        orchestration_preset_id: readAutomationEditorValue('automation-editor-orchestration-preset-id-input', currentAutomationEditorState.draft.orchestration_preset_id),
        delivery_binding_key: readAutomationEditorValue('automation-editor-delivery-binding-input', currentAutomationEditorState.draft.delivery_binding_key),
        delivery_event_started: readAutomationEditorChecked('automation-editor-delivery-started-input', currentAutomationEditorState.draft.delivery_event_started),
        delivery_event_completed: readAutomationEditorChecked('automation-editor-delivery-completed-input', currentAutomationEditorState.draft.delivery_event_completed),
        delivery_event_failed: readAutomationEditorChecked('automation-editor-delivery-failed-input', currentAutomationEditorState.draft.delivery_event_failed),
        schedule_kind: readAutomationEditorValue('automation-editor-schedule-kind-input', currentAutomationEditorState.draft.schedule_kind),
        time_of_day: readAutomationEditorValue('automation-editor-time-input', currentAutomationEditorState.draft.time_of_day),
        weekly_day: readAutomationEditorValue('automation-editor-weekday-input', currentAutomationEditorState.draft.weekly_day),
        monthly_day: readAutomationEditorValue('automation-editor-monthly-day-input', currentAutomationEditorState.draft.monthly_day),
        run_date: readAutomationEditorValue('automation-editor-run-date-input', currentAutomationEditorState.draft.run_date),
    };
    currentAutomationEditorState = {
        ...currentAutomationEditorState,
        draft: nextDraft,
    };
    return nextDraft;
}

function renderAutomationEditorFieldOptions(options, selectedValue) {
    return (Array.isArray(options) ? options : []).map(option => {
        const value = String(option?.value || '').trim();
        const selected = value === String(selectedValue || '').trim() ? ' selected' : '';
        return `<option value="${escapeHtml(value)}"${selected}>${escapeHtml(String(option?.label || value))}</option>`;
    }).join('');
}

function ensureAutomationEditorSelectedOption(options, selectedValue) {
    const normalizedSelectedValue = String(selectedValue || '').trim();
    const normalizedOptions = Array.isArray(options) ? options : [];
    if (!normalizedSelectedValue) {
        return normalizedOptions;
    }
    const hasSelectedOption = normalizedOptions.some(
        option => String(option?.value || '').trim() === normalizedSelectedValue,
    );
    if (hasSelectedOption) {
        return normalizedOptions;
    }
    return [
        ...normalizedOptions,
        {
            value: normalizedSelectedValue,
            label: normalizedSelectedValue,
            description: normalizedSelectedValue,
        },
    ];
}

function renderAutomationEditorWeekdayOptions(selectedValue) {
    return renderAutomationEditorFieldOptions([
        { value: '1', label: t('automation.cron.weekday.mon') },
        { value: '2', label: t('automation.cron.weekday.tue') },
        { value: '3', label: t('automation.cron.weekday.wed') },
        { value: '4', label: t('automation.cron.weekday.thu') },
        { value: '5', label: t('automation.cron.weekday.fri') },
        { value: '6', label: t('automation.cron.weekday.sat') },
        { value: '0', label: t('automation.cron.weekday.sun') },
    ], selectedValue);
}

function resolveAutomationSessionModeOptions() {
    return [
        { value: 'normal', label: t('composer.mode_normal') },
        { value: 'orchestration', label: t('composer.mode_orchestration') },
    ];
}

function renderAutomationEditorScheduleDetail(draft) {
    const scheduleKind = String(draft?.schedule_kind || AUTOMATION_SCHEDULE_KINDS.daily).trim();
    if (scheduleKind === AUTOMATION_SCHEDULE_KINDS.weekly) {
        return `
            <label class="automation-editor-field">
                <span>${escapeHtml(t('automation.schedule.weekday'))}</span>
                <select id="automation-editor-weekday-input" data-automation-editor-weekday>
                    ${renderAutomationEditorWeekdayOptions(draft?.weekly_day || '1')}
                </select>
            </label>
        `;
    }
    if (scheduleKind === AUTOMATION_SCHEDULE_KINDS.monthly) {
        return `
            <label class="automation-editor-field">
                <span>${escapeHtml(t('automation.schedule.monthly_day'))}</span>
                <input id="automation-editor-monthly-day-input" data-automation-editor-monthly-day type="number" min="1" max="31" value="${escapeHtml(String(draft?.monthly_day || '1'))}">
            </label>
        `;
    }
    if (scheduleKind === AUTOMATION_SCHEDULE_KINDS.oneShot) {
        return `
            <label class="automation-editor-field">
                <span>${escapeHtml(t('automation.schedule.run_date'))}</span>
                <input id="automation-editor-run-date-input" data-automation-editor-run-date type="date" value="${escapeHtml(String(draft?.run_date || ''))}">
            </label>
        `;
    }
    return '';
}

function ensureAutomationEditorModalRoot() {
    if (!document?.body) {
        return null;
    }
    if (!automationEditorModalRoot) {
        try {
            automationEditorModalRoot = document.getElementById('automation-editor-modal-root');
        } catch {
            automationEditorModalRoot = null;
        }
    }
    if (!automationEditorModalRoot && typeof document.createElement === 'function') {
        automationEditorModalRoot = document.createElement('div');
        automationEditorModalRoot.id = 'automation-editor-modal-root';
        automationEditorModalRoot.className = 'gateway-feature-modal-root automation-editor-modal-root';
        if (typeof document.body.appendChild === 'function') {
            document.body.appendChild(automationEditorModalRoot);
        }
    }
    return automationEditorModalRoot;
}

function renderAutomationEditorModal() {
    const root = ensureAutomationEditorModalRoot();
    if (!root) {
        return;
    }
    if (currentAutomationEditorState.open !== true || !currentAutomationEditorState.draft) {
        root.innerHTML = '';
        return;
    }
    const draft = currentAutomationEditorState.draft;
    const bindingOptions = buildFeishuBindingOptions(currentAutomationEditorState.feishuBindings);
    const workspaceOptions = (Array.isArray(currentAutomationEditorState.workspaces) ? currentAutomationEditorState.workspaces : []).map(workspace => ({
        value: String(workspace?.workspace_id || '').trim(),
        label: formatWorkspaceOptionLabel(workspace),
    })).filter(option => option.value);
    const sessionMode = String(draft.session_mode || DEFAULT_SESSION_MODE).trim() || DEFAULT_SESSION_MODE;
    const normalRoleOptions = ensureAutomationEditorSelectedOption(
        resolveRoleOptionsForForms(currentAutomationEditorState.normalRoles),
        draft.normal_root_role_id,
    );
    const orchestrationPresetOptions = ensureAutomationEditorSelectedOption(
        resolvePresetOptionsForForms(currentAutomationEditorState.orchestrationPresets),
        draft.orchestration_preset_id,
    );
    const bindingSelected = String(draft.delivery_binding_key || '').trim().length > 0;
    const scheduleLocked = draft.requires_schedule_reset === true && draft.schedule_kind === AUTOMATION_SCHEDULE_KINDS.unsupported;
    root.innerHTML = `
        <div class="modal gateway-feature-modal automation-editor-modal" data-automation-editor-modal>
            <div class="modal-content gateway-feature-modal-content automation-editor-modal-content" role="dialog" aria-modal="true" aria-labelledby="automation-editor-modal-title">
                <div class="modal-header gateway-feature-modal-header automation-editor-modal-header">
                    <div class="gateway-feature-modal-heading automation-editor-modal-heading">
                        <h3 id="automation-editor-modal-title">${escapeHtml(currentAutomationEditorState.title)}</h3>
                        <p>${escapeHtml(currentAutomationEditorState.message)}</p>
                    </div>
                    <button class="icon-btn" type="button" aria-label="${escapeHtml(t('settings.action.cancel'))}" data-automation-editor-close>
                        <span aria-hidden="true">&times;</span>
                    </button>
                </div>
                <div class="gateway-feature-modal-body automation-editor-modal-body">
                    ${currentAutomationEditorState.errorMessage
                        ? `<div class="feature-inline-status is-danger">${escapeHtml(currentAutomationEditorState.errorMessage)}</div>`
                        : ''
                    }
                    ${scheduleLocked
                        ? `<div class="feature-inline-status is-warning">${escapeHtml(formatMessage('automation.schedule.unsupported_copy', { expression: draft.unsupported_expression || t('automation.detail.not_scheduled') }))}</div>`
                        : ''
                    }
                    <div class="automation-editor-panel">
                        <section class="automation-editor-block">
                            <div class="automation-editor-section-head">
                                <h4>${escapeHtml(t('automation.edit.section.basic'))}</h4>
                            </div>
                            <div class="automation-editor-grid automation-editor-grid-2">
                                <label class="automation-editor-field">
                                    <span>${escapeHtml(t('automation.field.project_name'))}</span>
                                    <input id="automation-editor-display-name-input" data-automation-editor-display-name type="text" placeholder="Daily Briefing" value="${escapeHtml(draft.display_name)}">
                                </label>
                                <label class="automation-editor-field">
                                    <span>${escapeHtml(t('automation.field.workspace'))}</span>
                                    <select id="automation-editor-workspace-id-input" data-automation-editor-workspace>
                                        ${renderAutomationEditorFieldOptions(workspaceOptions, draft.workspace_id)}
                                    </select>
                                </label>
                            </div>
                            <label class="automation-editor-field automation-editor-field-prompt">
                                <span>${escapeHtml(t('automation.detail.prompt'))}</span>
                                <textarea id="automation-editor-prompt-input" data-automation-editor-prompt>${escapeHtml(draft.prompt)}</textarea>
                            </label>
                        </section>
                        <section class="automation-editor-block">
                            <div class="automation-editor-section-head">
                                <h4>${escapeHtml(t('automation.detail.schedule'))}</h4>
                            </div>
                            <div class="automation-editor-grid automation-editor-grid-3">
                                <label class="automation-editor-field">
                                    <span>${escapeHtml(t('automation.schedule.kind'))}</span>
                                    <select id="automation-editor-schedule-kind-input" data-automation-editor-schedule-kind>
                                        <option value="${escapeHtml(AUTOMATION_SCHEDULE_KINDS.unsupported)}"${draft.schedule_kind === AUTOMATION_SCHEDULE_KINDS.unsupported ? ' selected' : ''}>${escapeHtml(t('automation.schedule.choose'))}</option>
                                        <option value="${escapeHtml(AUTOMATION_SCHEDULE_KINDS.daily)}"${draft.schedule_kind === AUTOMATION_SCHEDULE_KINDS.daily ? ' selected' : ''}>${escapeHtml(t('automation.schedule.daily'))}</option>
                                        <option value="${escapeHtml(AUTOMATION_SCHEDULE_KINDS.weekdays)}"${draft.schedule_kind === AUTOMATION_SCHEDULE_KINDS.weekdays ? ' selected' : ''}>${escapeHtml(t('automation.schedule.weekdays'))}</option>
                                        <option value="${escapeHtml(AUTOMATION_SCHEDULE_KINDS.weekly)}"${draft.schedule_kind === AUTOMATION_SCHEDULE_KINDS.weekly ? ' selected' : ''}>${escapeHtml(t('automation.schedule.weekly'))}</option>
                                        <option value="${escapeHtml(AUTOMATION_SCHEDULE_KINDS.monthly)}"${draft.schedule_kind === AUTOMATION_SCHEDULE_KINDS.monthly ? ' selected' : ''}>${escapeHtml(t('automation.schedule.monthly'))}</option>
                                        <option value="${escapeHtml(AUTOMATION_SCHEDULE_KINDS.oneShot)}"${draft.schedule_kind === AUTOMATION_SCHEDULE_KINDS.oneShot ? ' selected' : ''}>${escapeHtml(t('automation.schedule.one_shot'))}</option>
                                    </select>
                                </label>
                                <label class="automation-editor-field">
                                    <span>${escapeHtml(t('automation.schedule.time'))}</span>
                                    <input id="automation-editor-time-input" data-automation-editor-time type="time" value="${escapeHtml(String(draft.time_of_day || '09:00'))}">
                                </label>
                                <label class="automation-editor-field">
                                    <span>${escapeHtml(t('automation.detail.timezone'))}</span>
                                    <select id="automation-editor-timezone-input" data-automation-editor-timezone>
                                        ${renderAutomationEditorFieldOptions(AUTOMATION_TIMEZONE_OPTIONS, draft.timezone)}
                                    </select>
                                </label>
                            </div>
                            ${renderAutomationEditorScheduleDetail(draft)
                                ? `<div class="automation-editor-grid automation-editor-grid-2">${renderAutomationEditorScheduleDetail(draft)}</div>`
                                : ''
                            }
                        </section>
                        <section class="automation-editor-block">
                            <div class="automation-editor-section-head">
                                <h4>${escapeHtml(t('settings.triggers.session_configuration'))}</h4>
                            </div>
                            <div class="automation-editor-grid automation-editor-grid-2">
                                <label class="automation-editor-field">
                                    <span>${escapeHtml(t('settings.triggers.mode'))}</span>
                                    <select id="automation-editor-session-mode-input" data-automation-editor-session-mode>
                                        ${renderAutomationEditorFieldOptions(resolveAutomationSessionModeOptions(), sessionMode)}
                                    </select>
                                </label>
                                ${sessionMode === 'normal' ? `
                                    <label class="automation-editor-field">
                                        <span>${escapeHtml(t('settings.triggers.normal_root_role_id'))}</span>
                                        <select id="automation-editor-normal-root-role-id-input" data-automation-editor-normal-root-role-id>
                                            ${renderAutomationEditorFieldOptions(normalRoleOptions, draft.normal_root_role_id)}
                                        </select>
                                    </label>
                                ` : `
                                    <label class="automation-editor-field">
                                        <span>${escapeHtml(t('settings.triggers.orchestration_preset_id'))}</span>
                                        <select id="automation-editor-orchestration-preset-id-input" data-automation-editor-orchestration-preset-id>
                                            ${renderAutomationEditorFieldOptions(orchestrationPresetOptions, draft.orchestration_preset_id)}
                                        </select>
                                    </label>
                                `}
                            </div>
                        </section>
                        <section class="automation-editor-block">
                            <div class="automation-editor-section-head">
                                <h4>${escapeHtml(t('automation.edit.section.delivery'))}</h4>
                            </div>
                            <div class="automation-editor-grid automation-editor-grid-1">
                                <label class="automation-editor-field">
                                    <span>${escapeHtml(t('sidebar.feishu_chat'))}</span>
                                    <select id="automation-editor-delivery-binding-input" data-automation-editor-binding>
                                        ${renderAutomationEditorFieldOptions(bindingOptions, draft.delivery_binding_key)}
                                    </select>
                                </label>
                            </div>
                            ${bindingSelected ? `
                                <div class="automation-editor-toggle-grid">
                                    <label class="automation-editor-compact-toggle">
                                        <input id="automation-editor-delivery-started-input" data-automation-editor-delivery-started type="checkbox" ${draft.delivery_event_started ? 'checked' : ''}>
                                        <span>${escapeHtml(t('sidebar.notify_on_start'))}</span>
                                    </label>
                                    <label class="automation-editor-compact-toggle">
                                        <input id="automation-editor-delivery-completed-input" data-automation-editor-delivery-completed type="checkbox" ${draft.delivery_event_completed ? 'checked' : ''}>
                                        <span>${escapeHtml(t('sidebar.notify_on_completion'))}</span>
                                    </label>
                                    <label class="automation-editor-compact-toggle">
                                        <input id="automation-editor-delivery-failed-input" data-automation-editor-delivery-failed type="checkbox" ${draft.delivery_event_failed ? 'checked' : ''}>
                                        <span>${escapeHtml(t('sidebar.notify_on_failure'))}</span>
                                    </label>
                                </div>
                            ` : ''}
                        </section>
                    </div>
                </div>
                <div class="gateway-connect-modal-actions automation-editor-actions">
                    <button class="secondary-btn" type="button" data-automation-editor-cancel>${escapeHtml(t('settings.action.cancel'))}</button>
                    <button class="primary-btn" type="button" data-automation-editor-save>${escapeHtml(currentAutomationEditorState.confirmLabel)}</button>
                </div>
            </div>
        </div>
    `;
    bindAutomationEditorModal();
}

function settleAutomationEditor(result) {
    const resolve = currentAutomationEditorState.resolve;
    currentAutomationEditorState = createInitialAutomationEditorState();
    renderAutomationEditorModal();
    if (typeof resolve === 'function') {
        resolve(result);
    }
}

function bindAutomationEditorModal() {
    const root = ensureAutomationEditorModalRoot();
    if (!root) {
        return;
    }
    root.querySelectorAll('[data-automation-editor-close],[data-automation-editor-cancel]').forEach(button => {
        button.addEventListener('click', () => {
            settleAutomationEditor(null);
        });
    });
    root.querySelector('[data-automation-editor-save]')?.addEventListener('click', () => {
        try {
            const draft = syncAutomationEditorDraftFromDom();
            const payload = buildAutomationProjectPayload(
                draft,
                currentAutomationEditorState.feishuBindings,
                currentAutomationEditorState.project || { name: currentAutomationEditorState.projectId },
            );
            settleAutomationEditor(payload);
        } catch (error) {
            currentAutomationEditorState = {
                ...currentAutomationEditorState,
                errorMessage: String(error?.message || error || ''),
            };
            renderAutomationEditorModal();
        }
    });
    root.querySelector('[data-automation-editor-schedule-kind]')?.addEventListener('change', event => {
        const draft = syncAutomationEditorDraftFromDom();
        currentAutomationEditorState = {
            ...currentAutomationEditorState,
            errorMessage: '',
            draft: {
                ...draft,
                schedule_kind: String(event?.target?.value || AUTOMATION_SCHEDULE_KINDS.daily).trim() || AUTOMATION_SCHEDULE_KINDS.daily,
                requires_schedule_reset: false,
            },
        };
        renderAutomationEditorModal();
    });
    root.querySelector('[data-automation-editor-session-mode]')?.addEventListener('change', event => {
        const draft = syncAutomationEditorDraftFromDom();
        const nextSessionMode = String(event?.target?.value || DEFAULT_SESSION_MODE).trim() || DEFAULT_SESSION_MODE;
        currentAutomationEditorState = {
            ...currentAutomationEditorState,
            errorMessage: '',
            draft: {
                ...draft,
                session_mode: nextSessionMode,
            },
        };
        renderAutomationEditorModal();
    });
    root.querySelector('[data-automation-editor-binding]')?.addEventListener('change', event => {
        const draft = syncAutomationEditorDraftFromDom();
        const bindingSelected = String(event?.target?.value || '').trim().length > 0;
        currentAutomationEditorState = {
            ...currentAutomationEditorState,
            errorMessage: '',
            draft: {
                ...draft,
                delivery_binding_key: String(event?.target?.value || '').trim(),
                delivery_event_started: bindingSelected ? draft.delivery_event_started : false,
                delivery_event_completed: bindingSelected ? draft.delivery_event_completed : false,
                delivery_event_failed: bindingSelected ? draft.delivery_event_failed : false,
            },
        };
        renderAutomationEditorModal();
    });
}

function normalizeFeishuTriggers(payload) {
    const rows = Array.isArray(payload) ? payload : [];
    return rows
        .filter(trigger => String(trigger?.source_config?.provider || '').trim().toLowerCase() === FEISHU_PLATFORM)
        .map(trigger => ({
            trigger_id: String(trigger?.trigger_id || '').trim(),
            name: String(trigger?.name || '').trim(),
            display_name: String(trigger?.display_name || trigger?.name || '').trim(),
            status: String(trigger?.status || 'disabled').trim() || 'disabled',
            source_config: trigger?.source_config && typeof trigger.source_config === 'object' ? { ...trigger.source_config } : {},
            target_config: trigger?.target_config && typeof trigger.target_config === 'object' ? { ...trigger.target_config } : {},
            secret_config: trigger?.secret_config && typeof trigger.secret_config === 'object' ? { ...trigger.secret_config } : {},
            secret_status: trigger?.secret_status && typeof trigger.secret_status === 'object' ? { ...trigger.secret_status } : {},
        }))
        .filter(trigger => trigger.trigger_id);
}

function normalizeWeChatAccounts(payload) {
    const rows = Array.isArray(payload) ? payload : [];
    return rows
        .map(account => ({
            account_id: String(account?.account_id || '').trim(),
            display_name: String(account?.display_name || account?.account_id || '').trim(),
            base_url: String(account?.base_url || '').trim(),
            cdn_base_url: String(account?.cdn_base_url || '').trim(),
            route_tag: account?.route_tag == null ? '' : String(account.route_tag).trim(),
            status: String(account?.status || 'disabled').trim() || 'disabled',
            workspace_id: String(account?.workspace_id || '').trim(),
            session_mode: String(account?.session_mode || DEFAULT_SESSION_MODE).trim() || DEFAULT_SESSION_MODE,
            normal_root_role_id: String(account?.normal_root_role_id || '').trim(),
            orchestration_preset_id: String(account?.orchestration_preset_id || '').trim(),
            yolo: account?.yolo !== false,
            thinking: account?.thinking && typeof account.thinking === 'object'
                ? { ...account.thinking }
                : { enabled: false, effort: null },
            running: account?.running === true,
            last_error: String(account?.last_error || '').trim(),
        }))
        .filter(account => account.account_id);
}

function normalizeGatewayWorkspaces(payload) {
    return (Array.isArray(payload) ? payload : [])
        .map(workspace => ({
            workspace_id: String(workspace?.workspace_id || '').trim(),
            root_path: String(workspace?.root_path || '').trim(),
        }))
        .filter(workspace => workspace.workspace_id);
}

function normalizeRoleOptions(payload) {
    return (Array.isArray(payload?.normal_mode_roles) ? payload.normal_mode_roles : [])
        .map(role => ({
            role_id: String(role?.role_id || '').trim(),
            name: String(role?.name || role?.role_id || '').trim(),
        }))
        .filter(role => role.role_id);
}

function normalizeOrchestrationPresets(payload) {
    return (Array.isArray(payload?.presets) ? payload.presets : [])
        .map(preset => ({
            preset_id: String(preset?.preset_id || '').trim(),
            name: String(preset?.name || preset?.preset_id || '').trim(),
        }))
        .filter(preset => preset.preset_id);
}

function resolveGatewayFeatureSummary(featureState) {
    const feishuCount = Array.isArray(featureState?.feishuTriggers) ? featureState.feishuTriggers.length : 0;
    const wechatCount = Array.isArray(featureState?.wechatAccounts) ? featureState.wechatAccounts.length : 0;
    return formatMessage('feature.gateway.summary', {
        feishu: feishuCount,
        wechat: wechatCount,
    });
}

function resolveSkillsSummary(status) {
    const count = Array.isArray(status?.skills?.skills) ? status.skills.skills.length : 0;
    return formatMessage('feature.skills.summary', { count });
}

function resolveAutomationSummary(projects) {
    return formatMessage('feature.automation.summary', {
        count: Array.isArray(projects) ? projects.length : 0,
    });
}

function resolveSkillScopeLabel(scope) {
    const normalizedScope = String(scope || '').trim().toLowerCase();
    if (normalizedScope === 'builtin') {
        return t('feature.skills.scope_builtin');
    }
    if (
        normalizedScope === 'user_relay_teams'
        || normalizedScope === 'user_agents'
        || normalizedScope === 'project_relay_teams'
        || normalizedScope === 'project_agents'
    ) {
        return t('feature.skills.scope_app');
    }
    return t('feature.skills.scope_unknown');
}

function resolveWorkspaceOptionValues(workspaces) {
    return (Array.isArray(workspaces) ? workspaces : []).map(workspace => ({
        value: String(workspace?.workspace_id || '').trim(),
        label: formatWorkspaceOptionLabel(workspace),
        description: formatWorkspaceOptionDescription(workspace),
    })).filter(option => option.value);
}

function resolveRoleOptionsForForms(roles) {
    return [
        {
            value: '',
            label: t('composer.no_roles'),
            description: '',
        },
        ...(Array.isArray(roles) ? roles : []).map(role => ({
            value: String(role?.role_id || '').trim(),
            label: String(role?.name || role?.role_id || '').trim(),
            description: String(role?.role_id || '').trim(),
        })).filter(option => option.value),
    ];
}

function resolvePresetOptionsForForms(presets) {
    return [
        {
            value: '',
            label: t('composer.no_presets'),
            description: '',
        },
        ...(Array.isArray(presets) ? presets : []).map(preset => ({
            value: String(preset?.preset_id || '').trim(),
            label: String(preset?.name || preset?.preset_id || '').trim(),
            description: String(preset?.preset_id || '').trim(),
        })).filter(option => option.value),
    ];
}

function resolveAutomationRoleDisplayName(roleId, roles) {
    const normalizedRoleId = String(roleId || '').trim();
    if (!normalizedRoleId) {
        return t('automation.detail.none');
    }
    const role = (Array.isArray(roles) ? roles : []).find(
        item => String(item?.role_id || '').trim() === normalizedRoleId,
    );
    return String(role?.name || normalizedRoleId).trim() || normalizedRoleId;
}

function resolveAutomationPresetDisplayName(presetId, presets) {
    const normalizedPresetId = String(presetId || '').trim();
    if (!normalizedPresetId) {
        return t('automation.detail.none');
    }
    const preset = (Array.isArray(presets) ? presets : []).find(
        item => String(item?.preset_id || '').trim() === normalizedPresetId,
    );
    return String(preset?.name || normalizedPresetId).trim() || normalizedPresetId;
}

function renderFeatureStatusPill(label, tone = 'neutral') {
    return `<span class="feature-status-pill is-${escapeHtml(tone)}">${escapeHtml(label)}</span>`;
}

function renderFeatureEmptyState(title, copy, action = '') {
    return `
        <div class="feature-empty-state">
            <h4>${escapeHtml(title)}</h4>
            <p>${escapeHtml(copy)}</p>
            ${action}
        </div>
    `;
}

function openFeatureShell(featureId) {
    cacheProjectViewState();
    currentProjectViewMode = 'feature';
    currentFeatureViewId = featureId;
    state.currentFeatureViewId = featureId;
    currentWorkspace = null;
    currentAutomationProject = null;
    currentSnapshot = null;
    currentSnapshotWorkspaceId = null;
    selectedTreePath = null;
    currentDiffState = createInitialDiffState();
    state.currentMainView = 'project';
    state.currentProjectViewWorkspaceId = `feature:${featureId}`;
    state.currentWorkspaceId = null;
    state.currentSessionId = null;
    clearNewSessionDraft();
    clearAllPanels();
    hideRoundNavigator();
    setSubagentRailExpanded(false);
    setProjectViewVisible(true);
}

function renderFeatureLoadingState(title, summary) {
    renderToolbar(null, {
        title,
        mode: 'feature',
        summary,
    });
    if (els.projectViewContent) {
        els.projectViewContent.innerHTML = renderInlineState(summary || t('workspace_view.loading'));
    }
}

async function loadAutomationHomeDetail(projectId) {
    const normalizedProjectId = String(projectId || '').trim();
    if (!normalizedProjectId) {
        currentAutomationHomeDetail = createInitialAutomationHomeDetail();
        currentAutomationProject = null;
        return;
    }
    const [project, sessions, workspaces, feishuBindings, sessionConfigDependencies] = await Promise.all([
        fetchAutomationProject(normalizedProjectId),
        fetchAutomationProjectSessions(normalizedProjectId),
        fetchWorkspaces(),
        fetchAutomationFeishuBindings(),
        fetchAutomationSessionConfigDependencies('detail'),
    ]);
    currentAutomationHomeDetail = {
        project,
        sessions: Array.isArray(sessions) ? sessions : [],
        workspace: findWorkspaceById(workspaces, project?.workspace_id),
        feishuBindings: Array.isArray(feishuBindings) ? feishuBindings : [],
        normalRoles: Array.isArray(sessionConfigDependencies?.normalRoles)
            ? sessionConfigDependencies.normalRoles
            : [],
        orchestrationPresets: Array.isArray(sessionConfigDependencies?.orchestrationPresets)
            ? sessionConfigDependencies.orchestrationPresets
            : [],
    };
    currentAutomationProject = project;
}

async function loadGitHubFeatureState() {
    const [accounts, repos, rules, workspaces] = await Promise.all([
        fetchGitHubTriggerAccounts(),
        fetchGitHubRepoSubscriptions(),
        fetchGitHubTriggerRules(),
        fetchWorkspaces(),
    ]);
    currentGitHubFeatureState = {
        accounts: Array.isArray(accounts) ? accounts : [],
        repos: Array.isArray(repos) ? repos : [],
        rules: Array.isArray(rules) ? rules : [],
        workspaces: Array.isArray(workspaces) ? workspaces : [],
    };
    currentGitHubFeatureNodeKey = resolveGitHubFeatureNodeKey(
        currentGitHubFeatureNodeKey,
    );
}

function parseGitHubFeatureNodeKey(nodeKey) {
    const normalizedNodeKey = String(nodeKey || '').trim();
    if (!normalizedNodeKey || normalizedNodeKey === 'access') {
        return { kind: 'access', id: '' };
    }
    const [kind, id] = normalizedNodeKey.split(':', 2);
    if ((kind === 'account' || kind === 'repo') && id) {
        return { kind, id };
    }
    return { kind: 'access', id: '' };
}

function resolveGitHubFeatureNodeKey(nodeKey) {
    const parsed = parseGitHubFeatureNodeKey(nodeKey);
    if (parsed.kind === 'account' && findGitHubAccountById(parsed.id)) {
        return `account:${parsed.id}`;
    }
    if (parsed.kind === 'repo' && findGitHubRepoById(parsed.id)) {
        return `repo:${parsed.id}`;
    }
    return 'access';
}

function findGitHubAccountById(accountId) {
    const normalizedAccountId = String(accountId || '').trim();
    return currentGitHubFeatureState.accounts.find(
        account => String(account?.account_id || '').trim() === normalizedAccountId,
    ) || null;
}

function findGitHubRepoById(repoSubscriptionId) {
    const normalizedRepoId = String(repoSubscriptionId || '').trim();
    return currentGitHubFeatureState.repos.find(
        repo => String(repo?.repo_subscription_id || '').trim() === normalizedRepoId,
    ) || null;
}

function getGitHubReposForAccount(accountId) {
    const normalizedAccountId = String(accountId || '').trim();
    return currentGitHubFeatureState.repos.filter(
        repo => String(repo?.account_id || '').trim() === normalizedAccountId,
    );
}

function getGitHubRulesForRepo(repoSubscriptionId) {
    const normalizedRepoId = String(repoSubscriptionId || '').trim();
    return currentGitHubFeatureState.rules.filter(
        rule => String(rule?.repo_subscription_id || '').trim() === normalizedRepoId,
    );
}

function findGitHubRuleById(triggerRuleId) {
    const normalizedRuleId = String(triggerRuleId || '').trim();
    return currentGitHubFeatureState.rules.find(
        rule => String(rule?.trigger_rule_id || '').trim() === normalizedRuleId,
    ) || null;
}

function upsertGitHubRuleInState(rule) {
    const normalizedRuleId = String(rule?.trigger_rule_id || '').trim();
    if (!normalizedRuleId) {
        return;
    }
    const nextRules = currentGitHubFeatureState.rules.filter(
        item => String(item?.trigger_rule_id || '').trim() !== normalizedRuleId,
    );
    nextRules.push(rule);
    currentGitHubFeatureState = {
        ...currentGitHubFeatureState,
        rules: nextRules,
    };
}

function removeGitHubRuleFromState(triggerRuleId) {
    const normalizedRuleId = String(triggerRuleId || '').trim();
    currentGitHubFeatureState = {
        ...currentGitHubFeatureState,
        rules: currentGitHubFeatureState.rules.filter(
            item => String(item?.trigger_rule_id || '').trim() !== normalizedRuleId,
        ),
    };
}

function resolveGitHubAccountLabel(account) {
    return String(account?.display_name || account?.name || account?.account_id || '').trim();
}

function normalizeGitHubRepositoryChoice(choice) {
    const owner = String(choice?.owner || '').trim();
    const repoName = String(choice?.repo_name || '').trim();
    const fullName = String(choice?.full_name || '').trim();
    if (!owner || !repoName || !fullName) {
        return null;
    }
    return {
        owner,
        repo_name: repoName,
        full_name: fullName,
        default_branch: String(choice?.default_branch || '').trim(),
        private: choice?.private === true,
    };
}

function buildGitHubRepositoryChoices(choices, repo = null) {
    const normalizedChoices = Array.isArray(choices)
        ? choices
            .map(choice => normalizeGitHubRepositoryChoice(choice))
            .filter(choice => choice)
        : [];
    const seenFullNames = new Set(normalizedChoices.map(choice => choice.full_name));
    const currentFullName = String(repo?.full_name || '').trim();
    if (currentFullName && !seenFullNames.has(currentFullName)) {
        const owner = String(repo?.owner || '').trim();
        const repoName = String(repo?.repo_name || '').trim();
        if (owner && repoName) {
            normalizedChoices.unshift({
                owner,
                repo_name: repoName,
                full_name: currentFullName,
                default_branch: String(repo?.default_branch || '').trim(),
                private: false,
            });
        }
    }
    return normalizedChoices;
}

function formatGitHubRepoEvents(repo) {
    return Array.isArray(repo?.subscribed_events) && repo.subscribed_events.length > 0
        ? repo.subscribed_events.join(', ')
        : t('automation.detail.none');
}

function formatGitHubRepoSubtitle(repo, { includeAccount = false } = {}) {
    const parts = [];
    if (includeAccount) {
        const accountLabel = resolveGitHubAccountLabel(findGitHubAccountById(repo?.account_id));
        if (accountLabel) {
            parts.push(accountLabel);
        }
    }
    parts.push(`${t('feature.automation.github_events')}: ${formatGitHubRepoEvents(repo)}`);
    parts.push(`${t('feature.automation.github_webhook_status')}: ${formatGitHubWebhookStatusLabel(String(repo?.webhook_status || 'unregistered'))}`);
    return parts.join(' · ');
}

function renderGitHubRepoListButton(
    repo,
    { child = false, includeAccount = false } = {},
) {
    const repoId = String(repo?.repo_subscription_id || '').trim();
    const statusTone = repo?.enabled === false ? 'disabled' : 'enabled';
    const nodeKey = `repo:${repoId}`;
    return `
        <button class="automation-record${child ? ' github-automation-record-child' : ''}${currentGitHubFeatureNodeKey === nodeKey ? ' is-active' : ''}" type="button" data-github-node-key="${escapeHtml(nodeKey)}">
            <div class="automation-record-copy">
                <strong>${escapeHtml(String(repo?.full_name || ''))}</strong>
                <span>${escapeHtml(formatGitHubRepoSubtitle(repo, { includeAccount }))}</span>
            </div>
            ${renderFeatureStatusPill(statusTone === 'disabled' ? t('automation.status.disabled') : t('automation.status.enabled'), statusTone)}
        </button>
    `;
}

function normalizeCommaSeparatedValues(value) {
    if (Array.isArray(value)) {
        return value.map(item => String(item || '').trim()).filter(Boolean);
    }
    return String(value || '')
        .split(',')
        .map(item => String(item || '').trim())
        .filter(Boolean);
}

function buildGitHubAccountPayloadFromDialogValues(account, values) {
    const name = String(values?.name || '').trim();
    if (!name) {
        throw new Error(t('feature.automation.github_account_required'));
    }
    const payload = {
        name,
        display_name: String(values?.display_name || '').trim() || null,
        enabled: values?.enabled === true,
    };
    const token = String(values?.token || '').trim();
    const webhookSecret = String(values?.webhook_secret || '').trim();
    if (account) {
        if (values?.clear_token === true) {
            payload.clear_token = true;
        } else if (token) {
            payload.token = token;
        }
        if (values?.clear_webhook_secret === true) {
            payload.clear_webhook_secret = true;
        } else if (webhookSecret) {
            payload.webhook_secret = webhookSecret;
        }
    } else {
        if (token) {
            payload.token = token;
        }
        if (webhookSecret) {
            payload.webhook_secret = webhookSecret;
        }
    }
    return payload;
}

async function requestGitHubAccountInput(account = null, submitHandler = null) {
    const values = await showFormDialog({
        title: account ? t('settings.roles.edit') : t('feature.automation.github_new_account'),
        message: t('feature.automation.github_account_copy'),
        tone: 'info',
        confirmLabel: t('settings.action.save'),
        cancelLabel: t('settings.action.cancel'),
        fields: [
            {
                id: 'name',
                label: t('feature.automation.github_account_name'),
                value: String(account?.name || '').trim(),
                placeholder: 'github-main',
            },
            {
                id: 'display_name',
                label: t('settings.triggers.display_name'),
                value: String(account?.display_name || '').trim(),
                placeholder: 'GitHub Main',
            },
            {
                id: 'token',
                label: t('settings.github.token'),
                type: 'password',
                allowEmptyReveal: true,
                value: '',
                placeholder: account
                    ? t('feature.automation.github_secret_keep')
                    : 'ghp_...',
                showLabel: t('settings.github.show_token'),
                hideLabel: t('settings.github.hide_token'),
                description: account
                    ? t('feature.automation.github_token_override_copy')
                    : t('feature.automation.github_token_copy'),
            },
            {
                id: 'clear_token',
                label: t('feature.automation.github_clear_token'),
                type: 'checkbox',
                value: false,
                description: t('feature.automation.github_clear_token_copy'),
            },
            {
                id: 'webhook_secret',
                label: t('feature.automation.github_webhook_secret'),
                type: 'password',
                allowEmptyReveal: true,
                value: '',
                placeholder: account
                    ? t('feature.automation.github_secret_keep')
                    : 'whsec_...',
                showLabel: t('feature.automation.github_show_webhook_secret'),
                hideLabel: t('feature.automation.github_hide_webhook_secret'),
                description: t('feature.automation.github_webhook_secret_copy'),
            },
            {
                id: 'clear_webhook_secret',
                label: t('feature.automation.github_clear_webhook_secret'),
                type: 'checkbox',
                value: false,
                description: t('feature.automation.github_clear_webhook_secret_copy'),
            },
            {
                id: 'enabled',
                label: t('automation.field.enabled'),
                type: 'checkbox',
                value: account ? String(account?.status || '').trim() !== 'disabled' : true,
                description: t('feature.automation.github_enabled_copy'),
            },
        ],
        submitHandler: typeof submitHandler === 'function'
            ? async formValues => await submitHandler(
                buildGitHubAccountPayloadFromDialogValues(account, formValues),
            )
            : null,
    });
    if (!values) {
        return null;
    }
    if (typeof submitHandler === 'function') {
        return values;
    }
    return buildGitHubAccountPayloadFromDialogValues(account, values);
}

async function requestGitHubRepoInput(account, repo = null) {
    const accountId = String(account?.account_id || '').trim();
    if (!accountId) {
        throw new Error(t('feature.automation.github_account_required'));
    }
    const repositoryChoices = buildGitHubRepositoryChoices(
        await fetchGitHubAccountRepositories(accountId),
        repo,
    );
    if (repositoryChoices.length === 0) {
        throw new Error(t('feature.automation.github_repo_options_empty'));
    }
    const selectedFullName = String(repo?.full_name || '').trim();
    const values = await showFormDialog({
        title: repo ? t('settings.roles.edit') : t('feature.automation.github_new_repo'),
        message: t('feature.automation.github_repo_copy'),
        tone: 'info',
        confirmLabel: t('settings.action.save'),
        cancelLabel: t('settings.action.cancel'),
        fields: [
            {
                id: 'full_name',
                type: 'select',
                label: t('feature.automation.github_repo_name'),
                value: selectedFullName,
                description: t('feature.automation.github_repo_select_copy'),
                options: [
                    {
                        value: '',
                        label: t('feature.automation.github_repo_select_placeholder'),
                    },
                    ...repositoryChoices.map(choice => ({
                        value: choice.full_name,
                        label: choice.full_name,
                    })),
                ],
            },
            {
                id: 'enabled',
                label: t('automation.field.enabled'),
                type: 'checkbox',
                value: repo ? repo.enabled !== false : true,
                description: t('feature.automation.github_repo_enabled_copy'),
            },
        ],
    });
    if (!values) {
        return null;
    }
    const fullName = String(values.full_name || '').trim();
    const selectedRepository = repositoryChoices.find(
        choice => choice.full_name === fullName,
    );
    if (!selectedRepository) {
        throw new Error(t('feature.automation.github_repo_required'));
    }
    const payload = {
        owner: selectedRepository.owner,
        repo_name: selectedRepository.repo_name,
        enabled: values.enabled === true,
    };
    if (!repo) {
        payload.account_id = accountId;
    }
    return payload;
}

function buildGitHubRulePayloadFromDialogValues(
    repo,
    rule,
    dispatchConfig,
    existingRunTemplate,
    values,
) {
    const name = String(values.name || '').trim();
    const promptTemplate = String(values.prompt_template || '').trim();
    if (!name) {
        throw new Error(t('feature.automation.github_rule_required'));
    }
    if (!promptTemplate) {
        throw new Error(t('automation.schedule.validation.prompt'));
    }
    const workspaceId = String(values.workspace_id || '').trim();
    if (!workspaceId) {
        throw new Error(t('automation.schedule.validation.workspace'));
    }
    const resolvedEventName = String(values.event_name || 'pull_request').trim() || 'pull_request';
    const selectedActions = normalizeCommaSeparatedValues(values.actions);
    const runTemplate = existingRunTemplate
        ? {
            ...existingRunTemplate,
            workspace_id: workspaceId,
            prompt_template: promptTemplate,
        }
        : {
            workspace_id: workspaceId,
            prompt_template: promptTemplate,
            session_mode: DEFAULT_SESSION_MODE,
            execution_mode: 'ai',
            yolo: true,
            thinking: {
                enabled: false,
                effort: DEFAULT_THINKING_EFFORT,
            },
        };
    const actionHooks = Array.isArray(dispatchConfig?.action_hooks)
        ? dispatchConfig.action_hooks.filter(action => !(
            String(action?.action_type || '').trim() === 'comment'
            && String(action?.phase || '').trim() === 'on_run_completed'
        ))
        : [];
    const payload = {
        name,
        match_config: {
            event_name: resolvedEventName,
            actions: selectedActions,
            base_branches: normalizeCommaSeparatedValues(values.base_branches),
            draft_pr: normalizeGitHubDraftPrValue(values.draft_pr),
        },
        dispatch_config: {
            target_type: 'run_template',
            run_template: runTemplate,
            action_hooks: actionHooks,
        },
        enabled: values.enabled === true,
    };
    if (!rule) {
        payload.provider = 'github';
        payload.account_id = String(repo?.account_id || '').trim();
        payload.repo_subscription_id = String(repo?.repo_subscription_id || '').trim();
    }
    return payload;
}

async function requestGitHubRuleInput(repo, rule = null, submitHandler = null) {
    const workspaces = currentGitHubFeatureState.workspaces.length > 0
        ? currentGitHubFeatureState.workspaces
        : await fetchWorkspaces();
    const workspaceOptions = resolveWorkspaceOptionValues(workspaces);
    if (workspaceOptions.length === 0) {
        throw new Error(t('settings.triggers.no_workspaces'));
    }
    const dispatchConfig = rule?.dispatch_config && typeof rule.dispatch_config === 'object'
        ? rule.dispatch_config
        : {};
    const existingRunTemplate = dispatchConfig?.run_template && typeof dispatchConfig.run_template === 'object'
        ? dispatchConfig.run_template
        : null;
    if (rule && String(dispatchConfig?.target_type || '').trim() && String(dispatchConfig?.target_type || '').trim() !== 'run_template') {
        throw new Error(t('feature.automation.github_rule_target_unsupported'));
    }
    const matchConfig = rule?.match_config && typeof rule.match_config === 'object'
        ? rule.match_config
        : {};
    const eventName = String(matchConfig?.event_name || 'pull_request').trim() || 'pull_request';
    const actionValues = Array.isArray(matchConfig?.actions) ? matchConfig.actions : [];
    const values = await showFormDialog({
        title: rule ? t('settings.roles.edit') : t('feature.automation.github_new_rule'),
        message: t('feature.automation.github_rule_copy'),
        tone: 'info',
        confirmLabel: t('settings.action.save'),
        cancelLabel: t('settings.action.cancel'),
        fields: [
            {
                id: 'name',
                label: t('feature.automation.github_rule_name'),
                value: String(rule?.name || '').trim(),
                placeholder: 'pr-opened',
            },
            {
                id: 'workspace_id',
                label: t('settings.triggers.workspace'),
                type: 'select',
                value: String(existingRunTemplate?.workspace_id || workspaceOptions[0]?.value || '').trim(),
                options: workspaceOptions,
                description: t('feature.automation.github_rule_workspace_copy'),
            },
            {
                id: 'event_name',
                label: t('feature.automation.github_event_subscription'),
                type: 'select',
                value: eventName,
                options: getGitHubRuleEventOptions(),
                description: t('feature.automation.github_event_copy'),
            },
            {
                id: 'actions',
                label: t('feature.automation.github_actions'),
                type: 'multiselect',
                value: actionValues,
                options: getGitHubRuleActionOptions(),
                placeholder: t('feature.automation.github_actions_placeholder'),
                description: t('feature.automation.github_actions_copy'),
            },
            {
                id: 'draft_pr',
                label: t('feature.automation.github_draft_pr'),
                type: 'select',
                value: resolveGitHubDraftPrFieldValue(matchConfig?.draft_pr),
                options: getGitHubDraftPrOptions(),
                description: t('feature.automation.github_draft_pr_copy'),
            },
            {
                id: 'base_branches',
                label: t('feature.automation.github_base_branches'),
                value: Array.isArray(matchConfig?.base_branches) ? matchConfig.base_branches.join(', ') : '',
                placeholder: 'main, release/*',
            },
            {
                id: 'prompt_template',
                label: t('automation.detail.prompt'),
                multiline: true,
                value: String(existingRunTemplate?.prompt_template || '').trim(),
                placeholder: 'Review the incoming GitHub event and summarize the next steps.',
            },
            {
                id: 'enabled',
                label: t('automation.field.enabled'),
                type: 'checkbox',
                value: rule ? rule.enabled !== false : true,
                description: t('feature.automation.github_rule_enabled_copy'),
            },
        ],
        submitHandler: typeof submitHandler === 'function'
            ? async formValues => await submitHandler(
                buildGitHubRulePayloadFromDialogValues(
                    repo,
                    rule,
                    dispatchConfig,
                    existingRunTemplate,
                    formValues,
                ),
            )
            : null,
    });
    if (!values) {
        return null;
    }
    if (typeof submitHandler === 'function') {
        return values;
    }
    return buildGitHubRulePayloadFromDialogValues(
        repo,
        rule,
        dispatchConfig,
        existingRunTemplate,
        values,
    );
}

async function requestFeishuTriggerInput(trigger = null) {
    const [workspaces, roleOptions, orchestrationConfig] = await Promise.all([
        fetchWorkspaces(),
        fetchRoleConfigOptions(),
        fetchOrchestrationConfig(),
    ]);
    const workspaceOptions = resolveWorkspaceOptionValues(workspaces);
    if (workspaceOptions.length === 0) {
        throw new Error(t('settings.triggers.no_workspaces'));
    }
    const roles = resolveRoleOptionsForForms(normalizeRoleOptions(roleOptions));
    const presets = resolvePresetOptionsForForms(normalizeOrchestrationPresets(orchestrationConfig));
    const sourceConfig = trigger?.source_config && typeof trigger.source_config === 'object' ? trigger.source_config : {};
    const targetConfig = trigger?.target_config && typeof trigger.target_config === 'object' ? trigger.target_config : {};
    const sessionMode = String(targetConfig?.session_mode || DEFAULT_SESSION_MODE).trim() || DEFAULT_SESSION_MODE;
    const thinkingEnabled = targetConfig?.thinking?.enabled === true;
    const values = await showFormDialog({
        title: trigger ? t('settings.roles.edit') : t('feature.gateway.add_feishu'),
        message: t('settings.triggers.feishu_detail_copy'),
        tone: 'info',
        confirmLabel: t('settings.action.save'),
        cancelLabel: t('settings.action.cancel'),
        fields: [
            {
                id: 'name',
                label: t('settings.triggers.trigger_name'),
                value: String(trigger?.name || '').trim(),
                placeholder: 'feishu-main',
            },
            {
                id: 'display_name',
                label: t('settings.triggers.display_name'),
                value: String(trigger?.display_name || '').trim(),
                placeholder: 'Feishu Main',
            },
            {
                id: 'workspace_id',
                label: t('settings.triggers.workspace'),
                type: 'select',
                value: String(targetConfig?.workspace_id || workspaceOptions[0]?.value || '').trim(),
                options: workspaceOptions,
            },
            {
                id: 'trigger_rule',
                label: t('settings.triggers.rule'),
                type: 'select',
                value: String(sourceConfig?.trigger_rule || DEFAULT_TRIGGER_RULE).trim() || DEFAULT_TRIGGER_RULE,
                options: [
                    { value: 'mention_only', label: 'mention_only', description: '' },
                    { value: 'all_messages', label: 'all_messages', description: '' },
                ],
            },
            {
                id: 'session_mode',
                label: t('settings.triggers.mode'),
                type: 'select',
                value: sessionMode,
                options: [
                    { value: 'normal', label: t('composer.mode_normal'), description: '' },
                    { value: 'orchestration', label: t('composer.mode_orchestration'), description: '' },
                ],
            },
            {
                id: 'normal_root_role_id',
                label: t('settings.triggers.normal_root_role_id'),
                type: 'select',
                value: String(targetConfig?.normal_root_role_id || '').trim(),
                options: roles,
            },
            {
                id: 'orchestration_preset_id',
                label: t('settings.triggers.orchestration_preset_id'),
                type: 'select',
                value: String(targetConfig?.orchestration_preset_id || '').trim(),
                options: presets,
            },
            {
                id: 'app_name',
                label: t('settings.triggers.feishu_app_name'),
                value: String(sourceConfig?.app_name || '').trim(),
                placeholder: t('settings.triggers.feishu_app_name_placeholder'),
            },
            {
                id: 'app_id',
                label: t('settings.triggers.feishu_app_id'),
                value: String(sourceConfig?.app_id || '').trim(),
                placeholder: t('settings.triggers.feishu_app_id_placeholder'),
            },
            {
                id: 'app_secret',
                label: t('settings.triggers.feishu_app_secret'),
                value: '',
                placeholder: t('settings.triggers.secret_keep_placeholder'),
            },
            {
                id: 'enabled',
                label: t('settings.field.enabled'),
                type: 'checkbox',
                value: String(trigger?.status || 'enabled').trim().toLowerCase() === 'enabled',
                description: '',
            },
            {
                id: 'yolo',
                label: t('settings.triggers.yolo'),
                type: 'checkbox',
                value: targetConfig?.yolo !== false,
                description: '',
            },
            {
                id: 'thinking_enabled',
                label: t('settings.triggers.thinking_enabled'),
                type: 'checkbox',
                value: thinkingEnabled,
                description: '',
            },
            {
                id: 'thinking_effort',
                label: t('settings.triggers.thinking_effort'),
                type: 'select',
                value: String(targetConfig?.thinking?.effort || DEFAULT_THINKING_EFFORT).trim() || DEFAULT_THINKING_EFFORT,
                options: THINKING_EFFORT_OPTIONS.map(option => ({ value: option, label: option, description: '' })),
            },
        ],
    });
    if (!values || typeof values !== 'object') {
        return null;
    }
    const name = String(values.name || '').trim();
    const workspaceId = String(values.workspace_id || '').trim();
    const appId = String(values.app_id || '').trim();
    const appName = String(values.app_name || '').trim();
    const appSecret = String(values.app_secret || '').trim();
    const nextSessionMode = String(values.session_mode || DEFAULT_SESSION_MODE).trim() || DEFAULT_SESSION_MODE;
    const orchestrationPresetId = String(values.orchestration_preset_id || '').trim();
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
    if (!trigger && !appSecret) {
        throw new Error(t('settings.triggers.missing_app_secret'));
    }
    if (nextSessionMode === 'orchestration' && !orchestrationPresetId) {
        throw new Error(t('settings.triggers.missing_orchestration_preset_id'));
    }
    const payload = {
        name,
        display_name: String(values.display_name || '').trim() || null,
        source_config: {
            provider: FEISHU_PLATFORM,
            trigger_rule: String(values.trigger_rule || DEFAULT_TRIGGER_RULE).trim() || DEFAULT_TRIGGER_RULE,
            app_id: appId,
            app_name: appName,
        },
        target_config: {
            workspace_id: workspaceId,
            session_mode: nextSessionMode,
            yolo: values.yolo !== false,
            thinking: {
                enabled: values.thinking_enabled === true,
                effort: values.thinking_enabled === true
                    ? (String(values.thinking_effort || DEFAULT_THINKING_EFFORT).trim() || DEFAULT_THINKING_EFFORT)
                    : null,
            },
        },
        enabled: values.enabled !== false,
    };
    const normalRootRoleId = String(values.normal_root_role_id || '').trim();
    if (nextSessionMode === 'normal' && normalRootRoleId) {
        payload.target_config.normal_root_role_id = normalRootRoleId;
    }
    if (nextSessionMode === 'orchestration' && orchestrationPresetId) {
        payload.target_config.orchestration_preset_id = orchestrationPresetId;
    }
    if (appSecret) {
        payload.secret_config = { app_secret: appSecret };
    }
    return payload;
}

async function requestWeChatAccountInput(account) {
    const workspaces = resolveWorkspaceOptionValues(currentGatewayFeatureState.workspaces);
    if (workspaces.length === 0) {
        throw new Error(t('settings.gateway.missing_workspace'));
    }
    const roles = resolveRoleOptionsForForms(currentGatewayFeatureState.normalRoles);
    const presets = resolvePresetOptionsForForms(currentGatewayFeatureState.orchestrationPresets);
    const sessionMode = String(account?.session_mode || DEFAULT_SESSION_MODE).trim() || DEFAULT_SESSION_MODE;
    const thinkingEnabled = account?.thinking?.enabled === true;
    const values = await showFormDialog({
        title: t('settings.gateway.account_editor'),
        message: String(account?.account_id || '').trim(),
        tone: 'info',
        confirmLabel: t('settings.action.save'),
        cancelLabel: t('settings.action.cancel'),
        fields: [
            {
                id: 'display_name',
                label: t('settings.gateway.display_name'),
                value: String(account?.display_name || '').trim(),
            },
            {
                id: 'workspace_id',
                label: t('settings.triggers.workspace'),
                type: 'select',
                value: String(account?.workspace_id || workspaces[0]?.value || '').trim(),
                options: workspaces,
            },
            {
                id: 'session_mode',
                label: t('settings.triggers.mode'),
                type: 'select',
                value: sessionMode,
                options: [
                    { value: 'normal', label: t('composer.mode_normal'), description: '' },
                    { value: 'orchestration', label: t('composer.mode_orchestration'), description: '' },
                ],
            },
            {
                id: 'normal_root_role_id',
                label: t('settings.triggers.normal_root_role_id'),
                type: 'select',
                value: String(account?.normal_root_role_id || '').trim(),
                options: roles,
            },
            {
                id: 'orchestration_preset_id',
                label: t('settings.triggers.orchestration_preset_id'),
                type: 'select',
                value: String(account?.orchestration_preset_id || '').trim(),
                options: presets,
            },
            {
                id: 'base_url',
                label: t('settings.gateway.base_url'),
                value: String(account?.base_url || '').trim(),
            },
            {
                id: 'cdn_base_url',
                label: t('settings.gateway.cdn_base_url'),
                value: String(account?.cdn_base_url || '').trim(),
            },
            {
                id: 'route_tag',
                label: t('settings.gateway.route_tag'),
                value: String(account?.route_tag || '').trim(),
            },
            {
                id: 'yolo',
                label: t('settings.triggers.yolo'),
                type: 'checkbox',
                value: account?.yolo !== false,
                description: '',
            },
            {
                id: 'thinking_enabled',
                label: t('settings.triggers.thinking_enabled'),
                type: 'checkbox',
                value: thinkingEnabled,
                description: '',
            },
            {
                id: 'thinking_effort',
                label: t('settings.triggers.thinking_effort'),
                type: 'select',
                value: String(account?.thinking?.effort || DEFAULT_THINKING_EFFORT).trim() || DEFAULT_THINKING_EFFORT,
                options: THINKING_EFFORT_OPTIONS.map(option => ({ value: option, label: option, description: '' })),
            },
        ],
    });
    if (!values || typeof values !== 'object') {
        return null;
    }
    const displayName = String(values.display_name || '').trim();
    const workspaceId = String(values.workspace_id || '').trim();
    const nextSessionMode = String(values.session_mode || DEFAULT_SESSION_MODE).trim() || DEFAULT_SESSION_MODE;
    const orchestrationPresetId = String(values.orchestration_preset_id || '').trim();
    if (!displayName) {
        throw new Error(t('settings.gateway.missing_display_name'));
    }
    if (!workspaceId) {
        throw new Error(t('settings.gateway.missing_workspace'));
    }
    if (nextSessionMode === 'orchestration' && !orchestrationPresetId) {
        throw new Error(t('settings.gateway.missing_orchestration_preset_id'));
    }
    return {
        display_name: displayName,
        workspace_id: workspaceId,
        session_mode: nextSessionMode,
        base_url: String(values.base_url || '').trim(),
        cdn_base_url: String(values.cdn_base_url || '').trim(),
        route_tag: String(values.route_tag || '').trim(),
        yolo: values.yolo !== false,
        thinking: {
            enabled: values.thinking_enabled === true,
            effort: values.thinking_enabled === true
                ? (String(values.thinking_effort || DEFAULT_THINKING_EFFORT).trim() || DEFAULT_THINKING_EFFORT)
                : null,
        },
        normal_root_role_id: nextSessionMode === 'normal'
            ? (String(values.normal_root_role_id || '').trim() || null)
            : null,
        orchestration_preset_id: nextSessionMode === 'orchestration' ? orchestrationPresetId : null,
    };
}

export function initializeProjectView() {
    syncActionLabels();
    if (els.projectViewReloadBtn) {
        els.projectViewReloadBtn.onclick = () => {
            void refreshProjectView();
        };
    }
    if (els.projectViewCloseBtn) {
        els.projectViewCloseBtn.title = t('workspace_view.back');
        els.projectViewCloseBtn.setAttribute('aria-label', t('workspace_view.back'));
        els.projectViewCloseBtn.onclick = () => {
            hideProjectView();
        };
    }
    if (!languageBound && typeof document?.addEventListener === 'function') {
        document.addEventListener('agent-teams-language-changed', () => {
            syncActionLabels();
            if (currentAutomationEditorState.open === true) {
                renderAutomationEditorModal();
            }
            if (state.currentMainView !== 'project') {
                return;
            }
            if (currentProjectViewMode === 'feature') {
                if (currentFeatureViewId === FEATURE_VIEW_IDS.skills) {
                    void openSkillsFeatureView();
                } else if (currentFeatureViewId === FEATURE_VIEW_IDS.automation) {
                    if (currentAutomationFeatureSection === 'github') {
                        void openAutomationGitHubView(currentGitHubFeatureNodeKey);
                    } else {
                        void openAutomationHomeView(selectedAutomationHomeProjectId);
                    }
                } else if (currentFeatureViewId === FEATURE_VIEW_IDS.gateway) {
                    void openImFeatureView();
                }
                return;
            }
            if (currentProjectViewMode === 'automation') {
                if (currentAutomationProject) {
                    void openAutomationProjectView(currentAutomationProject);
                }
                return;
            }
            if (currentSnapshot) {
                renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
            } else {
                renderLoadingState(currentWorkspace);
            }
        });
        languageBound = true;
    }
}

function syncActionLabels() {
    if (els.projectViewReloadBtn) {
        els.projectViewReloadBtn.textContent = t('workspace_view.reload');
    }
    if (els.projectViewCloseBtn) {
        els.projectViewCloseBtn.title = t('workspace_view.back');
        els.projectViewCloseBtn.setAttribute('aria-label', t('workspace_view.back'));
    }
}

export async function openWorkspaceProjectView(workspace) {
    const orderedWorkspace = normalizeWorkspaceRecordMountOrder(workspace);
    const workspaceId = String(orderedWorkspace?.workspace_id || '').trim();
    if (!workspaceId) {
        return;
    }

    cacheProjectViewState();
    currentProjectViewMode = 'workspace';
    currentAutomationProject = null;
    currentFeatureViewId = '';
    state.currentFeatureViewId = null;
    currentWorkspace = orderedWorkspace;
    currentSnapshotWorkspaceId = workspaceId;
    state.currentMainView = 'project';
    state.currentProjectViewWorkspaceId = workspaceId;
    state.currentWorkspaceId = workspaceId;
    state.currentSessionId = null;
    clearNewSessionDraft();
    clearAllPanels();
    hideRoundNavigator();
    setSubagentRailExpanded(false);
    setProjectViewVisible(true);

    const restoredFromCache = restoreProjectViewState(workspaceId);
    if (restoredFromCache && currentSnapshot) {
        renderWorkspaceSnapshot(orderedWorkspace, currentSnapshot);
        if (selectedTreePath && findDiffSummary(selectedTreePath)) {
            void ensureDiffFileLoaded(selectedTreePath);
        }
    } else {
        resetProjectViewState(workspaceId);
        currentMountName = resolveWorkspaceInitialMountName(orderedWorkspace);
        currentDiffState = {
            ...createInitialDiffState(),
            status: 'loading',
        };
        renderLoadingState(orderedWorkspace);
    }

    const loadToken = ++currentLoadToken;
    void loadWorkspaceSnapshot(workspaceId, loadToken);
    void loadWorkspaceDiffs(workspaceId, loadToken);
}

export async function openAutomationProjectView(project) {
    const automationProjectId = String(project?.automation_project_id || '').trim();
    if (!automationProjectId) {
        return;
    }
    await openAutomationHomeView(automationProjectId);
}

export async function openSkillsFeatureView() {
    openFeatureShell(FEATURE_VIEW_IDS.skills);
    renderFeatureLoadingState(t('feature.skills.title'), t('workspace_view.loading'));
    try {
        currentSkillsStatus = await fetchConfigStatus();
        renderSkillsFeatureView();
    } catch (error) {
        renderFeatureErrorState(t('feature.skills.title'), error);
        sysLog(`Failed to load skills feature: ${error?.message || error}`, 'log-error');
    }
}

async function openAutomationFeatureView(
    section,
    {
        projectId = '',
        nodeKey = '',
    } = {},
) {
    openFeatureShell(FEATURE_VIEW_IDS.automation);
    currentAutomationFeatureSection = section === 'github' ? 'github' : 'schedules';
    selectedAutomationHomeProjectId = String(projectId || '').trim();
    if (nodeKey) {
        currentGitHubFeatureNodeKey = String(nodeKey).trim() || 'access';
    }
    renderFeatureLoadingState(t('feature.automation.title'), t('workspace_view.loading'));
    try {
        if (currentAutomationFeatureSection === 'github') {
            await loadGitHubFeatureState();
        } else {
            const projects = await fetchAutomationProjects();
            currentAutomationProjects = Array.isArray(projects) ? projects : [];
            if (!selectedAutomationHomeProjectId && currentAutomationProjects.length > 0) {
                selectedAutomationHomeProjectId = String(
                    currentAutomationProjects[0]?.automation_project_id || '',
                ).trim();
            }
            if (selectedAutomationHomeProjectId) {
                await loadAutomationHomeDetail(selectedAutomationHomeProjectId);
            } else {
                currentAutomationHomeDetail = createInitialAutomationHomeDetail();
                currentAutomationProject = null;
            }
        }
        renderAutomationHomeView();
    } catch (error) {
        renderFeatureErrorState(t('feature.automation.title'), error);
        sysLog(`Failed to load automation feature: ${error?.message || error}`, 'log-error');
    }
}

export async function openAutomationHomeView(projectId = '') {
    await openAutomationFeatureView('schedules', { projectId });
}

export async function openAutomationGitHubView(nodeKey = 'access') {
    await openAutomationFeatureView('github', { nodeKey });
}

export async function openImFeatureView() {
    openFeatureShell(FEATURE_VIEW_IDS.gateway);
    renderFeatureLoadingState(t('feature.gateway.title'), t('workspace_view.loading'));
    try {
        const [triggers, wechatAccounts, workspaces, roleOptions, orchestrationConfig] = await Promise.all([
            fetchTriggers(),
            fetchWeChatGatewayAccounts(),
            fetchWorkspaces(),
            fetchRoleConfigOptions(),
            fetchOrchestrationConfig(),
        ]);
        currentGatewayFeatureState = {
            ...currentGatewayFeatureState,
            feishuTriggers: normalizeFeishuTriggers(triggers),
            feishuEditingTriggerId: '',
            feishuDraft: null,
            wechatAccounts: normalizeWeChatAccounts(wechatAccounts),
            workspaces: normalizeGatewayWorkspaces(workspaces),
            normalRoles: normalizeRoleOptions(roleOptions),
            orchestrationPresets: normalizeOrchestrationPresets(orchestrationConfig),
        };
        renderGatewayFeatureView();
    } catch (error) {
        renderFeatureErrorState(t('feature.gateway.title'), error);
        sysLog(`Failed to load IM feature: ${error?.message || error}`, 'log-error');
    }
}

export async function refreshProjectView() {
    if (currentProjectViewMode === 'feature') {
        if (currentFeatureViewId === FEATURE_VIEW_IDS.skills) {
            await openSkillsFeatureView();
            return;
        }
        if (currentFeatureViewId === FEATURE_VIEW_IDS.automation) {
            if (currentAutomationFeatureSection === 'github') {
                await openAutomationGitHubView(currentGitHubFeatureNodeKey);
            } else {
                await openAutomationHomeView(selectedAutomationHomeProjectId);
            }
            return;
        }
        if (currentFeatureViewId === FEATURE_VIEW_IDS.gateway) {
            await openImFeatureView();
            return;
        }
    }
    if (currentProjectViewMode === 'automation') {
        if (!currentAutomationProject) {
            return;
        }
        await openAutomationProjectView(currentAutomationProject);
        return;
    }
    if (!currentWorkspace) {
        return;
    }
    await openWorkspaceProjectView(currentWorkspace);
}

export function hideProjectView() {
    cacheProjectViewState();
    currentWorkspace = null;
    currentAutomationProject = null;
    currentProjectViewMode = 'workspace';
    currentFeatureViewId = '';
    state.currentFeatureViewId = null;
    currentAutomationProjects = [];
    selectedAutomationHomeProjectId = '';
    currentAutomationHomeDetail = createInitialAutomationHomeDetail();
    currentAutomationFeatureSection = 'schedules';
    currentGitHubFeatureState = createInitialGitHubFeatureState();
    currentGitHubFeatureNodeKey = 'access';
    currentSkillsStatus = null;
    currentGatewayFeatureState = createInitialGatewayFeatureState();
    renderGatewayFeatureModal();
    resetProjectViewState(null);
    state.currentMainView = 'session';
    state.currentProjectViewWorkspaceId = null;
    currentLoadToken += 1;
    setProjectViewVisible(false);
}

function resetProjectViewState(workspaceId) {
    currentSnapshot = null;
    currentSnapshotWorkspaceId = workspaceId;
    currentMountName = null;
    selectedTreePath = null;
    currentDiffState = createInitialDiffState();
    currentMountTrees.clear();
    expandedTreePaths.clear();
    loadingTreePaths.clear();
    treeLoadErrors.clear();
}

function createInitialDiffState() {
    return {
        status: 'idle',
        mountName: null,
        diffFiles: [],
        diffMessage: null,
        isGitRepository: null,
        gitRootPath: null,
        loadedDiffs: new Map(),
        loadingFilePaths: new Set(),
        fileErrors: new Map(),
    };
}

function cacheProjectViewState() {
    const workspaceId = String(currentSnapshotWorkspaceId || currentWorkspace?.workspace_id || '').trim();
    if (!workspaceId) {
        return;
    }
    if (!currentSnapshot && currentDiffState.status !== 'ready') {
        return;
    }
    workspaceViewCache.set(workspaceId, {
        snapshot: cloneSnapshot(currentSnapshot),
        currentMountName,
        selectedTreePath,
        mountTrees: Array.from(currentMountTrees.entries()).map(([mountName, tree]) => [
            String(mountName || '').trim(),
            cloneTreeNode(tree),
        ]),
        expandedTreePaths: Array.from(expandedTreePaths),
        diffState: cloneDiffState(currentDiffState),
    });
}

function restoreProjectViewState(workspaceId) {
    const cachedState = workspaceViewCache.get(workspaceId);
    resetProjectViewState(workspaceId);
    if (!cachedState) {
        return false;
    }

    currentSnapshot = cloneSnapshot(cachedState.snapshot);
    currentMountName = String(cachedState.currentMountName || '').trim() || null;
    selectedTreePath = String(cachedState.selectedTreePath || '').trim() || null;
    currentDiffState = cloneDiffState(cachedState.diffState);
    currentMountTrees.clear();
    for (const entry of Array.isArray(cachedState.mountTrees) ? cachedState.mountTrees : []) {
        if (!Array.isArray(entry) || entry.length < 2) {
            continue;
        }
        const mountName = String(entry[0] || '').trim();
        const tree = cloneTreeNode(entry[1]);
        if (!mountName || !tree) {
            continue;
        }
        currentMountTrees.set(mountName, tree);
    }

    for (const path of Array.isArray(cachedState.expandedTreePaths) ? cachedState.expandedTreePaths : []) {
        const normalizedPath = String(path || '').trim();
        if (normalizedPath) {
            expandedTreePaths.add(normalizedPath);
        }
    }

    return currentSnapshot !== null;
}

async function loadWorkspaceSnapshot(workspaceId, loadToken) {
    try {
        const snapshot = await fetchWorkspaceSnapshot(workspaceId);
        if (loadToken !== currentLoadToken || workspaceId !== currentSnapshotWorkspaceId) {
            return;
        }

        const nextSnapshot = normalizeSnapshot(snapshot, currentWorkspace);
        currentSnapshot = nextSnapshot;
        currentSnapshotWorkspaceId = workspaceId;
        currentMountName = resolveWorkspaceMountName(currentMountName, nextSnapshot);
        primeSnapshotMountTrees(nextSnapshot);

        ensureActiveMountTreeLoaded(loadToken);
        cacheProjectViewState();
    } catch (error) {
        if (loadToken !== currentLoadToken || workspaceId !== currentSnapshotWorkspaceId) {
            return;
        }
        if (!currentSnapshot) {
            currentDiffState = {
                ...createInitialDiffState(),
                status: 'error',
                diffMessage: String(error?.message || error || ''),
            };
            renderErrorState(currentWorkspace, error);
        }
        sysLog(`Failed to load project snapshot: ${error?.message || error}`, 'log-error');
    }
}

async function loadWorkspaceDiffs(workspaceId, loadToken) {
    const mountName = resolveActiveMountName();
    try {
        const payload = await fetchWorkspaceDiffs(workspaceId, mountName);
        if (loadToken !== currentLoadToken || workspaceId !== currentSnapshotWorkspaceId) {
            return;
        }

        const diffFiles = Array.isArray(payload?.diff_files) ? payload.diff_files : [];
        currentDiffState = {
            status: 'ready',
            mountName,
            diffFiles,
            diffMessage: String(payload?.diff_message || '').trim() || null,
            isGitRepository: payload?.is_git_repository === true,
            gitRootPath: payload?.git_root_path || null,
            loadedDiffs: filterLoadedDiffs(currentDiffState.loadedDiffs, diffFiles),
            loadingFilePaths: new Set(),
            fileErrors: filterFileErrors(currentDiffState.fileErrors, diffFiles),
        };
        if (!selectedTreePath && currentDiffState.diffFiles.length > 0) {
            selectedTreePath = String(currentDiffState.diffFiles[0]?.path || '').trim() || null;
        }
        if (currentSnapshot) {
            renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
        }
        cacheProjectViewState();
        if (selectedTreePath && findDiffSummary(selectedTreePath)) {
            void ensureDiffFileLoaded(selectedTreePath);
        }
    } catch (error) {
        if (loadToken !== currentLoadToken || workspaceId !== currentSnapshotWorkspaceId) {
            return;
        }
        if (currentDiffState.status !== 'ready') {
            currentDiffState = {
                ...createInitialDiffState(),
                status: 'error',
                diffMessage: String(error?.message || error || ''),
            };
        }
        if (currentSnapshot) {
            renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
        }
        sysLog(`Failed to load project diffs: ${error?.message || error}`, 'log-error');
    }
}

function setProjectViewVisible(visible) {
    if (els.projectView) {
        els.projectView.style.display = visible ? 'block' : 'none';
    }
    if (els.chatContainer) {
        els.chatContainer.style.display = visible ? 'none' : 'flex';
    }

    if (visible) {
        const observabilityView = document.getElementById('observability-view');
        const observabilityButton = document.getElementById('observability-btn');
        if (observabilityView) {
            observabilityView.style.display = 'none';
        }
        if (observabilityButton) {
            observabilityButton.classList.remove('active');
        }
        document.body?.classList?.remove('observability-mode');
    }
}

function renderFeatureErrorState(title, error) {
    renderToolbar(null, {
        title,
        mode: 'feature',
        summary: t('workspace_view.load_failed'),
    });
    if (els.projectViewContent) {
        els.projectViewContent.innerHTML = `
            <div class="workspace-view-empty-state is-error">
                <p>${escapeHtml(t('workspace_view.load_failed'))}</p>
                <p>${escapeHtml(String(error?.message || error || ''))}</p>
            </div>
        `;
    }
}

function renderSkillsFeatureView() {
    const skills = Array.isArray(currentSkillsStatus?.skills?.skills)
        ? currentSkillsStatus.skills.skills
        : [];
    renderToolbar(null, {
        title: t('feature.skills.title'),
        mode: 'feature',
        summary: resolveSkillsSummary(currentSkillsStatus),
        actions: `
            <button class="secondary-btn project-view-toolbar-btn" type="button" data-feature-skills-reload>${escapeHtml(t('feature.skills.reload'))}</button>
        `,
    });
    if (!els.projectViewContent) {
        return;
    }
    els.projectViewContent.innerHTML = `
        <div class="feature-page feature-page-neutral feature-skills-page">
            <section class="workspace-view-panel skills-clawhub-panel">
                <div class="workspace-view-panel-header">
                    <h3>${escapeHtml(t('settings.clawhub.section'))}</h3>
                    <span class="workspace-view-panel-meta">${escapeHtml(t('settings.clawhub.connectivity'))}</span>
                </div>
                <div class="feature-panel-body">
                    <div class="proxy-form-grid">
                        <div class="form-group proxy-inline-field">
                            <label for="${escapeHtml(FEATURE_CLAWHUB_FIELD_IDS.tokenInputId)}">${escapeHtml(t('settings.clawhub.token'))}</label>
                            <div class="secure-input-row">
                                <input type="password" id="${escapeHtml(FEATURE_CLAWHUB_FIELD_IDS.tokenInputId)}" placeholder="${escapeHtml(t('settings.clawhub.token_placeholder'))}" autocomplete="new-password" autocapitalize="off" autocorrect="off" spellcheck="false">
                                <button class="secure-input-btn" id="${escapeHtml(FEATURE_CLAWHUB_FIELD_IDS.toggleTokenButtonId)}" type="button" title="${escapeHtml(t('settings.clawhub.show_token'))}" aria-label="${escapeHtml(t('settings.clawhub.show_token'))}" style="display:none;">
                                    <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                                        <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"></path>
                                        <circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.8"></circle>
                                    </svg>
                                </button>
                            </div>
                        </div>
                        <div class="form-group proxy-inline-field web-provider-inline-field">
                            <span class="settings-token-source-label">${escapeHtml(t('settings.clawhub.token_source'))}</span>
                            <a class="web-provider-link-card" id="feature-clawhub-token-link" href="https://clawhub.ai/settings" target="_blank" rel="noreferrer" title="https://clawhub.ai/settings" aria-label="https://clawhub.ai/settings">
                                <span class="web-provider-link-copy">
                                    <span class="web-provider-link-badge">ClawHub</span>
                                    <span class="web-provider-link-url">https://clawhub.ai/settings</span>
                                    <span class="settings-token-source-note">${escapeHtml(t('settings.clawhub.token_source_help'))}</span>
                                </span>
                                <span class="web-provider-link-arrow" aria-hidden="true">
                                    <svg viewBox="0 0 24 24" fill="none" class="icon-sm">
                                        <path d="M7 17L17 7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path>
                                        <path d="M9 7h8v8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path>
                                    </svg>
                                </span>
                            </a>
                        </div>
                        <div class="form-group proxy-inline-field proxy-inline-field-actions">
                            <label for="${escapeHtml(FEATURE_CLAWHUB_FIELD_IDS.saveButtonId)}">${escapeHtml(t('settings.clawhub.token_action'))}</label>
                            <div class="settings-inline-action-row">
                                <button class="secondary-btn section-action-btn proxy-inline-test-btn" id="${escapeHtml(FEATURE_CLAWHUB_FIELD_IDS.probeButtonId)}" type="button">${escapeHtml(t('settings.clawhub.test_connection'))}</button>
                                <button class="primary-btn section-action-btn proxy-inline-test-btn" id="${escapeHtml(FEATURE_CLAWHUB_FIELD_IDS.saveButtonId)}" type="button">${escapeHtml(t('settings.action.save'))}</button>
                            </div>
                        </div>
                    </div>
                    <div class="proxy-probe-status" id="${escapeHtml(FEATURE_CLAWHUB_FIELD_IDS.statusId)}" style="display:none;"></div>
                </div>
            </section>
            <section class="workspace-view-panel skills-directory-panel">
                <div class="workspace-view-panel-header">
                    <h3>${escapeHtml(t('feature.skills.directory_title'))}</h3>
                    <span class="workspace-view-panel-meta">${escapeHtml(String(skills.length))}</span>
                </div>
                ${skills.length > 0 ? `
                    <div class="skills-directory-list">
                        ${skills.map(skill => `
                            <article class="skills-directory-row">
                                <div class="skills-directory-main">
                                    <div class="skills-directory-title-row">
                                        <strong>${escapeHtml(String(skill?.name || skill?.ref || ''))}</strong>
                                        ${renderFeatureStatusPill(resolveSkillScopeLabel(skill?.source || skill?.scope), 'neutral')}
                                    </div>
                                    <p>${escapeHtml(String(skill?.description || ''))}</p>
                                </div>
                                <div class="skills-directory-meta">
                                    <code>${escapeHtml(String(skill?.ref || ''))}</code>
                                    <span>${escapeHtml(String(skill?.path || skill?.instruction_path || ''))}</span>
                                </div>
                            </article>
                        `).join('')}
                    </div>
                ` : `
                    <div class="feature-panel-body">
                        ${renderFeatureEmptyState(
                            t('feature.skills.empty'),
                            t('feature.skills.empty_copy'),
                        )}
                    </div>
                `}
            </section>
        </div>
    `;
    els.projectViewToolbarActions?.querySelector('[data-feature-skills-reload]')?.addEventListener('click', () => {
        void handleSkillsReloadFeature();
    });
    bindClawHubSettingsHandlers(FEATURE_CLAWHUB_FIELD_IDS);
    void loadClawHubSettingsPanel(FEATURE_CLAWHUB_FIELD_IDS);
}

function renderAutomationHomeView() {
    const projects = Array.isArray(currentAutomationProjects) ? currentAutomationProjects : [];
    const detail = currentAutomationHomeDetail?.project ? currentAutomationHomeDetail : createInitialAutomationHomeDetail();
    const selectedProjectId = String(detail?.project?.automation_project_id || selectedAutomationHomeProjectId || '').trim();
    const automationSummary = currentAutomationFeatureSection === 'github'
        ? formatMessage('feature.automation.github_summary', {
            accounts: currentGitHubFeatureState.accounts.length,
            repos: currentGitHubFeatureState.repos.length,
            rules: currentGitHubFeatureState.rules.length,
        })
        : resolveAutomationSummary(projects);
    renderToolbar(null, {
        title: t('feature.automation.title'),
        mode: 'feature',
        summary: automationSummary,
        actions: `
            <div class="feature-inline-actions">
                <button class="secondary-btn project-view-toolbar-btn feature-section-tab${currentAutomationFeatureSection === 'schedules' ? ' is-active' : ''}" type="button" data-automation-section="schedules">${escapeHtml(t('feature.automation.section_schedules'))}</button>
                <button class="secondary-btn project-view-toolbar-btn feature-section-tab${currentAutomationFeatureSection === 'github' ? ' is-active' : ''}" type="button" data-automation-section="github">${escapeHtml(t('feature.automation.section_github'))}</button>
                ${currentAutomationFeatureSection === 'github'
                    ? `<button class="secondary-btn project-view-toolbar-btn" type="button" data-github-account-create>${escapeHtml(t('feature.automation.github_new_account'))}</button>`
                    : `<button class="secondary-btn project-view-toolbar-btn" type="button" data-feature-automation-create>${escapeHtml(t('feature.automation.create'))}</button>`
                }
            </div>
        `,
    });
    if (!els.projectViewContent) {
        return;
    }
    if (currentAutomationFeatureSection === 'github') {
        els.projectViewContent.innerHTML = renderGitHubAutomationView();
        bindAutomationFeatureSectionButtons();
        bindGitHubAutomationView();
        return;
    }
    els.projectViewContent.innerHTML = `
        <div class="feature-page feature-page-neutral automation-home-page">
            <div class="automation-home-shell">
                <section class="workspace-view-panel feature-list-panel automation-list-panel">
                    <div class="feature-panel-body">
                    ${projects.length > 0 ? `
                        <div class="automation-record-list">
                            ${projects.map(project => {
                                const projectId = String(project?.automation_project_id || '').trim();
                                const status = String(project?.status || 'disabled').trim() || 'disabled';
                                return `
                                    <button class="automation-record${projectId === selectedProjectId ? ' is-active' : ''}" type="button" data-automation-home-project-id="${escapeHtml(projectId)}">
                                        <div class="automation-record-copy">
                                            <strong>${escapeHtml(String(project?.display_name || project?.name || projectId))}</strong>
                                            <span>${escapeHtml(String(project?.cron_expression || t('automation.detail.not_scheduled')))}</span>
                                        </div>
                                        ${renderFeatureStatusPill(t(`automation.status.${status}`), status)}
                                    </button>
                                `;
                            }).join('')}
                        </div>
                    ` : renderFeatureEmptyState(
                        t('feature.automation.empty'),
                        t('feature.automation.empty_copy'),
                    )}
                    </div>
                </section>
                <section class="workspace-view-panel feature-detail-panel automation-detail-panel-surface">
                    <div class="feature-panel-body feature-panel-body-tight">
                    ${detail?.project ? renderAutomationHomeDetail(detail) : renderFeatureEmptyState(
                        t('feature.automation.empty'),
                        t('feature.automation.select'),
                    )}
                    </div>
                </section>
            </div>
        </div>
    `;
    bindAutomationFeatureSectionButtons();
    els.projectViewToolbarActions?.querySelector('[data-feature-automation-create]')?.addEventListener('click', () => {
        void handleAutomationCreateFeature();
    });
    els.projectViewContent.querySelectorAll('[data-automation-home-project-id]').forEach(button => {
        button.addEventListener('click', () => {
            void handleAutomationSelectFeatureProject(button.getAttribute('data-automation-home-project-id'));
        });
    });
    els.projectViewContent.querySelector('[data-automation-edit]')?.addEventListener('click', () => {
        void handleAutomationEditFeatureProject();
    });
    els.projectViewContent.querySelector('[data-automation-run]')?.addEventListener('click', () => {
        void handleAutomationRunFeatureProject();
    });
    els.projectViewContent.querySelector('[data-automation-toggle]')?.addEventListener('click', () => {
        void handleAutomationToggleFeatureProject();
    });
    els.projectViewContent.querySelector('[data-automation-delete]')?.addEventListener('click', () => {
        void handleAutomationDeleteFeatureProject();
    });
    els.projectViewContent.querySelectorAll('[data-automation-session-id]').forEach(node => {
        node.addEventListener('click', () => {
            const sessionId = String(node.getAttribute('data-automation-session-id') || '').trim();
            if (!sessionId) {
                return;
            }
            document.dispatchEvent(new CustomEvent('agent-teams-select-session', { detail: { sessionId } }));
        });
    });
}

function bindAutomationFeatureSectionButtons() {
    els.projectViewToolbarActions?.querySelectorAll('[data-automation-section]').forEach(button => {
        button.addEventListener('click', () => {
            const section = String(button.getAttribute('data-automation-section') || '').trim();
            if (section === 'github') {
                void openAutomationGitHubView(currentGitHubFeatureNodeKey);
                return;
            }
            void openAutomationHomeView(selectedAutomationHomeProjectId);
        });
    });
}

function renderGitHubAutomationView() {
    return `
        <div class="feature-page feature-page-neutral automation-home-page github-automation-page">
            <div class="automation-home-shell">
                <section class="workspace-view-panel feature-list-panel automation-list-panel">
                    <div class="feature-panel-body">
                        ${renderGitHubAutomationList()}
                    </div>
                </section>
                <section class="workspace-view-panel feature-detail-panel automation-detail-panel-surface">
                    <div class="feature-panel-body feature-panel-body-tight">
                        ${renderGitHubAutomationDetail()}
                    </div>
                </section>
            </div>
        </div>
    `;
}

function renderGitHubAutomationList() {
    const accounts = Array.isArray(currentGitHubFeatureState.accounts)
        ? currentGitHubFeatureState.accounts
        : [];
    return `
        <div class="automation-record-list github-automation-tree">
            <button class="automation-record${currentGitHubFeatureNodeKey === 'access' ? ' is-active' : ''}" type="button" data-github-node-key="access">
                <div class="automation-record-copy">
                    <strong>${escapeHtml(t('feature.automation.github_access'))}</strong>
                    <span>${escapeHtml(t('feature.automation.github_access_copy'))}</span>
                </div>
                ${renderFeatureStatusPill(t('feature.automation.github_access_status'), 'neutral')}
            </button>
            ${accounts.length > 0 ? accounts.map(account => {
                const accountId = String(account?.account_id || '').trim();
                const repos = getGitHubReposForAccount(accountId);
                const accountStatus = String(account?.status || 'disabled').trim() || 'disabled';
                return `
                    <div class="github-automation-group">
                        <button class="automation-record${currentGitHubFeatureNodeKey === `account:${accountId}` ? ' is-active' : ''}" type="button" data-github-node-key="${escapeHtml(`account:${accountId}`)}">
                            <div class="automation-record-copy">
                                <strong>${escapeHtml(resolveGitHubAccountLabel(account))}</strong>
                                <span>${escapeHtml(String(account?.name || accountId))}</span>
                            </div>
                            ${renderFeatureStatusPill(t(`automation.status.${accountStatus}`), accountStatus)}
                        </button>
                        ${repos.map(repo => renderGitHubRepoListButton(repo, { child: true })).join('')}
                    </div>
                `;
            }).join('') : renderFeatureEmptyState(
                t('feature.automation.github_no_accounts'),
                t('feature.automation.github_no_accounts_copy'),
            )}
        </div>
    `;
}

function renderGitHubAutomationDetail() {
    const parsedNode = parseGitHubFeatureNodeKey(currentGitHubFeatureNodeKey);
    if (parsedNode.kind === 'account') {
        const account = findGitHubAccountById(parsedNode.id);
        if (account) {
            return renderGitHubAccountDetail(account);
        }
    }
    if (parsedNode.kind === 'repo') {
        const repo = findGitHubRepoById(parsedNode.id);
        if (repo) {
            return renderGitHubRepoDetail(repo);
        }
    }
    return renderGitHubAccessDetail();
}

function renderGitHubAccessDetail() {
    return `
        <div class="automation-home-detail github-automation-detail">
            <div class="feature-detail-head automation-detail-head">
                <div class="automation-detail-copy">
                    <div class="feature-detail-title-row">
                        <h3>${escapeHtml(t('feature.automation.github_access'))}</h3>
                        ${renderFeatureStatusPill(t('feature.automation.github_access_status'), 'neutral')}
                    </div>
                    <div class="automation-prompt-inline">${escapeHtml(t('feature.automation.github_access_copy'))}</div>
                </div>
            </div>
            <div class="feature-card-grid">
                <article class="feature-card">
                    <div class="feature-card-header">
                        <div>
                            <h4>${escapeHtml(t('feature.automation.github_summary_accounts'))}</h4>
                        </div>
                    </div>
                    <div class="feature-meta-list">
                        <div><strong>${escapeHtml(String(currentGitHubFeatureState.accounts.length))}</strong></div>
                    </div>
                </article>
                <article class="feature-card">
                    <div class="feature-card-header">
                        <div>
                            <h4>${escapeHtml(t('feature.automation.github_summary_repos'))}</h4>
                        </div>
                    </div>
                    <div class="feature-meta-list">
                        <div><strong>${escapeHtml(String(currentGitHubFeatureState.repos.length))}</strong></div>
                    </div>
                </article>
                <article class="feature-card">
                    <div class="feature-card-header">
                        <div>
                            <h4>${escapeHtml(t('feature.automation.github_summary_rules'))}</h4>
                        </div>
                    </div>
                    <div class="feature-meta-list">
                        <div><strong>${escapeHtml(String(currentGitHubFeatureState.rules.length))}</strong></div>
                    </div>
                </article>
            </div>
            <article class="feature-card github-access-card">
                <div class="feature-card-header">
                    <div>
                        <h4>${escapeHtml(t('feature.automation.github_access'))}</h4>
                        <p>${escapeHtml(t('feature.automation.github_access_detail_copy'))}</p>
                    </div>
                </div>
                ${renderGitHubAccessPanelMarkup(FEATURE_GITHUB_FIELD_IDS)}
            </article>
            <section class="automation-flat-section">
                <div class="automation-section-header">
                    <h4>${escapeHtml(t('feature.automation.github_repo_section'))}</h4>
                    <span class="workspace-view-panel-meta">${escapeHtml(String(currentGitHubFeatureState.repos.length))}</span>
                </div>
                ${currentGitHubFeatureState.repos.length > 0 ? `
                    <div class="automation-record-list github-automation-inline-list">
                        ${currentGitHubFeatureState.repos.map(repo => renderGitHubRepoListButton(repo, { includeAccount: true })).join('')}
                    </div>
                ` : renderFeatureEmptyState(
                    t('feature.automation.github_no_repos'),
                    t('feature.automation.github_no_repos_copy'),
                )}
            </section>
        </div>
    `;
}

function renderGitHubAccountDetail(account) {
    const accountId = String(account?.account_id || '').trim();
    const repos = getGitHubReposForAccount(accountId);
    const status = String(account?.status || 'disabled').trim() || 'disabled';
    return `
        <div class="automation-home-detail github-automation-detail">
            <div class="feature-detail-head automation-detail-head">
                <div class="automation-detail-copy">
                    <div class="feature-detail-title-row">
                        <h3>${escapeHtml(resolveGitHubAccountLabel(account))}</h3>
                        ${renderFeatureStatusPill(t(`automation.status.${status}`), status)}
                    </div>
                    <div class="automation-prompt-inline">${escapeHtml(String(account?.name || accountId))}</div>
                </div>
                <div class="feature-action-row">
                    <button class="secondary-btn" type="button" data-github-account-edit="${escapeHtml(accountId)}">${escapeHtml(t('automation.action.edit'))}</button>
                    <button class="secondary-btn" type="button" data-github-account-toggle="${escapeHtml(accountId)}">${escapeHtml(status === 'enabled' ? t('automation.action.disable') : t('automation.action.enable'))}</button>
                    <button class="secondary-btn" type="button" data-github-repo-create="${escapeHtml(accountId)}">${escapeHtml(t('feature.automation.github_new_repo'))}</button>
                    <button class="secondary-btn danger-btn" type="button" data-github-account-delete="${escapeHtml(accountId)}">${escapeHtml(t('settings.action.delete'))}</button>
                </div>
            </div>
            <div class="feature-card-grid">
                <article class="feature-card">
                    <div class="feature-meta-list">
                        <div><span>${escapeHtml(t('feature.automation.github_account_token'))}</span><strong>${escapeHtml(account?.token_configured ? t('feature.automation.github_configured') : t('feature.automation.github_not_configured'))}</strong></div>
                        <div><span>${escapeHtml(t('feature.automation.github_account_secret'))}</span><strong>${escapeHtml(account?.webhook_secret_configured ? t('feature.automation.github_configured') : t('feature.automation.github_not_configured'))}</strong></div>
                        <div><span>${escapeHtml(t('feature.automation.github_summary_repos'))}</span><strong>${escapeHtml(String(repos.length))}</strong></div>
                    </div>
                </article>
                <article class="feature-card">
                    <div class="feature-meta-list">
                        <div><span>${escapeHtml(t('automation.detail.last_error'))}</span><strong>${escapeHtml(String(account?.last_error || t('automation.detail.none')))}</strong></div>
                        <div><span>${escapeHtml(t('automation.detail.updated_at'))}</span><strong>${escapeHtml(String(account?.updated_at || t('automation.detail.none')))}</strong></div>
                    </div>
                </article>
            </div>
            <section class="automation-flat-section">
                <div class="automation-section-header">
                    <div>
                        <h4>${escapeHtml(t('feature.automation.github_repo_section'))}</h4>
                    </div>
                </div>
                ${repos.length > 0 ? `
                    <div class="automation-record-list github-automation-inline-list">
                        ${repos.map(repo => renderGitHubRepoListButton(repo)).join('')}
                    </div>
                ` : renderFeatureEmptyState(
                    t('feature.automation.github_no_repos'),
                    t('feature.automation.github_no_repos_copy'),
                )}
            </section>
        </div>
    `;
}

function renderGitHubRepoDetail(repo) {
    const repoId = String(repo?.repo_subscription_id || '').trim();
    const rules = getGitHubRulesForRepo(repoId);
    const account = findGitHubAccountById(repo?.account_id);
    const webhooksUrl = buildGitHubRepoWebhooksUrl(repo);
    return `
        <div class="automation-home-detail github-automation-detail">
            <div class="feature-detail-head automation-detail-head">
                <div class="automation-detail-copy">
                    <div class="feature-detail-title-row">
                        <h3>${escapeHtml(String(repo?.full_name || ''))}</h3>
                        ${renderFeatureStatusPill(repo?.enabled === false ? t('automation.status.disabled') : t('automation.status.enabled'), repo?.enabled === false ? 'disabled' : 'enabled')}
                    </div>
                    <div class="automation-prompt-inline">${escapeHtml(String(repo?.callback_url || ''))}</div>
                </div>
                <div class="feature-action-row">
                    <button class="secondary-btn" type="button" data-github-repo-edit="${escapeHtml(repoId)}">${escapeHtml(t('automation.action.edit'))}</button>
                    <button class="secondary-btn" type="button" data-github-repo-toggle="${escapeHtml(repoId)}">${escapeHtml(repo?.enabled === false ? t('automation.action.enable') : t('automation.action.disable'))}</button>
                    <button class="secondary-btn" type="button" data-github-rule-create="${escapeHtml(repoId)}">${escapeHtml(t('feature.automation.github_new_rule'))}</button>
                    <button class="secondary-btn danger-btn" type="button" data-github-repo-delete="${escapeHtml(repoId)}">${escapeHtml(t('settings.action.delete'))}</button>
                </div>
            </div>
            <div class="feature-card-grid">
                <article class="feature-card">
                    <div class="feature-meta-list">
                        <div><span>${escapeHtml(t('feature.automation.github_account'))}</span><strong>${escapeHtml(resolveGitHubAccountLabel(account))}</strong></div>
                        <div><span>${escapeHtml(t('feature.automation.github_callback_url'))}</span><code>${escapeHtml(String(repo?.callback_url || t('automation.detail.none')))}</code></div>
                        <div><span>${escapeHtml(t('feature.automation.github_webhook_status'))}</span><strong>${escapeHtml(formatGitHubWebhookStatusLabel(String(repo?.webhook_status || 'unregistered')))}</strong></div>
                    </div>
                </article>
                <article class="feature-card">
                    <div class="feature-meta-list">
                        <div><span>${escapeHtml(t('feature.automation.github_default_branch'))}</span><strong>${escapeHtml(String(repo?.default_branch || t('automation.detail.none')))}</strong></div>
                        <div><span>${escapeHtml(t('feature.automation.github_events'))}</span><strong>${escapeHtml(formatGitHubRepoEvents(repo))}</strong></div>
                        <div><span>${escapeHtml(t('automation.detail.last_error'))}</span><strong>${escapeHtml(String(repo?.last_error || t('automation.detail.none')))}</strong></div>
                    </div>
                </article>
                ${webhooksUrl
                    ? `
                        <article class="feature-card github-webhooks-card">
                            <span class="settings-token-source-label">${escapeHtml(t('feature.automation.github_open_webhooks'))}</span>
                            <a class="web-provider-link-card" href="${escapeHtml(webhooksUrl)}" target="_blank" rel="noreferrer noopener" title="${escapeHtml(webhooksUrl)}" aria-label="${escapeHtml(webhooksUrl)}">
                                <span class="web-provider-link-copy">
                                    <span class="web-provider-link-badge">GitHub</span>
                                    <span class="web-provider-link-url">${escapeHtml(webhooksUrl)}</span>
                                    <span class="settings-token-source-note">${escapeHtml(t('feature.automation.github_open_webhooks_help'))}</span>
                                </span>
                                <span class="web-provider-link-arrow" aria-hidden="true">
                                    <svg viewBox="0 0 24 24" fill="none" class="icon-sm">
                                        <path d="M7 17L17 7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path>
                                        <path d="M9 7h8v8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path>
                                    </svg>
                                </span>
                            </a>
                        </article>
                    `
                    : ''
                }
            </div>
            <section class="automation-flat-section">
                <div class="automation-section-header automation-runs-header">
                    <h3>${escapeHtml(t('feature.automation.github_rule_section'))}</h3>
                    <span class="workspace-view-panel-meta">${escapeHtml(String(rules.length))}</span>
                </div>
                ${rules.length > 0 ? `
                    <div class="automation-run-list github-rule-list">
                        ${rules.map(rule => {
                            const ruleId = String(rule?.trigger_rule_id || '').trim();
                            const status = rule?.enabled === false ? 'disabled' : 'enabled';
                            return `
                                <article class="automation-run-card github-rule-card">
                                    <div class="automation-run-row github-rule-row">
                                        <div class="github-rule-heading">
                                            <strong>${escapeHtml(String(rule?.name || ruleId))}</strong>
                                            ${renderFeatureStatusPill(t(`automation.status.${status}`), status)}
                                        </div>
                                        <div class="feature-inline-actions github-rule-actions">
                                            <button class="secondary-btn" type="button" data-github-rule-edit="${escapeHtml(ruleId)}">${escapeHtml(t('automation.action.edit'))}</button>
                                            <button class="secondary-btn" type="button" data-github-rule-toggle="${escapeHtml(ruleId)}">${escapeHtml(status === 'enabled' ? t('automation.action.disable') : t('automation.action.enable'))}</button>
                                            <button class="secondary-btn danger-btn" type="button" data-github-rule-delete="${escapeHtml(ruleId)}">${escapeHtml(t('settings.action.delete'))}</button>
                                        </div>
                                    </div>
                                    <div class="automation-run-copy github-rule-copy">
                                        <div class="feature-meta-list github-rule-meta-list">
                                            <div><span>${escapeHtml(t('settings.triggers.workspace'))}</span><strong>${escapeHtml(formatGitHubRuleWorkspaceSummary(rule))}</strong></div>
                                            <div><span>${escapeHtml(t('feature.automation.github_event_subscription'))}</span><strong>${escapeHtml(resolveGitHubRuleEventLabel(rule))}</strong></div>
                                            <div><span>${escapeHtml(t('feature.automation.github_actions'))}</span><strong>${escapeHtml(resolveGitHubRuleActionsLabel(rule))}</strong></div>
                                            <div><span>${escapeHtml(t('feature.automation.github_draft_pr'))}</span><strong>${escapeHtml(resolveGitHubRuleDraftPrLabel(rule))}</strong></div>
                                            <div><span>${escapeHtml(t('feature.automation.github_base_branches'))}</span><strong>${escapeHtml(resolveGitHubRuleBaseBranchesLabel(rule))}</strong></div>
                                            <div class="github-rule-prompt-row"><span>${escapeHtml(t('automation.detail.prompt'))}</span><code class="github-rule-prompt">${escapeHtml(resolveGitHubRulePromptTemplate(rule))}</code></div>
                                        </div>
                                    </div>
                                </article>
                            `;
                        }).join('')}
                    </div>
                ` : renderFeatureEmptyState(
                    t('feature.automation.github_no_rules'),
                    t('feature.automation.github_no_rules_copy'),
                )}
            </section>
        </div>
    `;
}

function formatGitHubWebhookStatusLabel(status) {
    const normalizedStatus = String(status || 'unregistered').trim() || 'unregistered';
    if (normalizedStatus === 'registered') {
        return t('feature.automation.github_webhook_registered');
    }
    if (normalizedStatus === 'error') {
        return t('feature.automation.github_webhook_error');
    }
    return t('feature.automation.github_webhook_unregistered');
}

function buildGitHubRepoWebhooksUrl(repo) {
    const owner = String(repo?.owner || '').trim();
    const repoName = String(repo?.repo_name || '').trim();
    if (!owner || !repoName) {
        return '';
    }
    return `https://github.com/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/settings/hooks`;
}

function formatGitHubRuleSummary(rule) {
    const matchConfig = rule?.match_config && typeof rule.match_config === 'object'
        ? rule.match_config
        : {};
    const eventName = String(matchConfig?.event_name || '').trim();
    const actions = Array.isArray(matchConfig?.actions) ? matchConfig.actions.join(', ') : '';
    return actions ? `${eventName}: ${actions}` : eventName;
}

function formatGitHubRuleWorkspaceSummary(rule) {
    return resolveGitHubWorkspaceLabel(resolveGitHubRuleWorkspaceId(rule));
}

function resolveGitHubRuleWorkspaceId(rule) {
    return String(rule?.dispatch_config?.run_template?.workspace_id || '').trim();
}

function resolveGitHubRuleEventLabel(rule) {
    const eventName = String(rule?.match_config?.event_name || '').trim();
    return resolveOptionLabel(getGitHubRuleEventOptions(), eventName, eventName || t('automation.detail.none'));
}

function resolveGitHubRuleActionsLabel(rule) {
    const actions = Array.isArray(rule?.match_config?.actions)
        ? rule.match_config.actions.map(action => String(action || '').trim()).filter(Boolean)
        : [];
    return actions.length > 0 ? actions.join(', ') : t('automation.detail.none');
}

function resolveGitHubRuleDraftPrLabel(rule) {
    const draftPrValue = resolveGitHubDraftPrFieldValue(rule?.match_config?.draft_pr);
    return resolveOptionLabel(getGitHubDraftPrOptions(), draftPrValue, t('automation.detail.none'));
}

function resolveGitHubRuleBaseBranchesLabel(rule) {
    const branches = Array.isArray(rule?.match_config?.base_branches)
        ? rule.match_config.base_branches.map(branch => String(branch || '').trim()).filter(Boolean)
        : [];
    return branches.length > 0 ? branches.join(', ') : t('feature.automation.github_base_branches_all');
}

function resolveGitHubRulePromptTemplate(rule) {
    const promptTemplate = String(rule?.dispatch_config?.run_template?.prompt_template || '').trim();
    return promptTemplate || t('automation.detail.none');
}

function resolveOptionLabel(options, value, fallback = '') {
    const normalizedValue = String(value || '').trim();
    const match = (Array.isArray(options) ? options : []).find(
        option => String(option?.value || '').trim() === normalizedValue,
    );
    return String(match?.label || fallback || normalizedValue || '').trim();
}

function resolveGitHubWorkspaceLabel(workspaceId) {
    const normalizedWorkspaceId = String(workspaceId || '').trim();
    if (!normalizedWorkspaceId) {
        return t('automation.detail.none');
    }
    const workspace = (Array.isArray(currentGitHubFeatureState.workspaces)
        ? currentGitHubFeatureState.workspaces
        : []).find(item => String(item?.workspace_id || '').trim() === normalizedWorkspaceId);
    return formatWorkspaceOptionLabel(workspace || { workspace_id: normalizedWorkspaceId });
}

function getGitHubRuleEventOptions() {
    return [
        {
            value: 'pull_request',
            label: t('feature.automation.github_event_pull_request'),
            description: '',
        },
        {
            value: 'issues',
            label: t('feature.automation.github_event_issues'),
            description: '',
        },
    ];
}

function getGitHubRuleActionOptions() {
    return [
        {
            value: 'opened',
            label: 'opened',
            description: '',
        },
        {
            value: 'reopened',
            label: 'reopened',
            description: '',
        },
        {
            value: 'edited',
            label: 'edited',
            description: '',
        },
        {
            value: 'synchronize',
            label: 'synchronize',
            description: '',
        },
        {
            value: 'review_requested',
            label: 'review_requested',
            description: '',
        },
    ];
}

function getGitHubDraftPrOptions() {
    return [
        {
            value: 'any',
            label: t('feature.automation.github_draft_pr_any'),
            description: '',
        },
        {
            value: 'false',
            label: t('feature.automation.github_draft_pr_false'),
            description: '',
        },
        {
            value: 'true',
            label: t('feature.automation.github_draft_pr_true'),
            description: '',
        },
    ];
}

function resolveGitHubDraftPrFieldValue(value) {
    if (value === true) {
        return 'true';
    }
    if (value === false) {
        return 'false';
    }
    return 'any';
}

function normalizeGitHubDraftPrValue(value) {
    const normalizedValue = String(value || '').trim().toLowerCase();
    if (normalizedValue === 'true') {
        return true;
    }
    if (normalizedValue === 'false') {
        return false;
    }
    return null;
}

function bindGitHubAutomationView() {
    bindGitHubSettingsHandlers(FEATURE_GITHUB_FIELD_IDS);
    void loadGitHubSettingsPanel(FEATURE_GITHUB_FIELD_IDS);
    els.projectViewToolbarActions?.querySelector('[data-github-account-create]')?.addEventListener('click', () => {
        void handleGitHubCreateAccountFeature();
    });
    els.projectViewContent.querySelectorAll('[data-github-node-key]').forEach(button => {
        button.addEventListener('click', () => {
            const nodeKey = String(button.getAttribute('data-github-node-key') || '').trim();
            void openAutomationGitHubView(nodeKey || 'access');
        });
    });
    els.projectViewContent.querySelectorAll('[data-github-account-edit]').forEach(button => {
        button.addEventListener('click', () => {
            void handleGitHubEditAccountFeature(button.getAttribute('data-github-account-edit'));
        });
    });
    els.projectViewContent.querySelectorAll('[data-github-account-toggle]').forEach(button => {
        button.addEventListener('click', () => {
            void handleGitHubToggleAccountFeature(button.getAttribute('data-github-account-toggle'));
        });
    });
    els.projectViewContent.querySelectorAll('[data-github-account-delete]').forEach(button => {
        button.addEventListener('click', () => {
            void handleGitHubDeleteAccountFeature(button.getAttribute('data-github-account-delete'));
        });
    });
    els.projectViewContent.querySelectorAll('[data-github-repo-create]').forEach(button => {
        button.addEventListener('click', () => {
            void handleGitHubCreateRepoFeature(button.getAttribute('data-github-repo-create'));
        });
    });
    els.projectViewContent.querySelectorAll('[data-github-repo-edit]').forEach(button => {
        button.addEventListener('click', () => {
            void handleGitHubEditRepoFeature(button.getAttribute('data-github-repo-edit'));
        });
    });
    els.projectViewContent.querySelectorAll('[data-github-repo-toggle]').forEach(button => {
        button.addEventListener('click', () => {
            void handleGitHubToggleRepoFeature(button.getAttribute('data-github-repo-toggle'));
        });
    });
    els.projectViewContent.querySelectorAll('[data-github-repo-delete]').forEach(button => {
        button.addEventListener('click', () => {
            void handleGitHubDeleteRepoFeature(button.getAttribute('data-github-repo-delete'));
        });
    });
    els.projectViewContent.querySelectorAll('[data-github-rule-create]').forEach(button => {
        button.addEventListener('click', () => {
            void handleGitHubCreateRuleFeature(button.getAttribute('data-github-rule-create'));
        });
    });
    els.projectViewContent.querySelectorAll('[data-github-rule-edit]').forEach(button => {
        button.addEventListener('click', () => {
            void handleGitHubEditRuleFeature(button.getAttribute('data-github-rule-edit'));
        });
    });
    els.projectViewContent.querySelectorAll('[data-github-rule-toggle]').forEach(button => {
        button.addEventListener('click', () => {
            void handleGitHubToggleRuleFeature(button.getAttribute('data-github-rule-toggle'));
        });
    });
    els.projectViewContent.querySelectorAll('[data-github-rule-delete]').forEach(button => {
        button.addEventListener('click', () => {
            void handleGitHubDeleteRuleFeature(button.getAttribute('data-github-rule-delete'));
        });
    });
}

function renderAutomationHomeDetail(detail) {
    const project = detail?.project || null;
    if (!project) {
        return '';
    }
    const sessions = Array.isArray(detail?.sessions) ? detail.sessions : [];
    const workspaceRecord = detail?.workspace;
    const feishuBindings = Array.isArray(detail?.feishuBindings) ? detail.feishuBindings : [];
    const normalRoles = Array.isArray(detail?.normalRoles) ? detail.normalRoles : [];
    const orchestrationPresets = Array.isArray(detail?.orchestrationPresets) ? detail.orchestrationPresets : [];
    const runConfig = project?.run_config && typeof project.run_config === 'object' ? project.run_config : {};
    const sessionMode = String(runConfig?.session_mode || DEFAULT_SESSION_MODE).trim() || DEFAULT_SESSION_MODE;
    const normalRootRoleId = String(runConfig?.normal_root_role_id || '').trim();
    const orchestrationPresetId = String(runConfig?.orchestration_preset_id || '').trim();
    const status = String(project?.status || '').trim() || 'disabled';
    const statusLabel = t(`automation.status.${status}`);
    const deliveryBinding = project?.delivery_binding && typeof project.delivery_binding === 'object'
        ? project.delivery_binding
        : null;
    const deliveryBindingName = deliveryBinding
        ? resolveFeishuBindingDisplayName(deliveryBinding, feishuBindings)
        : '';
    const deliveryEvents = Array.isArray(project?.delivery_events) ? project.delivery_events : [];
    const workspaceId = String(project?.workspace_id || '').trim() || 'automation-system';
    const workspaceRootPath = String(workspaceRecord?.root_path || t('automation.workspace.missing'));
    return `
        <div class="automation-home-detail">
            <div class="feature-detail-head automation-detail-head">
                <div class="automation-detail-copy">
                    <div class="feature-detail-title-row">
                        <h3>${escapeHtml(String(project?.display_name || project?.name || ''))}</h3>
                        ${renderFeatureStatusPill(statusLabel, status)}
                    </div>
                    <div class="automation-prompt-inline">${escapeHtml(String(project?.prompt || ''))}</div>
                </div>
                <div class="feature-action-row">
                    <button class="secondary-btn" type="button" data-automation-edit>${escapeHtml(t('automation.action.edit'))}</button>
                    <button class="secondary-btn" type="button" data-automation-run>${escapeHtml(t('automation.action.run_now'))}</button>
                    <button class="secondary-btn" type="button" data-automation-toggle>${escapeHtml(status === 'enabled' ? t('automation.action.disable') : t('automation.action.enable'))}</button>
                    <button class="secondary-btn danger-btn" type="button" data-automation-delete>${escapeHtml(t('settings.action.delete'))}</button>
                </div>
            </div>
            <div class="automation-detail-grid automation-section-shell">
                <section class="automation-flat-section automation-meta-section">
                    <div class="automation-section-header">
                        <div>
                            <h4>${escapeHtml(t('automation.detail.configuration'))}</h4>
                        </div>
                    </div>
                    <div class="feature-meta-list automation-meta-list">
                        <div><span>${escapeHtml(t('automation.detail.schedule'))}</span><strong>${escapeHtml(String(project?.cron_expression || t('automation.detail.not_scheduled')))}</strong></div>
                        <div><span>${escapeHtml(t('automation.detail.timezone'))}</span><strong>${escapeHtml(String(project?.timezone || 'UTC'))}</strong></div>
                        <div><span>${escapeHtml(t('settings.triggers.mode'))}</span><strong>${escapeHtml(sessionMode === 'orchestration' ? t('composer.mode_orchestration') : t('composer.mode_normal'))}</strong></div>
                        <div><span>${escapeHtml(sessionMode === 'orchestration' ? t('settings.triggers.orchestration_preset_id') : t('settings.triggers.normal_root_role_id'))}</span><strong>${escapeHtml(
                            sessionMode === 'orchestration'
                                ? resolveAutomationPresetDisplayName(orchestrationPresetId, orchestrationPresets)
                                : resolveAutomationRoleDisplayName(normalRootRoleId, normalRoles)
                        )}</strong></div>
                        <div><span>${escapeHtml(t('automation.detail.next_run'))}</span><strong>${escapeHtml(String(project?.next_run_at || t('automation.detail.not_scheduled')))}</strong></div>
                        <div><span>${escapeHtml(t('automation.detail.last_run'))}</span><strong>${escapeHtml(String(project?.last_run_started_at || t('automation.detail.never')))}</strong></div>
                    </div>
                </section>
                <section class="automation-flat-section automation-meta-section">
                    <div class="automation-section-header">
                        <div>
                            <h4>${escapeHtml(t('workspace_view.bindings'))}</h4>
                        </div>
                    </div>
                    <div class="feature-meta-list automation-meta-list">
                        <div><span>${escapeHtml(t('automation.field.workspace'))}</span><strong>${escapeHtml(workspaceId)}</strong></div>
                        <div><span>${escapeHtml(t('automation.workspace.directory'))}</span><code>${escapeHtml(workspaceRootPath)}</code></div>
                        <div><span>${escapeHtml(t('workspace_view.delivery_events'))}</span><strong>${escapeHtml(deliveryEvents.length > 0 ? deliveryEvents.join(', ') : t('workspace_view.delivery_disabled'))}</strong></div>
                        ${deliveryBinding ? `
                            <div><span>${escapeHtml(t('workspace_view.feishu_trigger'))}</span><strong>${escapeHtml(String(deliveryBinding?.trigger_id || ''))}</strong></div>
                            <div><span>${escapeHtml(t('workspace_view.feishu_chat'))}</span><strong>${escapeHtml(deliveryBindingName)}</strong></div>
                        ` : ''}
                    </div>
                </section>
            </div>
            <section class="automation-flat-section automation-runs-section">
                <div class="automation-section-header automation-runs-header">
                    <h3>${escapeHtml(t('automation.detail.recent_runs'))}</h3>
                    <span class="workspace-view-panel-meta">${escapeHtml(String(sessions.length))} ${escapeHtml(t('automation.detail.session_count'))}</span>
                </div>
                ${sessions.length > 0 ? `
                    <div class="automation-run-list">
                        ${sessions.map(session => {
                            const sessionStatus = String(session?.active_run_status || 'completed').trim() || 'completed';
                            return `
                                <article class="automation-run-card" data-automation-session-id="${escapeHtml(String(session?.session_id || ''))}">
                                    <div class="automation-run-card-header">
                                        ${renderFeatureStatusPill(t(`automation.run_status.${sessionStatus}`), sessionStatus)}
                                        <code class="workspace-diff-path">${escapeHtml(String(session?.metadata?.title || session?.session_id || ''))}</code>
                                    </div>
                                    <div class="automation-run-card-meta">
                                        <span>${escapeHtml(t('automation.detail.updated_at'))}</span>
                                        <strong>${escapeHtml(String(session?.updated_at || ''))}</strong>
                                    </div>
                                </article>
                            `;
                        }).join('')}
                    </div>
                ` : renderInlineState(t('automation.detail.no_runs'))}
            </section>
        </div>
    `;
}

function resolveFeishuTriggerAppName(trigger) {
    const sourceConfig = trigger?.source_config && typeof trigger.source_config === 'object' ? trigger.source_config : {};
    return String(sourceConfig?.app_name || sourceConfig?.app_id || '').trim();
}

function renderGatewaySummaryChips(labels) {
    return `
        <div class="profile-card-chips gateway-summary-chips">
            ${labels
                .filter(label => String(label || '').trim())
                .map(label => `<span class="profile-card-chip">${escapeHtml(String(label))}</span>`)
                .join('')}
        </div>
    `;
}

function renderGatewayFeishuRecords(triggers) {
    if (!Array.isArray(triggers) || triggers.length === 0) {
        return `
            <div class="feature-panel-body">
                ${renderFeatureEmptyState(
                    t('settings.triggers.none'),
                    t('settings.triggers.none_copy'),
                )}
            </div>
        `;
    }
    return `
        <div class="role-records trigger-records gateway-records">
            ${triggers.map(trigger => {
                const triggerId = String(trigger?.trigger_id || '').trim();
                const status = String(trigger?.status || 'disabled').trim() || 'disabled';
                const workspaceId = String(trigger?.target_config?.workspace_id || '').trim();
                const appName = resolveFeishuTriggerAppName(trigger);
                const credentialsReady = trigger?.secret_status?.app_secret_configured === true;
                return `
                    <div class="role-record gateway-feature-record" data-feature-feishu-record="${escapeHtml(triggerId)}">
                        <div class="role-record-main">
                            <div class="role-record-title-row trigger-record-title-row">
                                <div class="role-record-title">${escapeHtml(String(trigger?.display_name || trigger?.name || triggerId))}</div>
                                <div class="profile-card-chips role-record-chips">
                                    <span class="profile-card-chip">${escapeHtml(t(`automation.status.${status}`))}</span>
                                    <span class="profile-card-chip">${escapeHtml(credentialsReady ? t('settings.triggers.credentials_ready') : t('settings.triggers.credentials_missing'))}</span>
                                </div>
                            </div>
                            <div class="role-record-meta trigger-record-meta">
                                ${workspaceId ? `<span>${escapeHtml(workspaceId)}</span>` : ''}
                                ${appName ? `<span>${escapeHtml(appName)}</span>` : ''}
                            </div>
                        </div>
                        <div class="role-record-actions trigger-record-actions">
                            <button class="settings-inline-action settings-list-action" type="button" data-feature-feishu-toggle="${escapeHtml(triggerId)}">${escapeHtml(status === 'enabled' ? t('settings.gateway.disable_account') : t('settings.gateway.enable_account'))}</button>
                            <button class="settings-inline-action settings-list-action" type="button" data-feature-feishu-edit="${escapeHtml(triggerId)}">${escapeHtml(t('settings.action.edit'))}</button>
                            <button class="settings-inline-action settings-list-action" type="button" data-feature-feishu-delete="${escapeHtml(triggerId)}">${escapeHtml(t('settings.action.delete'))}</button>
                        </div>
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

function renderGatewayWeChatRecords(accounts) {
    if (!Array.isArray(accounts) || accounts.length === 0) {
        return `
            <div class="feature-panel-body">
                ${renderFeatureEmptyState(
                    t('settings.gateway.wechat_none'),
                    t('settings.gateway.wechat_none_copy'),
                )}
            </div>
        `;
    }
    return `
        <div class="role-records trigger-records gateway-records">
            ${accounts.map(account => {
                const accountId = String(account?.account_id || '').trim();
                const status = String(account?.status || 'disabled').trim() || 'disabled';
                const statusLabel = account?.running === true
                    ? t('settings.gateway.status_running')
                    : t(`automation.status.${status}`);
                return `
                    <div class="role-record gateway-feature-record" data-feature-wechat-record="${escapeHtml(accountId)}">
                        <div class="role-record-main">
                            <div class="role-record-title-row trigger-record-title-row">
                                <div class="role-record-title">${escapeHtml(String(account?.display_name || accountId))}</div>
                                <div class="profile-card-chips role-record-chips">
                                    <span class="profile-card-chip">${escapeHtml(statusLabel)}</span>
                                    <span class="profile-card-chip">${escapeHtml(accountId)}</span>
                                </div>
                            </div>
                            <div class="role-record-meta trigger-record-meta">
                                ${account?.workspace_id ? `<span>${escapeHtml(String(account.workspace_id))}</span>` : ''}
                                ${account?.last_error ? `<span>${escapeHtml(`${t('settings.gateway.last_error')}: ${String(account.last_error)}`)}</span>` : ''}
                            </div>
                        </div>
                        <div class="role-record-actions trigger-record-actions">
                            <button class="settings-inline-action settings-list-action" type="button" data-feature-wechat-toggle="${escapeHtml(accountId)}">${escapeHtml(status === 'enabled' ? t('settings.gateway.disable_account') : t('settings.gateway.enable_account'))}</button>
                            <button class="settings-inline-action settings-list-action" type="button" data-feature-wechat-edit="${escapeHtml(accountId)}">${escapeHtml(t('settings.action.edit'))}</button>
                            <button class="settings-inline-action settings-list-action" type="button" data-feature-wechat-delete="${escapeHtml(accountId)}">${escapeHtml(t('settings.action.delete'))}</button>
                        </div>
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

function ensureGatewayModalRoot() {
    if (!document?.body) {
        return null;
    }
    if (!gatewayModalRoot) {
        try {
            gatewayModalRoot = document.getElementById('gateway-feature-modal-root');
        } catch {
            gatewayModalRoot = null;
        }
    }
    if (!gatewayModalRoot && typeof document.createElement === 'function') {
        gatewayModalRoot = document.createElement('div');
        gatewayModalRoot.id = 'gateway-feature-modal-root';
        gatewayModalRoot.className = 'gateway-feature-modal-root';
        if (typeof document.body.appendChild === 'function') {
            document.body.appendChild(gatewayModalRoot);
        }
    }
    return gatewayModalRoot;
}

function renderGatewayFeishuModal() {
    const draft = currentGatewayFeatureState.feishuDraft;
    if (!draft) {
        return '';
    }
    return `
        <div class="modal gateway-feature-modal" data-feature-gateway-modal>
            <div class="modal-content gateway-feature-modal-content gateway-feishu-modal-content" role="dialog" aria-modal="true" aria-labelledby="gateway-feature-modal-title">
                <div class="modal-header gateway-feature-modal-header">
                    <div class="gateway-feature-modal-heading">
                        <h3 id="gateway-feature-modal-title">${escapeHtml(String(draft.trigger_id || '').trim() ? t('settings.roles.edit') : t('feature.gateway.add_feishu'))}</h3>
                        <p>${escapeHtml(t('settings.triggers.feishu_detail_copy'))}</p>
                    </div>
                    <button class="icon-btn" type="button" aria-label="${escapeHtml(t('settings.action.cancel'))}" data-feature-gateway-modal-close>
                        <span aria-hidden="true">&times;</span>
                    </button>
                </div>
                <div class="gateway-feature-modal-body">
                    ${renderFeishuEditor()}
                </div>
            </div>
        </div>
    `;
}

function renderGatewayWeChatConnectModal() {
    if (currentGatewayFeatureState.wechatModalOpen !== true) {
        return '';
    }
    const session = currentGatewayFeatureState.wechatLoginSession;
    const statusTone = String(currentGatewayFeatureState.wechatStatusTone || '').trim() || 'neutral';
    const statusMessage = String(currentGatewayFeatureState.wechatStatusMessage || '').trim();
    const canRetry = currentGatewayFeatureState.wechatConnecting !== true;
    return `
        <div class="modal gateway-feature-modal gateway-connect-modal" data-feature-wechat-modal>
            <div class="modal-content gateway-feature-modal-content gateway-connect-modal-content" role="dialog" aria-modal="true" aria-labelledby="gateway-connect-modal-title">
                <div class="modal-header gateway-feature-modal-header gateway-connect-modal-header">
                    <div class="gateway-feature-modal-heading gateway-connect-modal-heading">
                        <h3 id="gateway-connect-modal-title">${escapeHtml(t('settings.gateway.connect_wechat'))}</h3>
                        <p>${escapeHtml(t('settings.gateway.qr_copy'))}</p>
                    </div>
                    <button class="icon-btn" type="button" aria-label="${escapeHtml(t('settings.action.cancel'))}" data-feature-wechat-modal-close>
                        <span aria-hidden="true">&times;</span>
                    </button>
                </div>
                <div class="gateway-connect-modal-body">
                    ${statusMessage
                        ? `<div class="feature-inline-status is-${escapeHtml(statusTone)}">${escapeHtml(statusMessage)}</div>`
                        : ''
                    }
                    ${session?.qr_code_url
                        ? `
                            <div class="gateway-connect-modal-qr">
                                <img class="gateway-qr-image" src="${escapeHtml(session.qr_code_url)}" alt="${escapeHtml(t('settings.gateway.qr_title'))}">
                            </div>
                        `
                        : `
                            <div class="gateway-connect-modal-placeholder">
                                <h4>${escapeHtml(t('settings.gateway.qr_title'))}</h4>
                                <p>${escapeHtml(t('settings.gateway.login_waiting'))}</p>
                            </div>
                        `
                    }
                </div>
                <div class="gateway-connect-modal-actions">
                    <button class="secondary-btn" type="button" data-feature-wechat-modal-close>${escapeHtml(t('settings.action.cancel'))}</button>
                    ${canRetry
                        ? `<button class="primary-btn" type="button" data-feature-wechat-modal-retry>${escapeHtml(t('settings.gateway.connect_wechat'))}</button>`
                        : ''
                    }
                </div>
            </div>
        </div>
    `;
}

function renderGatewayFeatureModal() {
    const root = ensureGatewayModalRoot();
    if (!root) {
        return;
    }
    const content = currentGatewayFeatureState.feishuDraft
        ? renderGatewayFeishuModal()
        : renderGatewayWeChatConnectModal();
    root.innerHTML = content;
    if (!content) {
        return;
    }
    root.querySelectorAll('[data-feature-gateway-modal-close]').forEach(button => {
        button.addEventListener('click', () => {
            handleCancelFeishuFeatureTrigger();
        });
    });
    root.querySelectorAll('[data-feature-feishu-save]').forEach(button => {
        button.addEventListener('click', () => {
            void handleSaveFeishuFeatureTrigger();
        });
    });
    root.querySelectorAll('[data-feature-feishu-cancel]').forEach(button => {
        button.addEventListener('click', () => {
            handleCancelFeishuFeatureTrigger();
        });
    });
    root.querySelectorAll('[data-feature-wechat-modal-close]').forEach(button => {
        button.addEventListener('click', () => {
            handleCloseWeChatFeatureModal();
        });
    });
    root.querySelectorAll('[data-feature-wechat-modal-retry]').forEach(button => {
        button.addEventListener('click', () => {
            void handleStartWeChatFeatureLogin();
        });
    });
    bindFeishuEditorInputs();
}

function renderGatewayFeatureView() {
    renderToolbar(null, {
        title: t('feature.gateway.title'),
        mode: 'feature',
        summary: resolveGatewayFeatureSummary(currentGatewayFeatureState),
    });
    if (!els.projectViewContent) {
        return;
    }
    const feishuTriggers = Array.isArray(currentGatewayFeatureState.feishuTriggers) ? currentGatewayFeatureState.feishuTriggers : [];
    const wechatAccounts = Array.isArray(currentGatewayFeatureState.wechatAccounts) ? currentGatewayFeatureState.wechatAccounts : [];
    els.projectViewContent.innerHTML = `
        <div class="feature-page feature-page-neutral gateway-feature-page">
            <section class="workspace-view-panel gateway-section">
                <div class="workspace-view-panel-header gateway-section-header">
                    <div class="gateway-section-headline">
                        <h3>${escapeHtml(t('feature.gateway.feishu_section'))}</h3>
                        <p>${escapeHtml(t('settings.triggers.feishu_detail_copy'))}</p>
                    </div>
                    <button class="secondary-btn gateway-section-btn" type="button" data-feature-gateway-add-feishu>${escapeHtml(t('feature.gateway.add_feishu'))}</button>
                </div>
                <div class="gateway-section-body">
                    ${renderGatewaySummaryChips([
                        t('settings.triggers.trigger_count').replace('{count}', String(feishuTriggers.length)),
                        t('settings.triggers.enabled_count').replace('{count}', String(feishuTriggers.filter(trigger => String(trigger?.status || '').trim() === 'enabled').length)),
                    ])}
                    ${renderGatewayFeishuRecords(feishuTriggers)}
                </div>
            </section>
            <section class="workspace-view-panel gateway-section">
                <div class="workspace-view-panel-header gateway-section-header">
                    <div class="gateway-section-headline">
                        <h3>${escapeHtml(t('feature.gateway.wechat_section'))}</h3>
                        <p>${escapeHtml(t('settings.gateway.wechat_none_copy'))}</p>
                    </div>
                    <button class="secondary-btn gateway-section-btn" type="button" data-feature-gateway-connect-wechat>${escapeHtml(t('settings.gateway.connect_wechat'))}</button>
                </div>
                <div class="gateway-section-body">
                    ${renderGatewaySummaryChips([
                        t('settings.gateway.gateway_count').replace('{count}', String(wechatAccounts.length)),
                        t('settings.triggers.enabled_count').replace('{count}', String(wechatAccounts.filter(account => String(account?.status || '').trim() === 'enabled').length)),
                        t('settings.gateway.running_count').replace('{count}', String(wechatAccounts.filter(account => account?.running === true).length)),
                    ])}
                    ${currentGatewayFeatureState.wechatStatusMessage ? `
                        <div class="feature-inline-status is-${escapeHtml(currentGatewayFeatureState.wechatStatusTone || 'neutral')}">${escapeHtml(currentGatewayFeatureState.wechatStatusMessage)}</div>
                    ` : ''}
                    ${renderGatewayWeChatRecords(wechatAccounts)}
                </div>
            </section>
        </div>
    `;
    els.projectViewContent.querySelectorAll('[data-feature-gateway-add-feishu]').forEach(button => {
        button.addEventListener('click', () => {
            void handleCreateFeishuFeatureTrigger();
        });
    });
    els.projectViewContent.querySelectorAll('[data-feature-gateway-connect-wechat]').forEach(button => {
        button.addEventListener('click', () => {
            void handleStartWeChatFeatureLogin();
        });
    });
    els.projectViewContent.querySelectorAll('[data-feature-feishu-edit]').forEach(button => {
        button.addEventListener('click', () => {
            void handleEditFeishuFeatureTrigger(button.getAttribute('data-feature-feishu-edit'));
        });
    });
    els.projectViewContent.querySelectorAll('[data-feature-feishu-toggle]').forEach(button => {
        button.addEventListener('click', () => {
            void handleToggleFeishuFeatureTrigger(button.getAttribute('data-feature-feishu-toggle'));
        });
    });
    els.projectViewContent.querySelectorAll('[data-feature-feishu-delete]').forEach(button => {
        button.addEventListener('click', () => {
            void handleDeleteFeishuFeatureTrigger(button.getAttribute('data-feature-feishu-delete'));
        });
    });
    els.projectViewContent.querySelectorAll('[data-feature-wechat-edit]').forEach(button => {
        button.addEventListener('click', () => {
            void handleEditWeChatFeatureAccount(button.getAttribute('data-feature-wechat-edit'));
        });
    });
    els.projectViewContent.querySelectorAll('[data-feature-wechat-toggle]').forEach(button => {
        button.addEventListener('click', () => {
            void handleToggleWeChatFeatureAccount(button.getAttribute('data-feature-wechat-toggle'));
        });
    });
    els.projectViewContent.querySelectorAll('[data-feature-wechat-delete]').forEach(button => {
        button.addEventListener('click', () => {
            void handleDeleteWeChatFeatureAccount(button.getAttribute('data-feature-wechat-delete'));
        });
    });
    renderGatewayFeatureModal();
}

async function handleSkillsReloadFeature() {
    try {
        await reloadSkillsConfig();
        showToast({
            title: t('settings.system.skills_reloaded'),
            message: t('settings.system.skills_reloaded_message'),
            tone: 'success',
        });
        await openSkillsFeatureView();
    } catch (error) {
        showToast({
            title: t('settings.system.reload_failed'),
            message: String(error?.message || error || ''),
            tone: 'danger',
        });
    }
}

function notifyGitHubFeatureSaved(label) {
    showToast({
        title: t('feature.automation.github_saved_title'),
        message: String(label || t('feature.automation.github_saved_message')),
        tone: 'success',
    });
}

function notifyGitHubFeatureDeleted(label) {
    showToast({
        title: t('feature.automation.github_deleted_title'),
        message: String(label || t('feature.automation.github_deleted_message')),
        tone: 'success',
    });
}

function notifyGitHubFeatureError(error) {
    showToast({
        title: t('feature.automation.github_failed_title'),
        message: String(error?.message || error || t('feature.automation.github_failed_message')),
        tone: 'danger',
    });
}

async function handleGitHubCreateAccountFeature() {
    try {
        const account = await requestGitHubAccountInput(
            null,
            async payload => await createGitHubTriggerAccount(payload),
        );
        if (!account) {
            return;
        }
        notifyGitHubFeatureSaved(resolveGitHubAccountLabel(account));
        await openAutomationGitHubView(`account:${String(account?.account_id || '').trim()}`);
    } catch (error) {
        notifyGitHubFeatureError(error);
    }
}

async function handleGitHubEditAccountFeature(accountId) {
    try {
        const account = findGitHubAccountById(accountId);
        if (!account) {
            return;
        }
        const updated = await requestGitHubAccountInput(
            account,
            async payload => await updateGitHubTriggerAccount(
                String(account.account_id || '').trim(),
                payload,
            ),
        );
        if (!updated) {
            return;
        }
        notifyGitHubFeatureSaved(resolveGitHubAccountLabel(updated));
        await openAutomationGitHubView(`account:${String(updated?.account_id || '').trim()}`);
    } catch (error) {
        notifyGitHubFeatureError(error);
    }
}

async function handleGitHubToggleAccountFeature(accountId) {
    try {
        const account = findGitHubAccountById(accountId);
        if (!account) {
            return;
        }
        const updated = String(account?.status || '').trim() === 'enabled'
            ? await disableGitHubTriggerAccount(String(account.account_id || '').trim())
            : await enableGitHubTriggerAccount(String(account.account_id || '').trim());
        notifyGitHubFeatureSaved(resolveGitHubAccountLabel(updated));
        await openAutomationGitHubView(`account:${String(updated?.account_id || '').trim()}`);
    } catch (error) {
        notifyGitHubFeatureError(error);
    }
}

async function handleGitHubDeleteAccountFeature(accountId) {
    try {
        const account = findGitHubAccountById(accountId);
        if (!account) {
            return;
        }
        const confirmed = await showConfirmDialog({
            title: t('settings.action.delete'),
            message: formatMessage('feature.automation.github_delete_account_confirm', {
                name: resolveGitHubAccountLabel(account),
            }),
            tone: 'danger',
            confirmLabel: t('settings.action.delete'),
            cancelLabel: t('settings.action.cancel'),
        });
        if (!confirmed) {
            return;
        }
        await deleteGitHubTriggerAccount(String(account.account_id || '').trim());
        notifyGitHubFeatureDeleted(resolveGitHubAccountLabel(account));
        await openAutomationGitHubView('access');
    } catch (error) {
        notifyGitHubFeatureError(error);
    }
}

async function handleGitHubCreateRepoFeature(accountId) {
    try {
        const account = findGitHubAccountById(accountId);
        if (!account) {
            return;
        }
        const payload = await requestGitHubRepoInput(account);
        if (!payload) {
            return;
        }
        const repo = await createGitHubRepoSubscription(payload);
        notifyGitHubFeatureSaved(String(repo?.full_name || ''));
        await openAutomationGitHubView(`repo:${String(repo?.repo_subscription_id || '').trim()}`);
    } catch (error) {
        notifyGitHubFeatureError(error);
    }
}

async function handleGitHubEditRepoFeature(repoSubscriptionId) {
    try {
        const repo = findGitHubRepoById(repoSubscriptionId);
        if (!repo) {
            return;
        }
        const account = findGitHubAccountById(repo.account_id);
        if (!account) {
            return;
        }
        const payload = await requestGitHubRepoInput(account, repo);
        if (!payload) {
            return;
        }
        const updated = await updateGitHubRepoSubscription(String(repo.repo_subscription_id || '').trim(), payload);
        notifyGitHubFeatureSaved(String(updated?.full_name || ''));
        await openAutomationGitHubView(`repo:${String(updated?.repo_subscription_id || '').trim()}`);
    } catch (error) {
        notifyGitHubFeatureError(error);
    }
}

async function handleGitHubToggleRepoFeature(repoSubscriptionId) {
    try {
        const repo = findGitHubRepoById(repoSubscriptionId);
        if (!repo) {
            return;
        }
        const updated = repo?.enabled === false
            ? await enableGitHubRepoSubscription(String(repo.repo_subscription_id || '').trim())
            : await disableGitHubRepoSubscription(String(repo.repo_subscription_id || '').trim());
        notifyGitHubFeatureSaved(String(updated?.full_name || ''));
        await openAutomationGitHubView(`repo:${String(updated?.repo_subscription_id || '').trim()}`);
    } catch (error) {
        notifyGitHubFeatureError(error);
    }
}

async function handleGitHubDeleteRepoFeature(repoSubscriptionId) {
    try {
        const repo = findGitHubRepoById(repoSubscriptionId);
        if (!repo) {
            return;
        }
        const confirmed = await showConfirmDialog({
            title: t('settings.action.delete'),
            message: formatMessage('feature.automation.github_delete_repo_confirm', {
                name: String(repo?.full_name || ''),
            }),
            tone: 'danger',
            confirmLabel: t('settings.action.delete'),
            cancelLabel: t('settings.action.cancel'),
        });
        if (!confirmed) {
            return;
        }
        await deleteGitHubRepoSubscription(String(repo.repo_subscription_id || '').trim());
        notifyGitHubFeatureDeleted(String(repo?.full_name || ''));
        await openAutomationGitHubView(`account:${String(repo?.account_id || '').trim()}`);
    } catch (error) {
        notifyGitHubFeatureError(error);
    }
}

async function handleGitHubCreateRuleFeature(repoSubscriptionId) {
    try {
        const repo = findGitHubRepoById(repoSubscriptionId);
        if (!repo) {
            return;
        }
        const created = await requestGitHubRuleInput(
            repo,
            null,
            async payload => await createGitHubTriggerRule(payload),
        );
        if (!created) {
            return;
        }
        upsertGitHubRuleInState(created);
        notifyGitHubFeatureSaved(String(repo?.full_name || ''));
        renderAutomationHomeView();
    } catch (error) {
        notifyGitHubFeatureError(error);
    }
}

async function handleGitHubEditRuleFeature(triggerRuleId) {
    try {
        const rule = findGitHubRuleById(triggerRuleId);
        if (!rule) {
            return;
        }
        const repo = findGitHubRepoById(rule.repo_subscription_id);
        if (!repo) {
            return;
        }
        const updated = await requestGitHubRuleInput(
            repo,
            rule,
            async payload => await updateGitHubTriggerRule(
                String(rule.trigger_rule_id || '').trim(),
                payload,
            ),
        );
        if (!updated) {
            return;
        }
        upsertGitHubRuleInState(updated);
        notifyGitHubFeatureSaved(String(rule?.name || ''));
        renderAutomationHomeView();
    } catch (error) {
        notifyGitHubFeatureError(error);
    }
}

async function handleGitHubToggleRuleFeature(triggerRuleId) {
    try {
        const rule = findGitHubRuleById(triggerRuleId);
        if (!rule) {
            return;
        }
        const updated = rule?.enabled === false
            ? await enableGitHubTriggerRule(String(rule.trigger_rule_id || '').trim())
            : await disableGitHubTriggerRule(String(rule.trigger_rule_id || '').trim());
        upsertGitHubRuleInState(updated);
        notifyGitHubFeatureSaved(String(updated?.name || ''));
        renderAutomationHomeView();
    } catch (error) {
        notifyGitHubFeatureError(error);
    }
}

async function handleGitHubDeleteRuleFeature(triggerRuleId) {
    try {
        const rule = findGitHubRuleById(triggerRuleId);
        if (!rule) {
            return;
        }
        const confirmed = await showConfirmDialog({
            title: t('settings.action.delete'),
            message: formatMessage('feature.automation.github_delete_rule_confirm', {
                name: String(rule?.name || ''),
            }),
            tone: 'danger',
            confirmLabel: t('settings.action.delete'),
            cancelLabel: t('settings.action.cancel'),
        });
        if (!confirmed) {
            return;
        }
        await deleteGitHubTriggerRule(String(rule.trigger_rule_id || '').trim());
        removeGitHubRuleFromState(rule.trigger_rule_id);
        notifyGitHubFeatureDeleted(String(rule?.name || ''));
        renderAutomationHomeView();
    } catch (error) {
        notifyGitHubFeatureError(error);
    }
}

async function handleAutomationCreateFeature() {
    const payload = await requestAutomationProjectInput({});
    if (!payload) {
        return;
    }
    await createAutomationProject(payload);
    document.dispatchEvent(new CustomEvent('agent-teams-projects-changed'));
    await openAutomationHomeView();
}

async function handleAutomationSelectFeatureProject(projectId) {
    selectedAutomationHomeProjectId = String(projectId || '').trim();
    await openAutomationHomeView(selectedAutomationHomeProjectId);
}

async function handleAutomationEditFeatureProject() {
    const project = currentAutomationHomeDetail?.project;
    if (!project) {
        return;
    }
    const payload = await requestAutomationProjectInput(project);
    if (!payload) {
        return;
    }
    await updateAutomationProject(String(project?.automation_project_id || ''), payload);
    document.dispatchEvent(new CustomEvent('agent-teams-projects-changed'));
    await openAutomationHomeView(String(project?.automation_project_id || ''));
}

async function handleAutomationRunFeatureProject() {
    const project = currentAutomationHomeDetail?.project;
    if (!project) {
        return;
    }
    const projectId = String(project?.automation_project_id || '').trim();
    const result = await runAutomationProject(String(project?.automation_project_id || ''));
    sysLog(formatAutomationRunLogMessage(result));
    document.dispatchEvent(new CustomEvent('agent-teams-projects-changed'));
    await openAutomationHomeView(projectId);
}

async function handleAutomationToggleFeatureProject() {
    const project = currentAutomationHomeDetail?.project;
    if (!project) {
        return;
    }
    const projectId = String(project?.automation_project_id || '').trim();
    if (String(project?.status || '').trim() === 'enabled') {
        await disableAutomationProject(projectId);
    } else {
        await enableAutomationProject(projectId);
    }
    await openAutomationHomeView(projectId);
}

async function handleAutomationDeleteFeatureProject() {
    const project = currentAutomationHomeDetail?.project;
    if (!project) {
        return;
    }
    const confirmed = await showConfirmDialog({
        title: t('settings.action.delete'),
        message: String(project?.display_name || project?.name || ''),
        tone: 'danger',
        confirmLabel: t('settings.action.delete'),
        cancelLabel: t('settings.action.cancel'),
    });
    if (!confirmed) {
        return;
    }
    await deleteAutomationProject(String(project?.automation_project_id || '').trim());
    document.dispatchEvent(new CustomEvent('agent-teams-projects-changed'));
    await openAutomationHomeView();
}

async function handleCreateFeishuFeatureTrigger() {
    try {
        if (currentGatewayFeatureState.workspaces.length === 0) {
            throw new Error(t('settings.triggers.no_workspaces'));
        }
        currentGatewayFeatureState = {
            ...currentGatewayFeatureState,
            wechatModalOpen: false,
            wechatLoginSession: null,
            wechatConnecting: false,
            feishuEditingTriggerId: '',
            feishuDraft: createFeishuTriggerDraft(),
        };
        renderGatewayFeatureModal();
    } catch (error) {
        showToast({
            title: t('settings.triggers.save_failed'),
            message: String(error?.message || error || ''),
            tone: 'danger',
        });
    }
}

async function handleEditFeishuFeatureTrigger(triggerId) {
    const trigger = currentGatewayFeatureState.feishuTriggers.find(item => item.trigger_id === String(triggerId || '').trim());
    if (!trigger) {
        return;
    }
    currentGatewayFeatureState = {
        ...currentGatewayFeatureState,
        wechatModalOpen: false,
        wechatLoginSession: null,
        wechatConnecting: false,
        feishuEditingTriggerId: trigger.trigger_id,
        feishuDraft: createFeishuTriggerDraft(trigger),
    };
    renderGatewayFeatureModal();
}

function handleCancelFeishuFeatureTrigger() {
    currentGatewayFeatureState = {
        ...currentGatewayFeatureState,
        feishuEditingTriggerId: '',
        feishuDraft: null,
    };
    renderGatewayFeatureView();
}

async function handleSaveFeishuFeatureTrigger() {
    try {
        const draft = syncFeishuDraftFromEditor();
        if (!draft) {
            return;
        }
        const isEditing = String(currentGatewayFeatureState.feishuEditingTriggerId || '').trim().length > 0;
        const payload = buildFeishuTriggerPayload(draft, { requireSecret: !isEditing });
        if (isEditing) {
            await updateTrigger(String(currentGatewayFeatureState.feishuEditingTriggerId || '').trim(), payload);
        } else {
            await createTrigger(payload);
        }
        currentGatewayFeatureState = {
            ...currentGatewayFeatureState,
            feishuEditingTriggerId: '',
            feishuDraft: null,
        };
        showToast({
            title: t('settings.triggers.saved'),
            message: t('settings.triggers.saved_message'),
            tone: 'success',
        });
        await openImFeatureView();
    } catch (error) {
        showToast({
            title: t('settings.triggers.save_failed'),
            message: String(error?.message || error || ''),
            tone: 'danger',
        });
    }
}

async function handleToggleFeishuFeatureTrigger(triggerId) {
    const trigger = currentGatewayFeatureState.feishuTriggers.find(item => item.trigger_id === String(triggerId || '').trim());
    if (!trigger) {
        return;
    }
    if (String(trigger?.status || '').trim() === 'enabled') {
        await disableTrigger(trigger.trigger_id);
    } else {
        await enableTrigger(trigger.trigger_id);
    }
    await openImFeatureView();
}

async function handleDeleteFeishuFeatureTrigger(triggerId) {
    const trigger = currentGatewayFeatureState.feishuTriggers.find(item => item.trigger_id === String(triggerId || '').trim());
    if (!trigger) {
        return;
    }
    const confirmed = await showConfirmDialog({
        title: t('settings.triggers.delete_confirm_title'),
        message: formatMessage('settings.triggers.delete_confirm_message', {
            name: String(trigger?.display_name || trigger?.name || trigger?.trigger_id || ''),
        }),
        tone: 'danger',
        confirmLabel: t('settings.action.delete'),
        cancelLabel: t('settings.action.cancel'),
    });
    if (!confirmed) {
        return;
    }
    await deleteTrigger(trigger.trigger_id);
    showToast({
        title: t('settings.triggers.deleted'),
        message: t('settings.triggers.deleted_message'),
        tone: 'success',
    });
    await openImFeatureView();
}

async function handleStartWeChatFeatureLogin() {
    const requestId = Date.now();
    currentGatewayFeatureState = {
        ...currentGatewayFeatureState,
        feishuEditingTriggerId: '',
        feishuDraft: null,
        wechatLoginRequestId: requestId,
        wechatModalOpen: true,
        wechatLoginSession: null,
        wechatStatusMessage: t('settings.gateway.login_waiting'),
        wechatStatusTone: '',
        wechatConnecting: true,
    };
    renderGatewayFeatureModal();
    try {
        const result = await startWeChatGatewayLogin({});
        if (currentGatewayFeatureState.wechatLoginRequestId !== requestId || currentGatewayFeatureState.wechatModalOpen !== true) {
            return;
        }
        currentGatewayFeatureState = {
            ...currentGatewayFeatureState,
            wechatModalOpen: true,
            wechatLoginSession: {
                session_key: String(result?.session_key || '').trim(),
                qr_code_url: String(result?.qr_code_url || '').trim(),
            },
        };
        renderGatewayFeatureModal();
        void finalizeWeChatFeatureLogin(String(result?.session_key || '').trim());
    } catch (error) {
        if (currentGatewayFeatureState.wechatLoginRequestId !== requestId || currentGatewayFeatureState.wechatModalOpen !== true) {
            return;
        }
        currentGatewayFeatureState = {
            ...currentGatewayFeatureState,
            wechatModalOpen: true,
            wechatLoginSession: null,
            wechatConnecting: false,
            wechatStatusMessage: String(error?.message || t('settings.gateway.login_failed')),
            wechatStatusTone: 'danger',
        };
        renderGatewayFeatureModal();
    }
}

function handleCloseWeChatFeatureModal() {
    currentGatewayFeatureState = {
        ...currentGatewayFeatureState,
        wechatLoginRequestId: 0,
        wechatModalOpen: false,
        wechatLoginSession: null,
        wechatConnecting: false,
    };
    renderGatewayFeatureModal();
}

async function finalizeWeChatFeatureLogin(sessionKey) {
    try {
        const result = await waitWeChatGatewayLogin({
            session_key: sessionKey,
            timeout_ms: 480000,
        });
        if (String(currentGatewayFeatureState?.wechatLoginSession?.session_key || '') !== String(sessionKey || '')) {
            return;
        }
        if (result?.connected === true) {
            showToast({
                title: t('settings.gateway.login_success'),
                message: String(result?.message || t('settings.gateway.login_success')),
                tone: 'success',
            });
            currentGatewayFeatureState = {
                ...currentGatewayFeatureState,
                wechatLoginRequestId: 0,
                wechatModalOpen: false,
                wechatConnecting: false,
                wechatLoginSession: null,
                wechatStatusMessage: String(result?.message || t('settings.gateway.login_success')),
                wechatStatusTone: 'success',
            };
            await openImFeatureView();
            return;
        }
        currentGatewayFeatureState = {
            ...currentGatewayFeatureState,
            wechatLoginRequestId: 0,
            wechatModalOpen: true,
            wechatConnecting: false,
            wechatStatusMessage: String(result?.message || t('settings.gateway.login_failed')),
            wechatStatusTone: 'danger',
        };
        renderGatewayFeatureModal();
    } catch (error) {
        currentGatewayFeatureState = {
            ...currentGatewayFeatureState,
            wechatLoginRequestId: 0,
            wechatModalOpen: true,
            wechatLoginSession: null,
            wechatConnecting: false,
            wechatStatusMessage: String(error?.message || t('settings.gateway.login_failed')),
            wechatStatusTone: 'danger',
        };
        renderGatewayFeatureModal();
    }
}

async function handleEditWeChatFeatureAccount(accountId) {
    const account = currentGatewayFeatureState.wechatAccounts.find(item => item.account_id === String(accountId || '').trim());
    if (!account) {
        return;
    }
    try {
        const payload = await requestWeChatAccountInput(account);
        if (!payload) {
            return;
        }
        await updateWeChatGatewayAccount(account.account_id, payload);
        showToast({
            title: t('settings.gateway.saved'),
            message: t('settings.gateway.saved_message'),
            tone: 'success',
        });
        await openImFeatureView();
    } catch (error) {
        showToast({
            title: t('settings.gateway.save_failed'),
            message: String(error?.message || error || ''),
            tone: 'danger',
        });
    }
}

async function handleToggleWeChatFeatureAccount(accountId) {
    const account = currentGatewayFeatureState.wechatAccounts.find(item => item.account_id === String(accountId || '').trim());
    if (!account) {
        return;
    }
    if (String(account?.status || '').trim() === 'enabled') {
        await disableWeChatGatewayAccount(account.account_id);
    } else {
        await enableWeChatGatewayAccount(account.account_id);
    }
    await openImFeatureView();
}

async function handleDeleteWeChatFeatureAccount(accountId) {
    const account = currentGatewayFeatureState.wechatAccounts.find(item => item.account_id === String(accountId || '').trim());
    if (!account) {
        return;
    }
    const confirmed = await showConfirmDialog({
        title: t('settings.gateway.delete_confirm_title'),
        message: formatMessage('settings.gateway.delete_confirm_message', {
            name: String(account?.display_name || account?.account_id || ''),
        }),
        tone: 'danger',
        confirmLabel: t('settings.action.delete'),
        cancelLabel: t('settings.action.cancel'),
    });
    if (!confirmed) {
        return;
    }
    await deleteWeChatGatewayAccount(account.account_id);
    showToast({
        title: t('settings.gateway.deleted'),
        message: t('settings.gateway.deleted_message'),
        tone: 'success',
    });
    await openImFeatureView();
}

function renderAutomationLoadingState(project) {
    renderToolbar(project, {
        summary: t('workspace_view.loading_automation_project'),
        mode: 'automation',
        actions: '',
    });
    if (els.projectViewContent) {
        els.projectViewContent.innerHTML = `
            <div class="workspace-view-grid">
                <section class="workspace-view-panel">
                    <div class="workspace-view-panel-header">
                        <h3>Schedule</h3>
                        <span class="workspace-view-panel-meta">Automation</span>
                    </div>
                    ${renderInlineState(t('workspace_view.loading_automation_details'))}
                </section>
                <section class="workspace-view-panel workspace-diff-panel">
                    <div class="workspace-view-panel-header">
                        <h3>${escapeHtml(t('workspace_view.recent_runs'))}</h3>
                        <span class="workspace-view-panel-meta"></span>
                    </div>
                    ${renderInlineState(t('workspace_view.loading_automation_sessions'))}
                </section>
            </div>
        `;
    }
}

function renderAutomationErrorState(project, error) {
    renderToolbar(project, {
        summary: t('workspace_view.failed_automation_project'),
        mode: 'automation',
        actions: '',
    });
    if (els.projectViewContent) {
        els.projectViewContent.innerHTML = `
            <div class="workspace-view-empty-state is-error">
                <p>${escapeHtml(t('workspace_view.failed_automation_project'))}</p>
                <p>${escapeHtml(String(error?.message || error || ''))}</p>
            </div>
        `;
    }
}

function renderAutomationProjectView(project, sessions, workspaceRecord = null, feishuBindings = []) {
    const safeSessions = Array.isArray(sessions) ? sessions : [];
    const status = String(project?.status || '').trim() || 'unknown';
    const scheduleMode = String(project?.schedule_mode || '').trim() || 'cron';
    const scheduleText = scheduleMode === 'one_shot'
        ? (String(project?.run_at || '').trim() || t('automation.detail.not_scheduled'))
        : (String(project?.cron_expression || '').trim() || t('automation.detail.not_scheduled'));
    const cronDescription = scheduleMode === 'one_shot'
        ? t('automation.cron.one_shot')
        : describeCronExpression(project?.cron_expression);
    const timezone = String(project?.timezone || 'UTC').trim() || 'UTC';
    const workspaceId = String(project?.workspace_id || '').trim() || 'automation-system';
    const workspaceRootPath = String(workspaceRecord?.root_path || '').trim() || t('automation.workspace.missing');
    const nextRunAt = String(project?.next_run_at || '').trim() || t('automation.detail.not_scheduled');
    const lastRunAt = String(project?.last_run_started_at || '').trim() || t('automation.detail.never');
    const lastError = String(project?.last_error || '').trim() || t('automation.detail.none');
    const deliveryBinding = project?.delivery_binding && typeof project.delivery_binding === 'object'
        ? project.delivery_binding
        : null;
    const deliveryBindingName = deliveryBinding
        ? resolveFeishuBindingDisplayName(deliveryBinding, feishuBindings)
        : '';
    const deliveryEvents = Array.isArray(project?.delivery_events) ? project.delivery_events : [];
    const deliveryEventsLabel = deliveryEvents.length > 0 ? deliveryEvents.join(', ') : 'none';
    const runButtonLabel = t('automation.action.run_now');
    const toggleButtonLabel = status === 'enabled' ? t('automation.action.disable') : t('automation.action.enable');
    const statusLabel = t(`automation.status.${status}`);

    renderToolbar(project, {
        summary: `${statusLabel} - ${safeSessions.length} ${t('automation.detail.session_count')}`,
        mode: 'automation',
        actions: `
            <button class="secondary-btn project-view-toolbar-btn" type="button" data-automation-edit>${escapeHtml(t('automation.action.edit'))}</button>
            <button class="secondary-btn project-view-toolbar-btn" type="button" data-automation-run>${escapeHtml(runButtonLabel)}</button>
            <button class="secondary-btn project-view-toolbar-btn" type="button" data-automation-toggle>${escapeHtml(toggleButtonLabel)}</button>
        `,
    });
    if (!els.projectViewContent) {
        return;
    }

    els.projectViewContent.innerHTML = `
        <div class="automation-detail-layout">
            <section class="workspace-view-panel automation-hero-panel">
                <div class="automation-hero-grid">
                    <div class="automation-hero-copy">
                        <span class="automation-status-pill is-${escapeHtml(status.toLowerCase())}">${escapeHtml(statusLabel)}</span>
                        <h3>${escapeHtml(t('automation.detail.overview'))}</h3>
                    </div>
                    <div class="automation-stat-grid">
                        <article class="automation-stat-card automation-stat-card-wide">
                            <span>${escapeHtml(t('automation.detail.schedule'))}</span>
                            <strong>${escapeHtml(scheduleText)}</strong>
                            <p class="automation-stat-note">${escapeHtml(cronDescription)}</p>
                        </article>
                        <article class="automation-stat-card">
                            <span>${escapeHtml(t('automation.field.workspace'))}</span>
                            <strong>${escapeHtml(workspaceId)}</strong>
                        </article>
                        <article class="automation-stat-card">
                            <span>${escapeHtml(t('automation.detail.timezone'))}</span>
                            <strong>${escapeHtml(timezone)}</strong>
                        </article>
                        <article class="automation-stat-card">
                            <span>${escapeHtml(t('automation.detail.next_run'))}</span>
                            <strong>${escapeHtml(nextRunAt)}</strong>
                        </article>
                        <article class="automation-stat-card">
                            <span>${escapeHtml(t('automation.detail.last_run'))}</span>
                            <strong>${escapeHtml(lastRunAt)}</strong>
                        </article>
                    </div>
                </div>
            </section>
            <section class="workspace-view-panel automation-prompt-panel">
                <div class="workspace-view-panel-header">
                    <h3>${escapeHtml(t('automation.detail.prompt'))}</h3>
                </div>
                <div class="automation-prompt-content">${escapeHtml(String(project?.prompt || ''))}</div>
            </section>
            <div class="automation-detail-grid">
                <section class="workspace-view-panel automation-detail-panel">
                    <div class="workspace-view-panel-header">
                        <h3>${escapeHtml(t('automation.detail.configuration'))}</h3>
                        <span class="workspace-view-panel-meta">${escapeHtml(scheduleMode)}</span>
                    </div>
                    <div class="automation-detail-section">
                        <div class="automation-detail-grid-compact">
                            <div class="automation-detail-row">
                                <span class="automation-detail-label">${escapeHtml(t('automation.detail.schedule'))}</span>
                                <span class="automation-detail-value">${escapeHtml(scheduleText)}</span>
                            </div>
                            <div class="automation-detail-row">
                                <span class="automation-detail-label">${escapeHtml(t('automation.detail.timezone'))}</span>
                                <span class="automation-detail-value">${escapeHtml(timezone)}</span>
                            </div>
                            <div class="automation-detail-row">
                                <span class="automation-detail-label">${escapeHtml(t('automation.detail.next_run'))}</span>
                                <span class="automation-detail-value">${escapeHtml(nextRunAt)}</span>
                            </div>
                            <div class="automation-detail-row">
                                <span class="automation-detail-label">${escapeHtml(t('automation.detail.last_run'))}</span>
                                <span class="automation-detail-value">${escapeHtml(lastRunAt)}</span>
                            </div>
                        </div>
                        <div class="automation-detail-row">
                            <span class="automation-detail-label">${escapeHtml(t('automation.detail.last_error'))}</span>
                            <span class="automation-detail-value${lastError === t('automation.detail.none') ? '' : ' is-error'}">${escapeHtml(lastError)}</span>
                        </div>
                    </div>
                </section>
                <section class="workspace-view-panel automation-binding-panel">
                    <div class="workspace-view-panel-header">
                        <h3>${escapeHtml(t('workspace_view.bindings'))}</h3>
                        <span class="workspace-view-panel-meta">${escapeHtml(deliveryBinding ? 'Feishu' : t('workspace_view.delivery_disabled'))}</span>
                    </div>
                    <div class="automation-binding-list">
                        <div class="automation-binding-item">
                            <span>${escapeHtml(t('automation.field.workspace'))}</span>
                            <strong>${escapeHtml(workspaceId)}</strong>
                        </div>
                        <div class="automation-binding-item">
                            <span>${escapeHtml(t('automation.workspace.directory'))}</span>
                            <code>${escapeHtml(workspaceRootPath)}</code>
                        </div>
                        <div class="automation-binding-item">
                            <span>${escapeHtml(t('workspace_view.delivery_events'))}</span>
                            <strong>${escapeHtml(deliveryEventsLabel)}</strong>
                        </div>
                        ${deliveryBinding ? `
                            <div class="automation-binding-item">
                                <span>${escapeHtml(t('workspace_view.feishu_trigger'))}</span>
                                <strong>${escapeHtml(String(deliveryBinding.trigger_id || ''))}</strong>
                            </div>
                            <div class="automation-binding-item">
                                <span>${escapeHtml(t('workspace_view.feishu_chat'))}</span>
                                <strong>${escapeHtml(deliveryBindingName)}</strong>
                            </div>
                            <div class="automation-binding-item">
                                <span>${escapeHtml(t('workspace_view.chat_type'))}</span>
                                <strong>${escapeHtml(String(deliveryBinding.chat_type || ''))}</strong>
                            </div>
                        ` : ''}
                    </div>
                </section>
            </div>
            <section class="workspace-view-panel automation-runs-panel">
                <div class="workspace-view-panel-header">
                    <h3>${escapeHtml(t('automation.detail.recent_runs'))}</h3>
                    <span class="workspace-view-panel-meta">${escapeHtml(String(safeSessions.length))} ${escapeHtml(t('automation.detail.session_count'))}</span>
                </div>
                ${safeSessions.length > 0 ? `
                    <div class="automation-run-list">
                        ${safeSessions.map(session => {
                            const sessionStatus = String(session.active_run_status || 'completed').trim() || 'completed';
                            const sessionStatusLabel = t(`automation.run_status.${sessionStatus}`);
                            const sessionTitle = String(session?.metadata?.title || session.session_id || '').trim() || String(session.session_id || '');
                            return `
                                <article class="automation-run-card" data-automation-session-id="${escapeHtml(String(session.session_id || ''))}">
                                    <div class="automation-run-card-header">
                                        <span class="workspace-diff-status is-modified">${escapeHtml(sessionStatusLabel)}</span>
                                        <code class="workspace-diff-path">${escapeHtml(sessionTitle)}</code>
                                    </div>
                                    <div class="automation-run-card-meta">
                                        <span>${escapeHtml(t('automation.detail.updated_at'))}</span>
                                        <strong>${escapeHtml(String(session.updated_at || ''))}</strong>
                                    </div>
                                </article>
                            `;
                        }).join('')}
                    </div>
                ` : renderInlineState(t('automation.detail.no_runs'))}
            </section>
        </div>
    `;

    const editAction = async () => {
        const nextPayload = await requestAutomationProjectInput(project);
        if (!nextPayload) {
            return;
        }
        await updateAutomationProject(String(project?.automation_project_id || ''), nextPayload);
        document.dispatchEvent(new CustomEvent('agent-teams-projects-changed'));
        await openAutomationProjectView(project);
    };
    document.querySelector('[data-automation-edit]')?.addEventListener('click', editAction);
    const runAction = async () => {
        const result = await runAutomationProject(String(project?.automation_project_id || ''));
        if (result?.reused_bound_session === true) {
            sysLog(formatAutomationRunLogMessage(result));
            await openAutomationProjectView(project);
            return;
        }
        if (result?.session_id) {
            document.dispatchEvent(new CustomEvent('agent-teams-select-session', { detail: { sessionId: result.session_id } }));
        }
    };
    document.querySelector('[data-automation-run]')?.addEventListener('click', runAction);
    const toggleAction = async () => {
        const projectId = String(project?.automation_project_id || '');
        if (status === 'enabled') {
            await disableAutomationProject(projectId);
        } else {
            await enableAutomationProject(projectId);
        }
        await openAutomationProjectView(project);
    };
    document.querySelector('[data-automation-toggle]')?.addEventListener('click', toggleAction);
    els.projectViewContent.querySelectorAll('[data-automation-session-id]').forEach(node => {
        node.addEventListener('click', () => {
            const sessionId = String(node.getAttribute('data-automation-session-id') || '').trim();
            if (!sessionId) return;
            document.dispatchEvent(new CustomEvent('agent-teams-select-session', { detail: { sessionId } }));
        });
    });
}

function renderLoadingState(workspace) {
    renderToolbar(workspace, {
        summary: t('workspace_view.loading'),
    });
    if (els.projectViewContent) {
        els.projectViewContent.innerHTML = `
            <div class="workspace-view-grid">
                <section class="workspace-view-panel">
                    <div class="workspace-view-panel-header">
                        <h3>${escapeHtml(t('workspace_view.tree'))}</h3>
                        <span class="workspace-view-panel-meta"></span>
                    </div>
                    <div class="workspace-tree-shell">
                        ${renderInlineState(t('workspace_view.loading_tree'))}
                    </div>
                </section>
                <section class="workspace-view-panel workspace-diff-panel">
                    <div class="workspace-view-panel-header">
                        <h3>${escapeHtml(t('workspace_view.diffs'))}</h3>
                        <span class="workspace-view-panel-meta"></span>
                    </div>
                    ${renderInlineState(t('workspace_view.loading_diffs'))}
                </section>
            </div>
        `;
    }
}

function renderErrorState(workspace, error) {
    renderToolbar(workspace, {
        summary: t('workspace_view.load_failed'),
    });
    if (els.projectViewContent) {
        els.projectViewContent.innerHTML = `
            <div class="workspace-view-empty-state is-error">
                <p>${escapeHtml(t('workspace_view.load_failed'))}</p>
                <p>${escapeHtml(String(error?.message || error || ''))}</p>
            </div>
        `;
    }
}

function renderWorkspaceSnapshot(workspace, snapshot) {
    renderToolbar(workspace, { summary: summarizeDiffState() });
    if (!els.projectViewContent) {
        return;
    }
    const activeTree = getCurrentMountTree();

    els.projectViewContent.innerHTML = `
        <div class="workspace-view-layout">
            ${renderWorkspaceMountStrip(snapshot)}
            <div class="workspace-view-grid">
                <section class="workspace-view-panel">
                    <div class="workspace-view-panel-header">
                        <h3>${escapeHtml(t('workspace_view.tree'))}</h3>
                        <span class="workspace-view-panel-meta">${renderWorkspaceRootMeta(snapshot)}</span>
                    </div>
                    <div class="workspace-tree-shell">
                        ${renderTree(activeTree)}
                    </div>
                </section>
                <section class="workspace-view-panel workspace-diff-panel">
                    <div class="workspace-view-panel-header">
                        <h3>${escapeHtml(t('workspace_view.diffs'))}</h3>
                        <span class="workspace-view-panel-meta">${escapeHtml(resolveDiffPanelMeta())}</span>
                    </div>
                    ${renderDiffSection()}
                </section>
            </div>
        </div>
    `;

    bindWorkspaceHeaderInteractions();
    bindTreeInteractions();
    bindDiffInteractions();
}

function renderWorkspaceRootMeta(snapshot) {
    const mount = resolveCurrentMount(snapshot);
    const rootPath = String(mount?.rootReference || snapshot?.root_path || '').trim();
    if (!rootPath) {
        return '';
    }
    if (mount?.provider !== 'local') {
        return `
            <span class="workspace-view-root-meta" title="${escapeHtml(rootPath)}">
                <span class="workspace-view-provider-badge is-remote">${escapeHtml(renderMountProviderLabel(mount))}</span>
                <span class="workspace-view-path-text">${escapeHtml(rootPath)}</span>
            </span>
        `;
    }
    const openLabel = t('workspace_view.open_root');
    return `
        <button
            type="button"
            class="workspace-view-path-button"
            data-open-workspace-root
            title="${escapeHtml(openLabel)}"
            aria-label="${escapeHtml(`${openLabel}: ${rootPath}`)}"
        >
            <span class="workspace-view-path-text">${escapeHtml(rootPath)}</span>
        </button>
    `;
}

function renderToolbar(projectOrWorkspace, { title = '', summary = '', mode = 'workspace', actions = '' } = {}) {
    if (els.projectViewTitle) {
        if (title) {
            els.projectViewTitle.textContent = title;
        } else {
            els.projectViewTitle.textContent = mode === 'automation'
                ? formatAutomationTitle(projectOrWorkspace)
                : formatWorkspaceTitle(projectOrWorkspace);
        }
    }
    if (els.projectViewSummary) {
        els.projectViewSummary.textContent = summary;
    }
    if (els.projectViewToolbarActions) {
        const reloadAction = mode === 'feature'
            ? ''
            : `<button id="project-view-reload" class="secondary-btn" type="button" data-project-view-reload>${escapeHtml(t('workspace_view.reload'))}</button>`;
        els.projectViewToolbarActions.innerHTML = `
            ${actions || ''}
            ${reloadAction}
            <button id="project-view-close" class="icon-btn" type="button" title="${escapeHtml(t('workspace_view.back'))}" aria-label="${escapeHtml(t('workspace_view.back'))}" data-project-view-close>
                <svg viewBox="0 0 24 24" fill="none" class="icon" aria-hidden="true">
                    <path d="M15 18l-6-6 6-6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path>
                </svg>
            </button>
        `;
        els.projectViewReloadBtn = els.projectViewToolbarActions.querySelector('[data-project-view-reload]');
        els.projectViewCloseBtn = els.projectViewToolbarActions.querySelector('[data-project-view-close]');
        if (els.projectViewReloadBtn) {
            els.projectViewReloadBtn.onclick = () => {
                void refreshProjectView();
            };
        }
        if (els.projectViewCloseBtn) {
            els.projectViewCloseBtn.onclick = () => {
                hideProjectView();
            };
        }
    }
}

function summarizeDiffState() {
    if (currentDiffState.status === 'loading') {
        return t('workspace_view.loading_diffs');
    }
    if (currentDiffState.status === 'error') {
        return t('workspace_view.load_failed');
    }
    if (currentDiffState.status !== 'ready') {
        return '';
    }
    if (currentDiffState.isGitRepository !== true) {
        return currentDiffState.diffMessage || t('workspace_view.not_git_repository');
    }
    if (currentDiffState.diffMessage) {
        return currentDiffState.diffMessage;
    }
    return formatTemplate(t('workspace_view.diff_summary'), {
        count: currentDiffState.diffFiles.length,
    });
}

function resolveDiffPanelMeta() {
    if (currentDiffState.status === 'loading') {
        return t('workspace_view.loading_diffs');
    }
    if (currentDiffState.status !== 'ready') {
        return '';
    }
    if (currentDiffState.isGitRepository !== true || currentDiffState.diffMessage) {
        return '';
    }
    return summarizeDiffState();
}

function resolveWorkspaceInitialMountName(workspace) {
    const explicitMountName = String(workspace?.default_mount_name || '').trim();
    if (explicitMountName) {
        return explicitMountName;
    }
    const mounts = Array.isArray(workspace?.mounts) ? sortWorkspaceMounts(workspace.mounts) : [];
    const firstMount = mounts.find(Boolean) || null;
    return String(firstMount?.mount_name || '').trim() || null;
}

function isMountShellSnapshot(snapshot) {
    if (!snapshot || typeof snapshot !== 'object') {
        return false;
    }
    return Object.prototype.hasOwnProperty.call(snapshot, 'default_mount_name')
        || Object.prototype.hasOwnProperty.call(snapshot, 'default_mount_root');
}

function normalizeWorkspaceRecordMountOrder(workspace) {
    if (!workspace || typeof workspace !== 'object' || !Array.isArray(workspace.mounts)) {
        return workspace;
    }
    return {
        ...workspace,
        mounts: sortWorkspaceMounts(workspace.mounts),
    };
}

function normalizeWorkspaceMounts(workspace, snapshot, defaultMountName, rootPath) {
    const workspaceMounts = Array.isArray(workspace?.mounts)
        ? sortWorkspaceMounts(workspace.mounts)
            .map(mount => normalizeWorkspaceMount(mount, defaultMountName, rootPath))
            .filter(Boolean)
        : [];
    if (workspaceMounts.length > 0) {
        return sortWorkspaceMounts(workspaceMounts);
    }
    const shellChildren = Array.isArray(snapshot?.tree?.children) ? snapshot.tree.children : [];
    if (shellChildren.length > 0 && isMountShellSnapshot(snapshot)) {
        const mounts = sortWorkspaceMounts(shellChildren
            .map(child => {
                const mountName = String(child?.path || child?.name || '').trim();
                if (!mountName) {
                    return null;
                }
                const fallbackProvider = mountName === defaultMountName && rootPath ? 'local' : 'unknown';
                return {
                    mountName,
                    provider: fallbackProvider,
                    rootReference: mountName === defaultMountName ? rootPath : '',
                    sshProfileId: '',
                    isDefault: mountName === defaultMountName,
                    hasChildren: child?.has_children === true || child?.hasChildren === true,
                };
            })
            .filter(Boolean));
        if (mounts.length > 0) {
            return mounts;
        }
    }
    return [
        {
            mountName: defaultMountName,
            provider: rootPath ? 'local' : 'unknown',
            rootReference: rootPath,
            sshProfileId: '',
            isDefault: true,
            hasChildren: snapshot?.tree?.has_children === true || snapshot?.tree?.hasChildren === true,
        },
    ];
}

function sortWorkspaceMounts(mounts = []) {
    return [...mounts].sort(compareWorkspaceMounts);
}

function compareWorkspaceMounts(left, right) {
    const providerDelta = workspaceMountProviderOrder(left) - workspaceMountProviderOrder(right);
    if (providerDelta !== 0) {
        return providerDelta;
    }
    return compareWorkspaceMountNames(workspaceMountSortName(left), workspaceMountSortName(right));
}

function workspaceMountProviderOrder(mount) {
    const provider = String(mount?.provider || '').trim();
    if (provider === 'local') {
        return 0;
    }
    if (provider === 'ssh') {
        return 1;
    }
    return 2;
}

function workspaceMountSortName(mount) {
    return String(mount?.mount_name || mount?.mountName || '').trim();
}

function compareWorkspaceMountNames(leftName, rightName) {
    const left = String(leftName || '').toLowerCase();
    const right = String(rightName || '').toLowerCase();
    if (left < right) {
        return -1;
    }
    if (left > right) {
        return 1;
    }
    const originalLeft = String(leftName || '');
    const originalRight = String(rightName || '');
    if (originalLeft < originalRight) {
        return -1;
    }
    if (originalLeft > originalRight) {
        return 1;
    }
    return 0;
}

function normalizeWorkspaceMount(mount, defaultMountName, rootPath) {
    if (!mount || typeof mount !== 'object') {
        return null;
    }
    const mountName = String(mount.mount_name || '').trim();
    if (!mountName) {
        return null;
    }
    const provider = String(mount.provider || '').trim() || 'unknown';
    const providerConfig = mount.provider_config && typeof mount.provider_config === 'object'
        ? mount.provider_config
        : {};
    const localRoot = String(providerConfig.root_path || '').trim();
    const remoteRoot = String(providerConfig.remote_root || '').trim();
    const sshProfileId = String(providerConfig.ssh_profile_id || '').trim();
    return {
        mountName,
        provider,
        rootReference: localRoot || remoteRoot || (mountName === defaultMountName ? rootPath : ''),
        sshProfileId,
        isDefault: mountName === defaultMountName,
        hasChildren: mount?.has_children === true || mount?.hasChildren === true,
    };
}

function renderWorkspaceMountStrip(snapshot) {
    const mounts = Array.isArray(snapshot?.mounts) ? sortWorkspaceMounts(snapshot.mounts) : [];
    if (!shouldRenderWorkspaceMountStrip(snapshot)) {
        return '';
    }
    return `
        <section class="workspace-mount-strip workspace-view-panel">
            <div class="workspace-view-panel-header">
                <div class="workspace-view-panel-header-copy">
                    <h3>${escapeHtml(t('workspace_view.mounts'))}</h3>
                    <span class="workspace-view-panel-meta">${escapeHtml(String(mounts.length))}</span>
                </div>
                <div class="workspace-panel-header-actions">
                    <button class="secondary-btn project-view-toolbar-btn workspace-panel-action-btn" type="button" data-workspace-add-mount>${escapeHtml(t('workspace_view.mount_add'))}</button>
                    <button class="secondary-btn project-view-toolbar-btn workspace-panel-action-btn" type="button" data-workspace-edit-mount>${escapeHtml(t('workspace_view.mount_edit'))}</button>
                    <button class="secondary-btn project-view-toolbar-btn workspace-panel-action-btn" type="button" data-workspace-open-settings>${escapeHtml(t('workspace_view.mount_profiles'))}</button>
                    <button class="secondary-btn project-view-toolbar-btn workspace-panel-action-btn" type="button" data-workspace-delete-mount ${mounts.length <= 1 ? 'disabled' : ''}>${escapeHtml(t('workspace_view.mount_remove'))}</button>
                </div>
            </div>
            <div class="workspace-mount-list">
                ${mounts.map(mount => renderWorkspaceMountCard(mount)).join('')}
            </div>
        </section>
    `;
}

function shouldRenderWorkspaceMountStrip(snapshot) {
    const mounts = Array.isArray(snapshot?.mounts) ? snapshot.mounts : [];
    return mounts.length > 0;
}

function renderWorkspaceMountCard(mount) {
    const mountName = String(mount?.mountName || '').trim();
    const rootReference = String(mount?.rootReference || '').trim();
    const sshProfileId = String(mount?.sshProfileId || '').trim();
    const isActive = resolveActiveMountName() === mountName;
    return `
        <button
            type="button"
            class="workspace-mount-card${isActive ? ' is-active' : ''}"
            data-workspace-mount="${escapeHtml(mountName)}"
            aria-pressed="${isActive ? 'true' : 'false'}"
        >
            <span class="workspace-mount-card-head">
                <strong>${escapeHtml(mountName)}</strong>
                <span class="workspace-mount-card-badges">
                    <span class="workspace-view-provider-badge">${escapeHtml(renderMountProviderLabel(mount))}</span>
                    ${mount?.isDefault ? `<span class="workspace-view-provider-badge is-default">${escapeHtml(t('workspace_view.mount_default'))}</span>` : ''}
                </span>
            </span>
            ${rootReference ? `<span class="workspace-mount-card-path">${escapeHtml(rootReference)}</span>` : ''}
            ${sshProfileId ? `<span class="workspace-mount-card-meta">${escapeHtml(`${t('workspace_view.mount_profile')}: ${sshProfileId}`)}</span>` : ''}
        </button>
    `;
}

function renderMountProviderLabel(mount) {
    const provider = String(mount?.provider || '').trim() || 'unknown';
    return t(`workspace_view.mount_provider.${provider}`);
}

function resolveWorkspaceMountName(candidateMountName, snapshot) {
    const mounts = Array.isArray(snapshot?.mounts) ? sortWorkspaceMounts(snapshot.mounts) : [];
    const normalizedCandidate = String(candidateMountName || '').trim();
    if (normalizedCandidate && mounts.some(mount => mount?.mountName === normalizedCandidate)) {
        return normalizedCandidate;
    }
    const defaultMountName = String(snapshot?.default_mount_name || '').trim();
    if (defaultMountName && mounts.some(mount => mount?.mountName === defaultMountName)) {
        return defaultMountName;
    }
    const firstMountName = String(mounts[0]?.mountName || '').trim();
    return firstMountName || null;
}

function resolveActiveMountName() {
    return currentSnapshot
        ? resolveWorkspaceMountName(currentMountName, currentSnapshot)
        : String(currentMountName || '').trim() || null;
}

function resolveCurrentMount(snapshot = currentSnapshot) {
    const mountName = resolveWorkspaceMountName(currentMountName, snapshot);
    if (!mountName) {
        return null;
    }
    const mounts = Array.isArray(snapshot?.mounts) ? snapshot.mounts : [];
    return mounts.find(mount => mount?.mountName === mountName) || null;
}

function createMountTreeRoot({ label = '.', hasChildren = false } = {}) {
    return {
        name: String(label || '.'),
        path: '.',
        kind: 'directory',
        hasChildren: hasChildren === true,
        children: [],
        childrenLoaded: false,
    };
}

function primeSnapshotMountTrees(snapshot) {
    if (!snapshot || typeof snapshot !== 'object') {
        return;
    }
    if (snapshot.isMountShell === true) {
        for (const mount of Array.isArray(snapshot.mounts) ? snapshot.mounts : []) {
            if (currentMountTrees.has(mount.mountName)) {
                continue;
            }
            currentMountTrees.set(
                mount.mountName,
                createMountTreeRoot({
                    label: mount.mountName,
                    hasChildren: mount.hasChildren === true,
                }),
            );
        }
        return;
    }
    const mount = resolveCurrentMount(snapshot);
    const normalizedTree = cloneTreeNode(snapshot.tree);
    if (!mount || !normalizedTree) {
        return;
    }
    const existingTree = currentMountTrees.get(mount.mountName);
    if (existingTree) {
        mergeTreeState(normalizedTree, existingTree);
    }
    currentMountTrees.set(mount.mountName, normalizedTree);
}

function ensureCurrentMountTree() {
    const mount = resolveCurrentMount();
    if (!mount) {
        return null;
    }
    if (!currentMountTrees.has(mount.mountName)) {
        currentMountTrees.set(
            mount.mountName,
            createMountTreeRoot({
                label: mount.mountName,
                hasChildren: mount.hasChildren === true,
            }),
        );
    }
    return currentMountTrees.get(mount.mountName) || null;
}

function getCurrentMountTree() {
    const mountName = resolveActiveMountName();
    if (!mountName) {
        return null;
    }
    return currentMountTrees.get(mountName) || null;
}

function ensureActiveMountTreeLoaded(loadToken) {
    const tree = ensureCurrentMountTree();
    if (!currentSnapshot || !currentWorkspace) {
        return;
    }
    if (tree?.childrenLoaded === true) {
        loadingTreePaths.delete(buildTreeStateKey('.'));
        treeLoadErrors.delete(buildTreeStateKey('.'));
        renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
        return;
    }
    loadingTreePaths.add(buildTreeStateKey('.'));
    treeLoadErrors.delete(buildTreeStateKey('.'));
    renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
    if (loadToken === currentLoadToken) {
        void loadWorkspaceTree('.');
    }
}

function buildTreeStateKey(path, mountName = resolveActiveMountName()) {
    const normalizedPath = String(path || '.').trim() || '.';
    const normalizedMountName = String(mountName || '').trim() || 'default';
    return `${normalizedMountName}:${normalizedPath}`;
}

function normalizeSnapshot(snapshot, workspace) {
    const isMountShell = isMountShellSnapshot(snapshot);
    const defaultMountName = String(
        snapshot?.default_mount_name
        || workspace?.default_mount_name
        || resolveWorkspaceInitialMountName(workspace)
        || 'default',
    ).trim() || 'default';
    const rootPath = String(snapshot?.root_path || snapshot?.default_mount_root || '').trim();
    const mounts = normalizeWorkspaceMounts(workspace, snapshot, defaultMountName, rootPath);
    return {
        workspace_id: String(snapshot?.workspace_id || workspace?.workspace_id || '').trim(),
        root_path: rootPath,
        default_mount_name: defaultMountName,
        isMountShell,
        mounts,
        tree: isMountShell ? null : normalizeTreeNode(snapshot?.tree, true),
    };
}

function normalizeTreeNode(node, childrenLoaded) {
    if (!node || typeof node !== 'object') {
        return null;
    }
    const isDirectory = node.kind === 'directory';
    const children = Array.isArray(node.children)
        ? node.children
            .map(child => normalizeTreeNode(child, false))
            .filter(Boolean)
        : [];
    return {
        name: String(node.name || node.path || '.'),
        path: String(node.path || '.').trim() || '.',
        kind: isDirectory ? 'directory' : 'file',
        hasChildren: node.has_children === true || node.hasChildren === true,
        children,
        childrenLoaded: childrenLoaded === true,
    };
}

function renderTree(tree) {
    if (!tree || typeof tree !== 'object') {
        return renderInlineState(t('workspace_view.loading_tree'));
    }
    const rootError = treeLoadErrors.get(buildTreeStateKey('.')) || '';
    if (tree.childrenLoaded !== true) {
        if (rootError) {
            return renderInlineState(rootError, 'is-error');
        }
        return renderInlineState(t('workspace_view.loading_tree'));
    }

    const children = Array.isArray(tree.children) ? tree.children : [];
    if (children.length === 0) {
        return renderInlineState(t('workspace_view.empty_tree'));
    }

    return `
        <div class="workspace-tree-root">
            ${children.map(child => renderTreeNode(child)).join('')}
        </div>
    `;
}

function renderTreeNode(node) {
    if (!node || typeof node !== 'object') {
        return '';
    }

    const nodePath = String(node.path || '.').trim() || '.';
    const nodeLabel = escapeHtml(node.name || node.path || '.');

    if (node.kind !== 'directory') {
        const isSelected = selectedTreePath === nodePath;
        return `
            <div class="workspace-tree-node is-file">
                <button
                    type="button"
                    class="workspace-tree-entry workspace-tree-file${isSelected ? ' is-selected' : ''}"
                    data-tree-file-path="${escapeHtml(nodePath)}"
                    aria-pressed="${isSelected ? 'true' : 'false'}"
                >
                    <span class="workspace-tree-chevron is-placeholder" aria-hidden="true"></span>
                    ${renderFileIcon()}
                    <span class="workspace-tree-label">${nodeLabel}</span>
                </button>
            </div>
        `;
    }

    const scopedPath = buildTreeStateKey(nodePath);
    const isExpanded = expandedTreePaths.has(scopedPath);
    const isLoading = loadingTreePaths.has(scopedPath);
    const loadError = treeLoadErrors.get(scopedPath) || '';
    return `
        <div class="workspace-tree-node is-directory">
            <button
                type="button"
                class="workspace-tree-toggle"
                data-tree-toggle-path="${escapeHtml(nodePath)}"
                aria-expanded="${isExpanded ? 'true' : 'false'}"
            >
                <span class="workspace-tree-chevron" aria-hidden="true">${isExpanded ? '&#9662;' : '&#9656;'}</span>
                ${renderFolderIcon(isExpanded)}
                <span class="workspace-tree-label">${nodeLabel}</span>
            </button>
            ${renderTreeChildren(node, { isExpanded, isLoading, loadError })}
        </div>
    `;
}

function renderTreeChildren(node, { isExpanded, isLoading, loadError }) {
    if (!isExpanded) {
        return '';
    }
    if (isLoading) {
        return `
            <div class="workspace-tree-children">
                ${renderTreePlaceholder(t('workspace_view.loading_directory'))}
            </div>
        `;
    }
    if (loadError) {
        return `
            <div class="workspace-tree-children">
                ${renderTreePlaceholder(loadError, 'is-error')}
            </div>
        `;
    }
    const children = Array.isArray(node.children) ? node.children : [];
    if (children.length === 0) {
        return '';
    }
    return `
        <div class="workspace-tree-children">
            ${children.map(child => renderTreeNode(child)).join('')}
        </div>
    `;
}

function renderTreePlaceholder(message, extraClass = '') {
    return `
        <div class="workspace-tree-placeholder ${extraClass}">
            <span>${escapeHtml(message)}</span>
        </div>
    `;
}

function renderDiffSection() {
    if (currentDiffState.status === 'loading') {
        return renderInlineState(t('workspace_view.loading_diffs'));
    }
    if (currentDiffState.status === 'error') {
        return renderInlineState(currentDiffState.diffMessage || t('workspace_view.load_failed'), 'is-error');
    }
    if (currentDiffState.status !== 'ready') {
        return renderInlineState(t('workspace_view.loading_diffs'));
    }
    if (currentDiffState.isGitRepository !== true) {
        return renderInlineState(currentDiffState.diffMessage || t('workspace_view.not_git_repository'));
    }
    if (currentDiffState.diffMessage) {
        return renderInlineState(currentDiffState.diffMessage, 'is-error');
    }
    if (currentDiffState.diffFiles.length === 0) {
        return renderInlineState(t('workspace_view.no_diffs'));
    }
    return `
        <div class="workspace-diff-list">
            ${currentDiffState.diffFiles.map(file => renderDiffFile(file)).join('')}
        </div>
    `;
}

function renderDiffFile(file) {
    const changeType = String(file?.change_type || '').trim() || 'modified';
    const changeLabel = t(`workspace_view.change.${changeType}`);
    const previousPath = String(file?.previous_path || '').trim();
    const filePath = String(file?.path || '').trim();
    const isSelected = filePath && selectedTreePath === filePath;
    const diffBody = renderDiffBody(filePath, isSelected);
    return `
        <article
            class="workspace-diff-card${isSelected ? ' is-selected' : ''}${diffBody ? ' has-body' : ''}"
            data-diff-path="${escapeHtml(filePath)}"
        >
            <div class="workspace-diff-header">
                <span class="workspace-diff-status is-${escapeHtml(changeType)}">${escapeHtml(changeLabel)}</span>
                <code class="workspace-diff-path">${escapeHtml(filePath)}</code>
                ${previousPath ? `<span class="workspace-diff-previous">${escapeHtml(previousPath)} -> ${escapeHtml(filePath)}</span>` : ''}
            </div>
            ${diffBody}
        </article>
    `;
}

function renderDiffBody(filePath, isSelected) {
    if (!isSelected) {
        return '';
    }
    if (currentDiffState.loadingFilePaths.has(filePath)) {
        return renderDiffBodyState(t('workspace_view.loading_diff'));
    }
    const loadError = currentDiffState.fileErrors.get(filePath);
    if (loadError) {
        return renderDiffBodyState(loadError, 'is-error');
    }
    const diffFile = currentDiffState.loadedDiffs.get(filePath);
    if (!diffFile) {
        return renderDiffBodyState(t('workspace_view.loading_diff'));
    }
    if (diffFile.is_binary === true) {
        return renderDiffBodyState(t('workspace_view.binary_diff'));
    }
    const diffText = String(diffFile.diff || '').replace(/\r\n/g, '\n');
    if (!diffText.trim()) {
        return renderDiffBodyState(t('workspace_view.empty_diff'));
    }
    return renderStructuredDiff(diffText);
}

function renderStructuredDiff(diffText) {
    const segments = parseDiffSegments(diffText);
    if (segments.length === 0) {
        return `
            <pre class="workspace-diff-pre"><code>${escapeHtml(diffText)}</code></pre>
        `;
    }
    return `
        <div class="workspace-diff-view">
            ${segments.map(renderDiffSegment).join('')}
        </div>
    `;
}

function parseDiffSegments(diffText) {
    const lines = String(diffText || '').split('\n');
    const segments = [];
    let currentSegment = null;
    let oldLine = 0;
    let newLine = 0;

    for (const line of lines) {
        if (line.startsWith('@@')) {
            const match = /@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(.*)/.exec(line);
            oldLine = Number(match?.[1] || 0);
            newLine = Number(match?.[3] || 0);
            currentSegment = {
                header: line,
                rows: [],
            };
            segments.push(currentSegment);
            continue;
        }

        if (!currentSegment) {
            currentSegment = {
                header: null,
                rows: [],
            };
            segments.push(currentSegment);
        }

        let kind = 'meta';
        let marker = '';
        let content = line;
        let oldNumber = '';
        let newNumber = '';

        if (line.startsWith('+') && !line.startsWith('+++')) {
            kind = 'added';
            marker = '+';
            content = line.slice(1);
            newNumber = String(newLine);
            newLine += 1;
        } else if (line.startsWith('-') && !line.startsWith('---')) {
            kind = 'deleted';
            marker = '-';
            content = line.slice(1);
            oldNumber = String(oldLine);
            oldLine += 1;
        } else if (line.startsWith(' ')) {
            kind = 'context';
            marker = ' ';
            content = line.slice(1);
            oldNumber = String(oldLine);
            newNumber = String(newLine);
            oldLine += 1;
            newLine += 1;
        } else if (line.startsWith('\\')) {
            kind = 'note';
            marker = '\\';
        }

        currentSegment.rows.push({
            kind,
            marker,
            content,
            oldNumber,
            newNumber,
        });
    }

    return segments;
}

function renderDiffSegment(segment) {
    const header = segment?.header
        ? `<div class="workspace-diff-hunk-header">${escapeHtml(segment.header)}</div>`
        : '';
    const rows = Array.isArray(segment?.rows) ? segment.rows.map(renderDiffRow).join('') : '';
    return `
        <section class="workspace-diff-hunk">
            ${header}
            <div class="workspace-diff-grid" role="table">
                ${rows}
            </div>
        </section>
    `;
}

function renderDiffRow(row) {
    const kind = String(row?.kind || 'context');
    return `
        <div class="workspace-diff-row is-${escapeHtml(kind)}" role="row">
            <span class="workspace-diff-line-number" role="cell">${escapeHtml(row?.oldNumber || '')}</span>
            <span class="workspace-diff-line-number" role="cell">${escapeHtml(row?.newNumber || '')}</span>
            <span class="workspace-diff-line-marker" role="cell">${escapeHtml(row?.marker || '')}</span>
            <code class="workspace-diff-line-text" role="cell">${escapeHtml(row?.content || '')}</code>
        </div>
    `;
}

function renderDiffBodyState(message, extraClass = '') {
    return `
        <div class="workspace-diff-body-state ${extraClass}">
            <p>${escapeHtml(message)}</p>
        </div>
    `;
}

function renderInlineState(message, extraClass = '') {
    return `
        <div class="workspace-view-empty-state ${extraClass}">
            <p>${escapeHtml(message)}</p>
        </div>
    `;
}

function bindWorkspaceHeaderInteractions() {
    if (!els.projectViewContent) {
        return;
    }
    for (const mountButton of els.projectViewContent.querySelectorAll('[data-workspace-mount]')) {
        const mountName = String(mountButton.getAttribute('data-workspace-mount') || '').trim();
        mountButton.onclick = () => {
            void switchWorkspaceMount(mountName);
        };
    }
    const addMountButton = els.projectViewContent?.querySelector('[data-workspace-add-mount]');
    if (addMountButton) {
        addMountButton.onclick = () => {
            void handleAddWorkspaceMount();
        };
    }
    const editMountButton = els.projectViewContent?.querySelector('[data-workspace-edit-mount]');
    if (editMountButton) {
        editMountButton.onclick = () => {
            void handleEditWorkspaceMount();
        };
    }
    const deleteMountButton = els.projectViewContent?.querySelector('[data-workspace-delete-mount]');
    if (deleteMountButton) {
        deleteMountButton.onclick = () => {
            void handleDeleteWorkspaceMount();
        };
    }
    const openSettingsButton = els.projectViewContent?.querySelector('[data-workspace-open-settings]');
    if (openSettingsButton) {
        openSettingsButton.onclick = () => {
            handleOpenWorkspaceSettings();
        };
    }
    const openRootButton = els.projectViewContent?.querySelector('[data-open-workspace-root]');
    if (!openRootButton) {
        return;
    }
    openRootButton.onclick = () => {
        void handleOpenWorkspaceRoot();
    };
}

function bindTreeInteractions() {
    if (!els.projectViewContent || typeof els.projectViewContent.querySelectorAll !== 'function') {
        return;
    }

    for (const toggle of els.projectViewContent.querySelectorAll('.workspace-tree-toggle')) {
        const togglePath = String(toggle.getAttribute('data-tree-toggle-path') || '').trim();
        toggle.onclick = () => {
            void toggleTreePath(togglePath);
        };
        toggle.onkeydown = (event) => {
            if (event?.key === 'Enter' || event?.key === ' ' || event?.key === 'Spacebar') {
                event.preventDefault?.();
                void toggleTreePath(togglePath);
            }
        };
    }

    for (const fileEntry of els.projectViewContent.querySelectorAll('.workspace-tree-file')) {
        const filePath = String(fileEntry.getAttribute('data-tree-file-path') || '').trim();
        fileEntry.onclick = () => {
            void selectTreePath(filePath);
        };
        fileEntry.onkeydown = (event) => {
            if (event?.key === 'Enter' || event?.key === ' ' || event?.key === 'Spacebar') {
                event.preventDefault?.();
                void selectTreePath(filePath);
            }
        };
    }
}

function bindDiffInteractions() {
    if (!els.projectViewContent || typeof els.projectViewContent.querySelectorAll !== 'function') {
        return;
    }

    for (const diffCard of els.projectViewContent.querySelectorAll('.workspace-diff-card')) {
        const diffPath = String(diffCard.getAttribute('data-diff-path') || '').trim();
        diffCard.onclick = () => {
            void selectTreePath(diffPath);
        };
    }
}

async function handleOpenWorkspaceRoot() {
    const workspaceId = String(currentWorkspace?.workspace_id || '').trim();
    const mountName = resolveActiveMountName();
    if (!workspaceId) {
        return;
    }
    try {
        await openWorkspaceRoot(workspaceId, mountName);
    } catch (error) {
        showToast({
            title: t('workspace_view.open_root_failed'),
            message: String(error?.message || error || ''),
            tone: 'danger',
        });
    }
}

function handleOpenWorkspaceSettings() {
    const openSettingsHandler = globalThis.window?.openSettings || globalThis.openSettings;
    if (typeof openSettingsHandler === 'function') {
        openSettingsHandler('workspace');
        return;
    }
    showToast({
        title: t('workspace_view.mount_profiles'),
        message: t('workspace_view.mount_profiles_unavailable'),
        tone: 'warning',
    });
}

async function handleAddWorkspaceMount() {
    if (!currentWorkspace) {
        return;
    }
    const sshProfiles = await loadWorkspaceSshProfiles();
    await showFormDialog({
        title: t('workspace_view.mount_add'),
        message: t('workspace_view.mount_dialog_add'),
        tone: 'info',
        confirmLabel: t('settings.action.save'),
        cancelLabel: t('settings.action.cancel'),
        fields: buildWorkspaceMountDialogFields({
            sshProfiles,
            defaultMountName: String(currentWorkspace.default_mount_name || '').trim(),
        }),
        submitHandler: async values => submitWorkspaceMountChange({
            mode: 'create',
            values,
        }),
    });
}

async function handleEditWorkspaceMount() {
    const activeMount = resolveCurrentMount();
    if (!currentWorkspace || !activeMount) {
        return;
    }
    const sshProfiles = await loadWorkspaceSshProfiles();
    await showFormDialog({
        title: t('workspace_view.mount_edit'),
        message: formatMessage('workspace_view.mount_dialog_edit', {
            mount: activeMount.mountName,
        }),
        tone: 'info',
        confirmLabel: t('settings.action.save'),
        cancelLabel: t('settings.action.cancel'),
        fields: buildWorkspaceMountDialogFields({
            mount: activeMount,
            sshProfiles,
            defaultMountName: String(currentWorkspace.default_mount_name || '').trim(),
        }),
        submitHandler: async values => submitWorkspaceMountChange({
            mode: 'edit',
            values,
            sourceMountName: activeMount.mountName,
        }),
    });
}

async function handleDeleteWorkspaceMount() {
    if (!currentWorkspace) {
        return;
    }
    const activeMount = resolveCurrentMount();
    const mounts = Array.isArray(currentWorkspace.mounts) ? currentWorkspace.mounts : [];
    if (!activeMount) {
        return;
    }
    if (mounts.length <= 1) {
        showToast({
            title: t('workspace_view.mount_remove_failed'),
            message: t('workspace_view.mount_remove_last'),
            tone: 'warning',
        });
        return;
    }
    const confirmed = await showConfirmDialog({
        title: t('workspace_view.mount_remove'),
        message: formatMessage('workspace_view.mount_remove_confirm', {
            mount: activeMount.mountName,
        }),
        tone: 'warning',
        confirmLabel: t('settings.action.delete'),
        cancelLabel: t('settings.action.cancel'),
    });
    if (confirmed !== true) {
        return;
    }
    const nextMounts = sortWorkspaceMounts(
        mounts.filter(mount => String(mount?.mount_name || '').trim() !== activeMount.mountName),
    );
    const nextDefaultMountName = resolveUpdatedDefaultMountName({
        nextMounts,
        requestedDefaultMountName: String(currentWorkspace.default_mount_name || '').trim(),
        removedMountName: activeMount.mountName,
    });
    try {
        const updatedWorkspace = await updateWorkspace(String(currentWorkspace.workspace_id || '').trim(), {
            default_mount_name: nextDefaultMountName,
            mounts: nextMounts,
        });
        await applyUpdatedWorkspaceRecord(updatedWorkspace, nextDefaultMountName);
        showToast({
            title: t('workspace_view.mount_removed_title'),
            message: formatMessage('workspace_view.mount_removed_detail', {
                mount: activeMount.mountName,
            }),
            tone: 'success',
        });
    } catch (error) {
        showToast({
            title: t('workspace_view.mount_remove_failed'),
            message: String(error?.message || error || ''),
            tone: 'danger',
        });
    }
}

async function loadWorkspaceSshProfiles() {
    try {
        const loadedProfiles = await fetchSshProfiles();
        return Array.isArray(loadedProfiles) ? loadedProfiles : [];
    } catch (error) {
        showToast({
            title: t('workspace_view.mount_profiles_failed'),
            message: String(error?.message || error || ''),
            tone: 'danger',
        });
        return [];
    }
}

function buildWorkspaceMountDialogFields({
    mount = null,
    sshProfiles = [],
    defaultMountName = '',
} = {}) {
    const provider = String(mount?.provider || 'local').trim() || 'local';
    const isLocal = provider === 'local';
    const sshProfileId = String(mount?.sshProfileId || '').trim();
    const localRoot = isLocal ? String(mount?.rootReference || '').trim() : '';
    const remoteRoot = provider === 'ssh' ? String(mount?.rootReference || '').trim() : '';
    const sshProfileOptions = [
        {
            value: '',
            label: t('workspace_view.mount_profile_select_placeholder'),
        },
        ...sshProfiles.map(profile => {
            const sshProfileValue = String(profile?.ssh_profile_id || '').trim();
            return {
                value: sshProfileValue,
                label: sshProfileValue,
            };
        }).filter(option => option.value),
    ];
    return [
        {
            id: 'mount_name',
            label: t('workspace_view.mount_field_name'),
            type: 'text',
            value: String(mount?.mountName || '').trim(),
            placeholder: t('workspace_view.mount_field_name_placeholder'),
        },
        {
            id: 'provider',
            label: t('workspace_view.mount_field_provider'),
            type: 'select',
            value: provider,
            options: [
                { value: 'local', label: t('workspace_view.mount_provider.local') },
                { value: 'ssh', label: t('workspace_view.mount_provider.ssh') },
            ],
        },
        {
            id: 'local_root_path',
            label: t('workspace_view.mount_field_local_root'),
            type: 'text',
            value: localRoot,
            placeholder: t('workspace_view.mount_field_local_root_placeholder'),
            description: t('workspace_view.mount_field_local_root_copy'),
            visibleWhen: {
                field: 'provider',
                equals: 'local',
            },
        },
        {
            id: 'ssh_profile_id',
            label: t('workspace_view.mount_field_ssh_profile'),
            type: 'select',
            value: sshProfileId,
            options: sshProfileOptions,
            description: t('workspace_view.mount_field_ssh_profile_copy'),
            visibleWhen: {
                field: 'provider',
                equals: 'ssh',
            },
        },
        {
            id: 'remote_root',
            label: t('workspace_view.mount_field_remote_root'),
            type: 'text',
            value: remoteRoot,
            placeholder: t('workspace_view.mount_field_remote_root_placeholder'),
            description: t('workspace_view.mount_field_remote_root_copy'),
            visibleWhen: {
                field: 'provider',
                equals: 'ssh',
            },
        },
        {
            id: 'set_default',
            label: t('workspace_view.mount_field_default'),
            type: 'checkbox',
            value: String(mount?.mountName || '').trim()
                ? String(mount.mountName).trim() === defaultMountName
                : provider === 'local' && !defaultMountName,
            description: t('workspace_view.mount_field_default_copy'),
            visibleWhen: {
                field: 'provider',
                equals: 'local',
            },
        },
    ];
}

async function submitWorkspaceMountChange({
    mode,
    values,
    sourceMountName = '',
} = {}) {
    if (!currentWorkspace) {
        return null;
    }
    const workspaceId = String(currentWorkspace.workspace_id || '').trim();
    const existingMounts = Array.isArray(currentWorkspace.mounts) ? currentWorkspace.mounts : [];
    const normalizedSourceMountName = String(sourceMountName || '').trim();
    const existingMount = normalizedSourceMountName
        ? existingMounts.find(mount => String(mount?.mount_name || '').trim() === normalizedSourceMountName) || null
        : null;
    const nextMountRecord = buildWorkspaceMountRecordFromValues(values, {
        existingMount,
        mode,
    });
    validateWorkspaceMountSubmission({
        mount: nextMountRecord,
        mode,
        sourceMountName: normalizedSourceMountName,
        existingMounts,
    });
    const nextMounts = sortWorkspaceMounts(mode === 'edit'
        ? existingMounts.map(mount => {
            return String(mount?.mount_name || '').trim() === normalizedSourceMountName ? nextMountRecord : mount;
        })
        : [...existingMounts, nextMountRecord]);
    const nextDefaultMountName = resolveUpdatedDefaultMountName({
        nextMounts,
        requestedDefaultMountName: values?.set_default === true && String(nextMountRecord?.provider || '').trim() === 'local'
            ? nextMountRecord.mount_name
            : String(currentWorkspace.default_mount_name || '').trim(),
        removedMountName: mode === 'edit' ? normalizedSourceMountName : '',
        replacementMountName: nextMountRecord.mount_name,
    });
    const updatedWorkspace = await updateWorkspace(workspaceId, {
        default_mount_name: nextDefaultMountName,
        mounts: nextMounts,
    });
    await applyUpdatedWorkspaceRecord(updatedWorkspace, nextMountRecord.mount_name);
    showToast({
        title: mode === 'edit' ? t('workspace_view.mount_updated_title') : t('workspace_view.mount_added_title'),
        message: formatMessage(
            mode === 'edit' ? 'workspace_view.mount_updated_detail' : 'workspace_view.mount_added_detail',
            { mount: nextMountRecord.mount_name },
        ),
        tone: 'success',
    });
    return updatedWorkspace;
}

function buildWorkspaceMountRecordFromValues(values, {
    existingMount = null,
    mode = 'create',
} = {}) {
    const mountName = String(values?.mount_name || '').trim();
    const provider = String(values?.provider || 'local').trim() || 'local';
    const baseRecord = buildWorkspaceMountBaseRecord({
        existingMount,
        nextProvider: provider,
        mode,
    });
    if (provider === 'ssh') {
        return {
            ...baseRecord,
            mount_name: mountName,
            provider: 'ssh',
            provider_config: {
                ssh_profile_id: String(values?.ssh_profile_id || '').trim(),
                remote_root: String(values?.remote_root || '').trim(),
            },
        };
    }
    return {
        ...baseRecord,
        mount_name: mountName,
        provider: 'local',
        provider_config: {
            root_path: String(values?.local_root_path || '').trim(),
        },
    };
}

function buildWorkspaceMountBaseRecord({
    existingMount = null,
    nextProvider = '',
    mode = 'create',
} = {}) {
    if (mode !== 'edit' || !existingMount || typeof existingMount !== 'object') {
        return {};
    }
    const existingProvider = String(existingMount.provider || '').trim();
    const providerUnchanged = existingProvider === nextProvider;
    const nextRecord = {};
    if (typeof existingMount.working_directory === 'string') {
        nextRecord.working_directory = existingMount.working_directory;
    }
    if (Array.isArray(existingMount.readable_paths)) {
        nextRecord.readable_paths = [...existingMount.readable_paths];
    }
    if (Array.isArray(existingMount.writable_paths)) {
        nextRecord.writable_paths = [...existingMount.writable_paths];
    }
    if (providerUnchanged && existingMount.capabilities && typeof existingMount.capabilities === 'object') {
        nextRecord.capabilities = { ...existingMount.capabilities };
    }
    if (nextProvider === 'local') {
        for (const key of ['branch_name', 'source_root_path', 'forked_from_workspace_id']) {
            if (typeof existingMount[key] === 'string' && existingMount[key].trim()) {
                nextRecord[key] = existingMount[key];
            }
        }
    }
    return nextRecord;
}

function validateWorkspaceMountSubmission({
    mount,
    mode,
    sourceMountName = '',
    existingMounts = [],
} = {}) {
    const mountName = String(mount?.mount_name || '').trim();
    const provider = String(mount?.provider || '').trim();
    const normalizedSourceMountName = String(sourceMountName || '').trim();
    if (!mountName) {
        throw new Error(t('workspace_view.mount_validation_name'));
    }
    const duplicateMount = existingMounts.find(existingMount => {
        const existingMountName = String(existingMount?.mount_name || '').trim();
        if (!existingMountName) {
            return false;
        }
        if (mode === 'edit' && existingMountName === normalizedSourceMountName) {
            return false;
        }
        return existingMountName === mountName;
    });
    if (duplicateMount) {
        throw new Error(formatMessage('workspace_view.mount_validation_duplicate', { mount: mountName }));
    }
    if (provider === 'ssh') {
        const sshProfileId = String(mount?.provider_config?.ssh_profile_id || '').trim();
        const remoteRoot = String(mount?.provider_config?.remote_root || '').trim();
        if (!sshProfileId) {
            throw new Error(t('workspace_view.mount_validation_ssh_profile'));
        }
        if (!remoteRoot) {
            throw new Error(t('workspace_view.mount_validation_remote_root'));
        }
        return;
    }
    const localRootPath = String(mount?.provider_config?.root_path || '').trim();
    if (!localRootPath) {
        throw new Error(t('workspace_view.mount_validation_local_root'));
    }
}

function resolveUpdatedDefaultMountName({
    nextMounts = [],
    requestedDefaultMountName = '',
    removedMountName = '',
    replacementMountName = '',
} = {}) {
    const orderedMounts = sortWorkspaceMounts(nextMounts);
    const normalizedRequested = String(requestedDefaultMountName || '').trim();
    const normalizedRemoved = String(removedMountName || '').trim();
    const normalizedReplacement = String(replacementMountName || '').trim();
    const nextMountNames = orderedMounts
        .map(mount => String(mount?.mount_name || '').trim())
        .filter(Boolean);
    const requestedMount = findWorkspaceMountByName(orderedMounts, normalizedRequested);
    if (requestedMount && isLocalWorkspaceMount(requestedMount)) {
        return normalizedRequested;
    }
    const replacementMount = findWorkspaceMountByName(orderedMounts, normalizedReplacement);
    if (
        normalizedRequested
        && normalizedRemoved
        && normalizedRequested === normalizedRemoved
        && replacementMount
        && isLocalWorkspaceMount(replacementMount)
    ) {
        return normalizedReplacement;
    }
    const firstLocalMount = orderedMounts.find(mount => isLocalWorkspaceMount(mount)) || null;
    if (firstLocalMount) {
        return String(firstLocalMount.mount_name || '').trim() || 'default';
    }
    return nextMountNames[0] || 'default';
}

function findWorkspaceMountByName(mounts = [], mountName = '') {
    const normalizedMountName = String(mountName || '').trim();
    if (!normalizedMountName) {
        return null;
    }
    return mounts.find(mount => String(mount?.mount_name || '').trim() === normalizedMountName) || null;
}

function isLocalWorkspaceMount(mount) {
    return String(mount?.provider || '').trim() === 'local';
}

async function applyUpdatedWorkspaceRecord(updatedWorkspace, preferredMountName = '') {
    const orderedWorkspace = normalizeWorkspaceRecordMountOrder(updatedWorkspace);
    const workspaceId = String(orderedWorkspace?.workspace_id || '').trim();
    if (!workspaceId) {
        return;
    }
    currentWorkspace = orderedWorkspace;
    currentSnapshotWorkspaceId = workspaceId;
    state.currentWorkspaceId = workspaceId;
    workspaceViewCache.delete(workspaceId);
    resetProjectViewState(workspaceId);
    currentMountName = String(preferredMountName || orderedWorkspace.default_mount_name || '').trim() || resolveWorkspaceInitialMountName(orderedWorkspace);
    currentDiffState = {
        ...createInitialDiffState(),
        status: 'loading',
        mountName: currentMountName,
    };
    renderLoadingState(orderedWorkspace);
    const loadToken = ++currentLoadToken;
    void loadWorkspaceSnapshot(workspaceId, loadToken);
    void loadWorkspaceDiffs(workspaceId, loadToken);
}

async function switchWorkspaceMount(mountName) {
    const nextMountName = resolveWorkspaceMountName(mountName, currentSnapshot);
    if (!nextMountName || nextMountName === resolveActiveMountName() || !currentWorkspace || !currentSnapshot) {
        return;
    }
    currentMountName = nextMountName;
    selectedTreePath = null;
    currentDiffState = {
        ...createInitialDiffState(),
        status: 'loading',
        mountName: nextMountName,
    };
    const loadToken = ++currentLoadToken;
    ensureActiveMountTreeLoaded(loadToken);
    cacheProjectViewState();
    void loadWorkspaceDiffs(String(currentWorkspace.workspace_id || '').trim(), loadToken);
}

async function toggleTreePath(path) {
    if (!path || !currentWorkspace || !currentSnapshot) {
        return;
    }
    const scopedPath = buildTreeStateKey(path);

    if (expandedTreePaths.has(scopedPath)) {
        expandedTreePaths.delete(scopedPath);
        renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
        cacheProjectViewState();
        return;
    }

    expandedTreePaths.add(scopedPath);
    treeLoadErrors.delete(scopedPath);
    const node = findTreeNode(getCurrentMountTree(), path);
    if (node?.kind === 'directory' && node.hasChildren && node.childrenLoaded !== true) {
        loadingTreePaths.add(scopedPath);
        renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
        await loadWorkspaceTree(path);
        return;
    }
    renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
    cacheProjectViewState();
}

async function loadWorkspaceTree(path) {
    if (!currentWorkspace || !currentSnapshot) {
        return;
    }
    const workspaceId = String(currentWorkspace.workspace_id || '').trim();
    const mountName = resolveActiveMountName();
    const loadToken = currentLoadToken;
    try {
        const listing = await fetchWorkspaceTree(workspaceId, path, mountName);
        if (loadToken !== currentLoadToken || workspaceId !== currentSnapshotWorkspaceId || !currentSnapshot) {
            return;
        }
        const node = findTreeNode(getCurrentMountTree(), path);
        if (node) {
            node.children = Array.isArray(listing?.children)
                ? listing.children
                    .map(child => normalizeTreeNode(child, false))
                    .filter(Boolean)
                : [];
            node.childrenLoaded = true;
        }
        loadingTreePaths.delete(buildTreeStateKey(path, mountName));
        treeLoadErrors.delete(buildTreeStateKey(path, mountName));
        renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
        cacheProjectViewState();
    } catch (error) {
        if (loadToken !== currentLoadToken || workspaceId !== currentSnapshotWorkspaceId) {
            return;
        }
        loadingTreePaths.delete(buildTreeStateKey(path, mountName));
        treeLoadErrors.set(
            buildTreeStateKey(path, mountName),
            String(error?.message || error || t('workspace_view.load_failed')),
        );
        renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
        cacheProjectViewState();
        sysLog(`Failed to load project tree path ${path}: ${error?.message || error}`, 'log-error');
    }
}

async function selectTreePath(path) {
    if (!path || !currentWorkspace || !currentSnapshot) {
        return;
    }

    await revealTreePath(path);
    selectedTreePath = path;
    renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
    cacheProjectViewState();
    if (findDiffSummary(path)) {
        void ensureDiffFileLoaded(path);
    }
}

function findDiffSummary(path) {
    const normalizedPath = String(path || '').trim();
    if (!normalizedPath || currentDiffState.status !== 'ready') {
        return null;
    }
    return currentDiffState.diffFiles.find(file => String(file?.path || '').trim() === normalizedPath) || null;
}

function ensureDiffFileLoaded(path) {
    const normalizedPath = String(path || '').trim();
    if (!normalizedPath || currentDiffState.status !== 'ready') {
        return;
    }
    if (currentDiffState.loadedDiffs.has(normalizedPath) || currentDiffState.loadingFilePaths.has(normalizedPath)) {
        return;
    }
    currentDiffState.fileErrors.delete(normalizedPath);
    currentDiffState.loadingFilePaths.add(normalizedPath);
    renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
    cacheProjectViewState();
    void loadWorkspaceDiffFile(normalizedPath);
}

async function loadWorkspaceDiffFile(path) {
    if (!currentWorkspace || currentDiffState.status !== 'ready') {
        return;
    }

    const workspaceId = String(currentWorkspace.workspace_id || '').trim();
    const mountName = resolveActiveMountName();
    const loadToken = currentLoadToken;
    try {
        const diffFile = await fetchWorkspaceDiffFile(workspaceId, path, mountName);
        if (loadToken !== currentLoadToken || workspaceId !== currentSnapshotWorkspaceId || currentDiffState.status !== 'ready') {
            return;
        }
        currentDiffState.loadingFilePaths.delete(path);
        currentDiffState.fileErrors.delete(path);
        currentDiffState.loadedDiffs.set(path, diffFile);
        renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
        cacheProjectViewState();
    } catch (error) {
        if (loadToken !== currentLoadToken || workspaceId !== currentSnapshotWorkspaceId || currentDiffState.status !== 'ready') {
            return;
        }
        currentDiffState.loadingFilePaths.delete(path);
        currentDiffState.fileErrors.set(path, String(error?.message || error || t('workspace_view.load_failed')));
        renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
        cacheProjectViewState();
        sysLog(`Failed to load project diff file ${path}: ${error?.message || error}`, 'log-error');
    }
}

async function revealTreePath(path) {
    if (!currentSnapshot || !currentWorkspace) {
        return;
    }
    const parentPaths = buildParentPaths(path);
    for (const parentPath of parentPaths) {
        expandedTreePaths.add(buildTreeStateKey(parentPath));
        const node = findTreeNode(getCurrentMountTree(), parentPath);
        if (node?.kind === 'directory' && node.hasChildren && node.childrenLoaded !== true) {
            loadingTreePaths.add(buildTreeStateKey(parentPath));
            renderWorkspaceSnapshot(currentWorkspace, currentSnapshot);
            await loadWorkspaceTree(parentPath);
        }
    }
    cacheProjectViewState();
}

function buildParentPaths(path) {
    const normalizedPath = String(path || '').trim();
    if (!normalizedPath || normalizedPath === '.') {
        return [];
    }
    const segments = normalizedPath.split('/');
    const parentPaths = [];
    let currentPath = '';
    for (const segment of segments.slice(0, -1)) {
        currentPath = currentPath ? `${currentPath}/${segment}` : segment;
        parentPaths.push(currentPath);
    }
    return parentPaths;
}

function findTreeNode(node, targetPath) {
    if (!node || typeof node !== 'object') {
        return null;
    }
    if (String(node.path || '.').trim() === targetPath) {
        return node;
    }
    const children = Array.isArray(node.children) ? node.children : [];
    for (const child of children) {
        const match = findTreeNode(child, targetPath);
        if (match) {
            return match;
        }
    }
    return null;
}

function mergeTreeState(nextNode, cachedNode) {
    if (!nextNode || !cachedNode || nextNode.kind !== 'directory' || cachedNode.kind !== 'directory') {
        return;
    }

    if (nextNode.childrenLoaded !== true && cachedNode.childrenLoaded === true) {
        nextNode.children = Array.isArray(cachedNode.children)
            ? cachedNode.children.map(child => cloneTreeNode(child)).filter(Boolean)
            : [];
        nextNode.childrenLoaded = true;
        nextNode.hasChildren = nextNode.hasChildren || nextNode.children.length > 0;
        return;
    }

    if (!Array.isArray(nextNode.children) || !Array.isArray(cachedNode.children)) {
        return;
    }

    const cachedChildrenByPath = new Map(
        cachedNode.children
            .filter(Boolean)
            .map(child => [String(child.path || '').trim(), child]),
    );

    for (const child of nextNode.children) {
        const childPath = String(child?.path || '').trim();
        const cachedChild = cachedChildrenByPath.get(childPath);
        if (cachedChild) {
            mergeTreeState(child, cachedChild);
        }
    }
}

function filterLoadedDiffs(loadedDiffs, diffFiles) {
    const nextLoadedDiffs = new Map();
    const safeLoadedDiffs = loadedDiffs instanceof Map ? loadedDiffs : new Map();
    for (const file of Array.isArray(diffFiles) ? diffFiles : []) {
        const filePath = String(file?.path || '').trim();
        if (!filePath || !safeLoadedDiffs.has(filePath)) {
            continue;
        }
        nextLoadedDiffs.set(filePath, cloneDiffFile(safeLoadedDiffs.get(filePath)));
    }
    return nextLoadedDiffs;
}

function filterFileErrors(fileErrors, diffFiles) {
    const nextFileErrors = new Map();
    const safeFileErrors = fileErrors instanceof Map ? fileErrors : new Map();
    for (const file of Array.isArray(diffFiles) ? diffFiles : []) {
        const filePath = String(file?.path || '').trim();
        if (!filePath || !safeFileErrors.has(filePath)) {
            continue;
        }
        nextFileErrors.set(filePath, String(safeFileErrors.get(filePath) || ''));
    }
    return nextFileErrors;
}

function cloneSnapshot(snapshot) {
    if (!snapshot || typeof snapshot !== 'object') {
        return null;
    }
    return {
        workspace_id: String(snapshot.workspace_id || ''),
        root_path: String(snapshot.root_path || ''),
        default_mount_name: String(snapshot.default_mount_name || 'default'),
        isMountShell: snapshot.isMountShell === true,
        mounts: Array.isArray(snapshot.mounts)
            ? snapshot.mounts.map(mount => ({
                mountName: String(mount?.mountName || '').trim(),
                provider: String(mount?.provider || '').trim() || 'unknown',
                rootReference: String(mount?.rootReference || '').trim(),
                sshProfileId: String(mount?.sshProfileId || '').trim(),
                isDefault: mount?.isDefault === true,
                hasChildren: mount?.hasChildren === true,
            }))
            : [],
        tree: cloneTreeNode(snapshot.tree),
    };
}

function cloneTreeNode(node) {
    if (!node || typeof node !== 'object') {
        return null;
    }
    return {
        name: String(node.name || node.path || '.'),
        path: String(node.path || '.').trim() || '.',
        kind: node.kind === 'directory' ? 'directory' : 'file',
        hasChildren: node.hasChildren === true,
        children: Array.isArray(node.children)
            ? node.children.map(child => cloneTreeNode(child)).filter(Boolean)
            : [],
        childrenLoaded: node.childrenLoaded === true,
    };
}

function cloneDiffState(diffState) {
    if (!diffState || typeof diffState !== 'object') {
        return createInitialDiffState();
    }
    return {
        status: String(diffState.status || 'idle'),
        mountName: diffState.mountName ? String(diffState.mountName) : null,
        diffFiles: Array.isArray(diffState.diffFiles)
            ? diffState.diffFiles.map(file => ({ ...file }))
            : [],
        diffMessage: diffState.diffMessage ? String(diffState.diffMessage) : null,
        isGitRepository: diffState.isGitRepository === true,
        gitRootPath: diffState.gitRootPath ? String(diffState.gitRootPath) : null,
        loadedDiffs: new Map(
            Array.from(diffState.loadedDiffs instanceof Map ? diffState.loadedDiffs.entries() : [])
                .map(([path, file]) => [String(path || '').trim(), cloneDiffFile(file)]),
        ),
        loadingFilePaths: new Set(),
        fileErrors: new Map(
            Array.from(diffState.fileErrors instanceof Map ? diffState.fileErrors.entries() : [])
                .map(([path, message]) => [String(path || '').trim(), String(message || '')]),
        ),
    };
}

function cloneDiffFile(diffFile) {
    if (!diffFile || typeof diffFile !== 'object') {
        return null;
    }
    return {
        ...diffFile,
        workspace_id: String(diffFile.workspace_id || ''),
        path: String(diffFile.path || ''),
        previous_path: diffFile.previous_path ? String(diffFile.previous_path) : null,
        change_type: String(diffFile.change_type || 'modified'),
        diff: diffFile.diff ? String(diffFile.diff) : '',
        is_binary: diffFile.is_binary === true,
    };
}


function describeCronExpression(expression) {
    const cron = String(expression || '').trim();
    if (!cron) {
        return t('automation.cron.empty');
    }
    const parts = cron.split(/\s+/);
    if (parts.length !== 5) {
        return formatTemplate(t('automation.cron.fallback'), { expression: cron });
    }
    const [minute, hour, dayOfMonth, month, dayOfWeek] = parts;
    if (month === '*' && dayOfMonth === '*' && dayOfWeek === '*') {
        return formatTemplate(t('automation.cron.daily'), {
            time: formatCronTime(hour, minute),
        });
    }
    if (month === '*' && dayOfMonth === '*' && dayOfWeek !== '*') {
        return formatTemplate(t('automation.cron.weekly'), {
            weekday: formatCronWeekday(dayOfWeek),
            time: formatCronTime(hour, minute),
        });
    }
    if (month === '*' && dayOfMonth !== '*' && dayOfWeek === '*') {
        return formatTemplate(t('automation.cron.monthly'), {
            day: dayOfMonth,
            time: formatCronTime(hour, minute),
        });
    }
    return formatTemplate(t('automation.cron.fallback'), { expression: cron });
}

function formatCronTime(hour, minute) {
    const safeHour = /^\d+$/.test(String(hour || '')) ? String(hour).padStart(2, '0') : String(hour || '*');
    const safeMinute = /^\d+$/.test(String(minute || '')) ? String(minute).padStart(2, '0') : String(minute || '*');
    return `${safeHour}:${safeMinute}`;
}

function formatCronWeekday(value) {
    const map = {
        '0': t('automation.cron.weekday.sun'),
        '1': t('automation.cron.weekday.mon'),
        '2': t('automation.cron.weekday.tue'),
        '3': t('automation.cron.weekday.wed'),
        '4': t('automation.cron.weekday.thu'),
        '5': t('automation.cron.weekday.fri'),
        '6': t('automation.cron.weekday.sat'),
        '7': t('automation.cron.weekday.sun'),
    };
    return map[String(value || '').trim()] || String(value || '*');
}

function renderFolderIcon(isExpanded) {
    const folderClass = isExpanded ? 'workspace-tree-icon is-folder-open' : 'workspace-tree-icon is-folder';
    return `
        <span class="${folderClass}" aria-hidden="true">
            <svg viewBox="0 0 16 16" focusable="false">
                <path d="M1.5 4.5a1 1 0 0 1 1-1h3.2l1.2 1.5H13.5a1 1 0 0 1 1 1v5.5a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1z" />
            </svg>
        </span>
    `;
}

function renderFileIcon() {
    return `
        <span class="workspace-tree-icon is-file" aria-hidden="true">
            <svg viewBox="0 0 16 16" focusable="false">
                <path d="M4 1.5h5l3 3v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-11a1 1 0 0 1 1-1z" />
                <path d="M9 1.5v3h3" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
        </span>
    `;
}

function formatAutomationTitle(project) {
    const label = String(project?.display_name || project?.name || project?.automation_project_id || '').trim();
    return label
        ? formatMessage('workspace_view.automation_suffix', { label })
        : t('workspace_view.automation_project');
}

function formatWorkspaceTitle(workspace) {
    const workspaceId = String(workspace?.workspace_id || '').trim();
    if (workspaceId) {
        return formatTemplate(t('workspace_view.title'), { workspace: workspaceId });
    }
    return t('workspace_view.title');
}

function formatTemplate(template, values) {
    return Object.entries(values).reduce(
        (result, [key, value]) => result.replace(`{${key}}`, String(value)),
        String(template || ''),
    );
}

function escapeHtml(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
