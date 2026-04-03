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

const WEB_PROVIDER_EXA = 'exa';
const WEB_PROVIDER_SEARXNG = 'searxng';
const WEB_PROVIDER_SITES = {
    [WEB_PROVIDER_EXA]: 'https://exa.ai',
    [WEB_PROVIDER_SEARXNG]: 'https://docs.searxng.org/',
};

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
    setInputValue('web-provider', config.provider);
    setInputValue('web-api-key', config.api_key);
    setInputValue('web-fallback-provider', config.fallback_provider);
    setInputValue('web-searxng-instance-url', config.searxng_instance_url);
    syncWebFormState();
}

function readWebFormValues() {
    return {
        provider: readInputValue('web-provider') || WEB_PROVIDER_EXA,
        api_key: readInputValue('web-api-key') || null,
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

function syncWebFormState() {
    const provider = readInputValue('web-provider') || WEB_PROVIDER_EXA;
    const fallbackProvider = readInputValue('web-fallback-provider');
    const apiKeyInput = document.getElementById('web-api-key');
    if (apiKeyInput) {
        apiKeyInput.disabled = provider !== WEB_PROVIDER_EXA;
    }
    const searxngInstanceInput = document.getElementById('web-searxng-instance-url');
    if (searxngInstanceInput) {
        searxngInstanceInput.disabled = !(
            provider === WEB_PROVIDER_SEARXNG || fallbackProvider === WEB_PROVIDER_SEARXNG
        );
    }
    const providerSiteLink = document.getElementById('web-provider-site-link');
    if (providerSiteLink) {
        const site = WEB_PROVIDER_SITES[provider] || WEB_PROVIDER_SITES[WEB_PROVIDER_EXA];
        providerSiteLink.href = site;
        providerSiteLink.textContent = site;
    }
}
