/**
 * components/messageRenderer/helpers/block.js
 * Message block and part rendering helpers.
 */
import { getPrimaryRoleLabel, isCoordinatorRoleId, isMainAgentRoleId } from '../../../core/state.js';
import { t } from '../../../utils/i18n.js';
import {
    applyToolReturn,
    buildToolBlock,
    indexPendingToolBlock,
    resolvePendingToolBlock,
    setToolValidationFailureState,
} from './toolBlocks.js';
import { appendStructuredContentPart, renderRichContent } from './content.js';
import {
    appendPromptContentBlock,
    normalizePromptContentPart,
    updatePromptContentBlock,
} from './prompt.js';

const STREAMING_CURSOR_CLASS = 'streaming-cursor';
const LARGE_STREAM_TEXT_THRESHOLD = 12000;
const RICH_TEXT_AUTORENDER_LIMIT = 80000;
const PLAIN_TEXT_CHUNK_SIZE = 16384;
const BOTTOM_FOLLOW_THRESHOLD_PX = 96;
const thinkingOpenState = new Map();

export function renderMessageBlock(container, role, label, parts = [], options = {}) {
    const safeLabel = label || 'Agent';
    if (container) {
        const empty = container.querySelector('.panel-empty');
        if (empty) empty.remove();
    }
    const wrapper = document.createElement('div');
    wrapper.className = 'message';
    wrapper.dataset.role = role;
    applyMessageMetadata(wrapper, options);

    const roleClass = roleClassName(role, safeLabel);
    wrapper.innerHTML = `
        <div class="msg-header">
            <span class="msg-role ${roleClass}">${safeLabel.toUpperCase()}</span>
        </div>
        <div class="msg-content"></div>
    `;
    container.appendChild(wrapper);
    scrollBottom(container);

    const contentEl = wrapper.querySelector('.msg-content');
    const pendingToolBlocks = {};

    if (parts.length > 0) {
        renderParts(contentEl, parts, pendingToolBlocks);
    }

    return { wrapper, contentEl, pendingToolBlocks };
}

function applyMessageMetadata(wrapper, options = {}) {
    const runId = String(options.runId || '').trim();
    const instanceId = String(options.instanceId || '').trim();
    const roleId = String(options.roleId || '').trim();
    const streamKey = String(options.streamKey || '').trim();
    if (runId) wrapper.dataset.runId = runId;
    if (instanceId) wrapper.dataset.instanceId = instanceId;
    if (roleId) wrapper.dataset.roleId = roleId;
    if (streamKey) wrapper.dataset.streamKey = streamKey;
}

export function renderParts(contentEl, parts, pendingToolBlocks, options = {}) {
    let combinedText = '';

    const flushText = () => {
        if (combinedText.trim()) {
            if (options.collapseUserPrompt === true) {
                appendUserPromptText(contentEl, combinedText.trim());
            } else {
                appendMessageText(contentEl, combinedText.trim());
            }
            combinedText = '';
        }
    };

    parts.forEach(part => {
        const kind = part.part_kind;

        if (kind === 'text') {
            combinedText += (part.content || '') + '\n\n';
        } else if (kind === 'user-prompt') {
            flushText();
            appendUserPromptText(contentEl, part.content);
        } else if (kind === 'thinking') {
            flushText();
            appendThinkingText(contentEl, part.content || '', {
                streaming: false,
                runId: options.runId,
                instanceId: options.instanceId,
                streamKey: options.streamKey,
                partIndex: part.part_index ?? part.part_id ?? '',
            });
        } else if (kind === 'file') {
            flushText();
            const structuredPart = binaryPayloadToStructuredPart(part.content);
            if (structuredPart) {
                appendStructuredContentPart(contentEl, structuredPart);
            }
        } else if (kind === 'tool-call' || (part.tool_name && part.args !== undefined)) {
            flushText();
            const tb = buildToolBlock(part.tool_name, part.args, part.tool_call_id);
            contentEl.appendChild(tb);
            indexPendingToolBlock(pendingToolBlocks, tb, part.tool_name, part.tool_call_id);
        } else if (kind === 'tool-return') {
            const toolBlock = resolvePendingToolBlock(
                pendingToolBlocks,
                part.tool_name,
                part.tool_call_id,
            );
            if (toolBlock) applyToolReturn(toolBlock, part.content);
        } else if (kind === 'retry-prompt' && part.tool_name) {
            let toolBlock = resolvePendingToolBlock(
                pendingToolBlocks,
                part.tool_name,
                part.tool_call_id,
            );
            if (!toolBlock) {
                toolBlock = buildToolBlock(part.tool_name, {}, part.tool_call_id);
                contentEl.appendChild(toolBlock);
                indexPendingToolBlock(
                    pendingToolBlocks,
                    toolBlock,
                    part.tool_name,
                    part.tool_call_id,
                );
            }
            setToolValidationFailureState(toolBlock, {
                reason: 'Input validation failed before tool execution.',
                details: part.content,
            });
        }
    });

    flushText();
}

