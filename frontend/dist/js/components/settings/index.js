/**
 * components/settings/index.js
 * Settings modal shell and tab routing.
 */
import { bindModelProfileHandlers, loadModelProfilesPanel } from './modelProfiles.js';
import {
    bindNotificationSettingsHandlers,
    loadNotificationSettingsPanel,
} from './notifications.js';
import { bindEnvironmentVariableSettingsHandlers, loadEnvironmentVariablesPanel } from './environmentVariables.js';
import {
    bindOrchestrationSettingsHandlers,
    loadOrchestrationSettingsPanel,
} from './orchestrationSettings.js';
import { bindProxySettingsHandlers, loadProxyStatusPanel } from './proxySettings.js';
import { bindRoleSettingsHandlers, loadRoleSettingsPanel } from './rolesSettings.js';
import { bindTriggerSettingsHandlers, loadTriggerSettingsPanel } from './triggerSettings.js';
import { bindSystemStatusHandlers, loadMcpStatusPanel, loadSkillsStatusPanel } from './systemStatus.js';
import { t, translateDocument } from '../../utils/i18n.js';

let settingsModal = null;
let currentTab = 'model';
let initialized = false;

const TAB_METADATA = {
    model: {
        titleKey: 'settings.panel.model.title',
        descriptionKey: 'settings.panel.model.description',
    },
    skills: {
        titleKey: 'settings.panel.skills.title',
        descriptionKey: 'settings.panel.skills.description',
    },
    mcp: {
        titleKey: 'settings.panel.mcp.title',
        descriptionKey: 'settings.panel.mcp.description',
    },
    roles: {
        titleKey: 'settings.panel.roles.title',
        descriptionKey: 'settings.panel.roles.description',
    },
    orchestration: {
        titleKey: 'settings.panel.orchestration.title',
        descriptionKey: 'settings.panel.orchestration.description',
    },
    triggers: {
        titleKey: 'settings.panel.triggers.title',
        descriptionKey: 'settings.panel.triggers.description',
    },
    notifications: {
        titleKey: 'settings.panel.notifications.title',
        descriptionKey: 'settings.panel.notifications.description',
    },
    proxy: {
        titleKey: 'settings.panel.proxy.title',
        descriptionKey: 'settings.panel.proxy.description',
    },
    environment: {
        titleKey: 'settings.panel.environment.title',
        descriptionKey: 'settings.panel.environment.description',
    },
};

export function initSettings() {
    if (initialized) return;
    createModal();
    setupEventListeners();
    initialized = true;
}

