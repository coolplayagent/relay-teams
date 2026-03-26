/**
 * components/messageRenderer/stream.js
 * Streaming message mutation helpers plus a durable in-browser overlay cache.
 */
import { isPrimaryRoleId } from '../../core/state.js';
import {
    applyToolReturn,
    appendStructuredContentPart,
    appendThinkingText,
    findToolBlock,
    findToolBlockInContainer,
    renderMessageBlock,
    scrollBottom,
    setToolValidationFailureState,
    syncStreamingCursor,
    updateThinkingText,
    updateMessageText,
} from './helpers.js';

const streamState = new Map();
const overlayState = new Map();
const overlayCleanupTimers = new Map();
const PRIMARY_KEY = 'primary';

export function getOrCreateStreamBlock(
    container,
    instanceId,
    roleId,
    label,
    runId = '',
) {
    const streamKey = resolveStreamKey(instanceId, roleId);
    let st = streamState.get(streamKey);
    if (!st || st.container !== container) {
        st = createStreamState({
            container,
            instanceId,
            roleId,
            label,
            runId,
        });
        streamState.set(streamKey, st);
    } else {
        if (!st.thinkingParts) st.thinkingParts = new Map();
        if (!st.thinkingActiveByPart) st.thinkingActiveByPart = new Map();
        if (typeof st.thinkingSequence !== 'number') st.thinkingSequence = 0;
        if (typeof st.activeRaw !== 'string') st.activeRaw = '';
    }
    ensureOverlayEntry(st.runId, st.instanceId, roleId, label);
    return st;
}

export function appendStreamChunk(instanceId, text, runId = '', roleId = '', label = '') {
    const streamKey = resolveStreamKey(instanceId, roleId);
    const st = streamState.get(streamKey);
    if (!st) return;

    if (!st.activeTextEl) {
        st.activeTextEl = document.createElement('div');
        st.activeTextEl.className = 'msg-text';
        st.contentEl.appendChild(st.activeTextEl);
        st.activeRaw = '';
    }

    st.raw += text;
    st.activeRaw += text;
    updateMessageText(st.activeTextEl, st.activeRaw, { streaming: true });
    updateOverlayText(st.runId || runId, st.instanceId || instanceId, roleId || st.roleId, label || st.label, text);
    scrollBottom(st.container);
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
    const streamKey = resolveStreamKey(instanceId, roleId);
    let st = streamState.get(streamKey);
    if (!st && container) {
        st = createStreamState({
            container,
            instanceId,
            roleId,
            label: label || 'Agent',
            runId,
        });
        streamState.set(streamKey, st);
    }
    if (!st || !Array.isArray(outputParts)) return;
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
    scrollBottom(st.container || container);
}

export function finalizeStream(instanceId, roleId = '') {
    const streamKey = resolveStreamKey(instanceId, roleId);
    const st = streamState.get(streamKey);
    if (st && st.activeTextEl) {
        updateMessageText(st.activeTextEl, st.activeRaw, { streaming: false });
    }
    if (st?.thinkingParts instanceof Map) {
        st.thinkingParts.forEach(entry => {
            updateThinkingText(entry.textEl, entry.raw, { streaming: false });
            entry.finished = true;
        });
        if (st.thinkingActiveByPart) {
            st.thinkingActiveByPart.clear();
        }
    }
    if (st?.runId) {
        clearOverlayEntry(st.runId, st.instanceId, st.roleId);
    }
    streamState.delete(streamKey);
}

export function clearStreamState(instanceId, roleId = '') {
    const streamKey = resolveStreamKey(instanceId, roleId);
    const entry = streamState.get(streamKey);
    if (entry?.activeTextEl) {
        syncStreamingCursor(entry.activeTextEl, false);
    }
    streamState.delete(streamKey);
}

export function clearRunStreamState(runId) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) return;
    clearRunOverlayCleanupTimer(safeRunId);
    overlayState.delete(safeRunId);
    Array.from(streamState.entries()).forEach(([key, entry]) => {
        if (entry.runId === safeRunId) {
            if (entry.activeTextEl) {
                syncStreamingCursor(entry.activeTextEl, false);
            }
            streamState.delete(key);
        }
    });
}