function userPromptItemToStructuredPart(item) {
    return normalizePromptContentPart(item);
}

function binaryPayloadToStructuredPart(content) {
    if (!content || typeof content !== 'object') {
        return null;
    }
    const mediaType = String(content.media_type || '').trim();
    const base64Data = String(content.data || '').trim();
    if (!mediaType || !base64Data) {
        return null;
    }
    const modality = mediaType.startsWith('image/')
        ? 'image'
        : mediaType.startsWith('audio/')
            ? 'audio'
            : mediaType.startsWith('video/')
                ? 'video'
                : '';
    if (!modality) {
        return null;
    }
    return {
        kind: 'media_ref',
        modality,
        mime_type: mediaType,
        url: `data:${mediaType};base64,${base64Data}`,
        name: '',
    };
}

export function labelFromRole(role, roleId, instanceId) {
    if (role === 'user') {
        if (isCoordinatorRoleId(roleId) || isMainAgentRoleId(roleId) || !roleId) {
            return 'System';
        }
        return t('subagent.task_prompt');
    }
    if (isCoordinatorRoleId(roleId)) return 'Coordinator';
    if (isMainAgentRoleId(roleId)) return 'Main Agent';
    if (roleId) return roleId;
    return instanceId ? instanceId.slice(0, 8) : 'Agent';
}

export function scrollBottom(container) {
    if (!container) return;
    const threshold = 80;
    const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    if (distanceFromBottom <= threshold) {
        container.scrollTop = container.scrollHeight;
    }
}

export function forceScrollBottom(container) {
    if (container) container.scrollTop = container.scrollHeight;
}

export function appendMessageText(contentEl, text, options = {}) {
    const textEl = document.createElement('div');
    textEl.className = 'msg-text';
    updateMessageText(textEl, text, options);
    contentEl.appendChild(textEl);
    return textEl;
}

export function appendUserPromptText(contentEl, text) {
    return appendPromptContentBlock(contentEl, text, {
        className: 'user-prompt-block',
        fallbackTitle: t('subagent.task_prompt'),
    });
}

export function appendThinkingText(contentEl, text, options = {}) {
    const { textEl } = ensureThinkingBlock(contentEl, options);
    updateThinkingText(textEl, text, options);
    return textEl;
}

export function updateMessageText(textEl, text, options = {}) {
    const source = String(text || '');
    if (shouldRenderPlainText(textEl, source, options)) {
        renderPlainTextContent(textEl, source, options);
        syncStreamingCursor(textEl, options.streaming === true);
        return textEl;
    }
    clearPlainTextRenderState(textEl);
    renderRichContent(textEl, String(text || ''), {
        enableWorkspaceImagePreview: options.enableWorkspaceImagePreview !== false,
    });
    syncStreamingCursor(textEl, options.streaming === true);
    return textEl;
}

export function updateUserPromptText(promptEl, text) {
    return updatePromptContentBlock(promptEl, text, {
        fallbackTitle: t('subagent.task_prompt'),
    });
}

export function updateThinkingText(textEl, text, options = {}) {
    updateMessageText(textEl, text, {
        ...options,
        enableWorkspaceImagePreview: false,
        forcePlainText: true,
    });
    const thinkingBlock = textEl?.closest?.('.thinking-block');
    if (thinkingBlock) {
        bindThinkingOpenState(thinkingBlock, options);
        const liveEl = thinkingBlock.querySelector('.thinking-live');
        if (liveEl) {
            liveEl.style.display = options.streaming === true ? 'inline-flex' : 'none';
        }
        thinkingBlock.dataset.streaming = options.streaming === true ? 'true' : 'false';
        syncThinkingOpenFromState(thinkingBlock, options.streaming === true);
    }
    return textEl;
}

