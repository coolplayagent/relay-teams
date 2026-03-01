/**
 * components/messageRenderer.js
 * Unified message renderer used by both SSE streaming and historical display.
 * All message rendering goes through this module to keep the two views consistent.
 */
import { parseMarkdown } from '../utils/markdown.js';

// ─── Block builder ────────────────────────────────────────────────────────────

/**
 * Create and append a message block to `container`.
 * @param {HTMLElement} container  - target scroll container
 * @param {'user'|'model'|string} role
 * @param {string} label           - display name (e.g. "Coordinator", "hello_agent")
 * @param {Array}  parts           - pydantic-ai message parts array (may be empty)
 * @returns {{ wrapper, textEl, pendingToolBlocks }}  — refs for live streaming
 */
export function renderMessageBlock(container, role, label, parts = []) {
    const safeLabel = label || 'Agent';
    const wrapper = document.createElement('div');
    wrapper.className = 'message';
    wrapper.dataset.role = role;

    const roleClass = _roleClass(role, safeLabel);
    wrapper.innerHTML = `
        <div class="msg-header">
            <span class="msg-role ${roleClass}">${safeLabel.toUpperCase()}</span>
        </div>
        <div class="msg-content"><div class="msg-text"></div></div>
    `;
    container.appendChild(wrapper);
    _scrollBottom(container);

    const textEl = wrapper.querySelector('.msg-text');
    const contentEl = wrapper.querySelector('.msg-content');
    const pendingToolBlocks = {};

    if (parts.length > 0) {
        _renderParts(contentEl, textEl, parts, pendingToolBlocks);
    }

    return { wrapper, textEl, contentEl, pendingToolBlocks };
}

/**
 * Render historical messages (pydantic-ai message objects) into a container.
 */
export function renderHistoricalMessageList(container, messages) {
    const pendingToolBlocks = {};

    messages.forEach(msgItem => {
        const role = msgItem.role;          // 'user' | 'model'
        const msgObj = msgItem.message;
        if (!msgObj) return;

        const parts = msgObj.parts || [];

        // Pure tool-return messages: inject into previous tool block result div
        const isPureToolReturn = role === 'user' && parts.length > 0 &&
            parts.every(p => p.part_kind === 'tool-return' || (p.tool_name !== undefined && p.content !== undefined && p.args === undefined));

        if (isPureToolReturn) {
            parts.forEach(part => {
                const resultDiv = pendingToolBlocks[part.tool_name];
                if (resultDiv) {
                    const val = typeof part.content === 'object'
                        ? JSON.stringify(part.content, null, 2)
                        : String(part.content);
                    resultDiv.innerHTML = parseMarkdown(val);
                    // mark status success
                    const status = resultDiv.closest('.tool-block')?.querySelector('.tool-status');
                    if (status) status.innerHTML = `<svg class="status-icon status-success" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>`;
                }
            });
            return;
        }

        const label = _labelFromRole(role, msgItem.instance_id);
        const { wrapper, textEl, contentEl } = renderMessageBlock(container, role, label, []);
        _renderParts(contentEl, textEl, parts, pendingToolBlocks);
    });

    _scrollBottom(container);
}

// ─── Streaming helpers ────────────────────────────────────────────────────────

/** State for currently streaming message per panel/container */
const _streamState = new Map(); // key = containerId or instanceId → { wrapper, textEl, raw, roleId }

export function getOrCreateStreamBlock(container, instanceId, roleId, label) {
    let st = _streamState.get(instanceId);
    if (!st || st.container !== container) {
        const { wrapper, textEl, contentEl } = renderMessageBlock(container, 'model', label, []);
        st = { container, wrapper, textEl, contentEl, raw: '', roleId, label };
        _streamState.set(instanceId, st);
    }
    return st;
}

export function appendStreamChunk(instanceId, text) {
    const st = _streamState.get(instanceId);
    if (!st) return;
    st.raw += text;
    st.textEl.innerHTML = parseMarkdown(st.raw);
    _scrollBottom(st.container);
}

export function finalizeStream(instanceId) {
    const st = _streamState.get(instanceId);
    if (st) {
        st.textEl.innerHTML = parseMarkdown(st.raw);
    }
    _streamState.delete(instanceId);
}

export function clearStreamState(instanceId) {
    _streamState.delete(instanceId);
}

