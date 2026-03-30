/**
 * components/messageRenderer/history.js
 * Historical message rendering and approval state hydration.
 */
import { isRunPrimaryRoleId } from '../../core/state.js';
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
    setToolStatus,
    setToolValidationFailureState,
} from './helpers.js';

export function renderHistoricalMessageList(container, messages, options = {}) {
    if (container?.dataset) {
        container.dataset.primaryRoleLabel = String(options.primaryRoleLabel || '').trim();
    }
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
        if (String(msgItem?.entry_type || '') === 'marker') {
            renderHistoryMarker(container, msgItem);
            lastRenderedMessage = null;
            return;
        }
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
        const streamKey = resolveHistoryStreamKey(runId, msgItem.instance_id, msgItem.role_id);
        const { wrapper, contentEl } = renderMessageBlock(container, role, label, [], {
            runId,
            instanceId: String(msgItem.instance_id || '').trim(),
            roleId: String(msgItem.role_id || '').trim(),
            streamKey,
        });
        renderParts(contentEl, parts, pendingToolBlocks, {
            collapseUserPrompt: role === 'user' && options.collapsibleUserPrompts === true,
        });
        lastRenderedMessage = {
            role,
            label,
            wrapper,
            contentEl,
            runId,
            roleId: String(msgItem.role_id || '').trim(),
            instanceId: String(msgItem.instance_id || '').trim(),
            streamKey,
        };
    });

    if (streamOverlayEntry && Array.isArray(streamOverlayEntry.parts) && streamOverlayEntry.parts.length > 0) {
        renderStreamOverlayEntry(container, streamOverlayEntry, pendingToolBlocks, lastRenderedMessage, runId);
    }

    applyPendingApprovalsToHistory(container, pendingToolApprovals, runId);
    forceScrollBottom(container);
}

