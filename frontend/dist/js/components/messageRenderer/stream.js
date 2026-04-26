/**
 * components/messageRenderer/stream.js
 * Streaming message mutation helpers plus a durable in-browser overlay cache.
 */
import {
    getRunPrimaryRoleId,
    isPrimaryRoleId,
} from '../../core/state.js';
import {
    applyToolReturn,
    appendStructuredContentPart,
    appendThinkingText,
    buildPendingToolBlock,
    findToolBlock,
    findToolBlockInContainer,
    indexPendingToolBlock,
    renderMessageBlock,
    resolvePendingToolBlock,
    setToolStatus,
    setToolValidationFailureState,
    syncStreamingCursor,
    updateThinkingText,
    updateMessageText,
} from './helpers.js';
import { formatMessage, t } from '../../utils/i18n.js';

const streamState = new Map();
const overlayState = new Map();
const overlaySeenEventIdsByRun = new Map();
const overlayCleanupTimers = new Map();
const PRIMARY_KEY = 'primary';
const pendingTextUpdates = new Map();
const pendingScrollContainers = new Map();
const streamFollowState = new WeakMap();
const LARGE_STREAM_TEXT_THRESHOLD = 12000;
const BOTTOM_FOLLOW_THRESHOLD_PX = 96;
const STREAM_USER_SCROLL_LOCK_MS = 1800;
const MAX_OVERLAY_SEEN_EVENT_IDS_PER_RUN = 2000;
let pendingTextFrame = 0;
let pendingScrollFrame = 0;

export function getOrCreateStreamBlock(
    container,
    instanceId,
    roleId,
    label,
    runId = '',
) {
    const streamKey = resolveStreamKey(instanceId, roleId, runId);
    const stateKey = resolveStreamStateKey(instanceId, roleId, runId);
    let st = streamState.get(stateKey);
    if (st && isStreamStateDetachedFromContainer(st, container)) {
        streamState.delete(stateKey);
        st = null;
    }
    if (!st || st.container !== container) {
        st = createStreamState({
            container,
            instanceId,
            roleId,
            label,
            runId,
        });
        streamState.set(stateKey, st);
    } else {
        if (!st.thinkingParts) st.thinkingParts = new Map();
        if (!st.thinkingActiveByPart) st.thinkingActiveByPart = new Map();
        if (!st.pendingToolBlocks) st.pendingToolBlocks = {};
        if (typeof st.thinkingSequence !== 'number') st.thinkingSequence = 0;
        if (typeof st.activeRaw !== 'string') st.activeRaw = '';
    }
    ensureOverlayEntry(st.runId, st.instanceId, roleId, label);
    return st;
}

export function appendStreamChunk(instanceId, text, runId = '', roleId = '', label = '') {
    const stateKey = resolveStreamStateKey(instanceId, roleId, runId);
    const st = streamState.get(stateKey);
    if (!st) return;
    const follow = captureStreamFollow(st.container);

    if (!st.activeTextEl) {
        st.activeTextEl = document.createElement('div');
        st.activeTextEl.className = 'msg-text';
        st.contentEl.appendChild(st.activeTextEl);
        st.activeRaw = '';
    }

    st.raw += text;
    st.activeRaw += text;
    st.activeTextIsIdle = false;
    if (shouldAppendPlainTextDelta(st.activeTextEl)) {
        pendingTextUpdates.delete(st.activeTextEl);
        updateMessageText(st.activeTextEl, String(text || ''), {
            streaming: true,
            appendDelta: true,
        });
    } else if (st.activeRaw.length >= LARGE_STREAM_TEXT_THRESHOLD) {
        pendingTextUpdates.delete(st.activeTextEl);
        updateMessageText(st.activeTextEl, st.activeRaw, { streaming: true });
    } else {
        scheduleRichTextUpdate(st.activeTextEl, st.activeRaw, { streaming: true }, updateMessageText);
    }
    markIdleCursorPlaceholder(st.activeTextEl, false);
    applyTimelineAction({
        type: 'text_delta',
        scope: streamScope(st, {
            runId,
            instanceId,
            roleId,
        }),
        text,
    });
    updateOverlayText(st.runId || runId, st.instanceId || instanceId, roleId || st.roleId, label || st.label, text);
    setOverlayTextStreaming(
        st.runId || runId,
        st.instanceId || instanceId,
        roleId || st.roleId,
        label || st.label,
        true,
    );
    setOverlayIdleCursor(
        st.runId || runId,
        st.instanceId || instanceId,
        roleId || st.roleId,
        label || st.label,
        false,
    );
    scheduleStreamScrollBottom(st.container, follow);
}

export function appendStreamOutputParts(
    instanceId,
    outputParts,
    options = {},
) {
    const runId = String(options.runId || '');
    const roleId = String(options.roleId || '');
    const label = String(options.label || '');
    const container = options.container || null;
    const streamKey = resolveStreamKey(instanceId, roleId, runId);
    const stateKey = resolveStreamStateKey(instanceId, roleId, runId);
    let st = streamState.get(stateKey);
    if (st && isStreamStateDetachedFromContainer(st, container || st.container)) {
        streamState.delete(stateKey);
        st = null;
    }
    if (!st && container) {
        st = createStreamState({
            container,
            instanceId,
            roleId,
            label: label || 'Agent',
            runId,
        });
        streamState.set(stateKey, st);
    }
    if (!st || !Array.isArray(outputParts)) return;
    const follow = captureStreamFollow(st.container || container);
    appendOverlayOutputParts(
        runId || st.runId,
        instanceId || st.instanceId,
        roleId || st.roleId,
        label || st.label,
        outputParts,
        { includeText: false },
    );
    applyTimelineAction({
        type: 'output_parts',
        scope: streamScope(st, {
            runId,
            instanceId,
            roleId,
        }),
        parts: outputParts.filter(part => part?.kind !== 'text'),
    });
    outputParts.forEach(part => {
        if (!part || typeof part !== 'object') return;
        if (part.kind === 'text') {
            appendStreamChunk(
                instanceId,
                String(part.text || ''),
                runId || st.runId,
                roleId || st.roleId,
                label || st.label,
            );
            return;
        }
        endActiveText(st);
        appendStructuredContentPart(st.contentEl, part);
    });
    scheduleStreamScrollBottom(st.container || container, follow);
}

export function finalizeStream(instanceId, roleId = '', options = {}) {
    const runId = String(options.runId || '').trim();
    const streamKey = resolveStreamKey(instanceId, roleId, runId);
    const stateKey = resolveStreamStateKey(instanceId, roleId, runId);
    const matchedEntries = [];
    const direct = streamState.get(stateKey);
    if (direct) {
        matchedEntries.push([stateKey, direct]);
    } else if (runId) {
        Array.from(streamState.entries()).forEach(([key, entry]) => {
            if (!entry || String(entry.runId || '').trim() !== runId) {
                return;
            }
            if (
                matchesFinalizeTarget(entry, {
                    instanceId,
                    roleId,
                    streamKey,
                })
            ) {
                matchedEntries.push([key, entry]);
            }
        });
    }

    matchedEntries.forEach(([key, entry]) => {
        finalizeStreamEntry(entry);
        streamState.delete(key);
    });

    const overlayRunId = String(
        matchedEntries[0]?.[1]?.runId || runId || '',
    ).trim();
    const overlayInstanceId = String(
        matchedEntries[0]?.[1]?.instanceId || instanceId || '',
    ).trim();
    const overlayRoleId = String(
        matchedEntries[0]?.[1]?.roleId || roleId || '',
    ).trim();
    if (overlayRunId) {
        setOverlayTextStreaming(overlayRunId, overlayInstanceId, overlayRoleId, '', false);
        setOverlayIdleCursor(overlayRunId, overlayInstanceId, overlayRoleId, '', false);
    }
}

export function bindStreamOverlayToContainer(
    container,
    {
        instanceId = '',
        roleId = '',
        label = '',
        runId = '',
    } = {},
) {
    const safeRunId = String(runId || '').trim();
    if (!container || !safeRunId) {
        return null;
    }
    const streamKey = resolveStreamKey(instanceId, roleId, safeRunId);
    const stateKey = resolveStreamStateKey(instanceId, roleId, safeRunId);
    let existing = streamState.get(stateKey);
    if (existing && isStreamStateDetachedFromContainer(existing, container)) {
        streamState.delete(stateKey);
        existing = null;
    }
    if (existing && existing.container === container) {
        return existing;
    }
    const overlayEntry = resolveOverlayEntry(safeRunId, instanceId, roleId, label);
    if (!overlayEntry) {
        return null;
    }
    const reused = findReusableStreamState({
        container,
        instanceId,
        roleId,
        label: String(label || overlayEntry.label || '').trim(),
        runId: safeRunId,
    });
    if (!reused) {
        return null;
    }
    streamState.set(stateKey, reused);
    return reused;
}

export function clearStreamState(instanceId, roleId = '', runId = '') {
    const safeRunId = String(runId || '').trim();
    const streamKey = resolveStreamKey(instanceId, roleId, safeRunId);
    const stateKey = resolveStreamStateKey(instanceId, roleId, safeRunId);
    const entries = safeRunId
        ? [[stateKey, streamState.get(stateKey)]]
        : Array.from(streamState.entries()).filter(([, entry]) => (
            String(entry?.streamKey || '').trim() === streamKey
        ));
    entries.forEach(([key, entry]) => {
        if (!entry) return;
        if (entry.activeTextEl) {
            syncStreamingCursor(entry.activeTextEl, false);
        }
        streamState.delete(key);
    });
}

function clearPendingStreamWork() {
    if (pendingTextFrame && typeof window !== 'undefined' && typeof window.cancelAnimationFrame === 'function') {
        window.cancelAnimationFrame(pendingTextFrame);
    }
    if (pendingScrollFrame && typeof window !== 'undefined' && typeof window.cancelAnimationFrame === 'function') {
        window.cancelAnimationFrame(pendingScrollFrame);
    }
    pendingTextFrame = 0;
    pendingScrollFrame = 0;
    pendingTextUpdates.clear();
    pendingScrollContainers.clear();
}

