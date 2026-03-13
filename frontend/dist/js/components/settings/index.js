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
import { bindProxySettingsHandlers, loadProxyStatusPanel } from './proxySettings.js';
import { bindRoleSettingsHandlers, loadRoleSettingsPanel } from './rolesSettings.js';
import { bindSystemStatusHandlers, loadMcpStatusPanel, loadSkillsStatusPanel } from './systemStatus.js';

let settingsModal = null;
let currentTab = 'model';
let initialized = false;

const TAB_METADATA = {
    model: {
        title: 'Model Profiles',
        description: 'Manage providers, endpoints, request limits, and sampling defaults.',
    },
    roles: {
        title: 'Roles',
        description: 'Edit role metadata, allowed tools, workspace profile, and prompt text.',
    },
    environment: {
        title: 'Environment Variables',
        description: 'Inspect system environment values and manage Agent Teams app environment variables.',
    },
    notifications: {
        title: 'Notifications',
        description: 'Choose which run events notify you and where they are delivered.',
    },
    proxy: {
        title: 'Proxy',
        description: 'Edit runtime proxy values and test outbound web connectivity.',
    },
    mcp: {
        title: 'MCP Config',
        description: 'Review the currently loaded MCP servers and reload the runtime view.',
    },
    skills: {
        title: 'Skills',
        description: 'Check installed skills and refresh the server-side registry.',
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
                    <h2>Settings</h2>
                </div>
                <div class="settings-tabs" role="tablist" aria-label="Settings Sections">
                    <button class="settings-tab active" data-tab="model">
                        <span class="settings-tab-label">Model Profiles</span>
                    </button>
                    <button class="settings-tab" data-tab="roles">
                        <span class="settings-tab-label">Roles</span>
                    </button>
                    <button class="settings-tab" data-tab="environment">
                        <span class="settings-tab-label">Environment</span>
                    </button>
                    <button class="settings-tab" data-tab="notifications">
                        <span class="settings-tab-label">Notifications</span>
                    </button>
                    <button class="settings-tab" data-tab="proxy">
                        <span class="settings-tab-label">Proxy</span>
                    </button>
                    <button class="settings-tab" data-tab="mcp">
                        <span class="settings-tab-label">MCP Config</span>
                    </button>
                    <button class="settings-tab" data-tab="skills">
                        <span class="settings-tab-label">Skills</span>
                    </button>
                </div>
            </aside>
            <section class="settings-main">
                <div class="modal-header settings-modal-header">
                    <div class="settings-modal-heading">
                        <h2 id="settings-panel-title">Model Profiles</h2>
                        <p id="settings-panel-description">Manage providers, endpoints, request limits, and sampling defaults.</p>
                    </div>
                    <button class="close-btn" id="settings-close" aria-label="Close Settings">&times;</button>
                </div>
                <div class="settings-body">
                    <div class="settings-panel" id="model-panel">
                        <div class="settings-section settings-section-model">
                            <div class="settings-content-stack settings-model-stack">
                                <div class="profiles-list" id="profiles-list"></div>
                                <div class="profile-editor" id="profile-editor" style="display:none;">
                                    <div class="profile-editor-header">
                                        <h4 id="profile-editor-title">Add Profile</h4>
                                        <p>Update the endpoint first, then tune runtime sampling and request limits.</p>
                                    </div>
                                    <form class="profile-editor-form" id="profile-editor-form" autocomplete="off">
                                        <div class="profile-editor-grid">
                                            <div class="form-group">
                                                <label for="profile-name">Profile Name</label>
                                                <input type="text" id="profile-name" placeholder="e.g., default, kimi" autocomplete="off">
                                            </div>
                                            <div class="form-group">
                                                <label for="profile-model">Model</label>
                                                <input type="text" id="profile-model" placeholder="e.g., gpt-4o, kimi-k2.5" autocomplete="off">
                                            </div>
                                            <div class="form-group form-group-span-2">
                                                <label for="profile-base-url">Base URL</label>
                                                <input type="text" id="profile-base-url" placeholder="e.g., https://api.openai.com/v1" autocomplete="url">
                                            </div>
                                            <div class="form-group form-group-span-2">
                                                <label for="profile-api-key">API Key</label>
                                                <input type="password" id="profile-api-key" placeholder="sk-..." autocomplete="current-password">
                                            </div>
                                        </div>
                                        <div class="profile-editor-subsection">
                                            <h5>Request Controls</h5>
                                            <div class="form-row">
                                                <div class="form-group">
                                                    <label for="profile-temperature">Temperature</label>
                                                    <input type="number" id="profile-temperature" value="0.7" step="0.1" min="0" max="2" autocomplete="off">
                                                </div>
                                                <div class="form-group">
                                                    <label for="profile-top-p">Top P</label>
                                                    <input type="number" id="profile-top-p" value="1.0" step="0.1" min="0" max="1" autocomplete="off">
                                                </div>
                                                <div class="form-group">
                                                    <label for="profile-max-tokens">Max Output Tokens</label>
                                                    <input type="number" id="profile-max-tokens" value="4096" min="1" autocomplete="off">
                                                </div>
                                                <div class="form-group">
                                                    <label for="profile-connect-timeout">Connect Timeout (s)</label>
                                                    <input type="number" id="profile-connect-timeout" value="15" step="1" min="1" max="300" autocomplete="off">
                                                </div>
                                            </div>
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
                                        <h4>No role selected</h4>
                                        <p>Select a role to edit its metadata and prompt.</p>
                                    </div>
                                    <div class="role-editor-form" id="role-editor-form" style="display:none;">
                                        <div class="role-editor-header">
                                            <div>
                                                <h4>Role Editor</h4>
                                                <p id="role-file-meta"></p>
                                            </div>
                                        </div>
                                        <div class="role-editor-sections">
                                            <section class="role-editor-section">
                                                <div class="profile-editor-grid role-editor-grid">
                                                    <div class="form-group">
                                                        <label for="role-id-input">Role ID</label>
                                                        <input type="text" id="role-id-input" placeholder="e.g. spec_coder">
                                                    </div>
                                                    <div class="form-group">
                                                        <label for="role-name-input">Name</label>
                                                        <input type="text" id="role-name-input" placeholder="e.g. Spec Coder">
                                                    </div>
                                                    <div class="form-group">
                                                        <label for="role-version-input">Version</label>
                                                        <input type="text" id="role-version-input" placeholder="e.g. 1.0.0">
                                                    </div>
                                                    <div class="form-group">
                                                        <label for="role-model-profile-input">Model Profile</label>
                                                        <select id="role-model-profile-input"></select>
                                                    </div>
                                                </div>
                                            </section>
                                            <section class="role-editor-section">
                                                <h5>Allowed Tools</h5>
                                                <div class="role-option-picker role-option-picker-tools" id="role-tools-picker"></div>
                                            </section>
                                            <section class="role-editor-section">
                                                <h5>MCP Servers</h5>
                                                <div class="role-option-picker role-option-picker-single" id="role-mcp-picker"></div>
                                            </section>
                                            <section class="role-editor-section">
                                                <h5>Skills</h5>
                                                <div class="role-option-picker role-option-picker-single" id="role-skills-picker"></div>
                                            </section>
                                            <section class="role-editor-section">
                                                <h5>Workspace</h5>
                                                <div class="role-workspace-row">
                                                    <div class="form-group">
                                                        <label for="role-workspace-binding-input">Binding</label>
                                                        <select id="role-workspace-binding-input"></select>
                                                    </div>
                                                    <p class="role-workspace-note" id="role-workspace-note">
                                                        Advanced workspace profile fields stay preserved. This editor only changes the binding mode.
                                                    </p>
                                                </div>
                                            </section>
                                            <section class="role-editor-section">
                                                <div class="role-prompt-header">
                                                    <h5>System Prompt</h5>
                                                    <div class="role-prompt-tabs">
                                                        <button class="role-prompt-tab active" id="role-prompt-edit-tab" type="button">Edit</button>
                                                        <button class="role-prompt-tab" id="role-prompt-preview-tab" type="button">Preview</button>
                                                    </div>
                                                </div>
                                                <textarea class="config-textarea role-prompt-textarea" id="role-system-prompt-input" placeholder="Write the role prompt here"></textarea>
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
                                <div class="env-editor-shell" id="env-editor-shell" style="display:none;">
                                    <div class="env-editor-header">
                                        <div>
                                            <h5 id="env-editor-title">Add Environment Variable</h5>
                                            <p id="env-editor-meta">Choose a scope, then save the key and value.</p>
                                        </div>
                                    </div>
                                    <input type="hidden" id="env-source-key-input" value="">
                                    <div class="env-editor-grid">
                                        <div class="form-group env-inline-field env-inline-field-compact">
                                            <label for="env-scope-select">Scope</label>
                                            <select id="env-scope-select">
                                                <option value="app">App</option>
                                            </select>
                                        </div>
                                        <div class="form-group env-inline-field">
                                            <label for="env-key-input">Key</label>
                                            <input type="text" id="env-key-input" placeholder="e.g. OPENAI_API_KEY" autocomplete="off">
                                        </div>
                                        <div class="form-group env-inline-field env-inline-field-value">
                                            <label for="env-value-input">Value</label>
                                            <textarea id="env-value-input" class="config-textarea env-value-textarea" placeholder="Variable value"></textarea>
                                        </div>
                                    </div>
                                </div>
                                <div class="env-groups" id="environment-variables-groups"></div>
                            </div>
                        </div>
                    </div>
                    <div class="settings-panel" id="notifications-panel" style="display:none;">
                        <div class="settings-section">
                            <div class="settings-content-stack notifications-panel-body">
                                <p class="notifications-help">
                                    A notification is sent only when <strong>Enabled</strong> is on and at least one delivery channel is selected.
                                </p>
                                <div class="notification-grid">
                                    <div class="notification-row" data-notif-type="tool_approval_requested">
                                        <div class="notification-row-main">
                                            <div class="notification-row-title">Tool approval requested</div>
                                            <div class="notification-row-desc">When an agent asks for approval before a tool call.</div>
                                        </div>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-tool_approval_requested-enabled">
                                            <span class="notification-toggle-check" aria-hidden="true"></span>
                                            <span class="notification-toggle-label">Enabled</span>
                                        </label>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-tool_approval_requested-browser">
                                            <span class="notification-toggle-check" aria-hidden="true"></span>
                                            <span class="notification-toggle-label">Browser</span>
                                        </label>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-tool_approval_requested-toast">
                                            <span class="notification-toggle-check" aria-hidden="true"></span>
                                            <span class="notification-toggle-label">Toast</span>
                                        </label>
                                    </div>
                                    <div class="notification-row" data-notif-type="run_completed">
                                        <div class="notification-row-main">
                                            <div class="notification-row-title">Run completed</div>
                                            <div class="notification-row-desc">When a run finishes successfully.</div>
                                        </div>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_completed-enabled">
                                            <span class="notification-toggle-check" aria-hidden="true"></span>
                                            <span class="notification-toggle-label">Enabled</span>
                                        </label>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_completed-browser">
                                            <span class="notification-toggle-check" aria-hidden="true"></span>
                                            <span class="notification-toggle-label">Browser</span>
                                        </label>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_completed-toast">
                                            <span class="notification-toggle-check" aria-hidden="true"></span>
                                            <span class="notification-toggle-label">Toast</span>
                                        </label>
                                    </div>
                                    <div class="notification-row" data-notif-type="run_failed">
                                        <div class="notification-row-main">
                                            <div class="notification-row-title">Run failed</div>
                                            <div class="notification-row-desc">When a run stops because of an error.</div>
                                        </div>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_failed-enabled">
                                            <span class="notification-toggle-check" aria-hidden="true"></span>
                                            <span class="notification-toggle-label">Enabled</span>
                                        </label>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_failed-browser">
                                            <span class="notification-toggle-check" aria-hidden="true"></span>
                                            <span class="notification-toggle-label">Browser</span>
                                        </label>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_failed-toast">
                                            <span class="notification-toggle-check" aria-hidden="true"></span>
                                            <span class="notification-toggle-label">Toast</span>
                                        </label>
                                    </div>
                                    <div class="notification-row" data-notif-type="run_stopped">
                                        <div class="notification-row-main">
                                            <div class="notification-row-title">Run stopped</div>
                                            <div class="notification-row-desc">When a run is stopped by user action.</div>
                                        </div>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_stopped-enabled">
                                            <span class="notification-toggle-check" aria-hidden="true"></span>
                                            <span class="notification-toggle-label">Enabled</span>
                                        </label>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_stopped-browser">
                                            <span class="notification-toggle-check" aria-hidden="true"></span>
                                            <span class="notification-toggle-label">Browser</span>
                                        </label>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_stopped-toast">
                                            <span class="notification-toggle-check" aria-hidden="true"></span>
                                            <span class="notification-toggle-label">Toast</span>
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
                                            <h5>Proxy Settings</h5>
                                        </div>
                                        <div class="proxy-form-grid">
                                            <div class="form-group proxy-inline-field">
                                                <label for="proxy-http-proxy">HTTP Proxy</label>
                                                <input type="text" id="proxy-http-proxy" placeholder="http://127.0.0.1:7890" autocomplete="off">
                                            </div>
                                            <div class="form-group proxy-inline-field">
                                                <label for="proxy-https-proxy">HTTPS Proxy</label>
                                                <input type="text" id="proxy-https-proxy" placeholder="http://127.0.0.1:7890" autocomplete="off">
                                            </div>
                                            <div class="form-group proxy-inline-field">
                                                <label for="proxy-all-proxy">ALL Proxy</label>
                                                <input type="text" id="proxy-all-proxy" placeholder="socks5://127.0.0.1:7890" autocomplete="off">
                                            </div>
                                            <div class="form-group proxy-inline-field">
                                                <label for="proxy-username">Username</label>
                                                <input type="text" id="proxy-username" placeholder="Optional proxy username" autocomplete="username">
                                            </div>
                                            <div class="form-group proxy-inline-field">
                                                <label for="proxy-password">Password</label>
                                                <input type="password" id="proxy-password" placeholder="Optional proxy password" autocomplete="current-password">
                                            </div>
                                            <div class="form-group proxy-inline-field">
                                                <label for="proxy-no-proxy">NO_PROXY</label>
                                                <input type="text" id="proxy-no-proxy" placeholder="localhost;127.*;192.168.*;<local>" autocomplete="off">
                                            </div>
                                            <div class="form-group proxy-inline-field proxy-inline-field-compact">
                                                <label class="notification-toggle" for="proxy-verify-ssl">
                                                    <input type="checkbox" id="proxy-verify-ssl" checked>
                                                    <span class="notification-toggle-check" aria-hidden="true"></span>
                                                    <span class="notification-toggle-label">Verify SSL</span>
                                                </label>
                                            </div>
                                        </div>
                                    </section>
                                    <section class="proxy-form-section proxy-form-section-test">
                                        <div class="proxy-form-section-header">
                                            <h5>Connectivity Test</h5>
                                        </div>
                                        <div class="proxy-probe-grid">
                                            <div class="form-group proxy-inline-field proxy-inline-field-test">
                                                <label for="proxy-probe-url">Target URL</label>
                                                <input type="text" id="proxy-probe-url" placeholder="https://example.com" autocomplete="url">
                                                <button class="secondary-btn proxy-inline-test-btn" id="test-proxy-web-btn" type="button">Test URL</button>
                                            </div>
                                            <div class="form-group proxy-inline-field proxy-inline-field-compact">
                                                <label for="proxy-probe-timeout">Timeout (ms)</label>
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
                            <button class="primary-btn section-action-btn settings-action" id="test-profile-btn" type="button" style="display:none;">Test</button>
                            <button class="primary-btn section-action-btn settings-action" id="validate-role-btn" type="button" style="display:none;">Validate</button>
                        </div>
                        <div class="settings-panel-actions-group settings-panel-actions-group-end">
                            <button class="primary-btn section-action-btn settings-action" id="add-profile-btn" type="button" style="display:none;">Add Profile</button>
                            <button class="primary-btn section-action-btn settings-action" id="save-profile-btn" type="button" style="display:none;">Save</button>
                            <button class="primary-btn section-action-btn settings-action" id="cancel-profile-btn" type="button" style="display:none;">Cancel</button>
                            <button class="primary-btn section-action-btn settings-action" id="add-role-btn" type="button" style="display:none;">Add Role</button>
                            <button class="primary-btn section-action-btn settings-action" id="add-env-btn" type="button" style="display:none;">Add Variable</button>
                            <button class="primary-btn section-action-btn settings-action" id="save-role-btn" type="button" style="display:none;">Save</button>
                            <button class="primary-btn section-action-btn settings-action" id="cancel-role-btn" type="button" style="display:none;">Cancel</button>
                            <button class="primary-btn section-action-btn settings-action" id="save-env-btn" type="button" style="display:none;">Save</button>
                            <button class="primary-btn section-action-btn settings-action" id="cancel-env-btn" type="button" style="display:none;">Cancel</button>
                            <button class="primary-btn section-action-btn settings-action" id="save-notifications-btn" type="button" style="display:none;">Save</button>
                            <button class="primary-btn section-action-btn settings-action" id="save-proxy-btn" type="button" style="display:none;">Save</button>
                            <button class="primary-btn section-action-btn settings-action" id="reload-mcp-btn" type="button" style="display:none;">Reload</button>
                            <button class="primary-btn section-action-btn settings-action" id="reload-skills-btn" type="button" style="display:none;">Reload</button>
                        </div>
                    </div>
                </div>
            </section>
        </div>
    `;
    document.body.appendChild(settingsModal);
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
    bindRoleSettingsHandlers();
    bindEnvironmentVariableSettingsHandlers();
    bindNotificationSettingsHandlers();
    bindProxySettingsHandlers();
    bindSystemStatusHandlers();
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

    const meta = TAB_METADATA[tab] || TAB_METADATA.model;
    document.getElementById('settings-panel-title').textContent = meta.title;
    document.getElementById('settings-panel-description').textContent = meta.description;
    renderPanelActions(tab);
    bindModelProfileHandlers();
    bindRoleSettingsHandlers();
    bindEnvironmentVariableSettingsHandlers();
    bindProxySettingsHandlers();
    bindSystemStatusHandlers();

    if (tab === 'model') {
        await loadModelProfilesPanel();
    } else if (tab === 'roles') {
        await loadRoleSettingsPanel();
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
