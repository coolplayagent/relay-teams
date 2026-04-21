/**
 * components/settings/workspaceSettings.js
 * Workspace provider settings, currently focused on reusable SSH profiles.
 */
import {
    deleteSshProfile,
    fetchSshProfiles,
    probeSshProfileConnection,
    revealSshProfilePassword,
    saveSshProfile,
} from '../../core/api.js';
import { showConfirmDialog, showToast } from '../../utils/feedback.js';
import { t } from '../../utils/i18n.js';
import { errorToPayload, logError } from '../../utils/logger.js';

const MASKED_SECRET_PLACEHOLDER = '************';

let sshProfiles = [];
let editingSshProfileId = null;
let sshPasswordState = createWorkspacePasswordState();
let sshProfileProbeStates = {};
let draftSshProfileProbeState = null;

function formatMessage(key, values = {}) {
    return Object.entries(values).reduce(
        (result, [name, value]) => result.replaceAll(`{${name}}`, String(value)),
        t(key),
    );
}

export function bindWorkspaceSettingsHandlers() {
    bindActionButton('add-ssh-profile-btn', handleAddSshProfile);
    bindActionButton('test-ssh-profile-btn', handleTestDraftSshProfile);
    bindActionButton('save-ssh-profile-btn', handleSaveSshProfile);
    bindActionButton('cancel-ssh-profile-btn', handleCancelSshProfile);
    bindActionButton('delete-ssh-profile-btn', handleDeleteSshProfile);
    bindPrivateKeyImportHandlers();
    bindSecretStateHandlers();
    bindPasswordHandlers();
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
    listEl.querySelectorAll('[data-workspace-ssh-profile-test]').forEach(button => {
        button.onclick = () => {
            void handleTestSshProfile(button.getAttribute('data-workspace-ssh-profile-test'));
        };
    });
}