export function clearRunStreamState(runId) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) return;
    clearRunOverlayCleanupTimer(safeRunId);
    overlayState.delete(safeRunId);
    overlaySeenEventIdsByRun.delete(safeRunId);
    clearTimelineRun(safeRunId);
    Array.from(streamState.entries()).forEach(([key, entry]) => {
        if (entry.runId === safeRunId) {
            if (entry.activeTextEl) {
                flushRichTextUpdate(entry.activeTextEl);
                syncStreamingCursor(entry.activeTextEl, false);
            }
            if (entry.thinkingParts instanceof Map) {
                entry.thinkingParts.forEach(thinkingEntry => {
                    flushRichTextUpdate(thinkingEntry.textEl);
                });
            }
            streamState.delete(key);
        }
    });
}

export function clearStreamOverlayEntry(runId, instanceId = '', roleId = '') {
    clearOverlayEntry(runId, instanceId, roleId);
}

if (typeof globalThis !== 'undefined') {
    globalThis.__relayTeamsClearStreamOverlayEntry = clearStreamOverlayEntry;
}

export function clearRunRenderedStreamState(runId) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) return;
    Array.from(streamState.entries()).forEach(([key, entry]) => {
        if (entry.runId === safeRunId) {
            if (entry.activeTextEl) {
                flushRichTextUpdate(entry.activeTextEl);
                syncStreamingCursor(entry.activeTextEl, false);
            }
            if (entry.thinkingParts instanceof Map) {
                entry.thinkingParts.forEach(thinkingEntry => {
                    flushRichTextUpdate(thinkingEntry.textEl);
                });
            }
            streamState.delete(key);
        }
    });
}

export function clearRenderedStreamState() {
    streamState.forEach(entry => {
        if (entry?.activeTextEl) {
            flushRichTextUpdate(entry.activeTextEl);
            syncStreamingCursor(entry.activeTextEl, false);
        }
    });
    streamState.clear();
    clearPendingStreamWork();
}

export function clearAllStreamState(options = {}) {
    clearRenderedStreamState();
    if (options?.preserveOverlay === true) {
        return;
    }
    overlayCleanupTimers.forEach(timerId => {
        clearTimeout(timerId);
    });
    overlayCleanupTimers.clear();
    overlayState.clear();
    overlaySeenEventIdsByRun.clear();
    clearTimelineState();
}

export function getRunStreamOverlaySnapshot(runId) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) {
        return { coordinator: null, byInstance: {} };
    }
    const runOverlay = overlayState.get(safeRunId);
    if (!runOverlay) {
        return { coordinator: null, byInstance: {} };
    }
    const coordinator = cloneOverlayEntry(runOverlay.entries.get(PRIMARY_KEY) || null);
    const byInstance = {};
    runOverlay.entries.forEach((entry, key) => {
        if (key === PRIMARY_KEY) return;
        if (!entry.instanceId) return;
        byInstance[entry.instanceId] = cloneOverlayEntry(entry);
    });
    return { coordinator, byInstance };
}

export function getCoordinatorStreamOverlay(runId) {
    return getRunStreamOverlaySnapshot(runId).coordinator;
}

export function getInstanceStreamOverlay(runId, instanceId) {
    const snapshot = getRunStreamOverlaySnapshot(runId);
    return snapshot.byInstance[String(instanceId || '')] || null;
}

export function appendToolCallBlock(
    container,
    instanceId,
    toolName,
    args,
    toolCallId = null,
    options = {},
) {
    const runId = String(options.runId || '');
    const roleId = String(options.roleId || '');
    const label = String(options.label || '');
    const streamKey = resolveStreamKey(instanceId, roleId, runId);
    const stateKey = resolveStreamStateKey(instanceId, roleId, runId);
    let st = streamState.get(stateKey);
    if (st && isStreamStateDetachedFromContainer(st, container)) {
        streamState.delete(stateKey);
        st = null;
    }
    if (!st) {
        const actorLabel = label || (toolName ? 'Tool' : 'Agent');
        st = createStreamState({
            container,
            instanceId,
            roleId,
            label: actorLabel,
            runId,
        });
        streamState.set(stateKey, st);
    } else {
        if (!st.thinkingParts) st.thinkingParts = new Map();
        if (!st.thinkingActiveByPart) st.thinkingActiveByPart = new Map();
        if (!st.pendingToolBlocks) st.pendingToolBlocks = {};
        if (typeof st.thinkingSequence !== 'number') st.thinkingSequence = 0;
        if (typeof st.activeRaw !== 'string') st.activeRaw = '';
    }

    const follow = captureStreamFollow(st.container || container);
    endActiveText(st);

    const toolBlock = buildPendingToolBlock(toolName, args, toolCallId);
    st.contentEl.appendChild(toolBlock);
    bindHeightObserver(st.container || container, toolBlock);
    indexPendingToolBlock(st.pendingToolBlocks, toolBlock, toolName, toolCallId);
    updateOverlayToolCall(st.runId || runId, st.instanceId || instanceId, roleId || st.roleId, st.label, {
        tool_call_id: toolCallId || '',
        tool_name: toolName,
        args,
        status: 'pending',
    });
    applyTimelineAction({
        type: 'tool_call',
        scope: streamScope(st, {
            runId,
            instanceId,
            roleId,
        }),
        toolCallId,
        toolName,
        args,
    });
    scheduleStreamScrollBottom(st.container || container, follow);
    return toolBlock;
}

export function updateToolResult(
    instanceId,
    toolName,
    result,
    isError,
    toolCallId = null,
    options = {},
) {
    const runId = String(options.runId || '');
    const roleId = String(options.roleId || '');
    const label = String(options.label || '');
    const container = options.container || null;
    const streamKey = resolveStreamKey(instanceId, roleId, runId);
    const stateKey = resolveStreamStateKey(instanceId, roleId, runId);
    let st = streamState.get(stateKey);
    if (st && isStreamStateDetachedFromContainer(st, container || st.container)) {
        streamState.delete(stateKey);
        st = null;
    }
    const resolvedRunId = (st && st.runId) || runId;
    const resolvedInstanceId = (st && st.instanceId) || instanceId;
    const resolvedRoleId = (st && st.roleId) || roleId;
    updateOverlayToolResult(
        resolvedRunId,
        resolvedInstanceId,
        resolvedRoleId,
        label,
        toolName,
        toolCallId,
        result,
        isError,
    );
    applyTimelineAction({
        type: 'tool_result',
        scope: streamScope(st, {
            runId: resolvedRunId,
            instanceId: resolvedInstanceId,
            roleId: resolvedRoleId,
        }),
        toolName,
        toolCallId,
        result,
        isError,
    });
    const follow = captureStreamFollow((st && st.container) || container);
    let toolBlock = resolveToolBlockTarget(st, container, toolName, toolCallId);
    let boundState = st;
    if (!toolBlock) {
        const materialized = materializeToolBlockFromOverlay({
            container,
            runId: resolvedRunId,
            instanceId: resolvedInstanceId,
            roleId: resolvedRoleId,
            label,
            toolName,
            toolCallId,
        });
        if (!materialized) {
            return;
        }
        toolBlock = materialized.toolBlock;
        boundState = materialized.streamState;
    }
    applyToolReturn(toolBlock, result);
    if (boundState && !hasActiveThinking(boundState)) {
        ensureIdleStreamingTail(boundState);
    }
    scheduleStreamScrollBottom((boundState && boundState.container) || container, follow);
}

export function markToolInputValidationFailed(instanceId, payload, options = {}) {
    const runId = String(options.runId || '');
    const roleId = String(options.roleId || '');
    const container = options.container || null;
    const streamKey = resolveStreamKey(instanceId, roleId, runId);
    const stateKey = resolveStreamStateKey(instanceId, roleId, runId);
    let st = streamState.get(stateKey);
    if (st && isStreamStateDetachedFromContainer(st, container || st.container)) {
        streamState.delete(stateKey);
        st = null;
    }
    const toolBlock = resolveToolBlockTarget(
        st,
        container,
        payload?.tool_name,
        payload?.tool_call_id || null,
    );
    if (!toolBlock) {
        updateOverlayToolValidation(runId, instanceId, roleId, payload);
        return false;
    }

    const follow = captureStreamFollow((st && st.container) || container);
    setToolValidationFailureState(toolBlock, payload);
    updateOverlayToolValidation(st.runId || runId, st.instanceId || instanceId, roleId || st.roleId, payload);
    applyTimelineAction({
        type: 'tool_input_validation_failed',
        scope: streamScope(st, {
            runId,
            instanceId,
            roleId,
        }),
        toolName: payload?.tool_name,
        toolCallId: payload?.tool_call_id || null,
        validation: payload,
    });
    scheduleStreamScrollBottom((st && st.container) || container, follow);
    return true;
}

export function startThinkingBlock(instanceId, partIndex, options = {}) {
    const runId = String(options.runId || '');
    const roleId = String(options.roleId || '');
    const label = String(options.label || '');
    const container = options.container || null;
    const streamKey = resolveStreamKey(instanceId, roleId, runId);
    const stateKey = resolveStreamStateKey(instanceId, roleId, runId);
    let st = streamState.get(stateKey);
    if (st && isStreamStateDetachedFromContainer(st, container || st.container)) {
        streamState.delete(stateKey);
        st = null;
    }
    if (!st && container) {
        const actorLabel = label || 'Agent';
        st = createStreamState({
            container,
            instanceId,
            roleId,
            label: actorLabel,
            runId,
        });
        streamState.set(stateKey, st);
    } else if (st) {
        if (!st.thinkingParts) st.thinkingParts = new Map();
        if (!st.thinkingActiveByPart) st.thinkingActiveByPart = new Map();
        if (!st.pendingToolBlocks) st.pendingToolBlocks = {};
        if (typeof st.thinkingSequence !== 'number') st.thinkingSequence = 0;
        if (typeof st.activeRaw !== 'string') st.activeRaw = '';
    }
    if (!st) return false;
    const follow = captureStreamFollow(st.container || container);
    endActiveText(st);
    ensureThinkingEntry(st, partIndex, { forceNew: true });
    startOverlayThinking(st.runId || runId, st.instanceId || instanceId, roleId || st.roleId, st.label || label, partIndex);
    applyTimelineAction({
        type: 'thinking_started',
        scope: streamScope(st, {
            runId,
            instanceId,
            roleId,
        }),
        partIndex,
    });
    scheduleStreamScrollBottom(st.container || container, follow);
    return true;
}

