/**
 * components/runtimeInjectQueue.js
 * Compact runtime inject queue rendered above the main composer.
 */
import { state } from '../core/state.js';
import { els } from '../utils/dom.js';
import { t } from '../utils/i18n.js';

const injectionMessagesByRun = new Map();
const VISIBLE_INJECT_LIMIT = 4;

export function upsertRuntimeInjectMessage(runId, rawMessage, options = {}) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId || !rawMessage || typeof rawMessage !== 'object') return;
    const normalized = normalizeInjectionMessage(safeRunId, rawMessage);
    const messages = injectionMessagesByRun.get(safeRunId) || [];
    const index = findInjectionMessageIndex(messages, normalized);
    const nextMessages = index === -1
        ? [...messages, normalized]
        : messages.map((item, itemIndex) => (
            itemIndex === index ? mergeInjectionMessage(item, normalized) : item
        ));
    injectionMessagesByRun.set(safeRunId, nextMessages.sort(compareInjectionMessages));
    if (options.render !== false) {
        renderRuntimeInjectQueue();
    }
}

export function renderRuntimeInjectQueue(runId = state.activeRunId) {
    const host = els.runtimeInjectQueue;
    if (!host) return;
    bindRuntimeInjectQueue(host);
    const safeRunId = String(runId || '').trim();
    const activeRunId = String(state.activeRunId || '').trim();
    const messages = safeRunId && safeRunId === activeRunId
        ? (injectionMessagesByRun.get(safeRunId) || [])
        : [];
    const displayMessages = mergeQueuedUserMessagesForDisplay(messages);
    const visibleMessages = displayMessages.slice(-VISIBLE_INJECT_LIMIT);
    host.innerHTML = '';
    host.hidden = visibleMessages.length === 0;
    host.classList.toggle('has-overflow', messages.length > VISIBLE_INJECT_LIMIT);
    if (visibleMessages.length === 0) return;

    const actionIndex = lastForceableMessageIndex(visibleMessages);
    visibleMessages.forEach((message, index) => {
        const item = document.createElement('div');
        item.className = [
            'runtime-inject-item',
            `is-${cssToken(message.mode)}`,
            `is-${cssToken(message.status)}`,
        ].join(' ');
        item.dataset.injectionMessageId = message.message_id;
        item.title = `${statusLabel(message.status)} · ${message.content}`;
        item.innerHTML = `
            <span class="runtime-inject-icon" aria-hidden="true">${iconForInject(message)}</span>
            <span class="runtime-inject-text">${escapeHtml(message.content)}</span>
            <span class="runtime-inject-state">${escapeHtml(statusLabel(message.status))}</span>
            ${index === actionIndex ? `<button
                class="runtime-inject-flush"
                type="button"
                data-inject-force="true"
                title="${escapeHtml(t('inject.queue.action.stop_insert'))}"
                aria-label="${escapeHtml(t('inject.queue.action.stop_insert'))}"
            >${flushIcon()}</button>` : ''}
        `;
        host.appendChild(item);
    });
}

function lastForceableMessageIndex(messages) {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
        const status = String(messages[index]?.status || 'queued');
        if (status === 'queued' || status === 'sending' || status === 'interrupting') {
            return index;
        }
    }
    return -1;
}

function mergeQueuedUserMessagesForDisplay(messages) {
    const items = Array.isArray(messages) ? messages : [];
    const merged = [];
    let pendingGroup = [];
    const flushPendingGroup = () => {
        if (pendingGroup.length === 0) return;
        if (pendingGroup.length === 1) {
            merged.push(pendingGroup[0]);
            pendingGroup = [];
            return;
        }
        const status = pendingGroup.some(item => item.status === 'interrupting')
            ? 'interrupting'
            : pendingGroup.some(item => item.status === 'sending')
                ? 'sending'
                : 'queued';
        merged.push({
            ...pendingGroup[pendingGroup.length - 1],
            message_id: pendingGroup.map(item => item.message_id).join('|'),
            injection_id: pendingGroup.map(item => item.injection_id).join('|'),
            mode: pendingGroup.some(item => item.mode === 'interrupt') ? 'interrupt' : 'queued',
            status,
            content: pendingGroup
                .map(item => String(item.content || '').trim())
                .filter(Boolean)
                .join('\n\n'),
            queued_at: pendingGroup[0].queued_at,
            occurred_at: pendingGroup[0].occurred_at,
        });
        pendingGroup = [];
    };
    items.forEach(item => {
        const status = String(item?.status || 'queued');
        const isPendingUser = String(item?.source || 'user') === 'user'
            && ['queued', 'sending', 'interrupting'].includes(status);
        if (isPendingUser) {
            pendingGroup.push(item);
            return;
        }
        flushPendingGroup();
        merged.push(item);
    });
    flushPendingGroup();
    return merged;
}