/** Attach a tool-call block to the currently streaming message for instanceId */
export function appendToolCallBlock(container, instanceId, toolName, args) {
    let st = _streamState.get(instanceId);
    if (!st) {
        // create a new block if nothing streaming
        const label = toolName ? `tool` : 'agent';
        const { wrapper, textEl, contentEl } = renderMessageBlock(container, 'model', label, []);
        st = { container, wrapper, textEl, contentEl, raw: '', roleId: '', label };
        _streamState.set(instanceId, st);
    }

    const toolBlock = document.createElement('div');
    toolBlock.className = 'tool-block';
    toolBlock.dataset.toolName = toolName;
    toolBlock.innerHTML = `
        <div class="tool-header" onclick="this.nextElementSibling.classList.toggle('open')">
            <div class="tool-title">
                <svg viewBox="0 0 24 24" fill="none" class="icon" style="width:14px;height:14px;"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" stroke="currentColor" stroke-width="2"/></svg>
                <span class="name">${toolName}</span>
            </div>
            <div class="tool-status"><div class="spinner"></div></div>
        </div>
        <div class="tool-body">
            <div class="tool-args">${JSON.stringify(args || {}, null, 2)}</div>
            <div class="tool-result">Processing…</div>
        </div>
    `;
    st.contentEl.appendChild(toolBlock);
    _scrollBottom(container);
    return toolBlock;
}

export function updateToolResult(instanceId, toolName, result, isError) {
    const st = _streamState.get(instanceId);
    if (!st) return;

    // find the last open tool block with matching name
    const blocks = st.contentEl.querySelectorAll(`.tool-block[data-tool-name="${toolName}"]`);
    const toolBlock = blocks[blocks.length - 1];
    if (!toolBlock) return;

    const statusEl = toolBlock.querySelector('.tool-status');
    const resultEl = toolBlock.querySelector('.tool-result');
    if (isError) {
        statusEl.innerHTML = `<svg class="status-icon status-error" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>`;
        resultEl.classList.add('error-text');
    } else {
        statusEl.innerHTML = `<svg class="status-icon status-success" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>`;
        resultEl.classList.remove('error-text');
    }
    const val = typeof result === 'object' ? JSON.stringify(result, null, 2) : String(result ?? '');
    resultEl.innerHTML = parseMarkdown(val);
    _scrollBottom(st.container);
}

// ─── Private helpers ──────────────────────────────────────────────────────────

function _renderParts(contentEl, textEl, parts, pendingToolBlocks) {
    let combinedText = '';

    parts.forEach(part => {
        const kind = part.part_kind;

        if (kind === 'text' || kind === 'user-prompt') {
            combinedText += (part.content || '') + '\n\n';
        } else if (kind === 'tool-call' || (part.tool_name && part.args !== undefined)) {
            // flush text first
            if (combinedText.trim()) {
                textEl.innerHTML = parseMarkdown(combinedText.trim());
                combinedText = '';
            }
            const tb = _buildToolBlock(part.tool_name, part.args);
            contentEl.appendChild(tb);
            pendingToolBlocks[part.tool_name] = tb.querySelector('.tool-result');
        } else if (kind === 'tool-return') {
            const rd = pendingToolBlocks[part.tool_name];
            if (rd) {
                const val = typeof part.content === 'object'
                    ? JSON.stringify(part.content, null, 2)
                    : String(part.content);
                rd.innerHTML = parseMarkdown(val);
                const status = rd.closest('.tool-block')?.querySelector('.tool-status');
                if (status) status.innerHTML = `<svg class="status-icon status-success" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>`;
            }
        }
    });

    if (combinedText.trim()) {
        textEl.innerHTML = parseMarkdown(combinedText.trim());
    }
}

function _buildToolBlock(toolName, args) {
    const tb = document.createElement('div');
    tb.className = 'tool-block';
    tb.dataset.toolName = toolName;
    tb.innerHTML = `
        <div class="tool-header" onclick="this.nextElementSibling.classList.toggle('open')">
            <div class="tool-title">
                <svg viewBox="0 0 24 24" fill="none" class="icon" style="width:14px;height:14px;"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" stroke="currentColor" stroke-width="2"/></svg>
                <span class="name">${toolName}</span>
            </div>
            <div class="tool-status"><svg class="status-icon status-success" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg></div>
        </div>
        <div class="tool-body">
            <div class="tool-args">${JSON.stringify(args || {}, null, 2)}</div>
            <div class="tool-result"></div>
        </div>
    `;
    return tb;
}

function _roleClass(role, label) {
    if (label?.toLowerCase().includes('coordinator')) return 'role-coordinator_agent';
    if (role === 'user') return 'role-user';
    return 'role-agent';
}

function _labelFromRole(role, instanceId) {
    if (role === 'user') return 'System';
    return instanceId ? instanceId.slice(0, 8) : 'Agent';
}

function _scrollBottom(container) {
    if (container) container.scrollTop = container.scrollHeight;
}
