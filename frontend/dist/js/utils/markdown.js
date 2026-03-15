/**
 * utils/markdown.js
 * Renders markdown with an optional marked/highlight.js integration and a
 * built-in fallback for offline environments.
 */
import { showToast } from './feedback.js';

let markdownInteractionsBound = false;
let markedConfigured = false;

export function renderMarkdownToHtml(source = '') {
    const text = String(source || '');
    const markedRuntime = getMarkedRuntime();
    if (markedRuntime) {
        return markedRuntime.parse(text);
    }
    return renderFallbackMarkdown(text);
}

export function parseMarkdown(source = '') {
    ensureMarkdownInteractions();
    
    let processedSource = String(source || '');
    
    const lastThinkOpen = processedSource.lastIndexOf('<think>');
    const lastThinkClose = processedSource.lastIndexOf('</think>');
    const isStreamingThink = lastThinkOpen > lastThinkClose;
    
    if (isStreamingThink) {
        processedSource += '\n</think>';
    }

    processedSource = processedSource
        .replace(/<think>/g, '\n::THINK_START::\n')
        .replace(/<\/think>/g, '\n::THINK_END::\n');

    let rendered = renderMarkdownToHtml(processedSource);
    
    rendered = rendered.replace(/<p>\s*::THINK_START::\s*<\/p>/g, '::THINK_START::');
    rendered = rendered.replace(/<p>\s*::THINK_END::\s*<\/p>/g, '::THINK_END::');
    
    const totalThinkBlocks = (processedSource.match(/::THINK_START::/g) || []).length;
    let thinkBlockCount = 0;
    rendered = rendered.replace(/::THINK_START::/g, () => {
        thinkBlockCount++;
        const isOpen = isStreamingThink && thinkBlockCount === totalThinkBlocks;
        return `<details class="thinking-block"${isOpen ? ' open' : ''}><summary class="thinking-summary"><span class="thinking-label">Thinking</span><span class="thinking-live" style="display:${isOpen ? 'inline-flex' : 'none'};">Live</span></summary><div class="thinking-body"><div class="msg-text thinking-text">`;
    });
    rendered = rendered.replace(/::THINK_END::/g, '</div></div></details>');

    const template = document.createElement('template');
    template.innerHTML = rendered;

    template.content.querySelectorAll('pre').forEach(pre => {
        if (pre.parentElement?.classList.contains('markdown-code-block')) return;
        const code = pre.querySelector('code');
        if (!code) return;
        const wrapper = document.createElement('div');
        wrapper.className = 'markdown-code-block';
        const language = extractCodeLanguage(code);
        wrapper.dataset.language = language;

        const header = document.createElement('div');
        header.className = 'markdown-code-header';

        const label = document.createElement('span');
        label.className = 'markdown-code-language';
        label.textContent = formatCodeLanguage(language);
        header.appendChild(label);

        const copyButton = document.createElement('button');
        copyButton.type = 'button';
        copyButton.className = 'markdown-code-copy';
        copyButton.dataset.copyLabel = 'Copy';
        copyButton.dataset.copiedLabel = 'Copied';
        copyButton.textContent = 'Copy';
        copyButton.setAttribute('aria-label', `Copy ${formatCodeLanguage(language)} code`);
        header.appendChild(copyButton);

        pre.parentNode?.insertBefore(wrapper, pre);
        wrapper.appendChild(header);
        wrapper.appendChild(pre);
    });

    template.content.querySelectorAll('table').forEach(table => {
        if (table.parentElement?.classList.contains('markdown-table-wrap')) return;
        const wrapper = document.createElement('div');
        wrapper.className = 'markdown-table-wrap';
        table.parentNode?.insertBefore(wrapper, table);
        wrapper.appendChild(table);
    });

    return template.innerHTML;
}

function getMarkedRuntime() {
    const runtime = globalThis.marked;
    if (
        !runtime
        || typeof runtime.parse !== 'function'
        || typeof runtime.setOptions !== 'function'
    ) {
        return null;
    }

    if (!markedConfigured) {
        runtime.setOptions({
            gfm: true,
            breaks: true,
            highlight(code, lang) {
                return highlightCode(code, lang);
            },
        });
        markedConfigured = true;
    }

    return runtime;
}

function getHighlightRuntime() {
    const runtime = globalThis.hljs;
    if (
        !runtime
        || typeof runtime.highlight !== 'function'
        || typeof runtime.getLanguage !== 'function'
    ) {
        return null;
    }
    return runtime;
}

