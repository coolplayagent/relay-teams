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
const WEB_PROVIDER_SEARXNG = 'searxng';
const WEB_PROVIDER_DETAILS = {
    [WEB_PROVIDER_EXA]: {
        label: 'Exa',
        website: 'https://exa.ai',
    },
    [WEB_PROVIDER_SEARXNG]: {
        label: 'SearXNG',
        website: 'https://docs.searxng.org/',
    },
};

let webApiKeyState = createWebApiKeyState();

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
    setInputValue('web-provider', config.provider || WEB_PROVIDER_EXA);
    setInputValue('web-fallback-provider', config.fallback_provider);
    setInputValue('web-searxng-instance-url', config.searxng_instance_url);
    webApiKeyState = createWebApiKeyState(config.api_key);
    syncWebFormState();
}

function readWebFormValues() {
    return {
        provider: readInputValue('web-provider') || WEB_PROVIDER_EXA,
        api_key: readWebApiKeyValue(),
        fallback_provider: readInputValue('web-fallback-provider') || null,
        searxng_instance_url: readInputValue('web-searxng-instance-url') || null,
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

function syncWebFormState() {
    const provider = readInputValue('web-provider') || WEB_PROVIDER_EXA;
    const fallbackProvider = readInputValue('web-fallback-provider');
    const apiKeyInput = document.getElementById('web-api-key');
    const isApiKeyEditable = provider === WEB_PROVIDER_EXA;

    if (apiKeyInput) {
        apiKeyInput.disabled = !isApiKeyEditable;
    }
    if (!isApiKeyEditable) {
        webApiKeyState.revealed = false;
    }

    const searxngInstanceInput = document.getElementById('web-searxng-instance-url');
    if (searxngInstanceInput) {
        searxngInstanceInput.disabled = !(
            provider === WEB_PROVIDER_SEARXNG || fallbackProvider === WEB_PROVIDER_SEARXNG
        );
    }

    renderWebProviderSite(provider);
    renderWebApiKeyField();
}

function handleWebApiKeyInput() {
    const apiKeyInput = document.getElementById('web-api-key');
    const nextValue = apiKeyInput ? apiKeyInput.value : '';
    webApiKeyState.draftValue = nextValue;
    webApiKeyState.isDirty = webApiKeyState.hasPersistedValue
        ? nextValue !== webApiKeyState.persistedValue
        : nextValue.trim().length > 0;
    if (!readWebApiKeyValue()) {
        webApiKeyState.revealed = false;
    }
    renderWebApiKeyField();
}

function toggleWebApiKeyVisibility() {
    if (!hasWebApiKeyValue()) {
        return;
    }
    webApiKeyState.revealed = !webApiKeyState.revealed;
    renderWebApiKeyField();
}

function renderWebProviderSite(providerValue) {
    const providerDetails = WEB_PROVIDER_DETAILS[providerValue] || {
        label: providerValue || 'Provider',
        website: '',
    };
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

function readWebApiKeyValue() {
    const apiKeyInput = document.getElementById('web-api-key');
    const inputValue = apiKeyInput ? apiKeyInput.value.trim() : '';
    if (!webApiKeyState.hasPersistedValue) {
        return inputValue || null;
    }
    if (webApiKeyState.isDirty) {
        return inputValue || null;
    }
    return inputValue || webApiKeyState.persistedValue || null;
}

function renderWebApiKeyField() {
    const apiKeyInput = document.getElementById('web-api-key');
    if (!apiKeyInput) {
        return;
    }

    if (webApiKeyState.revealed) {
        apiKeyInput.type = 'text';
        apiKeyInput.value = webApiKeyState.isDirty
            ? webApiKeyState.draftValue
            : webApiKeyState.persistedValue;
        apiKeyInput.placeholder = '';
    } else if (webApiKeyState.hasPersistedValue && !webApiKeyState.isDirty) {
        apiKeyInput.type = 'password';
        apiKeyInput.value = '';
        apiKeyInput.placeholder = MASKED_SECRET_PLACEHOLDER;
    } else {
        apiKeyInput.type = 'password';
        apiKeyInput.value = webApiKeyState.draftValue;
        apiKeyInput.placeholder = t('settings.web.api_key_placeholder');
    }

    renderWebApiKeyToggle();
}

function renderWebApiKeyToggle() {
    const toggleApiKeyBtn = document.getElementById('toggle-web-api-key-btn');
    const apiKeyInput = document.getElementById('web-api-key');
    if (!toggleApiKeyBtn) {
        return;
    }

    const canReveal = apiKeyInput ? apiKeyInput.disabled !== true : true;
    toggleApiKeyBtn.style.display = canReveal && hasWebApiKeyValue() ? 'inline-flex' : 'none';
    toggleApiKeyBtn.disabled = !canReveal;
    toggleApiKeyBtn.className = webApiKeyState.revealed ? 'secure-input-btn is-active' : 'secure-input-btn';
    toggleApiKeyBtn.title = webApiKeyState.revealed
        ? t('settings.model.hide_api_key')
        : t('settings.model.show_api_key');
    if (typeof toggleApiKeyBtn.setAttribute === 'function') {
        toggleApiKeyBtn.setAttribute('aria-label', toggleApiKeyBtn.title);
    } else {
        toggleApiKeyBtn.ariaLabel = toggleApiKeyBtn.title;
    }
}

function hasWebApiKeyValue() {
    const apiKeyInput = document.getElementById('web-api-key');
    const inputValue = apiKeyInput ? apiKeyInput.value.trim() : '';
    if (webApiKeyState.hasPersistedValue && !webApiKeyState.isDirty) {
        return Boolean(webApiKeyState.persistedValue || inputValue);
    }
    return Boolean(webApiKeyState.draftValue.trim() || inputValue);
}