function shouldRenderPlainText(textEl, source, options = {}) {
    if (!textEl) {
        return false;
    }
    if (options.appendDelta === true || options.forcePlainText === true) {
        return true;
    }
    if (textEl.__plainTextRenderState) {
        return true;
    }
    if (options.streaming === true && source.length >= LARGE_STREAM_TEXT_THRESHOLD) {
        return true;
    }
    return options.streaming !== true && source.length > RICH_TEXT_AUTORENDER_LIMIT;
}

function renderPlainTextContent(textEl, source, options = {}) {
    const state = ensurePlainTextRenderState(textEl);
    if (options.appendDelta === true) {
        appendPlainText(textEl, source, state);
        return;
    }
    if (
        state.renderedLength > 0
        && source.length >= state.renderedLength
        && options.streaming === true
    ) {
        appendPlainText(textEl, source.slice(state.renderedLength), state);
        return;
    }
    if (
        state.renderedLength > 0
        && source.length === state.renderedLength
        && options.streaming !== true
    ) {
        return;
    }
    resetPlainText(textEl, source, state);
}

function ensurePlainTextRenderState(textEl) {
    if (textEl.__plainTextRenderState) {
        textEl.classList?.add?.('plain-stream-text');
        if (textEl.dataset) {
            textEl.dataset.renderMode = 'plain-stream';
        }
        return textEl.__plainTextRenderState;
    }
    const state = {
        renderedLength: 0,
        tailNode: null,
    };
    textEl.__plainTextRenderState = state;
    textEl.classList?.add?.('plain-stream-text');
    if (textEl.dataset) {
        textEl.dataset.renderMode = 'plain-stream';
    }
    if (typeof textEl.replaceChildren === 'function') {
        textEl.replaceChildren();
    } else {
        textEl.textContent = '';
    }
    return state;
}

function clearPlainTextRenderState(textEl) {
    if (!textEl?.__plainTextRenderState) {
        return;
    }
    delete textEl.__plainTextRenderState;
    textEl.classList?.remove?.('plain-stream-text');
    if (textEl.dataset && textEl.dataset.renderMode === 'plain-stream') {
        delete textEl.dataset.renderMode;
    }
}

function resetPlainText(textEl, source, state) {
    if (typeof textEl.replaceChildren === 'function') {
        textEl.replaceChildren();
    } else {
        textEl.textContent = '';
    }
    state.renderedLength = 0;
    state.tailNode = null;
    appendPlainText(textEl, source, state);
}

function appendPlainText(textEl, delta, state) {
    const text = String(delta || '');
    if (!text) {
        return;
    }
    let offset = 0;
    while (offset < text.length) {
        if (!state.tailNode || String(state.tailNode.textContent || '').length >= PLAIN_TEXT_CHUNK_SIZE) {
            state.tailNode = createPlainTextNode('');
            textEl.appendChild(state.tailNode);
        }
        const currentLength = String(state.tailNode.textContent || '').length;
        const available = Math.max(1, PLAIN_TEXT_CHUNK_SIZE - currentLength);
        const next = text.slice(offset, offset + available);
        state.tailNode.textContent = String(state.tailNode.textContent || '') + next;
        state.renderedLength += next.length;
        offset += next.length;
    }
}

function createPlainTextNode(text) {
    if (typeof document !== 'undefined' && typeof document.createTextNode === 'function') {
        return document.createTextNode(text);
    }
    return {
        nodeType: 3,
        textContent: String(text || ''),
    };
}

export function clearThinkingOpenState() {
    thinkingOpenState.clear();
}

export function clearThinkingOpenStateForRun(runId) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) return;
    Array.from(thinkingOpenState.keys()).forEach(key => {
        if (key.startsWith(`${safeRunId}::`)) {
            thinkingOpenState.delete(key);
        }
    });
}

