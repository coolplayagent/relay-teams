/**
 * components/settings/modelProfiles.js
 * Model profile tab logic.
 */
import {
    deleteModelProfile,
    discoverModelCatalog,
    fetchModelCatalog,
    fetchCodeAgentOAuthSession,
    fetchModelFallbackConfig,
    fetchModelProfiles,
    probeModelConnection,
    refreshModelCatalog,
    reloadModelConfig,
    saveModelProfile,
    startCodeAgentOAuth,
} from '../../core/api.js';
import { showConfirmDialog, showToast } from '../../utils/feedback.js';
import { t } from '../../utils/i18n.js';
import { errorToPayload, logError } from '../../utils/logger.js';

let profiles = {};
let fallbackConfig = { policies: [] };
let editingProfile = null;
let profileProbeStates = {};
let draftProbeState = null;
let draftDiscoveredModels = [];
let draftModelDiscoveryState = null;
let draftApiKeyState = createDraftSecretState();
let draftMaasPasswordState = createDraftSecretState();
let draftCatalogSelection = null;
let modelCatalogProviders = [];
let selectedCatalogProviderId = '';
let catalogProviderSearch = '';
let catalogModelSearch = '';
let catalogLoadState = null;
let catalogPickerOpen = null;
let catalogProviderSearchDirty = false;
let catalogModelSearchDirty = false;
let catalogProviderKeyboardIndex = -1;
let catalogModelKeyboardIndex = -1;
let draftCodeAgentAuthState = createDraftCodeAgentAuthState();
let isModelMenuOpen = false;
let languageBound = false;
let draftBaseUrlExpanded = false;
let draftModelInputExpanded = false;
let draftProviderMode = 'external';

const DEFAULT_MAAS_BASE_URL = 'http://snapengine.cida.cce.prod-szv-g.dragon.tools.huawei.com/api/v2/';
const DEFAULT_CODEAGENT_BASE_URL = 'https://codeagentcli.rnd.huawei.com/codeAgentPro';

