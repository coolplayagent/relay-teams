/**
 * components/messageRenderer/history.js
 * Historical message rendering and approval state hydration.
 */
import {
    applyToolReturn,
    appendMessageText,
    appendThinkingText,
    buildToolBlock,
    decoratePendingApprovalBlock,
    findToolBlockInContainer,
    indexPendingToolBlock,
    labelFromRole,
    parseApprovalArgsPreview,
    renderMessageBlock,
    renderParts,
    resolvePendingToolBlock,
    forceScrollBottom,
    setToolValidationFailureState,
} from './helpers.js';
import { getPrimaryRoleLabel } from '../../core/state.js';

export function renderHistoricalMessageList(container, messages, options = {}) {
    const pendingToolApprovals = Array.isArray(options.pendingToolApprovals)
        ? options.pendingToolApprovals
        : [];
    const runId = typeof options.runId === 'string' ? options.runId : '';
    const streamOverlayEntry = options.streamOverlayEntry && typeof options.streamOverlayEntry === 'object'
        ? options.streamOverlayEntry
        : null;
    const pendingToolBlocks = {};
    const historyMessages = Array.isArray(messages) ? messages.slice() : [];
    let lastRenderedMessage = null;

    historyMessages.forEach(msgItem => {
        const role = msgItem.role;
        const msgObj = msgItem.message;
        if (!msgObj) return;

        const parts = msgObj.parts || [];

        const isPureToolReturn = role === 'user' && parts.length > 0 &&
            parts.every(p => {
                if (p.part_kind !== undefined) return p.part_kind === 'tool-return';
                return p.tool_name !== undefined && p.content !== undefined && p.args === undefined;
            });

        if (isPureToolReturn) {
            parts.forEach(part => {
                const toolBlock = resolvePendingToolBlock(
                    pendingToolBlocks,
                    part.tool_name,
                    part.tool_call_id,
                );
                if (toolBlock) applyToolReturn(toolBlock, part.content);
            });
            return;
        }

        const label = role === 'user' && String(options.userRoleLabel || '').trim()
            ? String(options.userRoleLabel || '').trim()
            : labelFromRole(role, msgItem.role_id, msgItem.instance_id);
        const { contentEl } = renderMessageBlock(container, role, label, []);
        renderParts(contentEl, parts, pendingToolBlocks);
        lastRenderedMessage = {
            role,
            label,
            contentEl,
        };
    });

    if (streamOverlayEntry && Array.isArray(streamOverlayEntry.parts) && streamOverlayEntry.parts.length > 0) {
        renderStreamOverlayEntry(container, streamOverlayEntry, pendingToolBlocks, lastRenderedMessage);
    }

    applyPendingApprovalsToHistory(container, pendingToolApprovals, runId);
    forceScrollBottom(container);
}

function applyPendingApprovalsToHistory(container, approvals, runId) {
    if (!approvals || approvals.length === 0) return;

    const missing = [];
    approvals.forEach(approval => {
        const toolBlock = findToolBlockInContainer(
            container,
            approval?.tool_name,
            approval?.tool_call_id || null,
            true,
        );
        if (toolBlock) {
            decoratePendingApprovalBlock(toolBlock, approval);
        } else {
            missing.push(approval);
        }
    });

    if (missing.length === 0) return;
    const { contentEl } = renderMessageBlock(container, 'model', getPrimaryRoleLabel(), []);
    missing.forEach(approval => {
        const toolBlock = buildToolBlock(
            approval?.tool_name || 'unknown_tool',
            parseApprovalArgsPreview(approval?.args_preview),
            approval?.tool_call_id || null,
        );
        contentEl.appendChild(toolBlock);
        decoratePendingApprovalBlock(toolBlock, approval);
    });
}

