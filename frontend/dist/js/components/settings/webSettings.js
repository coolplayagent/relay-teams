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

export function bindWebSettingsHandlers() {
    const saveBtn = document.getElementById('save-web-btn');
    if (saveBtn) {
        saveBtn.onclick = handleSaveWeb;
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
            message: `Failed to load web config: ${e.message}`,
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
            message: `Failed to save web config: ${e.message}`,
            tone: 'danger',
        });
    }
}

function writeWebFormValues(config) {
    setInputValue('web-provider', config.provider);
    setInputValue('web-api-key', config.api_key);
}

function readWebFormValues() {
    return {
        provider: readInputValue('web-provider') || 'exa',
        api_key: readInputValue('web-api-key') || null,
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
