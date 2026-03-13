/**
 * components/settings/modelProfiles.js
 * Model profile tab logic.
 */
import {
    deleteModelProfile,
    fetchModelProfiles,
    probeModelConnection,
    reloadModelConfig,
    saveModelProfile,
} from '../../core/api.js';
import { showConfirmDialog, showToast } from '../../utils/feedback.js';
import { errorToPayload, logError } from '../../utils/logger.js';

let profiles = {};
let editingProfile = null;
let profileProbeStates = {};
let draftProbeState = null;

export function bindModelProfileHandlers() {
    const addProfileBtn = document.getElementById('add-profile-btn');
    if (addProfileBtn) {
        addProfileBtn.onclick = handleAddProfile;
    }

    const saveProfileBtn = document.getElementById('save-profile-btn');
    if (saveProfileBtn) {
        saveProfileBtn.onclick = handleSaveProfile;
    }

    const testProfileBtn = document.getElementById('test-profile-btn');
    if (testProfileBtn) {
        testProfileBtn.onclick = handleTestDraftProfile;
    }

    const cancelProfileBtn = document.getElementById('cancel-profile-btn');
    if (cancelProfileBtn) {
        cancelProfileBtn.onclick = handleCancelProfile;
    }
}

export async function loadModelProfilesPanel() {
    try {
        profiles = await fetchModelProfiles();
        renderProfiles();
        renderDraftProbeState();
    } catch (e) {
        logError(
            'frontend.model_profiles.load_failed',
            'Failed to load model profiles',
            errorToPayload(e),
        );
    }
}

function renderProfiles() {
    const listEl = document.getElementById('profiles-list');
    showProfilesList();

    if (Object.keys(profiles).length === 0) {
        listEl.innerHTML = `
            <div class="settings-empty-state">
                <h4>No profiles configured</h4>
                <p>Create a profile to define the model endpoint, request limits, and sampling defaults.</p>
            </div>
        `;
        return;
    }

    let html = '<div class="profile-records">';
    Object.entries(profiles).forEach(([name, profile], index) => {
        html += renderProfileCard(name, profile, index);
    });
    html += '</div>';
    listEl.innerHTML = html;

    listEl.querySelectorAll('.profile-card-test-btn').forEach(btn => {
        btn.onclick = () => handleTestProfile(btn.dataset.name);
    });
    listEl.querySelectorAll('.edit-profile-btn').forEach(btn => {
        btn.onclick = () => handleEditProfile(btn.dataset.name);
    });
    listEl.querySelectorAll('.delete-profile-btn').forEach(btn => {
        btn.onclick = () => handleDeleteProfile(btn.dataset.name);
    });
}

function handleAddProfile() {
    editingProfile = null;
    draftProbeState = null;
    const apiKeyInput = document.getElementById('profile-api-key');
    document.getElementById('profile-editor-title').textContent = 'Add Profile';
    document.getElementById('profile-name').value = '';
    document.getElementById('profile-name').disabled = false;
    document.getElementById('profile-model').value = '';
    document.getElementById('profile-base-url').value = '';
    apiKeyInput.value = '';
    apiKeyInput.placeholder = '';
    document.getElementById('profile-temperature').value = '0.7';
    document.getElementById('profile-top-p').value = '1.0';
    document.getElementById('profile-max-tokens').value = '4096';
    document.getElementById('profile-connect-timeout').value = '15';
    document.getElementById('profile-ssl-verify').value = '';

    showProfileEditor();
    renderDraftProbeState();
    document.getElementById('profile-name').focus();
}

