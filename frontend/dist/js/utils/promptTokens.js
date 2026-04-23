/**
 * utils/promptTokens.js
 * Lightweight prompt token rendering for composer previews and run intents.
 */

export function extractPromptTokens(source, options = {}) {
    const text = String(source || '');
    const segments = [];
    let cursor = 0;
    while (cursor < text.length) {
        const tokenStart = findNextPromptTokenStart(text, cursor);
        if (tokenStart < 0) {
            pushTextSegment(segments, text.slice(cursor));
            break;
        }
        if (tokenStart > cursor) {
            pushTextSegment(segments, text.slice(cursor, tokenStart));
        }
        const token = readPromptToken(text, tokenStart, options);
        if (!token) {
            pushTextSegment(segments, text.charAt(tokenStart));
            cursor = tokenStart + 1;
            continue;
        }
        segments.push({
            kind: 'token',
            token,
        });
        cursor = token.end;
    }
    return segments;
}

export function renderPromptTokenChipsHtml(source, options = {}) {
    return extractPromptTokens(source, options)
        .filter(segment => segment.kind === 'token')
        .map(segment => renderPromptTokenChipHtml(segment.token))
        .join('');
}

export function renderPromptTokenizedText(targetEl, source, options = {}) {
    if (!targetEl) {
        return targetEl;
    }
    targetEl.replaceChildren();
    extractPromptTokens(source, options).forEach(segment => {
        if (segment.kind === 'text') {
            targetEl.appendChild(document.createTextNode(segment.text));
            return;
        }
        targetEl.appendChild(buildPromptTokenChip(segment.token));
    });
    return targetEl;
}

function findNextPromptTokenStart(text, start) {
    for (let index = start; index < text.length; index += 1) {
        const char = text.charAt(index);
        if ((char === '@' || char === '＠' || char === '/') && isTokenBoundary(text, index)) {
            return index;
        }
    }
    return -1;
}

function isTokenBoundary(text, index) {
    if (index <= 0) {
        return true;
    }
    return /\s/.test(text.charAt(index - 1));
}

function readPromptToken(text, start, options) {
    const trigger = text.charAt(start);
    if (trigger === '/') {
        return readSlashPromptToken(text, start, options);
    }
    return readMentionPromptToken(text, start, options);
}

function readSlashPromptToken(text, start, options) {
    let end = start + 1;
    while (end < text.length && !/\s/.test(text.charAt(end))) {
        end += 1;
    }
    const raw = text.slice(start, end);
    const body = raw.slice(1).trim();
    if (!body) {
        return null;
    }
    const skillNames = normalizeLookupSet(options.skills);
    const commandNames = normalizeLookupSet(options.commands);
    const normalizedBody = body.toLowerCase();
    const tokenType = skillNames.has(normalizedBody) ||
        (!commandNames.has(normalizedBody) && normalizedBody.includes('skill'))
        ? 'skill'
        : 'command';
    return {
        raw,
        end,
        type: tokenType,
        label: tokenType === 'skill' ? titleizePromptToken(body) : `/${body}`,
    };
}

function readMentionPromptToken(text, start, options) {
    let end = start + 1;
    while (end < text.length && !/\s/.test(text.charAt(end))) {
        end += 1;
    }
    if (end < text.length && shouldIncludeAgentSecondWord(text.slice(start + 1, end), text.slice(end + 1))) {
        end += 1;
        while (end < text.length && !/\s/.test(text.charAt(end))) {
            end += 1;
        }
    }
    const raw = text.slice(start, end);
    const body = raw.slice(1).trim();
    if (!body) {
        return null;
    }
    const normalizedBody = body.replaceAll('\\', '/');
    const type = normalizedBody.includes('/') || normalizedBody.includes('.')
        ? 'file'
        : 'agent';
    return {
        raw,
        end,
        type,
        label: type === 'file' ? basenamePromptPath(normalizedBody) : `@${body}`,
    };
}

function shouldIncludeAgentSecondWord(firstWord, remaining) {
    const first = String(firstWord || '').trim();
    if (!first || first.includes('/') || first.includes('.')) {
        return false;
    }
    const nextWord = String(remaining || '').match(/^[^\s]+/)?.[0] || '';
    return /^[A-Z][A-Za-z0-9_-]*$/.test(first) && /^[A-Z][A-Za-z0-9_-]*$/.test(nextWord);
}

function normalizeLookupSet(items) {
    return new Set(
        (Array.isArray(items) ? items : [])
            .map(item => String(item || '').trim().replace(/^[/@＠]/, '').toLowerCase())
            .filter(Boolean),
    );
}

function titleizePromptToken(value) {
    return String(value || '')
        .replace(/^[/@＠]/, '')
        .split(/[-_:/.]+/)
        .filter(Boolean)
        .map(part => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ');
}

function basenamePromptPath(path) {
    const normalized = String(path || '').replaceAll('\\', '/').replace(/\/+$/, '');
    const parts = normalized.split('/').filter(Boolean);
    return parts.at(-1) || normalized || path;
}

function buildPromptTokenChip(token) {
    const chip = document.createElement('span');
    chip.className = `prompt-token-chip prompt-token-${token.type}`;
    chip.textContent = token.label;
    chip.title = token.raw;
    return chip;
}

function renderPromptTokenChipHtml(token) {
    return `<span class="prompt-token-chip prompt-token-${escapeHtml(token.type)}" title="${escapeHtml(token.raw)}">${escapeHtml(token.label)}</span>`;
}

function pushTextSegment(segments, text) {
    if (text) {
        segments.push({ kind: 'text', text });
    }
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}