function renderSshProfileCard(profile, index) {
    const sshProfileId = String(profile?.ssh_profile_id || '').trim();
    const probeState = sshProfileProbeStates[sshProfileId] || null;
    const testButtonLabel = probeState?.status === 'probing' ? t('settings.workspace.testing') : t('settings.action.test');
    const host = String(profile?.host || '').trim() || t('settings.workspace.no_host');
    const username = String(profile?.username || '').trim();
    const port = formatOptionalNumber(profile?.port);
    const remoteShell = String(profile?.remote_shell || '').trim();
    const timeout = formatOptionalNumber(profile?.connect_timeout_seconds);
    const authSummary = buildAuthSummary(profile);
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
                        <div class="profile-record-summary" title="${escapeHtml(authSummary)}">
                            <span class="profile-record-summary-primary">${escapeHtml(authSummary)}</span>
                        </div>
                    </div>
                </div>
                <div class="profile-card-actions">
                    <button class="settings-inline-action settings-list-action profile-card-action-btn workspace-profile-card-test-btn" type="button" data-workspace-ssh-profile-test="${escapeHtml(sshProfileId)}" title="${escapeHtml(t('settings.action.test'))}" ${probeState?.status === 'probing' ? 'disabled' : ''}>${escapeHtml(testButtonLabel)}</button>
                    <button class="settings-inline-action settings-list-action profile-card-action-btn" type="button" data-workspace-ssh-profile-edit="${escapeHtml(sshProfileId)}">${escapeHtml(t('settings.action.edit'))}</button>
                    <button class="settings-inline-action settings-list-action settings-list-action-danger profile-card-action-btn" type="button" data-workspace-ssh-profile-delete="${escapeHtml(sshProfileId)}">${escapeHtml(t('settings.action.delete'))}</button>
                </div>
            </div>
            <div class="profile-card-inline-status" data-workspace-ssh-profile-probe-container="${escapeHtml(sshProfileId)}">
                ${renderSshProbeStatusMarkup(probeState)}
            </div>
        </div>
    `;
}

function handleAddSshProfile() {
    editingSshProfileId = null;
    sshPasswordState = createWorkspacePasswordState();
    draftSshProfileProbeState = null;
    setInputValue('workspace-ssh-profile-id', '');
    setInputValue('workspace-ssh-profile-host', '');
    setInputValue('workspace-ssh-profile-username', '');
    setInputValue('workspace-ssh-profile-port', '');
    setInputValue('workspace-ssh-profile-shell', '');
    setInputValue('workspace-ssh-profile-timeout', '');
    setInputValue('workspace-ssh-profile-private-key', '');
    setInputValue('workspace-ssh-profile-private-key-name', '');
    const title = document.getElementById('workspace-ssh-profile-editor-title');
    if (title) {
        title.textContent = t('settings.workspace.add_profile');
    }
    showSshProfileEditor();
    renderWorkspacePasswordField();
    updateSshProfileAuthState(null);
    renderDraftSshProfileProbeState();
    document.getElementById('workspace-ssh-profile-id')?.focus?.();
}

function handleEditSshProfile(sshProfileId) {
    const normalizedId = String(sshProfileId || '').trim();
    const matched = sshProfiles.find(profile => String(profile?.ssh_profile_id || '').trim() === normalizedId);
    if (!matched) {
        return;
    }
    editingSshProfileId = normalizedId;
    sshPasswordState = createWorkspacePasswordState(Boolean(matched.has_password));
    draftSshProfileProbeState = null;
    setInputValue('workspace-ssh-profile-id', normalizedId);
    setInputValue('workspace-ssh-profile-host', matched.host);
    setInputValue('workspace-ssh-profile-username', matched.username);
    setInputValue('workspace-ssh-profile-port', formatOptionalNumber(matched.port));
    setInputValue('workspace-ssh-profile-shell', matched.remote_shell);
    setInputValue('workspace-ssh-profile-timeout', formatOptionalNumber(matched.connect_timeout_seconds));
    setInputValue('workspace-ssh-profile-private-key', '');
    setInputValue('workspace-ssh-profile-private-key-name', matched.private_key_name);
    const title = document.getElementById('workspace-ssh-profile-editor-title');
    if (title) {
        title.textContent = t('settings.workspace.edit_profile');
    }
    showSshProfileEditor();
    renderWorkspacePasswordField();
    updateSshProfileAuthState(matched);
    renderDraftSshProfileProbeState();
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
        const password = readWorkspacePasswordValue();
        const privateKey = normalizeOptionalMultilineText(readInputValue('workspace-ssh-profile-private-key'));
        const privateKeyName = normalizeOptionalText(readInputValue('workspace-ssh-profile-private-key-name'));
        const payload = {
            host,
            username: normalizeOptionalText(readInputValue('workspace-ssh-profile-username')),
            port: parseOptionalInteger(readInputValue('workspace-ssh-profile-port')),
            remote_shell: normalizeOptionalText(readInputValue('workspace-ssh-profile-shell')),
            connect_timeout_seconds: parseOptionalInteger(readInputValue('workspace-ssh-profile-timeout')),
        };
        if (password !== null) {
            payload.password = password;
        }
        if (privateKey !== null) {
            payload.private_key = privateKey;
            if (privateKeyName !== null) {
                payload.private_key_name = privateKeyName;
            }
        }
        const saved = await saveSshProfile(sshProfileId, payload);
        await loadWorkspaceSettingsPanel();
        editingSshProfileId = String(saved?.ssh_profile_id || sshProfileId).trim();
        draftSshProfileProbeState = null;
        renderDraftSshProfileProbeState();
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
    draftSshProfileProbeState = null;
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
        delete sshProfileProbeStates[sshProfileId];
        draftSshProfileProbeState = null;
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

async function handleTestSshProfile(sshProfileId) {
    const normalizedId = String(sshProfileId || '').trim();
    if (!normalizedId) {
        return;
    }
    const matched = sshProfiles.find(profile => String(profile?.ssh_profile_id || '').trim() === normalizedId);
    sshProfileProbeStates = {
        ...sshProfileProbeStates,
        [normalizedId]: {
            status: 'probing',
            message: t('settings.workspace.testing'),
        },
    };
    renderSshProfiles();

    try {
        const result = await probeSshProfileConnection({
            ssh_profile_id: normalizedId,
            timeout_ms: Math.round((matched?.connect_timeout_seconds || 15) * 1000),
        });
        sshProfileProbeStates = {
            ...sshProfileProbeStates,
            [normalizedId]: buildSshProbeState(result),
        };
    } catch (error) {
        sshProfileProbeStates = {
            ...sshProfileProbeStates,
            [normalizedId]: {
                status: 'failed',
                message: formatMessage('settings.workspace.probe_failed', {
                    error: String(error?.message || error || ''),
                }),
            },
        };
    }

    renderSshProfiles();
}

async function handleTestDraftSshProfile() {
    const payload = buildDraftSshProfileProbePayload();
    if (!payload) {
        return;
    }
    draftSshProfileProbeState = {
        status: 'probing',
        message: t('settings.workspace.testing'),
    };
    renderDraftSshProfileProbeState();

    try {
        const result = await probeSshProfileConnection(payload);
        draftSshProfileProbeState = buildSshProbeState(result);
    } catch (error) {
        draftSshProfileProbeState = {
            status: 'failed',
            message: formatMessage('settings.workspace.probe_failed', {
                error: String(error?.message || error || ''),
            }),
        };
    }

    renderDraftSshProfileProbeState();
}

function buildDraftSshProfileProbePayload() {
    const savedSshProfileId = String(editingSshProfileId || '').trim();
    const host = String(readInputValue('workspace-ssh-profile-host')).trim();
    if (!host) {
        showToast({
            title: t('settings.workspace.validation_failed_title'),
            message: t('settings.workspace.host_required'),
            tone: 'danger',
        });
        return null;
    }

    const password = readWorkspacePasswordValue();
    const privateKey = normalizeOptionalMultilineText(readInputValue('workspace-ssh-profile-private-key'));
    const privateKeyName = normalizeOptionalText(readInputValue('workspace-ssh-profile-private-key-name'));
    const connectTimeoutSeconds = parseOptionalInteger(readInputValue('workspace-ssh-profile-timeout')) || 15;
    const override = {
        host,
        username: normalizeOptionalText(readInputValue('workspace-ssh-profile-username')),
        port: parseOptionalInteger(readInputValue('workspace-ssh-profile-port')),
        remote_shell: normalizeOptionalText(readInputValue('workspace-ssh-profile-shell')),
        connect_timeout_seconds: parseOptionalInteger(readInputValue('workspace-ssh-profile-timeout')),
    };
    if (password !== null) {
        override.password = password;
    }
    if (privateKey !== null) {
        override.private_key = privateKey;
        if (privateKeyName !== null) {
            override.private_key_name = privateKeyName;
        }
    }

    const payload = {
        override,
        timeout_ms: Math.round(connectTimeoutSeconds * 1000),
    };
    if (savedSshProfileId) {
        payload.ssh_profile_id = savedSshProfileId;
    }
    return payload;
}

function buildSshProbeState(result) {
    if (result?.ok) {
        return {
            status: 'success',
            message: formatMessage('settings.workspace.probe_success', {
                latency_ms: result.latency_ms,
            }),
        };
    }
    const reason = result?.error_message || result?.error_code || t('settings.workspace.unknown');
    return {
        status: 'failed',
        message: formatMessage('settings.workspace.connection_failed', { reason }),
    };
}

function renderSshProbeStatusMarkup(state) {
    if (!state) {
        return '';
    }
    return `<div class="profile-card-probe-status probe-status probe-status-${state.status}">${escapeHtml(state.message)}</div>`;
}

function renderDraftSshProfileProbeState() {
    const statusEl = document.getElementById('workspace-ssh-profile-probe-status');
    const testBtn = document.getElementById('test-ssh-profile-btn');
    if (!statusEl || !testBtn) {
        return;
    }
    if (!draftSshProfileProbeState) {
        statusEl.style.display = 'none';
        statusEl.textContent = '';
        statusEl.className = 'profile-probe-status';
        testBtn.disabled = false;
        testBtn.textContent = t('settings.action.test');
        return;
    }

    statusEl.style.display = 'block';
    statusEl.textContent = draftSshProfileProbeState.message;
    statusEl.className = `profile-probe-status probe-status probe-status-${draftSshProfileProbeState.status}`;
    testBtn.disabled = draftSshProfileProbeState.status === 'probing';
    testBtn.textContent = draftSshProfileProbeState.status === 'probing'
        ? t('settings.workspace.testing')
        : t('settings.action.test');
}

function showSshProfileList() {
    setElementDisplay('workspace-ssh-profile-list', 'block');
    setElementDisplay('workspace-ssh-profile-editor', 'none');
    toggleWorkspaceActions({
        add: true,
        test: false,
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
        test: true,
        save: true,
        cancel: true,
        delete: Boolean(editingSshProfileId),
    });
}

function toggleWorkspaceActions(visibility) {
    setActionDisplay('add-ssh-profile-btn', visibility.add);
    setActionDisplay('test-ssh-profile-btn', visibility.test);
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

function bindPrivateKeyImportHandlers() {
    const importButton = document.getElementById('workspace-ssh-profile-import-private-key-btn');
    const fileInput = document.getElementById('workspace-ssh-profile-private-key-file');
    if (importButton && fileInput) {
        importButton.onclick = () => {
            fileInput.value = '';
            fileInput.click?.();
        };
        fileInput.onchange = () => {
            void handlePrivateKeyFileInput(fileInput);
        };
    }
}

function bindSecretStateHandlers() {
    const privateKeyInput = document.getElementById('workspace-ssh-profile-private-key');
    if (privateKeyInput) {
        privateKeyInput.oninput = () => {
            updateSshProfileAuthState(findEditingSshProfile());
        };
    }
    const privateKeyNameInput = document.getElementById('workspace-ssh-profile-private-key-name');
    if (privateKeyNameInput) {
        privateKeyNameInput.oninput = () => {
            updateSshProfileAuthState(findEditingSshProfile());
        };
    }
}

function bindPasswordHandlers() {
    const passwordInput = document.getElementById('workspace-ssh-profile-password');
    if (passwordInput) {
        passwordInput.oninput = handleWorkspacePasswordInput;
        passwordInput.onchange = handleWorkspacePasswordInput;
        passwordInput.onfocus = armWorkspacePasswordInput;
        passwordInput.onpointerdown = armWorkspacePasswordInput;
        passwordInput.onkeydown = armWorkspacePasswordInput;
        passwordInput.onblur = disarmWorkspacePasswordInput;
    }
    const togglePasswordBtn = document.getElementById('toggle-workspace-ssh-profile-password-btn');
    if (togglePasswordBtn) {
        togglePasswordBtn.onclick = () => {
            void toggleWorkspacePasswordVisibility();
        };
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

function normalizeOptionalMultilineText(value) {
    const normalized = String(value || '').replaceAll('\r\n', '\n').replaceAll('\r', '\n').trim();
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

function createWorkspacePasswordState(hasPersistedValue = false, persistedValue = '') {
    const normalizedValue = typeof persistedValue === 'string' ? persistedValue : '';
    return {
        persistedValue: normalizedValue,
        persistedValueLoaded: Boolean(normalizedValue.trim()),
        draftValue: '',
        hasPersistedValue: hasPersistedValue === true || Boolean(normalizedValue.trim()),
        isDirty: false,
        isLoadingReveal: false,
        armedForInput: false,
        revealed: false,
    };
}

function handleWorkspacePasswordInput() {
    const passwordInput = document.getElementById('workspace-ssh-profile-password');
    const nextValue = passwordInput ? passwordInput.value : '';
    if (
        sshPasswordState.hasPersistedValue
        && !sshPasswordState.persistedValueLoaded
        && !sshPasswordState.revealed
        && !canAcceptWorkspacePasswordInput(passwordInput)
    ) {
        sshPasswordState.draftValue = '';
        sshPasswordState.isDirty = false;
        sshPasswordState.armedForInput = false;
        sshPasswordState.revealed = false;
        renderWorkspacePasswordField();
        updateSshProfileAuthState(findEditingSshProfile());
        return;
    }
    sshPasswordState.draftValue = nextValue;
    sshPasswordState.isDirty = sshPasswordState.hasPersistedValue
        ? sshPasswordState.persistedValueLoaded
            ? nextValue !== sshPasswordState.persistedValue
            : nextValue.trim().length > 0
        : nextValue.trim().length > 0;
    if (!readWorkspacePasswordValue()) {
        sshPasswordState.revealed = false;
    }
    renderWorkspacePasswordField();
    updateSshProfileAuthState(findEditingSshProfile());
}

async function toggleWorkspacePasswordVisibility() {
    if (!hasWorkspacePasswordValue() || sshPasswordState.isLoadingReveal) {
        return;
    }
    if (
        sshPasswordState.hasPersistedValue
        && !sshPasswordState.isDirty
        && !sshPasswordState.revealed
        && !sshPasswordState.persistedValueLoaded
    ) {
        const sshProfileId = String(editingSshProfileId || '').trim();
        if (!sshProfileId) {
            return;
        }
        sshPasswordState.isLoadingReveal = true;
        renderWorkspacePasswordToggle();
        try {
            const result = await revealSshProfilePassword(sshProfileId);
            sshPasswordState.persistedValue = typeof result?.password === 'string' ? result.password : '';
            sshPasswordState.persistedValueLoaded = Boolean(sshPasswordState.persistedValue.trim());
            if (!sshPasswordState.persistedValueLoaded) {
                sshPasswordState.hasPersistedValue = false;
                sshPasswordState.isLoadingReveal = false;
                renderWorkspacePasswordField();
                updateSshProfileAuthState(findEditingSshProfile());
                return;
            }
        } catch (error) {
            sshPasswordState.isLoadingReveal = false;
            renderWorkspacePasswordToggle();
            showToast({
                title: t('settings.workspace.password_reveal_failed_title'),
                message: formatMessage('settings.workspace.password_reveal_failed_detail', {
                    error: String(error?.message || error || ''),
                }),
                tone: 'danger',
            });
            return;
        }
    }
    sshPasswordState.isLoadingReveal = false;
    sshPasswordState.revealed = !sshPasswordState.revealed;
    renderWorkspacePasswordField();
    updateSshProfileAuthState(findEditingSshProfile());
}

function readWorkspacePasswordValue() {
    const passwordInput = document.getElementById('workspace-ssh-profile-password');
    const inputValue = passwordInput ? passwordInput.value.trim() : '';
    if (!sshPasswordState.hasPersistedValue) {
        return inputValue || null;
    }
    if (sshPasswordState.isDirty) {
        return inputValue || null;
    }
    return null;
}

function renderWorkspacePasswordField() {
    const passwordInput = document.getElementById('workspace-ssh-profile-password');
    if (!passwordInput) {
        return;
    }
    if (sshPasswordState.revealed) {
        passwordInput.type = 'text';
        passwordInput.value = sshPasswordState.isDirty
            ? sshPasswordState.draftValue
            : sshPasswordState.persistedValue;
        passwordInput.placeholder = '';
    } else if (sshPasswordState.hasPersistedValue && !sshPasswordState.isDirty) {
        passwordInput.type = 'password';
        passwordInput.value = '';
        passwordInput.placeholder = MASKED_SECRET_PLACEHOLDER;
    } else {
        passwordInput.type = sshPasswordState.revealed ? 'text' : 'password';
        passwordInput.value = sshPasswordState.draftValue;
        passwordInput.placeholder = t('settings.workspace.password_placeholder');
    }
    renderWorkspacePasswordToggle();
}

function renderWorkspacePasswordToggle() {
    const togglePasswordBtn = document.getElementById('toggle-workspace-ssh-profile-password-btn');
    if (!togglePasswordBtn) {
        return;
    }
    togglePasswordBtn.style.display = hasWorkspacePasswordValue() ? 'inline-flex' : 'none';
    togglePasswordBtn.disabled = sshPasswordState.isLoadingReveal;
    togglePasswordBtn.className = sshPasswordState.revealed ? 'secure-input-btn is-active' : 'secure-input-btn';
    togglePasswordBtn.title = sshPasswordState.revealed
        ? t('settings.proxy.hide_password')
        : t('settings.proxy.show_password');
    if (typeof togglePasswordBtn.setAttribute === 'function') {
        togglePasswordBtn.setAttribute('aria-label', togglePasswordBtn.title);
    } else {
        togglePasswordBtn.ariaLabel = togglePasswordBtn.title;
    }
}

function hasWorkspacePasswordValue() {
    const passwordInput = document.getElementById('workspace-ssh-profile-password');
    const inputValue = passwordInput ? passwordInput.value.trim() : '';
    if (sshPasswordState.hasPersistedValue && !sshPasswordState.isDirty) {
        return true;
    }
    return Boolean(sshPasswordState.draftValue.trim() || inputValue);
}

function armWorkspacePasswordInput() {
    sshPasswordState.armedForInput = true;
}

function disarmWorkspacePasswordInput() {
    sshPasswordState.armedForInput = false;
}

function canAcceptWorkspacePasswordInput(passwordInput) {
    if (!passwordInput) {
        return false;
    }
    if (sshPasswordState.armedForInput) {
        return true;
    }
    if (typeof document !== 'object' || document === null) {
        return false;
    }
    if (!('activeElement' in document)) {
        return true;
    }
    return document.activeElement === passwordInput;
}

async function handlePrivateKeyFileInput(fileInput) {
    const files = Array.isArray(fileInput?.files) ? fileInput.files : Array.from(fileInput?.files || []);
    const selectedFile = files[0];
    if (!selectedFile || typeof selectedFile.text !== 'function') {
        return;
    }
    try {
        const content = await selectedFile.text();
        setInputValue('workspace-ssh-profile-private-key', content);
        setInputValue('workspace-ssh-profile-private-key-name', selectedFile.name || '');
        updateSshProfileAuthState(findEditingSshProfile());
    } catch (error) {
        showToast({
            title: t('settings.workspace.private_key_import_failed_title'),
            message: formatMessage('settings.workspace.private_key_import_failed_detail', {
                error: String(error?.message || error || ''),
            }),
            tone: 'danger',
        });
    }
}

function findEditingSshProfile() {
    const normalizedId = String(editingSshProfileId || '').trim();
    if (!normalizedId) {
        return null;
    }
    return (
        sshProfiles.find(profile => String(profile?.ssh_profile_id || '').trim() === normalizedId)
        || null
    );
}

function updateSshProfileAuthState(profile) {
    const stateEl = document.getElementById('workspace-ssh-profile-auth-state');
    if (!stateEl) {
        return;
    }
    const password = readWorkspacePasswordValue();
    const privateKey = normalizeOptionalMultilineText(readInputValue('workspace-ssh-profile-private-key'));
    const privateKeyName = normalizeOptionalText(readInputValue('workspace-ssh-profile-private-key-name'));
    const messages = [];
    if (password !== null) {
        messages.push(t('settings.workspace.auth_state_new_password'));
    } else if (profile?.has_password) {
        messages.push(t('settings.workspace.auth_state_password'));
    }
    if (privateKey !== null) {
        messages.push(formatMessage('settings.workspace.auth_state_new_private_key', {
            name: privateKeyName || t('settings.workspace.private_key_inline'),
        }));
    } else if (profile?.has_private_key) {
        messages.push(
            privateKeyName
                ? formatMessage('settings.workspace.auth_state_private_key_named', {
                    name: privateKeyName,
                })
                : t('settings.workspace.auth_state_private_key'),
        );
    }
    stateEl.style.display = messages.length > 0 ? 'block' : 'none';
    stateEl.textContent = messages.join(' ');
}

function buildAuthSummary(profile) {
    const segments = [];
    if (profile?.has_password) {
        segments.push(t('settings.workspace.auth_method_password'));
    }
    if (profile?.has_private_key) {
        const privateKeyName = normalizeOptionalText(profile?.private_key_name);
        segments.push(
            privateKeyName
                ? formatMessage('settings.workspace.auth_method_private_key_named', {
                    name: privateKeyName,
                })
                : t('settings.workspace.auth_method_private_key'),
        );
    }
    if (segments.length === 0) {
        segments.push(t('settings.workspace.auth_method_system'));
    }
    return segments.join(' / ');
}

function escapeHtml(value) {
    return String(value)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