export function clearRuntimeInjectMessages(runId) {
    const safeRunId = String(runId || '').trim();
    if (safeRunId) {
        injectionMessagesByRun.delete(safeRunId);
    }
    renderRuntimeInjectQueue();
}

export function removeRuntimeInjectMessage(runId, rawMessage, options = {}) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId || !rawMessage || typeof rawMessage !== 'object') return;
    const normalized = normalizeInjectionMessage(safeRunId, rawMessage);
    const messages = injectionMessagesByRun.get(safeRunId) || [];
    const appliedIds = normalized.applied_injection_ids;
    if (appliedIds.length > 0) {
        const appliedIdSet = new Set(appliedIds);
        const nextMessages = messages.filter(item => (
            !appliedIdSet.has(String(item.injection_id || '').trim())
            && !appliedIdSet.has(String(item.message_id || '').trim())
        ));
        if (nextMessages.length !== messages.length) {
            if (nextMessages.length === 0) {
                injectionMessagesByRun.delete(safeRunId);
            } else {
                injectionMessagesByRun.set(safeRunId, nextMessages);
            }
            if (options.render !== false) {
                renderRuntimeInjectQueue();
            }
            return;
        }
    }
    const supersededClientMessageIds = normalized.superseded_client_message_ids;
    if (supersededClientMessageIds.length > 0) {
        const supersededClientMessageIdSet = new Set(supersededClientMessageIds);
        const nextMessages = messages.filter(item => (
            !supersededClientMessageIdSet.has(String(item.client_message_id || '').trim())
            && !supersededClientMessageIdSet.has(String(item.message_id || '').trim())
        ));
        if (nextMessages.length !== messages.length) {
            if (nextMessages.length === 0) {
                injectionMessagesByRun.delete(safeRunId);
            } else {
                injectionMessagesByRun.set(safeRunId, nextMessages);
            }
            if (options.render !== false) {
                renderRuntimeInjectQueue();
            }
            return;
        }
    }
    const index = findInjectionMessageIndex(messages, normalized);
    const fallbackIndex = index === -1
        ? findFallbackRemovalIndex(messages, normalized)
        : index;
    if (fallbackIndex === -1) return;
    const nextMessages = messages.filter((_, itemIndex) => itemIndex !== fallbackIndex);
    if (nextMessages.length === 0) {
        injectionMessagesByRun.delete(safeRunId);
    } else {
        injectionMessagesByRun.set(safeRunId, nextMessages);
    }
    if (options.render !== false) {
        renderRuntimeInjectQueue();
    }
}

export function replaceRuntimeInjectMessages(runId, rawMessages) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) return;
    const messages = Array.isArray(rawMessages)
        ? rawMessages.map(message => normalizeInjectionMessage(safeRunId, message))
        : [];
    injectionMessagesByRun.set(safeRunId, messages.sort(compareInjectionMessages));
    renderRuntimeInjectQueue();
}

function normalizeInjectionMessage(runId, rawMessage) {
    const content = String(rawMessage.content || '').trim();
    const mode = String(rawMessage.mode || rawMessage.delivery_mode || 'queued');
    const status = String(rawMessage.status || 'queued');
    const queuedAt = String(
        rawMessage.queued_at
        || rawMessage.created_at
        || rawMessage.occurred_at
        || new Date().toISOString(),
    );
    const recipient = String(rawMessage.recipient_instance_id || '').trim();
    const source = String(rawMessage.source || 'user').trim();
    const messageId = String(rawMessage.message_id || [
        rawMessage.injection_id || '',
        recipient,
        queuedAt,
        source,
        mode,
        content,
    ].join('|')).trim();
    return {
        message_id: messageId,
        injection_id: String(rawMessage.injection_id || messageId),
        client_message_id: String(rawMessage.client_message_id || '').trim(),
        run_id: runId,
        source,
        mode,
        status,
        content,
        recipient_instance_id: recipient,
        applied_injection_ids: normalizeAppliedInjectionIds(rawMessage),
        superseded_client_message_ids: normalizeSupersededClientMessageIds(rawMessage),
        queued_at: queuedAt,
        applied_at: String(rawMessage.applied_at || ''),
        occurred_at: String(rawMessage.occurred_at || queuedAt),
        interrupted_current_step: rawMessage.interrupted_current_step === true,
    };
}

function normalizeSupersededClientMessageIds(rawMessage) {
    const rawIds = Array.isArray(rawMessage.superseded_client_message_ids)
        ? rawMessage.superseded_client_message_ids
        : [];
    return Array.from(new Set(
        rawIds.map(item => String(item || '').trim()).filter(Boolean),
    ));
}

