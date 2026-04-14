/**
 * components/settings/webSettings.js
 * Web provider settings persistence.
 */
import {
    fetchWebConfig,
    saveWebConfig,
} from '../../core/api.js';
import { showToast } from '../../utils/feedback.js';
import { t } from '../../utils/i18n.js';
import { errorToPayload, logError } from '../../utils/logger.js';

const MASKED_SECRET_PLACEHOLDER = '************';
const WEB_PROVIDER_EXA = 'exa';
const WEB_FALLBACK_PROVIDER_SEARXNG = 'searxng';
const WEB_PROVIDER_DETAILS = {
    [WEB_PROVIDER_EXA]: {
        apiKeyLabelKey: 'settings.web.exa_api_key',
        label: 'Exa',
        website: 'https://exa.ai',
    },
};

let webApiKeyStates = createWebApiKeyStates();
let searxngInstanceSeeds = [];
let webLanguageChangeHandlerBound = false;

function formatMessage(key, values = {}) {
    return Object.entries(values).reduce(
        (result, [name, value]) => result.replaceAll(`{${name}}`, String(value)),
        t(key),
    );
}

export function bindWebSettingsHandlers() {
    const saveBtn = document.getElementById('save-web-btn');
    if (saveBtn) {
        saveBtn.onclick = handleSaveWeb;
    }

    const providerInput = document.getElementById('web-provider');
    if (providerInput) {
        providerInput.onchange = syncWebFormState;
    }

    const fallbackProviderInput = document.getElementById('web-fallback-provider');
    if (fallbackProviderInput) {
        fallbackProviderInput.onchange = syncWebFormState;
    }

    const apiKeyInput = document.getElementById('web-api-key');
    if (apiKeyInput) {
        apiKeyInput.oninput = handleWebApiKeyInput;
        apiKeyInput.onchange = handleWebApiKeyInput;
    }

    const toggleApiKeyBtn = document.getElementById('toggle-web-api-key-btn');
    if (toggleApiKeyBtn) {
        toggleApiKeyBtn.onclick = toggleWebApiKeyVisibility;
    }

    if (!webLanguageChangeHandlerBound && typeof document?.addEventListener === 'function') {
        document.addEventListener('agent-teams-language-changed', () => {
            if (typeof queueMicrotask === 'function') {
                queueMicrotask(syncWebFormState);
                return;
            }
            setTimeout(syncWebFormState, 0);
        });
        webLanguageChangeHandlerBound = true;
    }
}

export async function loadWebSettingsPanel() {
    try {
        const config = await fetchWebConfig();
        writeWebFormValues(config);
    } catch (e) {
        logError(
            'frontend.web_settings.load_failed',
            'Failed to load web config',
            errorToPayload(e),
        );
        showToast({
            title: t('settings.web.load_failed'),
            message: formatMessage('settings.web.load_failed_detail', { error: e.message }),
            tone: 'danger',
        });
    }
}

async function handleSaveWeb() {
    try {
        await saveWebConfig(readWebFormValues());
        showToast({
            title: t('settings.web.saved'),
            message: t('settings.web.saved_message'),
            tone: 'success',
        });
        await loadWebSettingsPanel();
    } catch (e) {
        showToast({
            title: t('settings.web.save_failed'),
            message: formatMessage('settings.web.save_failed_detail', { error: e.message }),
            tone: 'danger',
        });
    }
}

function writeWebFormValues(config) {
    searxngInstanceSeeds = resolveSearxngInstanceSeeds(config);
    setInputValue('web-provider', WEB_PROVIDER_EXA);
    setInputValue(
        'web-fallback-provider',
        config.fallback_provider || WEB_FALLBACK_PROVIDER_SEARXNG,
    );
    setInputValue(
        'web-searxng-instance-url',
        config.searxng_instance_url || getDefaultSearxngInstanceUrl(config),
    );
    webApiKeyStates = createWebApiKeyStates(config);
    syncWebFormState();
}

function readWebFormValues() {
    return {
        provider: WEB_PROVIDER_EXA,
        exa_api_key: readWebApiKeyValueForProvider(WEB_PROVIDER_EXA),
        fallback_provider: (
            readInputValue('web-fallback-provider') || WEB_FALLBACK_PROVIDER_SEARXNG
        ),
        searxng_instance_url: (
            readInputValue('web-searxng-instance-url') || getDefaultSearxngInstanceUrl()
        ),
    };
}

function setInputValue(id, value) {
    const input = document.getElementById(id);
    if (!input) {
        return;
    }
    input.value = value || '';
}

function readInputValue(id) {
    const input = document.getElementById(id);
    if (!input) {
        return '';
    }
    return input.value.trim();
}