function createModal() {
    settingsModal = document.createElement('div');
    settingsModal.id = 'settings-modal';
    settingsModal.className = 'modal settings-modal';
    settingsModal.innerHTML = `
        <div class="modal-content settings-modal-content">
            <aside class="settings-sidebar">
                <div class="settings-sidebar-head">
                    <h2 data-i18n="settings.shell">Settings</h2>
                </div>
                <div class="settings-tabs" role="tablist" aria-label="Settings Sections" data-i18n-aria-label="settings.sections">
                    <button class="settings-tab active" data-tab="model">
                        <span class="settings-tab-label" data-i18n="settings.tab.model">Model</span>
                    </button>
                    <button class="settings-tab" data-tab="skills">
                        <span class="settings-tab-label" data-i18n="settings.tab.skills">Skills</span>
                    </button>
                    <button class="settings-tab" data-tab="mcp">
                        <span class="settings-tab-label" data-i18n="settings.tab.mcp">MCP</span>
                    </button>
                    <button class="settings-tab" data-tab="roles">
                        <span class="settings-tab-label" data-i18n="settings.tab.roles">Roles</span>
                    </button>
                    <button class="settings-tab" data-tab="orchestration">
                        <span class="settings-tab-label" data-i18n="settings.tab.orchestration">Orchestration</span>
                    </button>
                    <button class="settings-tab" data-tab="triggers">
                        <span class="settings-tab-label" data-i18n="settings.tab.triggers">Triggers</span>
                    </button>
                    <button class="settings-tab" data-tab="notifications">
                        <span class="settings-tab-label" data-i18n="settings.tab.notifications">Notifications</span>
                    </button>
                    <button class="settings-tab" data-tab="proxy">
                        <span class="settings-tab-label" data-i18n="settings.tab.proxy">Proxy</span>
                    </button>
                    <button class="settings-tab" data-tab="environment">
                        <span class="settings-tab-label" data-i18n="settings.tab.environment">Environment</span>
                    </button>
                </div>
            </aside>
            <section class="settings-main">
                <div class="modal-header settings-modal-header">
                    <div class="settings-modal-heading">
                        <h2 id="settings-panel-title">Model</h2>
                        <p id="settings-panel-description">Manage providers, endpoints, request limits, and sampling defaults.</p>
                    </div>
                    <button class="close-btn" id="settings-close" aria-label="Close Settings" data-i18n-aria-label="settings.close_title" data-i18n-title="settings.close_title">&times;</button>
                </div>
                <div class="settings-body">
                    <div class="settings-panel" id="model-panel">
                        <div class="settings-section settings-section-model">
                            <div class="settings-content-stack settings-model-stack">
                                <div class="profiles-list" id="profiles-list"></div>
                                <div class="profile-editor" id="profile-editor" style="display:none;">
                                    <div class="profile-editor-header">
                                        <h4 id="profile-editor-title" data-i18n="settings.model.add_profile">Add Profile</h4>
                                        <p data-i18n="settings.model.editor_copy">Configure the endpoint, model, request limits, and sampling defaults.</p>
                                    </div>
                                    <form class="profile-editor-form" id="profile-editor-form" autocomplete="off">
                                        <div class="profile-editor-grid">
                                            <div class="form-group">
                                                <label for="profile-name" data-i18n="settings.model.profile_name">Profile Name</label>
                                                <input type="text" id="profile-name" placeholder="e.g., default, kimi" data-i18n-placeholder="settings.model.profile_name_placeholder" autocomplete="off">
                                            </div>
                                            <div class="form-group">
                                                <label for="profile-provider" data-i18n="settings.model.provider">Provider</label>
                                                <input type="text" id="profile-provider" list="profile-provider-options" autocomplete="off" spellcheck="false">
                                                <datalist id="profile-provider-options">
                                                    <option value="openai_compatible"></option>
                                                </datalist>
                                            </div>
                                            <div class="form-group form-group-span-2">
                                                <label for="profile-base-url" data-i18n="settings.model.base_url">Base URL</label>
                                                <input type="text" id="profile-base-url" placeholder="e.g., https://api.openai.com/v1" data-i18n-placeholder="settings.model.base_url_placeholder" autocomplete="url">
                                            </div>
                                            <div class="profile-credentials-row form-group-span-2">
                                                <div class="form-group">
                                                    <label for="profile-api-key" data-i18n="settings.model.api_key">API Key</label>
                                                    <div class="secure-input-row">
                                                        <input type="password" id="profile-api-key" placeholder="sk-..." autocomplete="current-password">
                                                        <button class="secure-input-btn" id="toggle-profile-api-key-btn" type="button" title="Show API key" aria-label="Show API key" style="display:none;">
                                                            <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                                                                <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"></path>
                                                                <circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.8"></circle>
                                                            </svg>
                                                        </button>
                                                    </div>
                                                </div>
                                                <div class="form-group form-group-inline-action">
                                                    <label for="profile-model" data-i18n="settings.model.model">Model</label>
                                                    <div class="secure-input-row profile-model-input-row">
                                                        <input type="text" id="profile-model" autocomplete="off" spellcheck="false">
                                                        <button class="secure-input-btn profile-model-menu-btn" id="open-profile-model-menu-btn" type="button" title="Show Models" aria-label="Show Models">
                                                            <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                                                                <path d="m7 10 5 5 5-5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path>
                                                            </svg>
                                                        </button>
                                                        <button class="secure-input-btn profile-discovery-btn" id="fetch-profile-models-btn" type="button" title="Fetch Models" aria-label="Fetch Models">
                                                            <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                                                                <path d="M20 12a8 8 0 1 1-2.34-5.66" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path>
                                                                <path d="M20 4v6h-6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path>
                                                            </svg>
                                                        </button>
                                                        <div class="profile-model-menu" id="profile-model-menu" style="display:none;"></div>
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                        <div class="profile-model-discovery-status" id="profile-model-discovery-status" style="display:none;"></div>
                                        <div class="profile-editor-subsection">
                                            <h5 data-i18n="settings.model.request_controls">Request Controls</h5>
                                            <div class="form-row">
                                                <div class="form-group">
                                                    <label for="profile-temperature" data-i18n="settings.model.temperature">Temperature</label>
                                                    <input type="number" id="profile-temperature" value="0.7" step="0.1" min="0" max="2" autocomplete="off">
                                                </div>
                                                <div class="form-group">
                                                    <label for="profile-top-p" data-i18n="settings.model.top_p">Top P</label>
                                                    <input type="number" id="profile-top-p" value="1.0" step="0.1" min="0" max="1" autocomplete="off">
                                                </div>
                                                <div class="form-group">
                                                    <label for="profile-max-tokens" data-i18n="settings.model.max_output_tokens">Max Output Tokens</label>
                                                    <input type="number" id="profile-max-tokens" value="100000" min="1" autocomplete="off">
                                                </div>
                                                <div class="form-group">
                                                    <label for="profile-context-window" data-i18n="settings.model.context_window">Context Window</label>
                                                    <input type="number" id="profile-context-window" value="" min="1" autocomplete="off" placeholder="Optional" data-i18n-placeholder="settings.model.optional">
                                                </div>
                                                <div class="form-group">
                                                    <label for="profile-connect-timeout" data-i18n="settings.model.connect_timeout">Connect Timeout (s)</label>
                                                    <input type="number" id="profile-connect-timeout" value="15" step="1" min="1" max="300" autocomplete="off">
                                                </div>
                                                <div class="form-group">
                                                    <label for="profile-ssl-verify" data-i18n="settings.proxy.default_ssl">SSL Verification</label>
                                                    <select id="profile-ssl-verify">
                                                        <option value="" data-i18n="settings.proxy.inherit_default">Inherit Default</option>
                                                        <option value="true" data-i18n="settings.proxy.verify">Verify</option>
                                                        <option value="false" data-i18n="settings.proxy.skip_verify">Skip Verify</option>
                                                    </select>
                                                </div>
                                            </div>
                                        </div>
                                        <div class="profile-default-row">
                                            <input type="checkbox" id="profile-is-default">
                                            <label for="profile-is-default" data-i18n="settings.model.set_default">Set as default profile</label>
                                        </div>
                                        <div class="profile-probe-status" id="profile-probe-status" style="display:none;"></div>
                                    </form>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="settings-panel" id="mcp-panel" style="display:none;">
                        <div class="settings-section">
                            <div class="settings-content-stack status-stack" id="mcp-status"></div>
                        </div>
                    </div>
                    <div class="settings-panel" id="roles-panel" style="display:none;">
                        <div class="settings-section">
                            <div class="settings-content-stack">
                                <div class="roles-list" id="roles-list"></div>
                                <div class="role-editor-panel" id="role-editor-panel" style="display:none;">
                                    <div class="roles-editor-empty settings-empty-state settings-empty-state-compact" id="roles-editor-empty" style="display:none;">
                                        <h4 data-i18n="settings.roles.empty">No role selected</h4>
                                        <p data-i18n="settings.roles.empty_copy">Select a role to edit its metadata and prompt.</p>
                                    </div>
                                    <div class="role-editor-form" id="role-editor-form" style="display:none;">
                                        <div class="role-editor-header">
                                            <div>
                                                <h4 data-i18n="settings.roles.editor">Role Editor</h4>
                                                <p id="role-file-meta"></p>
                                            </div>
                                        </div>
                                        <div class="role-editor-sections">
                                            <section class="role-editor-section">
                                                <div class="profile-editor-grid role-editor-grid">
                                                    <div class="form-group">
                                                        <label for="role-id-input" data-i18n="settings.roles.id">Role ID</label>
                                                        <input type="text" id="role-id-input" placeholder="e.g. spec_coder" data-i18n-placeholder="settings.roles.id_placeholder">
                                                    </div>
                                                    <div class="form-group">
                                                        <label for="role-name-input" data-i18n="settings.roles.name">Name</label>
                                                        <input type="text" id="role-name-input" placeholder="e.g. Spec Coder" data-i18n-placeholder="settings.roles.name_placeholder">
                                                    </div>
                                                    <div class="form-group form-group-span-2">
                                                        <label for="role-description-input" data-i18n="settings.roles.description">Description</label>
                                                        <input type="text" id="role-description-input" placeholder="Short summary used in coordinator prompts" data-i18n-placeholder="settings.roles.description_placeholder">
                                                    </div>
                                                    <div class="form-group">
                                                        <label for="role-version-input" data-i18n="settings.roles.version">Version</label>
                                                        <input type="text" id="role-version-input" placeholder="e.g. 1.0.0" data-i18n-placeholder="settings.roles.version_placeholder">
                                                    </div>
                                                    <div class="form-group">
                                                        <label for="role-model-profile-input" data-i18n="settings.roles.model_profile">Model Profile</label>
                                                        <select id="role-model-profile-input"></select>
                                                    </div>
                                                </div>
                                            </section>
                                            <section class="role-editor-section">
                                                <h5 data-i18n="settings.roles.allowed_tools">Allowed Tools</h5>
                                                <div class="role-option-picker role-option-picker-tools" id="role-tools-picker"></div>
                                            </section>
                                            <section class="role-editor-section">
                                                <h5 data-i18n="settings.roles.mcp_servers">MCP Servers</h5>
                                                <div class="role-option-picker role-option-picker-single" id="role-mcp-picker"></div>
                                            </section>
                                            <section class="role-editor-section">
                                                <h5 data-i18n="settings.tab.skills">Skills</h5>
                                                <div class="role-option-picker role-option-picker-single" id="role-skills-picker"></div>
                                            </section>
                                            <section class="role-editor-section">
                                                <h5 data-i18n="settings.roles.memory">Memory</h5>
                                                <div class="role-workspace-row">
                                                    <div class="form-group">
                                                        <label for="role-memory-enabled-input" data-i18n="settings.roles.durable_memory">Durable Memory</label>
                                                        <select id="role-memory-enabled-input"></select>
                                                    </div>
                                                    <div class="form-group">
                                                    </div>
                                                    <p class="role-workspace-note" id="role-workspace-note">
                                                        <span data-i18n="settings.roles.memory_note">Role memory is global by role. Stage documents are managed separately under the bound workspace and session directory.</span>
                                                    </p>
                                                </div>
                                            </section>
                                            <section class="role-editor-section">
                                                <div class="role-prompt-header">
                                                    <h5 data-i18n="settings.roles.system_prompt">System Prompt</h5>
                                                    <div class="role-prompt-tabs">
                                                        <button class="role-prompt-tab active" id="role-prompt-edit-tab" type="button" data-i18n="settings.roles.prompt_edit">Edit</button>
                                                        <button class="role-prompt-tab" id="role-prompt-preview-tab" type="button" data-i18n="settings.roles.prompt_preview">Preview</button>
                                                    </div>
                                                </div>
                                                <textarea class="config-textarea role-prompt-textarea" id="role-system-prompt-input" placeholder="Write the role prompt here" data-i18n-placeholder="settings.roles.prompt_placeholder"></textarea>
                                                <div class="role-prompt-preview msg-text" id="role-system-prompt-preview" style="display:none;"></div>
                                            </section>
                                        </div>
                                        <div class="role-editor-status" id="role-editor-status" style="display:none;"></div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="settings-panel" id="environment-panel" style="display:none;">
                        <div class="settings-section">
                            <div class="settings-content-stack env-panel-body">
                                <p class="env-settings-help" id="env-variables-help"></p>
                                <div class="env-groups" id="environment-variables-groups"></div>
                            </div>
                        </div>
                    </div>
                    <div class="settings-panel" id="orchestration-panel" style="display:none;">
                        <div class="settings-section">
                            <div class="settings-content-stack orchestration-settings-stack">
                                <section class="orchestration-settings-block">
                                    <div class="orchestration-preset-list" id="orchestration-preset-list"></div>
                                    <div class="role-editor-panel orchestration-editor-panel" id="orchestration-editor-panel" style="display:none;">
                                        <div class="roles-editor-empty settings-empty-state settings-empty-state-compact" id="orchestration-editor-empty" style="display:none;">
                                            <h4 data-i18n="settings.orchestration.empty">No orchestration selected</h4>
                                            <p data-i18n="settings.orchestration.empty_copy">Select an orchestration to edit its roles and orchestration prompt.</p>
                                        </div>
                                        <div class="role-editor-form orchestration-editor-form" id="orchestration-editor-form" style="display:none;">
                                            <div class="role-editor-header">
                                                <div>
                                                    <h4 data-i18n="settings.orchestration.editor">Orchestration Editor</h4>
                                                    <p id="orchestration-file-meta" data-i18n="settings.orchestration.configuration">Orchestration configuration</p>
                                                </div>
                                                <button class="secondary-btn section-action-btn" id="delete-orchestration-preset-btn" type="button" data-i18n="settings.orchestration.delete">Delete Orchestration</button>
                                            </div>
                                            <div id="orchestration-preset-editor"></div>
                                        </div>
                                    </div>
                                </section>
                                <div class="role-editor-status" id="orchestration-editor-status" style="display:none;"></div>
                            </div>
                        </div>
                    </div>
                    <div class="settings-panel" id="triggers-panel" style="display:none;">
                        <div class="settings-section">
                            <div class="settings-content-stack orchestration-settings-stack">
                                <section class="orchestration-settings-block trigger-settings-block">
                                    <div class="orchestration-preset-list trigger-platform-list" id="trigger-platform-list"></div>
                                    <div class="role-editor-panel orchestration-editor-panel trigger-provider-detail-panel" id="trigger-provider-detail-panel" style="display:none;">
                                        <div class="role-editor-form orchestration-editor-form trigger-provider-detail" id="trigger-provider-detail"></div>
                                    </div>
                                </section>
                                <div class="role-editor-status" id="trigger-editor-status" style="display:none;"></div>
                            </div>
                        </div>
                    </div>
                    <div class="settings-panel" id="notifications-panel" style="display:none;">
                        <div class="settings-section">
                            <div class="settings-content-stack notifications-panel-body">
                                <p class="notifications-help" data-i18n="settings.notifications.help">
                                    A notification is sent only when <strong>Enabled</strong> is on and at least one delivery channel is selected.
                                </p>
                                <div class="notification-grid">
                                    <div class="notification-row" data-notif-type="tool_approval_requested">
                                        <div class="notification-row-main">
                                            <div class="notification-row-title" data-i18n="settings.notifications.tool_approval_requested">Tool approval requested</div>
                                            <div class="notification-row-desc" data-i18n="settings.notifications.tool_approval_requested_copy">When an agent asks for approval before a tool call.</div>
                                        </div>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-tool_approval_requested-enabled">
                                            <span class="notification-toggle-check" aria-hidden="true"></span>
                                            <span class="notification-toggle-label" data-i18n="settings.field.enabled">Enabled</span>
                                        </label>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-tool_approval_requested-browser">
                                            <span class="notification-toggle-check" aria-hidden="true"></span>
                                            <span class="notification-toggle-label" data-i18n="settings.field.browser">Browser</span>
                                        </label>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-tool_approval_requested-toast">
                                            <span class="notification-toggle-check" aria-hidden="true"></span>
                                            <span class="notification-toggle-label" data-i18n="settings.field.toast">Toast</span>
                                        </label>
                                    </div>
                                    <div class="notification-row" data-notif-type="run_completed">
                                        <div class="notification-row-main">
                                            <div class="notification-row-title" data-i18n="settings.notifications.run_completed">Run completed</div>
                                            <div class="notification-row-desc" data-i18n="settings.notifications.run_completed_copy">When a run finishes successfully.</div>
                                        </div>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_completed-enabled">
                                            <span class="notification-toggle-check" aria-hidden="true"></span>
                                            <span class="notification-toggle-label" data-i18n="settings.field.enabled">Enabled</span>
                                        </label>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_completed-browser">
                                            <span class="notification-toggle-check" aria-hidden="true"></span>
                                            <span class="notification-toggle-label" data-i18n="settings.field.browser">Browser</span>
                                        </label>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_completed-toast">
                                            <span class="notification-toggle-check" aria-hidden="true"></span>
                                            <span class="notification-toggle-label" data-i18n="settings.field.toast">Toast</span>
                                        </label>
                                    </div>
                                    <div class="notification-row" data-notif-type="run_failed">
                                        <div class="notification-row-main">
                                            <div class="notification-row-title" data-i18n="settings.notifications.run_failed">Run failed</div>
                                            <div class="notification-row-desc" data-i18n="settings.notifications.run_failed_copy">When a run stops because of an error.</div>
                                        </div>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_failed-enabled">
                                            <span class="notification-toggle-check" aria-hidden="true"></span>
                                            <span class="notification-toggle-label" data-i18n="settings.field.enabled">Enabled</span>
                                        </label>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_failed-browser">
                                            <span class="notification-toggle-check" aria-hidden="true"></span>
                                            <span class="notification-toggle-label" data-i18n="settings.field.browser">Browser</span>
                                        </label>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_failed-toast">
                                            <span class="notification-toggle-check" aria-hidden="true"></span>
                                            <span class="notification-toggle-label" data-i18n="settings.field.toast">Toast</span>
                                        </label>
                                    </div>
                                    <div class="notification-row" data-notif-type="run_stopped">
                                        <div class="notification-row-main">
                                            <div class="notification-row-title" data-i18n="settings.notifications.run_stopped">Run stopped</div>
                                            <div class="notification-row-desc" data-i18n="settings.notifications.run_stopped_copy">When a run is stopped by user action.</div>
                                        </div>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_stopped-enabled">
                                            <span class="notification-toggle-check" aria-hidden="true"></span>
                                            <span class="notification-toggle-label" data-i18n="settings.field.enabled">Enabled</span>
                                        </label>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_stopped-browser">
                                            <span class="notification-toggle-check" aria-hidden="true"></span>
                                            <span class="notification-toggle-label" data-i18n="settings.field.browser">Browser</span>
                                        </label>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_stopped-toast">
                                            <span class="notification-toggle-check" aria-hidden="true"></span>
                                            <span class="notification-toggle-label" data-i18n="settings.field.toast">Toast</span>
                                        </label>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="settings-panel" id="proxy-panel" style="display:none;">
                        <div class="settings-section">
                            <div class="settings-content-stack proxy-panel-body">
                                <div class="proxy-editor-form">
                                    <section class="proxy-form-section">
                                        <div class="proxy-form-section-header">
                                            <h5 data-i18n="settings.proxy.section">Proxy Settings</h5>
                                        </div>
                                        <div class="proxy-form-grid">
                                            <div class="form-group proxy-inline-field">
                                                <label for="proxy-http-proxy" data-i18n="settings.proxy.http">HTTP Proxy</label>
                                                <input type="text" id="proxy-http-proxy" placeholder="http://127.0.0.1:7890" autocomplete="off">
                                            </div>
                                            <div class="form-group proxy-inline-field">
                                                <label for="proxy-https-proxy" data-i18n="settings.proxy.https">HTTPS Proxy</label>
                                                <input type="text" id="proxy-https-proxy" placeholder="http://127.0.0.1:7890" autocomplete="off">
                                            </div>
                                            <div class="form-group proxy-inline-field">
                                                <label for="proxy-all-proxy" data-i18n="settings.proxy.all">ALL Proxy</label>
                                                <input type="text" id="proxy-all-proxy" placeholder="socks5://127.0.0.1:7890" autocomplete="off">
                                            </div>
                                            <div class="form-group proxy-inline-field">
                                                <label for="proxy-username" data-i18n="settings.proxy.username">Username</label>
                                                <input type="text" id="proxy-username" placeholder="Optional proxy username" data-i18n-placeholder="settings.proxy.username_placeholder" autocomplete="username">
                                            </div>
                                            <div class="form-group proxy-inline-field">
                                                <label for="proxy-password" data-i18n="settings.proxy.password">Password</label>
                                                <input type="password" id="proxy-password" placeholder="Optional proxy password" data-i18n-placeholder="settings.proxy.password_placeholder" autocomplete="current-password">
                                            </div>
                                            <div class="form-group proxy-inline-field">
                                                <label for="proxy-no-proxy" data-i18n="settings.proxy.no_proxy">NO_PROXY</label>
                                                <input type="text" id="proxy-no-proxy" placeholder="localhost;127.*;192.168.*;<local>" autocomplete="off">
                                            </div>
                                            <div class="form-group proxy-inline-field proxy-inline-field-compact">
                                                <label for="proxy-ssl-verify" data-i18n="settings.proxy.default_ssl">Default SSL Verification</label>
                                                <select id="proxy-ssl-verify">
                                                    <option value="" data-i18n="settings.proxy.inherit_default">Inherit Default</option>
                                                    <option value="true" data-i18n="settings.proxy.verify">Verify</option>
                                                    <option value="false" data-i18n="settings.proxy.skip_verify">Skip Verify</option>
                                                </select>
                                            </div>
                                        </div>
                                    </section>
                                    <section class="proxy-form-section proxy-form-section-test">
                                        <div class="proxy-form-section-header">
                                            <h5 data-i18n="settings.proxy.connectivity">Connectivity Test</h5>
                                        </div>
                                        <div class="proxy-probe-grid">
                                            <div class="form-group proxy-inline-field proxy-inline-field-test">
                                                <label for="proxy-probe-url" data-i18n="settings.proxy.target_url">Target URL</label>
                                                <input type="text" id="proxy-probe-url" placeholder="https://example.com" data-i18n-placeholder="settings.proxy.target_url_placeholder" autocomplete="url">
                                                <button class="secondary-btn section-action-btn proxy-inline-test-btn" id="test-proxy-web-btn" type="button" data-i18n="settings.proxy.test_url">Test URL</button>
                                            </div>
                                            <div class="form-group proxy-inline-field proxy-inline-field-compact">
                                                <label for="proxy-probe-timeout" data-i18n="settings.proxy.timeout">Timeout (ms)</label>
                                                <input type="number" id="proxy-probe-timeout" value="5000" min="1000" max="300000" step="500" autocomplete="off">
                                            </div>
                                        </div>
                                        <div class="proxy-probe-status" id="proxy-probe-status" style="display:none;"></div>
                                    </section>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="settings-panel" id="skills-panel" style="display:none;">
                        <div class="settings-section">
                            <div class="settings-content-stack status-stack" id="skills-status"></div>
                        </div>
                    </div>
                </div>
                <div class="settings-actions-bar" id="settings-actions-bar">
                    <div class="settings-panel-actions" id="settings-panel-actions">
                        <div class="settings-panel-actions-group settings-panel-actions-group-start">
                            <button class="secondary-btn section-action-btn settings-action" id="test-profile-btn" type="button" style="display:none;">Test</button>
                            <button class="secondary-btn section-action-btn settings-action" id="validate-role-btn" type="button" style="display:none;" data-i18n="settings.action.validate">Validate</button>
                        </div>
                        <div class="settings-panel-actions-group settings-panel-actions-group-end">
                            <button class="secondary-btn section-action-btn settings-action" id="add-profile-btn" type="button" style="display:none;" data-i18n="settings.action.add_profile">Add Profile</button>
                            <button class="primary-btn section-action-btn settings-action" id="save-profile-btn" type="button" style="display:none;" data-i18n="settings.action.save">Save</button>
                            <button class="secondary-btn section-action-btn settings-action" id="cancel-profile-btn" type="button" style="display:none;" data-i18n="settings.action.cancel">Cancel</button>
                            <button class="secondary-btn section-action-btn settings-action" id="add-role-btn" type="button" style="display:none;" data-i18n="settings.action.add_role">Add Role</button>
                            <button class="secondary-btn section-action-btn settings-action" id="add-orchestration-preset-btn" type="button" style="display:none;" data-i18n="settings.action.add_orchestration">Add Orchestration</button>
                            <button class="secondary-btn section-action-btn settings-action" id="add-trigger-btn" type="button" style="display:none;" data-i18n="settings.action.add_trigger">Add Trigger</button>
                            <button class="secondary-btn section-action-btn settings-action" id="add-env-btn" type="button" style="display:none;" data-i18n="settings.action.add_variable">Add Variable</button>
                            <button class="primary-btn section-action-btn settings-action" id="save-role-btn" type="button" style="display:none;" data-i18n="settings.action.save">Save</button>
                            <button class="primary-btn section-action-btn settings-action" id="save-orchestration-btn" type="button" style="display:none;" data-i18n="settings.action.save">Save</button>
                            <button class="primary-btn section-action-btn settings-action" id="save-trigger-btn" type="button" style="display:none;" data-i18n="settings.action.save">Save</button>
                            <button class="secondary-btn section-action-btn settings-action" id="cancel-role-btn" type="button" style="display:none;" data-i18n="settings.action.cancel">Cancel</button>
                            <button class="secondary-btn section-action-btn settings-action" id="cancel-orchestration-btn" type="button" style="display:none;" data-i18n="settings.action.cancel">Cancel</button>
                            <button class="secondary-btn section-action-btn settings-action" id="cancel-trigger-btn" type="button" style="display:none;" data-i18n="settings.action.cancel">Cancel</button>
                            <button class="primary-btn section-action-btn settings-action" id="save-env-btn" type="button" style="display:none;" data-i18n="settings.action.save">Save</button>
                            <button class="secondary-btn section-action-btn settings-action" id="cancel-env-btn" type="button" style="display:none;" data-i18n="settings.action.cancel">Cancel</button>
                            <button class="primary-btn section-action-btn settings-action" id="save-notifications-btn" type="button" style="display:none;" data-i18n="settings.action.save">Save</button>
                            <button class="primary-btn section-action-btn settings-action" id="save-proxy-btn" type="button" style="display:none;" data-i18n="settings.action.save">Save</button>
                            <button class="secondary-btn section-action-btn settings-action" id="reload-mcp-btn" type="button" style="display:none;" data-i18n="settings.action.reload">Reload</button>
                            <button class="secondary-btn section-action-btn settings-action" id="reload-skills-btn" type="button" style="display:none;" data-i18n="settings.action.reload">Reload</button>
                        </div>
                    </div>
                </div>
            </section>
        </div>
    `;
    document.body.appendChild(settingsModal);
    translateDocument(settingsModal);
}

