/**
 * core/api/request.js
 * Shared HTTP request helper for JSON endpoints.
 */
import { errorToPayload, logError } from '../../utils/logger.js';

const BACKEND_STATUS_HINT_EVENT = 'agent-teams-backend-status-hint';
const COALESCED_CACHE = new Map();
const COALESCED_IN_FLIGHT = new Map();
const COALESCED_GENERATIONS = new Map();
const REQUEST_LIMITS = {
    normal: 6,
    heavy: 2,
};
const REQUEST_ACTIVE_COUNTS = {
    normal: 0,
    heavy: 0,
};
const REQUEST_QUEUES = {
    normal: [],
    heavy: [],
};

export async function requestJson(url, options, errorMessage) {
    const method = String(options?.method || 'GET').toUpperCase();
    const requestOptions = (method === 'GET' || method === 'HEAD') && options?.cache == null
        ? { ...options, cache: 'no-store' }
        : options;
    try {
        const res = await fetch(url, requestOptions);
        emitBackendStatusHint('online');
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
        if (error?.name === 'AbortError') {
            throw error;
        }
        emitBackendStatusHint('offline');
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

function emitBackendStatusHint(status) {
    const safeStatus = String(status || '').trim();
    if (!safeStatus) {
        return;
    }
    const target = typeof window !== 'undefined' && typeof window.dispatchEvent === 'function'
        ? window
        : typeof globalThis.dispatchEvent === 'function'
            ? globalThis
            : null;
    if (!target) {
        return;
    }
    try {
        if (typeof CustomEvent === 'function') {
            target.dispatchEvent(new CustomEvent(
                BACKEND_STATUS_HINT_EVENT,
                { detail: { status: safeStatus } },
            ));
            return;
        }
        if (typeof Event === 'function') {
            const event = new Event(BACKEND_STATUS_HINT_EVENT);
            event.detail = { status: safeStatus };
            target.dispatchEvent(event);
        }
    } catch (_) {
        // Status hints are best-effort and must not affect API behavior.
    }
}

export function requestJsonManaged(
    key,
    url,
    options,
    errorMessage,
    { ttlMs = 600, lane = 'normal' } = {},
) {
    const requestKey = String(key || url || '').trim();
    const safeLane = lane === 'heavy' ? 'heavy' : 'normal';
    const signal = options?.signal || null;
    const method = String(options?.method || 'GET').toUpperCase();
    if (!requestKey || method !== 'GET') {
        return requestJson(url, options, errorMessage);
    }

    const now = Date.now();
    pruneExpiredManagedCache(now);
    const cached = COALESCED_CACHE.get(requestKey);
    if (cached && cached.expiresAt > now) {
        return raceAbort(Promise.resolve(cached.value), signal);
    }
    if (cached) {
        deleteManagedCacheEntry(requestKey);
    }

    const inFlight = COALESCED_IN_FLIGHT.get(requestKey);
    if (inFlight) {
        return raceManagedAbort(inFlight, signal);
    }

    const sharedOptions = options && 'signal' in options
        ? { ...options, signal: undefined }
        : options;
    const generation = getManagedRequestGeneration(requestKey);
    const controller = typeof AbortController === 'function'
        ? new AbortController()
        : null;
    const effectiveOptions = controller
        ? { ...(sharedOptions || {}), signal: controller.signal }
        : sharedOptions;
    const entry = {
        requestKey,
        promise: null,
        controller,
        consumers: 0,
        queueItem: null,
        settled: false,
    };
    let promise;
    promise = runLimitedRequest(safeLane, () => {
        if (controller?.signal?.aborted) {
            throw buildAbortError();
        }
        return requestJson(url, effectiveOptions, errorMessage);
    }, entry)
        .then(value => {
            if (
                COALESCED_IN_FLIGHT.get(requestKey) === entry
                && getManagedRequestGeneration(requestKey) === generation
            ) {
                COALESCED_CACHE.set(requestKey, {
                    expiresAt: Date.now() + Math.max(0, ttlMs),
                    value,
                });
            }
            return value;
        })
        .finally(() => {
            entry.settled = true;
            if (COALESCED_IN_FLIGHT.get(requestKey) === entry) {
                COALESCED_IN_FLIGHT.delete(requestKey);
            }
            pruneManagedRequestGeneration(requestKey);
        });
    entry.promise = promise;
    COALESCED_IN_FLIGHT.set(requestKey, entry);
    return raceManagedAbort(entry, signal);
}

export function invalidateManagedRequests(prefix = '') {
    const safePrefix = String(prefix || '').trim();
    const shouldDelete = key => !safePrefix || String(key).startsWith(safePrefix);
    for (const key of COALESCED_CACHE.keys()) {
        if (shouldDelete(key)) {
            deleteManagedCacheEntry(key);
        }
    }
    for (const key of COALESCED_IN_FLIGHT.keys()) {
        if (shouldDelete(key)) {
            const entry = COALESCED_IN_FLIGHT.get(key);
            entry?.controller?.abort();
            evictManagedInFlightEntry(entry);
        }
    }
}

function pruneExpiredManagedCache(now = Date.now()) {
    for (const [key, cached] of COALESCED_CACHE.entries()) {
        if (!cached || cached.expiresAt <= now) {
            deleteManagedCacheEntry(key);
        }
    }
}

function getManagedRequestGeneration(key) {
    return COALESCED_GENERATIONS.get(key) || 0;
}

function bumpManagedRequestGeneration(key) {
    COALESCED_GENERATIONS.set(key, getManagedRequestGeneration(key) + 1);
}

function deleteManagedCacheEntry(key) {
    COALESCED_CACHE.delete(key);
    pruneManagedRequestGeneration(key);
}

function pruneManagedRequestGeneration(key) {
    if (!key || COALESCED_CACHE.has(key) || COALESCED_IN_FLIGHT.has(key)) {
        return;
    }
    COALESCED_GENERATIONS.delete(key);
}

function runLimitedRequest(lane, operation, entry = null) {
    if (REQUEST_ACTIVE_COUNTS[lane] < REQUEST_LIMITS[lane]) {
        return startLimitedRequest(lane, operation);
    }
    return new Promise((resolve, reject) => {
        const queueItem = {
            canceled: false,
            entry,
            lane,
            reject,
            run() {
                if (queueItem.canceled) {
                    return;
                }
                if (entry?.queueItem === queueItem) {
                    entry.queueItem = null;
                }
                startLimitedRequest(lane, operation).then(resolve, reject);
            },
        };
        if (entry) {
            entry.queueItem = queueItem;
        }
        REQUEST_QUEUES[lane].push(queueItem);
    });
}

function startLimitedRequest(lane, operation) {
    REQUEST_ACTIVE_COUNTS[lane] += 1;
    return Promise.resolve()
        .then(operation)
        .finally(() => {
            REQUEST_ACTIVE_COUNTS[lane] = Math.max(0, REQUEST_ACTIVE_COUNTS[lane] - 1);
            runNextLimitedRequest(lane);
        });
}

function runNextLimitedRequest(lane) {
    while (REQUEST_ACTIVE_COUNTS[lane] < REQUEST_LIMITS[lane]) {
        const next = REQUEST_QUEUES[lane].shift();
        if (!next) {
            return;
        }
        if (isQueuedLimitedRequestCanceled(next)) {
            next.canceled = true;
            if (next.entry?.queueItem === next) {
                next.entry.queueItem = null;
            }
            next.reject(buildAbortError());
            continue;
        }
        next.run();
        return;
    }
}

function isQueuedLimitedRequestCanceled(queueItem) {
    return (
        queueItem?.canceled === true
        || queueItem?.entry?.controller?.signal?.aborted === true
    );
}

function raceAbort(promise, signal) {
    if (!signal) {
        return promise;
    }
    if (signal.aborted) {
        return Promise.reject(buildAbortError());
    }
    return new Promise((resolve, reject) => {
        const abort = () => {
            signal.removeEventListener('abort', abort);
            reject(buildAbortError());
        };
        signal.addEventListener('abort', abort, { once: true });
        promise.then(
            value => {
                signal.removeEventListener('abort', abort);
                resolve(value);
            },
            error => {
                signal.removeEventListener('abort', abort);
                reject(error);
            },
        );
    });
}

function raceManagedAbort(entry, signal) {
    const promise = entry?.promise;
    if (!promise) {
        return Promise.reject(buildAbortError());
    }
    entry.consumers += 1;
    let released = false;
    const releaseConsumer = () => {
        if (released) {
            return;
        }
        released = true;
        entry.consumers = Math.max(0, entry.consumers - 1);
        if (!entry.settled && entry.consumers === 0) {
            entry.controller?.abort();
            evictManagedInFlightEntry(entry);
        }
    };

    if (!signal) {
        return promise.finally(releaseConsumer);
    }
    if (signal.aborted) {
        releaseConsumer();
        return Promise.reject(buildAbortError());
    }
    return new Promise((resolve, reject) => {
        const abort = () => {
            signal.removeEventListener('abort', abort);
            releaseConsumer();
            reject(buildAbortError());
        };
        signal.addEventListener('abort', abort, { once: true });
        promise.then(
            value => {
                signal.removeEventListener('abort', abort);
                releaseConsumer();
                resolve(value);
            },
            error => {
                signal.removeEventListener('abort', abort);
                releaseConsumer();
                reject(error);
            },
        );
    });
}

function evictManagedInFlightEntry(entry) {
    const requestKey = String(entry?.requestKey || '').trim();
    if (!requestKey || COALESCED_IN_FLIGHT.get(requestKey) !== entry) {
        return;
    }
    cancelQueuedLimitedRequest(entry);
    COALESCED_IN_FLIGHT.delete(requestKey);
    bumpManagedRequestGeneration(requestKey);
    pruneManagedRequestGeneration(requestKey);
}

function cancelQueuedLimitedRequest(entry) {
    const queueItem = entry?.queueItem || null;
    if (!queueItem || queueItem.canceled === true) {
        return;
    }
    const queue = REQUEST_QUEUES[queueItem.lane] || [];
    const index = queue.indexOf(queueItem);
    if (index >= 0) {
        queue.splice(index, 1);
    }
    queueItem.canceled = true;
    if (entry.queueItem === queueItem) {
        entry.queueItem = null;
    }
    queueItem.reject(buildAbortError());
}

function buildAbortError() {
    if (typeof DOMException === 'function') {
        return new DOMException('The operation was aborted.', 'AbortError');
    }
    const error = new Error('The operation was aborted.');
    error.name = 'AbortError';
    return error;
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