function createWebApiKeyStates(config = {}) {
    return {
        [WEB_PROVIDER_EXA]: createWebApiKeyState(config.exa_api_key),
    };
}

function createWebApiKeyState(persistedValue = null) {
    const normalizedValue = typeof persistedValue === 'string' ? persistedValue : '';
    return {
        persistedValue: normalizedValue,
        draftValue: '',
        hasPersistedValue: Boolean(normalizedValue.trim()),
        isDirty: false,
        revealed: false,
    };
}

function getSelectedProvider() {
    return WEB_PROVIDER_EXA;
}

function getWebApiKeyState(provider = getSelectedProvider()) {
    return webApiKeyStates[provider] || createWebApiKeyState();
}

function syncWebFormState() {
    const fallbackProvider = (
        readInputValue('web-fallback-provider') || WEB_FALLBACK_PROVIDER_SEARXNG
    );
    const searxngField = document.getElementById('web-searxng-instance-url-field');
    const searxngBuiltinsField = document.getElementById('web-searxng-builtins-field');
    const showsSearxngField = fallbackProvider === WEB_FALLBACK_PROVIDER_SEARXNG;

    const searxngInstanceInput = document.getElementById('web-searxng-instance-url');
    if (searxngField) {
        searxngField.style.display = showsSearxngField ? 'grid' : 'none';
    }
    if (searxngBuiltinsField) {
        searxngBuiltinsField.style.display = showsSearxngField ? 'grid' : 'none';
    }
    if (searxngInstanceInput) {
        searxngInstanceInput.disabled = !showsSearxngField;
        if (!searxngInstanceInput.value.trim()) {
            searxngInstanceInput.value = getDefaultSearxngInstanceUrl();
        }
    }
    renderSearxngInstancePlaceholder();
    renderBuiltinSearxngInstances();

    renderWebApiKeyLabel();
    renderWebProviderSite();
    renderWebApiKeyField();
}

function renderBuiltinSearxngInstances() {
    const builtinsList = document.getElementById('web-searxng-builtins-list');
    if (!builtinsList) {
        return;
    }
    builtinsList.innerHTML = searxngInstanceSeeds.map(
        (instanceUrl) => (
            `<div class="trigger-readonly-value trigger-readonly-value-mono">${instanceUrl}</div>`
        ),
    ).join('');
}

function renderSearxngInstancePlaceholder() {
    const searxngInstanceInput = document.getElementById('web-searxng-instance-url');
    if (!searxngInstanceInput) {
        return;
    }
    searxngInstanceInput.placeholder = formatMessage(
        'settings.web.searxng_instance_url_placeholder',
        { default: getDefaultSearxngInstanceUrl() },
    );
}

function getDefaultSearxngInstanceUrl(config = null) {
    const configuredInstanceUrl = (
        typeof config?.searxng_instance_url === 'string'
            ? config.searxng_instance_url.trim()
            : ''
    );
    return searxngInstanceSeeds[0] || configuredInstanceUrl || '';
}

function resolveSearxngInstanceSeeds(config = {}) {
    const configuredSeeds = Array.isArray(config.searxng_instance_seeds)
        ? config.searxng_instance_seeds
        : [];
    const combinedSeeds = [...configuredSeeds, ...searxngInstanceSeeds];
    const normalizedSeeds = [];
    const seenSeeds = new Set();
    combinedSeeds.forEach((candidate) => {
        if (typeof candidate !== 'string') {
            return;
        }
        const normalizedCandidate = candidate.trim();
        if (!normalizedCandidate || seenSeeds.has(normalizedCandidate)) {
            return;
        }
        normalizedSeeds.push(normalizedCandidate);
        seenSeeds.add(normalizedCandidate);
    });
    return normalizedSeeds;
}

function renderWebApiKeyLabel() {
    const apiKeyLabel = document.getElementById('web-api-key-label');
    if (!apiKeyLabel) {
        return;
    }
    const providerDetails = WEB_PROVIDER_DETAILS[WEB_PROVIDER_EXA];
    if (typeof apiKeyLabel.setAttribute === 'function') {
        apiKeyLabel.setAttribute('data-i18n', providerDetails.apiKeyLabelKey);
    } else {
        apiKeyLabel.dataset = apiKeyLabel.dataset || {};
        apiKeyLabel.dataset.i18n = providerDetails.apiKeyLabelKey;
    }
    apiKeyLabel.textContent = t(providerDetails.apiKeyLabelKey);
}

