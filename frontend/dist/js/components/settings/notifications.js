/**
 * components/settings/notifications.js
 * Notification settings panel bindings.
 */
import { fetchNotificationConfig, saveNotificationConfig } from '../../core/api.js';
import { showToast } from '../../utils/feedback.js';
import { formatMessage, t } from '../../utils/i18n.js';
import { sysLog } from '../../utils/logger.js';

const NOTIFICATION_TYPES = [
    'tool_approval_requested',
    'run_completed',
    'run_failed',
    'run_stopped',
];

let handlersBound = false;
let notificationConfig = null;
let notificationConfigLoaded = false;

export function bindNotificationSettingsHandlers() {
    if (handlersBound) return;
    const saveBtn = document.getElementById('save-notifications-btn');
    if (saveBtn) {
        saveBtn.onclick = async () => {
            try {
                const config = collectNotificationConfigFromPanel();
                await saveNotificationConfig(config);
                showToast({
                    title: t('settings.notifications.saved'),
                    message: t('settings.notifications.saved_message'),
                    tone: 'success',
                });
                sysLog(t('settings.notifications.log_saved'));
            } catch (e) {
                showToast({
                    title: t('settings.notifications.save_failed'),
                    message: formatMessage('settings.notifications.save_failed_detail', { error: e.message }),
                    tone: 'danger',
                });
                sysLog(formatMessage('settings.notifications.log_save_failed', { error: e.message }), 'log-error');
            }
        };
    }
    NOTIFICATION_TYPES.forEach(type => {
        const enabledEl = document.getElementById(`notif-${type}-enabled`);
        if (enabledEl) {
            enabledEl.addEventListener('change', () => {
                syncRowState(type);
            });
        }
    });
    if (typeof document.addEventListener === 'function') {
        document.addEventListener('agent-teams-language-changed', () => {
            const enabledInput = document.getElementById('notif-tool_approval_requested-enabled');
            if (enabledInput) {
                void loadNotificationSettingsPanel();
            }
        });
    }
    handlersBound = true;
}

export async function loadNotificationSettingsPanel() {
    try {
        const config = await fetchNotificationConfig();
        notificationConfig = config || {};
        notificationConfigLoaded = true;
        applyNotificationConfigToPanel(config);
    } catch (e) {
        notificationConfigLoaded = false;
        sysLog(formatMessage('settings.notifications.log_load_failed', { error: e.message }), 'log-error');
    }
}

export function canSaveNotificationConfig() {
    return notificationConfigLoaded;
}

export function getLoadedNotificationConfig() {
    return notificationConfig || {};
}

export function renderNotificationSettingsSectionMarkup() {
    return `
        <section class="proxy-form-section general-setting-card">
            <div class="proxy-form-section-header general-setting-card-head general-setting-card-head-compact">
                <div class="general-setting-card-copy-block">
                    <h5 data-i18n="settings.panel.notifications.title">Notifications</h5>
                </div>
            </div>
            <div class="general-setting-card-body">
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
        </section>
    `;
}

export function collectNotificationConfigFromPanel() {
    const config = {};
    NOTIFICATION_TYPES.forEach(type => {
        const rowEl = document.querySelector(`.notification-row[data-notif-type="${type}"]`);
        const enabledEl = document.getElementById(`notif-${type}-enabled`);
        const browserEl = document.getElementById(`notif-${type}-browser`);
        const toastEl = document.getElementById(`notif-${type}-toast`);
        const channels = [];
        if (browserEl?.checked) channels.push('browser');
        if (toastEl?.checked) channels.push('toast');
        const hasHiddenChannels = rowEl?.dataset?.hasHiddenChannels === 'true';
        if (enabledEl?.checked && channels.length === 0 && !hasHiddenChannels) {
            channels.push('toast');
            if (toastEl) toastEl.checked = true;
        }
        config[type] = {
            enabled: !!enabledEl?.checked,
            channels,
        };
    });
    return config;
}

function applyNotificationConfigToPanel(config) {
    const safeConfig = (config && typeof config === 'object') ? config : {};
    NOTIFICATION_TYPES.forEach(type => {
        const rule = (safeConfig[type] && typeof safeConfig[type] === 'object')
            ? safeConfig[type]
            : { enabled: false, channels: [] };
        const channels = Array.isArray(rule.channels) ? rule.channels : [];
        const hiddenChannels = channels.filter(channel => !['browser', 'toast'].includes(channel));
        const rowEl = document.querySelector(`.notification-row[data-notif-type="${type}"]`);
        const enabledEl = document.getElementById(`notif-${type}-enabled`);
        const browserEl = document.getElementById(`notif-${type}-browser`);
        const toastEl = document.getElementById(`notif-${type}-toast`);
        if (rowEl) {
            rowEl.dataset.hasHiddenChannels = hiddenChannels.length > 0 ? 'true' : 'false';
        }
        if (enabledEl) enabledEl.checked = !!rule.enabled;
        if (browserEl) browserEl.checked = channels.includes('browser');
        if (toastEl) toastEl.checked = channels.includes('toast');
        syncRowState(type);
    });
}

function syncRowState(type) {
    const rowEl = document.querySelector(`.notification-row[data-notif-type="${type}"]`);
    const enabledEl = document.getElementById(`notif-${type}-enabled`);
    const browserEl = document.getElementById(`notif-${type}-browser`);
    const toastEl = document.getElementById(`notif-${type}-toast`);
    const enabled = !!enabledEl?.checked;
    if (browserEl) browserEl.disabled = !enabled;
    if (toastEl) toastEl.disabled = !enabled;
    if (!rowEl) return;
    if (enabled) {
        rowEl.classList.remove('notification-row-disabled');
    } else {
        rowEl.classList.add('notification-row-disabled');
    }
}