export function syncStreamingCursor(textEl, active) {
    const existingCursor = textEl?.querySelector?.(`.${STREAMING_CURSOR_CLASS}`);
    if (existingCursor) {
        existingCursor.remove();
    }
    if (!active || !textEl) return textEl;

    const cursor = document.createElement('span');
    cursor.className = STREAMING_CURSOR_CLASS;
    cursor.setAttribute('aria-hidden', 'true');
    resolveStreamingCursorHost(textEl).appendChild(cursor);
    return textEl;
}

function roleClassName(role, label) {
    const safeLabel = String(label || '').toLowerCase();
    if (safeLabel === String(getPrimaryRoleLabel()).toLowerCase()) return 'role-coordinator';
    if (safeLabel.includes('coordinator') || safeLabel.includes('main agent')) return 'role-coordinator';
    if (role === 'user') return 'role-user';
    return 'role-agent';
}

function ensureThinkingBlock(contentEl, options = {}) {
    const safePartIndex = String(options.partIndex ?? '').trim();
    if (safePartIndex) {
        const existing = contentEl.querySelector(
            `.thinking-block[data-part-index="${safePartIndex}"]`
        );
        if (existing) {
            bindThinkingOpenState(existing, options);
            syncThinkingOpenFromState(existing, options.streaming === true);
            const textEl = existing.querySelector('.thinking-text');
            if (textEl) {
                return { blockEl: existing, textEl };
            }
        }
    }

    const detailsEl = document.createElement('details');
    detailsEl.className = 'thinking-block';
    detailsEl.dataset.streaming = options.streaming === true ? 'true' : 'false';
    if (safePartIndex) {
        detailsEl.dataset.partIndex = safePartIndex;
    }
    bindThinkingOpenState(detailsEl, options);
    syncThinkingOpenFromState(detailsEl, options.streaming === true);
    detailsEl.innerHTML = `
        <summary class="thinking-summary">
            <span class="thinking-label">Thinking</span>
            <span class="thinking-live" style="display:${options.streaming === true ? 'inline-flex' : 'none'};">Live</span>
        </summary>
        <div class="thinking-body">
            <div class="msg-text thinking-text"></div>
        </div>
    `;
    contentEl.appendChild(detailsEl);
    return {
        blockEl: detailsEl,
        textEl: detailsEl.querySelector('.thinking-text'),
    };
}

function bindThinkingOpenState(block, options = {}) {
    const key = resolveThinkingOpenStateKey(block, options);
    if (key) {
        block.dataset.thinkingOpenKey = key;
    }
    if (block.dataset.thinkingOpenBound === 'true') {
        return;
    }
    block.dataset.thinkingOpenBound = 'true';
    const captureToggleAnchor = () => {
        const container = block.closest?.('.chat-scroll');
        if (!container || !block.getBoundingClientRect) return;
        block.__thinkingToggleAnchor = {
            container,
            top: block.getBoundingClientRect().top,
        };
    };
    const summary = block.querySelector?.('.thinking-summary') || null;
    summary?.addEventListener?.('pointerdown', captureToggleAnchor, { passive: true });
    summary?.addEventListener?.('keydown', event => {
        const key = String(event?.key || '');
        if (key === 'Enter' || key === ' ') {
            captureToggleAnchor();
        }
    });
    block.addEventListener('toggle', () => {
        if (block.dataset.thinkingOpenSync === 'true') return;
        const stateKey = String(block.dataset.thinkingOpenKey || '').trim();
        if (!stateKey) return;
        block.dataset.thinkingUserToggled = 'true';
        thinkingOpenState.set(stateKey, block.open === true);
        restoreThinkingToggleAnchor(block);
    });
}

function restoreThinkingToggleAnchor(block) {
    const anchor = block?.__thinkingToggleAnchor || null;
    delete block.__thinkingToggleAnchor;
    if (!anchor?.container || typeof window === 'undefined') {
        return;
    }
    const restore = () => {
        if (!anchor.container?.isConnected || !block?.isConnected || !block.getBoundingClientRect) {
            return;
        }
        const nextTop = block.getBoundingClientRect().top;
        const delta = nextTop - Number(anchor.top || 0);
        if (Math.abs(delta) > 0.5) {
            anchor.container.scrollTop += delta;
        }
    };
    if (typeof window.requestAnimationFrame === 'function') {
        window.requestAnimationFrame(() => {
            restore();
            window.requestAnimationFrame(restore);
        });
        return;
    }
    restore();
}