export function appendThinkingChunk(instanceId, partIndex, text, options = {}) {
    const runId = String(options.runId || '');
    const roleId = String(options.roleId || '');
    const label = String(options.label || '');
    const container = options.container || null;
    const streamKey = resolveStreamKey(instanceId, roleId, runId);
    const stateKey = resolveStreamStateKey(instanceId, roleId, runId);
    let st = streamState.get(stateKey);
    if (st && isStreamStateDetachedFromContainer(st, container || st.container)) {
        streamState.delete(stateKey);
        st = null;
        if (container) {
            st = createStreamState({
                container,
                instanceId,
                roleId,
                label: label || 'Agent',
                runId,
            });
            streamState.set(stateKey, st);
        }
    }
    if (!st) {
        updateOverlayThinkingText(runId, instanceId, roleId, label, partIndex, text, { append: true });
        return false;
    }
    const follow = captureStreamFollow((st && st.container) || container);
    const entry = resolveThinkingEntry(st, partIndex);
    const delta = String(text || '');
    entry.raw += delta;
    updateThinkingText(entry.textEl, delta, {
        streaming: true,
        runId: st.runId || runId,
        instanceId: st.instanceId || instanceId,
        streamKey: st.streamKey,
        partIndex: entry.key,
        appendDelta: true,
    });
    applyTimelineAction({
        type: 'thinking_delta',
        scope: streamScope(st, {
            runId,
            instanceId,
            roleId,
        }),
        partIndex,
        text,
    });
    updateOverlayThinkingText(
        st.runId || runId,
        st.instanceId || instanceId,
        roleId || st.roleId,
        st.label || label,
        partIndex,
        delta,
        { append: true },
    );
    scheduleStreamScrollBottom((st && st.container) || container, follow);
    return true;
}

export function finalizeThinking(instanceId, partIndex, options = {}) {
    const runId = String(options.runId || '');
    const roleId = String(options.roleId || '');
    const streamKey = resolveStreamKey(instanceId, roleId, runId);
    const stateKey = resolveStreamStateKey(instanceId, roleId, runId);
    let st = streamState.get(stateKey);
    if (st && isStreamStateDetachedFromContainer(st, options.container || st.container)) {
        streamState.delete(stateKey);
        st = null;
    }
    const entry = resolveThinkingEntry(st, partIndex, { allowCreate: false });
    if (!entry) {
        finishOverlayThinking(runId, instanceId, roleId, partIndex);
        applyTimelineAction({
            type: 'thinking_finished',
            scope: {
                runId,
                instanceId,
                roleId,
            },
            partIndex,
        });
        return false;
    }
    const follow = captureStreamFollow(st && st.container);
    flushRichTextUpdate(entry.textEl);
    updateThinkingText(entry.textEl, entry.raw, {
        streaming: false,
        runId: st.runId || runId,
        instanceId: st.instanceId || instanceId,
        streamKey: st.streamKey,
        partIndex: entry.key,
    });
    entry.finished = true;
    if (st?.thinkingActiveByPart) {
        st.thinkingActiveByPart.delete(String(partIndex));
    }
    finishOverlayThinking((st && st.runId) || runId, (st && st.instanceId) || instanceId, (st && st.roleId) || roleId, partIndex);
    applyTimelineAction({
        type: 'thinking_finished',
        scope: streamScope(st, {
            runId,
            instanceId,
            roleId,
        }),
        partIndex,
    });
    if (st && !hasActiveThinking(st)) {
        ensureIdleStreamingTail(st);
    }
    scheduleStreamScrollBottom(st && st.container, follow);
    return true;
}

export function attachToolApprovalControls(instanceId, toolName, payload, handlers, options = {}) {
    const runId = String(options.runId || '');
    const roleId = String(options.roleId || '');
    const container = options.container || null;
    const streamKey = resolveStreamKey(instanceId, roleId, runId);
    const stateKey = resolveStreamStateKey(instanceId, roleId, runId);
    let st = streamState.get(stateKey);
    if (st && isStreamStateDetachedFromContainer(st, container || st.container)) {
        streamState.delete(stateKey);
        st = null;
    }
    const toolBlock = resolveToolBlockTarget(
        st,
        container,
        toolName,
        payload?.tool_call_id || null,
    );
    if (!toolBlock) {
        updateOverlayToolApproval(runId, instanceId, roleId, toolName, payload, 'requested');
        return false;
    }
    if (payload?.tool_call_id) {
        toolBlock.dataset.toolCallId = payload.tool_call_id;
    }

    const follow = captureStreamFollow((st && st.container) || container);
    const approvalEl = ensureApprovalState(toolBlock);

    toolBlock.open = true;

    const stateEl = approvalEl.querySelector('.tool-approval-state');
    if (stateEl) stateEl.textContent = t('stream.approval_required');

    updateOverlayToolApproval(st.runId || runId, st.instanceId || instanceId, roleId || st.roleId, toolName, payload, 'requested');
    applyTimelineAction({
        type: 'tool_approval_requested',
        scope: streamScope(st, {
            runId,
            instanceId,
            roleId,
        }),
        toolName,
        toolCallId: payload?.tool_call_id || null,
        payload,
    });
    scheduleStreamScrollBottom((st && st.container) || container, follow);
    return true;
}

export function markToolApprovalResolved(instanceId, payload, options = {}) {
    const runId = String(options.runId || '');
    const roleId = String(options.roleId || '');
    const container = options.container || null;
    const streamKey = resolveStreamKey(instanceId, roleId, runId);
    const stateKey = resolveStreamStateKey(instanceId, roleId, runId);
    let st = streamState.get(stateKey);
    if (st && isStreamStateDetachedFromContainer(st, container || st.container)) {
        streamState.delete(stateKey);
        st = null;
    }
    updateOverlayToolApproval(
        (st && st.runId) || runId,
        (st && st.instanceId) || instanceId,
        (st && st.roleId) || roleId,
        payload?.tool_name,
        payload,
        String(payload?.action || '').toLowerCase() || 'resolved',
    );
    applyTimelineAction({
        type: 'tool_approval_resolved',
        scope: streamScope(st, {
            runId,
            instanceId,
            roleId,
        }),
        toolName: payload?.tool_name,
        toolCallId: payload?.tool_call_id || null,
        action: payload?.action || '',
        payload,
    });
    const toolCallId = payload?.tool_call_id;
    if (!toolCallId) return false;

    const follow = captureStreamFollow((st && st.container) || container);
    const toolBlock = resolveToolBlockTarget(st, container, payload?.tool_name, toolCallId);
    if (!toolBlock) return false;
    toolBlock.dataset.toolCallId = toolCallId;

    const approvalEl = ensureApprovalState(toolBlock);
    const action = String(payload.action || 'resolved').toUpperCase();
    const stateEl = approvalEl.querySelector('.tool-approval-state');
    if (stateEl) {
        stateEl.textContent = formatMessage('stream.approval_action', { action });
    }
    setToolStatus(
        toolBlock,
        String(payload.action || '').toLowerCase() === 'deny' ? 'warning' : 'running',
    );
    const outputEl = toolBlock.querySelector('.tool-output');
    if (outputEl) {
        outputEl.classList.remove('error-text');
        outputEl.classList.add('warning-text');
        if (String(payload.action || '').toLowerCase() === 'deny') {
            outputEl.innerHTML = t('stream.approval_denied');
        } else {
            outputEl.innerHTML = t('stream.approval_waiting');
        }
    }
    scheduleStreamScrollBottom((st && st.container) || container, follow);
    return true;
}