function renderFallbackMarkdown(source) {
    const normalized = String(source || '').replace(/\r\n?/g, '\n').trim();
    if (!normalized) return '';

    const fencePattern = /```([^\n`]*)\n?([\s\S]*?)```/g;
    const parts = [];
    let lastIndex = 0;
    let match = fencePattern.exec(normalized);
    while (match) {
        if (match.index > lastIndex) {
            parts.push(renderFallbackBlocks(normalized.slice(lastIndex, match.index)));
        }
        parts.push(renderCodeBlock(match[2], match[1]));
        lastIndex = fencePattern.lastIndex;
        match = fencePattern.exec(normalized);
    }

    if (lastIndex < normalized.length) {
        parts.push(renderFallbackBlocks(normalized.slice(lastIndex)));
    }

    return parts.join('');
}

function renderFallbackBlocks(source) {
    const blocks = String(source || '')
        .split(/\n{2,}/)
        .map(block => block.trim())
        .filter(Boolean);
    return blocks.map(renderFallbackBlock).join('');
}

function renderFallbackBlock(block) {
    const lines = block.split('\n').map(line => line.trimEnd());
    if (lines.length === 0) return '';

    if (isTableBlock(lines)) {
        return renderTableBlock(lines);
    }

    if (isListBlock(lines, /^\s*[-*+]\s+/)) {
        return renderListBlock(lines, 'ul', /^\s*[-*+]\s+/);
    }

    if (isListBlock(lines, /^\s*\d+\.\s+/)) {
        return renderListBlock(lines, 'ol', /^\s*\d+\.\s+/);
    }

    if (lines.every(line => /^>\s?/.test(line))) {
        const content = lines.map(line => line.replace(/^>\s?/, ''));
        return `<blockquote><p>${content.map(renderInlineMarkdown).join('<br>')}</p></blockquote>`;
    }

    if (lines.length === 1 && /^#{1,4}\s+/.test(lines[0])) {
        const headingLine = lines[0];
        const match = headingLine.match(/^#+/);
        const level = match ? Math.min(match[0].length, 4) : 1;
        const content = headingLine.replace(/^#{1,4}\s+/, '');
        return `<h${level}>${renderInlineMarkdown(content)}</h${level}>`;
    }

    if (lines.length === 1 && /^([-*_])(?:\s*\1){2,}\s*$/.test(lines[0])) {
        return '<hr>';
    }

    return `<p>${lines.map(renderInlineMarkdown).join('<br>')}</p>`;
}

function isListBlock(lines, pattern) {
    return lines.length > 0 && lines.every(line => pattern.test(line));
}

function renderListBlock(lines, tagName, markerPattern) {
    const items = lines
        .map(line => line.replace(markerPattern, ''))
        .map(item => `<li>${renderInlineMarkdown(item)}</li>`)
        .join('');
    return `<${tagName}>${items}</${tagName}>`;
}

function isTableBlock(lines) {
    if (lines.length < 2) return false;
    if (!lines[0].includes('|') || !lines[1].includes('|')) return false;
    return /^\s*\|?[\s:-]+\|[\s|:-]*\|?\s*$/.test(lines[1]);
}

function renderTableBlock(lines) {
    const headerCells = splitTableRow(lines[0]);
    const bodyRows = lines.slice(2).map(splitTableRow);
    const headerHtml = headerCells
        .map(cell => `<th>${renderInlineMarkdown(cell)}</th>`)
        .join('');
    const bodyHtml = bodyRows
        .map(row => `<tr>${row.map(cell => `<td>${renderInlineMarkdown(cell)}</td>`).join('')}</tr>`)
        .join('');
    return `<table><thead><tr>${headerHtml}</tr></thead><tbody>${bodyHtml}</tbody></table>`;
}

function splitTableRow(row) {
    return String(row || '')
        .trim()
        .replace(/^\|/, '')
        .replace(/\|$/, '')
        .split('|')
        .map(cell => cell.trim());
}

function renderCodeBlock(code, lang) {
    const language = normalizeCodeLanguage(lang);
    const codeClass = language ? ` class="language-${escapeAttribute(language)}"` : '';
    return `<pre><code${codeClass}>${highlightCode(code, language)}</code></pre>`;
}

function highlightCode(code, lang) {
    const source = String(code || '');
    const highlightRuntime = getHighlightRuntime();
    if (!highlightRuntime) {
        return escapeHtml(source);
    }

    const requestedLanguage = normalizeCodeLanguage(lang);
    const language = highlightRuntime.getLanguage(requestedLanguage)
        ? requestedLanguage
        : 'plaintext';
    return highlightRuntime.highlight(source, { language }).value;
}

function normalizeCodeLanguage(language) {
    const normalized = String(language || '').trim().toLowerCase();
    return normalized || 'text';
}

function renderInlineMarkdown(source) {
    const codePlaceholders = [];
    let working = String(source || '').replace(/`([^`]+)`/g, (_match, code) => {
        const token = `%%CODE_${codePlaceholders.length}%%`;
        codePlaceholders.push(`<code>${escapeHtml(code)}</code>`);
        return token;
    });

    working = escapeHtml(working);
    working = working.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_match, label, url) => {
        const safeUrl = escapeAttribute(sanitizeUrl(url));
        return `<a href="${safeUrl}" target="_blank" rel="noreferrer">${label}</a>`;
    });
    working = working.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    working = working.replace(/__([^_]+)__/g, '<strong>$1</strong>');
    working = working.replace(/(^|[^\w])\*([^*\n]+)\*(?!\w)/g, '$1<em>$2</em>');
    working = working.replace(/(^|[^\w])_([^_\n]+)_(?!\w)/g, '$1<em>$2</em>');

    codePlaceholders.forEach((html, index) => {
        working = working.replace(`%%CODE_${index}%%`, html);
    });

    return working;
}