function syncThinkingOpenFromState(block, defaultOpen = false) {
    const stateKey = String(block?.dataset?.thinkingOpenKey || '').trim();
    const nextOpen = stateKey && thinkingOpenState.has(stateKey)
        ? thinkingOpenState.get(stateKey) === true
        : defaultOpen === true;
    if (block.open === nextOpen) return;
    block.dataset.thinkingOpenSync = 'true';
    block.open = nextOpen;
    const clearSyncFlag = () => {
        if (block.dataset.thinkingOpenSync === 'true') {
            delete block.dataset.thinkingOpenSync;
        }
    };
    if (typeof window !== 'undefined' && typeof window.setTimeout === 'function') {
        window.setTimeout(clearSyncFlag, 0);
        return;
    }
    clearSyncFlag();
}

function resolveThinkingOpenStateKey(block, options = {}) {
    const runId = String(options.runId || block?.closest?.('.message')?.dataset?.runId || '').trim();
    const streamKey = String(
        options.streamKey
        || block?.closest?.('.message')?.dataset?.streamKey
        || options.instanceId
        || '',
    ).trim();
    const partIndex = String(
        options.partIndex
        ?? options.partKey
        ?? block?.dataset?.partIndex
        ?? '',
    ).trim();
    if (!runId && !streamKey && !partIndex) return '';
    return `${runId || 'run'}::${streamKey || 'stream'}::${partIndex || 'thinking'}`;
}

function captureBottomIntent(container) {
    if (!container) {
        return { shouldFollow: false };
    }
    return { shouldFollow: isNearScrollBottom(container) };
}

function scheduleFollowBottom(container, options = {}) {
    if (!container || options.follow?.shouldFollow !== true) return;
    const scroll = () => {
        container.scrollTop = Math.max(
            0,
            Number(container.scrollHeight || 0) - Number(container.clientHeight || 0),
        );
    };
    if (typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function') {
        window.requestAnimationFrame(() => {
            scroll();
            window.requestAnimationFrame(scroll);
        });
        return;
    }
    scroll();
}

function isNearScrollBottom(container) {
    const distance = Number(container?.scrollHeight || 0)
        - Number(container?.scrollTop || 0)
        - Number(container?.clientHeight || 0);
    return distance <= BOTTOM_FOLLOW_THRESHOLD_PX;
}

function resolveStreamingCursorHost(root) {
    if (!root || !root.childNodes?.length) return root;

    const terminalSelector = [
        'pre code',
        'blockquote > :last-child',
        'li:last-child',
        'p:last-child',
        'h1:last-child, h2:last-child, h3:last-child, h4:last-child, h5:last-child, h6:last-child',
        'td:last-child, th:last-child',
        '.msg-inline-code:last-child',
    ].join(', ');
    const terminalCandidates = Array.from(root.querySelectorAll(terminalSelector));
    for (let index = terminalCandidates.length - 1; index >= 0; index -= 1) {
        const candidate = terminalCandidates[index];
        if (!candidate || candidate.classList?.contains(STREAMING_CURSOR_CLASS)) continue;
        if (hasRenderableTerminalContent(candidate)) {
            return candidate;
        }
    }

    return findLastRenderableElement(root) || root;
}

function findLastRenderableElement(root) {
    if (!root || !root.childNodes?.length) return null;
    for (let index = root.childNodes.length - 1; index >= 0; index -= 1) {
        const child = root.childNodes[index];
        if (!child) continue;
        if (child.nodeType === Node.TEXT_NODE) {
            if (String(child.textContent || '').trim()) {
                return root;
            }
            continue;
        }
        if (child.nodeType !== Node.ELEMENT_NODE) continue;
        if (child.classList?.contains(STREAMING_CURSOR_CLASS)) continue;
        if (hasRenderableTerminalContent(child)) {
            const nested = findLastRenderableElement(child);
            return nested || child;
        }
    }
    return null;
}

function hasRenderableTerminalContent(node) {
    if (!node) return false;
    const ownText = Array.from(node.childNodes || [])
        .filter(child => child?.nodeType === Node.TEXT_NODE)
        .map(child => String(child.textContent || ''))
        .join('')
        .trim();
    if (ownText) return true;
    return !!String(node.textContent || '').trim();
}