function setupEventListeners() {
    const closeBtn = document.getElementById('settings-close');
    if (closeBtn) {
        closeBtn.onclick = closeSettings;
    }

    settingsModal.onclick = (e) => {
        if (e.target === settingsModal) {
            closeSettings();
        }
    };

    document.querySelectorAll('.settings-tab').forEach(tab => {
        tab.onclick = () => {
            currentTab = tab.dataset.tab;
            showPanel(currentTab);
        };
    });

    bindModelProfileHandlers();
    bindOrchestrationSettingsHandlers();
    bindRoleSettingsHandlers();
    bindTriggerSettingsHandlers();
    bindEnvironmentVariableSettingsHandlers();
    bindNotificationSettingsHandlers();
    bindProxySettingsHandlers();
    bindSystemStatusHandlers();
    if (typeof document.addEventListener === 'function') {
        document.addEventListener('agent-teams-language-changed', () => {
            if (!settingsModal) {
                return;
            }
            translateDocument(settingsModal);
            updatePanelHeading(currentTab);
        });
    }
}

async function showPanel(tab) {
    document.querySelectorAll('.settings-tab').forEach(button => {
        button.classList.toggle('active', button.dataset.tab === tab);
    });

    document.querySelectorAll('.settings-panel').forEach(panel => {
        const isActive = panel.id === `${tab}-panel`;
        panel.style.display = isActive ? 'block' : 'none';
        panel.classList.toggle('active', isActive);
    });

    updatePanelHeading(tab);
    renderPanelActions(tab);
    bindModelProfileHandlers();
    bindOrchestrationSettingsHandlers();
    bindRoleSettingsHandlers();
    bindTriggerSettingsHandlers();
    bindEnvironmentVariableSettingsHandlers();
    bindProxySettingsHandlers();
    bindSystemStatusHandlers();

    if (tab === 'model') {
        await loadModelProfilesPanel();
    } else if (tab === 'roles') {
        await loadRoleSettingsPanel();
    } else if (tab === 'orchestration') {
        await loadOrchestrationSettingsPanel();
    } else if (tab === 'triggers') {
        await loadTriggerSettingsPanel();
    } else if (tab === 'environment') {
        await loadEnvironmentVariablesPanel();
    } else if (tab === 'notifications') {
        await loadNotificationSettingsPanel();
    } else if (tab === 'proxy') {
        await loadProxyStatusPanel();
    } else if (tab === 'mcp') {
        await loadMcpStatusPanel();
    } else if (tab === 'skills') {
        await loadSkillsStatusPanel();
    }
}