export function clearRenderedStreamState() {
    streamState.forEach(entry => {
        if (entry?.activeTextEl) {
            syncStreamingCursor(entry.activeTextEl, false);
        }
    });
    streamState.clear();
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
    const streamKey = resolveStreamKey(instanceId, roleId);
    let st = streamState.get(streamKey);
    if (!st) {
        const actorLabel = label || (toolName ? 'Tool' : 'Agent');
        st = createStreamState({
            container,
            instanceId,
            roleId,
            label: actorLabel,
            runId,
        });
        streamState.set(streamKey, st);
    } else {
        if (!st.thinkingParts) st.thinkingParts = new Map();
        if (!st.thinkingActiveByPart) st.thinkingActiveByPart = new Map();
        if (typeof st.thinkingSequence !== 'number') st.thinkingSequence = 0;
        if (typeof st.activeRaw !== 'string') st.activeRaw = '';
    }

    endActiveText(st);

    let argsStr = '';
    try {
        argsStr = typeof args === 'object' ? JSON.stringify(args, null, 2) : String(args || '');
    } catch (e) {
        argsStr = String(args);
    }

    const toolBlock = document.createElement('div');
    toolBlock.className = 'tool-block';
    toolBlock.dataset.toolName = toolName;
    if (toolCallId) {
        toolBlock.dataset.toolCallId = toolCallId;
    }
    toolBlock.style.display = 'block';
    toolBlock.style.visibility = 'visible';
    toolBlock.innerHTML = `
        <div class="tool-header" onclick="this.nextElementSibling.classList.toggle('open')">
            <div class="tool-title">
                <svg viewBox="0 0 24 24" fill="none" class="icon" style="width:14px;height:14px;"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" stroke="currentColor" stroke-width="2"/></svg>
                <span class="name">${toolName}</span>
            </div>
            <div class="tool-status"><div class="spinner"></div></div>
        </div>
        <div class="tool-body">
            <pre class="tool-args" style="white-space:pre-wrap;">${argsStr}</pre>
            <div class="tool-result">Processing...</div>
        </div>
    `;
    st.contentEl.appendChild(toolBlock);
    updateOverlayToolCall(st.runId || runId, st.instanceId || instanceId, roleId || st.roleId, st.label, {
        tool_call_id: toolCallId || '',
        tool_name: toolName,
        args,
        status: 'pending',
    });
    scrollBottom(st.container || container);
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
    const container = options.container || null;
    const streamKey = resolveStreamKey(instanceId, roleId);
    const st = streamState.get(streamKey);
    const toolBlock = resolveToolBlockTarget(st, container, toolName, toolCallId);
    if (!toolBlock) {
        updateOverlayToolResult(runId, instanceId, roleId, toolName, toolCallId, result, isError);
        return;
    }
    applyToolReturn(toolBlock, result);
    updateOverlayToolResult(st.runId || runId, st.instanceId || instanceId, roleId || st.roleId, toolName, toolCallId, result, isError);
    scrollBottom((st && st.container) || container);
}

export function markToolInputValidationFailed(instanceId, payload, options = {}) {
    const runId = String(options.runId || '');
    const roleId = String(options.roleId || '');
    const container = options.container || null;
    const streamKey = resolveStreamKey(instanceId, roleId);
    const st = streamState.get(streamKey);
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

    setToolValidationFailureState(toolBlock, payload);
    updateOverlayToolValidation(st.runId || runId, st.instanceId || instanceId, roleId || st.roleId, payload);
    scrollBottom((st && st.container) || container);
    return true;
}

export function startThinkingBlock(instanceId, partIndex, options = {}) {
    const runId = String(options.runId || '');
    const roleId = String(options.roleId || '');
    const label = String(options.label || '');
    const container = options.container || null;
    const streamKey = resolveStreamKey(instanceId, roleId);
    let st = streamState.get(streamKey);
    if (!st && container) {
        const actorLabel = label || 'Agent';
        st = createStreamState({
            container,
            instanceId,
            roleId,
            label: actorLabel,
            runId,
        });
        streamState.set(streamKey, st);
    } else if (st) {
        if (!st.thinkingParts) st.thinkingParts = new Map();
        if (!st.thinkingActiveByPart) st.thinkingActiveByPart = new Map();
        if (typeof st.thinkingSequence !== 'number') st.thinkingSequence = 0;
        if (typeof st.activeRaw !== 'string') st.activeRaw = '';
    }
    if (!st) return false;
    endActiveText(st);
    ensureThinkingEntry(st, partIndex, { forceNew: true });
    startOverlayThinking(st.runId || runId, st.instanceId || instanceId, roleId || st.roleId, st.label || label, partIndex);
    scrollBottom(st.container || container);
    return true;
}

