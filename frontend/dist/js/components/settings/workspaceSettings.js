/**
 * components/settings/workspaceSettings.js
 * Workspace provider settings, currently focused on reusable SSH profiles.
 */
import {
    deleteSshProfile,
    fetchSshProfiles,
    saveSshProfile,
} from '../../core/api.js';
import { showConfirmDialog, showToast } from '../../utils/feedback.js';
import { t } from '../../utils/i18n.js';
import { errorToPayload, logError } from '../../utils/logger.js';

let sshProfiles = [];
let editingSshProfileId = null;

function formatMessage(key, values = {}) {
    return Object.entries(values).reduce(
        (result, [name, value]) => result.replaceAll(`{${name}}`, String(value)),
        t(key),
    );
}

export function bindWorkspaceSettingsHandlers() {
    bindActionButton('add-ssh-profile-btn', handleAddSshProfile);
    bindActionButton('save-ssh-profile-btn', handleSaveSshProfile);
    bindActionButton('cancel-ssh-profile-btn', handleCancelSshProfile);
    bindActionButton('delete-ssh-profile-btn', handleDeleteSshProfile);
}

export async function loadWorkspaceSettingsPanel() {
    try {
        const loadedProfiles = await fetchSshProfiles();
        sshProfiles = Array.isArray(loadedProfiles) ? loadedProfiles : [];
        renderSshProfiles();
    } catch (error) {
        logError(
            'frontend.workspace_settings.load_failed',
            'Failed to load SSH profiles',
            errorToPayload(error),
        );
        showToast({
            title: t('settings.workspace.load_failed_title'),
            message: formatMessage('settings.workspace.load_failed_detail', {
                error: String(error?.message || error || ''),
            }),
            tone: 'danger',
        });
    }
}

function renderSshProfiles() {
    const listEl = document.getElementById('workspace-ssh-profile-list');
    if (!listEl) {
        return;
    }
    showSshProfileList();
    const profileEntries = [...sshProfiles].sort((left, right) => {
        return String(left?.ssh_profile_id || '').localeCompare(String(right?.ssh_profile_id || ''));
    });
    if (profileEntries.length === 0) {
        listEl.innerHTML = `
            <div class="settings-empty-state">
                <h4>${escapeHtml(t('settings.workspace.empty_title'))}</h4>
                <p>${escapeHtml(t('settings.workspace.empty_copy'))}</p>
            </div>
        `;
        return;
    }
    listEl.innerHTML = `
        <div class="profile-records">
            ${profileEntries.map((profile, index) => renderSshProfileCard(profile, index)).join('')}
        </div>
    `;
    listEl.querySelectorAll('[data-workspace-ssh-profile-edit]').forEach(button => {
        button.onclick = () => {
            void handleEditSshProfile(button.getAttribute('data-workspace-ssh-profile-edit'));
        };
    });
    listEl.querySelectorAll('[data-workspace-ssh-profile-delete]').forEach(button => {
        button.onclick = () => {
            void handleDeleteSshProfile(button.getAttribute('data-workspace-ssh-profile-delete'));
        };
    });
}