function updatePanelHeading(tab) {
    const meta = TAB_METADATA[tab] || TAB_METADATA.model;
    document.getElementById('settings-panel-title').textContent = t(meta.titleKey);
    document.getElementById('settings-panel-description').textContent = t(meta.descriptionKey);
}

function renderPanelActions(tab) {
    const actions = document.getElementById('settings-panel-actions');
    const actionsBar = document.getElementById('settings-actions-bar');
    if (!actions) {
        return;
    }
    actions.querySelectorAll('.settings-action').forEach(button => {
        button.style.display = 'none';
    });
    if (actionsBar) actionsBar.style.display = 'flex';
    if (tab === 'model') {
        document.getElementById('add-profile-btn').style.display = 'inline-flex';
        return;
    }
    if (tab === 'roles') {
        document.getElementById('add-role-btn').style.display = 'inline-flex';
        return;
    }
    if (tab === 'orchestration') {
        document.getElementById('add-orchestration-preset-btn').style.display = 'inline-flex';
        return;
    }
    if (tab === 'triggers') {
        return;
    }
    if (tab === 'environment') {
        document.getElementById('add-env-btn').style.display = 'inline-flex';
        return;
    }
    if (tab === 'notifications') {
        document.getElementById('save-notifications-btn').style.display = 'inline-flex';
        return;
    }
    if (tab === 'proxy') {
        document.getElementById('save-proxy-btn').style.display = 'inline-flex';
        return;
    }
    if (tab === 'mcp') {
        document.getElementById('reload-mcp-btn').style.display = 'inline-flex';
        return;
    }
    if (tab === 'skills') {
        document.getElementById('reload-skills-btn').style.display = 'inline-flex';
        return;
    }
    if (actionsBar) actionsBar.style.display = 'none';
}

export function openSettings() {
    if (!initialized) initSettings();
    settingsModal.style.display = 'flex';
    settingsModal.classList.add('settings-modal-visible');
    showPanel(currentTab);
}

export function closeSettings() {
    if (!settingsModal) return;
    settingsModal.classList.remove('settings-modal-visible');
    settingsModal.style.display = 'none';
}

window.openSettings = openSettings;
