/**
 * components/settings/speechSettings.js
 * Speech interaction settings.
 */
import {
    fetchModelProfiles,
    fetchSpeechConfig,
    saveSpeechConfig,
} from '../../core/api.js';
import { showToast } from '../../utils/feedback.js';
import { formatMessage, t } from '../../utils/i18n.js';

let speechConfig = null;
let speechConfigLoaded = false;
let modelProfiles = {};
let modelProfilesLoaded = false;
const SPEECH_LANGUAGE_OPTIONS = Object.freeze([
    ['', 'Auto'],
    ['zh-CN', '中文（简体）'],
    ['zh-TW', '中文（繁體）'],
    ['en-US', 'English (US)'],
    ['en-GB', 'English (UK)'],
    ['ja-JP', '日本語'],
    ['ko-KR', '한국어'],
    ['fr-FR', 'Français'],
    ['de-DE', 'Deutsch'],
    ['es-ES', 'Español'],
]);

export function bindSpeechSettingsHandlers() {
    const saveBtn = document.getElementById('save-speech-btn');
    if (saveBtn) {
        saveBtn.onclick = handleSaveSpeechConfig;
    }
}

export async function loadSpeechSettingsPanel() {
    const [configResult, profilesResult] = await Promise.allSettled([
        fetchSpeechConfig(),
        fetchModelProfiles(),
    ]);
    const errors = [];
    if (configResult.status === 'fulfilled') {
        speechConfig = configResult.value || {};
        speechConfigLoaded = true;
    } else {
        speechConfig = speechConfig || {};
        speechConfigLoaded = false;
        errors.push(configResult.reason);
    }
    if (profilesResult.status === 'fulfilled') {
        modelProfiles = profilesResult.value || {};
        modelProfilesLoaded = true;
    } else {
        modelProfilesLoaded = false;
        errors.push(profilesResult.reason);
    }
    if (errors.length > 0) {
        showToast({
            title: t('settings.speech.load_failed'),
            message: formatMessage('settings.speech.load_failed_detail', {
                error: errors[0]?.message || t('settings.speech.load_failed'),
            }),
            tone: 'danger',
        });
    }
    renderSpeechSettingsPanel();
}

export function canSaveSpeechConfig() {
    return speechConfigLoaded && modelProfilesLoaded;
}

export function renderSpeechSettingsPanelMarkup() {
    return `
        <div class="settings-panel" id="speech-panel" style="display:none;">
            <div class="settings-section">
                <div class="settings-content-stack">
                    ${renderSpeechSettingsSectionMarkup()}
                </div>
            </div>
        </div>
    `;
}

export function renderSpeechSettingsSectionMarkup() {
    return `
        <section class="proxy-form-section general-setting-card">
            <div class="proxy-form-section-header general-setting-card-head general-setting-card-head-compact">
                <div class="general-setting-card-copy-block">
                    <h5 data-i18n="settings.speech.stt">Speech to Text</h5>
                </div>
            </div>
            <div class="appearance-grid general-setting-card-body">
                <div class="appearance-row">
                    <label for="speech-stt-profile" data-i18n="settings.speech.stt_profile">STT Profile</label>
                    <select id="speech-stt-profile" class="appearance-text-input">
                        <option value="" data-i18n="settings.speech.no_profile">No profile selected</option>
                    </select>
                </div>
                <div class="appearance-row">
                    <label for="speech-language" data-i18n="settings.speech.language">Language</label>
                    <select id="speech-language" class="appearance-text-input"></select>
                </div>
                <div class="appearance-row">
                    <label for="speech-prompt" data-i18n="settings.speech.prompt">Prompt</label>
                    <textarea id="speech-prompt" class="appearance-text-input" rows="3"></textarea>
                </div>
            </div>
        </section>
    `;
}

async function handleSaveSpeechConfig() {
    const payload = readSpeechForm();
    try {
        speechConfig = await saveSpeechConfig(payload);
        document.dispatchEvent(new CustomEvent('agent-teams-speech-config-updated'));
        showToast({
            title: t('settings.speech.saved'),
            message: t('settings.speech.saved_message'),
            tone: 'success',
        });
        renderSpeechSettingsPanel();
    } catch (error) {
        showToast({
            title: t('settings.speech.save_failed'),
            message: error?.message || t('settings.speech.save_failed'),
            tone: 'danger',
        });
    }
}

