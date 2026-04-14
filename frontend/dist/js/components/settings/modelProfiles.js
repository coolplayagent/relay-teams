/**
 * components/settings/modelProfiles.js
 * Model profile tab logic.
 */
import {
    deleteModelProfile,
    discoverModelCatalog,
    fetchModelProfiles,
    probeModelConnection,
    reloadModelConfig,
    saveModelProfile,
} from '../../core/api.js';
import { showConfirmDialog, showToast } from '../../utils/feedback.js';
import { t } from '../../utils/i18n.js';
import { errorToPayload, logError } from '../../utils/logger.js';

let profiles = {};
let editingProfile = null;
let profileProbeStates = {};
let draftProbeState = null;
let draftDiscoveredModels = [];
let draftModelDiscoveryState = null;
let draftApiKeyState = createDraftSecretState();
let draftMaasPasswordState = createDraftSecretState();
let isModelMenuOpen = false;

const DEFAULT_MAAS_BASE_URL = 'http://snapengine.cida.cce.prod-szv-g.dragon.tools.huawei.com/api/v2/';

const PROVIDER_DEFAULT_BASE_URLS = {
    bigmodel: 'https://open.bigmodel.cn/api/coding/paas/v4',
    minimax: 'https://api.minimaxi.com/v1',
    maas: DEFAULT_MAAS_BASE_URL,
};