function sanitizeUrl(url) {
    const value = String(url || '').trim();
    if (!value) return '#';
    if (
        value.startsWith('/')
        || value.startsWith('#')
        || value.startsWith('http://')
        || value.startsWith('https://')
        || value.startsWith('mailto:')
    ) {
        return value;
    }
    return '#';
}

function escapeHtml(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function escapeAttribute(value) {
    return escapeHtml(value).replaceAll('`', '&#96;');
}

function extractCodeLanguage(codeElement) {
    const classNames = String(codeElement.className || '').split(/\s+/);
    const languageClass = classNames.find(className => className.startsWith('language-'));
    if (!languageClass) return 'text';
    const language = languageClass.slice('language-'.length).trim().toLowerCase();
    return language || 'text';
}

function formatCodeLanguage(language) {
    if (language === 'plaintext' || language === 'text') return 'Text';
    if (language === 'javascript') return 'JavaScript';
    if (language === 'typescript') return 'TypeScript';
    if (language === 'shell' || language === 'bash' || language === 'sh') return 'Shell';
    if (language === 'json') return 'JSON';
    if (language === 'yaml') return 'YAML';
    if (language === 'python') return 'Python';
    return language.charAt(0).toUpperCase() + language.slice(1);
}

function ensureMarkdownInteractions() {
    if (markdownInteractionsBound || typeof document === 'undefined') return;
    document.addEventListener('click', event => {
        const button = event.target instanceof Element
            ? event.target.closest('.markdown-code-copy')
            : null;
        if (!(button instanceof HTMLButtonElement)) return;
        void handleCopyCodeBlock(button);
    });
    markdownInteractionsBound = true;
}

async function handleCopyCodeBlock(button) {
    const codeBlock = button.closest('.markdown-code-block');
    const codeEl = codeBlock?.querySelector('pre code');
    const copyText = codeEl ? String(codeEl.textContent || '').trimEnd() : '';
    if (!copyText) {
        showToast({
            title: 'Copy Failed',
            message: 'No code content was found in this block.',
            tone: 'warning',
        });
        return;
    }

    try {
        await navigator.clipboard.writeText(copyText);
        indicateCopySuccess(button);
        showToast({
            title: 'Code Copied',
            message: 'The code block has been copied to your clipboard.',
            tone: 'success',
            durationMs: 1800,
        });
    } catch (error) {
        showToast({
            title: 'Copy Failed',
            message: 'Clipboard access is not available right now.',
            tone: 'danger',
        });
    }
}

function indicateCopySuccess(button) {
    const defaultLabel = button.dataset.copyLabel || 'Copy';
    const copiedLabel = button.dataset.copiedLabel || 'Copied';
    button.textContent = copiedLabel;
    button.classList.add('is-copied');
    window.setTimeout(() => {
        button.textContent = defaultLabel;
        button.classList.remove('is-copied');
    }, 1400);
}