function renderStreamOverlayEntry(
    container,
    streamOverlayEntry,
    pendingToolBlocks,
    lastRenderedMessage = null,
) {
    const label = streamOverlayEntry.label
        || labelFromRole('assistant', streamOverlayEntry.roleId, streamOverlayEntry.instanceId);
    const contentEl = resolveOverlayContentTarget(container, label, lastRenderedMessage);
    let combinedText = '';
    const overlayParts = Array.isArray(streamOverlayEntry.parts) ? streamOverlayEntry.parts : [];
    const trailingTextPart = [...overlayParts].reverse().find(part => part && typeof part === 'object');
    const flushText = (streaming = false) => {
        const safeText = String(combinedText || '');
        if (!safeText.trim()) return;
        appendMessageText(contentEl, safeText.trim(), { streaming });
        combinedText = '';
    };

    overlayParts.forEach(part => {
        if (!part || typeof part !== 'object') return;
        if (part.kind === 'text') {
            combinedText += String(part.content || '');
            return;
        }
        if (part.kind === 'thinking') {
            flushText(false);
            appendThinkingText(contentEl, String(part.content || ''), {
                partIndex: part._key ?? part.part_index ?? '',
                streaming: part.finished !== true,
            });
            return;
        }
        if (part.kind !== 'tool') return;
        flushText(false);
        const toolBlock = buildToolBlock(
            part.tool_name || 'unknown_tool',
            part.args || {},
            part.tool_call_id || null,
        );
        contentEl.appendChild(toolBlock);
        indexPendingToolBlock(
            pendingToolBlocks,
            toolBlock,
            part.tool_name,
            part.tool_call_id || null,
        );
        applyOverlayToolState(toolBlock, part);
    });

    flushText(trailingTextPart?.kind === 'text');
}

function resolveOverlayContentTarget(container, label, lastRenderedMessage) {
    const safeLabel = String(label || '').trim();
    const lastLabel = String(lastRenderedMessage?.label || '').trim();
    if (
        lastRenderedMessage?.contentEl
        && lastRenderedMessage.role !== 'user'
        && safeLabel
        && safeLabel.localeCompare(lastLabel, undefined, { sensitivity: 'accent' }) === 0
    ) {
        return lastRenderedMessage.contentEl;
    }
    const lastMessageContentEl = findLastCompatibleMessageContent(container, safeLabel);
    if (lastMessageContentEl) {
        return lastMessageContentEl;
    }
    return renderMessageBlock(container, 'assistant', label, []).contentEl;
}

function findLastCompatibleMessageContent(container, label) {
    if (!container || !label) return null;
    const messages = Array.from(container.querySelectorAll('.message'));
    const lastMessage = messages[messages.length - 1];
    if (!lastMessage) return null;

    const roleEl = lastMessage.querySelector('.msg-role');
    const contentEl = lastMessage.querySelector('.msg-content');
    const renderedLabel = String(roleEl?.textContent || '').trim();
    const expectedLabel = String(label || '').trim().toUpperCase();
    if (!contentEl || !renderedLabel || !expectedLabel) return null;
    if (renderedLabel !== expectedLabel) return null;
    return contentEl;
}

function applyOverlayToolState(toolBlock, part) {
    const statusEl = toolBlock.querySelector('.tool-status');
    const resultEl = toolBlock.querySelector('.tool-result');
    if (!statusEl || !resultEl) return;

    if (part.validation) {
        setToolValidationFailureState(toolBlock, part.validation);
        return;
    }

    if (part.approvalStatus === 'requested') {
        decoratePendingApprovalBlock(toolBlock, {
            tool_call_id: part.tool_call_id,
            tool_name: part.tool_name,
            args_preview: JSON.stringify(part.args || {}),
            status: 'requested',
        });
        return;
    }

    if (part.approvalStatus === 'deny') {
        resultEl.classList.remove('error-text');
        resultEl.classList.add('warning-text');
        resultEl.innerHTML = 'Approval denied. Tool will not execute.';
        return;
    }

    if (part.approvalStatus === 'approve' && part.result === undefined) {
        resultEl.classList.remove('error-text');
        resultEl.classList.add('warning-text');
        resultEl.innerHTML = 'Approval submitted. Waiting for tool result...';
        return;
    }

    if (part.result !== undefined) {
        applyToolReturn(toolBlock, part.result);
        return;
    }

    statusEl.innerHTML = '<div class="spinner"></div>';
    resultEl.classList.remove('error-text');
    resultEl.classList.add('warning-text');
    resultEl.innerHTML = 'Processing...';
}