function handleEditProfile(name) {
    const profile = profiles[name];
    if (!profile) return;

    editingProfile = name;
    draftProbeState = null;
    const apiKeyInput = document.getElementById('profile-api-key');
    document.getElementById('profile-editor-title').textContent = `Edit Profile: ${name}`;
    document.getElementById('profile-name').value = name;
    document.getElementById('profile-name').disabled = false;
    document.getElementById('profile-model').value = profile.model || '';
    document.getElementById('profile-base-url').value = profile.base_url || '';
    apiKeyInput.value = '';
    apiKeyInput.placeholder = profile.has_api_key ? 'Leave blank to keep current API key' : '';
    document.getElementById('profile-temperature').value = profile.temperature || 0.7;
    document.getElementById('profile-top-p').value = profile.top_p || 1.0;
    document.getElementById('profile-max-tokens').value = profile.max_tokens || 4096;
    document.getElementById('profile-connect-timeout').value = profile.connect_timeout_seconds || 15;
    document.getElementById('profile-ssl-verify').value = serializeTriStateValue(profile.ssl_verify);

    showProfileEditor();
    renderDraftProbeState();
}

function handleCancelProfile() {
    showProfilesList();
    editingProfile = null;
    draftProbeState = null;
    renderDraftProbeState();
}

async function handleSaveProfile() {
    const name = document.getElementById('profile-name').value.trim();
    const model = document.getElementById('profile-model').value.trim();
    const baseUrl = document.getElementById('profile-base-url').value.trim();
    const apiKey = document.getElementById('profile-api-key').value.trim();
    const temperature = parseFloat(document.getElementById('profile-temperature').value) || 0.7;
    const topP = parseFloat(document.getElementById('profile-top-p').value) || 1.0;
    const maxTokens = parseInt(document.getElementById('profile-max-tokens').value) || 4096;
    const connectTimeoutSeconds = parseFloat(document.getElementById('profile-connect-timeout').value) || 15;
    const sslVerify = parseTriStateValue(document.getElementById('profile-ssl-verify').value);

    if (!name) {
        showToast({ title: 'Profile Required', message: 'Profile name is required.', tone: 'warning' });
        return;
    }

    if (!editingProfile && !apiKey) {
        showToast({ title: 'API Key Required', message: 'API key is required for a new profile.', tone: 'warning' });
        return;
    }

    const profile = {
        model: model,
        base_url: baseUrl,
        temperature: temperature,
        top_p: topP,
        max_tokens: maxTokens,
        connect_timeout_seconds: connectTimeoutSeconds,
    };
    if (sslVerify !== null) {
        profile.ssl_verify = sslVerify;
    }

    if (apiKey) {
        profile.api_key = apiKey;
    }
    if (editingProfile) {
        profile.source_name = editingProfile;
    }

    try {
        await saveModelProfile(name, profile);
        await reloadModelConfig();
        draftProbeState = null;
        renderDraftProbeState();
        showToast({ title: 'Profile Saved', message: 'Profile saved and reloaded.', tone: 'success' });
        await loadModelProfilesPanel();
    } catch (e) {
        showToast({ title: 'Save Failed', message: `Failed to save: ${e.message}`, tone: 'danger' });
    }
}

async function handleTestProfile(name) {
    if (!name) {
        return;
    }

    profileProbeStates[name] = {
        status: 'probing',
        message: 'Testing connection...',
    };
    renderProfileProbeState(name);

    try {
        const result = await probeModelConnection({
            profile_name: name,
            timeout_ms: Math.round((profiles[name]?.connect_timeout_seconds || 15) * 1000),
        });
        profileProbeStates[name] = buildProbeState(result);
    } catch (e) {
        profileProbeStates[name] = {
            status: 'failed',
            message: `Probe failed: ${e.message}`,
        };
    }

    renderProfileProbeState(name);
}

async function handleTestDraftProfile() {
    const payload = buildDraftProbePayload();
    if (!payload) {
        return;
    }

    draftProbeState = {
        status: 'probing',
        message: 'Testing connection...',
    };
    renderDraftProbeState();

    try {
        const result = await probeModelConnection(payload);
        draftProbeState = buildProbeState(result);
    } catch (e) {
        draftProbeState = {
            status: 'failed',
            message: `Probe failed: ${e.message}`,
        };
    }

    renderDraftProbeState();
}