const PROVIDER_DEFAULT_BASE_URLS = {
    maas: DEFAULT_MAAS_BASE_URL,
    codeagent: DEFAULT_CODEAGENT_BASE_URL,
};
const IMAGE_CAPABILITY_MODES = {
    FOLLOW_DETECTION: 'follow_detection',
    SUPPORTED: 'supported',
    UNSUPPORTED: 'unsupported',
};
const PROVIDER_MODES = {
    EXTERNAL: 'external',
    MAAS: 'maas',
    CODEAGENT: 'codeagent',
    CUSTOM: 'custom',
};
const FALLBACK_POLICY_TRANSLATION_KEYS = {
    same_provider_then_other_provider: 'settings.model.fallback_policy_same_provider_then_other_provider',
    other_provider_only: 'settings.model.fallback_policy_other_provider_only',
};
let draftImageCapabilityMode = IMAGE_CAPABILITY_MODES.FOLLOW_DETECTION;

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

    const refreshCatalogBtn = document.getElementById('refresh-model-catalog-btn');
    if (refreshCatalogBtn) {
        refreshCatalogBtn.onclick = handleRefreshModelCatalog;
    }

    const catalogProviderSearchInput = document.getElementById('model-catalog-provider-search');
    if (catalogProviderSearchInput) {
        catalogProviderSearchInput.oninput = handleCatalogProviderSearch;
        catalogProviderSearchInput.onfocus = () => openCatalogPicker('provider');
        catalogProviderSearchInput.onclick = () => openCatalogPicker('provider');
        catalogProviderSearchInput.onkeydown = event => handleCatalogPickerKeydown(event, 'provider');
    }

    const catalogModelSearchInput = document.getElementById('model-catalog-model-search');
    if (catalogModelSearchInput) {
        catalogModelSearchInput.oninput = handleCatalogModelSearch;
        catalogModelSearchInput.onfocus = () => openCatalogPicker('model');
        catalogModelSearchInput.onclick = () => openCatalogPicker('model');
        catalogModelSearchInput.onkeydown = event => handleCatalogPickerKeydown(event, 'model');
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
    document.querySelectorAll('[data-provider-value]').forEach(button => {
        button.onclick = () => handleProviderChoice(button.dataset.providerMode || button.dataset.providerValue);
    });
    document.querySelectorAll('[data-profile-step-toggle]').forEach(button => {
        button.onclick = () => toggleProfileStep(button.dataset.profileStepToggle);
    });

    const apiKeyInput = document.getElementById('profile-api-key');
    if (apiKeyInput) {
        apiKeyInput.oninput = handleDraftApiKeyInput;
        apiKeyInput.onfocus = armDraftApiKeyInput;
        apiKeyInput.onpointerdown = armDraftApiKeyInput;
        apiKeyInput.onkeydown = armDraftApiKeyInput;
        apiKeyInput.onblur = disarmDraftApiKeyInput;
    }

    const maasUsernameInput = document.getElementById('profile-maas-username');
    if (maasUsernameInput) {
        maasUsernameInput.oninput = handleDraftEndpointChanged;
    }

    const maasPasswordInput = document.getElementById('profile-maas-password');
    if (maasPasswordInput) {
        maasPasswordInput.oninput = handleDraftMaasPasswordInput;
        maasPasswordInput.onfocus = armDraftMaasPasswordInput;
        maasPasswordInput.onpointerdown = armDraftMaasPasswordInput;
        maasPasswordInput.onkeydown = armDraftMaasPasswordInput;
        maasPasswordInput.onblur = disarmDraftMaasPasswordInput;
    }

    const codeagentLoginBtn = document.getElementById('profile-codeagent-login-status');
    if (codeagentLoginBtn) {
        codeagentLoginBtn.onclick = handleCodeAgentLogin;
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

    const defaultInput = document.getElementById('profile-is-default');
    if (defaultInput) {
        defaultInput.onchange = renderProfileEditorState;
    }

    const imageCapabilityInput = document.getElementById('profile-image-capability');
    if (imageCapabilityInput) {
        imageCapabilityInput.onchange = syncDraftImageCapabilityMode;
    }

    if (!languageBound && typeof document.addEventListener === 'function') {
        document.addEventListener('agent-teams-language-changed', handleModelProfileLanguageChanged);
        languageBound = true;
    }
}

export async function loadModelProfilesPanel() {
    try {
        const [loadedProfiles, loadedFallbackConfig] = await Promise.all([
            fetchModelProfiles(),
            fetchModelFallbackConfig(),
        ]);
        profiles = loadedProfiles;
        fallbackConfig = loadedFallbackConfig || { policies: [] };
        renderFallbackPolicyOptions();
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

function handleModelProfileLanguageChanged() {
    const profileEditor = document.getElementById('profile-editor');
    const editorVisible = profileEditor?.style.display !== 'none';
    if (editorVisible) {
        renderProfileEditorTitle();
        renderFallbackPolicyOptions();
        renderDraftApiKeyField();
        renderDraftProviderFields();
        renderDraftImageCapability();
        renderDraftProbeState();
        renderDraftModelDiscoveryState();
        renderDraftCodeAgentAuthState();
        renderDiscoveredModels();
        renderProfileEditorState();
        setModelCatalogPanelVisible(draftProviderMode === PROVIDER_MODES.EXTERNAL);
        renderModelCatalog();
        return;
    }
    renderProfiles();
}

async function loadModelCatalogForNewProfile() {
    setModelCatalogPanelVisible(draftProviderMode === PROVIDER_MODES.EXTERNAL);
    if (!catalogLoadState) {
        catalogLoadState = {
            status: 'probing',
            message: t('settings.model.catalog_loading'),
        };
    }
    renderModelCatalog();
    try {
        const result = await fetchModelCatalog();
        applyModelCatalogResult(result);
        renderModelCatalog();
        void refreshModelCatalogInBackground();
    } catch (e) {
        catalogLoadState = {
            status: 'failed',
            message: formatMessage('settings.model.catalog_failed', { error: e.message }),
        };
    }
    renderModelCatalog();
}

async function refreshModelCatalogInBackground() {
    try {
        const result = await refreshModelCatalog();
        applyModelCatalogResult(result);
    } catch (e) {
        if (modelCatalogProviders.length === 0) {
            catalogLoadState = {
                status: 'failed',
                message: formatMessage('settings.model.catalog_failed', { error: e.message }),
            };
        }
    }
    renderModelCatalog();
}

async function handleRefreshModelCatalog() {
    catalogLoadState = {
        status: 'probing',
        message: t('settings.model.catalog_refreshing'),
    };
    renderModelCatalog();
    try {
        const result = await refreshModelCatalog();
        applyModelCatalogResult(result);
    } catch (e) {
        catalogLoadState = {
            status: 'failed',
            message: formatMessage('settings.model.catalog_failed', { error: e.message }),
        };
    }
    renderModelCatalog();
}

function handleCatalogProviderSearch(event) {
    catalogPickerOpen = 'provider';
    catalogProviderSearchDirty = true;
    catalogProviderSearch = String(event?.target?.value || '');
    catalogProviderKeyboardIndex = -1;
    renderModelCatalog();
}

function handleCatalogModelSearch(event) {
    catalogPickerOpen = 'model';
    catalogModelSearchDirty = true;
    catalogModelSearch = String(event?.target?.value || '');
    catalogModelKeyboardIndex = -1;
    renderModelCatalog();
}

function openCatalogPicker(kind) {
    catalogPickerOpen = kind === 'provider' ? 'provider' : 'model';
    if (catalogPickerOpen === 'provider') {
        catalogProviderKeyboardIndex = -1;
    } else {
        catalogModelKeyboardIndex = -1;
    }
    renderModelCatalog();
}

function closeCatalogPicker() {
    catalogPickerOpen = null;
    catalogProviderSearchDirty = false;
    catalogModelSearchDirty = false;
    catalogProviderKeyboardIndex = -1;
    catalogModelKeyboardIndex = -1;
    renderModelCatalog();
}

function handleCatalogPickerKeydown(event, kind) {
    if (!event) {
        return;
    }
    if (event.key === 'Escape') {
        event.preventDefault();
        closeCatalogPicker();
        return;
    }
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp' && event.key !== 'Enter') {
        return;
    }
    event.preventDefault();
    if (kind === 'provider') {
        handleCatalogProviderKeyboard(event.key);
        return;
    }
    handleCatalogModelKeyboard(event.key);
}

function handleCatalogProviderKeyboard(key) {
    const providers = filterCatalogProviders();
    if (providers.length === 0) {
        catalogProviderKeyboardIndex = -1;
        renderModelCatalog();
        return;
    }
    catalogPickerOpen = 'provider';
    if (key === 'Enter') {
        const index = catalogProviderKeyboardIndex >= 0 ? catalogProviderKeyboardIndex : 0;
        selectCatalogProvider(providers[index]?.id || '');
        return;
    }
    catalogProviderKeyboardIndex = getNextCatalogKeyboardIndex(
        catalogProviderKeyboardIndex,
        providers.length,
        key === 'ArrowDown' ? 1 : -1,
    );
    renderModelCatalog();
}

function handleCatalogModelKeyboard(key) {
    const provider = findCatalogProvider(selectedCatalogProviderId);
    const models = filterCatalogModels(provider);
    const itemCount = models.length + (provider ? 1 : 0);
    if (itemCount === 0) {
        catalogModelKeyboardIndex = -1;
        renderModelCatalog();
        return;
    }
    catalogPickerOpen = 'model';
    if (key === 'Enter') {
        const index = catalogModelKeyboardIndex >= 0 ? catalogModelKeyboardIndex : 0;
        if (index === 0) {
            toggleDraftModelInputFields();
            return;
        }
        const model = models[index - 1];
        handleCatalogModelPicked(provider?.id || '', model?.id || '');
        return;
    }
    catalogModelKeyboardIndex = getNextCatalogKeyboardIndex(
        catalogModelKeyboardIndex,
        itemCount,
        key === 'ArrowDown' ? 1 : -1,
    );
    renderModelCatalog();
}

function getNextCatalogKeyboardIndex(currentIndex, itemCount, delta) {
    if (itemCount <= 0) {
        return -1;
    }
    const startIndex = currentIndex < 0 ? (delta > 0 ? -1 : 0) : currentIndex;
    return (startIndex + delta + itemCount) % itemCount;
}

function applyModelCatalogResult(result) {
    modelCatalogProviders = Array.isArray(result?.providers)
        ? result.providers.filter(provider => provider && Array.isArray(provider.models))
        : [];
    const preferredProviderId = draftProviderMode === PROVIDER_MODES.EXTERNAL
        ? String(draftCatalogSelection?.providerId || '').trim()
        : '';
    if (preferredProviderId && findCatalogProvider(preferredProviderId)) {
        selectedCatalogProviderId = preferredProviderId;
    }
    if (selectedCatalogProviderId && !findCatalogProvider(selectedCatalogProviderId)) {
        selectedCatalogProviderId = '';
    }
    if (!result?.ok) {
        const message = result?.error_message || result?.error_code || t('settings.model.unknown');
        catalogLoadState = {
            status: modelCatalogProviders.length > 0 ? 'failed' : 'failed',
            message: formatMessage('settings.model.catalog_failed', { error: message }),
        };
        return;
    }
    catalogLoadState = {
        status: 'success',
        message: formatModelCatalogStatus(result),
    };
}

function renderModelCatalog() {
    const providerListEl = document.getElementById('model-catalog-provider-list');
    const modelListEl = document.getElementById('model-catalog-model-list');
    const selectedEl = getOptionalElement('model-catalog-selected');
    const statusEl = document.getElementById('model-catalog-status');
    const refreshBtn = document.getElementById('refresh-model-catalog-btn');
    if (!providerListEl || !modelListEl || !statusEl || !refreshBtn) {
        return;
    }

    const filteredProviders = filterCatalogProviders();
    if (selectedCatalogProviderId && !findCatalogProvider(selectedCatalogProviderId)) {
        selectedCatalogProviderId = '';
    }
    if (catalogProviderKeyboardIndex >= filteredProviders.length) {
        catalogProviderKeyboardIndex = -1;
    }

    providerListEl.style.display = catalogPickerOpen === 'provider' ? 'block' : 'none';
    modelListEl.style.display = catalogPickerOpen === 'model' ? 'block' : 'none';
    providerListEl.innerHTML = filteredProviders.length === 0
        ? `<div class="model-catalog-empty">${escapeHtml(t('settings.model.catalog_empty'))}</div>`
        : filteredProviders.map(provider => renderCatalogProviderButton(provider)).join('');
    providerListEl.querySelectorAll('.model-catalog-provider-btn').forEach(button => {
        button.onclick = () => {
            selectCatalogProvider(button.dataset.providerId || '');
        };
    });

    const selectedProvider = findCatalogProvider(selectedCatalogProviderId);
    const models = filterCatalogModels(selectedProvider);
    const customModelButton = selectedProvider ? renderCatalogCustomModelButton() : '';
    const modelEmptyText = selectedProvider
        ? t('settings.model.catalog_no_models')
        : t('settings.model.catalog_select_provider_first');
    if (catalogModelKeyboardIndex >= models.length + (selectedProvider ? 1 : 0)) {
        catalogModelKeyboardIndex = -1;
    }
    modelListEl.innerHTML = models.length === 0
        ? `${customModelButton}<div class="model-catalog-empty">${escapeHtml(modelEmptyText)}</div>`
        : `${customModelButton}${models.map(model => renderCatalogModelButton(selectedProvider, model)).join('')}`;
    modelListEl.querySelectorAll('.model-catalog-custom-model-btn').forEach(button => {
        button.onclick = toggleDraftModelInputFields;
    });
    const customModelInput = getOptionalElement('model-catalog-custom-model-input');
    if (customModelInput) {
        customModelInput.oninput = handleCatalogCustomModelInput;
        customModelInput.onkeydown = handleCatalogCustomModelKeydown;
    }
    const customModelApplyBtn = getOptionalElement('model-catalog-custom-model-apply-btn');
    if (customModelApplyBtn) {
        customModelApplyBtn.onclick = applyCatalogCustomModelInput;
    }
    modelListEl.querySelectorAll('.model-catalog-model-btn').forEach(button => {
        button.onclick = () => handleCatalogModelPicked(
            button.dataset.providerId || '',
            button.dataset.modelId || '',
        );
    });
    if (selectedEl) {
        selectedEl.innerHTML = renderCatalogSelectionSummary();
    }
    syncCatalogPickerInputs(selectedProvider);

    if (!catalogLoadState) {
        statusEl.textContent = t('settings.model.catalog_loading');
        statusEl.className = 'model-catalog-status probe-status probe-status-probing';
    } else {
        statusEl.textContent = catalogLoadState.message;
        statusEl.className = `model-catalog-status probe-status probe-status-${catalogLoadState.status}`;
    }
    refreshBtn.disabled = catalogLoadState?.status === 'probing';
}

function selectCatalogProvider(providerId) {
    const provider = findCatalogProvider(providerId);
    if (!provider) {
        return;
    }
    selectedCatalogProviderId = provider.id;
    catalogProviderSearch = '';
    catalogModelSearch = '';
    catalogProviderSearchDirty = false;
    catalogModelSearchDirty = false;
    catalogProviderKeyboardIndex = -1;
    catalogModelKeyboardIndex = -1;
    if (draftCatalogSelection?.providerId !== selectedCatalogProviderId) {
        draftCatalogSelection = null;
        setDraftModelValue('');
    }
    catalogPickerOpen = 'model';
    renderModelCatalog();
    getOptionalElement('model-catalog-model-search')?.focus();
}

function renderCatalogCustomModelButton() {
    const activeClass = catalogModelKeyboardIndex === 0 ? ' is-keyboard-active' : '';
    if (draftProviderMode === PROVIDER_MODES.EXTERNAL && draftModelInputExpanded) {
        return renderCatalogCustomModelInput(activeClass);
    }
    return `
        <button class="model-catalog-custom-model-btn${activeClass}" type="button">
            <span class="model-catalog-model-main">
                <span class="model-catalog-model-name">${escapeHtml(t('settings.model.custom_model'))}</span>
                <span class="model-catalog-model-summary">${escapeHtml(t('settings.model.custom_model_catalog_hint'))}</span>
            </span>
        </button>
    `;
}

function renderCatalogCustomModelInput(activeClass) {
    const model = String(getOptionalElement('profile-model')?.value || '').trim();
    return `
        <div class="model-catalog-custom-model-slot${activeClass}" id="model-catalog-custom-model-slot">
            <div class="model-catalog-model-main">
                <label class="model-catalog-model-name" for="model-catalog-custom-model-input">${escapeHtml(t('settings.model.custom_model'))}</label>
                <span class="model-catalog-model-summary">${escapeHtml(t('settings.model.custom_model_catalog_hint'))}</span>
            </div>
            <div class="model-catalog-custom-model-row">
                <input type="text" id="model-catalog-custom-model-input" value="${escapeHtml(model)}" placeholder="${escapeHtml(t('settings.model.custom_model_placeholder'))}" autocomplete="off" spellcheck="false">
                <button class="settings-inline-action settings-list-action" id="model-catalog-custom-model-apply-btn" type="button">${escapeHtml(t('settings.model.use_custom_model'))}</button>
            </div>
        </div>
    `;
}

function handleCatalogCustomModelInput(event) {
    const value = String(event?.target?.value || '').trim();
    syncDraftModelValueWithoutRender(value);
    draftCatalogSelection = null;
    setElementText('profile-model-summary', formatDraftModelSummary(value));
}

function handleCatalogCustomModelKeydown(event) {
    if (event?.key === 'Enter') {
        event.preventDefault();
        applyCatalogCustomModelInput();
    }
}

function applyCatalogCustomModelInput() {
    const model = String(getOptionalElement('model-catalog-custom-model-input')?.value || '').trim();
    if (!model) {
        return;
    }
    const provider = findCatalogProvider(selectedCatalogProviderId);
    draftCatalogSelection = provider
        ? {
            providerId: provider.id,
            providerName: provider.name || provider.id,
            modelName: model,
        }
        : null;
    setDraftModelValue(model);
    catalogModelSearch = '';
    catalogModelSearchDirty = false;
    catalogPickerOpen = null;
    renderProfileEditorState();
}

function syncCatalogPickerInputs(selectedProvider) {
    const providerSearchInput = getOptionalElement('model-catalog-provider-search');
    const modelSearchInput = getOptionalElement('model-catalog-model-search');
    if (providerSearchInput) {
        providerSearchInput.value = catalogPickerOpen === 'provider' && catalogProviderSearchDirty
            ? catalogProviderSearch
            : catalogProviderDisplayName(selectedProvider);
    }
    if (modelSearchInput) {
        modelSearchInput.value = catalogPickerOpen === 'model' && catalogModelSearchDirty
            ? catalogModelSearch
            : catalogModelDisplayName();
    }
}

function catalogProviderDisplayName(provider) {
    return String(
        provider?.name
        || draftCatalogSelection?.providerName
        || draftCatalogSelection?.providerId
        || '',
    ).trim();
}

function catalogModelDisplayName() {
    return String(
        draftCatalogSelection?.modelName
        || getOptionalElement('profile-model')?.value
        || '',
    ).trim();
}

function renderCatalogProviderButton(provider) {
    const index = filterCatalogProviders().findIndex(candidate => candidate.id === provider.id);
    const activeClass = provider.id === selectedCatalogProviderId ? ' is-active' : '';
    const keyboardClass = index === catalogProviderKeyboardIndex ? ' is-keyboard-active' : '';
    const modelCount = Array.isArray(provider.models) ? provider.models.length : 0;
    return `
        <button class="model-catalog-provider-btn${activeClass}${keyboardClass}" type="button" data-provider-id="${escapeHtml(provider.id)}">
            <span class="model-catalog-provider-name">${escapeHtml(provider.name || provider.id)}</span>
            <span class="model-catalog-provider-count">${escapeHtml(String(modelCount))}</span>
        </button>
    `;
}

function renderCatalogModelButton(provider, model) {
    const providerId = provider?.id || '';
    const savedCatalogModelName = String(draftCatalogSelection?.modelName || '').trim();
    const draftModelId = String(getOptionalElement('profile-model')?.value || '').trim();
    const selectedClass = draftCatalogSelection?.providerId === providerId
        && (draftModelId === model.id || (savedCatalogModelName && savedCatalogModelName === model.name))
        ? ' is-active'
        : '';
    const providerModels = filterCatalogModels(provider);
    const keyboardIndex = providerModels.findIndex(candidate => candidate.id === model.id);
    const keyboardClass = keyboardIndex >= 0 && catalogModelKeyboardIndex === keyboardIndex + 1
        ? ' is-keyboard-active'
        : '';
    const summaryParts = [];
    if (Number.isInteger(model.context_window)) {
        summaryParts.push(formatContextWindowLabel(model.context_window));
    }
    if (model.reasoning === true) {
        summaryParts.push(t('settings.model.catalog_reasoning'));
    }
    if (model.tool_call === true) {
        summaryParts.push(t('settings.model.catalog_tools'));
    }
    const summary = summaryParts.join(' / ');
    return `
        <button class="model-catalog-model-btn${selectedClass}${keyboardClass}" type="button" data-provider-id="${escapeHtml(providerId)}" data-model-id="${escapeHtml(model.id)}">
            <span class="model-catalog-model-main">
                <span class="model-catalog-model-name">${escapeHtml(model.name || model.id)}</span>
                <span class="model-catalog-model-id">${escapeHtml(model.id)}</span>
                ${summary ? `<span class="model-catalog-model-summary">${escapeHtml(summary)}</span>` : ''}
            </span>
            ${renderInputCapabilityChip(model.capabilities, {
                compact: true,
                inputModalities: model.input_modalities,
            })}
        </button>
    `;
}

function renderCatalogSelectionSummary() {
    const model = String(getOptionalElement('profile-model')?.value || '').trim();
    const providerName = draftCatalogSelection?.providerName || draftCatalogSelection?.providerId || '';
    if (!model) {
        return `
            <div class="model-catalog-selected-title">${escapeHtml(t('settings.model.catalog_selected'))}</div>
            <div class="model-catalog-selected-empty">${escapeHtml(t('settings.model.catalog_selected_empty'))}</div>
        `;
    }
    return `
        <div class="model-catalog-selected-title">${escapeHtml(t('settings.model.catalog_selected'))}</div>
        <div class="model-catalog-selected-model">${escapeHtml(draftCatalogSelection?.modelName || model)}</div>
        <div class="model-catalog-selected-meta">${escapeHtml(providerName || getDraftProvider())} · ${escapeHtml(model)}</div>
    `;
}

function filterCatalogProviders() {
    const query = catalogProviderSearch.trim().toLowerCase();
    if (!query) {
        return modelCatalogProviders;
    }
    return modelCatalogProviders.filter(provider => {
        const haystack = `${provider.id || ''} ${provider.name || ''}`.toLowerCase();
        return haystack.includes(query);
    });
}

function filterCatalogModels(provider) {
    if (!provider || !Array.isArray(provider.models)) {
        return [];
    }
    const query = catalogModelSearch.trim().toLowerCase();
    if (!query) {
        return provider.models;
    }
    return provider.models.filter(model => {
        const haystack = `${model.id || ''} ${model.name || ''} ${model.family || ''}`.toLowerCase();
        return haystack.includes(query);
    });
}

function findCatalogProvider(providerId) {
    const normalized = String(providerId || '').trim();
    if (!normalized) {
        return null;
    }
    return modelCatalogProviders.find(provider => provider.id === normalized) || null;
}

function findCatalogModel(provider, modelId) {
    const normalized = String(modelId || '').trim();
    if (!provider || !normalized || !Array.isArray(provider.models)) {
        return null;
    }
    return provider.models.find(model => model.id === normalized) || null;
}

function handleCatalogModelPicked(providerId, modelId) {
    const provider = findCatalogProvider(providerId);
    const model = findCatalogModel(provider, modelId);
    if (!provider || !model) {
        return;
    }
    const providerApi = String(provider.api || '').trim();
    const runtimeProvider = mapCatalogProviderToRuntimeProvider(provider.id);
    const catalogMaasProvider = isMaaSProvider(runtimeProvider);
    draftProviderMode = catalogMaasProvider ? PROVIDER_MODES.MAAS : PROVIDER_MODES.EXTERNAL;
    draftBaseUrlExpanded = !catalogMaasProvider && !providerApi;
    draftModelInputExpanded = catalogMaasProvider;
    catalogProviderSearch = '';
    catalogModelSearch = '';
    catalogProviderSearchDirty = false;
    catalogModelSearchDirty = false;
    catalogPickerOpen = null;
    selectedCatalogProviderId = provider.id;
    draftCatalogSelection = {
        providerId: provider.id,
        providerName: provider.name || provider.id,
        modelName: model.name || model.id,
    };
    const profileNameInput = document.getElementById('profile-name');
    if (!editingProfile && !String(profileNameInput.value || '').trim()) {
        profileNameInput.value = buildCatalogProfileName(provider.id, model.id);
    }
    setDraftProviderValue(runtimeProvider);
    setDraftModelValue(model.id);
    document.getElementById('profile-base-url').value = providerApi;
    document.getElementById('profile-base-url').dataset.initialValue = providerApi;
    document.getElementById('profile-base-url').dataset.previousProvider = getDraftProvider();
    delete document.getElementById('profile-base-url').dataset.defaultSourceProvider;
    document.getElementById('profile-max-tokens').value = model.output_limit ? String(model.output_limit) : '';
    document.getElementById('profile-context-window').value = model.context_window ? String(model.context_window) : '';
    delete document.getElementById('profile-context-window').dataset.autofilledModel;
    draftImageCapabilityMode = deriveImageCapabilityMode(model.capabilities, model.input_modalities);
    draftDiscoveredModels = [normalizeCatalogModelForDraft(model)];

    renderDraftApiKeyField();
    renderDraftProviderFields();
    renderDraftImageCapability();
    renderDraftProbeState();
    renderDraftModelDiscoveryState();
    renderDiscoveredModels();
    renderProfileEditorState();
    setModelMenuOpen(false);
    document.getElementById('profile-api-key').focus();
}

function normalizeCatalogModelForDraft(model) {
    return {
        model: model.id,
        context_window: Number.isInteger(model.context_window) ? model.context_window : null,
        capabilities: normalizeModelCapabilities(model.capabilities, model.input_modalities),
        input_modalities: normalizeInputModalities(model.input_modalities, model.capabilities),
    };
}

function buildCatalogProfileName(providerId, modelId) {
    const base = `${providerId}-${modelId}`
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9_-]+/g, '-')
        .replace(/-+/g, '-')
        .replace(/^-|-$/g, '') || 'model-profile';
    if (!profiles[base]) {
        return base;
    }
    for (let index = 2; index < 1000; index += 1) {
        const candidate = `${base}-${index}`;
        if (!profiles[candidate]) {
            return candidate;
        }
    }
    return `${base}-${Date.now()}`;
}

