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

const STREAMING_CURSOR_CLASS = 'streaming-cursor';

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
            if (Array.isArray(part.content)) {
                flushText();
                part.content
                    .map(userPromptItemToStructuredPart)
                    .filter(Boolean)
                    .forEach(structuredPart => {
                        appendStructuredContentPart(contentEl, structuredPart);
                    });
                return;
            }
            combinedText += (part.content || '') + '\n\n';
        } else if (kind === 'thinking') {
            flushText();
            appendThinkingText(contentEl, part.content || '', { streaming: false });
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
    if (typeof item === 'string') {
        return { kind: 'text', text: item };
    }
    if (!item || typeof item !== 'object') {
        return null;
    }
    const kind = String(item.kind || '');
    if (kind === 'image-url') {
        return {
            kind: 'media_ref',
            modality: 'image',
            mime_type: String(item.media_type || 'image/*'),
            url: String(item.url || ''),
            name: '',
        };
    }
    if (kind === 'audio-url') {
        return {
            kind: 'media_ref',
            modality: 'audio',
            mime_type: String(item.media_type || 'audio/*'),
            url: String(item.url || ''),
            name: '',
        };
    }
    if (kind === 'video-url') {
        return {
            kind: 'media_ref',
            modality: 'video',
            mime_type: String(item.media_type || 'video/*'),
            url: String(item.url || ''),
            name: '',
        };
    }
    if (kind === 'binary') {
        return binaryPayloadToStructuredPart(item);
    }
    return null;
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
    const promptEl = document.createElement('details');
    promptEl.className = 'user-prompt-block';
    promptEl.innerHTML = `
        <summary class="user-prompt-summary">
            <span class="user-prompt-title"></span>
            <span class="user-prompt-preview"></span>
        </summary>
        <div class="user-prompt-body">
            <div class="user-prompt-text"></div>
        </div>
    `;
    updateUserPromptText(promptEl, text);
    contentEl.appendChild(promptEl);
    return promptEl;
}

export function appendThinkingText(contentEl, text, options = {}) {
    const { textEl } = ensureThinkingBlock(contentEl, options);
    updateThinkingText(textEl, text, options);
    return textEl;
}

export function updateMessageText(textEl, text, options = {}) {
    renderRichContent(textEl, String(text || ''));
    syncStreamingCursor(textEl, options.streaming === true);
    return textEl;
}

export function updateUserPromptText(promptEl, text) {
    const normalized = String(text || '').replace(/\r\n?/g, '\n').trim();
    const lines = normalized ? normalized.split('\n') : [];
    const title = lines[0] || t('subagent.task_prompt');
    const preview = lines.length > 1 ? lines.slice(1).join('\n') : title;
    const titleEl = promptEl?.querySelector('.user-prompt-title');
    const previewEl = promptEl?.querySelector('.user-prompt-preview');
    const bodyEl = promptEl?.querySelector('.user-prompt-text');
    if (titleEl) {
        titleEl.textContent = title;
    }
    if (previewEl) {
        previewEl.textContent = preview;
    }
    if (bodyEl) {
        bodyEl.textContent = normalized;
    }
    return promptEl;
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