export function appendThinkingChunk(instanceId, partIndex, text, options = {}) {
    const runId = String(options.runId || '');
    const roleId = String(options.roleId || '');
    const label = String(options.label || '');
    const container = options.container || null;
    const streamKey = resolveStreamKey(instanceId, roleId);
    const st = streamState.get(streamKey);
    if (!st) {
        updateOverlayThinkingText(runId, instanceId, roleId, label, partIndex, text, { append: true });
        return false;
    }
    const entry = resolveThinkingEntry(st, partIndex);
    entry.raw += String(text || '');
    updateThinkingText(entry.textEl, entry.raw, { streaming: true });
    updateOverlayThinkingText(st.runId || runId, st.instanceId || instanceId, roleId || st.roleId, st.label || label, partIndex, entry.raw);
    scrollBottom((st && st.container) || container);
    return true;
}

export function finalizeThinking(instanceId, partIndex, options = {}) {
    const runId = String(options.runId || '');
    const roleId = String(options.roleId || '');
    const streamKey = resolveStreamKey(instanceId, roleId);
    const st = streamState.get(streamKey);
    const entry = resolveThinkingEntry(st, partIndex, { allowCreate: false });
    if (!entry) {
        finishOverlayThinking(runId, instanceId, roleId, partIndex);
        return false;
    }
    updateThinkingText(entry.textEl, entry.raw, { streaming: false });
    entry.finished = true;
    if (st?.thinkingActiveByPart) {
        st.thinkingActiveByPart.delete(String(partIndex));
    }
    finishOverlayThinking((st && st.runId) || runId, (st && st.instanceId) || instanceId, (st && st.roleId) || roleId, partIndex);
    return true;
}

export function attachToolApprovalControls(instanceId, toolName, payload, handlers, options = {}) {
    const runId = String(options.runId || '');
    const roleId = String(options.roleId || '');
    const container = options.container || null;
    const streamKey = resolveStreamKey(instanceId, roleId);
    const st = streamState.get(streamKey);
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

    const approvalEl = ensureApprovalState(toolBlock);

    const body = toolBlock.querySelector('.tool-body');
    if (body) body.classList.add('open');

    const stateEl = approvalEl.querySelector('.tool-approval-state');
    if (stateEl) stateEl.textContent = 'Approval required';

    updateOverlayToolApproval(st.runId || runId, st.instanceId || instanceId, roleId || st.roleId, toolName, payload, 'requested');
    scrollBottom((st && st.container) || container);
    return true;
}

export function markToolApprovalResolved(instanceId, payload, options = {}) {
    const runId = String(options.runId || '');
    const roleId = String(options.roleId || '');
    const container = options.container || null;
    const streamKey = resolveStreamKey(instanceId, roleId);
    const st = streamState.get(streamKey);
    updateOverlayToolApproval(
        (st && st.runId) || runId,
        (st && st.instanceId) || instanceId,
        (st && st.roleId) || roleId,
        payload?.tool_name,
        payload,
        String(payload?.action || '').toLowerCase() || 'resolved',
    );
    const toolCallId = payload?.tool_call_id;
    if (!toolCallId) return false;

    const toolBlock = resolveToolBlockTarget(st, container, payload?.tool_name, toolCallId);
    if (!toolBlock) return false;
    toolBlock.dataset.toolCallId = toolCallId;

    const approvalEl = ensureApprovalState(toolBlock);
    const action = String(payload.action || 'resolved').toUpperCase();
    const stateEl = approvalEl.querySelector('.tool-approval-state');
    if (stateEl) stateEl.textContent = `Approval ${action}`;
    const resultEl = toolBlock.querySelector('.tool-result');
    if (resultEl) {
        resultEl.classList.remove('error-text');
        resultEl.classList.add('warning-text');
        if (String(payload.action || '').toLowerCase() === 'deny') {
            resultEl.innerHTML = 'Approval denied. Tool will not execute.';
        } else {
            resultEl.innerHTML = 'Approval submitted. Waiting for tool result...';
        }
    }
    scrollBottom((st && st.container) || container);
    return true;
}