function renderSpeechSettingsPanel() {
    const profileSelect = document.getElementById('speech-stt-profile');
    const languageSelect = document.getElementById('speech-language');
    const promptInput = document.getElementById('speech-prompt');
    if (!profileSelect || !languageSelect || !promptInput) {
        return;
    }
    const selected = String(speechConfig?.stt_profile_name || '');
    profileSelect.innerHTML = [
        `<option value="">${escapeHtml(t('settings.speech.no_profile'))}</option>`,
        ...buildProfileOptions(selected),
    ].join('');
    languageSelect.innerHTML = buildLanguageOptions(String(speechConfig?.language || ''));
    promptInput.value = String(speechConfig?.prompt || '');
}

function buildProfileOptions(selected) {
    let hasSelectedOption = !selected;
    const options = Object.entries(modelProfiles)
        .filter(([, profile]) => isSpeechProfileCandidate(profile))
        .map(([name, profile]) => {
            const model = String(profile?.model || '');
            const active = name === selected ? ' selected' : '';
            if (name === selected) {
                hasSelectedOption = true;
            }
            return `<option value="${escapeHtml(name)}"${active}>${escapeHtml(name)} (${escapeHtml(model)})</option>`;
        });
    if (!hasSelectedOption) {
        options.unshift(
            `<option value="${escapeHtml(selected)}" selected>${escapeHtml(selected)}</option>`,
        );
    }
    return options;
}

function isSpeechProfileCandidate(profile) {
    const provider = String(profile?.provider || '').trim();
    const model = String(profile?.model || '').trim();
    if (provider !== 'openai_compatible') return false;
    if (resolveRealtimeSpeechModel(profile) === 'gpt-4o-transcribe-diarize') return false;
    if (hasSpeechRealtimeModelOverride(profile)) return true;
    return isKnownRealtimeSttModel(model)
        || resolveSpeechCapability(profile) === 'stt';
}

function hasSpeechRealtimeModelOverride(profile) {
    return normalizeOptionalValue(profile?.speech_realtime?.model) !== null;
}

function resolveRealtimeSpeechModel(profile) {
    return normalizeOptionalValue(profile?.speech_realtime?.model)
        || String(profile?.model || '').trim();
}

function isKnownRealtimeSttModel(model) {
    return model === 'whisper-1'
        || model === 'gpt-4o-transcribe'
        || model === 'gpt-4o-transcribe-latest'
        || model === 'gpt-4o-mini-transcribe'
        || model.startsWith('gpt-4o-mini-transcribe-');
}

function buildLanguageOptions(selected) {
    const hasSelectedOption = SPEECH_LANGUAGE_OPTIONS.some(([value]) => value === selected);
    const options = SPEECH_LANGUAGE_OPTIONS
        .map(([value, label]) => {
            const active = value === selected ? ' selected' : '';
            return `<option value="${escapeHtml(value)}"${active}>${escapeHtml(label)}</option>`;
        });
    if (selected && !hasSelectedOption) {
        options.push(
            `<option value="${escapeHtml(selected)}" selected>${escapeHtml(selected)}</option>`,
        );
    }
    return options.join('');
}

function resolveSpeechCapability(profile) {
    const capabilities = profile?.resolved_capabilities || profile?.capabilities || {};
    const inputAudio = normalizeOptionalBoolean(capabilities?.input?.audio);
    const outputAudio = normalizeOptionalBoolean(capabilities?.output?.audio);
    if (inputAudio === true) return 'stt';
    if (outputAudio === true) return 'tts';
    if (inputAudio === false && outputAudio === false) return 'none';
    return 'unknown';
}

function normalizeOptionalBoolean(value) {
    if (value === true) return true;
    if (value === false) return false;
    return null;
}

export function readSpeechForm() {
    return {
        stt_profile_name: normalizeOptionalValue(document.getElementById('speech-stt-profile')?.value),
        language: normalizeOptionalValue(document.getElementById('speech-language')?.value),
        prompt: normalizeOptionalValue(document.getElementById('speech-prompt')?.value),
        vad_threshold: speechConfig?.vad_threshold,
        vad_prefix_padding_ms: speechConfig?.vad_prefix_padding_ms,
        vad_silence_duration_ms: speechConfig?.vad_silence_duration_ms,
        noise_reduction: speechConfig?.noise_reduction,
    };
}

function normalizeOptionalValue(value) {
    const normalized = String(value || '').trim();
    return normalized || null;
}

function escapeHtml(value) {
    return String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}