function renderSshProfileCard(profile, index) {
    const sshProfileId = String(profile?.ssh_profile_id || '').trim();
    const host = String(profile?.host || '').trim() || t('settings.workspace.no_host');
    const username = String(profile?.username || '').trim();
    const port = formatOptionalNumber(profile?.port);
    const remoteShell = String(profile?.remote_shell || '').trim();
    const timeout = formatOptionalNumber(profile?.connect_timeout_seconds);
    const summaryParts = [];
    if (username) {
        summaryParts.push(username);
    }
    if (port) {
        summaryParts.push(`:${port}`);
    }
    return `
        <div class="profile-record profile-card" data-ssh-profile-id="${escapeHtml(sshProfileId)}" style="--profile-index:${index};">
            <div class="profile-record-main">
                <div class="profile-record-heading">
                    <div class="profile-card-heading">
                        <div class="profile-card-title-row">
                            <h4>${escapeHtml(sshProfileId)}</h4>
                            <div class="profile-card-chips">
                                <span class="profile-card-chip">${escapeHtml(t('settings.workspace.profile_chip'))}</span>
                            </div>
                        </div>
                        <div class="profile-record-summary" title="${escapeHtml(host)}">
                            <span class="profile-record-summary-primary">${escapeHtml(host)}</span>
                            ${summaryParts.length > 0
                                ? `<span class="profile-record-summary-separator">/</span><span class="profile-record-summary-secondary">${escapeHtml(summaryParts.join(' '))}</span>`
                                : ''
                            }
                        </div>
                        <div class="profile-record-summary" title="${escapeHtml(remoteShell || t('settings.workspace.shell_default'))}">
                            <span class="profile-record-summary-primary">${escapeHtml(remoteShell || t('settings.workspace.shell_default'))}</span>
                            <span class="profile-record-summary-separator">/</span>
                            <span class="profile-record-summary-secondary">${escapeHtml(timeout ? formatMessage('settings.workspace.timeout_value', { value: timeout }) : t('settings.workspace.timeout_default'))}</span>
                        </div>
                    </div>
                </div>
                <div class="profile-card-actions">
                    <button class="settings-inline-action settings-list-action profile-card-action-btn" type="button" data-workspace-ssh-profile-edit="${escapeHtml(sshProfileId)}">${escapeHtml(t('settings.action.edit'))}</button>
                    <button class="settings-inline-action settings-list-action settings-list-action-danger profile-card-action-btn" type="button" data-workspace-ssh-profile-delete="${escapeHtml(sshProfileId)}">${escapeHtml(t('settings.action.delete'))}</button>
                </div>
            </div>
        </div>
    `;
}

function handleAddSshProfile() {
    editingSshProfileId = null;
    setInputValue('workspace-ssh-profile-id', '');
    setInputValue('workspace-ssh-profile-host', '');
    setInputValue('workspace-ssh-profile-username', '');
    setInputValue('workspace-ssh-profile-port', '');
    setInputValue('workspace-ssh-profile-shell', '');
    setInputValue('workspace-ssh-profile-timeout', '');
    const title = document.getElementById('workspace-ssh-profile-editor-title');
    if (title) {
        title.textContent = t('settings.workspace.add_profile');
    }
    showSshProfileEditor();
    document.getElementById('workspace-ssh-profile-id')?.focus?.();
}

function handleEditSshProfile(sshProfileId) {
    const normalizedId = String(sshProfileId || '').trim();
    const matched = sshProfiles.find(profile => String(profile?.ssh_profile_id || '').trim() === normalizedId);
    if (!matched) {
        return;
    }
    editingSshProfileId = normalizedId;
    setInputValue('workspace-ssh-profile-id', normalizedId);
    setInputValue('workspace-ssh-profile-host', matched.host);
    setInputValue('workspace-ssh-profile-username', matched.username);
    setInputValue('workspace-ssh-profile-port', formatOptionalNumber(matched.port));
    setInputValue('workspace-ssh-profile-shell', matched.remote_shell);
    setInputValue('workspace-ssh-profile-timeout', formatOptionalNumber(matched.connect_timeout_seconds));
    const title = document.getElementById('workspace-ssh-profile-editor-title');
    if (title) {
        title.textContent = t('settings.workspace.edit_profile');
    }
    showSshProfileEditor();
    document.getElementById('workspace-ssh-profile-host')?.focus?.();
}

async function handleSaveSshProfile() {
    const sshProfileId = String(readInputValue('workspace-ssh-profile-id')).trim();
    const host = String(readInputValue('workspace-ssh-profile-host')).trim();
    if (!sshProfileId) {
        showToast({
            title: t('settings.workspace.validation_failed_title'),
            message: t('settings.workspace.profile_id_required'),
            tone: 'danger',
        });
        return;
    }
    if (!host) {
        showToast({
            title: t('settings.workspace.validation_failed_title'),
            message: t('settings.workspace.host_required'),
            tone: 'danger',
        });
        return;
    }

    try {
        const saved = await saveSshProfile(sshProfileId, {
            host,
            username: normalizeOptionalText(readInputValue('workspace-ssh-profile-username')),
            port: parseOptionalInteger(readInputValue('workspace-ssh-profile-port')),
            remote_shell: normalizeOptionalText(readInputValue('workspace-ssh-profile-shell')),
            connect_timeout_seconds: parseOptionalInteger(readInputValue('workspace-ssh-profile-timeout')),
        });
        await loadWorkspaceSettingsPanel();
        editingSshProfileId = String(saved?.ssh_profile_id || sshProfileId).trim();
        showToast({
            title: t('settings.workspace.saved_title'),
            message: formatMessage('settings.workspace.saved_detail', { ssh_profile_id: editingSshProfileId }),
            tone: 'success',
        });
    } catch (error) {
        showToast({
            title: t('settings.workspace.save_failed_title'),
            message: formatMessage('settings.workspace.save_failed_detail', {
                error: String(error?.message || error || ''),
            }),
            tone: 'danger',
        });
    }
}

