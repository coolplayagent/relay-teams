/**
 * components/messageRenderer/stream.js
 * Streaming message mutation helpers plus a durable in-browser overlay cache.
 */
import { isCoordinatorRoleId } from '../../core/state.js';
import {
    applyToolReturn,
    findToolBlock,
    findToolBlockInContainer,
    renderMessageBlock,
    scrollBottom,
    setToolValidationFailureState,
    syncStreamingCursor,
    updateMessageText,
} from './helpers.js';

const streamState = new Map();
const overlayState = new Map();
const COORDINATOR_KEY = 'coordinator';

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
        const { wrapper, contentEl } = renderMessageBlock(container, 'model', label, []);
        st = {
            container,
            wrapper,
            contentEl,
            activeTextEl: null,
            raw: '',
            roleId,
            label,
            runId: String(runId || ''),
            instanceId: String(instanceId || ''),
        };
        streamState.set(streamKey, st);
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
        st.raw = '';
    }

    st.raw += text;
    updateMessageText(st.activeTextEl, st.raw, { streaming: true });
    updateOverlayText(st.runId || runId, st.instanceId || instanceId, roleId || st.roleId, label || st.label, st.raw);
    scrollBottom(st.container);
}

export function finalizeStream(instanceId, roleId = '') {
    const streamKey = resolveStreamKey(instanceId, roleId);
    const st = streamState.get(streamKey);
    if (st && st.activeTextEl) {
        updateMessageText(st.activeTextEl, st.raw, { streaming: false });
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

export function clearAllStreamState() {
    streamState.forEach(entry => {
        if (entry?.activeTextEl) {
            syncStreamingCursor(entry.activeTextEl, false);
        }
    });
    streamState.clear();
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
    const coordinator = cloneOverlayEntry(runOverlay.entries.get(COORDINATOR_KEY) || null);
    const byInstance = {};
    runOverlay.entries.forEach((entry, key) => {
        if (key === COORDINATOR_KEY) return;
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
        const { wrapper, contentEl } = renderMessageBlock(container, 'model', actorLabel, []);
        st = {
            container,
            wrapper,
            contentEl,
            activeTextEl: null,
            raw: '',
            roleId,
            label: actorLabel,
            runId,
            instanceId: String(instanceId || ''),
        };
        streamState.set(streamKey, st);
    }

    st.activeTextEl = null;
    st.raw = '';

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
    if (safeInstanceId) return safeInstanceId;
    return isCoordinatorRoleId(roleId) || !roleId
        ? COORDINATOR_KEY
        : `role:${String(roleId || '').trim()}`;
}

function resolveToolBlockTarget(st, container, toolName, toolCallId) {
    if (st) {
        const byStreamState = findToolBlock(st.contentEl, toolName, toolCallId);
        if (byStreamState) return byStreamState;
    }
    if (!container) return null;
    return findToolBlockInContainer(container, toolName, toolCallId);
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
        };
        runOverlay.entries.set(key, entry);
    } else {
        if (instanceId) entry.instanceId = String(instanceId);
        if (roleId) entry.roleId = String(roleId);
        if (label) entry.label = String(label);
    }
    return entry;
}

function updateOverlayText(runId, instanceId, roleId, label, text) {
    const entry = ensureOverlayEntry(runId, instanceId, roleId, label);
    if (!entry) return;
    const nextText = String(text || '');
    const lastPart = entry.parts[entry.parts.length - 1];
    if (lastPart && lastPart.kind === 'text') {
        lastPart.content = nextText;
        return;
    }
    entry.parts.push({ kind: 'text', content: nextText });
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

function cloneOverlayEntry(entry) {
    if (!entry) return null;
    return {
        instanceId: entry.instanceId,
        roleId: entry.roleId,
        label: entry.label,
        parts: entry.parts.map(part => ({ ...part })),
    };
}
