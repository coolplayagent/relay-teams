/**
 * components/messageRenderer/helpers/toolBlocks.js
 * Tool block rendering and mutation helpers.
 */
import { syncApprovalStateFromEnvelope } from './approval.js';
import { renderRichContent } from './content.js';

export function buildToolBlock(toolName, args, toolCallId = null) {
    const tb = document.createElement('div');
    tb.className = 'tool-block';
    tb.dataset.toolName = toolName;
    if (toolCallId) {
        tb.dataset.toolCallId = toolCallId;
    }
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

export function findToolBlock(contentEl, toolName, toolCallId) {
    if (toolCallId) {
        const byCallId = contentEl.querySelector(`.tool-block[data-tool-call-id="${toolCallId}"]`);
        if (byCallId) return byCallId;
    }
    return findLatestToolBlock(contentEl, toolName);
}

export function setToolValidationFailureState(toolBlock, payload) {
    const statusEl = toolBlock.querySelector('.tool-status');
    const resultEl = toolBlock.querySelector('.tool-result');
    if (!statusEl || !resultEl) return;

    statusEl.innerHTML = `<svg class="status-icon status-warning" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.29 3.86l-8.2 14.2A2 2 0 0 0 3.8 21h16.4a2 2 0 0 0 1.73-2.94l-8.2-14.2a2 2 0 0 0-3.46 0z"/></svg>`;
    resultEl.classList.remove('error-text');
    resultEl.classList.add('warning-text');
    renderRichContent(resultEl, formatValidationDetails(payload));
}

export function applyToolReturn(toolBlock, content) {
    const statusEl = toolBlock.querySelector('.tool-status');
    const resultEl = toolBlock.querySelector('.tool-result');
    if (!statusEl || !resultEl) return;

    const isError = isToolEnvelopeError(content);
    if (isError) {
        statusEl.innerHTML = `<svg class="status-icon status-error" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>`;
        resultEl.classList.add('error-text');
        resultEl.classList.remove('warning-text');
    } else {
        statusEl.innerHTML = `<svg class="status-icon status-success" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>`;
        resultEl.classList.remove('error-text');
        resultEl.classList.remove('warning-text');
    }

    const val = typeof content === 'object' ? JSON.stringify(content, null, 2) : String(content);
    renderRichContent(resultEl, val);
    syncApprovalStateFromEnvelope(toolBlock, content);
}

export function indexPendingToolBlock(pendingToolBlocks, toolBlock, toolName, toolCallId) {
    pendingToolBlocks[pendingToolKey(toolName, toolCallId)] = toolBlock;
    if (toolName) {
        pendingToolBlocks[pendingToolKey(toolName, null)] = toolBlock;
    }
}

export function resolvePendingToolBlock(pendingToolBlocks, toolName, toolCallId) {
    if (toolCallId) {
        const byId = pendingToolBlocks[pendingToolKey(toolName, toolCallId)];
        if (byId) return byId;
    }
    return pendingToolBlocks[pendingToolKey(toolName, null)] || null;
}

export function findToolBlockInContainer(container, toolName, toolCallId, preferIdOnly = false) {
    if (toolCallId) {
        const byId = container.querySelector(`.tool-block[data-tool-call-id="${toolCallId}"]`);
        if (byId) return byId;
        if (preferIdOnly) return null;
    }
    if (!toolName) return null;
    const blocks = container.querySelectorAll(`.tool-block[data-tool-name="${toolName}"]`);
    return blocks.length > 0 ? blocks[blocks.length - 1] : null;
}

function findLatestToolBlock(contentEl, toolName) {
    if (!toolName) return null;
    const blocks = contentEl.querySelectorAll(`.tool-block[data-tool-name="${toolName}"]`);
    return blocks.length > 0 ? blocks[blocks.length - 1] : null;
}

function formatValidationDetails(payload) {
    const reason = payload?.reason || 'Input validation failed before tool execution.';
    const details = payload?.details;
    if (details === undefined || details === null || details === '') {
        return `${reason}\n\nTool was not executed.`;
    }

    let detailsText = '';
    try {
        detailsText = typeof details === 'string' ? details : JSON.stringify(details, null, 2);
    } catch (e) {
        detailsText = String(details);
    }
    return `${reason}\n\nTool was not executed.\n\n\`\`\`json\n${detailsText}\n\`\`\``;
}

function isToolEnvelopeError(content) {
    return !!(content && typeof content === 'object' && content.ok === false);
}

function pendingToolKey(toolName, toolCallId) {
    if (toolCallId) return `id:${toolCallId}`;
    return `name:${toolName || ''}`;
}