export function applyStreamOverlayEvent(evType, payload, options = {}) {
    const runId = String(options.runId || '').trim();
    if (!runId) return;
    const instanceId = String(options.instanceId || '').trim();
    const roleId = String(options.roleId || '').trim();
    const label = String(options.label || '').trim();
    const streamKey = resolveStreamKey(instanceId, roleId);
    const cleanupDelayMs = Number(options.cleanupDelayMs || 0);

    if (evType === 'text_delta') {
        clearOverlayEntryCleanupTimer(runId, streamKey);
        updateOverlayText(runId, streamKey, roleId, label, payload?.text || '');
        return;
    }
    if (evType === 'thinking_started') {
        clearOverlayEntryCleanupTimer(runId, streamKey);
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
        return;
    }
    if (evType === 'tool_call') {
        clearOverlayEntryCleanupTimer(runId, streamKey);
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
            payload?.tool_name || '',
            payload?.tool_call_id || null,
            resultEnvelope,
            isError,
        );
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
        scheduleOverlayEntryCleanup(runId, streamKey, roleId, cleanupDelayMs);
        return;
    }
    if (evType === 'run_completed' || evType === 'run_failed' || evType === 'run_stopped') {
        scheduleRunOverlayCleanup(runId, cleanupDelayMs);
    }
}

function ensureApprovalState(toolBlock) {
    let approvalEl = toolBlock.querySelector('.tool-approval-inline');
    if (approvalEl) return approvalEl;

    approvalEl = document.createElement('div');
    approvalEl.className = 'tool-approval-inline';
    approvalEl.innerHTML = '<div class="tool-approval-state">Approval required</div>';
    const body = toolBlock.querySelector('.tool-body');
    const resultEl = toolBlock.querySelector('.tool-result');
    if (body && resultEl) {
        body.insertBefore(approvalEl, resultEl);
    } else if (body) {
        body.appendChild(approvalEl);
    }
    return approvalEl;
}

function resolveStreamKey(instanceId, roleId) {
    const safeInstanceId = String(instanceId || '').trim();
    if (isPrimaryRoleId(roleId) || !roleId || safeInstanceId === PRIMARY_KEY || safeInstanceId === 'coordinator') {
        return PRIMARY_KEY;
    }
    if (safeInstanceId) return safeInstanceId;
    return `role:${String(roleId || '').trim()}`;
}