function renderHistoryMarker(container, marker) {
    if (!container || !marker) return;
    const markerEl = document.createElement('div');
    markerEl.className = 'message-history-divider';
    markerEl.dataset.markerType = String(marker.marker_type || '').trim();
    markerEl.innerHTML = `
        <span class="message-history-divider-line" aria-hidden="true"></span>
        <span class="message-history-divider-chip">${String(marker.label || 'History marker')}</span>
        <span class="message-history-divider-line" aria-hidden="true"></span>
    `;
    container.appendChild(markerEl);
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
    const primaryRoleLabel = String(container?.dataset?.primaryRoleLabel || '').trim()
        || 'Main Agent';
    const { contentEl } = renderMessageBlock(container, 'model', primaryRoleLabel, [], {
        runId,
        streamKey: 'primary',
    });
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
    runId = '',
) {
    const label = streamOverlayEntry.label
        || labelFromRole('assistant', streamOverlayEntry.roleId, streamOverlayEntry.instanceId);
    const contentEl = resolveOverlayContentTarget(
        container,
        label,
        streamOverlayEntry,
        lastRenderedMessage,
        runId,
    );
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

function resolveOverlayContentTarget(container, label, streamOverlayEntry, lastRenderedMessage, runId = '') {
    const safeLabel = String(label || '').trim();
    const lastLabel = String(lastRenderedMessage?.label || '').trim();
    const overlayStreamKey = resolveHistoryStreamKey(
        runId || lastRenderedMessage?.runId || '',
        streamOverlayEntry?.instanceId,
        streamOverlayEntry?.roleId,
    );
    if (
        wrapperMatchesOverlay(lastRenderedMessage?.wrapper, {
            runId: runId || lastRenderedMessage?.runId || '',
            roleId: streamOverlayEntry?.roleId,
            instanceId: streamOverlayEntry?.instanceId,
            streamKey: overlayStreamKey,
        })
        && safeLabel
        && lastRenderedMessage?.contentEl
        && lastRenderedMessage.role !== 'user'
        && safeLabel.localeCompare(lastLabel, undefined, { sensitivity: 'accent' }) === 0
    ) {
        return lastRenderedMessage.contentEl;
    }
    const lastMessageContentEl = findLastCompatibleMessageContent(container, safeLabel, {
        runId: runId || lastRenderedMessage?.runId || '',
        roleId: streamOverlayEntry?.roleId,
        instanceId: streamOverlayEntry?.instanceId,
        streamKey: overlayStreamKey,
    });
    if (lastMessageContentEl) {
        return lastMessageContentEl;
    }
    return renderMessageBlock(container, 'assistant', label, [], {
        runId: runId || lastRenderedMessage?.runId || '',
        roleId: String(streamOverlayEntry?.roleId || '').trim(),
        instanceId: String(streamOverlayEntry?.instanceId || '').trim(),
        streamKey: overlayStreamKey,
    }).contentEl;
}

function findLastCompatibleMessageContent(container, label, options = {}) {
    if (!container || !label) return null;
    const messages = Array.from(container.querySelectorAll('.message'));
    const expectedLabel = String(label || '').trim().toUpperCase();
    if (!expectedLabel) return null;
    for (let index = messages.length - 1; index >= 0; index -= 1) {
        const message = messages[index];
        const roleEl = message.querySelector('.msg-role');
        const contentEl = message.querySelector('.msg-content');
        const renderedLabel = String(roleEl?.textContent || '').trim();
        if (!contentEl || !renderedLabel) continue;
        if (renderedLabel !== expectedLabel) continue;
        if (!wrapperMatchesOverlay(message, options)) continue;
        return contentEl;
    }
    return null;
}

function applyOverlayToolState(toolBlock, part) {
    const outputEl = toolBlock.querySelector('.tool-output');
    if (!outputEl) return;

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
        setToolStatus(toolBlock, 'warning');
        outputEl.classList.remove('error-text');
        outputEl.classList.add('warning-text');
        outputEl.innerHTML = 'Approval denied. Tool will not execute.';
        return;
    }

    if (part.approvalStatus === 'approve' && part.result === undefined) {
        setToolStatus(toolBlock, 'running');
        outputEl.classList.remove('error-text');
        outputEl.classList.add('warning-text');
        outputEl.innerHTML = 'Approval submitted. Waiting for tool result...';
        return;
    }

    if (part.result !== undefined) {
        applyToolReturn(toolBlock, part.result);
        return;
    }

    setToolStatus(toolBlock, 'running');
    outputEl.classList.remove('error-text');
    outputEl.classList.remove('warning-text');
    outputEl.textContent = '';
}

function resolveHistoryStreamKey(runId, instanceId, roleId) {
    const safeRoleId = String(roleId || '').trim();
    const safeInstanceId = String(instanceId || '').trim();
    if (!safeRoleId || safeInstanceId === 'primary' || safeInstanceId === 'coordinator') {
        return 'primary';
    }
    if (runId && isRunPrimaryRoleId(safeRoleId, runId)) {
        return 'primary';
    }
    return safeInstanceId || `role:${safeRoleId}`;
}

function wrapperMatchesOverlay(wrapper, options = {}) {
    if (!wrapper) return false;
    const expectedRunId = String(options.runId || '').trim();
    const expectedRoleId = String(options.roleId || '').trim();
    const expectedInstanceId = String(options.instanceId || '').trim();
    const expectedStreamKey = String(options.streamKey || '').trim();
    const wrapperRunId = String(wrapper.dataset.runId || '').trim();
    const wrapperRoleId = String(wrapper.dataset.roleId || '').trim();
    const wrapperInstanceId = String(wrapper.dataset.instanceId || '').trim();
    const wrapperStreamKey = String(wrapper.dataset.streamKey || '').trim();
    if (expectedRunId && wrapperRunId && wrapperRunId !== expectedRunId) return false;
    if (expectedStreamKey && wrapperStreamKey && wrapperStreamKey !== expectedStreamKey) return false;
    if (expectedRoleId && wrapperRoleId && wrapperRoleId !== expectedRoleId) return false;
    if (expectedInstanceId && wrapperInstanceId && wrapperInstanceId !== expectedInstanceId) return false;
    return true;
}