function handleCancelSshProfile() {
    editingSshProfileId = null;
    renderSshProfiles();
}

async function handleDeleteSshProfile(explicitProfileId = null) {
    const sshProfileId = String(explicitProfileId || editingSshProfileId || readInputValue('workspace-ssh-profile-id')).trim();
    if (!sshProfileId) {
        return;
    }
    const confirmed = await showConfirmDialog({
        title: t('settings.workspace.delete_title'),
        message: formatMessage('settings.workspace.delete_message', { ssh_profile_id: sshProfileId }),
        tone: 'warning',
        confirmLabel: t('settings.action.delete'),
        cancelLabel: t('settings.action.cancel'),
    });
    if (confirmed !== true) {
        return;
    }
    try {
        await deleteSshProfile(sshProfileId);
        editingSshProfileId = null;
        await loadWorkspaceSettingsPanel();
        showToast({
            title: t('settings.workspace.deleted_title'),
            message: formatMessage('settings.workspace.deleted_detail', { ssh_profile_id: sshProfileId }),
            tone: 'success',
        });
    } catch (error) {
        showToast({
            title: t('settings.workspace.delete_failed_title'),
            message: formatMessage('settings.workspace.delete_failed_detail', {
                error: String(error?.message || error || ''),
            }),
            tone: 'danger',
        });
    }
}

function showSshProfileList() {
    setElementDisplay('workspace-ssh-profile-list', 'block');
    setElementDisplay('workspace-ssh-profile-editor', 'none');
    toggleWorkspaceActions({
        add: true,
        save: false,
        cancel: false,
        delete: false,
    });
}

function showSshProfileEditor() {
    setElementDisplay('workspace-ssh-profile-list', 'none');
    setElementDisplay('workspace-ssh-profile-editor', 'block');
    toggleWorkspaceActions({
        add: false,
        save: true,
        cancel: true,
        delete: Boolean(editingSshProfileId),
    });
}

function toggleWorkspaceActions(visibility) {
    setActionDisplay('add-ssh-profile-btn', visibility.add);
    setActionDisplay('save-ssh-profile-btn', visibility.save);
    setActionDisplay('cancel-ssh-profile-btn', visibility.cancel);
    setActionDisplay('delete-ssh-profile-btn', visibility.delete);
}

function bindActionButton(id, handler) {
    const button = document.getElementById(id);
    if (button) {
        button.onclick = handler;
    }
}

function setActionDisplay(id, visible) {
    const button = document.getElementById(id);
    if (button) {
        button.style.display = visible ? 'inline-flex' : 'none';
    }
}

function setElementDisplay(id, value) {
    const element = document.getElementById(id);
    if (element) {
        element.style.display = value;
    }
}

function setInputValue(id, value) {
    const input = document.getElementById(id);
    if (input) {
        input.value = String(value || '');
    }
}

function readInputValue(id) {
    const input = document.getElementById(id);
    return input ? String(input.value || '') : '';
}

function normalizeOptionalText(value) {
    const normalized = String(value || '').trim();
    return normalized || null;
}

function parseOptionalInteger(value) {
    const normalized = String(value || '').trim();
    if (!normalized) {
        return null;
    }
    const parsed = Number.parseInt(normalized, 10);
    return Number.isFinite(parsed) ? parsed : null;
}

function formatOptionalNumber(value) {
    if (typeof value !== 'number' || !Number.isFinite(value)) {
        return '';
    }
    return String(value);
}

function escapeHtml(value) {
    return String(value)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