export function applyStreamOverlayEvent(evType, payload, options = {}) {
    const runId = String(options.runId || '').trim();
    if (!runId) return;
    if (isDuplicateOverlayEvent(runId, payload?.event_id || options.eventId)) {
        return;
    }
    const instanceId = String(options.instanceId || '').trim();
    const roleId = String(options.roleId || '').trim();
    const label = String(options.label || '').trim();
    const streamKey = resolveStreamKey(instanceId, roleId, runId);
    applyRunEventToTimeline(evType, payload, {
        event_id: payload?.event_id || options.eventId || '',
        run_id: runId,
        role_id: roleId,
        instance_id: instanceId,
    }, {
        runId,
        roleId,
        instanceId,
        streamKey,
        view: resolveTimelineView(runId, instanceId),
    });

    if (evType === 'text_delta') {
        clearOverlayEntryCleanupTimer(runId, streamKey);
        updateOverlayText(runId, streamKey, roleId, label, payload?.text || '');
        setOverlayIdleCursor(runId, streamKey, roleId, label, false);
        return;
    }
    if (evType === 'output_delta') {
        clearOverlayEntryCleanupTimer(runId, streamKey);
        const hasTextOutput = appendOverlayOutputParts(
            runId,
            streamKey,
            roleId,
            label,
            Array.isArray(payload?.output) ? payload.output : [],
            { includeText: true },
        );
        if (hasTextOutput) {
            setOverlayTextStreaming(runId, streamKey, roleId, label, true);
        }
        setOverlayIdleCursor(runId, streamKey, roleId, label, false);
        return;
    }
    if (evType === 'thinking_started') {
        clearOverlayEntryCleanupTimer(runId, streamKey);
        setOverlayTextStreaming(runId, streamKey, roleId, label, false);
        setOverlayIdleCursor(runId, streamKey, roleId, label, false);
        startOverlayThinking(runId, streamKey, roleId, label, payload?.part_index ?? 0);
        return;
    }
    if (evType === 'thinking_delta') {
        clearOverlayEntryCleanupTimer(runId, streamKey);
        updateOverlayThinkingText(
            runId,
            streamKey,
            roleId,
            label,
            payload?.part_index ?? 0,
            payload?.text || '',
            { append: true },
        );
        return;
    }
    if (evType === 'thinking_finished') {
        finishOverlayThinking(runId, streamKey, roleId, payload?.part_index ?? 0);
        setOverlayIdleCursor(runId, streamKey, roleId, label, true);
        return;
    }
    if (evType === 'tool_call') {
        clearOverlayEntryCleanupTimer(runId, streamKey);
        setOverlayTextStreaming(runId, streamKey, roleId, label, false);
        setOverlayIdleCursor(runId, streamKey, roleId, label, false);
        updateOverlayToolCall(runId, streamKey, roleId, label, {
            tool_call_id: payload?.tool_call_id || '',
            tool_name: payload?.tool_name || '',
            args: payload?.args || {},
            status: 'pending',
        });
        return;
    }
    if (evType === 'tool_result') {
        clearOverlayEntryCleanupTimer(runId, streamKey);
        const resultEnvelope = payload?.result || {};
        const isError = typeof resultEnvelope === 'object'
            ? resultEnvelope.ok === false
            : !!payload?.error;
        updateOverlayToolResult(
            runId,
            streamKey,
            roleId,
            label,
            payload?.tool_name || '',
            payload?.tool_call_id || null,
            resultEnvelope,
            isError,
        );
        setOverlayIdleCursor(runId, streamKey, roleId, label, true);
        return;
    }
    if (evType === 'tool_input_validation_failed') {
        clearOverlayEntryCleanupTimer(runId, streamKey);
        updateOverlayToolValidation(runId, streamKey, roleId, payload);
        return;
    }
    if (evType === 'tool_approval_requested') {
        clearOverlayEntryCleanupTimer(runId, streamKey);
        updateOverlayToolApproval(runId, streamKey, roleId, payload?.tool_name, payload, 'requested');
        return;
    }
    if (evType === 'tool_approval_resolved') {
        clearOverlayEntryCleanupTimer(runId, streamKey);
        updateOverlayToolApproval(
            runId,
            streamKey,
            roleId,
            payload?.tool_name,
            payload,
            String(payload?.action || '').toLowerCase() || 'resolved',
        );
        return;
    }
    if (evType === 'model_step_finished') {
        setOverlayTextStreaming(runId, streamKey, roleId, label, false);
        setOverlayIdleCursor(runId, streamKey, roleId, label, false);
        return;
    }
    if (evType === 'run_completed' || evType === 'run_failed' || evType === 'run_stopped') {
        setOverlayTextStreaming(runId, streamKey, roleId, label, false);
        setOverlayIdleCursor(runId, streamKey, roleId, label, false);
        overlaySeenEventIdsByRun.delete(runId);
        clearTimelineRun(runId);
    }
}

function isDuplicateOverlayEvent(runId, eventId) {
    const safeRunId = String(runId || '').trim();
    const safeEventId = String(eventId || '').trim();
    if (!safeRunId || !safeEventId) return false;
    let seen = overlaySeenEventIdsByRun.get(safeRunId);
    if (!seen) {
        seen = new Set();
        overlaySeenEventIdsByRun.set(safeRunId, seen);
    }
    if (seen.has(safeEventId)) return true;
    seen.add(safeEventId);
    if (seen.size > MAX_OVERLAY_SEEN_EVENT_IDS_PER_RUN) {
        const overflow = seen.size - MAX_OVERLAY_SEEN_EVENT_IDS_PER_RUN;
        Array.from(seen).slice(0, overflow).forEach(id => seen.delete(id));
    }
    return false;
}

function ensureApprovalState(toolBlock) {
    let approvalEl = toolBlock.querySelector('.tool-approval-inline');
    if (approvalEl) return approvalEl;

    approvalEl = document.createElement('div');
    approvalEl.className = 'tool-approval-inline';
    const _label = t('approval.state.required');
    const _labelEl = document.createElement('div');
    _labelEl.className = 'tool-approval-state';
    _labelEl.textContent = _label;
    approvalEl.replaceChildren(_labelEl);
    const card = toolBlock.querySelector('.tool-detail-card');
    const outputEl = toolBlock.querySelector('.tool-output');
    if (card && outputEl) {
        card.insertBefore(approvalEl, outputEl);
    } else if (card) {
        card.appendChild(approvalEl);
    }
    return approvalEl;
}

function finalizeStreamEntry(entry) {
    if (!entry) {
        return;
    }
    if (entry.activeTextEl) {
        flushRichTextUpdate(entry.activeTextEl);
        if (entry.activeTextIsIdle === true && isIdleCursorPlaceholder(entry.activeTextEl)) {
            syncStreamingCursor(entry.activeTextEl, false);
            entry.activeTextEl.remove?.();
        } else {
            updateMessageText(entry.activeTextEl, entry.activeRaw, { streaming: false });
            markIdleCursorPlaceholder(entry.activeTextEl, false);
        }
        entry.activeTextEl = null;
        entry.activeRaw = '';
        entry.activeTextIsIdle = false;
    }
    if (entry.thinkingParts instanceof Map) {
        entry.thinkingParts.forEach(thinkingEntry => {
            flushRichTextUpdate(thinkingEntry.textEl);
            updateThinkingText(thinkingEntry.textEl, thinkingEntry.raw, {
                streaming: false,
                runId: entry.runId,
                instanceId: entry.instanceId,
                streamKey: entry.streamKey,
                partIndex: thinkingEntry.key,
            });
            thinkingEntry.finished = true;
        });
    }
    if (entry.thinkingActiveByPart) {
        entry.thinkingActiveByPart.clear();
    }
    applyTimelineAction({
        type: 'stream_finished',
        scope: streamScope(entry),
    });
}

function matchesFinalizeTarget(entry, target) {
    const safeInstanceId = String(target?.instanceId || '').trim();
    const safeRoleId = String(target?.roleId || '').trim();
    const safeStreamKey = String(target?.streamKey || '').trim();
    const entryInstanceId = String(entry?.instanceId || '').trim();
    const entryRoleId = String(entry?.roleId || '').trim();
    const entryStreamKey = String(entry?.streamKey || '').trim();
    if (safeStreamKey && entryStreamKey && entryStreamKey === safeStreamKey) {
        return true;
    }
    if (safeInstanceId && entryInstanceId && entryInstanceId === safeInstanceId) {
        return true;
    }
    if (safeRoleId && entryRoleId && entryRoleId === safeRoleId) {
        return true;
    }
    return false;
}

function resolveStreamKey(instanceId, roleId, runId = '') {
    const safeInstanceId = String(instanceId || '').trim();
    const safeRoleId = String(roleId || '').trim();
    const safeRunId = String(runId || '').trim();
    const runPrimaryRoleId = safeRunId ? String(getRunPrimaryRoleId(safeRunId) || '').trim() : '';
    const isPrimaryForRun = !!(safeRoleId && runPrimaryRoleId && safeRoleId === runPrimaryRoleId);
    if (
        isPrimaryForRun
        || (!safeRunId && isPrimaryRoleId(safeRoleId))
        || !safeRoleId
        || safeInstanceId === PRIMARY_KEY
        || safeInstanceId === 'coordinator'
    ) {
        return PRIMARY_KEY;
    }
    if (safeInstanceId) return safeInstanceId;
    return `role:${safeRoleId}`;
}

function resolveStreamStateKey(instanceId, roleId, runId = '') {
    const streamKey = resolveStreamKey(instanceId, roleId, runId);
    const safeRunId = String(runId || '').trim();
    return safeRunId ? `${safeRunId}::${streamKey}` : streamKey;
}

function createStreamState({
    container,
    instanceId,
    roleId,
    label,
    runId,
}) {
    const streamKey = resolveStreamKey(instanceId, roleId, runId);
    const reused = findReusableStreamState({
        container,
        instanceId,
        roleId,
        label,
        runId,
    });
    if (reused) {
        return reused;
    }
    const { wrapper, contentEl } = renderMessageBlock(container, 'model', label, [], {
        runId,
        instanceId: String(instanceId || '').trim(),
        roleId: String(roleId || '').trim(),
        streamKey,
    });
    bindHeightObserver(container, wrapper);
    return {
        container,
        wrapper,
        contentEl,
        pendingToolBlocks: {},
        activeTextEl: null,
        raw: '',
        activeRaw: '',
        activeTextIsIdle: false,
        thinkingParts: new Map(),
        thinkingActiveByPart: new Map(),
        thinkingSequence: 0,
        roleId,
        label,
        runId: String(runId || ''),
        instanceId: String(instanceId || ''),
        streamKey,
    };
}

function isStreamStateDetachedFromContainer(st, container) {
    if (!st || !container) {
        return false;
    }
    if (isDisconnectedNode(st.wrapper) || isDisconnectedNode(st.contentEl)) {
        return true;
    }
    if (Array.isArray(container.__messages)) {
        return !container.__messages.some(item => (
            item?.wrapper === st.wrapper
            || item?.contentEl === st.contentEl
        ));
    }
    if (typeof container.contains === 'function' && st.wrapper) {
        try {
            return !container.contains(st.wrapper);
        } catch (_) {
            return false;
        }
    }
    return false;
}

function isDisconnectedNode(node) {
    return !!(
        node
        && typeof node.isConnected === 'boolean'
        && node.isConnected === false
    );
}

function findReusableStreamState({
    container,
    instanceId,
    roleId,
    label,
    runId,
}) {
    const overlayEntry = resolveOverlayEntry(runId, instanceId, roleId, label);
    const wrapper = findReusableMessageWrapper({
        container,
        instanceId,
        roleId,
        label,
        runId,
    });
    if (!wrapper) return null;
    const contentEl = wrapper.querySelector('.msg-content');
    if (!contentEl) return null;
    const idleRebind = overlayEntry?.idleCursor === true && overlayEntry?.textStreaming !== true;
    const activeTextEl = idleRebind
        ? findReusableIdleCursorElement(contentEl)
        : findLastReusableTextElement(contentEl);
    const activeRaw = idleRebind ? '' : resolveReusableRawText(overlayEntry);
    if (activeTextEl) {
        syncStreamingCursor(activeTextEl, overlayEntry?.textStreaming === true);
        if (overlayEntry?.idleCursor === true && overlayEntry?.textStreaming !== true) {
            markIdleCursorPlaceholder(activeTextEl, true);
            syncStreamingCursor(activeTextEl, true);
        }
    }
    const thinkingBinding = bindReusableThinkingState(contentEl, overlayEntry);
    const pendingToolBlocks = bindReusableToolBlocks(contentEl, overlayEntry);
    bindHeightObserver(container, wrapper);
    return {
        container,
        wrapper,
        contentEl,
        pendingToolBlocks,
        activeTextEl,
        raw: activeRaw,
        activeRaw,
        activeTextIsIdle: isIdleCursorPlaceholder(activeTextEl),
        thinkingParts: thinkingBinding.parts,
        thinkingActiveByPart: thinkingBinding.activeByPart,
        thinkingSequence: thinkingBinding.nextSequence,
        roleId,
        label,
        runId: String(runId || ''),
        instanceId: String(instanceId || ''),
        streamKey: resolveStreamKey(instanceId, roleId, runId),
    };
}

