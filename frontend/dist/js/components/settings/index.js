/**
 * components/settings/index.js
 * Settings modal shell and tab routing.
 */
import { bindModelProfileHandlers, loadModelProfilesPanel } from './modelProfiles.js';
import {
    bindNotificationSettingsHandlers,
    loadNotificationSettingsPanel,
} from './notifications.js';
import { bindSystemStatusHandlers, loadMcpStatusPanel, loadSkillsStatusPanel } from './systemStatus.js';

let settingsModal = null;
let currentTab = 'model';
let initialized = false;

const TAB_METADATA = {
    model: {
        title: 'Model Profiles',
        description: 'Manage providers, endpoints, request limits, and sampling defaults.',
    },
    notifications: {
        title: 'Notifications',
        description: 'Choose which run events notify you and where they are delivered.',
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
                    <button class="settings-tab" data-tab="notifications">
                        <span class="settings-tab-label">Notifications</span>
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
                    <div class="settings-panel-actions" id="settings-panel-actions"></div>
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
                                    <div class="profile-editor-grid">
                                        <div class="form-group">
                                            <label>Profile Name</label>
                                            <input type="text" id="profile-name" placeholder="e.g., default, kimi">
                                        </div>
                                        <div class="form-group">
                                            <label>Model</label>
                                            <input type="text" id="profile-model" placeholder="e.g., gpt-4o, kimi-k2.5">
                                        </div>
                                        <div class="form-group form-group-span-2">
                                            <label>Base URL</label>
                                            <input type="text" id="profile-base-url" placeholder="e.g., https://api.openai.com/v1">
                                        </div>
                                        <div class="form-group form-group-span-2">
                                            <label>API Key</label>
                                            <input type="password" id="profile-api-key" placeholder="sk-...">
                                        </div>
                                    </div>
                                    <div class="profile-editor-subsection">
                                        <h5>Request Controls</h5>
                                        <div class="form-row">
                                            <div class="form-group">
                                                <label>Temperature</label>
                                                <input type="number" id="profile-temperature" value="0.7" step="0.1" min="0" max="2">
                                            </div>
                                            <div class="form-group">
                                                <label>Top P</label>
                                                <input type="number" id="profile-top-p" value="1.0" step="0.1" min="0" max="1">
                                            </div>
                                            <div class="form-group">
                                                <label>Max Output Tokens</label>
                                                <input type="number" id="profile-max-tokens" value="4096" min="1">
                                            </div>
                                            <div class="form-group">
                                                <label>Connect Timeout (s)</label>
                                                <input type="number" id="profile-connect-timeout" value="15" step="1" min="1" max="300">
                                            </div>
                                        </div>
                                    </div>
                                    <div class="form-actions">
                                        <button class="primary-btn section-action-btn" id="save-profile-btn">Save</button>
                                        <button class="secondary-btn" id="test-profile-btn" type="button">Test Connection</button>
                                        <button class="secondary-btn" id="cancel-profile-btn">Cancel</button>
                                    </div>
                                    <div class="profile-probe-status" id="profile-probe-status" style="display:none;"></div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="settings-panel" id="mcp-panel" style="display:none;">
                        <div class="settings-section">
                            <div class="settings-content-stack status-stack" id="mcp-status"></div>
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
                                            <span>Enabled</span>
                                        </label>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-tool_approval_requested-browser">
                                            <span>Browser</span>
                                        </label>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-tool_approval_requested-toast">
                                            <span>Toast</span>
                                        </label>
                                    </div>
                                    <div class="notification-row" data-notif-type="run_completed">
                                        <div class="notification-row-main">
                                            <div class="notification-row-title">Run completed</div>
                                            <div class="notification-row-desc">When a run finishes successfully.</div>
                                        </div>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_completed-enabled">
                                            <span>Enabled</span>
                                        </label>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_completed-browser">
                                            <span>Browser</span>
                                        </label>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_completed-toast">
                                            <span>Toast</span>
                                        </label>
                                    </div>
                                    <div class="notification-row" data-notif-type="run_failed">
                                        <div class="notification-row-main">
                                            <div class="notification-row-title">Run failed</div>
                                            <div class="notification-row-desc">When a run stops because of an error.</div>
                                        </div>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_failed-enabled">
                                            <span>Enabled</span>
                                        </label>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_failed-browser">
                                            <span>Browser</span>
                                        </label>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_failed-toast">
                                            <span>Toast</span>
                                        </label>
                                    </div>
                                    <div class="notification-row" data-notif-type="run_stopped">
                                        <div class="notification-row-main">
                                            <div class="notification-row-title">Run stopped</div>
                                            <div class="notification-row-desc">When a run is stopped by user action.</div>
                                        </div>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_stopped-enabled">
                                            <span>Enabled</span>
                                        </label>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_stopped-browser">
                                            <span>Browser</span>
                                        </label>
                                        <label class="notification-toggle">
                                            <input type="checkbox" id="notif-run_stopped-toast">
                                            <span>Toast</span>
                                        </label>
                                    </div>
                                </div>
                                <div class="notifications-actions">
                                    <button class="primary-btn section-action-btn" id="save-notifications-btn">Save Notifications</button>
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
    bindNotificationSettingsHandlers();
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
    bindSystemStatusHandlers();

    if (tab === 'model') {
        await loadModelProfilesPanel();
    } else if (tab === 'notifications') {
        await loadNotificationSettingsPanel();
    } else if (tab === 'mcp') {
        await loadMcpStatusPanel();
    } else if (tab === 'skills') {
        await loadSkillsStatusPanel();
    }
}

function renderPanelActions(tab) {
    const actions = document.getElementById('settings-panel-actions');
    if (!actions) {
        return;
    }

    if (tab === 'model') {
        actions.innerHTML = '<button class="primary-btn section-action-btn" id="add-profile-btn" type="button">Add Profile</button>';
        return;
    }

    if (tab === 'mcp') {
        actions.innerHTML = '<button class="primary-btn section-action-btn" id="reload-mcp-btn" type="button">Reload</button>';
        return;
    }

    if (tab === 'skills') {
        actions.innerHTML = '<button class="primary-btn section-action-btn" id="reload-skills-btn" type="button">Reload</button>';
        return;
    }

    actions.innerHTML = '';
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
