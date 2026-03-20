/**
 * components/messageRenderer/helpers/block.js
 * Message block and part rendering helpers.
 */
import { getPrimaryRoleLabel, isCoordinatorRoleId, isMainAgentRoleId } from '../../../core/state.js';
import { parseMarkdown } from '../../../utils/markdown.js';
import {
    applyToolReturn,
    buildToolBlock,
    indexPendingToolBlock,
    resolvePendingToolBlock,
    setToolValidationFailureState,
} from './toolBlocks.js';

const STREAMING_CURSOR_CLASS = 'streaming-cursor';

export function renderMessageBlock(container, role, label, parts = []) {
    const safeLabel = label || 'Agent';
    if (container) {
        const empty = container.querySelector('.panel-empty');
        if (empty) empty.remove();
    }
    const wrapper = document.createElement('div');
    wrapper.className = 'message';
    wrapper.dataset.role = role;

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

export function renderParts(contentEl, parts, pendingToolBlocks) {
    let combinedText = '';

    const flushText = () => {
        if (combinedText.trim()) {
            appendMessageText(contentEl, combinedText.trim());
            combinedText = '';
        }
    };

    parts.forEach(part => {
        const kind = part.part_kind;

        if (kind === 'text' || kind === 'user-prompt') {
            combinedText += (part.content || '') + '\n\n';
        } else if (kind === 'thinking') {
            flushText();
            appendThinkingText(contentEl, part.content || '', { streaming: false });
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

export function labelFromRole(role, roleId, instanceId) {
    if (role === 'user') return 'System';
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

export function appendThinkingText(contentEl, text, options = {}) {
    const { textEl } = ensureThinkingBlock(contentEl, options);
    updateThinkingText(textEl, text, options);
    return textEl;
}

export function updateMessageText(textEl, text, options = {}) {
    textEl.innerHTML = parseMarkdown(String(text || ''));
    syncStreamingCursor(textEl, options.streaming === true);
    return textEl;
}

export function updateThinkingText(textEl, text, options = {}) {
    updateMessageText(textEl, text, options);
    const thinkingBlock = textEl?.closest?.('.thinking-block');
    if (thinkingBlock) {
        const liveEl = thinkingBlock.querySelector('.thinking-live');
        if (liveEl) {
            liveEl.style.display = options.streaming === true ? 'inline-flex' : 'none';
        }
        thinkingBlock.dataset.streaming = options.streaming === true ? 'true' : 'false';
        thinkingBlock.open = options.streaming === true;
    }
    return textEl;
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
            const textEl = existing.querySelector('.thinking-text');
            if (textEl) {
                return { blockEl: existing, textEl };
            }
        }
    }

    const detailsEl = document.createElement('details');
    detailsEl.className = 'thinking-block';
    detailsEl.dataset.streaming = options.streaming === true ? 'true' : 'false';
    detailsEl.open = options.streaming === true;
    if (safePartIndex) {
        detailsEl.dataset.partIndex = safePartIndex;
    }
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

function resolveStreamingCursorHost(root) {
    if (!root || !root.childNodes?.length) return root;

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
        return resolveStreamingCursorHost(child);
    }

    return root;
}
