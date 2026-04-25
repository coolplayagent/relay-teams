/**
 * components/messageRenderer/helpers/toolBlocks.js
 * Tool block rendering and mutation helpers.
 */
import { syncApprovalStateFromEnvelope } from './approval.js';
import { appendStructuredContentPart, renderRichContent } from './content.js';
import { t, formatMessage } from '../../../utils/i18n.js';

const TOOL_SUMMARY_MAP = {
    shell:     { key: 'tool.summary.shell',     fields: ['command', 'cmd'], detailKind: 'command' },
    read:      { key: 'tool.summary.read',      fields: ['path', 'file_path', 'filepath', 'target_path'], detailKind: 'read' },
    write:     { key: 'tool.summary.write',     fields: ['path', 'file_path', 'filepath', 'target_path'], detailKind: 'write' },
    edit:      { key: 'tool.summary.edit',      fields: ['path', 'file_path', 'filepath', 'target_path'], detailKind: 'edit' },
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
const MAX_DIFF_DP_CELLS = 50000;
const MAX_DIFF_TOTAL_LINES = 600;
const MAX_WRITE_PREVIEW_LINES = 200;
const MAX_WRITE_PREVIEW_CHARS = 12000;
const INLINE_READ_TAGS = new Set(['path', 'type']);

function classifyTool(toolName, args) {
    const normalizedArgs = normalizeToolArgs(args);
    const entry = TOOL_SUMMARY_MAP[toolName];
    if (entry) {
        const detailText = pickToolFieldValue(normalizedArgs, entry.fields);
        let preview = detailText || argsPreviewText(normalizedArgs);
        if (entry.detailKind === 'read' && detailText) {
            const lineInfo = buildLineRangeText(normalizedArgs);
            if (lineInfo) preview = `${detailText} ${lineInfo}`;
        }
        return {
            summaryLabel: t(entry.key),
            langLabel: entry.lang ? t(entry.lang) : '',
            preview: truncatePreview(preview),
            detailText: detailText || '',
            detailKind: entry.detailKind || 'json',
            args: normalizedArgs,
        };
    }
    const genericPreview = pickGenericPreview(normalizedArgs);
    return {
        summaryLabel: formatMessage('tool.summary.generic', { tool: toolName }),
        langLabel: '',
        preview: truncatePreview(genericPreview || argsPreviewText(normalizedArgs)),
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
        // Fallback: try to extract a JSON object or array from the string
        const objMatch = raw.match(/(\{[\s\S]*\})/);
        if (objMatch) {
            try {
                const extracted = JSON.parse(objMatch[1]);
                if (extracted && typeof extracted === 'object') return extracted;
            } catch (_e) { /* ignore */ }
        }
        const arrMatch = raw.match(/(\[[\s\S]*\])/);
        if (arrMatch) {
            try {
                const extracted = JSON.parse(arrMatch[1]);
                if (Array.isArray(extracted)) return { __items: extracted };
            } catch (_e) { /* ignore */ }
        }
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

const GENERIC_PREVIEW_FIELDS = [
    'title', 'name', 'description', 'query', 'path', 'url',
    'command', 'prompt', 'message', 'content', 'text', 'objective',
];

function pickGenericPreview(args) {
    if (!args || typeof args !== 'object') return '';
    const direct = pickToolFieldValue(args, GENERIC_PREVIEW_FIELDS);
    if (direct) return direct;
    // Try first item of the first array-valued field
    for (const key of Object.keys(args)) {
        const val = args[key];
        if (Array.isArray(val) && val.length > 0 && val[0] && typeof val[0] === 'object') {
            const nested = pickToolFieldValue(val[0], GENERIC_PREVIEW_FIELDS);
            if (nested) return nested;
        }
    }
    return '';
}

function buildLineRangeText(args) {
    const offset = Number(args.offset || args.line || args.start || 0);
    const limit = Number(args.limit || args.count || 0);
    if (!offset && !limit) return '';
    const startLine = offset || 1;
    if (limit) return `L${startLine}-${startLine + limit - 1}`;
    return `L${startLine}`;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function escapeHtmlInline(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function renderLinedContent(text, startLine = 1) {
    const lines = String(text).split('\n');
    const endLine = startLine + lines.length - 1;
    const padWidth = String(endLine).length;
    return lines.map((line, i) => {
        const no = String(startLine + i).padStart(padWidth, '\u00a0');
        return `<div class="tool-line"><span class="tool-line-no">${no}</span><span class="tool-line-text">${escapeHtmlInline(line) || '\u00a0'}</span></div>`;
    }).join('');
}

function renderTaggedLineContent(text, fallbackStartLine = 1) {
    const parsedLines = String(text).split('\n').map(parseTaggedLine);
    const numberedLines = parsedLines.filter(line => line.lineNo != null);
    if (numberedLines.length === 0) {
        return renderLinedContent(text, fallbackStartLine);
    }
    const padWidth = Math.max(
        String(fallbackStartLine).length,
        ...numberedLines.map(line => String(line.lineNo).length),
    );
    return parsedLines.map(line => {
        const lineNo = line.lineNo != null
            ? String(line.lineNo).padStart(padWidth, '\u00a0')
            : ''.padStart(padWidth, '\u00a0');
        return `<div class="tool-line"><span class="tool-line-no">${lineNo}</span><span class="tool-line-text">${escapeHtmlInline(line.text) || '\u00a0'}</span></div>`;
    }).join('');
}

function parseTaggedLine(line) {
    const match = String(line).match(/^(\d+):\s?(.*)$/);
    if (!match) {
        return { lineNo: null, text: String(line) };
    }
    return {
        lineNo: Number(match[1]),
        text: match[2] || '',
    };
}

export function buildToolBlock(toolName, args, toolCallId = null) {
    const info = classifyTool(toolName, args);
    const tb = document.createElement('details');
    tb.className = 'tool-block';
    tb.dataset.toolName = toolName;
    if (toolCallId) {
        tb.dataset.toolCallId = toolCallId;
    }
    if (toolName === 'read') {
        const off = Number(info.args.offset || info.args.line || info.args.start || 0);
        if (off > 0) tb.dataset.readOffset = String(off);
    }

    const hasLangLabel = !!info.langLabel;
    const headerClass = hasLangLabel ? 'tool-detail-header' : 'tool-detail-header tool-detail-header--minimal';
    const langSpan = hasLangLabel ? `<span class="tool-lang-label">${escapeHtml(info.langLabel)}</span>` : '';

    tb.innerHTML = `
        <summary class="tool-summary">
            <span class="tool-summary-label">${escapeHtml(info.summaryLabel)}</span>
            <span class="tool-summary-preview">${escapeHtml(info.preview)}</span>
            <span class="tool-status">${CHECK_SVG}</span>
        </summary>
        <div class="tool-detail">
            <div class="tool-detail-card">
                <div class="${headerClass}">
                    ${langSpan}
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
    if (toolName === 'read') {
        const off = Number(info.args.offset || info.args.line || info.args.start || 0);
        if (off > 0) tb.dataset.readOffset = String(off);
    }

    const hasLangLabel = !!info.langLabel;
    const headerClass = hasLangLabel ? 'tool-detail-header' : 'tool-detail-header tool-detail-header--minimal';
    const langSpan = hasLangLabel ? `<span class="tool-lang-label">${escapeHtml(info.langLabel)}</span>` : '';

    tb.innerHTML = `
        <summary class="tool-summary">
            <span class="tool-summary-label">${escapeHtml(info.summaryLabel)}</span>
            <span class="tool-summary-preview">${escapeHtml(info.preview)}</span>
            <span class="tool-status">${SPINNER_HTML}</span>
        </summary>
        <div class="tool-detail">
            <div class="tool-detail-card">
                <div class="${headerClass}">
                    ${langSpan}
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
        return `<div class="tool-command"><code>${escapeHtml(info.detailText)}</code></div>`;
    }
    if (info.detailKind === 'read' && info.detailText) {
        return renderReadInput(info);
    }
    if (info.detailKind === 'write' && info.detailText) {
        return renderWriteInput(info);
    }
    if (info.detailKind === 'edit' && info.detailText) {
        return renderEditInput(info);
    }
    if (info.detailKind === 'value' && info.detailText) {
        return `<div class="tool-input-value"><code>${escapeHtml(info.detailText)}</code></div>`;
    }
    return `<pre class="tool-args">${escapeHtml(formatArgs(info.args))}</pre>`;
}

function renderReadInput(info) {
    const lineRange = buildLineRangeText(info.args);
    const lineRangeHtml = lineRange
        ? `<span class="tool-line-range">${escapeHtml(lineRange)}</span>`
        : '';
    return `<div class="tool-input-value"><code>${escapeHtml(info.detailText)}</code>${lineRangeHtml}</div>`;
}

function renderWriteInput(info) {
    const content = info.args.content || '';
    let html = `<div class="tool-input-value"><code>${escapeHtml(info.detailText)}</code></div>`;
    if (content) {
        const preview = buildBoundedPreview(String(content), {
            maxLines: MAX_WRITE_PREVIEW_LINES,
            maxChars: MAX_WRITE_PREVIEW_CHARS,
        });
        html += `<div class="tool-lined-code tool-lined-code--scroll">${renderLinedContent(preview.text)}</div>`;
    }
    return html;
}

function renderEditInput(info) {
    const oldStr = String(info.args.old_string ?? '');
    const newStr = String(info.args.new_string ?? '');
    let html = `<div class="tool-input-value"><code>${escapeHtml(info.detailText)}</code></div>`;
    if (oldStr || newStr) {
        html += renderSideBySideDiff(oldStr, newStr);
    }
    return html;
}

function renderSideBySideDiff(oldText, newText) {
    const oldLines = oldText.split('\n');
    const newLines = newText.split('\n');
    const pairs = diffLinePairs(oldLines, newLines);
    let html = '<div class="tool-diff-side">';
    for (const p of pairs) {
        const cls = p.type === 'same' ? 'tool-diff-row' : 'tool-diff-row tool-diff-row--changed';
        const oTxt = p.old != null ? (escapeHtmlInline(p.old) || '\u00a0') : '\u00a0';
        const nTxt = p.new != null ? (escapeHtmlInline(p.new) || '\u00a0') : '\u00a0';
        html += `<div class="${cls}"><span class="tool-diff-cell tool-diff-cell--old">${oTxt}</span><span class="tool-diff-cell tool-diff-cell--new">${nTxt}</span></div>`;
    }
    html += '</div>';
    return html;
}

function diffLinePairs(oldLines, newLines) {
    const m = oldLines.length, n = newLines.length;
    if (m === 0 || n === 0) {
        return pairLinesByIndex(oldLines, newLines);
    }
    if ((m * n) > MAX_DIFF_DP_CELLS || (m + n) > MAX_DIFF_TOTAL_LINES) {
        return pairLinesByIndex(oldLines, newLines);
    }
    // LCS DP
    const dp = Array.from({ length: m + 1 }, () => new Uint16Array(n + 1));
    for (let i = 1; i <= m; i++) {
        for (let j = 1; j <= n; j++) {
            dp[i][j] = oldLines[i - 1] === newLines[j - 1]
                ? dp[i - 1][j - 1] + 1
                : Math.max(dp[i - 1][j], dp[i][j - 1]);
        }
    }
    // Backtrack LCS matches
    const matches = [];
    let i = m, j = n;
    while (i > 0 && j > 0) {
        if (oldLines[i - 1] === newLines[j - 1]) {
            matches.unshift({ oi: i - 1, ni: j - 1 });
            i--; j--;
        } else if (dp[i - 1][j] >= dp[i][j - 1]) {
            i--;
        } else {
            j--;
        }
    }
    // Build raw entries from LCS
    const raw = [];
    let oi = 0, ni = 0;
    for (const mt of matches) {
        while (oi < mt.oi) { raw.push({ type: 'removed', old: oldLines[oi], oldNo: oi + 1 }); oi++; }
        while (ni < mt.ni) { raw.push({ type: 'added', new: newLines[ni], newNo: ni + 1 }); ni++; }
        raw.push({ type: 'same', old: oldLines[oi], new: newLines[ni], oldNo: oi + 1, newNo: ni + 1 });
        oi++; ni++;
    }
    while (oi < m) { raw.push({ type: 'removed', old: oldLines[oi], oldNo: oi + 1 }); oi++; }
    while (ni < n) { raw.push({ type: 'added', new: newLines[ni], newNo: ni + 1 }); ni++; }
    // Merge adjacent removed+added into side-by-side changed pairs
    const pairs = [];
    let ri = 0;
    while (ri < raw.length) {
        if (raw[ri].type === 'same') { pairs.push(raw[ri]); ri++; continue; }
        const rem = [], add = [];
        while (ri < raw.length && raw[ri].type === 'removed') { rem.push(raw[ri]); ri++; }
        while (ri < raw.length && raw[ri].type === 'added') { add.push(raw[ri]); ri++; }
        const len = Math.max(rem.length, add.length);
        for (let k = 0; k < len; k++) {
            pairs.push({
                type: 'changed',
                old: rem[k]?.old ?? null, oldNo: rem[k]?.oldNo ?? null,
                new: add[k]?.new ?? null, newNo: add[k]?.newNo ?? null,
            });
        }
    }
    return pairs;
}

function pairLinesByIndex(oldLines, newLines) {
    const len = Math.max(oldLines.length, newLines.length);
    const pairs = [];
    for (let i = 0; i < len; i++) {
        const hasOld = i < oldLines.length;
        const hasNew = i < newLines.length;
        pairs.push({
            type: hasOld && hasNew && oldLines[i] === newLines[i] ? 'same' : 'changed',
            old: hasOld ? oldLines[i] : null,
            oldNo: hasOld ? i + 1 : null,
            new: hasNew ? newLines[i] : null,
            newNo: hasNew ? i + 1 : null,
        });
    }
    return pairs;
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
    if (toolCallId) {
        pendingToolBlocks[pendingToolKey(toolName, toolCallId)] = toolBlock;
    }
    if (toolName) {
        const nameKey = pendingToolKey(toolName, null);
        const bucket = Array.isArray(pendingToolBlocks[nameKey])
            ? pendingToolBlocks[nameKey]
            : [];
        bucket.push(toolBlock);
        pendingToolBlocks[nameKey] = bucket;
    }
}

export function resolvePendingToolBlock(pendingToolBlocks, toolName, toolCallId) {
    if (toolCallId) {
        const byId = pendingToolBlocks[pendingToolKey(toolName, toolCallId)];
        if (byId) return byId;
    }
    const byName = pendingToolBlocks[pendingToolKey(toolName, null)];
    if (Array.isArray(byName)) {
        const liveCandidates = byName.filter(block => {
            const status = String(block?.dataset?.status || '').trim().toLowerCase();
            return !['completed', 'error', 'validation_failed'].includes(status);
        });
        if (liveCandidates.length === 1) return liveCandidates[0];
        if (byName.length === 1) return byName[0];
        return null;
    }
    return byName || null;
}

export function findToolBlockInContainer(container, toolName, toolCallId, preferIdOnly = false) {
    if (toolCallId) {
        const byId = container.querySelector(`.tool-block[data-tool-call-id="${toolCallId}"]`);
        if (byId) return byId;
        if (preferIdOnly) return null;
    }
    if (!toolName) return null;
    const blocks = container.querySelectorAll(`.tool-block[data-tool-name="${toolName}"]`);
    return blocks.length === 1 ? blocks[0] : null;
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

    if (toolName === 'read' && data != null) {
        if (renderStructuredPayload(targetEl, data, envelope.meta)) {
            return;
        }
        renderReadOutput(targetEl, data);
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

function renderReadOutput(targetEl, data) {
    const text = getReadOutputText(data);
    const toolBlock = targetEl.closest('.tool-block');
    const startLine = Math.max(1, Number(toolBlock?.dataset?.readOffset || 1));
    const container = document.createElement('div');
    container.className = 'tool-lined-code';
    const parsed = parseReadPayload(text);
    if (parsed?.instructions) {
        const instructionsEl = document.createElement('pre');
        instructionsEl.className = 'tool-args';
        instructionsEl.textContent = parsed.instructions;
        targetEl.appendChild(instructionsEl);
    }
    if (parsed?.content) {
        container.innerHTML = renderTaggedLineContent(parsed.content, startLine);
    } else if (parsed?.entries) {
        container.innerHTML = renderLinedContent(parsed.entries, startLine);
    } else {
        container.innerHTML = renderLinedContent(String(text), startLine);
    }
    targetEl.appendChild(container);
}

function getReadOutputText(data) {
    return typeof data === 'string' ? data
        : (data && typeof data === 'object')
            ? (data.content || data.text || data.output || JSON.stringify(data, null, 2))
            : String(data);
}

function parseReadPayload(text) {
    if (typeof text !== 'string' || !text.includes('<type>')) {
        return null;
    }
    const lines = text.split('\n');
    const path = extractTaggedSection(text, lines, 'path');
    const type = extractTaggedSection(text, lines, 'type');
    const instructions = extractTaggedSection(text, lines, 'instructions');
    const content = extractTaggedSection(text, lines, 'content');
    const entries = extractTaggedSection(text, lines, 'entries');
    if (!path && !type && !instructions && !content && !entries) {
        return null;
    }
    return { path, type, instructions, content, entries };
}

function extractTaggedSection(text, lines, tagName) {
    const inline = extractInlineTaggedSection(lines, tagName);
    if (inline) {
        return inline;
    }
    return extractBlockTaggedSection(lines, tagName);
}

function extractInlineTaggedSection(lines, tagName) {
    if (!INLINE_READ_TAGS.has(tagName)) {
        return '';
    }
    const tagPattern = new RegExp(`^<${tagName}>(.*)</${tagName}>$`);
    for (const line of lines) {
        const match = line.match(tagPattern);
        if (match) {
            return match[1].trim();
        }
    }
    return '';
}

function extractBlockTaggedSection(lines, tagName) {
    const startMarker = `<${tagName}>`;
    const endMarker = `</${tagName}>`;
    const startIndex = lines.indexOf(startMarker);
    if (startIndex === -1) {
        return '';
    }
    const endIndex = lines.lastIndexOf(endMarker);
    if (endIndex <= startIndex) {
        return '';
    }
    return lines.slice(startIndex + 1, endIndex).join('\n').replace(/^\n+|\n+$/g, '');
}

function buildBoundedPreview(text, { maxLines, maxChars }) {
    const source = String(text);
    const lines = source.split('\n');
    const previewLines = [];
    let charsUsed = 0;
    let truncated = false;

    for (let i = 0; i < lines.length; i++) {
        if (previewLines.length >= maxLines) {
            truncated = true;
            break;
        }
        const line = lines[i];
        const remainingChars = maxChars - charsUsed;
        if (remainingChars <= 0) {
            truncated = true;
            break;
        }
        if (line.length > remainingChars) {
            previewLines.push(line.slice(0, remainingChars));
            charsUsed += remainingChars;
            truncated = true;
            break;
        }
        previewLines.push(line);
        charsUsed += line.length;
    }

    if (!truncated) {
        return { text: source };
    }

    const previewText = previewLines.join('\n');
    const notice = `\n(Preview truncated. Showing first ${previewLines.length} of ${lines.length} lines.)`;
    return { text: `${previewText}${notice}` };
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
        renderRichContent(textEl, String(payload.text || ''), {
            enableWorkspaceImagePreview: !hasStructuredContent,
        });
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
