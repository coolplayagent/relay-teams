/**
 * components/messageRenderer/messageActions.js
 * Message-level actions for the main chat transcript.
 */
import { showToast } from '../../utils/feedback.js';
import { t } from '../../utils/i18n.js';
import {
    bindReadAloudButton,
    supportsMessageSpeech,
} from '../messageSpeech.js';

const COPY_BUTTON_CLASS = 'message-copy-btn';
const READ_BUTTON_CLASS = 'message-read-btn';
const ACTIONS_CLASS = 'message-copy-actions';
const COPY_ICON = `
    <svg class="message-copy-icon" viewBox="0 0 24 24" focusable="false" aria-hidden="true">
        <rect x="9" y="9" width="13" height="13" rx="2"></rect>
        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
    </svg>
`;
const COPIED_ICON = `
    <svg class="message-copy-icon" viewBox="0 0 24 24" focusable="false" aria-hidden="true">
        <path d="M20 6 9 17l-5-5"></path>
    </svg>
`;

export function syncLastAnswerCopyButton(target) {
    const root = resolveTranscriptRoot(target);
    if (!root) return;

    removeStaleCopyActions(root);
    const message = findLatestAnswerMessage(root);
    if (!message) return;

    const copyText = extractMessageCopyText(message);
    if (!copyText) return;

    ensureCopyActions(message, copyText);
}

if (typeof globalThis !== 'undefined') {
    globalThis.__relayTeamsSyncLastAnswerCopyButton = syncLastAnswerCopyButton;
}

export function extractMessageCopyText(message) {
    const contentEl = message?.querySelector?.('.msg-content');
    if (!contentEl) return '';
    const clone = contentEl.cloneNode(true);
    clone.querySelectorAll?.([
        '.thinking-block',
        '.tool-block',
        '.tool-group',
        '.message-copy-actions',
        '.markdown-code-header',
        '.streaming-cursor',
        '.msg-image',
        '.msg-media',
    ].join(',')).forEach(node => node.remove());
    return normalizeCopiedText(collectCopyText(clone));
}

export function bindCopyButton(button, copyText) {
    if (!button) return null;
    button.type = 'button';
    button.classList.add(COPY_BUTTON_CLASS);
    button.innerHTML = COPY_ICON;
    button.__messageCopyText = copyText;
    button.setAttribute('aria-label', t('message.copy'));
    button.setAttribute('title', t('message.copy'));
    if (button.__messageCopyBound !== true) {
        button.addEventListener('click', handleCopyClick);
        button.__messageCopyBound = true;
    }
    return button;
}

function resolveTranscriptRoot(target) {
    if (!target) {
        return typeof document !== 'undefined'
            ? document.getElementById('chat-messages')
            : null;
    }
    if (target.id === 'chat-messages') return target;
    return target.closest?.('#chat-messages') || null;
}

function removeStaleCopyActions(root) {
    const latestMessage = findLatestAnswerMessage(root);
    root.querySelectorAll?.(`.${ACTIONS_CLASS}`).forEach(actions => {
        const message = actions.closest?.('.message');
        if (!message || message !== latestMessage) {
            actions.remove();
        }
    });
}

function findLatestAnswerMessage(root) {
    const messages = Array.from(root.querySelectorAll?.('.message') || []);
    for (let index = messages.length - 1; index >= 0; index -= 1) {
        const message = messages[index];
        if (!isEligibleAnswerMessage(message)) continue;
        if (isUnstableAnswerMessage(message)) {
            return null;
        }
        if (extractMessageCopyText(message)) {
            return message;
        }
        return null;
    }
    return null;
}

function isEligibleAnswerMessage(message) {
    if (!message || message.hidden || message.closest?.('[hidden]')) {
        return false;
    }
    const role = String(message.dataset?.role || '').trim().toLowerCase();
    if (role === 'user') return false;
    if (!message.querySelector?.('.msg-content')) return false;
    if (message.closest?.('.tool-group')) return false;
    return true;
}