function formatMessage(key, values = {}) {
    return Object.entries(values).reduce(
        (result, [name, value]) => result.replaceAll(`{${name}}`, String(value)),
        t(key),
    );
}

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

    const fetchModelsBtn = document.getElementById('fetch-profile-models-btn');
    if (fetchModelsBtn) {
        fetchModelsBtn.onclick = handleDiscoverDraftModels;
    }

    const openModelMenuBtn = document.getElementById('open-profile-model-menu-btn');
    if (openModelMenuBtn) {
        openModelMenuBtn.onclick = toggleDiscoveredModelMenu;
    }

    const cancelProfileBtn = document.getElementById('cancel-profile-btn');
    if (cancelProfileBtn) {
        cancelProfileBtn.onclick = handleCancelProfile;
    }

    const baseUrlInput = document.getElementById('profile-base-url');
    if (baseUrlInput) {
        baseUrlInput.oninput = handleDraftBaseUrlInput;
    }

    const providerInput = document.getElementById('profile-provider');
    if (providerInput) {
        providerInput.oninput = handleDraftEndpointChanged;
        providerInput.onchange = handleDraftEndpointChanged;
    }

    const apiKeyInput = document.getElementById('profile-api-key');
    if (apiKeyInput) {
        apiKeyInput.oninput = handleDraftApiKeyInput;
    }

    const maasUsernameInput = document.getElementById('profile-maas-username');
    if (maasUsernameInput) {
        maasUsernameInput.oninput = handleDraftEndpointChanged;
    }

    const maasPasswordInput = document.getElementById('profile-maas-password');
    if (maasPasswordInput) {
        maasPasswordInput.oninput = handleDraftMaasPasswordInput;
    }

    const toggleApiKeyBtn = document.getElementById('toggle-profile-api-key-btn');
    if (toggleApiKeyBtn) {
        toggleApiKeyBtn.onclick = toggleDraftApiKeyVisibility;
    }

    const toggleMaasPasswordBtn = document.getElementById('toggle-profile-maas-password-btn');
    if (toggleMaasPasswordBtn) {
        toggleMaasPasswordBtn.onclick = toggleDraftMaasPasswordVisibility;
    }

    const sslVerifyInput = document.getElementById('profile-ssl-verify');
    if (sslVerifyInput) {
        sslVerifyInput.onchange = handleDraftEndpointChanged;
    }

    const contextWindowInput = document.getElementById('profile-context-window');
    if (contextWindowInput) {
        contextWindowInput.oninput = handleContextWindowInputChanged;
    }

    const modelInput = document.getElementById('profile-model');
    if (modelInput) {
        modelInput.onfocus = openDiscoveredModelMenu;
        modelInput.onclick = openDiscoveredModelMenu;
        modelInput.oninput = syncDraftModelSelection;
        modelInput.onchange = syncDraftModelSelection;
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
                <h4>${t('settings.model.empty_title')}</h4>
                <p>${t('settings.model.empty_copy')}</p>
            </div>
        `;
        return;
    }

    const profileEntries = Object.entries(profiles).sort(([leftName, leftProfile], [rightName, rightProfile]) => {
        if (leftProfile?.is_default === rightProfile?.is_default) {
            return leftName.localeCompare(rightName);
        }
        return leftProfile?.is_default === true ? -1 : 1;
    });

    let html = '<div class="profile-records">';
    profileEntries.forEach(([name, profile], index) => {
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
    resetDraftEditorState();
    renderProfileEditorTitle();
    document.getElementById('profile-name').value = '';
    document.getElementById('profile-provider').value = 'openai_compatible';
    setDraftModelValue('');
    document.getElementById('profile-base-url').value = '';
    delete document.getElementById('profile-base-url').dataset.initialValue;
    delete document.getElementById('profile-base-url').dataset.previousProvider;
    delete document.getElementById('profile-base-url').dataset.defaultSourceProvider;
    draftApiKeyState = createDraftSecretState();
    draftMaasPasswordState = createDraftSecretState();
    document.getElementById('profile-maas-username').value = '';
    document.getElementById('profile-maas-password').value = '';
    document.getElementById('profile-is-default').checked = Object.keys(profiles).length === 0;
    document.getElementById('profile-temperature').value = '0.7';
    document.getElementById('profile-top-p').value = '1.0';
    document.getElementById('profile-max-tokens').value = '';
    document.getElementById('profile-context-window').value = '';
    delete document.getElementById('profile-context-window').dataset.autofilledModel;
    document.getElementById('profile-connect-timeout').value = '15';
    document.getElementById('profile-ssl-verify').value = '';

    showProfileEditor();
    renderDraftApiKeyField();
    renderDraftProviderFields();
    renderDraftProbeState();
    renderDraftModelDiscoveryState();
    renderDiscoveredModels();
    setModelMenuOpen(false);
    document.getElementById('profile-name').focus();
}

function handleEditProfile(name) {
    const profile = profiles[name];
    if (!profile) return;

    editingProfile = name;
    resetDraftEditorState();
    renderProfileEditorTitle();
    document.getElementById('profile-name').value = name;
    document.getElementById('profile-provider').value = profile.provider || 'openai_compatible';
    setDraftModelValue(profile.model || '');
    document.getElementById('profile-base-url').value = profile.base_url || '';
    document.getElementById('profile-base-url').dataset.initialValue = profile.base_url || '';
    document.getElementById('profile-base-url').dataset.previousProvider = profile.provider || 'openai_compatible';
    setDraftBaseUrlDefaultSource(profile.provider || 'openai_compatible', profile.base_url || '');
    draftApiKeyState = {
        persistedValue: typeof profile.api_key === 'string' ? profile.api_key : '',
        draftValue: '',
        hasPersistedValue: Boolean(profile.has_api_key),
        isDirty: false,
        revealed: false,
    };
    draftMaasPasswordState = {
        persistedValue: typeof profile.maas_auth?.password === 'string' ? profile.maas_auth.password : '',
        draftValue: '',
        hasPersistedValue: Boolean(profile.maas_auth?.has_password),
        isDirty: false,
        revealed: false,
    };
    document.getElementById('profile-maas-username').value = profile.maas_auth?.username || '';
    document.getElementById('profile-maas-password').value = '';
    document.getElementById('profile-is-default').checked = profile.is_default === true;
    document.getElementById('profile-temperature').value = profile.temperature || 0.7;
    document.getElementById('profile-top-p').value = profile.top_p || 1.0;
    document.getElementById('profile-max-tokens').value = profile.max_tokens || '';
    document.getElementById('profile-context-window').value = profile.context_window || '';
    delete document.getElementById('profile-context-window').dataset.autofilledModel;
    document.getElementById('profile-connect-timeout').value = profile.connect_timeout_seconds || 15;
    document.getElementById('profile-ssl-verify').value = serializeTriStateValue(profile.ssl_verify);

    showProfileEditor();
    renderDraftApiKeyField();
    renderDraftProviderFields();
    renderDraftProbeState();
    renderDraftModelDiscoveryState();
    renderDiscoveredModels();
    setModelMenuOpen(false);
}

function handleCancelProfile() {
    showProfilesList();
    editingProfile = null;
    resetDraftEditorState();
    renderDraftProbeState();
    renderDraftModelDiscoveryState();
    renderDiscoveredModels();
    setModelMenuOpen(false);
}

async function handleSaveProfile() {
    const name = document.getElementById('profile-name').value.trim();
    const provider = getDraftProvider();
    const model = document.getElementById('profile-model').value.trim();
    const baseUrl = document.getElementById('profile-base-url').value.trim();
    const apiKey = readDraftApiKeyValue();
    const maasAuth = readDraftMaasAuth();
    const isDefault = document.getElementById('profile-is-default').checked;
    const temperature = parseFloat(document.getElementById('profile-temperature').value) || 0.7;
    const topP = parseFloat(document.getElementById('profile-top-p').value) || 1.0;
    const maxTokensValue = String(document.getElementById('profile-max-tokens').value || '').trim();
    const maxTokens = maxTokensValue ? parseInt(maxTokensValue) || null : null;
    const contextWindowValue = String(
        document.getElementById('profile-context-window').value || '',
    ).trim();
    const contextWindow = contextWindowValue ? parseInt(contextWindowValue) || null : null;
    const connectTimeoutSeconds = parseFloat(document.getElementById('profile-connect-timeout').value) || 15;
    const sslVerify = parseTriStateValue(document.getElementById('profile-ssl-verify').value);

    if (!name) {
        showToast({ title: t('settings.model.profile_required_title'), message: t('settings.model.profile_required_message'), tone: 'warning' });
        return;
    }

    if (!model) {
        showToast({ title: t('settings.model.model_required_title'), message: t('settings.model.model_required_message'), tone: 'warning' });
        return;
    }

    if (!baseUrl) {
        showToast({ title: t('settings.model.base_url_required_title'), message: t('settings.model.base_url_required_message'), tone: 'warning' });
        return;
    }

    if (isMaaSProvider(provider)) {
        if (!maasAuth.username || !hasDraftMaasPassword(maasAuth)) {
            showToast({
                title: t('settings.model.save_failed_title'),
                message: 'MAAS profiles require username and password.',
                tone: 'warning',
            });
            return;
        }
    } else if (!editingProfile && !apiKey) {
        showToast({ title: t('settings.model.api_key_required_title'), message: t('settings.model.api_key_required_message'), tone: 'warning' });
        return;
    }

    const profile = {
        provider: provider,
        model: model,
        base_url: baseUrl,
        is_default: isDefault,
        temperature: temperature,
        top_p: topP,
        context_window: contextWindow,
        connect_timeout_seconds: connectTimeoutSeconds,
    };
    if (maxTokens !== null) {
        profile.max_tokens = maxTokens;
    }
    if (sslVerify !== null) {
        profile.ssl_verify = sslVerify;
    }

    if (isMaaSProvider(provider)) {
        profile.maas_auth = {
            username: maasAuth.username,
        };
        if (maasAuth.password) {
            profile.maas_auth.password = maasAuth.password;
        }
    } else if (apiKey) {
        profile.api_key = apiKey;
    }
    if (editingProfile) {
        profile.source_name = editingProfile;
    }

    try {
        await saveModelProfile(name, profile);
        await reloadModelConfig();
        resetDraftEditorState();
        renderDraftProbeState();
        renderDraftModelDiscoveryState();
        renderDiscoveredModels();
        setModelMenuOpen(false);
        showToast({ title: t('settings.model.saved_title'), message: t('settings.model.saved_message_detail'), tone: 'success' });
        await loadModelProfilesPanel();
    } catch (e) {
        showToast({ title: t('settings.model.save_failed_title'), message: formatMessage('settings.model.save_failed_detail', { error: e.message }), tone: 'danger' });
    }
}

async function handleTestProfile(name) {
    if (!name) {
        return;
    }

    profileProbeStates[name] = {
        status: 'probing',
        message: t('settings.model.testing'),
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
            message: formatMessage('settings.model.probe_failed', { error: e.message }),
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
        message: t('settings.model.testing'),
    };
    renderDraftProbeState();

    try {
        const result = await probeModelConnection(payload);
        draftProbeState = buildProbeState(result);
    } catch (e) {
        draftProbeState = {
            status: 'failed',
            message: formatMessage('settings.model.probe_failed', { error: e.message }),
        };
    }

    renderDraftProbeState();
}

async function handleDeleteProfile(name) {
    const shouldDelete = await showConfirmDialog({
        title: t('settings.model.delete_title'),
        message: formatMessage('settings.model.delete_message', { name }),
        tone: 'warning',
        confirmLabel: t('settings.action.delete'),
        cancelLabel: t('settings.action.cancel'),
    });
    if (!shouldDelete) {
        return;
    }

    try {
        await deleteModelProfile(name);
        await reloadModelConfig();
        delete profileProbeStates[name];
        showToast({ title: t('settings.model.deleted_title'), message: t('settings.model.deleted_message_detail'), tone: 'success' });
        await loadModelProfilesPanel();
    } catch (e) {
        showToast({ title: t('settings.model.delete_failed_title'), message: formatMessage('settings.model.delete_failed_detail', { error: e.message }), tone: 'danger' });
    }
}

async function handleDiscoverDraftModels() {
    const payload = buildDraftModelDiscoveryPayload();
    if (!payload) {
        return;
    }

    draftModelDiscoveryState = {
        status: 'probing',
        message: t('settings.model.fetching_models'),
    };
    renderDraftModelDiscoveryState();

    try {
        const result = await discoverModelCatalog(payload);
        if (!result.ok) {
            draftDiscoveredModels = [];
            draftModelDiscoveryState = {
                status: 'failed',
                message: formatMessage('settings.model.fetch_failed', {
                    error: result.error_message || result.error_code || t('settings.model.unknown'),
                }),
            };
        } else {
            draftDiscoveredModels = normalizeDiscoveredModels(result);
            draftModelDiscoveryState = {
                status: 'success',
                message: buildDiscoveredModelsMessage(result),
            };
            applyDiscoveredModelSelection();
            setModelMenuOpen(draftDiscoveredModels.length > 0);
        }
    } catch (e) {
        draftDiscoveredModels = [];
        draftModelDiscoveryState = {
            status: 'failed',
            message: formatMessage('settings.model.fetch_failed', { error: e.message }),
        };
    }

    renderDiscoveredModels();
    renderDraftModelDiscoveryState();
}

function buildDraftProbePayload() {
    const provider = getDraftProvider();
    const model = document.getElementById('profile-model').value.trim();
    const baseUrl = document.getElementById('profile-base-url').value.trim();
    const apiKey = readDraftApiKeyValue();
    const maasAuth = readDraftMaasAuth();
    const temperature = parseFloat(document.getElementById('profile-temperature').value) || 0.7;
    const topP = parseFloat(document.getElementById('profile-top-p').value) || 1.0;
    const maxTokensValue = String(document.getElementById('profile-max-tokens').value || '').trim();
    const maxTokens = maxTokensValue ? parseInt(maxTokensValue) || null : null;
    const connectTimeoutSeconds = parseFloat(document.getElementById('profile-connect-timeout').value) || 15;
    const sslVerify = parseTriStateValue(document.getElementById('profile-ssl-verify').value);

    if (!model || !baseUrl) {
        draftProbeState = {
            status: 'failed',
            message: t('settings.model.validation_test_new'),
        };
        renderDraftProbeState();
        return null;
    }

    if (isMaaSProvider(provider)) {
        if (!maasAuth.username || !hasDraftMaasPassword(maasAuth)) {
            draftProbeState = {
                status: 'failed',
                message: 'Model, base URL, username, and password are required before testing a MAAS profile.',
            };
            renderDraftProbeState();
            return null;
        }
    } else if (!apiKey && !editingProfile) {
        draftProbeState = {
            status: 'failed',
            message: t('settings.model.validation_test_new'),
        };
        renderDraftProbeState();
        return null;
    }

    const override = {
        provider: provider,
        model: model,
        base_url: baseUrl,
        temperature: temperature,
        top_p: topP,
    };
    if (maxTokens !== null) {
        override.max_tokens = maxTokens;
    }
    if (sslVerify !== null) {
        override.ssl_verify = sslVerify;
    }

    if (isMaaSProvider(provider)) {
        override.maas_auth = {
            username: maasAuth.username,
        };
        if (maasAuth.password) {
            override.maas_auth.password = maasAuth.password;
        }
    } else if (apiKey) {
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

function buildDraftModelDiscoveryPayload() {
    const provider = getDraftProvider();
    const baseUrl = document.getElementById('profile-base-url').value.trim();
    const apiKey = readDraftApiKeyValue();
    const maasAuth = readDraftMaasAuth();
    const connectTimeoutSeconds = parseFloat(document.getElementById('profile-connect-timeout').value) || 15;
    const sslVerify = parseTriStateValue(document.getElementById('profile-ssl-verify').value);

    if (isMaaSProvider(provider)) {
        if (!baseUrl || !maasAuth.username || !hasDraftMaasPassword(maasAuth)) {
            draftDiscoveredModels = [];
            draftModelDiscoveryState = {
                status: 'failed',
                message: 'Base URL, username, and password are required before fetching models for a MAAS profile.',
            };
            renderDiscoveredModels();
            renderDraftModelDiscoveryState();
            return null;
        }
    } else if (!baseUrl || (!apiKey && !editingProfile)) {
        draftDiscoveredModels = [];
        draftModelDiscoveryState = {
            status: 'failed',
            message: t('settings.model.validation_fetch_models'),
        };
        renderDiscoveredModels();
        renderDraftModelDiscoveryState();
        return null;
    }

    const override = {
        provider: provider,
        base_url: baseUrl,
    };
    if (sslVerify !== null) {
        override.ssl_verify = sslVerify;
    }
    if (isMaaSProvider(provider)) {
        override.maas_auth = {
            username: maasAuth.username,
        };
        if (maasAuth.password) {
            override.maas_auth.password = maasAuth.password;
        }
    } else if (apiKey) {
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
        const usageText = result.token_usage
            ? formatMessage('settings.model.usage_tokens', { tokens: result.token_usage.total_tokens })
            : '';
        return {
            status: 'success',
            message: formatMessage('settings.model.probe_success', {
                latency_ms: result.latency_ms,
                usage_text: usageText,
            }),
        };
    }

    const reason = result.error_message || result.error_code || t('settings.model.unknown');
    return {
        status: 'failed',
        message: formatMessage('settings.model.connection_failed', { reason }),
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
        testBtn.textContent = t('settings.action.test');
        return;
    }

    statusEl.style.display = 'block';
    statusEl.textContent = draftProbeState.message;
    statusEl.className = `profile-probe-status probe-status probe-status-${draftProbeState.status}`;
    testBtn.disabled = draftProbeState.status === 'probing';
    testBtn.textContent = draftProbeState.status === 'probing' ? t('settings.model.testing') : t('settings.action.test');
}

function renderDraftModelDiscoveryState() {
    const statusEl = document.getElementById('profile-model-discovery-status');
    const fetchBtn = document.getElementById('fetch-profile-models-btn');
    if (!statusEl || !fetchBtn) {
        return;
    }

    const defaultTitle = t('settings.model.fetch_models');

    if (!draftModelDiscoveryState) {
        statusEl.style.display = 'none';
        statusEl.textContent = '';
        statusEl.className = 'profile-model-discovery-status';
        fetchBtn.disabled = false;
        fetchBtn.className = 'secure-input-btn profile-discovery-btn';
        fetchBtn.title = defaultTitle;
        if (typeof fetchBtn.setAttribute === 'function') {
            fetchBtn.setAttribute('aria-label', defaultTitle);
        } else {
            fetchBtn.ariaLabel = defaultTitle;
        }
        return;
    }

    statusEl.style.display = 'block';
    statusEl.textContent = draftModelDiscoveryState.message;
    statusEl.className = `profile-model-discovery-status probe-status probe-status-${draftModelDiscoveryState.status}`;
    fetchBtn.disabled = draftModelDiscoveryState.status === 'probing';
    fetchBtn.className = draftModelDiscoveryState.status === 'probing'
        ? 'secure-input-btn profile-discovery-btn is-loading'
        : 'secure-input-btn profile-discovery-btn';
    fetchBtn.title = draftModelDiscoveryState.status === 'probing'
        ? t('settings.model.fetching_models')
        : t('settings.model.fetch_models');
    if (typeof fetchBtn.setAttribute === 'function') {
        fetchBtn.setAttribute('aria-label', fetchBtn.title);
    } else {
        fetchBtn.ariaLabel = fetchBtn.title;
    }
}

function renderDiscoveredModels() {
    const modelInput = document.getElementById('profile-model');
    const modelMenu = document.getElementById('profile-model-menu');
    const openModelMenuBtn = document.getElementById('open-profile-model-menu-btn');
    if (!modelInput || !modelMenu || !openModelMenuBtn) {
        return;
    }

    const currentValue = String(modelInput.dataset.currentValue || modelInput.value || '').trim();
    const discoveredNames = draftDiscoveredModels.map(item => item.model);
    const seenValues = new Set();
    const menuOptions = [];
    if (currentValue && !discoveredNames.includes(currentValue)) {
        seenValues.add(currentValue);
    }
    draftDiscoveredModels.forEach(modelEntry => {
        const modelName = modelEntry.model;
        if (seenValues.has(modelName)) {
            return;
        }
        seenValues.add(modelName);
        const activeClass = currentValue === modelName ? ' is-active' : '';
        menuOptions.push(
            `<button class="profile-model-menu-item${activeClass}" data-model-name="${escapeHtml(modelName)}" type="button">${escapeHtml(modelName)}</button>`,
        );
    });
    modelMenu.innerHTML = menuOptions.join('');
    modelInput.value = currentValue;

    modelMenu.querySelectorAll('.profile-model-menu-item').forEach(button => {
        button.onclick = () => handleDiscoveredModelPicked(button.dataset.modelName);
    });
    openModelMenuBtn.disabled = draftDiscoveredModels.length === 0;
    openModelMenuBtn.title = draftDiscoveredModels.length === 0
        ? t('settings.model.no_models_loaded')
        : t('settings.model.show_models');
    if (typeof openModelMenuBtn.setAttribute === 'function') {
        openModelMenuBtn.setAttribute('aria-label', openModelMenuBtn.title);
    } else {
        openModelMenuBtn.ariaLabel = openModelMenuBtn.title;
    }
    if (draftDiscoveredModels.length === 0) {
        setModelMenuOpen(false);
        return;
    }
    if (isModelMenuOpen) {
        setModelMenuOpen(true);
    }
}

function handleDraftEndpointChanged() {
    applyProviderDefaultBaseUrl();
    syncDraftBaseUrlDefaultSource();
    renderDraftProviderFields();
    draftDiscoveredModels = [];
    draftModelDiscoveryState = null;
    renderDiscoveredModels();
    renderDraftModelDiscoveryState();
    setModelMenuOpen(false);
}

function applyProviderDefaultBaseUrl() {
    const providerInput = document.getElementById('profile-provider');
    const baseUrlInput = document.getElementById('profile-base-url');
    if (!providerInput || !baseUrlInput) {
        return;
    }
    const provider = String(providerInput.value || '').trim();
    const previousProvider = String(baseUrlInput.dataset.previousProvider || '').trim();
    const initialValue = String(baseUrlInput.dataset.initialValue || '').trim();
    const previousDefaultSourceProvider = String(
        baseUrlInput.dataset.defaultSourceProvider || '',
    ).trim();
    const previousDefaultBaseUrl = getProviderDefaultBaseUrl(previousDefaultSourceProvider);
    const providerChanged = provider !== previousProvider;
    if (isMaaSProvider(provider)) {
        baseUrlInput.value = DEFAULT_MAAS_BASE_URL;
        baseUrlInput.dataset.previousProvider = provider;
        baseUrlInput.dataset.defaultSourceProvider = provider;
        return;
    }
    if (providerChanged && isMaaSProvider(previousProvider)) {
        baseUrlInput.value = '';
        delete baseUrlInput.dataset.defaultSourceProvider;
    }
    const defaultBaseUrl = getProviderDefaultBaseUrl(provider);
    if (!providerChanged || !defaultBaseUrl) {
        baseUrlInput.dataset.previousProvider = provider;
        return;
    }
    const currentBaseUrl = String(baseUrlInput.value || '').trim();
    if (!currentBaseUrl) {
        baseUrlInput.value = defaultBaseUrl;
    } else if (currentBaseUrl === previousDefaultBaseUrl) {
        baseUrlInput.value = defaultBaseUrl;
    } else if (editingProfile && currentBaseUrl === initialValue) {
        baseUrlInput.value = defaultBaseUrl;
    }
    baseUrlInput.dataset.previousProvider = provider;
}

function handleDraftBaseUrlInput() {
    syncDraftBaseUrlDefaultSource();
    handleDraftEndpointChanged();
}

function handleDraftApiKeyInput() {
    const apiKeyInput = document.getElementById('profile-api-key');
    if (!apiKeyInput) {
        return;
    }
    if (
        draftApiKeyState.hasPersistedValue
        && !draftApiKeyState.revealed
        && !isDraftSecretInputActive(apiKeyInput)
    ) {
        draftApiKeyState.draftValue = '';
        draftApiKeyState.isDirty = false;
        draftApiKeyState.revealed = false;
        renderDraftApiKeyField();
        return;
    }

    draftApiKeyState.draftValue = apiKeyInput.value;
    draftApiKeyState.isDirty = draftApiKeyState.draftValue !== draftApiKeyState.persistedValue;
    handleDraftEndpointChanged();
    renderDraftApiKeyToggle();
}

function handleDraftMaasPasswordInput() {
    const maasPasswordInput = document.getElementById('profile-maas-password');
    if (!maasPasswordInput) {
        return;
    }
    if (
        draftMaasPasswordState.hasPersistedValue
        && !draftMaasPasswordState.revealed
        && !isDraftSecretInputActive(maasPasswordInput)
    ) {
        draftMaasPasswordState.draftValue = '';
        draftMaasPasswordState.isDirty = false;
        draftMaasPasswordState.revealed = false;
        renderDraftMaaSPasswordField();
        return;
    }

    draftMaasPasswordState.draftValue = maasPasswordInput.value;
    draftMaasPasswordState.isDirty = draftMaasPasswordState.draftValue !== draftMaasPasswordState.persistedValue;
    handleDraftEndpointChanged();
    renderDraftMaaSPasswordToggle();
}

function toggleDraftApiKeyVisibility() {
    if (!draftApiKeyState.hasPersistedValue && !draftApiKeyState.draftValue.trim()) {
        return;
    }
    draftApiKeyState.revealed = !draftApiKeyState.revealed;
    renderDraftApiKeyField();
}

function toggleDraftMaasPasswordVisibility() {
    if (!draftMaasPasswordState.hasPersistedValue && !draftMaasPasswordState.draftValue.trim()) {
        return;
    }
    draftMaasPasswordState.revealed = !draftMaasPasswordState.revealed;
    renderDraftMaaSPasswordField();
}

function applyDiscoveredModelSelection() {
    const modelInput = document.getElementById('profile-model');
    if (!modelInput) {
        return;
    }
    const currentModel = String(modelInput.dataset.currentValue || modelInput.value || '').trim();
    if (currentModel && findDiscoveredModelEntry(currentModel)) {
        applyDiscoveredContextWindow(currentModel);
        renderDiscoveredModels();
        return;
    }
    if (!currentModel && draftDiscoveredModels.length > 0) {
        setDraftModelValue(draftDiscoveredModels[0].model);
        applyDiscoveredContextWindow(draftDiscoveredModels[0].model);
    }
    renderDiscoveredModels();
}

function handleContextWindowInputChanged() {
    const contextInput = document.getElementById('profile-context-window');
    if (!contextInput) {
        return;
    }
    delete contextInput.dataset.autofilledModel;
}

function setDraftModelValue(value) {
    const modelInput = document.getElementById('profile-model');
    if (!modelInput) {
        return;
    }
    const normalized = String(value || '').trim();
    modelInput.dataset.currentValue = normalized;
    modelInput.value = normalized;
}

function syncDraftModelSelection() {
    const modelInput = document.getElementById('profile-model');
    if (!modelInput) {
        return;
    }
    const normalized = String(modelInput.value || '').trim();
    modelInput.dataset.currentValue = normalized;
    applyDiscoveredContextWindow(normalized);
    renderDiscoveredModels();
    if (draftDiscoveredModels.length > 0) {
        setModelMenuOpen(true);
    }
}

function handleDiscoveredModelPicked(modelName) {
    setDraftModelValue(modelName || '');
    applyDiscoveredContextWindow(modelName || '');
    renderDiscoveredModels();
    setModelMenuOpen(false);
}

function openDiscoveredModelMenu() {
    if (draftDiscoveredModels.length === 0) {
        return;
    }
    setModelMenuOpen(true);
}

function toggleDiscoveredModelMenu() {
    if (draftDiscoveredModels.length === 0) {
        return;
    }
    setModelMenuOpen(!isModelMenuOpen);
}

function setModelMenuOpen(open) {
    const modelMenu = document.getElementById('profile-model-menu');
    if (!modelMenu) {
        return;
    }
    isModelMenuOpen = open === true && draftDiscoveredModels.length > 0;
    modelMenu.style.display = isModelMenuOpen ? 'block' : 'none';
}

function buildDiscoveredModelsMessage(result) {
    const modelCount = normalizeDiscoveredModels(result).length;
    if (modelCount === 0) {
        return formatMessage('settings.model.probe_no_models', { latency_ms: result.latency_ms });
    }
    return formatMessage('settings.model.models_fetched', {
        count: modelCount,
        latency_ms: result.latency_ms,
    });
}

function normalizeDiscoveredModels(result) {
    if (Array.isArray(result?.model_entries) && result.model_entries.length > 0) {
        return result.model_entries
            .filter(entry => entry && typeof entry === 'object')
            .map(entry => ({
                model: String(entry.model || '').trim(),
                context_window: Number.isInteger(entry.context_window) ? entry.context_window : null,
            }))
            .filter(entry => entry.model);
    }
    if (!Array.isArray(result?.models)) {
        return [];
    }
    return result.models
        .map(model => String(model || '').trim())
        .filter(Boolean)
        .map(model => ({ model, context_window: null }));
}

function findDiscoveredModelEntry(modelName) {
    const normalized = String(modelName || '').trim();
    if (!normalized) {
        return null;
    }
    return draftDiscoveredModels.find(entry => entry.model === normalized) || null;
}

function applyDiscoveredContextWindow(modelName) {
    const contextInput = document.getElementById('profile-context-window');
    if (!contextInput) {
        return;
    }
    const entry = findDiscoveredModelEntry(modelName);
    if (!entry || !Number.isInteger(entry.context_window) || entry.context_window <= 0) {
        if (String(contextInput.dataset.autofilledModel || '').trim()) {
            contextInput.value = '';
            delete contextInput.dataset.autofilledModel;
        }
        return;
    }
    contextInput.value = String(entry.context_window);
    contextInput.dataset.autofilledModel = entry.model;
}

function resetDraftEditorState() {
    draftProbeState = null;
    draftDiscoveredModels = [];
    draftModelDiscoveryState = null;
    draftApiKeyState = createDraftSecretState();
    draftMaasPasswordState = createDraftSecretState();
    isModelMenuOpen = false;
}

function renderProfileEditorTitle() {
    const titleEl = document.getElementById('profile-editor-title');
    if (!titleEl) {
        return;
    }
    titleEl.textContent = editingProfile ? t('settings.model.edit_profile') : t('settings.model.add_profile');
}

function getProviderDefaultBaseUrl(provider) {
    if (isMaaSProvider(provider)) {
        return DEFAULT_MAAS_BASE_URL;
    }
    return PROVIDER_DEFAULT_BASE_URLS[String(provider || '').trim()] || '';
}

function setDraftBaseUrlDefaultSource(provider, baseUrl) {
    const baseUrlInput = document.getElementById('profile-base-url');
    if (!baseUrlInput) {
        return;
    }
    const defaultBaseUrl = getProviderDefaultBaseUrl(provider);
    if (defaultBaseUrl && String(baseUrl || '').trim() === defaultBaseUrl) {
        baseUrlInput.dataset.defaultSourceProvider = String(provider || '').trim();
        return;
    }
    delete baseUrlInput.dataset.defaultSourceProvider;
}

function syncDraftBaseUrlDefaultSource() {
    const baseUrlInput = document.getElementById('profile-base-url');
    if (!baseUrlInput) {
        return;
    }
    const currentBaseUrl = String(baseUrlInput.value || '').trim();
    const trackedProvider = String(baseUrlInput.dataset.defaultSourceProvider || '').trim();
    const trackedDefaultBaseUrl = getProviderDefaultBaseUrl(trackedProvider);
    if (trackedDefaultBaseUrl && currentBaseUrl === trackedDefaultBaseUrl) {
        return;
    }
    setDraftBaseUrlDefaultSource(getDraftProvider(), currentBaseUrl);
}

function isMaaSProvider(provider) {
    return String(provider || '').trim() === 'maas';
}

function getDraftProvider() {
    const providerInput = document.getElementById('profile-provider');
    return providerInput ? String(providerInput.value || '').trim() || 'openai_compatible' : 'openai_compatible';
}

function readDraftMaasAuth() {
    return {
        username: document.getElementById('profile-maas-username').value.trim(),
        password: readDraftMaasPasswordValue(),
    };
}

function hasDraftMaasPassword(maasAuth) {
    return Boolean(maasAuth.password) || draftMaasPasswordState.hasPersistedValue;
}

function renderDraftProviderFields() {
    const maasProvider = isMaaSProvider(getDraftProvider());
    const apiKeyGroup = document.getElementById('profile-api-key-group');
    const maasFields = document.getElementById('profile-maas-auth-fields');
    const passwordInput = document.getElementById('profile-maas-password');
    const baseUrlInput = document.getElementById('profile-base-url');
    if (apiKeyGroup) {
        apiKeyGroup.style.display = maasProvider ? 'none' : 'block';
    }
    if (maasFields) {
        maasFields.style.display = maasProvider ? 'grid' : 'none';
    }
    if (passwordInput) {
        renderDraftMaaSPasswordField();
    }
    if (baseUrlInput) {
        baseUrlInput.disabled = maasProvider;
        if (maasProvider) {
            baseUrlInput.value = DEFAULT_MAAS_BASE_URL;
            baseUrlInput.title = DEFAULT_MAAS_BASE_URL;
        } else {
            baseUrlInput.title = '';
        }
    }
}

function readDraftMaasPasswordValue() {
    const maasPasswordInput = document.getElementById('profile-maas-password');
    const inputValue = maasPasswordInput ? maasPasswordInput.value.trim() : '';
    if (!draftMaasPasswordState.hasPersistedValue) {
        return inputValue || draftMaasPasswordState.draftValue.trim();
    }
    if (draftMaasPasswordState.isDirty) {
        return inputValue || draftMaasPasswordState.draftValue.trim();
    }
    return '';
}

function renderDraftMaaSPasswordField() {
    const maasPasswordInput = document.getElementById('profile-maas-password');
    if (!maasPasswordInput) {
        return;
    }

    if (draftMaasPasswordState.revealed) {
        maasPasswordInput.type = 'text';
        maasPasswordInput.value = draftMaasPasswordState.isDirty
            ? draftMaasPasswordState.draftValue
            : draftMaasPasswordState.persistedValue;
        maasPasswordInput.placeholder = '';
    } else if (draftMaasPasswordState.hasPersistedValue && !draftMaasPasswordState.isDirty) {
        maasPasswordInput.type = 'password';
        maasPasswordInput.value = '';
        maasPasswordInput.placeholder = '************';
    } else {
        maasPasswordInput.type = 'password';
        maasPasswordInput.value = draftMaasPasswordState.draftValue;
        maasPasswordInput.placeholder = 'password';
    }

    renderDraftMaaSPasswordToggle();
}

function renderDraftMaaSPasswordToggle() {
    const toggleMaasPasswordBtn = document.getElementById('toggle-profile-maas-password-btn');
    const maasPasswordInput = document.getElementById('profile-maas-password');
    if (!toggleMaasPasswordBtn) {
        return;
    }

    const inputValue = maasPasswordInput ? maasPasswordInput.value.trim() : '';
    const hasValue = draftMaasPasswordState.hasPersistedValue || Boolean(draftMaasPasswordState.draftValue.trim()) || Boolean(inputValue);
    toggleMaasPasswordBtn.style.display = hasValue ? 'inline-flex' : 'none';
    toggleMaasPasswordBtn.className = draftMaasPasswordState.revealed ? 'secure-input-btn is-active' : 'secure-input-btn';
    toggleMaasPasswordBtn.title = draftMaasPasswordState.revealed
        ? t('settings.model.hide_password')
        : t('settings.model.show_password');
    if (typeof toggleMaasPasswordBtn.setAttribute === 'function') {
        toggleMaasPasswordBtn.setAttribute('aria-label', toggleMaasPasswordBtn.title);
    } else {
        toggleMaasPasswordBtn.ariaLabel = toggleMaasPasswordBtn.title;
    }
}

function createDraftSecretState() {
    return {
        persistedValue: '',
        draftValue: '',
        hasPersistedValue: false,
        isDirty: false,
        revealed: false,
    };
}

function readDraftApiKeyValue() {
    const apiKeyInput = document.getElementById('profile-api-key');
    const inputValue = apiKeyInput ? apiKeyInput.value.trim() : '';
    if (!draftApiKeyState.hasPersistedValue) {
        return inputValue || draftApiKeyState.draftValue.trim();
    }
    if (draftApiKeyState.isDirty) {
        return inputValue || draftApiKeyState.draftValue.trim();
    }
    return '';
}

function isDraftSecretInputActive(secretInput) {
    if (!secretInput || typeof document !== 'object' || document === null) {
        return false;
    }
    if (!('activeElement' in document)) {
        return true;
    }
    return document.activeElement === secretInput;
}

function renderDraftApiKeyField() {
    const apiKeyInput = document.getElementById('profile-api-key');
    if (!apiKeyInput) {
        return;
    }

    if (draftApiKeyState.revealed) {
        apiKeyInput.type = 'text';
        apiKeyInput.value = draftApiKeyState.isDirty
            ? draftApiKeyState.draftValue
            : draftApiKeyState.persistedValue;
        apiKeyInput.placeholder = '';
    } else if (draftApiKeyState.hasPersistedValue && !draftApiKeyState.isDirty) {
        apiKeyInput.type = 'password';
        apiKeyInput.value = '';
        apiKeyInput.placeholder = '************';
    } else {
        apiKeyInput.type = 'password';
        apiKeyInput.value = draftApiKeyState.draftValue;
        apiKeyInput.placeholder = 'sk-...';
    }

    renderDraftApiKeyToggle();
}

function renderDraftApiKeyToggle() {
    const toggleApiKeyBtn = document.getElementById('toggle-profile-api-key-btn');
    const apiKeyInput = document.getElementById('profile-api-key');
    if (!toggleApiKeyBtn) {
        return;
    }

    const inputValue = apiKeyInput ? apiKeyInput.value.trim() : '';
    const hasValue = draftApiKeyState.hasPersistedValue || Boolean(draftApiKeyState.draftValue.trim()) || Boolean(inputValue);
    toggleApiKeyBtn.style.display = hasValue ? 'inline-flex' : 'none';
    toggleApiKeyBtn.className = draftApiKeyState.revealed ? 'secure-input-btn is-active' : 'secure-input-btn';
    toggleApiKeyBtn.title = draftApiKeyState.revealed
        ? t('settings.model.hide_api_key')
        : t('settings.model.show_api_key');
    if (typeof toggleApiKeyBtn.setAttribute === 'function') {
        toggleApiKeyBtn.setAttribute('aria-label', toggleApiKeyBtn.title);
    } else {
        toggleApiKeyBtn.ariaLabel = toggleApiKeyBtn.title;
    }
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
    const testButtonLabel = probeState?.status === 'probing' ? t('settings.model.testing') : t('settings.action.test');
    const providerLabel = formatProviderLabel(profile.provider);
    const defaultChip = profile.is_default === true
        ? `<span class="profile-card-chip profile-card-chip-accent">${escapeHtml(t('settings.model.default_badge'))}</span>`
        : '';
    const modelLabel = profile.model || t('settings.model.no_model');
    const baseUrlLabel = profile.base_url || t('settings.model.no_endpoint');

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
                    <button class="settings-inline-action settings-list-action profile-card-action-btn profile-card-test-btn" data-name="${escapeHtml(name)}" title="${escapeHtml(t('settings.action.test'))}" ${probeState?.status === 'probing' ? 'disabled' : ''}>${escapeHtml(testButtonLabel)}</button>
                    <button class="settings-inline-action settings-list-action profile-card-action-btn edit-profile-btn" data-name="${escapeHtml(name)}" title="${escapeHtml(t('settings.action.edit'))}">${escapeHtml(t('settings.action.edit'))}</button>
                    <button class="settings-inline-action settings-list-action settings-list-action-danger profile-card-action-btn delete-profile-btn" data-name="${escapeHtml(name)}" title="${escapeHtml(t('settings.action.delete'))}">${escapeHtml(t('settings.action.delete'))}</button>
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
        testButton.textContent = state?.status === 'probing' ? t('settings.model.testing') : t('settings.action.test');
        testButton.title = t('settings.action.test');
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
    if (provider === 'maas') {
        return 'MAAS';
    }
    if (provider === 'echo') {
        return 'Echo';
    }
    return provider || t('settings.model.unknown');
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
