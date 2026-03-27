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
            const panel = document.getElementById('notifications-panel');
            if (panel && panel.style.display !== 'none') {
                void loadNotificationSettingsPanel();
            }
        });
    }
    handlersBound = true;
}

export async function loadNotificationSettingsPanel() {
    try {
        const config = await fetchNotificationConfig();
        applyNotificationConfigToPanel(config);
    } catch (e) {
        sysLog(formatMessage('settings.notifications.log_load_failed', { error: e.message }), 'log-error');
    }
}

function collectNotificationConfigFromPanel() {
    const config = {};
    NOTIFICATION_TYPES.forEach(type => {
        const enabledEl = document.getElementById(`notif-${type}-enabled`);
        const browserEl = document.getElementById(`notif-${type}-browser`);
        const toastEl = document.getElementById(`notif-${type}-toast`);
        const channels = [];
        if (browserEl?.checked) channels.push('browser');
        if (toastEl?.checked) channels.push('toast');
        if (enabledEl?.checked && channels.length === 0) {
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
        const enabledEl = document.getElementById(`notif-${type}-enabled`);
        const browserEl = document.getElementById(`notif-${type}-browser`);
        const toastEl = document.getElementById(`notif-${type}-toast`);
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