function isUnstableAnswerMessage(message) {
    if (!message) return false;
    if (message.querySelector?.('.streaming-cursor')) return true;
    if (message.querySelector?.('.msg-text[data-idle-cursor="true"]')) return true;
    const liveThinking = Array.from(message.querySelectorAll?.('.thinking-block') || [])
        .some(block => String(block.dataset?.streaming || '') === 'true');
    return liveThinking;
}

function ensureCopyActions(message, copyText) {
    let actions = message.querySelector?.(`:scope > .${ACTIONS_CLASS}`) || null;
    if (!actions) {
        actions = document.createElement('div');
        actions.className = ACTIONS_CLASS;
        message.appendChild(actions);
    }

    let button = actions.querySelector?.(`.${COPY_BUTTON_CLASS}`) || null;
    if (!button) {
        button = document.createElement('button');
        actions.appendChild(button);
    }
    bindCopyButton(button, copyText);
    ensureReadButton(actions, copyText);
}

function ensureReadButton(actions, copyText) {
    if (!supportsMessageSpeech()) return;
    let button = actions.querySelector?.(`.${READ_BUTTON_CLASS}`) || null;
    if (!button) {
        button = document.createElement('button');
        actions.appendChild(button);
    }
    bindReadAloudButton(button, copyText);
}

async function handleCopyClick(event) {
    event.preventDefault();
    event.stopPropagation();
    const button = event.currentTarget;
    const copyText = String(button?.__messageCopyText || '').trim();
    if (!copyText) {
        showToast({
            title: t('message.copy_failed_title'),
            message: t('message.copy_empty_message'),
            tone: 'warning',
        });
        return;
    }

    try {
        if (!globalThis.navigator?.clipboard?.writeText) {
            throw new Error('clipboard unavailable');
        }
        await globalThis.navigator.clipboard.writeText(copyText);
        indicateCopied(button);
        showToast({
            title: t('message.copy_success_title'),
            message: t('message.copy_success_message'),
            tone: 'success',
            durationMs: 1600,
        });
    } catch (error) {
        showToast({
            title: t('message.copy_failed_title'),
            message: t('message.copy_unavailable_message'),
            tone: 'danger',
        });
    }
}

function indicateCopied(button) {
    if (!button) return;
    button.innerHTML = COPIED_ICON;
    button.classList.add('is-copied');
    button.setAttribute('title', t('message.copied'));
    window.setTimeout(() => {
        button.innerHTML = COPY_ICON;
        button.classList.remove('is-copied');
        button.setAttribute('title', t('message.copy'));
    }, 1400);
}

function collectCopyText(node) {
    if (!node) return '';
    if (node.nodeType === 3) {
        return node.textContent || '';
    }
    if (node.nodeType !== 1 && node.nodeType !== 11) {
        return '';
    }
    const tagName = String(node.tagName || '').toLowerCase();
    if (tagName === 'br') return '\n';
    const childrenText = Array.from(node.childNodes || [])
        .map(child => collectCopyText(child))
        .join('');
    if (isBlockTextElement(tagName)) {
        return `\n${childrenText}\n`;
    }
    return childrenText;
}

function isBlockTextElement(tagName) {
    return [
        'blockquote',
        'div',
        'figcaption',
        'h1',
        'h2',
        'h3',
        'h4',
        'li',
        'ol',
        'p',
        'pre',
        'table',
        'tbody',
        'td',
        'th',
        'thead',
        'tr',
        'ul',
    ].includes(tagName);
}

function normalizeCopiedText(text) {
    return String(text || '')
        .replace(/\r\n?/g, '\n')
        .replace(/[ \t]+\n/g, '\n')
        .replace(/\n{3,}/g, '\n\n')
        .replace(/^(?:[ \t]*\n)+/g, '')
        .replace(/(?:\n[ \t]*)+$/g, '');
}