async function handleDeleteProfile(name) {
    const shouldDelete = await showConfirmDialog({
        title: 'Delete Profile',
        message: `Delete profile "${name}"?`,
        tone: 'warning',
        confirmLabel: 'Delete',
        cancelLabel: 'Cancel',
    });
    if (!shouldDelete) {
        return;
    }

    try {
        await deleteModelProfile(name);
        await reloadModelConfig();
        delete profileProbeStates[name];
        showToast({ title: 'Profile Deleted', message: 'Profile deleted and reloaded.', tone: 'success' });
        await loadModelProfilesPanel();
    } catch (e) {
        showToast({ title: 'Delete Failed', message: `Failed to delete: ${e.message}`, tone: 'danger' });
    }
}

function buildDraftProbePayload() {
    const model = document.getElementById('profile-model').value.trim();
    const baseUrl = document.getElementById('profile-base-url').value.trim();
    const apiKey = document.getElementById('profile-api-key').value.trim();
    const temperature = parseFloat(document.getElementById('profile-temperature').value) || 0.7;
    const topP = parseFloat(document.getElementById('profile-top-p').value) || 1.0;
    const maxTokens = parseInt(document.getElementById('profile-max-tokens').value) || 4096;
    const connectTimeoutSeconds = parseFloat(document.getElementById('profile-connect-timeout').value) || 15;
    const sslVerify = parseTriStateValue(document.getElementById('profile-ssl-verify').value);

    if (!model || !baseUrl || (!apiKey && !editingProfile)) {
        draftProbeState = {
            status: 'failed',
            message: 'Model, base URL, and API key are required before testing a new profile.',
        };
        renderDraftProbeState();
        return null;
    }

    const override = {
        model: model,
        base_url: baseUrl,
        temperature: temperature,
        top_p: topP,
        max_tokens: maxTokens,
    };
    if (sslVerify !== null) {
        override.ssl_verify = sslVerify;
    }

    if (apiKey) {
        override.api_key = apiKey;
    }

    const payload = {
        override,
        timeout_ms: Math.round(connectTimeoutSeconds * 1000),
    };
    if (editingProfile) {
        payload.profile_name = editingProfile;
    }
    return payload;
}

function buildProbeState(result) {
    if (result.ok) {
        const usageText = result.token_usage ? ` · ${result.token_usage.total_tokens} tokens` : '';
        return {
            status: 'success',
            message: `Connected in ${result.latency_ms}ms${usageText}`,
        };
    }

    const reason = result.error_message || result.error_code || 'Unknown error';
    return {
        status: 'failed',
        message: `Connection failed: ${reason}`,
    };
}

function renderDraftProbeState() {
    const statusEl = document.getElementById('profile-probe-status');
    const testBtn = document.getElementById('test-profile-btn');
    if (!statusEl || !testBtn) {
        return;
    }

    if (!draftProbeState) {
        statusEl.style.display = 'none';
        statusEl.textContent = '';
        statusEl.className = 'profile-probe-status';
        testBtn.disabled = false;
        testBtn.textContent = 'Test';
        return;
    }

    statusEl.style.display = 'block';
    statusEl.textContent = draftProbeState.message;
    statusEl.className = `profile-probe-status probe-status probe-status-${draftProbeState.status}`;
    testBtn.disabled = draftProbeState.status === 'probing';
    testBtn.textContent = draftProbeState.status === 'probing' ? 'Testing...' : 'Test';
}

function parseTriStateValue(value) {
    const normalized = String(value || '').trim().toLowerCase();
    if (normalized === 'true') {
        return true;
    }
    if (normalized === 'false') {
        return false;
    }
    return null;
}

function serializeTriStateValue(value) {
    if (value === true) {
        return 'true';
    }
    if (value === false) {
        return 'false';
    }
    return '';
}

function showProfilesList() {
    document.getElementById('profile-editor').style.display = 'none';
    document.getElementById('profiles-list').style.display = 'block';
    toggleModelProfileActions({
        add: true,
        test: false,
        cancel: false,
        save: false,
    });
}

function showProfileEditor() {
    document.getElementById('profiles-list').style.display = 'none';
    document.getElementById('profile-editor').style.display = 'block';
    toggleModelProfileActions({
        add: false,
        test: true,
        cancel: true,
        save: true,
    });
}

