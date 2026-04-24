/**
 * core/api/request.js
 * Shared HTTP request helper for JSON endpoints.
 */
import { markBackendOffline, markBackendOnline } from '../../utils/backendStatus.js';
import { errorToPayload, logError } from '../../utils/logger.js';

export async function requestJson(url, options, errorMessage) {
    const method = String(options?.method || 'GET').toUpperCase();
    const requestOptions = (method === 'GET' || method === 'HEAD') && options?.cache == null
        ? { ...options, cache: 'no-store' }
        : options;
    try {
        const res = await fetch(url, requestOptions);
        markBackendOnline();
        if (!res.ok) {
            let detail = errorMessage;
            try {
                const payload = await res.json();
                detail = extractApiErrorDetail(payload, errorMessage);
            } catch (_) {
                // keep fallback message
            }
            logError(
                'frontend.api.failed',
                detail,
                {
                    url,
                    method,
                    status: res.status,
                },
            );
            const error = new Error(detail);
            error.__agentTeamsLogged = true;
            error.status = res.status;
            error.detail = detail;
            error.url = url;
            error.method = method;
            throw error;
        }
        return res.json();
    } catch (error) {
        if (error?.__agentTeamsLogged === true) {
            throw error;
        }
        markBackendOffline();
        logError(
            'frontend.api.exception',
            errorMessage,
            errorToPayload(error, {
                url,
                method,
            }),
        );
        throw error;
    }
}

function extractApiErrorDetail(payload, fallbackMessage) {
    const directDetail = formatApiErrorValue(payload?.detail);
    if (directDetail) {
        return directDetail;
    }
    const message = formatApiErrorValue(payload?.message);
    if (message) {
        return message;
    }
    const error = formatApiErrorValue(payload?.error);
    if (error) {
        return error;
    }
    return fallbackMessage;
}

function formatApiErrorValue(value) {
    if (typeof value === 'string') {
        return value.trim();
    }
    if (Array.isArray(value)) {
        const parts = value.map(formatApiErrorEntry).filter(Boolean);
        return parts.join('; ');
    }
    if (value && typeof value === 'object') {
        const nestedDetail = formatApiErrorValue(value.detail);
        if (nestedDetail) {
            return nestedDetail;
        }
        const nestedMessage = formatApiErrorValue(value.message);
        if (nestedMessage) {
            return nestedMessage;
        }
        const nestedError = formatApiErrorValue(value.error);
        if (nestedError) {
            return nestedError;
        }
    }
    return '';
}

function formatApiErrorEntry(entry) {
    if (typeof entry === 'string') {
        return entry.trim();
    }
    if (!entry || typeof entry !== 'object') {
        return '';
    }
    const location = Array.isArray(entry.loc)
        ? entry.loc.map(part => String(part ?? '').trim()).filter(Boolean).join('.')
        : '';
    const message = typeof entry.msg === 'string'
        ? entry.msg.trim()
        : (typeof entry.message === 'string' ? entry.message.trim() : '');
    if (location && message) {
        return `${location}: ${message}`;
    }
    if (message) {
        return message;
    }
    return '';
}
