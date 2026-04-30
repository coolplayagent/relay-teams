/**
 * components/messageSpeech.js
 * Browser-native read-aloud support for answer messages.
 */
import { showToast } from '../utils/feedback.js';
import { t } from '../utils/i18n.js';

const READ_BUTTON_CLASS = 'message-read-btn';
const READ_ICON = `
    <svg class="message-copy-icon" viewBox="0 0 24 24" focusable="false" aria-hidden="true">
        <path d="M4 9v6h4l5 4V5L8 9H4Z"></path>
        <path d="M16 9.5a4 4 0 0 1 0 5"></path>
        <path d="M18.5 7a7 7 0 0 1 0 10"></path>
    </svg>
`;
const STOP_ICON = `
    <svg class="message-copy-icon" viewBox="0 0 24 24" focusable="false" aria-hidden="true">
        <rect x="7" y="7" width="10" height="10" rx="2"></rect>
    </svg>
`;

let activeButton = null;

export function supportsMessageSpeech() {
    return Boolean(globalThis.speechSynthesis && globalThis.SpeechSynthesisUtterance);
}

export function bindReadAloudButton(button, text) {
    if (!button) return null;
    button.type = 'button';
    button.classList.add(READ_BUTTON_CLASS);
    button.innerHTML = activeButton === button ? STOP_ICON : READ_ICON;
    button.__messageSpeechText = text;
    button.setAttribute('aria-label', t('message.read_aloud'));
    button.setAttribute('title', t('message.read_aloud'));
    if (button.__messageSpeechBound !== true) {
        button.addEventListener('click', handleReadClick);
        button.__messageSpeechBound = true;
    }
    return button;
}

function handleReadClick(event) {
    event.preventDefault();
    event.stopPropagation();
    const button = event.currentTarget;
    const text = String(button?.__messageSpeechText || '').trim();
    if (!text) return;
    if (!supportsMessageSpeech()) {
        showToast({
            title: t('message.read_unavailable_title'),
            message: t('message.read_unavailable_message'),
            tone: 'warning',
        });
        return;
    }
    if (activeButton === button) {
        stopReading();
        return;
    }
    stopReading();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = document.documentElement.lang || navigator.language || '';
    utterance.onend = () => clearActiveButton(button);
    utterance.onerror = () => clearActiveButton(button);
    activeButton = button;
    button.classList.add('is-reading');
    button.innerHTML = STOP_ICON;
    button.setAttribute('title', t('message.stop_reading'));
    globalThis.speechSynthesis.speak(utterance);
}

function stopReading() {
    if (supportsMessageSpeech()) {
        globalThis.speechSynthesis.cancel();
    }
    clearActiveButton(activeButton);
}

function clearActiveButton(button) {
    if (!button) return;
    button.classList.remove('is-reading');
    button.innerHTML = READ_ICON;
    button.setAttribute('title', t('message.read_aloud'));
    if (activeButton === button) {
        activeButton = null;
    }
}
