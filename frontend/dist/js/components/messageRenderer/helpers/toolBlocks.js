/**
 * components/messageRenderer/helpers/toolBlocks.js
 * Tool block rendering and mutation helpers.
 */
import { syncApprovalStateFromEnvelope } from './approval.js';
import { appendStructuredContentPart, renderRichContent } from './content.js';
import { t, formatMessage } from '../../../utils/i18n.js';

const TOOL_SUMMARY_MAP = {
    shell:     { key: 'tool.summary.shell',     fields: ['command', 'cmd'], lang: 'tool.lang.shell', detailKind: 'command' },
    read:      { key: 'tool.summary.read',      fields: ['path', 'file_path', 'filepath', 'target_path'], detailKind: 'value' },
    write:     { key: 'tool.summary.write',     fields: ['path', 'file_path', 'filepath', 'target_path'], detailKind: 'value' },
    write_tmp: { key: 'tool.summary.write',     fields: ['path', 'file_path', 'filepath', 'target_path'], detailKind: 'value' },
    edit:      { key: 'tool.summary.edit',      fields: ['path', 'file_path', 'filepath', 'target_path'], detailKind: 'value' },
    grep:      { key: 'tool.summary.grep',      fields: ['pattern', 'query'], detailKind: 'value' },
    glob:      { key: 'tool.summary.glob',      fields: ['pattern', 'glob'], detailKind: 'value' },
    websearch: { key: 'tool.summary.websearch', fields: ['query', 'q', 'search_query'], detailKind: 'value' },
    webfetch:  { key: 'tool.summary.webfetch',  fields: ['url', 'uri'], detailKind: 'value' },
};

const COPY_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px;"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
const CHECK_SVG = '<svg class="status-icon status-success" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px;"><path d="M20 6L9 17l-5-5"/></svg>';
const ERROR_SVG = '<svg class="status-icon status-error" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px;"><path d="M18 6L6 18M6 6l12 12"/></svg>';
const WARNING_SVG = '<svg class="status-icon status-warning" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px;"><path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.29 3.86l-8.2 14.2A2 2 0 0 0 3.8 21h16.4a2 2 0 0 0 1.73-2.94l-8.2-14.2a2 2 0 0 0-3.46 0z"/></svg>';
const SPINNER_HTML = '<div class="spinner"></div>';

function classifyTool(toolName, args) {
    const normalizedArgs = normalizeToolArgs(args);
    const entry = TOOL_SUMMARY_MAP[toolName];
    if (entry) {
        const detailText = pickToolFieldValue(normalizedArgs, entry.fields);
        return {
            summaryLabel: t(entry.key),
            langLabel: entry.lang ? t(entry.lang) : '',
            preview: truncatePreview(detailText || argsPreviewText(normalizedArgs)),
            detailText: detailText || '',
            detailKind: entry.detailKind || 'json',
            args: normalizedArgs,
        };
    }
    return {
        summaryLabel: formatMessage('tool.summary.generic', { tool: toolName }),
        langLabel: '',
        preview: truncatePreview(argsPreviewText(normalizedArgs)),
        detailText: '',
        detailKind: 'json',
        args: normalizedArgs,
    };
}

function normalizeToolArgs(args) {
    if (!args) return {};
    if (typeof args === 'object') return args;
    const raw = String(args || '').trim();
    if (!raw) return {};
    try {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === 'object') {
            return parsed;
        }
        return { __raw: String(parsed ?? '') };
    } catch (_) {
        return { __raw: raw };
    }
}

function pickToolFieldValue(args, fields = []) {
    if (!args || typeof args !== 'object') return '';
    for (const field of fields) {
        const value = args[field];
        if (value === undefined || value === null) continue;
        const text = String(value).trim();
        if (text) return text;
    }
    const raw = typeof args.__raw === 'string' ? args.__raw.trim() : '';
    return raw;
}

function truncatePreview(text, maxLen = 80) {
    if (!text) return '';
    if (text.length <= maxLen) return text;
    return text.slice(0, maxLen) + '...';
}