function resolveOverlayEntry(runId, instanceId, roleId, label) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) {
        return null;
    }
    const runOverlay = overlayState.get(safeRunId);
    if (!runOverlay) {
        return null;
    }
    const key = resolveStreamKey(instanceId, roleId, safeRunId);
    return runOverlay.entries.get(key)
        || runOverlay.entries.get(resolveStreamKey(instanceId, '', safeRunId))
        || runOverlay.entries.get(resolveStreamKey('', roleId, safeRunId))
        || runOverlay.entries.get(resolveStreamKey('', '', safeRunId));
}

function findReusableMessageWrapper({
    container,
    instanceId,
    roleId,
    label,
    runId,
}) {
    if (!container) return null;
    const streamKey = resolveStreamKey(instanceId, roleId, runId);
    const safeLabel = String(label || '').trim().toUpperCase();
    const safeRunId = String(runId || '').trim();
    const wrappers = Array.from(container.querySelectorAll('.message'));
    for (let index = wrappers.length - 1; index >= 0; index -= 1) {
        const wrapper = wrappers[index];
        const roleEl = wrapper.querySelector('.msg-role');
        if (!roleEl) continue;
        const renderedLabel = String(roleEl.textContent || '').trim();
        if (!renderedLabel || renderedLabel !== safeLabel) continue;
        if (!wrapperMatchesStreamKey(wrapper, streamKey, roleId)) continue;
        if (safeRunId && !wrapperBelongsToRun(wrapper, safeRunId)) continue;
        return wrapper;
    }
    return null;
}

function wrapperMatchesStreamKey(wrapper, streamKey, roleId) {
    const safeStreamKey = String(streamKey || '').trim();
    const wrapperStreamKey = String(wrapper.dataset.streamKey || '').trim();
    if (wrapperStreamKey) {
        return wrapperStreamKey === safeStreamKey;
    }
    const wrapperInstanceId = String(wrapper.dataset.instanceId || '').trim();
    const wrapperRoleId = String(wrapper.dataset.roleId || '').trim();
    const safeRoleId = String(roleId || '').trim();
    return !!(
        (wrapperInstanceId && wrapperInstanceId === safeStreamKey)
        || (safeRoleId && wrapperRoleId === safeRoleId)
    );
}

function wrapperBelongsToRun(wrapper, runId) {
    const wrapperRunId = String(wrapper.dataset.runId || '').trim();
    if (wrapperRunId) {
        return wrapperRunId === runId;
    }
    const section = wrapper.closest('.session-round-section');
    if (!section) return true;
    return String(section.dataset.runId || '').trim() === runId;
}

function findLastReusableTextElement(contentEl) {
    if (!contentEl) return null;
    const textBlocks = Array.from(contentEl.querySelectorAll('.msg-text'));
    for (let index = textBlocks.length - 1; index >= 0; index -= 1) {
        const textEl = textBlocks[index];
        if (textEl.closest('.thinking-block')) continue;
        if (isIdleCursorPlaceholder(textEl)) continue;
        return textEl;
    }
    return null;
}

function findReusableIdleCursorElement(contentEl) {
    if (!contentEl) return null;
    const textBlocks = Array.from(contentEl.querySelectorAll('.msg-text'));
    for (let index = textBlocks.length - 1; index >= 0; index -= 1) {
        const textEl = textBlocks[index];
        if (textEl.closest('.thinking-block')) continue;
        if (isIdleCursorPlaceholder(textEl)) {
            return textEl;
        }
    }
    return null;
}

function resolveReusableRawText(overlayEntry) {
    if (!overlayEntry || !Array.isArray(overlayEntry.parts)) {
        return '';
    }
    for (let index = overlayEntry.parts.length - 1; index >= 0; index -= 1) {
        const part = overlayEntry.parts[index];
        if (!part || part.kind !== 'text') {
            continue;
        }
        return String(part.content || '');
    }
    return '';
}

function bindReusableThinkingState(contentEl, overlayEntry) {
    const parts = new Map();
    const activeByPart = new Map();
    let nextSequence = 0;
    if (!contentEl || !overlayEntry || !Array.isArray(overlayEntry.parts)) {
        return { parts, activeByPart, nextSequence };
    }

    overlayEntry.parts.forEach(part => {
        if (!part || part.kind !== 'thinking') {
            return;
        }
        const safePartIndex = String(part.part_index ?? '');
        const key = String(part._key || `${safePartIndex}:${nextSequence}`);
        const textEl = findReusableThinkingTextElement(contentEl, key, safePartIndex);
        if (!textEl) {
            return;
        }
        parts.set(key, {
            textEl,
            raw: String(part.content || ''),
            finished: part.finished === true,
            partIndex: safePartIndex,
            key,
        });
        if (part.finished !== true && safePartIndex) {
            activeByPart.set(safePartIndex, key);
        }
        const sequenceValue = parseThinkingSequenceValue(key, safePartIndex);
        nextSequence = Math.max(nextSequence, sequenceValue + 1);
    });

    return { parts, activeByPart, nextSequence };
}

function bindReusableToolBlocks(contentEl, overlayEntry) {
    const pendingToolBlocks = {};
    if (!contentEl || !overlayEntry || !Array.isArray(overlayEntry.parts)) {
        return pendingToolBlocks;
    }
    overlayEntry.parts.forEach(part => {
        if (!part || part.kind !== 'tool') {
            return;
        }
        const toolBlock = findToolBlock(contentEl, part.tool_name, part.tool_call_id || null);
        if (!toolBlock) {
            return;
        }
        indexPendingToolBlock(
            pendingToolBlocks,
            toolBlock,
            part.tool_name,
            part.tool_call_id || null,
        );
    });
    return pendingToolBlocks;
}

function findReusableThinkingTextElement(contentEl, key, partIndex) {
    if (!contentEl) {
        return null;
    }
    const candidates = [
        key ? `.thinking-block[data-part-index="${escapeSelectorValue(key)}"] .thinking-text` : '',
        partIndex ? `.thinking-block[data-part-index="${escapeSelectorValue(partIndex)}"] .thinking-text` : '',
    ].filter(Boolean);
    for (const selector of candidates) {
        const textEl = contentEl.querySelector(selector);
        if (textEl) {
            return textEl;
        }
    }
    return null;
}

function parseThinkingSequenceValue(key, partIndex) {
    const safeKey = String(key || '');
    const safePartIndex = String(partIndex || '');
    const prefix = safePartIndex ? `${safePartIndex}:` : '';
    if (!prefix || !safeKey.startsWith(prefix)) {
        return 0;
    }
    const parsed = Number.parseInt(safeKey.slice(prefix.length), 10);
    return Number.isFinite(parsed) ? parsed : 0;
}

function escapeSelectorValue(value) {
    return String(value || '').replaceAll('\\', '\\\\').replaceAll('"', '\\"');
}

function endActiveText(st) {
    if (!st) return;
    if (st.activeTextEl) {
        flushRichTextUpdate(st.activeTextEl);
        if (st.activeTextIsIdle === true && isIdleCursorPlaceholder(st.activeTextEl)) {
            syncStreamingCursor(st.activeTextEl, false);
            st.activeTextEl.remove?.();
        } else {
            syncStreamingCursor(st.activeTextEl, false);
            markIdleCursorPlaceholder(st.activeTextEl, false);
        }
    }
    setOverlayTextStreaming(st.runId, st.instanceId, st.roleId, st.label, false);
    setOverlayIdleCursor(st.runId, st.instanceId, st.roleId, st.label, false);
    st.activeTextEl = null;
    st.activeRaw = '';
    st.activeTextIsIdle = false;
}

function resolveToolBlockTarget(st, container, toolName, toolCallId) {
    if (st) {
        const indexed = resolvePendingToolBlock(
            st.pendingToolBlocks || {},
            toolName,
            toolCallId,
        );
        if (indexed) return indexed;
        const byStreamState = findToolBlock(st.contentEl, toolName, toolCallId);
        if (byStreamState) return byStreamState;
    }
    if (!container) return null;
    return findToolBlockInContainer(container, toolName, toolCallId);
}

function materializeToolBlockFromOverlay({
    container,
    runId,
    instanceId,
    roleId,
    label,
    toolName,
    toolCallId,
}) {
    if (!container) {
        return null;
    }
    const overlayEntry = resolveOverlayEntry(runId, instanceId, roleId, label);
    const overlayPart = overlayEntry
        ? findOverlayToolPart(overlayEntry, toolName, toolCallId)
        : null;
    const stateKey = resolveStreamStateKey(instanceId, roleId, runId);
    let st = streamState.get(stateKey);
    if (!st) {
        st = createStreamState({
            container,
            instanceId,
            roleId,
            label: String(label || overlayEntry?.label || roleId || 'Agent'),
            runId,
        });
        streamState.set(stateKey, st);
    }
    const existing = resolveToolBlockTarget(
        st,
        container,
        toolName,
        toolCallId,
    );
    if (existing) {
        return { streamState: st, toolBlock: existing };
    }
    endActiveText(st);
    const nextToolName = String(
        toolName || overlayPart?.tool_name || 'unknown_tool',
    );
    const nextToolCallId = toolCallId || overlayPart?.tool_call_id || null;
    const toolBlock = buildPendingToolBlock(
        nextToolName,
        overlayPart?.args || {},
        nextToolCallId,
    );
    st.contentEl.appendChild(toolBlock);
    indexPendingToolBlock(
        st.pendingToolBlocks,
        toolBlock,
        nextToolName,
        nextToolCallId,
    );
    return { streamState: st, toolBlock };
}