function renderProbeStatusMarkup(state) {
    if (!state) {
        return '';
    }
    return `<div class="profile-card-probe-status probe-status probe-status-${state.status}">${escapeHtml(state.message)}</div>`;
}

function renderProfileCard(name, profile, index) {
    const probeState = profileProbeStates[name] || null;
    const testButtonLabel = probeState?.status === 'probing' ? 'Testing...' : 'Test';
    const providerLabel = formatProviderLabel(profile.provider);
    const defaultChip = name === 'default'
        ? '<span class="profile-card-chip profile-card-chip-accent">Default</span>'
        : '';
    const modelLabel = profile.model || 'No model';
    const baseUrlLabel = profile.base_url || 'No endpoint';

    return `
        <div class="profile-record profile-card" data-profile-name="${escapeHtml(name)}" style="--profile-index:${index};">
            <div class="profile-record-main">
                <div class="profile-record-heading">
                    <div class="profile-card-heading">
                        <div class="profile-card-title-row">
                            <h4>${escapeHtml(name)}</h4>
                            <div class="profile-card-chips">
                                <span class="profile-card-chip">${escapeHtml(providerLabel)}</span>
                                ${defaultChip}
                            </div>
                        </div>
                        <div class="profile-record-summary" title="${escapeHtml(`${modelLabel} ${baseUrlLabel}`)}">
                            <span class="profile-record-summary-primary">${escapeHtml(modelLabel)}</span>
                            <span class="profile-record-summary-separator">/</span>
                            <span class="profile-record-summary-secondary">${escapeHtml(baseUrlLabel)}</span>
                        </div>
                    </div>
                </div>
                <div class="profile-card-actions">
                    <button class="settings-inline-action settings-list-action profile-card-action-btn profile-card-test-btn" data-name="${escapeHtml(name)}" title="Test" ${probeState?.status === 'probing' ? 'disabled' : ''}>${testButtonLabel}</button>
                    <button class="settings-inline-action settings-list-action profile-card-action-btn edit-profile-btn" data-name="${escapeHtml(name)}" title="Edit">Edit</button>
                    <button class="settings-inline-action settings-list-action settings-list-action-danger profile-card-action-btn delete-profile-btn" data-name="${escapeHtml(name)}" title="Delete">Delete</button>
                </div>
            </div>
            <div class="profile-card-inline-status" data-profile-probe-container="${escapeHtml(name)}">
                ${renderProbeStatusMarkup(probeState)}
            </div>
        </div>
    `;
}

function renderProfileProbeState(name) {
    const card = findProfileCard(name);
    if (!card) {
        return;
    }

    const state = profileProbeStates[name] || null;
    const testButton = card.querySelector('.profile-card-test-btn');
    const probeContainer = card.querySelector('[data-profile-probe-container]');

    if (testButton) {
        testButton.disabled = state?.status === 'probing';
        testButton.textContent = state?.status === 'probing' ? 'Testing...' : 'Test';
    }

    if (probeContainer) {
        probeContainer.innerHTML = renderProbeStatusMarkup(state);
    }
}

function findProfileCard(name) {
    return Array.from(document.querySelectorAll('.profile-card')).find(card => card.dataset.profileName === name) || null;
}

function formatProviderLabel(provider) {
    if (provider === 'openai_compatible') {
        return 'OpenAI Compatible';
    }
    if (provider === 'echo') {
        return 'Echo';
    }
    return provider || 'Unknown';
}

function escapeHtml(value) {
    return String(value)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function toggleModelProfileActions(visibility) {
    setActionDisplay('add-profile-btn', visibility.add);
    setActionDisplay('test-profile-btn', visibility.test);
    setActionDisplay('cancel-profile-btn', visibility.cancel);
    setActionDisplay('save-profile-btn', visibility.save);
}

function setActionDisplay(id, visible) {
    const button = document.getElementById(id);
    if (button) {
        button.style.display = visible ? 'inline-flex' : 'none';
    }
}