function createStreamState({
    container,
    instanceId,
    roleId,
    label,
    runId,
}) {
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
    const { wrapper, contentEl } = renderMessageBlock(container, 'model', label, []);
    return {
        container,
        wrapper,
        contentEl,
        activeTextEl: null,
        raw: '',
        activeRaw: '',
        thinkingParts: new Map(),
        thinkingActiveByPart: new Map(),
        thinkingSequence: 0,
        roleId,
        label,
        runId: String(runId || ''),
        instanceId: String(instanceId || ''),
    };
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
    const activeTextEl = findLastReusableTextElement(contentEl);
    const activeRaw = resolveReusableRawText(overlayEntry);
    const thinkingBinding = bindReusableThinkingState(contentEl, overlayEntry);
    return {
        container,
        wrapper,
        contentEl,
        activeTextEl,
        raw: activeRaw,
        activeRaw,
        thinkingParts: thinkingBinding.parts,
        thinkingActiveByPart: thinkingBinding.activeByPart,
        thinkingSequence: thinkingBinding.nextSequence,
        roleId,
        label,
        runId: String(runId || ''),
        instanceId: String(instanceId || ''),
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
    const key = resolveStreamKey(instanceId, roleId);
    return runOverlay.entries.get(key)
        || runOverlay.entries.get(resolveStreamKey(instanceId, ''))
        || runOverlay.entries.get(resolveStreamKey('', roleId))
        || runOverlay.entries.get(resolveStreamKey('', ''));
}

function findReusableMessageWrapper({
    container,
    instanceId,
    roleId,
    label,
    runId,
}) {
    if (!container) return null;
    const streamKey = resolveStreamKey(instanceId, roleId);
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
    if (safeStreamKey === PRIMARY_KEY) {
        return true;
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
        return textEl;
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
        syncStreamingCursor(st.activeTextEl, false);
    }
    st.activeTextEl = null;
    st.activeRaw = '';
}

function resolveToolBlockTarget(st, container, toolName, toolCallId) {
    if (st) {
        const byStreamState = findToolBlock(st.contentEl, toolName, toolCallId);
        if (byStreamState) return byStreamState;
    }
    if (!container) return null;
    return findToolBlockInContainer(container, toolName, toolCallId);
}

function clearOverlayEntry(runId, instanceId, roleId) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) return;
    const runOverlay = overlayState.get(safeRunId);
    if (!runOverlay) return;
    const key = resolveStreamKey(instanceId, roleId);
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
    const key = resolveStreamKey(instanceId, roleId);
    let entry = runOverlay.entries.get(key);
    if (!entry) {
        entry = {
            instanceId: String(instanceId || ''),
            roleId: String(roleId || ''),
            label: String(label || ''),
            parts: [],
            thinkingActiveByPart: new Map(),
            thinkingSequence: 0,
        };
        runOverlay.entries.set(key, entry);
    } else {
        if (instanceId) entry.instanceId = String(instanceId);
        if (roleId) entry.roleId = String(roleId);
        if (label) entry.label = String(label);
        if (!entry.thinkingActiveByPart) entry.thinkingActiveByPart = new Map();
        if (typeof entry.thinkingSequence !== 'number') entry.thinkingSequence = 0;
    }
    return entry;
}

function scheduleOverlayEntryCleanup(runId, instanceId, roleId, delayMs = 0) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) {
        return;
    }
    const key = resolveStreamKey(instanceId, roleId);
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
    if (!nextText) return;
    const lastPart = entry.parts[entry.parts.length - 1];
    if (lastPart && lastPart.kind === 'text') {
        lastPart.content = String(lastPart.content || '') + nextText;
        return;
    }
    entry.parts.push({ kind: 'text', content: nextText });
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
    const nextPart = {
        kind: 'tool',
        tool_call_id: String(toolPart.tool_call_id || ''),
        tool_name: String(toolPart.tool_name || ''),
        args: toolPart.args || {},
        status: String(toolPart.status || 'pending'),
    };
    entry.parts.push(nextPart);
}

function updateOverlayToolResult(runId, instanceId, roleId, toolName, toolCallId, result, isError) {
    const entry = ensureOverlayEntry(runId, instanceId, roleId, '');
    if (!entry) return;
    const part = findOverlayToolPart(entry, toolName, toolCallId);
    if (!part) return;
    part.status = isError ? 'error' : 'completed';
    part.result = result;
}

function updateOverlayToolValidation(runId, instanceId, roleId, payload) {
    const entry = ensureOverlayEntry(runId, instanceId, roleId, '');
    if (!entry) return;
    const part = findOverlayToolPart(entry, payload?.tool_name, payload?.tool_call_id || null);
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
    const part = findOverlayToolPart(entry, toolName, payload?.tool_call_id || null);
    if (!part) return;
    part.approvalStatus = approvalStatus;
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

function findOverlayToolPart(entry, toolName, toolCallId) {
    const safeToolCallId = String(toolCallId || '').trim();
    if (safeToolCallId) {
        for (let index = entry.parts.length - 1; index >= 0; index -= 1) {
            const part = entry.parts[index];
            if (part.kind !== 'tool') continue;
            if (String(part.tool_call_id || '') === safeToolCallId) {
                return part;
            }
        }
    }
    const safeToolName = String(toolName || '').trim();
    if (!safeToolName) return null;
    for (let index = entry.parts.length - 1; index >= 0; index -= 1) {
        const part = entry.parts[index];
        if (part.kind !== 'tool') continue;
        if (String(part.tool_name || '') === safeToolName) {
            return part;
        }
    }
    return null;
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
        label: entry.label,
        parts: entry.parts.map(part => ({ ...part })),
    };
}