function clearOverlayEntry(runId, instanceId, roleId) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) return;
    const runOverlay = overlayState.get(safeRunId);
    if (!runOverlay) return;
    const key = resolveStreamKey(instanceId, roleId, safeRunId);
    runOverlay.entries.delete(key);
    if (runOverlay.entries.size === 0) {
        clearRunOverlayCleanupTimer(safeRunId);
        overlayState.delete(safeRunId);
    }
}

function ensureOverlayEntry(runId, instanceId, roleId, label) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) return null;
    let runOverlay = overlayState.get(safeRunId);
    if (!runOverlay) {
        runOverlay = { entries: new Map() };
        overlayState.set(safeRunId, runOverlay);
    }
    const key = resolveStreamKey(instanceId, roleId, safeRunId);
    let entry = runOverlay.entries.get(key);
    if (!entry) {
        entry = {
            instanceId: String(instanceId || ''),
            roleId: String(roleId || ''),
            streamKey: key,
            label: String(label || ''),
            parts: [],
            thinkingActiveByPart: new Map(),
            thinkingSequence: 0,
            toolSequence: 0,
            textStreaming: false,
            idleCursor: false,
        };
        runOverlay.entries.set(key, entry);
    } else {
        if (instanceId) entry.instanceId = String(instanceId);
        if (roleId) entry.roleId = String(roleId);
        entry.streamKey = key;
        if (label) entry.label = String(label);
        if (!entry.thinkingActiveByPart) entry.thinkingActiveByPart = new Map();
        if (typeof entry.thinkingSequence !== 'number') entry.thinkingSequence = 0;
        if (typeof entry.textStreaming !== 'boolean') entry.textStreaming = false;
        if (typeof entry.idleCursor !== 'boolean') entry.idleCursor = false;
        if (typeof entry.toolSequence !== 'number') entry.toolSequence = 0;
    }
    return entry;
}

function scheduleOverlayEntryCleanup(runId, instanceId, roleId, delayMs = 0) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) {
        return;
    }
    const key = resolveStreamKey(instanceId, roleId, safeRunId);
    if (delayMs <= 0) {
        clearOverlayEntryCleanupTimer(safeRunId, key);
        clearOverlayEntry(safeRunId, key, roleId);
        return;
    }
    clearOverlayEntryCleanupTimer(safeRunId, key);
    const timerKey = overlayEntryCleanupKey(safeRunId, key);
    const timerId = setTimeout(() => {
        overlayCleanupTimers.delete(timerKey);
        clearOverlayEntry(safeRunId, key, roleId);
    }, delayMs);
    overlayCleanupTimers.set(timerKey, timerId);
}

function scheduleRunOverlayCleanup(runId, delayMs = 0) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) {
        return;
    }
    if (delayMs <= 0) {
        clearRunOverlayCleanupTimer(safeRunId);
        overlayState.delete(safeRunId);
        return;
    }
    clearRunOverlayCleanupTimer(safeRunId);
    const timerKey = overlayRunCleanupKey(safeRunId);
    const timerId = setTimeout(() => {
        overlayCleanupTimers.delete(timerKey);
        overlayState.delete(safeRunId);
    }, delayMs);
    overlayCleanupTimers.set(timerKey, timerId);
}

function clearOverlayEntryCleanupTimer(runId, streamKey) {
    const safeRunId = String(runId || '').trim();
    const safeStreamKey = String(streamKey || '').trim();
    if (!safeRunId || !safeStreamKey) {
        return;
    }
    const timerKey = overlayEntryCleanupKey(safeRunId, safeStreamKey);
    const timerId = overlayCleanupTimers.get(timerKey);
    if (!timerId) {
        return;
    }
    clearTimeout(timerId);
    overlayCleanupTimers.delete(timerKey);
}

function clearRunOverlayCleanupTimer(runId) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) {
        return;
    }
    const runTimerKey = overlayRunCleanupKey(safeRunId);
    const runTimerId = overlayCleanupTimers.get(runTimerKey);
    if (runTimerId) {
        clearTimeout(runTimerId);
        overlayCleanupTimers.delete(runTimerKey);
    }
    Array.from(overlayCleanupTimers.keys()).forEach(timerKey => {
        if (!timerKey.startsWith(`${safeRunId}::entry::`)) {
            return;
        }
        const timerId = overlayCleanupTimers.get(timerKey);
        if (timerId) {
            clearTimeout(timerId);
            overlayCleanupTimers.delete(timerKey);
        }
    });
}

function overlayEntryCleanupKey(runId, streamKey) {
    return `${runId}::entry::${streamKey}`;
}

function overlayRunCleanupKey(runId) {
    return `${runId}::run`;
}

function updateOverlayText(runId, instanceId, roleId, label, text) {
    const entry = ensureOverlayEntry(runId, instanceId, roleId, label);
    if (!entry) return;
    const nextText = String(text || '');
    entry.textStreaming = true;
    entry.idleCursor = false;
    if (!nextText) return;
    const lastPart = entry.parts[entry.parts.length - 1];
    if (lastPart && lastPart.kind === 'text') {
        lastPart.content = String(lastPart.content || '') + nextText;
        return;
    }
    entry.parts.push({ kind: 'text', content: nextText });
}

function appendOverlayOutputParts(
    runId,
    instanceId,
    roleId,
    label,
    outputParts,
    options = {},
) {
    const entry = ensureOverlayEntry(runId, instanceId, roleId, label);
    if (!entry) return false;
    const includeText = options.includeText === true;
    const parts = Array.isArray(outputParts) ? outputParts : [];
    let hasTextOutput = false;
    parts.forEach(rawPart => {
        const normalizedPart = normalizeOverlayOutputPart(rawPart);
        if (!normalizedPart) {
            return;
        }
        if (normalizedPart.kind === 'text') {
            if (!includeText) {
                return;
            }
            const lastPart = entry.parts[entry.parts.length - 1];
            if (lastPart && lastPart.kind === 'text') {
                lastPart.content = String(lastPart.content || '') + normalizedPart.content;
            } else {
                entry.parts.push(normalizedPart);
            }
            hasTextOutput = true;
            return;
        }
        entry.parts.push(normalizedPart);
    });
    return hasTextOutput;
}

function setOverlayTextStreaming(runId, instanceId, roleId, label, isStreaming) {
    const entry = ensureOverlayEntry(runId, instanceId, roleId, label);
    if (!entry) return;
    entry.textStreaming = isStreaming === true;
}

function setOverlayIdleCursor(runId, instanceId, roleId, label, isIdle) {
    const entry = ensureOverlayEntry(runId, instanceId, roleId, label);
    if (!entry) return;
    entry.idleCursor = isIdle === true;
}

function startOverlayThinking(runId, instanceId, roleId, label, partIndex) {
    const entry = ensureOverlayEntry(runId, instanceId, roleId, label);
    if (!entry) return;
    const safePartIndex = Number(partIndex);
    const activeKey = entry.thinkingActiveByPart?.get(String(safePartIndex));
    const activePart = activeKey ? findOverlayThinkingPartByKey(entry, activeKey) : null;
    if (activePart && activePart.finished === false) {
        activePart.finished = false;
        return;
    }
    const nextKey = `${safePartIndex}:${entry.thinkingSequence++}`;
    entry.thinkingActiveByPart?.set(String(safePartIndex), nextKey);
    entry.parts.push({
        kind: 'thinking',
        part_index: safePartIndex,
        content: '',
        finished: false,
        _key: nextKey,
    });
}

function updateOverlayThinkingText(runId, instanceId, roleId, label, partIndex, text, options = {}) {
    const entry = ensureOverlayEntry(runId, instanceId, roleId, label);
    if (!entry) return;
    const safePartIndex = Number(partIndex);
    let part = resolveOverlayThinkingPart(entry, safePartIndex);
    if (!part) {
        startOverlayThinking(runId, instanceId, roleId, label, safePartIndex);
        part = resolveOverlayThinkingPart(entry, safePartIndex);
    }
    if (!part) return;
    const nextText = String(text || '');
    if (options.append === true) {
        part.content = String(part.content || '') + nextText;
    } else {
        part.content = nextText;
    }
    part.finished = false;
}

function finishOverlayThinking(runId, instanceId, roleId, partIndex) {
    const entry = ensureOverlayEntry(runId, instanceId, roleId, '');
    if (!entry) return;
    const safePartIndex = Number(partIndex);
    const part = resolveOverlayThinkingPart(entry, safePartIndex);
    if (!part) return;
    part.finished = true;
    if (entry.thinkingActiveByPart) {
        entry.thinkingActiveByPart.delete(String(safePartIndex));
    }
}

function updateOverlayToolCall(runId, instanceId, roleId, label, toolPart) {
    const entry = ensureOverlayEntry(runId, instanceId, roleId, label);
    if (!entry) return;
    const part = upsertOverlayToolPart(
        entry,
        toolPart.tool_name,
        toolPart.tool_call_id || null,
        toolPart.args || {},
        { createForCall: true },
    );
    const wasTerminal = isTerminalOverlayToolPart(part);
    part.args = normalizeOverlayToolArgs(toolPart.args);
    if (wasTerminal) {
        return;
    }
    part.status = String(toolPart.status || 'pending');
    delete part.result;
    delete part.validation;
}

function updateOverlayToolResult(runId, instanceId, roleId, label, toolName, toolCallId, result, isError) {
    const entry = ensureOverlayEntry(runId, instanceId, roleId, label);
    if (!entry) return;
    const part = upsertOverlayToolPart(entry, toolName, toolCallId);
    part.status = isError ? 'error' : 'completed';
    part.result = result;
}

function updateOverlayToolValidation(runId, instanceId, roleId, payload) {
    const entry = ensureOverlayEntry(runId, instanceId, roleId, '');
    if (!entry) return;
    const part = findOverlayToolPart(
        entry,
        payload?.tool_name,
        payload?.tool_call_id || null,
        { matchUnidentifiedPendingByName: true },
    );
    if (!part) return;
    part.status = 'validation_failed';
    part.validation = {
        reason: payload?.reason || '',
        details: payload?.details,
    };
}

