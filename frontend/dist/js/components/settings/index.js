/**
 * components/settings/index.js
 * Settings modal shell and tab routing.
 */
import { bindAgentSettingsHandlers, loadAgentSettingsPanel } from './agentsSettings.js';
import { bindHooksSettingsHandlers, loadHooksSettingsPanel } from './hooksSettings.js';
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
import { bindWorkspaceSettingsHandlers, loadWorkspaceSettingsPanel } from './workspaceSettings.js';
import { bindWebSettingsHandlers, loadWebSettingsPanel } from './webSettings.js';
import { bindSystemStatusHandlers, loadMcpStatusPanel, loadSkillsStatusPanel } from './systemStatus.js';
import { bindAppearanceHandlers, loadAppearancePanel, initAppearanceOnStartup } from './appearanceSettings.js';
import { t, translateDocument } from '../../utils/i18n.js';

let settingsModal = null;
let currentTab = 'appearance';
let initialized = false;
let overlayPointerDown = false;

const TAB_METADATA = {
    appearance: {
        titleKey: 'settings.panel.appearance.title',
        descriptionKey: 'settings.panel.appearance.description',
    },
    model: {
        titleKey: 'settings.panel.model.title',
        descriptionKey: 'settings.panel.model.description',
    },
    mcp: {
        titleKey: 'settings.panel.mcp.title',
        descriptionKey: 'settings.panel.mcp.description',
    },
    hooks: {
        titleKey: 'settings.panel.hooks.title',
        descriptionKey: 'settings.panel.hooks.description',
    },
    agents: {
        titleKey: 'settings.panel.agents.title',
        descriptionKey: 'settings.panel.agents.description',
    },
    roles: {
        titleKey: 'settings.panel.roles.title',
        descriptionKey: 'settings.panel.roles.description',
    },
    orchestration: {
        titleKey: 'settings.panel.orchestration.title',
        descriptionKey: 'settings.panel.orchestration.description',
    },
    notifications: {
        titleKey: 'settings.panel.notifications.title',
        descriptionKey: 'settings.panel.notifications.description',
    },
    web: {
        titleKey: 'settings.panel.web.title',
        descriptionKey: 'settings.panel.web.description',
    },
    proxy: {
        titleKey: 'settings.panel.proxy.title',
        descriptionKey: 'settings.panel.proxy.description',
    },
    workspace: {
        titleKey: 'settings.panel.workspace.title',
        descriptionKey: 'settings.panel.workspace.description',
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
                    <button class="settings-tab active" data-tab="appearance">
                        <span class="settings-tab-label" data-i18n="settings.tab.appearance">Appearance</span>
                    </button>
                    <button class="settings-tab" data-tab="model">
                        <span class="settings-tab-label" data-i18n="settings.tab.model">Model</span>
                    </button>
                    <button class="settings-tab" data-tab="mcp">
                        <span class="settings-tab-label" data-i18n="settings.tab.mcp">MCP</span>
                    </button>
                    <button class="settings-tab" data-tab="hooks">
                        <span class="settings-tab-label" data-i18n="settings.tab.hooks">Hooks</span>
                    </button>
                    <button class="settings-tab" data-tab="agents">
                        <span class="settings-tab-label" data-i18n="settings.tab.agents">Agents</span>
                    </button>
                    <button class="settings-tab" data-tab="roles">
                        <span class="settings-tab-label" data-i18n="settings.tab.roles">Roles</span>
                    </button>
                    <button class="settings-tab" data-tab="orchestration">
                        <span class="settings-tab-label" data-i18n="settings.tab.orchestration">Orchestration</span>
                    </button>
                    <button class="settings-tab" data-tab="notifications">
                        <span class="settings-tab-label" data-i18n="settings.tab.notifications">Notifications</span>
                    </button>
                    <button class="settings-tab" data-tab="web">
                        <span class="settings-tab-label" data-i18n="settings.tab.web">Web</span>
                    </button>
                    <button class="settings-tab" data-tab="proxy">
                        <span class="settings-tab-label" data-i18n="settings.tab.proxy">Proxy</span>
                    </button>
                    <button class="settings-tab" data-tab="workspace">
                        <span class="settings-tab-label" data-i18n="settings.tab.workspace">Remote Workspace</span>
                    </button>
                    <button class="settings-tab" data-tab="environment">
                        <span class="settings-tab-label" data-i18n="settings.tab.environment">Environment</span>
                    </button>
                </div>
            </aside>
            <section class="settings-main">
                <div class="modal-header settings-modal-header">
                    <div class="settings-modal-heading">
                        <h2 id="settings-panel-title" data-i18n="settings.panel.appearance.title">Appearance</h2>
                        <p id="settings-panel-description" data-i18n="settings.panel.appearance.description">Customize colors, fonts, and density. Changes apply in real time.</p>
                    </div>
                    <button class="close-btn" id="settings-close" aria-label="Close Settings" data-i18n-aria-label="settings.close_title" data-i18n-title="settings.close_title">&times;</button>
                </div>
                <div class="settings-body">
                    <div class="settings-panel" id="appearance-panel">
                        <div class="settings-section">
                            <div class="settings-content-stack">
                                <section class="proxy-form-section">
                                    <div class="proxy-form-section-header"><h5 data-i18n="settings.appearance.colors">Colors</h5></div>
                                    <div class="appearance-grid">
                                        <div class="appearance-row">
                                            <label data-i18n="settings.appearance.accent">Accent</label>
                                            <div class="appearance-color-field" id="appearance-accent">
                                                <input type="color" value="#91a698">
                                                <input type="text" placeholder="#91a698" spellcheck="false">
                                            </div>
                                        </div>
                                        <div class="appearance-row">
                                            <label data-i18n="settings.appearance.background">Background</label>
                                            <div class="appearance-color-field" id="appearance-background">
                                                <input type="color" value="#161718">
                                                <input type="text" placeholder="#161718" spellcheck="false">
                                            </div>
                                        </div>
                                        <div class="appearance-row">
                                            <label data-i18n="settings.appearance.foreground">Foreground</label>
                                            <div class="appearance-color-field" id="appearance-foreground">
                                                <input type="color" value="#f0eee8">
                                                <input type="text" placeholder="#f0eee8" spellcheck="false">
                                            </div>
                                        </div>
                                    </div>
                                </section>
                                <section class="proxy-form-section">
                                    <div class="proxy-form-section-header"><h5 data-i18n="settings.appearance.fonts">Fonts</h5></div>
                                    <div class="appearance-grid">
                                        <div class="appearance-row">
                                            <label data-i18n="settings.appearance.ui_font">UI Font</label>
                                            <input type="text" id="appearance-ui-font" class="appearance-text-input" placeholder="IBM Plex Sans, sans-serif" spellcheck="false">
                                        </div>
                                        <div class="appearance-row">
                                            <label data-i18n="settings.appearance.code_font">Code Font</label>
                                            <input type="text" id="appearance-code-font" class="appearance-text-input" placeholder="IBM Plex Mono, monospace" spellcheck="false">
                                        </div>
                                    </div>
                                </section>
                                <section class="proxy-form-section">
                                    <div class="proxy-form-section-header"><h5 data-i18n="settings.appearance.sizing">Sizing</h5></div>
                                    <div class="appearance-grid">
                                        <div class="appearance-row">
                                            <label data-i18n="settings.appearance.ui_font_size">UI Font Size</label>
                                            <div class="appearance-range-field">
                                                <input type="range" id="appearance-ui-font-size" min="11" max="20" value="15" step="1">
                                                <span class="appearance-range-value">15px</span>
                                            </div>
                                        </div>
                                        <div class="appearance-row">
                                            <label data-i18n="settings.appearance.code_font_size">Code Font Size</label>
                                            <div class="appearance-range-field">
                                                <input type="range" id="appearance-code-font-size" min="10" max="18" value="13" step="1">
                                                <span class="appearance-range-value">13px</span>
                                            </div>
                                        </div>
                                        <div class="appearance-row">
                                            <label data-i18n="settings.appearance.line_height">Line Height</label>
                                            <div class="appearance-range-field">
                                                <input type="range" id="appearance-line-height" min="120" max="200" value="148" step="2">
                                                <span class="appearance-range-value">1.48</span>
                                            </div>
                                        </div>
                                        <div class="appearance-row">
                                            <label data-i18n="settings.appearance.msg_density">Message Spacing</label>
                                            <div class="appearance-range-field">
                                                <input type="range" id="appearance-msg-density" min="30" max="150" value="85" step="5">
                                                <span class="appearance-range-value">0.85</span>
                                            </div>
                                        </div>
                                    </div>
                                </section>
                            </div>
                        </div>
                    </div>
                    <div class="settings-panel" id="model-panel" style="display:none;">
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
                                                <select id="profile-provider">
                                                    <option value="openai_compatible">openai_compatible</option>
                                                    <option value="bigmodel">bigmodel</option>
                                                    <option value="minimax">minimax</option>
                                                    <option value="maas">maas</option>
                                                </select>
                                            </div>
                                            <div class="form-group form-group-span-2">
                                                <label for="profile-base-url" data-i18n="settings.model.base_url">Base URL</label>
                                                <input type="text" id="profile-base-url" placeholder="e.g., https://api.openai.com/v1" data-i18n-placeholder="settings.model.base_url_placeholder" autocomplete="url">
                                            </div>
                                            <div class="profile-credentials-row form-group-span-2" id="profile-primary-credentials-row">
                                                <div class="form-group" id="profile-api-key-group">
                                                    <label for="profile-api-key" data-i18n="settings.model.api_key">API Key</label>
                                                    <div class="secure-input-row">
                                                        <input type="password" id="profile-api-key" placeholder="sk-..." autocomplete="new-password" autocapitalize="off" autocorrect="off" spellcheck="false">
                                                        <button class="secure-input-btn" id="toggle-profile-api-key-btn" type="button" title="Show API key" aria-label="Show API key" style="display:none;">
                                                            <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                                                                <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"></path>
                                                                <circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.8"></circle>
                                                            </svg>
                                                        </button>
                                                    </div>
                                                </div>
                                                <div class="form-group form-group-inline-action" id="profile-model-group">
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
                                            <div class="profile-credentials-row form-group-span-2" id="profile-maas-auth-fields" style="display:none;">
                                                <div class="form-group">
                                                    <label for="profile-maas-username">MAAS Username</label>
                                                    <input type="text" id="profile-maas-username" placeholder="username" autocomplete="username">
                                                </div>
                                                <div class="form-group">
                                                    <label for="profile-maas-password">MAAS Password</label>
                                                    <div class="secure-input-row">
                                                        <input type="password" id="profile-maas-password" placeholder="password" autocomplete="new-password" autocapitalize="off" autocorrect="off" spellcheck="false">
                                                        <button class="secure-input-btn" id="toggle-profile-maas-password-btn" type="button" title="Show password" aria-label="Show password" style="display:none;">
                                                            <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                                                                <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"></path>
                                                                <circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.8"></circle>
                                                            </svg>
                                                        </button>
                                                    </div>
                                                </div>
                                                <div class="form-group-span-2" id="profile-maas-model-slot"></div>
                                            </div>
                                         </div>
                                         <div class="profile-model-discovery-status" id="profile-model-discovery-status" style="display:none;"></div>
                                         <div class="profile-editor-subsection">
                                             <h5 data-i18n="settings.model.capability_section">Capabilities</h5>
                                             <div class="form-row">
                                                 <div class="form-group">
                                                     <label for="profile-image-capability" data-i18n="settings.model.image_capability">Image Input</label>
                                                     <select id="profile-image-capability">
                                                         <option value="follow_detection" data-i18n="settings.model.image_capability_follow">Follow detection</option>
                                                         <option value="supported" data-i18n="settings.model.image_capability_supported">Supports image input</option>
                                                         <option value="unsupported" data-i18n="settings.model.image_capability_unsupported">Text only</option>
                                                     </select>
                                                 </div>
                                             </div>
                                         </div>
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
                                                    <input type="number" id="profile-max-tokens" value="" min="1" autocomplete="off" placeholder="Optional" data-i18n-placeholder="settings.model.optional">
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
                                        <div class="profile-editor-subsection">
                                            <h5>Fallback</h5>
                                            <div class="form-row">
                                                <div class="form-group">
                                                    <label for="profile-fallback-policy">Fallback Strategy</label>
                                                    <select id="profile-fallback-policy"></select>
                                                </div>
                                                <div class="form-group">
                                                    <label for="profile-fallback-priority">Fallback Priority</label>
                                                    <input type="number" id="profile-fallback-priority" value="0" min="0" max="1000000" autocomplete="off">
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
                    <div class="settings-panel" id="hooks-panel" style="display:none;">
                        <div class="settings-section">
                            <div class="settings-content-stack status-stack" id="hooks-runtime-status"></div>
                        </div>
                    </div>
                    <div class="settings-panel" id="agents-panel" style="display:none;">
                        <div class="settings-section">
                            <div class="settings-content-stack">
                                <div class="roles-list" id="agents-list"></div>
                                <div class="role-editor-panel" id="agent-editor-panel" style="display:none;">
                                    <div class="roles-editor-empty settings-empty-state settings-empty-state-compact" id="agents-editor-empty" style="display:none;">
                                        <h4 data-i18n="settings.agents.empty">No agent selected</h4>
                                        <p data-i18n="settings.agents.empty_copy">Select an external ACP agent to edit its transport settings.</p>
                                    </div>
                                    <div class="role-editor-form" id="agent-editor-form" style="display:none;">
                                        <div class="role-editor-header">
                                            <div>
                                                <h4 data-i18n="settings.agents.editor">Agent Editor</h4>
                                                <p data-i18n="settings.agents.editor_copy">Configure an ACP-compatible external agent and bind it to roles.</p>
                                            </div>
                                        </div>
                                        <div class="role-editor-sections">
                                            <section class="role-editor-section">
                                                <div class="profile-editor-grid role-editor-grid">
                                                    <div class="form-group">
                                                        <label for="agent-id-input" data-i18n="settings.agents.id">Agent ID</label>
                                                        <input type="text" id="agent-id-input" placeholder="e.g. codex_local" data-i18n-placeholder="settings.agents.id_placeholder">
                                                    </div>
                                                    <div class="form-group">
                                                        <label for="agent-name-input" data-i18n="settings.agents.name">Name</label>
                                                        <input type="text" id="agent-name-input" placeholder="e.g. Codex Local" data-i18n-placeholder="settings.agents.name_placeholder">
                                                    </div>
                                                    <div class="form-group form-group-span-2">
                                                        <label for="agent-description-input" data-i18n="settings.agents.description">Description</label>
                                                        <input type="text" id="agent-description-input" placeholder="Short summary shown in role binding pickers" data-i18n-placeholder="settings.agents.description_placeholder">
                                                    </div>
                                                    <div class="form-group">
                                                        <label for="agent-transport-input" data-i18n="settings.agents.transport">Transport</label>
                                                        <select id="agent-transport-input">
                                                            <option value="stdio" data-i18n="settings.agents.transport_stdio">stdio</option>
                                                            <option value="streamable_http" data-i18n="settings.agents.transport_http">streamable_http</option>
                                                            <option value="custom" data-i18n="settings.agents.transport_custom">custom</option>
                                                        </select>
                                                    </div>
                                                </div>
                                            </section>
                                            <section class="role-editor-section" id="agent-transport-stdio">
                                                <h5 data-i18n="settings.agents.stdio">Stdio Transport</h5>
                                                <div class="profile-editor-grid role-editor-grid">
                                                    <div class="form-group form-group-span-2">
                                                        <label for="agent-stdio-command-input" data-i18n="settings.agents.command">Command</label>
                                                        <input type="text" id="agent-stdio-command-input" placeholder="e.g. codex" data-i18n-placeholder="settings.agents.command_placeholder">
                                                    </div>
                                                    <div class="form-group form-group-span-2">
                                                        <label for="agent-stdio-args-input" data-i18n="settings.agents.args">Args</label>
                                                        <textarea class="config-textarea role-prompt-textarea agent-args-textarea" id="agent-stdio-args-input" placeholder="One argument per line" data-i18n-placeholder="settings.agents.args_placeholder"></textarea>
                                                    </div>
                                                </div>
                                                <div class="role-prompt-header">
                                                    <h5 data-i18n="settings.agents.env_bindings">Environment Bindings</h5>
                                                    <div class="role-prompt-tabs">
                                                        <button class="role-prompt-tab active" id="add-agent-stdio-env-btn" type="button" data-i18n="settings.agents.add_env_binding">Add Variable</button>
                                                    </div>
                                                </div>
                                                <div id="agent-stdio-env-list"></div>
                                            </section>
                                            <section class="role-editor-section" id="agent-transport-http" style="display:none;">
                                                <h5 data-i18n="settings.agents.http">HTTP Transport</h5>
                                                <div class="profile-editor-grid role-editor-grid">
                                                    <div class="form-group form-group-span-2">
                                                        <label for="agent-http-url-input" data-i18n="settings.agents.http_url">URL</label>
                                                        <input type="text" id="agent-http-url-input" placeholder="e.g. http://127.0.0.1:4000/acp" data-i18n-placeholder="settings.agents.http_url_placeholder">
                                                    </div>
                                                    <div class="form-group">
                                                        <label for="agent-http-ssl-verify-input" data-i18n="settings.agents.ssl_verify">SSL Verification</label>
                                                        <select id="agent-http-ssl-verify-input">
                                                            <option value="" data-i18n="settings.agents.ssl_verify_inherit">Inherit</option>
                                                            <option value="true" data-i18n="settings.agents.ssl_verify_true">Verify</option>
                                                            <option value="false" data-i18n="settings.agents.ssl_verify_false">Skip Verify</option>
                                                        </select>
                                                    </div>
                                                </div>
                                                <div class="role-prompt-header">
                                                    <h5 data-i18n="settings.agents.header_bindings">Header Bindings</h5>
                                                    <div class="role-prompt-tabs">
                                                        <button class="role-prompt-tab active" id="add-agent-http-header-btn" type="button" data-i18n="settings.agents.add_header_binding">Add Header</button>
                                                    </div>
                                                </div>
                                                <div id="agent-http-header-list"></div>
                                            </section>
                                            <section class="role-editor-section" id="agent-transport-custom" style="display:none;">
                                                <h5 data-i18n="settings.agents.custom">Custom Transport</h5>
                                                <div class="profile-editor-grid role-editor-grid">
                                                    <div class="form-group form-group-span-2">
                                                        <label for="agent-custom-adapter-id-input" data-i18n="settings.agents.adapter_id">Adapter ID</label>
                                                        <input type="text" id="agent-custom-adapter-id-input" placeholder="e.g. plugin.acp" data-i18n-placeholder="settings.agents.adapter_id_placeholder">
                                                    </div>
                                                    <div class="form-group form-group-span-2">
                                                        <label for="agent-custom-config-input" data-i18n="settings.agents.custom_config">Config JSON</label>
                                                        <textarea class="config-textarea role-prompt-textarea" id="agent-custom-config-input" placeholder="{&#10;  &quot;endpoint&quot;: &quot;...&quot;&#10;}" data-i18n-placeholder="settings.agents.custom_config_placeholder"></textarea>
                                                    </div>
                                                </div>
                                            </section>
                                        </div>
                                        <div class="role-editor-status" id="agent-editor-status" style="display:none;"></div>
                                    </div>
                                </div>
                            </div>
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
                                                    <div class="form-group">
                                                        <label for="role-bound-agent-input">Bound Agent</label>
                                                        <select id="role-bound-agent-input"></select>
                                                    </div>
                                                    <div class="form-group">
                                                        <label for="role-execution-surface-input">Execution Surface</label>
                                                        <select id="role-execution-surface-input"></select>
                                                    </div>
                                                </div>
                                            </section>
                                            <section class="role-editor-section">
                                                <h5 data-i18n="settings.roles.tool_groups">Tool Groups</h5>
                                                <div class="role-option-picker role-option-picker-single" id="role-tool-groups-picker"></div>
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
                                                <div class="secure-input-row">
                                                    <input type="password" id="proxy-password" placeholder="Optional proxy password" data-i18n-placeholder="settings.proxy.password_placeholder" autocomplete="new-password" autocapitalize="off" autocorrect="off" spellcheck="false">
                                                    <button class="secure-input-btn" id="toggle-proxy-password-btn" type="button" title="Show password" aria-label="Show password" style="display:none;">
                                                        <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                                                            <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"></path>
                                                            <circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.8"></circle>
                                                        </svg>
                                                    </button>
                                                </div>
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
                                                    <option value="false" selected data-i18n="settings.proxy.skip_verify">Skip Verify</option>
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
                    <div class="settings-panel" id="workspace-panel" style="display:none;">
                        <div class="settings-section settings-section-model">
                            <div class="settings-content-stack settings-model-stack">
                                <div class="profiles-list" id="workspace-ssh-profile-list"></div>
                                <div class="profile-editor" id="workspace-ssh-profile-editor" style="display:none;">
                                    <div class="profile-editor-header">
                                        <h4 id="workspace-ssh-profile-editor-title" data-i18n="settings.workspace.add_profile">Add SSH Profile</h4>
                                        <p data-i18n="settings.workspace.editor_copy">Reusable SSH profiles are referenced by remote workspace mounts. You can save a username, an optional password, or import a private key.</p>
                                    </div>
                                    <form class="profile-editor-form" id="workspace-ssh-profile-form" autocomplete="off">
                                        <div class="profile-editor-grid workspace-profile-grid">
                                            <div class="form-group workspace-field-span-1">
                                                <label for="workspace-ssh-profile-id" data-i18n="settings.workspace.profile_id">Profile ID</label>
                                                <input type="text" id="workspace-ssh-profile-id" placeholder="e.g. prod, staging" data-i18n-placeholder="settings.workspace.profile_id_placeholder" autocomplete="off">
                                            </div>
                                            <div class="form-group workspace-field-span-1">
                                                <label for="workspace-ssh-profile-host" data-i18n="settings.workspace.host">Host</label>
                                                <input type="text" id="workspace-ssh-profile-host" placeholder="e.g. prod-alias" data-i18n-placeholder="settings.workspace.host_placeholder" autocomplete="off">
                                            </div>
                                            <div class="form-group workspace-field-span-1">
                                                <label for="workspace-ssh-profile-port" data-i18n="settings.workspace.port">Port</label>
                                                <input type="text" id="workspace-ssh-profile-port" placeholder="22" data-i18n-placeholder="settings.workspace.port_placeholder" inputmode="numeric" autocomplete="off">
                                            </div>
                                            <div class="form-group workspace-field-span-2">
                                                <label for="workspace-ssh-profile-shell" data-i18n="settings.workspace.remote_shell">Remote Shell</label>
                                                <input type="text" id="workspace-ssh-profile-shell" placeholder="e.g. /bin/bash" data-i18n-placeholder="settings.workspace.remote_shell_placeholder" autocomplete="off">
                                            </div>
                                            <div class="form-group workspace-field-span-1">
                                                <label for="workspace-ssh-profile-timeout" data-i18n="settings.workspace.connect_timeout_seconds">Connect Timeout (s)</label>
                                                <input type="text" id="workspace-ssh-profile-timeout" placeholder="15" data-i18n-placeholder="settings.workspace.connect_timeout_seconds_placeholder" inputmode="numeric" autocomplete="off">
                                            </div>
                                        </div>
                                        <div class="profile-editor-subsection">
                                            <div class="profile-editor-subsection-header">
                                                <h5 data-i18n="settings.workspace.auth_title">Authentication</h5>
                                                <p data-i18n="settings.workspace.auth_copy">Set a username, optionally save a password, or import a private key.</p>
                                                <p data-i18n="settings.workspace.auth_system_copy">If password and private key are empty, Agent Teams falls back to your system SSH configuration.</p>
                                            </div>
                                            <div class="profile-editor-grid workspace-auth-grid">
                                                <div class="form-group workspace-auth-field">
                                                    <label for="workspace-ssh-profile-username" data-i18n="settings.workspace.username">Username</label>
                                                    <input type="text" id="workspace-ssh-profile-username" placeholder="Optional username" data-i18n-placeholder="settings.workspace.username_placeholder" autocomplete="username">
                                                </div>
                                                <div class="form-group workspace-auth-field">
                                                    <label for="workspace-ssh-profile-password" data-i18n="settings.workspace.password">Password</label>
                                                    <div class="secure-input-row">
                                                        <input type="password" id="workspace-ssh-profile-password" placeholder="Optional password" data-i18n-placeholder="settings.workspace.password_placeholder" autocomplete="new-password" autocapitalize="off" autocorrect="off" spellcheck="false">
                                                        <button class="secure-input-btn" id="toggle-workspace-ssh-profile-password-btn" type="button" title="Show password" aria-label="Show password" style="display:none;">
                                                            <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                                                                <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"></path>
                                                                <circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.8"></circle>
                                                            </svg>
                                                        </button>
                                                    </div>
                                                </div>
                                                <div class="form-group form-group-span-2 workspace-auth-field workspace-auth-field-span-2">
                                                    <div class="form-label-row workspace-private-key-label-row">
                                                        <label for="workspace-ssh-profile-private-key" data-i18n="settings.workspace.private_key">Private Key</label>
                                                        <div class="settings-inline-action-row">
                                                            <button class="secondary-btn section-action-btn" id="workspace-ssh-profile-import-private-key-btn" type="button" data-i18n="settings.workspace.private_key_import">Import Private Key</button>
                                                        </div>
                                                    </div>
                                                    <textarea class="config-textarea workspace-private-key-textarea" id="workspace-ssh-profile-private-key" placeholder="Paste a private key or import one from a file" data-i18n-placeholder="settings.workspace.private_key_placeholder" autocapitalize="off" autocorrect="off" spellcheck="false"></textarea>
                                                    <input type="hidden" id="workspace-ssh-profile-private-key-name">
                                                    <input type="file" id="workspace-ssh-profile-private-key-file" style="display:none;" accept=".pem,.key,.ppk,text/plain">
                                                </div>
                                            </div>
                                            <p class="workspace-auth-state" id="workspace-ssh-profile-auth-state"></p>
                                            <div class="profile-probe-status" id="workspace-ssh-profile-probe-status" style="display:none;"></div>
                                        </div>
                                    </form>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="settings-panel" id="web-panel" style="display:none;">
                        <div class="settings-section">
                            <div class="settings-content-stack proxy-panel-body">
                                <div class="proxy-editor-form">
                                    <section class="proxy-form-section">
                                        <div class="proxy-form-section-header">
                                            <h5 data-i18n="settings.web.section">网页搜索</h5>
                                        </div>
                                        <div class="proxy-form-grid">
                                            <div class="form-group proxy-inline-field">
                                                <label for="web-provider" data-i18n="settings.web.provider">提供商</label>
                                                <select id="web-provider">
                                                    <option value="exa">Exa</option>
                                                </select>
                                            </div>
                                            <div class="form-group proxy-inline-field">
                                                <label for="web-api-key" id="web-api-key-label" data-i18n="settings.web.exa_api_key">Exa API Key</label>
                                                <div class="secure-input-row">
                                                    <input type="password" id="web-api-key" placeholder="可选，用于更高频率限制" data-i18n-placeholder="settings.web.api_key_placeholder" autocomplete="new-password" autocapitalize="off" autocorrect="off" spellcheck="false">
                                                    <button class="secure-input-btn" id="toggle-web-api-key-btn" type="button" title="Show API key" aria-label="Show API key">
                                                        <svg viewBox="0 0 24 24" fill="none" class="icon-sm" aria-hidden="true">
                                                            <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"></path>
                                                            <circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.8"></circle>
                                                        </svg>
                                                    </button>
                                                </div>
                                            </div>
                                            <div class="form-group proxy-inline-field">
                                                <label for="web-fallback-provider" data-i18n="settings.web.fallback_provider">回退提供商</label>
                                                <select id="web-fallback-provider">
                                                    <option value="searxng">SearXNG</option>
                                                    <option value="disabled">Disabled</option>
                                                </select>
                                            </div>
                                            <div class="form-group proxy-inline-field" id="web-searxng-instance-url-field" style="display:none;">
                                                <label for="web-searxng-instance-url" data-i18n="settings.web.searxng_instance_url">SearXNG 实例 URL</label>
                                                <input type="text" id="web-searxng-instance-url" placeholder="默认值：{default}" data-i18n-placeholder="settings.web.searxng_instance_url_placeholder" autocomplete="off">
                                            </div>
                                            <div class="form-group proxy-inline-field" id="web-searxng-builtins-field" style="display:none;">
                                                <span class="web-searxng-builtins-label" data-i18n="settings.web.searxng_builtin_instances">内置实例</span>
                                                <div class="web-searxng-builtins-list" id="web-searxng-builtins-list"></div>
                                            </div>
                                        </div>
                                        <div class="form-group proxy-inline-field web-provider-inline-field">
                                            <span class="web-provider-inline-label" data-i18n="settings.web.provider_site">提供商网站：</span>
                                            <a class="web-provider-link-card" id="web-provider-site-link" href="https://exa.ai" target="_blank" rel="noreferrer" title="https://exa.ai" aria-label="https://exa.ai">
                                                <span class="web-provider-link-copy">
                                                    <span class="web-provider-link-badge" id="web-provider-site-badge">Exa</span>
                                                    <span class="web-provider-link-url" id="web-provider-site-url">https://exa.ai</span>
                                                    <span class="web-provider-link-note" data-i18n="settings.web.provider_site_help">官方文档与账户概览</span>
                                                </span>
                                                <span class="web-provider-link-arrow" aria-hidden="true">
                                                    <svg viewBox="0 0 24 24" fill="none" class="icon-sm">
                                                        <path d="M7 17L17 7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path>
                                                        <path d="M9 7h8v8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path>
                                                    </svg>
                                                </span>
                                            </a>
                                        </div>
                                    </section>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="settings-panel" id="skills-panel" style="display:none;">
                        <div class="settings-section">
                            <div class="settings-content-stack">
                                <div class="status-stack" id="skills-status"></div>
                            </div>
                        </div>
                    </div>
                </div>
                <div class="settings-actions-bar" id="settings-actions-bar">
                    <div class="settings-panel-actions" id="settings-panel-actions">
                        <div class="settings-panel-actions-group settings-panel-actions-group-start">
                            <button class="secondary-btn section-action-btn settings-action" id="test-profile-btn" type="button" style="display:none;">Test</button>
                            <button class="secondary-btn section-action-btn settings-action" id="test-ssh-profile-btn" type="button" style="display:none;" data-i18n="settings.action.test">Test</button>
                            <button class="secondary-btn section-action-btn settings-action" id="test-agent-btn" type="button" style="display:none;" data-i18n="settings.action.test">Test</button>
                            <button class="secondary-btn section-action-btn settings-action" id="validate-role-btn" type="button" style="display:none;" data-i18n="settings.action.validate">Validate</button>
                        </div>
                        <div class="settings-panel-actions-group settings-panel-actions-group-end">
                            <button class="secondary-btn section-action-btn settings-action" id="add-profile-btn" type="button" style="display:none;" data-i18n="settings.action.add_profile">Add Profile</button>
                            <button class="secondary-btn section-action-btn settings-action" id="add-ssh-profile-btn" type="button" style="display:none;" data-i18n="settings.workspace.add_profile">Add SSH Profile</button>
                            <button class="primary-btn section-action-btn settings-action" id="save-profile-btn" type="button" style="display:none;" data-i18n="settings.action.save">Save</button>
                            <button class="primary-btn section-action-btn settings-action" id="save-ssh-profile-btn" type="button" style="display:none;" data-i18n="settings.action.save">Save</button>
                            <button class="secondary-btn section-action-btn settings-action" id="cancel-profile-btn" type="button" style="display:none;" data-i18n="settings.action.cancel">Cancel</button>
                            <button class="secondary-btn section-action-btn settings-action" id="cancel-ssh-profile-btn" type="button" style="display:none;" data-i18n="settings.action.cancel">Cancel</button>
                            <button class="secondary-btn section-action-btn settings-action" id="add-agent-btn" type="button" style="display:none;" data-i18n="settings.action.add_agent">Add Agent</button>
                            <button class="primary-btn section-action-btn settings-action" id="save-agent-btn" type="button" style="display:none;" data-i18n="settings.action.save">Save</button>
                            <button class="secondary-btn section-action-btn settings-action" id="delete-agent-btn" type="button" style="display:none;" data-i18n="settings.action.delete">Delete</button>
                            <button class="secondary-btn section-action-btn settings-action" id="cancel-agent-btn" type="button" style="display:none;" data-i18n="settings.action.cancel">Cancel</button>
                            <button class="secondary-btn section-action-btn settings-action" id="add-role-btn" type="button" style="display:none;" data-i18n="settings.action.add_role">Add Role</button>
                            <button class="secondary-btn section-action-btn settings-action" id="add-orchestration-preset-btn" type="button" style="display:none;" data-i18n="settings.action.add_orchestration">Add Orchestration</button>
                            <button class="secondary-btn section-action-btn settings-action" id="add-env-btn" type="button" style="display:none;" data-i18n="settings.action.add_variable">Add Variable</button>
                            <button class="primary-btn section-action-btn settings-action" id="save-role-btn" type="button" style="display:none;" data-i18n="settings.action.save">Save</button>
                            <button class="primary-btn section-action-btn settings-action" id="save-orchestration-btn" type="button" style="display:none;" data-i18n="settings.action.save">Save</button>
                            <button class="secondary-btn section-action-btn settings-action" id="cancel-role-btn" type="button" style="display:none;" data-i18n="settings.action.cancel">Cancel</button>
                            <button class="secondary-btn section-action-btn settings-action" id="cancel-orchestration-btn" type="button" style="display:none;" data-i18n="settings.action.cancel">Cancel</button>
                            <button class="primary-btn section-action-btn settings-action" id="save-env-btn" type="button" style="display:none;" data-i18n="settings.action.save">Save</button>
                            <button class="secondary-btn section-action-btn settings-action" id="cancel-env-btn" type="button" style="display:none;" data-i18n="settings.action.cancel">Cancel</button>
                            <button class="primary-btn section-action-btn settings-action" id="save-notifications-btn" type="button" style="display:none;" data-i18n="settings.action.save">Save</button>
                            <button class="primary-btn section-action-btn settings-action" id="save-web-btn" type="button" style="display:none;" data-i18n="settings.action.save">Save</button>
                            <button class="primary-btn section-action-btn settings-action" id="save-proxy-btn" type="button" style="display:none;" data-i18n="settings.action.save">Save</button>
                            <button class="secondary-btn section-action-btn settings-action" id="delete-ssh-profile-btn" type="button" style="display:none;" data-i18n="settings.action.delete">Delete</button>
                            <button class="secondary-btn section-action-btn settings-action" id="reload-mcp-btn" type="button" style="display:none;" data-i18n="settings.action.reload">Reload</button>
                            <button class="secondary-btn section-action-btn settings-action" id="reset-appearance-btn" type="button" style="display:none;" data-i18n="settings.action.reset">Reset</button>
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

    settingsModal.onmousedown = (e) => {
        overlayPointerDown = e.target === settingsModal;
    };

    settingsModal.onclick = (e) => {
        const shouldClose = overlayPointerDown && e.target === settingsModal;
        overlayPointerDown = false;
        if (shouldClose) {
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
    bindHooksSettingsHandlers();
    bindAgentSettingsHandlers();
    bindOrchestrationSettingsHandlers();
    bindRoleSettingsHandlers();
    bindEnvironmentVariableSettingsHandlers();
    bindNotificationSettingsHandlers();
    bindWebSettingsHandlers();
    bindProxySettingsHandlers();
    bindWorkspaceSettingsHandlers();
    bindSystemStatusHandlers();
    try { bindAppearanceHandlers(); } catch (e) { console.error('appearance bind failed', e); }
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
    bindHooksSettingsHandlers();
    bindAgentSettingsHandlers();
    bindOrchestrationSettingsHandlers();
    bindRoleSettingsHandlers();
    bindEnvironmentVariableSettingsHandlers();
    bindWebSettingsHandlers();
    bindProxySettingsHandlers();
    bindWorkspaceSettingsHandlers();
    bindSystemStatusHandlers();

    if (tab === 'model') {
        await loadModelProfilesPanel();
    } else if (tab === 'hooks') {
        await loadHooksSettingsPanel();
    } else if (tab === 'agents') {
        await loadAgentSettingsPanel();
    } else if (tab === 'roles') {
        await loadRoleSettingsPanel();
    } else if (tab === 'orchestration') {
        await loadOrchestrationSettingsPanel();
    } else if (tab === 'environment') {
        await loadEnvironmentVariablesPanel();
    } else if (tab === 'notifications') {
        await loadNotificationSettingsPanel();
    } else if (tab === 'web') {
        await loadWebSettingsPanel();
    } else if (tab === 'proxy') {
        await loadProxyStatusPanel();
    } else if (tab === 'workspace') {
        await loadWorkspaceSettingsPanel();
    } else if (tab === 'mcp') {
        await loadMcpStatusPanel();
    } else if (tab === 'skills') {
        await loadSkillsStatusPanel();
    } else if (tab === 'appearance') {
        loadAppearancePanel();
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
    if (tab === 'agents') {
        document.getElementById('add-agent-btn').style.display = 'inline-flex';
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
    if (tab === 'environment') {
        document.getElementById('add-env-btn').style.display = 'inline-flex';
        return;
    }
    if (tab === 'notifications') {
        document.getElementById('save-notifications-btn').style.display = 'inline-flex';
        return;
    }
    if (tab === 'web') {
        document.getElementById('save-web-btn').style.display = 'inline-flex';
        return;
    }
    if (tab === 'proxy') {
        document.getElementById('save-proxy-btn').style.display = 'inline-flex';
        return;
    }
    if (tab === 'workspace') {
        document.getElementById('add-ssh-profile-btn').style.display = 'inline-flex';
        return;
    }
    if (tab === 'mcp') {
        document.getElementById('reload-mcp-btn').style.display = 'inline-flex';
        return;
    }
    if (tab === 'appearance') {
        document.getElementById('reset-appearance-btn').style.display = 'inline-flex';
        return;
    }
    if (actionsBar) actionsBar.style.display = 'none';
}

export function openSettings(tab = null) {
    if (!initialized) initSettings();
    const normalizedTab = String(tab || '').trim();
    if (normalizedTab && TAB_METADATA[normalizedTab]) {
        currentTab = normalizedTab;
    }
    settingsModal.style.display = 'flex';
    settingsModal.classList.add('settings-modal-visible');
    showPanel(currentTab);
}

export function closeSettings() {
    if (!settingsModal) return;
    overlayPointerDown = false;
    settingsModal.classList.remove('settings-modal-visible');
    settingsModal.style.display = 'none';
}

window.openSettings = openSettings;
