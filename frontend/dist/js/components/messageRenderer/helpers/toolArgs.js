/**
 * components/messageRenderer/helpers/toolArgs.js
 * DOM-free tool argument normalization shared by renderers and stream caches.
 */

export function normalizeToolArgs(args) {
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
        return normalizeParsedArgs(JSON.parse(raw), raw);
    } catch (_) {
        const extractedObject = extractJsonValue(raw, '{', '}');
        if (extractedObject) {
            try {
                return normalizeParsedArgs(JSON.parse(extractedObject), raw);
            } catch (_e) {
                // Continue to array extraction and raw fallback.
            }
        }
        const extractedArray = extractJsonValue(raw, '[', ']');
        if (extractedArray) {
            try {
                return normalizeParsedArgs(JSON.parse(extractedArray), raw);
            } catch (_e) {
                // Continue to raw fallback.
            }
        }
        return { __raw: raw };
    }
}

function normalizeParsedArgs(value, rawFallback = '') {
    if (Array.isArray(value)) {
        return { __items: value };
    }
    if (value && typeof value === 'object') {
        return value;
    }
    const raw = String(value ?? rawFallback ?? '').trim();
    return raw ? { __raw: raw } : {};
}

function extractJsonValue(raw, openToken, closeToken) {
    const start = raw.indexOf(openToken);
    const end = raw.lastIndexOf(closeToken);
    if (start < 0 || end <= start) {
        return '';
    }
    return raw.slice(start, end + 1);
}