function updateOverlayToolApproval(runId, instanceId, roleId, toolName, payload, approvalStatus) {
    const entry = ensureOverlayEntry(runId, instanceId, roleId, '');
    if (!entry) return;
    const part = upsertOverlayToolPart(
        entry,
        toolName,
        payload?.tool_call_id || null,
    );
    part.approvalStatus = approvalStatus;
}

function upsertOverlayToolPart(entry, toolName, toolCallId, args = {}, options = {}) {
    const safeToolCallId = String(toolCallId || '').trim();
    let part = findOverlayToolPart(entry, toolName, toolCallId, {
        matchUnidentifiedPendingByName: !!safeToolCallId && options.createForCall !== true,
        preferUnresolved: !safeToolCallId && options.createForCall !== true,
    });
    if (
        part
        && options.createForCall === true
        && !safeToolCallId
        && isTerminalOverlayToolPart(part)
        && hasMeaningfulOverlayToolArgs(part.args)
    ) {
        part = null;
    }
    if (!part) {
        const localKey = safeToolCallId
            ? ''
            : `${String(toolName || 'unknown_tool')}:${entry.toolSequence++}`;
        part = {
            kind: 'tool',
            tool_call_id: safeToolCallId,
            local_tool_key: localKey,
            tool_name: String(toolName || 'unknown_tool'),
            args: normalizeOverlayToolArgs(args),
            status: 'pending',
        };
        entry.parts.push(part);
        return part;
    }
    if (!part.tool_name && toolName) {
        part.tool_name = String(toolName);
    }
    if (!part.tool_call_id && toolCallId) {
        part.tool_call_id = String(toolCallId);
    }
    const normalizedArgs = normalizeOverlayToolArgs(args);
    if (Object.keys(normalizedArgs).length > 0) {
        part.args = normalizedArgs;
    } else if (!part.args || typeof part.args !== 'object') {
        part.args = {};
    }
    return part;
}

function isTerminalOverlayToolPart(part) {
    const status = String(part?.status || '').trim().toLowerCase();
    return (
        status === 'completed'
        || status === 'error'
        || status === 'validation_failed'
        || part?.result !== undefined
        || part?.validation !== undefined
    );
}

function hasMeaningfulOverlayToolArgs(args) {
    return Object.keys(normalizeOverlayToolArgs(args)).length > 0;
}

function normalizeOverlayToolArgs(args) {
    if (args === null || args === undefined) {
        return {};
    }
    if (Array.isArray(args)) {
        return { __items: args };
    }
    if (typeof args === 'object') {
        return args;
    }
    const raw = String(args || '').trim();
    if (!raw) {
        return {};
    }
    try {
        return normalizeOverlayParsedToolArgs(JSON.parse(raw), raw);
    } catch (_) {
        const extractedObject = extractOverlayJsonValue(raw, '{', '}');
        if (extractedObject) {
            try {
                return normalizeOverlayParsedToolArgs(JSON.parse(extractedObject), raw);
            } catch (_e) {
                // Continue to array extraction and raw fallback.
            }
        }
        const extractedArray = extractOverlayJsonValue(raw, '[', ']');
        if (extractedArray) {
            try {
                return normalizeOverlayParsedToolArgs(JSON.parse(extractedArray), raw);
            } catch (_e) {
                // Continue to raw fallback.
            }
        }
        return { __raw: raw };
    }
}

function normalizeOverlayParsedToolArgs(value, rawFallback = '') {
    if (Array.isArray(value)) {
        return { __items: value };
    }
    if (value && typeof value === 'object') {
        return value;
    }
    const raw = String(value ?? rawFallback ?? '').trim();
    return raw ? { __raw: raw } : {};
}

function extractOverlayJsonValue(raw, openToken, closeToken) {
    const start = raw.indexOf(openToken);
    const end = raw.lastIndexOf(closeToken);
    if (start < 0 || end <= start) {
        return '';
    }
    return raw.slice(start, end + 1);
}

function normalizeOverlayOutputPart(part) {
    if (!part || typeof part !== 'object') {
        return null;
    }
    const kind = String(part.kind || '').trim();
    if (kind === 'text') {
        const content = String(part.text || part.content || '');
        return content ? { kind: 'text', content } : null;
    }
    if (kind !== 'media_ref') {
        return null;
    }
    const url = String(part.url || '').trim();
    if (!url) {
        return null;
    }
    return {
        kind: 'media_ref',
        modality: String(part.modality || '').trim(),
        mime_type: String(part.mime_type || '').trim(),
        url,
        name: String(part.name || '').trim(),
    };
}

function findOverlayThinkingPartByKey(entry, key) {
    if (!key) return null;
    for (let index = entry.parts.length - 1; index >= 0; index -= 1) {
        const part = entry.parts[index];
        if (part.kind !== 'thinking') continue;
        if (part._key === key) return part;
    }
    return null;
}

function resolveOverlayThinkingPart(entry, partIndex) {
    const activeKey = entry.thinkingActiveByPart?.get(String(partIndex));
    if (activeKey) {
        const active = findOverlayThinkingPartByKey(entry, activeKey);
        if (active) return active;
    }
    return findOverlayThinkingPart(entry, partIndex, { preferUnfinished: true });
}

function findOverlayThinkingPart(entry, partIndex, options = {}) {
    let fallback = null;
    for (let index = entry.parts.length - 1; index >= 0; index -= 1) {
        const part = entry.parts[index];
        if (part.kind !== 'thinking') continue;
        if (Number(part.part_index) !== Number(partIndex)) continue;
        if (options.preferUnfinished && part.finished) {
            if (!fallback) fallback = part;
            continue;
        }
        return part;
    }
    return fallback;
}

function findOverlayToolPart(entry, toolName, toolCallId, options = {}) {
    const safeToolCallId = String(toolCallId || '').trim();
    const safeToolName = String(toolName || '').trim();
    if (safeToolCallId) {
        for (let index = entry.parts.length - 1; index >= 0; index -= 1) {
            const part = entry.parts[index];
            if (part.kind !== 'tool') continue;
            if (String(part.tool_call_id || '') === safeToolCallId) {
                return part;
            }
        }
        if (options.matchUnidentifiedPendingByName === true && safeToolName) {
            return findSinglePendingUnidentifiedOverlayToolPart(entry, safeToolName);
        }
        return null;
    }
    if (!safeToolName) return null;
    if (options.preferUnresolved === true) {
        const unresolved = findSinglePendingUnidentifiedOverlayToolPart(entry, safeToolName);
        if (unresolved) return unresolved;
        if (hasMultiplePendingUnidentifiedOverlayToolParts(entry, safeToolName)) return null;
    }
    for (let index = entry.parts.length - 1; index >= 0; index -= 1) {
        const part = entry.parts[index];
        if (part.kind !== 'tool') continue;
        if (String(part.tool_name || '') === safeToolName) {
            return part;
        }
    }
    return null;
}

function findSinglePendingUnidentifiedOverlayToolPart(entry, safeToolName) {
    const unresolved = entry.parts.filter(part => isPendingUnidentifiedOverlayToolPart(part, safeToolName));
    return unresolved.length === 1 ? unresolved[0] : null;
}

function hasMultiplePendingUnidentifiedOverlayToolParts(entry, safeToolName) {
    let count = 0;
    for (const part of entry.parts) {
        if (!isPendingUnidentifiedOverlayToolPart(part, safeToolName)) continue;
        count += 1;
        if (count > 1) return true;
    }
    return false;
}

function isPendingUnidentifiedOverlayToolPart(part, safeToolName) {
    return (
        part.kind === 'tool'
        && String(part.tool_name || '') === safeToolName
        && !String(part.tool_call_id || '').trim()
        && part.result === undefined
        && part.validation === undefined
        && !['completed', 'error', 'validation_failed'].includes(String(part.status || '').trim().toLowerCase())
    );
}

function hasActiveThinking(st) {
    return !!(st?.thinkingActiveByPart && st.thinkingActiveByPart.size > 0);
}

function ensureIdleStreamingTail(st) {
    if (!st || hasActiveThinking(st)) {
        return;
    }
    if (!st.activeTextEl || !isIdleCursorPlaceholder(st.activeTextEl)) {
        const placeholder = document.createElement('div');
        placeholder.className = 'msg-text';
        st.contentEl.appendChild(placeholder);
        st.activeTextEl = placeholder;
    }
    st.activeRaw = '';
    st.activeTextIsIdle = true;
    updateMessageText(st.activeTextEl, '', { streaming: true });
    markIdleCursorPlaceholder(st.activeTextEl, true);
    setOverlayTextStreaming(st.runId, st.instanceId, st.roleId, st.label, false);
    setOverlayIdleCursor(st.runId, st.instanceId, st.roleId, st.label, true);
}

function isIdleCursorPlaceholder(textEl) {
    if (!textEl) {
        return false;
    }
    return textEl?.dataset?.idleCursor === 'true' || textEl.__idleCursor === true;
}

function markIdleCursorPlaceholder(textEl, isIdle) {
    if (!textEl) {
        return;
    }
    if (textEl.dataset) {
        if (isIdle === true) {
            textEl.dataset.idleCursor = 'true';
        } else if ('idleCursor' in textEl.dataset) {
            delete textEl.dataset.idleCursor;
        }
    }
    textEl.__idleCursor = isIdle === true;
}

function resolveThinkingEntry(st, partIndex, options = {}) {
    if (!st) return null;
    const safePartIndex = String(partIndex);
    const activeKey = st.thinkingActiveByPart?.get(safePartIndex);
    if (activeKey) {
        const activeEntry = st.thinkingParts.get(activeKey);
        if (activeEntry) return activeEntry;
    }
    if (options.allowCreate === false) return null;
    return ensureThinkingEntry(st, partIndex);
}