function handleWebApiKeyInput() {
    const apiKeyInput = document.getElementById('web-api-key');
    const nextValue = apiKeyInput ? apiKeyInput.value : '';
    const provider = getSelectedProvider();
    const nextState = getWebApiKeyState(provider);
    if (
        nextState.hasPersistedValue
        && !nextState.revealed
        && !isWebApiKeyInputActive(apiKeyInput)
    ) {
        nextState.draftValue = '';
        nextState.isDirty = false;
        nextState.revealed = false;
        webApiKeyStates[provider] = nextState;
        renderWebApiKeyField();
        return;
    }
    nextState.draftValue = nextValue;
    nextState.isDirty = nextState.hasPersistedValue
        ? nextValue !== nextState.persistedValue
        : nextValue.trim().length > 0;
    if (!readWebApiKeyValueForProvider(provider)) {
        nextState.revealed = false;
    }
    webApiKeyStates[provider] = nextState;
    renderWebApiKeyField();
}

function toggleWebApiKeyVisibility() {
    const provider = getSelectedProvider();
    if (!hasWebApiKeyValue(provider)) {
        return;
    }
    const nextState = getWebApiKeyState(provider);
    nextState.revealed = !nextState.revealed;
    webApiKeyStates[provider] = nextState;
    renderWebApiKeyField();
}

function renderWebProviderSite() {
    const providerDetails = WEB_PROVIDER_DETAILS[WEB_PROVIDER_EXA];
    const siteLink = document.getElementById('web-provider-site-link');
    const siteBadge = document.getElementById('web-provider-site-badge');
    const siteUrl = document.getElementById('web-provider-site-url');

    if (siteLink) {
        siteLink.href = providerDetails.website;
        siteLink.title = providerDetails.website;
        if (typeof siteLink.setAttribute === 'function') {
            siteLink.setAttribute('aria-label', providerDetails.website);
        } else {
            siteLink.ariaLabel = providerDetails.website;
        }
    }

    if (siteBadge) {
        siteBadge.textContent = providerDetails.label;
    }

    if (siteUrl) {
        siteUrl.textContent = providerDetails.website;
    }
}

function readWebApiKeyValueForProvider(provider) {
    const state = getWebApiKeyState(provider);
    const apiKeyInput = document.getElementById('web-api-key');
    const inputValue = (
        apiKeyInput && getSelectedProvider() === provider
            ? apiKeyInput.value.trim()
            : state.draftValue.trim()
    );

    if (!state.hasPersistedValue) {
        return inputValue || null;
    }
    if (state.isDirty) {
        return inputValue || null;
    }
    return state.persistedValue || null;
}

function renderWebApiKeyField() {
    const apiKeyInput = document.getElementById('web-api-key');
    if (!apiKeyInput) {
        return;
    }

    const state = getWebApiKeyState();
    if (state.revealed) {
        apiKeyInput.type = 'text';
        apiKeyInput.value = state.isDirty ? state.draftValue : state.persistedValue;
        apiKeyInput.placeholder = '';
    } else if (state.hasPersistedValue && !state.isDirty) {
        apiKeyInput.type = 'password';
        apiKeyInput.value = '';
        apiKeyInput.placeholder = MASKED_SECRET_PLACEHOLDER;
    } else {
        apiKeyInput.type = 'password';
        apiKeyInput.value = state.draftValue;
        apiKeyInput.placeholder = t('settings.web.api_key_placeholder');
    }

    renderWebApiKeyToggle();
}

function renderWebApiKeyToggle() {
    const toggleApiKeyBtn = document.getElementById('toggle-web-api-key-btn');
    if (!toggleApiKeyBtn) {
        return;
    }

    const state = getWebApiKeyState();
    toggleApiKeyBtn.style.display = 'inline-flex';
    toggleApiKeyBtn.disabled = false;
    toggleApiKeyBtn.className = state.revealed ? 'secure-input-btn is-active' : 'secure-input-btn';
    toggleApiKeyBtn.title = state.revealed
        ? t('settings.model.hide_api_key')
        : t('settings.model.show_api_key');
    if (typeof toggleApiKeyBtn.setAttribute === 'function') {
        toggleApiKeyBtn.setAttribute('aria-label', toggleApiKeyBtn.title);
    } else {
        toggleApiKeyBtn.ariaLabel = toggleApiKeyBtn.title;
    }
}

function hasWebApiKeyValue(provider = getSelectedProvider()) {
    const state = getWebApiKeyState(provider);
    const apiKeyInput = document.getElementById('web-api-key');
    const inputValue = (
        apiKeyInput && getSelectedProvider() === provider
            ? apiKeyInput.value.trim()
            : state.draftValue.trim()
    );
    if (state.hasPersistedValue && !state.isDirty) {
        return Boolean(state.persistedValue);
    }
    return Boolean(state.draftValue.trim() || inputValue);
}

function isWebApiKeyInputActive(apiKeyInput) {
    if (!apiKeyInput || typeof document !== 'object' || document === null) {
        return false;
    }
    if (!('activeElement' in document)) {
        return true;
    }
    return document.activeElement === apiKeyInput;
}