function normalizeAppliedInjectionIds(rawMessage) {
    const rawIds = Array.isArray(rawMessage.applied_injection_ids)
        ? rawMessage.applied_injection_ids
        : Array.isArray(rawMessage.superseded_injection_ids)
            ? rawMessage.superseded_injection_ids
            : [];
    const ids = rawIds
        .map(item => String(item || '').trim())
        .filter(Boolean);
    const ownId = String(rawMessage.injection_id || '').trim();
    if (ownId) {
        ids.push(ownId);
    }
    return Array.from(new Set(ids));
}

function findInjectionMessageIndex(messages, nextMessage) {
    const nextInjectionId = String(nextMessage.injection_id || '').trim();
    const nextClientMessageId = String(nextMessage.client_message_id || '').trim();
    if (nextClientMessageId) {
        const clientIndex = messages.findIndex(item => (
            String(item.client_message_id || '').trim() === nextClientMessageId
        ));
        if (clientIndex !== -1) return clientIndex;
    }
    const injectionIndex = messages.findIndex(item => (
        String(item.injection_id || '').trim()
        && nextInjectionId
        && String(item.injection_id || '').trim() === nextInjectionId
    ));
    if (injectionIndex !== -1) return injectionIndex;
    const directIndex = messages.findIndex(item => (
        String(item.message_id || '').trim()
        && String(item.message_id || '').trim() === String(nextMessage.message_id || '').trim()
    ));
    if (directIndex !== -1) return directIndex;
    return -1;
}

function findFallbackRemovalIndex(messages, nextMessage) {
    for (let index = 0; index < messages.length; index += 1) {
        const item = messages[index];
        const status = String(item?.status || 'queued');
        if (
            item.content === nextMessage.content
            && item.mode === nextMessage.mode
            && item.source === nextMessage.source
            && (
                !nextMessage.recipient_instance_id
                || item.recipient_instance_id === nextMessage.recipient_instance_id
            )
            && (status === 'sending' || status === 'queued' || status === 'interrupting')
        ) {
            return index;
        }
    }
    return -1;
}

function mergeInjectionMessage(current, next) {
    return {
        ...current,
        ...next,
        message_id: current.message_id || next.message_id,
        injection_id: next.injection_id || current.injection_id,
        client_message_id: current.client_message_id || next.client_message_id,
        content: next.content || current.content,
        queued_at: current.queued_at || next.queued_at,
        applied_at: next.applied_at || current.applied_at,
    };
}

function compareInjectionMessages(a, b) {
    return String(a?.queued_at || a?.occurred_at || '').localeCompare(
        String(b?.queued_at || b?.occurred_at || ''),
    );
}

function statusLabel(status) {
    if (status === 'applied') return t('inject.queue.status.inserted');
    if (status === 'failed') return t('inject.queue.status.failed');
    if (status === 'sending') return t('inject.queue.status.sending');
    if (status === 'interrupting') return t('inject.queue.status.inserting');
    return t('inject.queue.status.queued');
}

function iconForInject(message) {
    if (message.status === 'failed') {
        return '<svg viewBox="0 0 16 16" fill="none"><path d="M8 5v3.25M8 11h.01M2.75 13.25h10.5L8 2.75 2.75 13.25Z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    }
    if (message.status === 'applied') {
        return '<svg viewBox="0 0 16 16" fill="none"><path d="m3.25 8.25 3 3 6.5-6.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    }
    if (message.mode === 'interrupt') {
        return '<svg viewBox="0 0 16 16" fill="none"><path d="M3 3v10M6 3l7 5-7 5V3Z" stroke="currentColor" stroke-width="1.45" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    }
    return '<svg viewBox="0 0 16 16" fill="none"><path d="M3 4.5h7M3 8h10M3 11.5h5M11 3l2 2-2 2" stroke="currentColor" stroke-width="1.45" stroke-linecap="round" stroke-linejoin="round"/></svg>';
}

function flushIcon() {
    return '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M4 3.75h3.5M4 7.75h2.25M4 11.75h3.5M10 4.25l3.25 3.25L10 10.75M8.75 7.5h4.25" stroke="currentColor" stroke-width="1.45" stroke-linecap="round" stroke-linejoin="round"/></svg>';
}

function bindRuntimeInjectQueue(host) {
    if (!host || host.dataset.bound === 'true') return;
    host.dataset.bound = 'true';
    host.addEventListener('click', event => {
        const button = event.target?.closest?.('[data-inject-force="true"]');
        if (!button) return;
        event.preventDefault();
        event.stopPropagation();
        const runId = String(state.activeRunId || '').trim();
        if (!runId) return;
        host.dispatchEvent(new CustomEvent('agent-teams-force-inject-requested', {
            bubbles: true,
            detail: { runId },
        }));
    });
}

function cssToken(value) {
    return String(value || '').replace(/[^a-z0-9_-]/gi, '') || 'unknown';
}

function escapeHtml(value) {
    return String(value || '').replace(/[&<>"']/g, char => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
    }[char] || char));
}