function argsPreviewText(args) {
    if (!args || typeof args !== 'object') return '';
    if (typeof args.__raw === 'string' && Object.keys(args).length === 1) {
        return args.__raw;
    }
    try {
        const str = JSON.stringify(args);
        return str.length > 2 ? str : '';
    } catch (_) {
        return '';
    }
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

export function buildToolBlock(toolName, args, toolCallId = null) {
    const info = classifyTool(toolName, args);
    const tb = document.createElement('details');
    tb.className = 'tool-block';
    tb.dataset.toolName = toolName;
    if (toolCallId) {
        tb.dataset.toolCallId = toolCallId;
    }

    const langHeader = info.langLabel
        ? `<span class="tool-lang-label">${escapeHtml(info.langLabel)}</span>`
        : '<span></span>';

    tb.innerHTML = `
        <summary class="tool-summary">
            <span class="tool-summary-label">${escapeHtml(info.summaryLabel)}</span>
            <span class="tool-summary-preview">${escapeHtml(info.preview)}</span>
            <span class="tool-status">${CHECK_SVG}</span>
        </summary>
        <div class="tool-detail">
            <div class="tool-detail-card">
                <div class="tool-detail-header">
                    ${langHeader}
                    <button class="tool-copy-btn" title="${escapeHtml(t('tool.action.copy'))}">${COPY_SVG}</button>
                </div>
                ${renderToolInput(info)}
                <div class="tool-output"></div>
            </div>
        </div>
    `;

    const copyBtn = tb.querySelector('.tool-copy-btn');
    if (copyBtn) {
        copyBtn.addEventListener('click', handleCopyClick);
    }

    tb.dataset.status = 'completed';
    return tb;
}

export function buildPendingToolBlock(toolName, args, toolCallId = null) {
    const info = classifyTool(toolName, args);
    const tb = document.createElement('details');
    tb.className = 'tool-block';
    tb.dataset.toolName = toolName;
    if (toolCallId) {
        tb.dataset.toolCallId = toolCallId;
    }

    const langHeader = info.langLabel
        ? `<span class="tool-lang-label">${escapeHtml(info.langLabel)}</span>`
        : '<span></span>';

    tb.innerHTML = `
        <summary class="tool-summary">
            <span class="tool-summary-label">${escapeHtml(info.summaryLabel)}</span>
            <span class="tool-summary-preview">${escapeHtml(info.preview)}</span>
            <span class="tool-status">${SPINNER_HTML}</span>
        </summary>
        <div class="tool-detail">
            <div class="tool-detail-card">
                <div class="tool-detail-header">
                    ${langHeader}
                    <button class="tool-copy-btn" title="${escapeHtml(t('tool.action.copy'))}">${COPY_SVG}</button>
                </div>
                ${renderToolInput(info)}
                <div class="tool-output"></div>
            </div>
        </div>
    `;

    const copyBtn = tb.querySelector('.tool-copy-btn');
    if (copyBtn) {
        copyBtn.addEventListener('click', handleCopyClick);
    }

    tb.dataset.status = 'running';
    return tb;
}

function formatArgs(args) {
    if (args && typeof args === 'object' && typeof args.__raw === 'string' && Object.keys(args).length === 1) {
        return args.__raw;
    }
    try {
        return JSON.stringify(args || {}, null, 2);
    } catch (_) {
        return String(args || '');
    }
}

function renderToolInput(info) {
    if (info.detailKind === 'command' && info.detailText) {
        return `<div class="tool-command"><span class="tool-prompt">$</span> <code>${escapeHtml(info.detailText)}</code></div>`;
    }
    if (info.detailKind === 'value' && info.detailText) {
        return `<div class="tool-input-value"><code>${escapeHtml(info.detailText)}</code></div>`;
    }
    return `<pre class="tool-args">${escapeHtml(formatArgs(info.args))}</pre>`;
}

export function findToolBlock(contentEl, toolName, toolCallId) {
    if (toolCallId) {
        const byCallId = contentEl.querySelector(`.tool-block[data-tool-call-id="${toolCallId}"]`);
        if (byCallId) return byCallId;
    }
    return findLatestToolBlock(contentEl, toolName);
}

export function setToolValidationFailureState(toolBlock, payload) {
    const outputEl = toolBlock.querySelector('.tool-output');
    setToolStatus(toolBlock, 'warning');
    if (outputEl) {
        outputEl.classList.remove('error-text');
        outputEl.classList.add('warning-text');
        renderRichContent(outputEl, formatValidationDetails(payload));
    }
}

export function applyToolReturn(toolBlock, content) {
    const outputEl = toolBlock.querySelector('.tool-output');

    const isError = isToolEnvelopeError(content);
    if (isError) {
        setToolStatus(toolBlock, 'error');
        if (outputEl) {
            outputEl.classList.add('error-text');
            outputEl.classList.remove('warning-text');
        }
    } else {
        setToolStatus(toolBlock, 'completed');
        if (outputEl) {
            outputEl.classList.remove('error-text');
            outputEl.classList.remove('warning-text');
        }
    }

    if (outputEl) {
        renderToolResultContent(outputEl, content, toolBlock.dataset.toolName);
    }
    syncApprovalStateFromEnvelope(toolBlock, content);
}

export function setToolStatus(toolBlock, status) {
    const statusEl = toolBlock?.querySelector('.tool-status');
    if (!toolBlock || !statusEl) return;
    if (status === 'running') {
        statusEl.innerHTML = SPINNER_HTML;
    } else if (status === 'error') {
        statusEl.innerHTML = ERROR_SVG;
    } else if (status === 'warning') {
        statusEl.innerHTML = WARNING_SVG;
    } else {
        statusEl.innerHTML = CHECK_SVG;
    }
    toolBlock.dataset.status = String(status || '');
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

function renderToolResultContent(targetEl, content, toolName) {
    targetEl.replaceChildren();
    if (content && typeof content === 'object' && typeof content.ok === 'boolean') {
        renderEnvelopeResult(targetEl, content, toolName);
        return;
    }
    if (renderStructuredPayload(targetEl, content)) {
        return;
    }
    const val = typeof content === 'object' ? JSON.stringify(content, null, 2) : String(content);
    targetEl.textContent = val;
}

function renderEnvelopeResult(targetEl, envelope, toolName) {
    if (envelope.ok !== true) {
        const error = envelope.error && typeof envelope.error === 'object'
            ? envelope.error.message || JSON.stringify(envelope.error, null, 2)
            : JSON.stringify(envelope, null, 2);
        targetEl.textContent = String(error || 'Tool execution failed.');
        return;
    }

    const data = envelope.data;

    if (toolName === 'shell' && data && typeof data === 'object') {
        const output = String(data.output || '');
        targetEl.textContent = output;
        return;
    }

    if (renderStructuredPayload(targetEl, data, envelope.meta)) {
        return;
    }
    const val = typeof data === 'object'
        ? JSON.stringify(data, null, 2)
        : String(data ?? '');
    targetEl.textContent = val;
}

function renderStructuredPayload(targetEl, payload, meta = null) {
    if (!payload || typeof payload !== 'object') return false;

    const hasStructuredContent = Array.isArray(payload.content);
    const hasText = typeof payload.text === 'string' && payload.text.trim();
    const computer = payload.computer && typeof payload.computer === 'object' ? payload.computer : null;
    if (!hasStructuredContent && !hasText && !computer) {
        return false;
    }

    if (computer) {
        const metaEl = document.createElement('div');
        metaEl.className = 'tool-computer-meta';
        metaEl.textContent = [
            computer.source ? `source ${computer.source}` : '',
            computer.action ? `action ${computer.action}` : '',
            computer.risk_level ? `risk ${computer.risk_level}` : '',
            computer.target_summary ? `target ${computer.target_summary}` : '',
        ].filter(Boolean).join(' · ');
        if (metaEl.textContent) {
            targetEl.appendChild(metaEl);
        }
    } else if (meta && typeof meta === 'object') {
        const metaEl = document.createElement('div');
        metaEl.className = 'tool-computer-meta';
        metaEl.textContent = [
            typeof meta.source === 'string' && meta.source ? `source ${meta.source}` : '',
            typeof meta.risk_level === 'string' && meta.risk_level ? `risk ${meta.risk_level}` : '',
            typeof meta.target_summary === 'string' && meta.target_summary ? `target ${meta.target_summary}` : '',
        ].filter(Boolean).join(' · ');
        if (metaEl.textContent) {
            targetEl.appendChild(metaEl);
        }
    }

    if (hasText) {
        const textEl = document.createElement('div');
        textEl.className = 'msg-text';
        renderRichContent(textEl, String(payload.text || ''));
        targetEl.appendChild(textEl);
    }
    if (hasStructuredContent) {
        payload.content.forEach(part => {
            appendStructuredContentPart(targetEl, part);
        });
    }
    if (!hasText && !hasStructuredContent) {
        renderRichContent(targetEl, JSON.stringify(payload, null, 2));
    }
    return true;
}

function handleCopyClick(event) {
    event.preventDefault();
    event.stopPropagation();
    const btn = event.currentTarget;
    const toolBlock = btn.closest('.tool-block');
    if (!toolBlock) return;

    const outputEl = toolBlock.querySelector('.tool-output');
    const commandEl = toolBlock.querySelector('.tool-command code')
        || toolBlock.querySelector('.tool-input-value code')
        || toolBlock.querySelector('.tool-args');
    const parts = [];
    if (commandEl) parts.push(commandEl.textContent || '');
    if (outputEl) parts.push(outputEl.textContent || '');
    const textToCopy = parts.filter(Boolean).join('\n\n');

    navigator.clipboard.writeText(textToCopy).then(() => {
        const original = btn.innerHTML;
        btn.innerHTML = `${CHECK_SVG}`;
        btn.title = t('tool.action.copied');
        setTimeout(() => {
            btn.innerHTML = original;
            btn.title = t('tool.action.copy');
        }, 1500);
    }).catch(() => {});
}

function pendingToolKey(toolName, toolCallId) {
    if (toolCallId) return `id:${toolCallId}`;
    return `name:${toolName || ''}`;
}
