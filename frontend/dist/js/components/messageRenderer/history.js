/**
 * components/messageRenderer/history.js
 * Historical message rendering and approval state hydration.
 */
import { isRunPrimaryRoleId } from '../../core/state.js';
import { formatMessage } from '../../utils/i18n.js';
import {
    applyToolReturn,
    appendMessageText,
    appendStructuredContentPart,
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
    const persistedOverlayIndex = buildPersistedOverlayIndex(historyMessages, runId);
    const timelineHydration = new Map();
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
        collectHydratedParts(timelineHydration, {
            runId,
            instanceId: String(msgItem.instance_id || '').trim(),
            roleId: String(msgItem.role_id || '').trim(),
            streamKey,
            view: String(options.timelineView || '').trim(),
        }, parts);
        const { wrapper, contentEl } = renderMessageBlock(container, role, label, [], {
            runId,
            instanceId: String(msgItem.instance_id || '').trim(),
            roleId: String(msgItem.role_id || '').trim(),
            streamKey,
        });
        const msgCreatedAt = String(msgItem.created_at || '').trim();
        if (msgCreatedAt) wrapper.dataset.createdAt = msgCreatedAt;
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

    timelineHydration.forEach((entry) => {
        applyTimelineAction({
            type: 'hydrate_parts',
            scope: entry.scope,
            parts: entry.parts,
            status: options.runStatus || '',
        });
    });

    // Store the last message timestamp from raw data (including tool-return
    // messages not rendered as .message elements) so the collapse group can
    // compute duration from round start to the final message.
    if (historyMessages.length > 0) {
        for (let i = historyMessages.length - 1; i >= 0; i -= 1) {
            const ts = String(historyMessages[i]?.created_at || '').trim();
            if (ts) {
                container.dataset.roundLastMessageAt = ts;
                break;
            }
        }
    }

    const filteredOverlayEntry = filterPersistedOverlayParts(
        streamOverlayEntry,
        persistedOverlayIndex,
        runId,
    );

    if (
        filteredOverlayEntry
        && (
            (Array.isArray(filteredOverlayEntry.parts) && filteredOverlayEntry.parts.length > 0)
            || filteredOverlayEntry.textStreaming === true
            || filteredOverlayEntry.idleCursor === true
        )
    ) {
        renderStreamOverlayEntry(
            container,
            filteredOverlayEntry,
            pendingToolBlocks,
            lastRenderedMessage,
            runId,
            options,
        );
    }

    applyPendingApprovalsToHistory(container, pendingToolApprovals, runId);
    if (shouldCollapseIntermediateMessages(filteredOverlayEntry, options)) {
        collapseIntermediateMessages(container);
    }
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

function applyTimelineAction() {
    globalThis.__relayTeamsMessageTimelineApplyAction?.(...arguments);
}

function collectHydratedParts(groups, scope, parts) {
    const safeRunId = String(scope.runId || '').trim();
    if (!safeRunId) return;
    const streamKey = String(scope.streamKey || 'primary').trim();
    const groupKey = `${safeRunId}::${streamKey}::${scope.view || 'history'}`;
    let entry = groups.get(groupKey);
    if (!entry) {
        entry = {
            scope: {
                runId: safeRunId,
                instanceId: String(scope.instanceId || '').trim(),
                roleId: String(scope.roleId || '').trim(),
                streamKey,
                view: String(scope.view || (safeRunId.startsWith('subagent_run_') ? 'normal-child-session' : 'main')).trim(),
            },
            parts: [],
        };
        groups.set(groupKey, entry);
    }
    (Array.isArray(parts) ? parts : []).forEach((part, index) => {
        const normalized = normalizeHistoryPart(part, index);
        if (normalized) {
            entry.parts.push(normalized);
        }
    });
}

function normalizeHistoryPart(part, index) {
    if (!part || typeof part !== 'object') return null;
    const kind = String(part.part_kind || part.kind || '').trim();
    if (kind === 'text') {
        return {
            kind: 'text',
            content: String(part.content || part.text || ''),
            streaming: false,
            part_index: index,
        };
    }
    if (kind === 'thinking') {
        return {
            kind: 'thinking',
            content: String(part.content || ''),
            part_index: part.part_index ?? index,
            streaming: false,
            finished: true,
        };
    }
    if (kind === 'tool-call' || (part.tool_name && part.args !== undefined)) {
        return {
            kind: 'tool',
            tool_name: String(part.tool_name || 'unknown_tool'),
            tool_call_id: String(part.tool_call_id || ''),
            args: part.args || {},
            status: 'pending',
            part_index: index,
        };
    }
    if (kind === 'tool-return') {
        return {
            kind: 'tool',
            tool_name: String(part.tool_name || 'unknown_tool'),
            tool_call_id: String(part.tool_call_id || ''),
            result: part.content,
            status: 'completed',
            part_index: index,
        };
    }
    if (kind === 'file') {
        return null;
    }
    return null;
}

function buildPersistedOverlayIndex(historyMessages, runId) {
    const index = {
        toolCallIds: new Set(),
        thinkingTailByStream: new Map(),
        textTailByStream: new Map(),
    };
    (Array.isArray(historyMessages) ? historyMessages : []).forEach(msgItem => {
        const parts = Array.isArray(msgItem?.message?.parts)
            ? msgItem.message.parts
            : [];
        const streamKey = resolveHistoryStreamKey(
            runId,
            msgItem?.instance_id,
            msgItem?.role_id,
        );
        const messageThinkingText = new Set();
        const messageText = new Set();
        parts.forEach(part => {
            if (!part || typeof part !== 'object') return;
            const kind = String(part.part_kind || part.kind || '').trim();
            const toolCallId = String(part.tool_call_id || '').trim();
            if ((kind === 'tool-call' || kind === 'tool-return' || part.tool_name) && toolCallId) {
                index.toolCallIds.add(toolCallId);
            }
            if (kind === 'thinking') {
                const text = normalizeOverlayTextSignature(part.content);
                if (text) messageThinkingText.add(text);
            }
            if (kind === 'text') {
                const text = normalizeOverlayTextSignature(part.content || part.text);
                if (text) messageText.add(text);
            }
        });
        if (messageThinkingText.size > 0) {
            index.thinkingTailByStream.set(streamKey, messageThinkingText);
        }
        if (messageText.size > 0) {
            index.textTailByStream.set(streamKey, messageText);
        }
    });
    return index;
}

function filterPersistedOverlayParts(streamOverlayEntry, persistedIndex, runId) {
    if (!streamOverlayEntry || typeof streamOverlayEntry !== 'object') {
        return null;
    }
    const parts = Array.isArray(streamOverlayEntry.parts)
        ? streamOverlayEntry.parts
        : [];
    const streamKey = String(streamOverlayEntry.streamKey || '').trim()
        || resolveHistoryStreamKey(
            runId,
            streamOverlayEntry.instanceId,
            streamOverlayEntry.roleId,
        );
    const emptySet = new Set();
    const persistedThinkingText = persistedIndex.thinkingTailByStream.get(streamKey) || emptySet;
    const persistedText = persistedIndex.textTailByStream.get(streamKey) || emptySet;
    const filteredParts = parts.filter(part => {
        if (!part || typeof part !== 'object') return false;
        if (part.kind === 'tool') {
            const toolCallId = String(part.tool_call_id || '').trim();
            return !(toolCallId && persistedIndex.toolCallIds.has(toolCallId));
        }
        if (part.kind === 'thinking') {
            const text = normalizeOverlayTextSignature(part.content);
            return !(text && persistedThinkingText.has(text));
        }
        if (part.kind === 'text') {
            const text = normalizeOverlayTextSignature(part.content || part.text);
            return !(text && persistedText.has(text));
        }
        return true;
    });
    const hasRenderableState = filteredParts.length > 0
        || streamOverlayEntry.textStreaming === true
        || streamOverlayEntry.idleCursor === true;
    if (!hasRenderableState) {
        return null;
    }
    return {
        ...streamOverlayEntry,
        parts: filteredParts,
    };
}

function normalizeOverlayTextSignature(value) {
    return String(value || '').replace(/\s+/g, ' ').trim();
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
    options = {},
) {
    const label = streamOverlayEntry.label
        || labelFromRole('assistant', streamOverlayEntry.roleId, streamOverlayEntry.instanceId);
    const contentEl = resolveOverlayContentTarget(
        container,
        label,
        streamOverlayEntry,
        lastRenderedMessage,
        runId,
        options,
    );
    let combinedText = '';
    let renderedLiveTextTail = false;
    const overlayParts = Array.isArray(streamOverlayEntry.parts) ? streamOverlayEntry.parts : [];
    const overlayRunId = runId || lastRenderedMessage?.runId || '';
    const overlayStreamKey = resolveHistoryStreamKey(
        overlayRunId,
        streamOverlayEntry?.instanceId,
        streamOverlayEntry?.roleId,
    );
    const hasLiveTextTail = streamOverlayEntry.textStreaming === true;
    const hasIdleCursor = streamOverlayEntry.idleCursor === true;
    const trailingTextPart = [...overlayParts]
        .reverse()
        .find(part => part && typeof part === 'object' && part.kind === 'text');
    const flushText = (streaming = false) => {
        const safeText = String(combinedText || '');
        if (!safeText && !streaming) return;
        if (!safeText.trim() && !streaming) return;
        appendMessageText(contentEl, streaming ? safeText : safeText.trim(), { streaming });
        if (streaming) {
            renderedLiveTextTail = true;
        }
        combinedText = '';
    };

    overlayParts.forEach(part => {
        if (!part || typeof part !== 'object') return;
        if (part.kind === 'text') {
            combinedText += String(part.content || '');
            return;
        }
        if (part.kind === 'media_ref') {
            flushText(false);
            appendStructuredContentPart(contentEl, part);
            return;
        }
        if (part.kind === 'thinking') {
            flushText(false);
            appendThinkingText(contentEl, String(part.content || ''), {
                partIndex: part._key ?? part.part_index ?? '',
                streaming: part.finished !== true,
                runId: overlayRunId,
                instanceId: String(streamOverlayEntry?.instanceId || '').trim(),
                streamKey: overlayStreamKey,
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

    flushText(hasLiveTextTail && !!trailingTextPart);
    if ((hasLiveTextTail || hasIdleCursor) && !renderedLiveTextTail) {
        const liveTail = appendMessageText(contentEl, '', { streaming: true });
        if (hasIdleCursor && liveTail) {
            if (liveTail.dataset) {
                liveTail.dataset.idleCursor = 'true';
            }
            liveTail.__idleCursor = true;
        }
    }
}

function resolveOverlayContentTarget(
    container,
    label,
    streamOverlayEntry,
    lastRenderedMessage,
    runId = '',
    options = {},
) {
    const safeLabel = String(label || '').trim();
    if (options.separateOverlayMessage === true) {
        return renderMessageBlock(container, 'assistant', label, [], {
            runId: runId || lastRenderedMessage?.runId || '',
            roleId: String(streamOverlayEntry?.roleId || '').trim(),
            instanceId: String(streamOverlayEntry?.instanceId || '').trim(),
            streamKey: resolveHistoryStreamKey(
                runId || lastRenderedMessage?.runId || '',
                streamOverlayEntry?.instanceId,
                streamOverlayEntry?.roleId,
            ),
        }).contentEl;
    }
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

    if (isApprovedApprovalStatus(part.approvalStatus) && part.result === undefined) {
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

function collapseIntermediateMessages(container) {
    if (!container) return;
    const messages = Array.from(container.querySelectorAll(':scope > .message'));
    if (messages.length === 0) return;

    const last = messages[messages.length - 1];

    // Everything before the last message is intermediate (coordinator_messages
    // do not contain the user prompt; that lives in the round header intent).
    const beforeLast = messages.slice(0, -1);

    // Also lift thinking and tool blocks out of the final message so only
    // the plain text reply remains visible.
    const lastContent = last.querySelector('.msg-content');
    const liftedFromLast = [];
    if (lastContent) {
        Array.from(lastContent.children).forEach(child => {
            if (child.classList.contains('thinking-block') || child.classList.contains('tool-block')) {
                liftedFromLast.push(child);
            }
        });
    }

    if (beforeLast.length === 0 && liftedFromLast.length === 0) return;

    // Compute elapsed duration from round start (user sent message) to last
    // coordinator message. Round start comes from the section's dataset
    // (set by renderRoundSection); last message time from raw data stored
    // by renderHistoricalMessageList (covers non-rendered tool-return messages).
    const firstTime = Date.parse(container.dataset.roundCreatedAt || messages[0].dataset.createdAt || '');
    const lastTime = Date.parse(container.dataset.roundLastMessageAt || last.dataset.createdAt || '');
    const durationText = Number.isFinite(firstTime) && Number.isFinite(lastTime) && lastTime > firstTime
        ? formatElapsed(lastTime - firstTime)
        : '';
    const durationSuffix = durationText ? ` (${durationText})` : '';
    const label = formatMessage('tool.group.processed', { duration: durationSuffix }).trim();

    const group = document.createElement('details');
    group.className = 'tool-group';
    group.innerHTML = `
        <summary class="tool-group-summary">
            <span class="tool-group-line" aria-hidden="true"></span>
            <span class="tool-group-label">${label}</span>
            <span class="tool-group-toggle" aria-hidden="true">></span>
            <span class="tool-group-line" aria-hidden="true"></span>
        </summary>
    `;
    const body = document.createElement('div');
    body.className = 'tool-group-body';
    group.appendChild(body);

    // Collect all sibling nodes from the first message up to (but not
    // including) the last message, preserving markers and dividers.
    container.insertBefore(group, beforeLast[0] || last);
    let node = group.nextElementSibling;
    while (node && node !== last) {
        const next = node.nextElementSibling;
        body.appendChild(node);
        node = next;
    }
    liftedFromLast.forEach(el => body.appendChild(el));

    // If the last message has no remaining visible content after lifting,
    // hide it so only the collapsed group is shown.
    if (lastContent && lastContent.childNodes.length === 0) {
        last.hidden = true;
    }

    // Animate open / close via Web Animations API.
    group.addEventListener('click', (e) => {
        if (!e.target.closest('.tool-group-summary')) return;
        e.preventDefault();
        if (group.open) {
            body.animate(
                [
                    { opacity: 1, maxHeight: body.scrollHeight + 'px' },
                    { opacity: 0, maxHeight: '0px' },
                ],
                { duration: 180, easing: 'ease' },
            ).onfinish = () => { group.open = false; };
        } else {
            group.open = true;
            body.animate(
                [
                    { opacity: 0, maxHeight: '0px' },
                    { opacity: 1, maxHeight: body.scrollHeight + 'px' },
                ],
                { duration: 200, easing: 'ease' },
            );
        }
    });
}

function shouldCollapseIntermediateMessages(streamOverlayEntry, options = {}) {
    const runStatus = String(options.runStatus || '').trim().toLowerCase();
    const isLatestRound = options.isLatestRound === true;
    if (isLatestRound && runStatus !== 'completed') {
        return false;
    }
    if (!streamOverlayEntry || typeof streamOverlayEntry !== 'object') {
        return true;
    }
    if (streamOverlayEntry.textStreaming === true) {
        return false;
    }
    if (streamOverlayEntry.idleCursor === true) {
        return false;
    }
    const parts = Array.isArray(streamOverlayEntry.parts) ? streamOverlayEntry.parts : [];
    return !parts.some(part => {
        if (!part || typeof part !== 'object') return false;
        if (part.kind === 'thinking') {
            return part.finished !== true;
        }
        if (part.kind !== 'tool') {
            return false;
        }
        const status = String(part.status || '').trim().toLowerCase();
        const approvalStatus = String(part.approvalStatus || '').trim().toLowerCase();
        return (
            status === 'pending'
            || status === 'running'
            || approvalStatus === 'requested'
            || isApprovedApprovalStatus(approvalStatus)
            || (part.result === undefined && part.validation === undefined)
        );
    });
}
function isApprovedApprovalStatus(value) {
    const approvalStatus = String(value || '').trim().toLowerCase();
    return (
        approvalStatus === 'approve'
        || approvalStatus === 'approve_once'
        || approvalStatus === 'approve_exact'
        || approvalStatus === 'approve_prefix'
    );
}
function formatElapsed(ms) {
    const totalSeconds = Math.round(ms / 1000);
    if (totalSeconds < 60) return `${totalSeconds}s`;
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    if (minutes < 60) return seconds > 0 ? `${minutes}m ${seconds}s` : `${minutes}m`;
    const hours = Math.floor(minutes / 60);
    const remainMinutes = minutes % 60;
    return remainMinutes > 0 ? `${hours}h ${remainMinutes}m` : `${hours}h`;
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