function ensureThinkingEntry(st, partIndex, options = {}) {
    const safePartIndex = String(partIndex);
    if (typeof st.thinkingSequence !== 'number') {
        st.thinkingSequence = 0;
    }
    const activeKey = !options.forceNew
        ? st.thinkingActiveByPart?.get(safePartIndex)
        : null;
    if (activeKey) {
        const existing = st.thinkingParts.get(activeKey);
        if (existing && existing.finished !== true) {
            return existing;
        }
    }
    const nextKey = String(options.partKey || `${safePartIndex}:${st.thinkingSequence++}`);
    const textEl = appendThinkingText(st.contentEl, '', {
        partIndex: nextKey,
        streaming: true,
        runId: st.runId,
        instanceId: st.instanceId,
        streamKey: st.streamKey,
    });
    const entry = {
        textEl,
        raw: '',
        finished: false,
        partIndex: safePartIndex,
        key: nextKey,
    };
    st.thinkingParts.set(nextKey, entry);
    st.thinkingActiveByPart?.set(safePartIndex, nextKey);
    st.activeTextEl = null;
    return entry;
}

function cloneOverlayEntry(entry) {
    if (!entry) return null;
    return {
        instanceId: entry.instanceId,
        roleId: entry.roleId,
        streamKey: entry.streamKey || '',
        label: entry.label,
        parts: entry.parts.map(part => cloneOverlayPart(part)),
        textStreaming: entry.textStreaming === true,
        idleCursor: entry.idleCursor === true,
    };
}

function cloneOverlayPart(part) {
    if (!part || typeof part !== 'object') return part;
    const cloned = { ...part };
    delete cloned.local_tool_key;
    return cloned;
}

function applyTimelineAction() {
    globalThis.__relayTeamsMessageTimelineApplyAction?.(...arguments);
}

function applyRunEventToTimeline() {
    globalThis.__relayTeamsMessageTimelineApplyRunEvent?.(...arguments);
}

function clearTimelineRun(runId) {
    globalThis.__relayTeamsMessageTimelineClearRun?.(runId);
}

function clearTimelineState(options = {}) {
    globalThis.__relayTeamsMessageTimelineClearState?.(options);
}

function scheduleRichTextUpdate(targetEl, text, options, renderFn) {
    if (!targetEl || typeof renderFn !== 'function') {
        return;
    }
    if (typeof window === 'undefined' || typeof window.requestAnimationFrame !== 'function') {
        renderFn(targetEl, String(text || ''), { ...(options || {}) });
        return;
    }
    pendingTextUpdates.set(targetEl, {
        text: String(text || ''),
        options: { ...(options || {}) },
        renderFn,
    });
    if (pendingTextFrame) {
        return;
    }
    pendingTextFrame = window.requestAnimationFrame(flushRichTextUpdates);
}

function flushRichTextUpdate(targetEl) {
    const update = pendingTextUpdates.get(targetEl);
    if (!update) {
        return;
    }
    pendingTextUpdates.delete(targetEl);
    update.renderFn(targetEl, update.text, update.options);
}

function flushRichTextUpdates() {
    pendingTextFrame = 0;
    const updates = Array.from(pendingTextUpdates.entries());
    pendingTextUpdates.clear();
    updates.forEach(([targetEl, update]) => {
        update.renderFn(targetEl, update.text, update.options);
    });
}

function shouldAppendPlainTextDelta(textEl) {
    if (!textEl) {
        return false;
    }
    if (textEl.__plainTextRenderState || textEl.dataset?.renderMode === 'plain-stream') {
        return true;
    }
    return false;
}

function captureStreamFollow(container) {
    if (!container) {
        return { shouldFollow: false };
    }
    const state = ensureStreamFollowState(container);
    const nearBottom = isStreamNearBottom(container);
    if (nearBottom) {
        state.sticky = true;
        state.userScrollLockUntil = 0;
        return {
            shouldFollow: true,
            wasNearBottom: true,
        };
    }
    if (isStreamUserScrollLocked(state)) {
        return {
            shouldFollow: false,
            wasNearBottom: false,
        };
    }
    return {
        shouldFollow: false,
        wasNearBottom: false,
    };
}

function scheduleStreamScrollBottom(container, follow = null) {
    if (!container) {
        return;
    }
    if (typeof window === 'undefined' || typeof window.requestAnimationFrame !== 'function') {
        applyStreamFollowBottom(container, follow);
        return;
    }
    const previous = pendingScrollContainers.get(container);
    pendingScrollContainers.set(container, {
        container,
        follow: mergeFollowIntent(previous?.follow, follow),
    });
    if (pendingScrollFrame) {
        return;
    }
    pendingScrollFrame = window.requestAnimationFrame(flushStreamScrollBottom);
}

function flushStreamScrollBottom() {
    pendingScrollFrame = 0;
    const containers = Array.from(pendingScrollContainers.values());
    pendingScrollContainers.clear();
    containers.forEach(item => {
        applyStreamFollowBottom(item.container, item.follow);
    });
}

function bindHeightObserver(container, target = container) {
    if (!container || !target || typeof ResizeObserver !== 'function') return;
    const state = ensureStreamFollowState(container);
    if (!state.resizeObserver) {
        state.resizeObserver = new ResizeObserver(() => {
            const nearBottom = isStreamNearBottom(container);
            if (nearBottom) {
                state.sticky = true;
                state.userScrollLockUntil = 0;
            }
            if (isStreamUserScrollLocked(state)) {
                return;
            }
            if (state.sticky === true || nearBottom) {
                scheduleStreamScrollBottom(container, { shouldFollow: true });
            }
        });
        state.observedTargets = new WeakSet();
    }
    if (!state.observedTargets.has(target)) {
        state.resizeObserver.observe(target);
        state.observedTargets.add(target);
    }
}

function applyStreamFollowBottom(container, follow = null) {
    if (!container) return;
    const state = ensureStreamFollowState(container);
    const nearBottom = isStreamNearBottom(container);
    if (nearBottom) {
        state.sticky = true;
        state.userScrollLockUntil = 0;
    }
    if (isStreamUserScrollLocked(state)) {
        return;
    }
    const shouldFollow = follow?.shouldFollow === true
        || state.sticky === true
        || nearBottom;
    if (!shouldFollow) return;
    state.sticky = true;
    const scroll = () => scrollStreamToBottom(container);
    if (typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function') {
        window.requestAnimationFrame(() => {
            scroll();
            window.requestAnimationFrame(scroll);
        });
        return;
    }
    scroll();
}

function ensureStreamFollowState(container) {
    let state = streamFollowState.get(container);
    if (state) return state;
    state = {
        sticky: isStreamNearBottom(container),
        programmaticUntil: 0,
        userScrollLockUntil: 0,
        resizeObserver: null,
        observedTargets: null,
    };
    streamFollowState.set(container, state);
    bindStreamUserScrollIntent(container, state);
    return state;
}

function bindStreamUserScrollIntent(container, followState) {
    if (container.dataset?.streamBottomFollowBound === 'true') return;
    if (container.dataset) {
        container.dataset.streamBottomFollowBound = 'true';
    }
    container.addEventListener?.('wheel', event => {
        const deltaY = Number(event?.deltaY || 0);
        if (Math.abs(deltaY) <= 1) {
            return;
        }
        if (deltaY > 0 && isStreamNearBottom(container)) {
            followState.sticky = true;
            followState.userScrollLockUntil = 0;
            return;
        }
        pauseStreamAutoFollow(container, followState);
    }, { passive: true });
    container.addEventListener?.('touchstart', () => {
        if (isStreamNearBottom(container)) {
            followState.sticky = true;
            followState.userScrollLockUntil = 0;
            return;
        }
        pauseStreamAutoFollow(container, followState);
    }, { passive: true });
    container.addEventListener?.('pointerdown', event => {
        const target = event?.target;
        if (target?.closest?.('summary, .thinking-summary, .tool-summary')) {
            pauseStreamAutoFollow(container, followState);
        }
    }, { passive: true });
    container.addEventListener?.('scroll', () => {
        if (nowMs() < Number(followState.programmaticUntil || 0)) return;
        const nearBottom = isStreamNearBottom(container);
        followState.sticky = nearBottom;
        if (nearBottom) {
            followState.userScrollLockUntil = 0;
        } else {
            pauseStreamAutoFollow(container, followState);
        }
    }, { passive: true });
}

function pauseStreamAutoFollow(container, followState = null) {
    const state = followState || ensureStreamFollowState(container);
    state.sticky = false;
    state.userScrollLockUntil = nowMs() + STREAM_USER_SCROLL_LOCK_MS;
}

function isStreamUserScrollLocked(state) {
    return nowMs() < Number(state?.userScrollLockUntil || 0);
}

function scrollStreamToBottom(container) {
    const state = ensureStreamFollowState(container);
    state.programmaticUntil = nowMs() + 120;
    container.scrollTop = Math.max(
        0,
        Number(container.scrollHeight || 0) - Number(container.clientHeight || 0),
    );
}

function isStreamNearBottom(container) {
    const distance = Number(container?.scrollHeight || 0)
        - Number(container?.scrollTop || 0)
        - Number(container?.clientHeight || 0);
    return distance <= BOTTOM_FOLLOW_THRESHOLD_PX;
}

function nowMs() {
    return globalThis.performance?.now?.() || Date.now();
}

function mergeFollowIntent(previous, next) {
    if (!previous) return next || null;
    if (!next) return previous;
    return {
        ...next,
        shouldFollow: previous.shouldFollow === true || next.shouldFollow === true,
        wasNearBottom: previous.wasNearBottom === true || next.wasNearBottom === true,
    };
}

function streamScope(st, fallback = {}) {
    const runId = String(st?.runId || fallback.runId || '').trim();
    const instanceId = String(st?.instanceId || fallback.instanceId || '').trim();
    const roleId = String(st?.roleId || fallback.roleId || '').trim();
    const streamKey = String(st?.streamKey || resolveStreamKey(instanceId, roleId, runId)).trim();
    return {
        runId,
        instanceId,
        roleId,
        streamKey,
        view: resolveTimelineView(runId, instanceId),
    };
}

function resolveTimelineView(runId, instanceId) {
    const safeRunId = String(runId || '').trim();
    if (safeRunId.startsWith('subagent_run_')) {
        return 'normal-child-session';
    }
    return String(instanceId || '').trim() && String(instanceId || '').trim() !== PRIMARY_KEY
        ? 'orchestration-panel'
        : 'main';
}