function mapCatalogProviderToRuntimeProvider(providerId) {
    const normalized = String(providerId || '').trim().toLowerCase();
    if (normalized === 'maas' || normalized.includes('maas')) {
        return 'maas';
    }
    return 'openai_compatible';
}

function formatModelCatalogStatus(result) {
    const providerCount = Array.isArray(result?.providers) ? result.providers.length : 0;
    const modelCount = modelCatalogProviders.reduce(
        (total, provider) => total + (Array.isArray(provider.models) ? provider.models.length : 0),
        0,
    );
    const age = Number.isInteger(result?.cache_age_seconds)
        ? formatMessage('settings.model.catalog_cache_age', {
            seconds: result.cache_age_seconds,
        })
        : t('settings.model.catalog_cache_current');
    return formatMessage('settings.model.catalog_loaded', {
        providers: providerCount,
        models: modelCount,
        age,
    });
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
    listEl.querySelectorAll('.set-default-profile-btn').forEach(btn => {
        btn.onclick = () => handleSetDefaultProfile(btn.dataset.name);
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
    resetCatalogFilters();
    renderProfileEditorTitle();
    renderFallbackPolicyOptions();
    document.getElementById('profile-name').value = '';
    setDraftProviderValue('openai_compatible');
    setDraftModelValue('');
    document.getElementById('profile-base-url').value = '';
    delete document.getElementById('profile-base-url').dataset.initialValue;
    delete document.getElementById('profile-base-url').dataset.previousProvider;
    delete document.getElementById('profile-base-url').dataset.defaultSourceProvider;
    draftApiKeyState = createDraftSecretState();
    draftMaasPasswordState = createDraftSecretState();
    draftCodeAgentAuthState = createDraftCodeAgentAuthState();
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
    document.getElementById('profile-fallback-policy').value = '';
    document.getElementById('profile-fallback-priority').value = '0';
    draftImageCapabilityMode = IMAGE_CAPABILITY_MODES.FOLLOW_DETECTION;

    showProfileEditor();
    renderDraftApiKeyField();
    renderDraftProviderFields();
    renderDraftImageCapability();
    renderDraftProbeState();
    renderDraftModelDiscoveryState();
    renderDiscoveredModels();
    renderProfileEditorState();
    setModelMenuOpen(false);
    void loadModelCatalogForNewProfile();
    document.getElementById('profile-name').focus();
}

function handleEditProfile(name) {
    const profile = profiles[name];
    if (!profile) return;

    editingProfile = name;
    resetDraftEditorState();
    renderProfileEditorTitle();
    renderFallbackPolicyOptions();
    document.getElementById('profile-name').value = name;
    setDraftProviderValue(profile.provider || 'openai_compatible');
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
        armedForInput: false,
        revealed: false,
    };
    draftMaasPasswordState = {
        persistedValue: typeof profile.maas_auth?.password === 'string' ? profile.maas_auth.password : '',
        draftValue: '',
        hasPersistedValue: Boolean(profile.maas_auth?.has_password),
        isDirty: false,
        armedForInput: false,
        revealed: false,
    };
    document.getElementById('profile-maas-username').value = profile.maas_auth?.username || '';
    document.getElementById('profile-maas-password').value = '';
    const codeagentAuth = profile.codeagent_auth || {};
    draftCodeAgentAuthState = {
        authSessionId: '',
        completed: Boolean(codeagentAuth.has_refresh_token),
        hasPersistedAccessToken: Boolean(codeagentAuth.has_access_token),
        hasPersistedRefreshToken: Boolean(codeagentAuth.has_refresh_token),
        statusMessage: Boolean(codeagentAuth.has_refresh_token) ? 'Signed in' : 'Not signed in',
    };
    document.getElementById('profile-is-default').checked = profile.is_default === true;
    document.getElementById('profile-temperature').value = profile.temperature || 0.7;
    document.getElementById('profile-top-p').value = profile.top_p || 1.0;
    document.getElementById('profile-max-tokens').value = profile.max_tokens || '';
    document.getElementById('profile-context-window').value = profile.context_window || '';
    delete document.getElementById('profile-context-window').dataset.autofilledModel;
    document.getElementById('profile-connect-timeout').value = profile.connect_timeout_seconds || 15;
    document.getElementById('profile-ssl-verify').value = serializeTriStateValue(profile.ssl_verify);
    document.getElementById('profile-fallback-policy').value = profile.fallback_policy_id || '';
    document.getElementById('profile-fallback-priority').value = String(profile.fallback_priority || 0);
    draftCatalogSelection = buildDraftCatalogSelection(profile);
    draftImageCapabilityMode = deriveImageCapabilityMode(profile.capabilities);
    draftProviderMode = isMaaSProvider(profile.provider)
        ? PROVIDER_MODES.MAAS
        : isCodeAgentProvider(profile.provider)
            ? PROVIDER_MODES.CODEAGENT
            : profile.catalog_provider_id
            ? PROVIDER_MODES.EXTERNAL
            : PROVIDER_MODES.CUSTOM;
    draftModelInputExpanded = draftProviderMode !== PROVIDER_MODES.EXTERNAL;
    draftBaseUrlExpanded = draftProviderMode === PROVIDER_MODES.CUSTOM
        || (draftProviderMode === PROVIDER_MODES.EXTERNAL && !String(profile.base_url || '').trim());
    if (draftCatalogSelection?.providerId) {
        selectedCatalogProviderId = draftCatalogSelection.providerId;
    }

    showProfileEditor();
    renderDraftApiKeyField();
    renderDraftProviderFields();
    renderDraftImageCapability();
    renderDraftProbeState();
    renderDraftModelDiscoveryState();
    renderDiscoveredModels();
    renderProfileEditorState();
    setModelMenuOpen(false);
    if (draftProviderMode === PROVIDER_MODES.EXTERNAL) {
        void loadModelCatalogForNewProfile();
    }
}

function handleCancelProfile() {
    showProfilesList();
    editingProfile = null;
    resetDraftEditorState();
    resetCatalogFilters();
    renderDraftImageCapability();
    renderDraftProbeState();
    renderDraftModelDiscoveryState();
    renderDiscoveredModels();
    renderProfileEditorState();
    setModelMenuOpen(false);
}

async function handleSaveProfile() {
    const name = document.getElementById('profile-name').value.trim();
    const provider = getDraftProvider();
    const model = document.getElementById('profile-model').value.trim();
    const baseUrl = document.getElementById('profile-base-url').value.trim();
    const apiKey = readDraftApiKeyValue();
    const maasAuth = readDraftMaasAuth();
    const codeagentAuth = readDraftCodeAgentAuth();
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
    const fallbackPolicyId = String(
        document.getElementById('profile-fallback-policy').value || '',
    ).trim();
    const fallbackPriority = Math.max(
        0,
        parseInt(document.getElementById('profile-fallback-priority').value || '0', 10) || 0,
    );
    const discoveredModelEntry = findDiscoveredModelEntry(model);

    if (!name) {
        showToast({ title: t('settings.model.profile_required_title'), message: t('settings.model.profile_required_message'), tone: 'warning' });
        return;
    }

    if (!model) {
        showToast({ title: t('settings.model.model_required_title'), message: t('settings.model.model_required_message'), tone: 'warning' });
        return;
    }

    if (!baseUrl && !isCodeAgentProvider(provider)) {
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
    } else if (isCodeAgentProvider(provider)) {
        if (!hasDraftCodeAgentAuth()) {
            showToast({
                title: t('settings.model.save_failed_title'),
                message: 'CodeAgent profiles require SSO login before saving.',
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
        base_url: isCodeAgentProvider(provider) ? DEFAULT_CODEAGENT_BASE_URL : baseUrl,
        is_default: isDefault,
        temperature: temperature,
        top_p: topP,
        context_window: contextWindow,
        fallback_policy_id: fallbackPolicyId || null,
        fallback_priority: fallbackPriority,
        connect_timeout_seconds: connectTimeoutSeconds,
    };
    if (draftCatalogSelection) {
        profile.catalog_provider_id = draftCatalogSelection.providerId;
        profile.catalog_provider_name = draftCatalogSelection.providerName;
        profile.catalog_model_name = draftCatalogSelection.modelName;
    } else {
        profile.catalog_provider_id = null;
        profile.catalog_provider_name = null;
        profile.catalog_model_name = null;
    }
    if (maxTokens !== null) {
        profile.max_tokens = maxTokens;
    }
    if (sslVerify !== null) {
        profile.ssl_verify = sslVerify;
    }
    profile.capabilities = buildDraftProfileCapabilities(discoveredModelEntry);

    if (isMaaSProvider(provider)) {
        profile.maas_auth = {
            username: maasAuth.username,
        };
        if (maasAuth.password) {
            profile.maas_auth.password = maasAuth.password;
        }
    } else if (isCodeAgentProvider(provider)) {
        profile.codeagent_auth = {
            has_access_token: codeagentAuth.has_access_token,
            has_refresh_token: codeagentAuth.has_refresh_token,
        };
        if (codeagentAuth.oauth_session_id) {
            profile.codeagent_auth.oauth_session_id = codeagentAuth.oauth_session_id;
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
        resetCatalogFilters();
        renderDraftProbeState();
        renderDraftModelDiscoveryState();
        renderDiscoveredModels();
        setModelMenuOpen(false);
        showToast({ title: t('settings.model.saved_title'), message: t('settings.model.saved_message_detail'), tone: 'success' });
        await loadModelProfilesPanel();
        notifyModelProfilesUpdated();
    } catch (e) {
        showToast({ title: t('settings.model.save_failed_title'), message: formatMessage('settings.model.save_failed_detail', { error: e.message }), tone: 'danger' });
    }
}

async function handleSetDefaultProfile(name) {
    const profile = profiles[name];
    if (!profile || profile.is_default === true) {
        return;
    }
    try {
        await saveModelProfile(name, buildExistingProfileSavePayload(profile, { isDefault: true }));
        await reloadModelConfig();
        showToast({
            title: t('settings.model.default_saved_title'),
            message: formatMessage('settings.model.default_saved_message', { name }),
            tone: 'success',
        });
        await loadModelProfilesPanel();
        notifyModelProfilesUpdated();
    } catch (e) {
        showToast({
            title: t('settings.model.save_failed_title'),
            message: formatMessage('settings.model.save_failed_detail', { error: e.message }),
            tone: 'danger',
        });
    }
}

function buildExistingProfileSavePayload(profile, options = {}) {
    const payload = {
        provider: profile.provider || 'openai_compatible',
        model: profile.model || '',
        base_url: profile.base_url || '',
        is_default: options.isDefault === true ? true : profile.is_default === true,
        temperature: Number(profile.temperature ?? 0.7),
        top_p: Number(profile.top_p ?? 1.0),
        context_window: Number.isInteger(profile.context_window) ? profile.context_window : null,
        fallback_policy_id: profile.fallback_policy_id || null,
        fallback_priority: Number(profile.fallback_priority || 0),
        connect_timeout_seconds: Number(profile.connect_timeout_seconds || 15),
    };
    if (Number.isInteger(profile.max_tokens)) {
        payload.max_tokens = profile.max_tokens;
    }
    if (profile.ssl_verify === true || profile.ssl_verify === false) {
        payload.ssl_verify = profile.ssl_verify;
    }
    if (profile.catalog_provider_id) {
        payload.catalog_provider_id = profile.catalog_provider_id;
    }
    if (profile.catalog_provider_name) {
        payload.catalog_provider_name = profile.catalog_provider_name;
    }
    if (profile.catalog_model_name) {
        payload.catalog_model_name = profile.catalog_model_name;
    }
    if (profile.capabilities && typeof profile.capabilities === 'object') {
        payload.capabilities = profile.capabilities;
    }
    return payload;
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
        notifyModelProfilesUpdated();
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

async function handleCodeAgentLogin() {
    const provider = getDraftProvider();
    if (!isCodeAgentProvider(provider)) {
        return;
    }
    if (draftCodeAgentAuthState.pendingAuthorizationUrl) {
        await continueCodeAgentLogin();
        return;
    }
    if (draftCodeAgentAuthState.loginInProgress) {
        return;
    }
    const authPopup = openCodeAgentAuthorizationPopup();
    draftCodeAgentAuthState.loginInProgress = true;
    draftCodeAgentAuthState.statusMessage = 'Starting SSO login';
    renderDraftCodeAgentAuthState();
    try {
        const result = await startCodeAgentOAuth({});
        draftCodeAgentAuthState.authSessionId = result.auth_session_id;
        draftCodeAgentAuthState.completed = false;
        draftCodeAgentAuthState.statusMessage = 'Waiting for SSO callback';
        renderDraftCodeAgentAuthState();
        if (!result.authorization_url) {
            throw new Error('CodeAgent OAuth response did not include an authorization URL.');
        }
        if (!navigateCodeAgentAuthorizationPopup(authPopup, result.authorization_url)) {
            if (authPopup && typeof authPopup.close === 'function') {
                authPopup.close();
            }
            draftCodeAgentAuthState.pendingAuthorizationUrl = result.authorization_url;
            draftCodeAgentAuthState.statusMessage = 'SSO popup blocked';
            renderDraftCodeAgentAuthState();
            return;
        }
        draftCodeAgentAuthState.pendingAuthorizationUrl = '';
        await pollCodeAgentOAuthSession(result.auth_session_id);
    } catch (e) {
        if (authPopup && typeof authPopup.close === 'function') {
            authPopup.close();
        }
        draftCodeAgentAuthState.pendingAuthorizationUrl = '';
        draftCodeAgentAuthState.statusMessage = `SSO failed: ${e.message}`;
        renderDraftCodeAgentAuthState();
    } finally {
        draftCodeAgentAuthState.loginInProgress = false;
        renderDraftCodeAgentAuthState();
    }
}

async function continueCodeAgentLogin() {
    const authSessionId = String(draftCodeAgentAuthState.authSessionId || '').trim();
    const authorizationUrl = String(
        draftCodeAgentAuthState.pendingAuthorizationUrl || '',
    ).trim();
    if (!authSessionId || !authorizationUrl) {
        return;
    }
    const authPopup = openCodeAgentAuthorizationPopup(authorizationUrl);
    if (!authPopup) {
        draftCodeAgentAuthState.statusMessage = 'SSO popup blocked';
        renderDraftCodeAgentAuthState();
        return;
    }
    draftCodeAgentAuthState.pendingAuthorizationUrl = '';
    draftCodeAgentAuthState.loginInProgress = true;
    draftCodeAgentAuthState.statusMessage = 'Waiting for SSO callback';
    renderDraftCodeAgentAuthState();
    try {
        await pollCodeAgentOAuthSession(authSessionId);
    } catch (e) {
        draftCodeAgentAuthState.statusMessage = `SSO failed: ${e.message}`;
        renderDraftCodeAgentAuthState();
    } finally {
        draftCodeAgentAuthState.loginInProgress = false;
        renderDraftCodeAgentAuthState();
    }
}

function openCodeAgentAuthorizationPopup(initialUrl = 'about:blank') {
    const authPopup = window.open(initialUrl, '_blank');
    if (authPopup && typeof authPopup === 'object') {
        try {
            authPopup.opener = null;
        } catch {
            // Ignore browsers that do not allow mutating opener on the popup proxy.
        }
    }
    return authPopup;
}

function navigateCodeAgentAuthorizationPopup(authPopup, authorizationUrl) {
    const normalizedUrl = String(authorizationUrl || '').trim();
    if (!normalizedUrl) {
        return false;
    }
    if (authPopup && authPopup.location) {
        try {
            if (typeof authPopup.location.replace === 'function') {
                authPopup.location.replace(normalizedUrl);
                return true;
            }
            authPopup.location.href = normalizedUrl;
            return true;
        } catch {
            return false;
        }
    }
    return false;
}

async function pollCodeAgentOAuthSession(authSessionId) {
    for (let attempt = 0; attempt < 900; attempt += 1) {
        await new Promise(resolve => setTimeout(resolve, 2000));
        if (draftCodeAgentAuthState.authSessionId !== authSessionId) {
            return;
        }
        const result = await fetchCodeAgentOAuthSession(authSessionId);
        if (draftCodeAgentAuthState.authSessionId !== authSessionId) {
            return;
        }
        if (!result.completed) {
            continue;
        }
        if (draftCodeAgentAuthState.authSessionId !== authSessionId) {
            return;
        }
        draftCodeAgentAuthState.completed = true;
        draftCodeAgentAuthState.hasPersistedAccessToken = true;
        draftCodeAgentAuthState.hasPersistedRefreshToken = true;
        draftCodeAgentAuthState.statusMessage = 'Signed in';
        renderDraftCodeAgentAuthState();
        return;
    }
    if (draftCodeAgentAuthState.authSessionId !== authSessionId) {
        return;
    }
    draftCodeAgentAuthState.statusMessage = 'SSO login timed out';
    renderDraftCodeAgentAuthState();
}

function buildDraftProbePayload() {
    const provider = getDraftProvider();
    const model = document.getElementById('profile-model').value.trim();
    const baseUrl = document.getElementById('profile-base-url').value.trim();
    const apiKey = readDraftApiKeyValue();
    const maasAuth = readDraftMaasAuth();
    const codeagentAuth = readDraftCodeAgentAuth();
    const temperature = parseFloat(document.getElementById('profile-temperature').value) || 0.7;
    const topP = parseFloat(document.getElementById('profile-top-p').value) || 1.0;
    const maxTokensValue = String(document.getElementById('profile-max-tokens').value || '').trim();
    const maxTokens = maxTokensValue ? parseInt(maxTokensValue) || null : null;
    const connectTimeoutSeconds = parseFloat(document.getElementById('profile-connect-timeout').value) || 15;
    const sslVerify = parseTriStateValue(document.getElementById('profile-ssl-verify').value);

    if (!model || (!baseUrl && !isCodeAgentProvider(provider))) {
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
    } else if (isCodeAgentProvider(provider)) {
        if (!hasDraftCodeAgentAuth()) {
            draftProbeState = {
                status: 'failed',
                message: 'Model and SSO login are required before testing a CodeAgent profile.',
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
        base_url: isCodeAgentProvider(provider) ? DEFAULT_CODEAGENT_BASE_URL : baseUrl,
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
    } else if (isCodeAgentProvider(provider)) {
        override.codeagent_auth = {
            oauth_session_id: codeagentAuth.oauth_session_id,
            has_access_token: codeagentAuth.has_access_token,
            has_refresh_token: codeagentAuth.has_refresh_token,
        };
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
    const codeagentAuth = readDraftCodeAgentAuth();
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
    } else if (isCodeAgentProvider(provider)) {
        if (!hasDraftCodeAgentAuth()) {
            draftDiscoveredModels = [];
            draftModelDiscoveryState = {
                status: 'failed',
                message: 'SSO login is required before fetching CodeAgent models.',
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
        base_url: isCodeAgentProvider(provider) ? DEFAULT_CODEAGENT_BASE_URL : baseUrl,
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
    } else if (isCodeAgentProvider(provider)) {
        override.codeagent_auth = {
            oauth_session_id: codeagentAuth.oauth_session_id,
            has_access_token: codeagentAuth.has_access_token,
            has_refresh_token: codeagentAuth.has_refresh_token,
        };
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
    const statusEl = document.getElementById('profile-probe-inline-status');
    const testBtn = document.getElementById('test-profile-btn');
    if (!statusEl || !testBtn) {
        return;
    }

    if (!draftProbeState) {
        statusEl.style.display = 'none';
        statusEl.textContent = '';
        statusEl.className = 'settings-action-status profile-probe-inline-status';
        testBtn.disabled = false;
        testBtn.textContent = t('settings.action.test');
        return;
    }

    statusEl.style.display = 'inline-flex';
    statusEl.textContent = draftProbeState.message;
    statusEl.className = `settings-action-status profile-probe-inline-status profile-probe-inline-status-${draftProbeState.status}`;
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
        const summaryMarkup = renderDiscoveredModelSummary(modelEntry);
        menuOptions.push(
            `
                <button class="profile-model-menu-item${activeClass}" data-model-name="${escapeHtml(modelName)}" type="button">
                    <span class="profile-model-menu-copy">
                        <span class="profile-model-menu-name">${escapeHtml(modelName)}</span>
                        ${summaryMarkup}
                    </span>
                    ${renderInputCapabilityChip(modelEntry.capabilities, {
                        compact: true,
                        inputModalities: modelEntry.input_modalities,
                    })}
                </button>
            `,
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
    const previousProvider = String(getOptionalElement('profile-base-url')?.dataset.previousProvider || '').trim();
    if (getDraftProvider() === 'maas') {
        draftProviderMode = PROVIDER_MODES.MAAS;
    } else if (getDraftProvider() === 'codeagent') {
        draftProviderMode = PROVIDER_MODES.CODEAGENT;
    } else if (
        draftProviderMode === PROVIDER_MODES.MAAS
        || draftProviderMode === PROVIDER_MODES.CODEAGENT
    ) {
        draftProviderMode = PROVIDER_MODES.EXTERNAL;
    }
    applyProviderDefaultBaseUrl();
    syncDraftBaseUrlDefaultSource();
    const maasProvider = isMaaSProvider(getDraftProvider());
    const codeagentProvider = isCodeAgentProvider(getDraftProvider());
    if (maasProvider || codeagentProvider) {
        draftModelInputExpanded = true;
        draftBaseUrlExpanded = false;
    } else if (
        (isMaaSProvider(previousProvider) || isCodeAgentProvider(previousProvider))
        && draftProviderMode === PROVIDER_MODES.EXTERNAL
    ) {
        draftModelInputExpanded = false;
    }
    renderDraftProviderFields();
    renderProfileEditorState();
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
    if (isMaaSProvider(provider) || isCodeAgentProvider(provider)) {
        baseUrlInput.value = getProviderDefaultBaseUrl(provider);
        baseUrlInput.dataset.previousProvider = provider;
        baseUrlInput.dataset.defaultSourceProvider = provider;
        return;
    }
    if (
        providerChanged
        && (isMaaSProvider(previousProvider) || isCodeAgentProvider(previousProvider))
    ) {
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
        && !canAcceptDraftApiKeyInput(apiKeyInput)
    ) {
        draftApiKeyState.draftValue = '';
        draftApiKeyState.isDirty = false;
        draftApiKeyState.armedForInput = false;
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
        && !canAcceptDraftMaasPasswordInput(maasPasswordInput)
    ) {
        draftMaasPasswordState.draftValue = '';
        draftMaasPasswordState.isDirty = false;
        draftMaasPasswordState.armedForInput = false;
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
    renderProfileEditorState();
}

function setDraftModelValue(value) {
    syncDraftModelValueWithoutRender(value);
    renderProfileEditorState();
}

function syncDraftModelValueWithoutRender(value) {
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
    renderProfileEditorState();
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
                capabilities: normalizeModelCapabilities(entry.capabilities, entry.input_modalities),
                input_modalities: normalizeInputModalities(
                    entry.input_modalities,
                    entry.capabilities,
                ),
            }))
            .filter(entry => entry.model);
    }
    if (!Array.isArray(result?.models)) {
        return [];
    }
    return result.models
        .map(model => String(model || '').trim())
        .filter(Boolean)
        .map(model => ({
            model,
            context_window: null,
            capabilities: normalizeModelCapabilities(null, []),
            input_modalities: [],
        }));
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
    draftCodeAgentAuthState = createDraftCodeAgentAuthState();
    draftCatalogSelection = null;
    draftImageCapabilityMode = IMAGE_CAPABILITY_MODES.FOLLOW_DETECTION;
    isModelMenuOpen = false;
    draftBaseUrlExpanded = false;
    draftModelInputExpanded = false;
    draftProviderMode = PROVIDER_MODES.EXTERNAL;
    catalogPickerOpen = null;
}

function resetCatalogFilters() {
    catalogProviderSearch = '';
    catalogModelSearch = '';
    catalogProviderSearchDirty = false;
    catalogModelSearchDirty = false;
    const providerSearchInput = document.getElementById('model-catalog-provider-search');
    const modelSearchInput = document.getElementById('model-catalog-model-search');
    if (providerSearchInput) {
        providerSearchInput.value = '';
    }
    if (modelSearchInput) {
        modelSearchInput.value = '';
    }
    selectedCatalogProviderId = '';
    catalogPickerOpen = null;
}

function setModelCatalogPanelVisible(visible) {
    const panel = document.getElementById('model-catalog-panel');
    if (panel) {
        panel.style.display = visible ? 'flex' : 'none';
    }
}

function buildDraftCatalogSelection(profile) {
    const providerId = String(profile?.catalog_provider_id || '').trim();
    if (!providerId) {
        return null;
    }
    const providerName = String(profile?.catalog_provider_name || providerId).trim();
    const modelName = String(profile?.catalog_model_name || profile?.model || '').trim();
    return {
        providerId,
        providerName: providerName || providerId,
        modelName: modelName || String(profile?.model || '').trim(),
    };
}

function notifyModelProfilesUpdated() {
    if (typeof document?.dispatchEvent !== 'function' || typeof CustomEvent !== 'function') {
        return;
    }
    document.dispatchEvent(new CustomEvent('agent-teams-model-profiles-updated'));
}

function deriveImageCapabilityMode(capabilities, inputModalities = []) {
    const imageCapability = resolveImageCapabilityState(capabilities, inputModalities);
    if (imageCapability === true) {
        return IMAGE_CAPABILITY_MODES.SUPPORTED;
    }
    if (imageCapability === false) {
        return IMAGE_CAPABILITY_MODES.UNSUPPORTED;
    }
    return IMAGE_CAPABILITY_MODES.FOLLOW_DETECTION;
}

function syncDraftImageCapabilityMode() {
    const imageCapabilityInput = document.getElementById('profile-image-capability');
    draftImageCapabilityMode = normalizeImageCapabilityMode(imageCapabilityInput?.value);
    renderDraftImageCapability();
    renderProfileEditorState();
}

function renderDraftImageCapability() {
    const imageCapabilityInput = document.getElementById('profile-image-capability');
    if (!imageCapabilityInput) {
        return;
    }
    imageCapabilityInput.value = normalizeImageCapabilityMode(draftImageCapabilityMode);
}

function normalizeImageCapabilityMode(value) {
    const normalized = String(value || '').trim().toLowerCase();
    if (normalized === IMAGE_CAPABILITY_MODES.SUPPORTED) {
        return IMAGE_CAPABILITY_MODES.SUPPORTED;
    }
    if (normalized === IMAGE_CAPABILITY_MODES.UNSUPPORTED) {
        return IMAGE_CAPABILITY_MODES.UNSUPPORTED;
    }
    return IMAGE_CAPABILITY_MODES.FOLLOW_DETECTION;
}

function buildDraftProfileCapabilities(discoveredModelEntry) {
    const baseCapabilities = resolveDraftCapabilityBase(discoveredModelEntry);
    const imageCapabilityMode = normalizeImageCapabilityMode(draftImageCapabilityMode);
    const imageCapability = imageCapabilityMode === IMAGE_CAPABILITY_MODES.SUPPORTED
        ? true
        : imageCapabilityMode === IMAGE_CAPABILITY_MODES.UNSUPPORTED
            ? false
            : null;
    return {
        input: {
            ...baseCapabilities.input,
            image: imageCapability,
        },
        output: {
            ...baseCapabilities.output,
        },
    };
}

function resolveDraftCapabilityBase(discoveredModelEntry) {
    if (discoveredModelEntry?.capabilities) {
        return normalizeModelCapabilities(
            discoveredModelEntry.capabilities,
            discoveredModelEntry.input_modalities,
        );
    }
    const editingProfileRecord = editingProfile ? profiles[editingProfile] : null;
    if (editingProfileRecord?.capabilities) {
        return normalizeModelCapabilities(
            editingProfileRecord.capabilities,
            editingProfileRecord.input_modalities,
        );
    }
    return normalizeModelCapabilities(null, []);
}

function renderProfileEditorTitle() {
    const titleEl = document.getElementById('profile-editor-title');
    if (!titleEl) {
        return;
    }
    titleEl.textContent = editingProfile ? t('settings.model.edit_profile') : t('settings.model.add_profile');
}

function handleProviderChoice(provider) {
    const providerInput = document.getElementById('profile-provider');
    if (!providerInput) {
        return;
    }
    const nextMode = normalizeProviderMode(provider);
    const nextProvider = nextMode === PROVIDER_MODES.MAAS
        ? 'maas'
        : nextMode === PROVIDER_MODES.CODEAGENT
            ? 'codeagent'
            : 'openai_compatible';
    draftProviderMode = nextMode;
    catalogPickerOpen = null;
    setDraftProviderValue(nextProvider);
    if (nextMode === PROVIDER_MODES.MAAS) {
        draftCatalogSelection = null;
        draftModelInputExpanded = true;
        draftBaseUrlExpanded = false;
        setModelCatalogPanelVisible(false);
    } else if (nextMode === PROVIDER_MODES.CODEAGENT) {
        draftCatalogSelection = null;
        draftModelInputExpanded = true;
        draftBaseUrlExpanded = false;
        setModelCatalogPanelVisible(false);
    } else if (nextMode === PROVIDER_MODES.CUSTOM) {
        draftCatalogSelection = null;
        draftModelInputExpanded = true;
        draftBaseUrlExpanded = true;
        setModelCatalogPanelVisible(false);
    } else {
        draftModelInputExpanded = false;
        draftBaseUrlExpanded = false;
        setModelCatalogPanelVisible(true);
        if (!catalogLoadState || modelCatalogProviders.length === 0) {
            void loadModelCatalogForNewProfile();
        }
    }
    handleDraftEndpointChanged();
}

function normalizeProviderMode(value) {
    const normalized = String(value || '').trim().toLowerCase();
    if (normalized === PROVIDER_MODES.MAAS || normalized === 'maas') {
        return PROVIDER_MODES.MAAS;
    }
    if (normalized === PROVIDER_MODES.CODEAGENT || normalized === 'codeagent') {
        return PROVIDER_MODES.CODEAGENT;
    }
    if (normalized === PROVIDER_MODES.CUSTOM) {
        return PROVIDER_MODES.CUSTOM;
    }
    return PROVIDER_MODES.EXTERNAL;
}

function toggleDraftBaseUrlFields() {
    draftProviderMode = PROVIDER_MODES.CUSTOM;
    setDraftProviderValue('openai_compatible');
    draftBaseUrlExpanded = true;
    draftModelInputExpanded = true;
    renderProfileEditorState();
    getOptionalElement('profile-base-url')?.focus();
}

function toggleDraftModelInputFields() {
    if (draftProviderMode !== PROVIDER_MODES.CUSTOM) {
        draftProviderMode = PROVIDER_MODES.EXTERNAL;
    }
    draftModelInputExpanded = true;
    renderProfileEditorState();
    if (draftProviderMode === PROVIDER_MODES.EXTERNAL) {
        getOptionalElement('model-catalog-custom-model-input')?.focus();
    } else {
        getOptionalElement('profile-model')?.focus();
    }
}

function toggleProfileStep(stepName) {
    const step = findProfileStep(stepName);
    if (!step) {
        return;
    }
    setElementClassFlag(step, 'is-open', !hasElementClass(step, 'is-open'));
}

function renderProfileEditorState() {
    const provider = getDraftProvider();
    const maasProvider = isMaaSProvider(provider);
    const codeagentProvider = isCodeAgentProvider(provider);
    const customMode = draftProviderMode === PROVIDER_MODES.CUSTOM;
    const marketplaceBaseUrlVisible = draftProviderMode === PROVIDER_MODES.EXTERNAL && draftBaseUrlExpanded;
    const model = String(getOptionalElement('profile-model')?.value || '').trim();
    const temperature = String(getOptionalElement('profile-temperature')?.value || '0.7').trim() || '0.7';
    const topP = String(getOptionalElement('profile-top-p')?.value || '1.0').trim() || '1.0';
    const fallbackPolicyId = String(getOptionalElement('profile-fallback-policy')?.value || '').trim();
    const fallbackPriority = String(getOptionalElement('profile-fallback-priority')?.value || '0').trim() || '0';
    const apiKeyConfigured = draftApiKeyState.hasPersistedValue || Boolean(readDraftApiKeyValue());
    const maasAuth = readDraftMaasAuth();
    const credentialsReady = maasProvider
        ? Boolean(maasAuth.username && hasDraftMaasPassword(maasAuth))
        : codeagentProvider
            ? hasDraftCodeAgentAuth()
            : apiKeyConfigured;

    setElementText(
        'profile-model-summary',
        formatDraftModelSummary(model),
    );
    setElementText(
        'profile-credentials-summary',
        credentialsReady ? t('settings.model.credentials_configured') : t('settings.model.credentials_missing'),
    );
    setElementClassFlag(
        getOptionalElement('profile-primary-credentials-row'),
        'is-missing-required',
        !maasProvider && !codeagentProvider && !apiKeyConfigured,
    );
    setElementText(
        'profile-advanced-summary',
        formatMessage('settings.model.advanced_summary', { temperature, top_p: topP }),
    );
    setElementText(
        'profile-fallback-summary',
        fallbackPolicyId
            ? `${formatFallbackPolicyLabel(fallbackPolicyId)} · ${formatMessage('settings.model.priority_compact', { priority: fallbackPriority })}`
            : t('settings.model.fallback_disabled'),
    );

    setProviderChoiceActive('profile-provider-external-btn', draftProviderMode === PROVIDER_MODES.EXTERNAL);
    setProviderChoiceActive('profile-provider-maas-btn', draftProviderMode === PROVIDER_MODES.MAAS);
    setProviderChoiceActive('profile-provider-codeagent-btn', draftProviderMode === PROVIDER_MODES.CODEAGENT);
    setProviderChoiceActive('profile-provider-custom-btn', customMode);
    setOptionalElementDisplay('profile-base-url-fields', customMode || marketplaceBaseUrlVisible ? 'block' : 'none');
    setOptionalElementDisplay('profile-model-group', maasProvider || codeagentProvider || customMode ? 'block' : 'none');
    setModelCatalogPanelVisible(draftProviderMode === PROVIDER_MODES.EXTERNAL);
    renderModelCatalog();
}

function formatDraftModelSummary(model) {
    const providerLabel = formatDraftProviderModeLabel();
    const modelLabel = model || t('settings.model.no_model');
    if (draftProviderMode !== PROVIDER_MODES.EXTERNAL) {
        return `${providerLabel} · ${modelLabel}`;
    }
    const catalogProvider = String(draftCatalogSelection?.providerName || draftCatalogSelection?.providerId || '').trim();
    const catalogModel = String(draftCatalogSelection?.modelName || model || '').trim();
    if (catalogProvider && catalogModel) {
        return `${providerLabel} · ${catalogProvider} · ${catalogModel}`;
    }
    if (catalogProvider) {
        return `${providerLabel} · ${catalogProvider}`;
    }
    return `${providerLabel} · ${modelLabel}`;
}

function formatDraftProviderModeLabel() {
    if (draftProviderMode === PROVIDER_MODES.MAAS) {
        return t('settings.model.provider_maas');
    }
    if (draftProviderMode === PROVIDER_MODES.CODEAGENT) {
        return t('settings.model.provider_codeagent');
    }
    if (draftProviderMode === PROVIDER_MODES.CUSTOM) {
        return t('settings.model.provider_custom');
    }
    return t('settings.model.provider_external');
}

function setProviderChoiceActive(elementId, active) {
    const element = getOptionalElement(elementId);
    if (!element) {
        return;
    }
    setElementClassFlag(element, 'is-active', active);
}

function setElementText(elementId, value) {
    const element = getOptionalElement(elementId);
    if (element) {
        element.textContent = value;
    }
}

function setOptionalElementDisplay(elementId, display) {
    const element = getOptionalElement(elementId);
    if (element) {
        element.style.display = display;
    }
}

function getOptionalElement(elementId) {
    try {
        return document.getElementById(elementId);
    } catch (_e) {
        return null;
    }
}

function findProfileStep(stepName) {
    const normalized = String(stepName || '').trim();
    if (!normalized || typeof document.querySelectorAll !== 'function') {
        return null;
    }
    return Array.from(document.querySelectorAll('[data-profile-step]'))
        .find(step => step?.dataset?.profileStep === normalized) || null;
}

function hasElementClass(element, className) {
    if (element?.classList && typeof element.classList.contains === 'function') {
        return element.classList.contains(className);
    }
    return String(element?.className || '').split(/\s+/).includes(className);
}

function setElementClassFlag(element, className, active) {
    if (!element) {
        return;
    }
    if (element.classList && typeof element.classList.toggle === 'function') {
        element.classList.toggle(className, active);
        return;
    }
    const classes = new Set(String(element.className || '').split(/\s+/).filter(Boolean));
    if (active) {
        classes.add(className);
    } else {
        classes.delete(className);
    }
    element.className = Array.from(classes).join(' ');
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

function isCodeAgentProvider(provider) {
    return String(provider || '').trim() === 'codeagent';
}

function getDraftProvider() {
    const providerInput = document.getElementById('profile-provider');
    return providerInput ? String(providerInput.value || '').trim() || 'openai_compatible' : 'openai_compatible';
}

function setDraftProviderValue(provider) {
    const providerInput = getOptionalElement('profile-provider');
    if (!providerInput) {
        return;
    }
    const normalized = String(provider || '').trim() || 'openai_compatible';
    ensureProviderOption(providerInput, normalized);
    providerInput.value = normalized;
}

function ensureProviderOption(providerInput, value) {
    if (!value || typeof document === 'undefined' || typeof document.createElement !== 'function') {
        return;
    }
    const options = Array.from(providerInput.options || []);
    if (options.some(option => option.value === value)) {
        return;
    }
    const option = document.createElement('option');
    option.value = value;
    option.textContent = value;
    providerInput.appendChild(option);
}

function readDraftMaasAuth() {
    return {
        username: document.getElementById('profile-maas-username').value.trim(),
        password: readDraftMaasPasswordValue(),
    };
}

function readDraftCodeAgentAuth() {
    const completedSessionId = draftCodeAgentAuthState.completed
        ? draftCodeAgentAuthState.authSessionId || null
        : null;
    return {
        oauth_session_id: completedSessionId,
        has_access_token: draftCodeAgentAuthState.completed || draftCodeAgentAuthState.hasPersistedAccessToken,
        has_refresh_token: draftCodeAgentAuthState.completed || draftCodeAgentAuthState.hasPersistedRefreshToken,
    };
}

function hasDraftCodeAgentAuth() {
    return draftCodeAgentAuthState.completed || draftCodeAgentAuthState.hasPersistedRefreshToken;
}

function renderDraftCodeAgentAuthState() {
    const loginBtn = document.getElementById('profile-codeagent-login-status');
    const statusMessageEl = document.getElementById('profile-codeagent-login-status-message');
    const provider = getDraftProvider();
    const isCodeAgent = isCodeAgentProvider(provider);
    const hasAuth = hasDraftCodeAgentAuth();
    const statusMessage = draftCodeAgentAuthState.statusMessage
        || (hasAuth ? 'Signed in' : 'Not signed in');
    const loginLabel = t('settings.model.codeagent_sign_in_sso');
    const showStatus = isCodeAgent && statusMessage && statusMessage !== 'Not signed in';
    const statusTone = getCodeAgentAuthStatusTone(statusMessage, hasAuth);

    if (loginBtn) {
        loginBtn.textContent = loginLabel;
        loginBtn.title = loginLabel;
        loginBtn.setAttribute('aria-label', loginLabel);
        loginBtn.disabled = !isCodeAgent || draftCodeAgentAuthState.loginInProgress;
    }
    if (statusMessageEl) {
        statusMessageEl.textContent = showStatus ? localizeCodeAgentAuthStatusMessage(statusMessage) : '';
        statusMessageEl.style.display = showStatus ? 'block' : 'none';
        statusMessageEl.className = showStatus
            ? `codeagent-sso-status-message probe-status probe-status-${statusTone}`
            : 'codeagent-sso-status-message';
    }
}

function localizeCodeAgentAuthStatusMessage(statusMessage) {
    const message = String(statusMessage || '').trim();
    if (message === 'Starting SSO login') {
        return t('settings.model.codeagent_sso_starting');
    }
    if (message === 'Waiting for SSO callback') {
        return t('settings.model.codeagent_sso_waiting');
    }
    if (message === 'Signed in') {
        return t('settings.model.codeagent_sso_signed_in');
    }
    if (message === 'SSO popup blocked') {
        return t('settings.model.codeagent_sso_popup_blocked');
    }
    if (message === 'SSO login timed out') {
        return t('settings.model.codeagent_sso_timed_out');
    }
    if (message.startsWith('SSO failed: ')) {
        return formatMessage('settings.model.codeagent_sso_failed', {
            error: message.slice('SSO failed: '.length),
        });
    }
    return message;
}

function getCodeAgentAuthStatusTone(statusMessage, hasAuth) {
    const normalizedMessage = String(statusMessage || '').toLowerCase();
    if (normalizedMessage.includes('failed') || normalizedMessage.includes('timed out')) {
        return 'failed';
    }
    if (hasAuth || normalizedMessage.includes('signed in')) {
        return 'success';
    }
    return 'probing';
}

function hasDraftMaasPassword(maasAuth) {
    return Boolean(maasAuth.password) || draftMaasPasswordState.hasPersistedValue;
}

function syncDraftModelFieldPlacement(maasProvider, codeagentProvider = false) {
    const primaryCredentialsRow = document.getElementById('profile-primary-credentials-row');
    const modelFieldHome = document.getElementById('profile-model-field-home');
    const apiKeyGroup = document.getElementById('profile-api-key-group');
    const modelGroup = document.getElementById('profile-model-group');
    const maasModelSlot = document.getElementById('profile-maas-model-slot');
    const codeagentModelSlot = document.getElementById('profile-codeagent-model-slot');
    if (!primaryCredentialsRow || !modelFieldHome || !apiKeyGroup || !modelGroup || !maasModelSlot || !codeagentModelSlot) {
        return;
    }

    if (maasProvider) {
        if (modelGroup.parentElement !== maasModelSlot && typeof maasModelSlot.appendChild === 'function') {
            maasModelSlot.appendChild(modelGroup);
        }
        primaryCredentialsRow.style.display = 'none';
        return;
    }
    if (codeagentProvider) {
        if (modelGroup.parentElement !== codeagentModelSlot && typeof codeagentModelSlot.appendChild === 'function') {
            codeagentModelSlot.appendChild(modelGroup);
        }
        primaryCredentialsRow.style.display = 'none';
        return;
    }

    if (modelGroup.parentElement !== modelFieldHome && typeof modelFieldHome.appendChild === 'function') {
        modelFieldHome.appendChild(modelGroup);
    }
    primaryCredentialsRow.style.display = 'grid';
}

function renderDraftProviderFields() {
    const provider = getDraftProvider();
    const maasProvider = isMaaSProvider(provider);
    const codeagentProvider = isCodeAgentProvider(provider);
    const apiKeyGroup = document.getElementById('profile-api-key-group');
    const maasFields = document.getElementById('profile-maas-auth-fields');
    const codeagentFields = document.getElementById('profile-codeagent-auth-fields');
    const passwordInput = document.getElementById('profile-maas-password');
    const baseUrlInput = document.getElementById('profile-base-url');
    const baseUrlGroup = baseUrlInput ? baseUrlInput.closest('.form-group') : null;
    syncDraftModelFieldPlacement(maasProvider, codeagentProvider);
    if (apiKeyGroup) {
        apiKeyGroup.style.display = (maasProvider || codeagentProvider) ? 'none' : 'block';
    }
    if (maasFields) {
        maasFields.style.display = maasProvider ? 'grid' : 'none';
    }
    if (codeagentFields) {
        codeagentFields.style.display = codeagentProvider ? 'grid' : 'none';
    }
    if (passwordInput) {
        renderDraftMaaSPasswordField();
    }
    if (baseUrlInput) {
        baseUrlInput.disabled = maasProvider || codeagentProvider;
        if (maasProvider || codeagentProvider) {
            baseUrlInput.value = getProviderDefaultBaseUrl(provider);
            baseUrlInput.title = getProviderDefaultBaseUrl(provider);
        } else {
            baseUrlInput.title = '';
        }
    }
    if (baseUrlGroup) {
        baseUrlGroup.style.display = codeagentProvider ? 'none' : 'block';
    }
    renderDraftCodeAgentAuthState();
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
        armedForInput: false,
        revealed: false,
    };
}

function createDraftCodeAgentAuthState() {
    return {
        authSessionId: '',
        completed: false,
        hasPersistedAccessToken: false,
        hasPersistedRefreshToken: false,
        loginInProgress: false,
        pendingAuthorizationUrl: '',
        statusMessage: 'Not signed in',
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

function armDraftApiKeyInput() {
    draftApiKeyState.armedForInput = true;
}

function disarmDraftApiKeyInput() {
    draftApiKeyState.armedForInput = false;
}

function canAcceptDraftApiKeyInput(secretInput) {
    if (!secretInput) {
        return false;
    }
    if (draftApiKeyState.armedForInput) {
        return true;
    }
    if (typeof document !== 'object' || document === null) {
        return false;
    }
    if (!('activeElement' in document)) {
        return true;
    }
    return document.activeElement === secretInput;
}

function armDraftMaasPasswordInput() {
    draftMaasPasswordState.armedForInput = true;
}

function disarmDraftMaasPasswordInput() {
    draftMaasPasswordState.armedForInput = false;
}

function canAcceptDraftMaasPasswordInput(secretInput) {
    if (!secretInput) {
        return false;
    }
    if (draftMaasPasswordState.armedForInput) {
        return true;
    }
    if (typeof document !== 'object' || document === null) {
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
    setModelCatalogPanelVisible(false);
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
    const capabilityChip = renderInputCapabilityChip(
        profile.resolved_capabilities || profile.capabilities,
        {
        inputModalities: profile.input_modalities,
        },
    );
    const modelLabel = profile.model || t('settings.model.no_model');
    const baseUrlLabel = profile.base_url || t('settings.model.no_endpoint');
    const fallbackLabel = profile.fallback_policy_id
        ? formatFallbackPolicyLabel(profile.fallback_policy_id)
        : t('settings.model.fallback_disabled');
    const fallbackPriority = Number(profile.fallback_priority || 0);
    const fallbackPriorityLabel = formatMessage('settings.model.priority_compact', {
        priority: fallbackPriority,
    });

    return `
        <div class="profile-record profile-card" data-profile-name="${escapeHtml(name)}" style="--profile-index:${index};">
            <div class="profile-record-main">
                <div class="profile-record-heading">
                    <div class="profile-card-heading">
                        <div class="profile-card-title-row">
                            <h4>${escapeHtml(name)}</h4>
                            <div class="profile-card-chips">
                                <span class="profile-card-chip">${escapeHtml(providerLabel)}</span>
                                ${capabilityChip}
                                ${defaultChip}
                            </div>
                        </div>
                        <div class="profile-record-summary" title="${escapeHtml(`${modelLabel} ${baseUrlLabel}`)}">
                            <span class="profile-record-summary-primary">${escapeHtml(modelLabel)}</span>
                            <span class="profile-record-summary-separator">/</span>
                            <span class="profile-record-summary-secondary">${escapeHtml(baseUrlLabel)}</span>
                        </div>
                        <div class="profile-record-summary" title="${escapeHtml(fallbackLabel)}">
                            <span class="profile-record-summary-primary">${escapeHtml(fallbackLabel)}</span>
                            <span class="profile-record-summary-separator">/</span>
                            <span class="profile-record-summary-secondary">${escapeHtml(fallbackPriorityLabel)}</span>
                        </div>
                    </div>
                </div>
                <div class="profile-card-actions">
                    <button class="settings-inline-action settings-list-action profile-card-action-btn set-default-profile-btn" data-name="${escapeHtml(name)}" title="${escapeHtml(t('settings.model.default_model_action'))}" ${profile.is_default === true ? 'disabled' : ''}>${escapeHtml(t('settings.model.default_model_action_short'))}</button>
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
    if (provider === 'codeagent') {
        return t('settings.model.provider_codeagent');
    }
    if (provider === 'echo') {
        return 'Echo';
    }
    return provider || t('settings.model.unknown');
}

function normalizeInputModalities(inputModalities, capabilities = null) {
    const normalized = Array.isArray(inputModalities)
        ? inputModalities
        .map(modality => String(modality || '').trim().toLowerCase())
        .filter(Boolean)
        : [];
    return deriveInputModalitiesFromCapabilities(capabilities, normalized);
}

function normalizeModelCapabilities(capabilities, inputModalities = []) {
    const normalizedInput = normalizeCapabilityMatrix(capabilities?.input);
    const normalizedOutput = normalizeCapabilityMatrix(capabilities?.output);
    const normalizedInputModalities = Array.isArray(inputModalities)
        ? inputModalities
            .map(modality => String(modality || '').trim().toLowerCase())
            .filter(Boolean)
        : [];
    if (normalizedInput.image === null && normalizedInputModalities.includes('image')) {
        normalizedInput.image = true;
    }
    if (normalizedInput.audio === null && normalizedInputModalities.includes('audio')) {
        normalizedInput.audio = true;
    }
    if (normalizedInput.video === null && normalizedInputModalities.includes('video')) {
        normalizedInput.video = true;
    }
    if (normalizedInput.text === null) {
        normalizedInput.text = true;
    }
    if (normalizedOutput.text === null) {
        normalizedOutput.text = true;
    }
    return {
        input: normalizedInput,
        output: normalizedOutput,
    };
}

function normalizeCapabilityMatrix(matrix) {
    return {
        text: normalizeOptionalCapabilityFlag(matrix?.text),
        image: normalizeOptionalCapabilityFlag(matrix?.image),
        audio: normalizeOptionalCapabilityFlag(matrix?.audio),
        video: normalizeOptionalCapabilityFlag(matrix?.video),
        pdf: normalizeOptionalCapabilityFlag(matrix?.pdf),
    };
}

function normalizeOptionalCapabilityFlag(value) {
    if (value === true) {
        return true;
    }
    if (value === false) {
        return false;
    }
    return null;
}

function deriveInputModalitiesFromCapabilities(capabilities, fallback = []) {
    const derived = Array.isArray(fallback) ? [...fallback] : [];
    const normalizedCapabilities = normalizeModelCapabilities(capabilities, derived);
    if (normalizedCapabilities.input.image === true && !derived.includes('image')) {
        derived.push('image');
    }
    if (normalizedCapabilities.input.audio === true && !derived.includes('audio')) {
        derived.push('audio');
    }
    if (normalizedCapabilities.input.video === true && !derived.includes('video')) {
        derived.push('video');
    }
    return derived;
}

function resolveImageCapabilityState(capabilities, inputModalities = []) {
    const normalizedCapabilities = normalizeModelCapabilities(capabilities, inputModalities);
    return normalizedCapabilities.input.image;
}

function renderInputCapabilityChip(capabilities, { compact = false, inputModalities = [] } = {}) {
    const imageInput = resolveImageCapabilityState(capabilities, inputModalities);
    const label = imageInput === true
        ? t('settings.model.capability_image_input')
        : imageInput === false
            ? t('settings.model.capability_text_only')
            : t('settings.model.capability_unknown');
    const classes = [
        'profile-card-chip',
        'profile-card-chip-capability',
        imageInput === true
            ? 'profile-card-chip-capability-image'
            : imageInput === false
                ? 'profile-card-chip-capability-text'
                : 'profile-card-chip-capability-unknown',
        compact ? 'profile-card-chip-compact' : '',
    ]
        .filter(Boolean)
        .join(' ');
    return `<span class="${classes}">${escapeHtml(label)}</span>`;
}

function renderDiscoveredModelSummary(modelEntry) {
    const contextWindow = Number(modelEntry?.context_window);
    if (!Number.isInteger(contextWindow) || contextWindow <= 0) {
        return '';
    }
    return `<span class="profile-model-menu-meta">${escapeHtml(formatContextWindowLabel(contextWindow))}</span>`;
}

function formatContextWindowLabel(contextWindow) {
    const value = Number(contextWindow);
    if (!Number.isFinite(value) || value <= 0) {
        return '';
    }
    return formatMessage('settings.model.context_window_compact', {
        count: new Intl.NumberFormat().format(value),
    });
}

function renderFallbackPolicyOptions() {
    const select = document.getElementById('profile-fallback-policy');
    if (!select) {
        return;
    }
    const selectedValue = String(select.value || '').trim();
    const policies = Array.isArray(fallbackConfig?.policies) ? fallbackConfig.policies : [];
    const options = [
        `<option value="">${escapeHtml(t('settings.model.disabled'))}</option>`,
        ...policies.map(policy => (
            `<option value="${escapeHtml(policy.policy_id)}">${escapeHtml(formatFallbackPolicyDisplayName(policy.policy_id, policy.name))}</option>`
        )),
    ];
    select.innerHTML = options.join('');
    if (
        selectedValue === ''
        || policies.some(policy => String(policy?.policy_id || '').trim() === selectedValue)
    ) {
        select.value = selectedValue;
    }
}

function formatFallbackPolicyLabel(policyId) {
    const policies = Array.isArray(fallbackConfig?.policies) ? fallbackConfig.policies : [];
    const matched = policies.find(policy => policy?.policy_id === policyId);
    return formatFallbackPolicyDisplayName(policyId, matched?.name);
}

function formatFallbackPolicyDisplayName(policyId, fallbackName = '') {
    const normalizedPolicyId = String(policyId || '').trim();
    const translationKey = FALLBACK_POLICY_TRANSLATION_KEYS[normalizedPolicyId] || null;
    if (translationKey) {
        const translated = t(translationKey);
        if (translated !== translationKey) {
            return translated;
        }
    }
    const normalizedFallbackName = String(fallbackName || '').trim();
    if (normalizedFallbackName) {
        return normalizedFallbackName;
    }
    return normalizedPolicyId;
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
