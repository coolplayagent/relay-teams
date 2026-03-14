/**
 * components/sessionTokenUsage.js
 * Session-level token usage summary shown below the main composer.
 */
import { fetchSessionTokenUsage } from '../core/api.js';
import { state } from '../core/state.js';
import { els } from '../utils/dom.js';

const REFRESH_DEBOUNCE_MS = 160;
const EMPTY_TEXT = 'Tokens --';
const numberFormatter = new Intl.NumberFormat('en-US');

const refreshState = {
    timerId: 0,
    requestId: 0,
    controller: null,
};

export function initializeSessionTokenUsage() {
    renderIdle();
}

export function clearSessionTokenUsage() {
    if (refreshState.timerId) {
        clearTimeout(refreshState.timerId);
        refreshState.timerId = 0;
    }
    if (refreshState.controller) {
        refreshState.controller.abort();
        refreshState.controller = null;
    }
    renderIdle();
}

export function scheduleSessionTokenUsageRefresh({ immediate = false } = {}) {
    if (refreshState.timerId) {
        clearTimeout(refreshState.timerId);
        refreshState.timerId = 0;
    }
    if (immediate) {
        void refreshSessionTokenUsage();
        return;
    }
    refreshState.timerId = setTimeout(() => {
        refreshState.timerId = 0;
        void refreshSessionTokenUsage();
    }, REFRESH_DEBOUNCE_MS);
}

async function refreshSessionTokenUsage() {
    const sessionId = String(state.currentSessionId || '').trim();
    if (!sessionId) {
        renderIdle();
        return;
    }

    const nextRequestId = refreshState.requestId + 1;
    refreshState.requestId = nextRequestId;
    if (refreshState.controller) {
        refreshState.controller.abort();
    }
    const controller = new AbortController();
    refreshState.controller = controller;
    renderLoading();

    try {
        const usage = await fetchSessionTokenUsage(sessionId, {
            signal: controller.signal,
        });
        if (refreshState.requestId !== nextRequestId) return;
        if (!usage || !hasUsage(usage)) {
            renderEmpty(sessionId);
            return;
        }
        renderUsage(usage);
    } catch (error) {
        if (error?.name === 'AbortError') return;
        if (refreshState.requestId !== nextRequestId) return;
        renderError();
    } finally {
        if (refreshState.requestId === nextRequestId) {
            refreshState.controller = null;
        }
    }
}

function renderUsage(usage) {
    const target = els.sessionTokenUsage;
    if (!target) return;
    target.dataset.state = 'ready';
    target.innerHTML = `
        <span class="session-token-usage-label">Tokens</span>
        <span class="session-token-usage-value">${formatCompact(usage.total_input_tokens)} / ${formatCompact(usage.total_output_tokens)}</span>
    `;
    target.title = buildDetailTitle(usage);
}

function renderIdle() {
    const target = els.sessionTokenUsage;
    if (!target) return;
    target.dataset.state = 'idle';
    target.textContent = EMPTY_TEXT;
    target.title = 'Session token usage';
}

function renderLoading() {
    const target = els.sessionTokenUsage;
    if (!target) return;
    target.dataset.state = 'loading';
    if (!target.textContent) {
        target.textContent = EMPTY_TEXT;
    }
    target.title = 'Loading session token usage';
}

function renderEmpty(sessionId) {
    const target = els.sessionTokenUsage;
    if (!target) return;
    target.dataset.state = 'idle';
    target.textContent = EMPTY_TEXT;
    target.title = `Session token usage: no recorded usage yet for ${sessionId}`;
}

function renderError() {
    const target = els.sessionTokenUsage;
    if (!target) return;
    target.dataset.state = 'error';
    target.textContent = EMPTY_TEXT;
    target.title = 'Session token usage unavailable';
}

function buildDetailTitle(usage) {
    const total = formatInteger(usage.total_tokens);
    const input = formatInteger(usage.total_input_tokens);
    const cached = formatInteger(usage.total_cached_input_tokens);
    const output = formatInteger(usage.total_output_tokens);
    const reasoning = formatInteger(usage.total_reasoning_output_tokens);
    let detail = `Token usage: total=${total} input=${input}`;
    if (Number(usage.total_cached_input_tokens) > 0) {
        detail += ` (+ ${cached} cached)`;
    }
    detail += ` output=${output}`;
    if (Number(usage.total_reasoning_output_tokens) > 0) {
        detail += ` (reasoning ${reasoning})`;
    }
    return detail;
}

function hasUsage(usage) {
    return Number(usage?.total_tokens || 0) > 0
        || Number(usage?.total_cached_input_tokens || 0) > 0
        || Number(usage?.total_reasoning_output_tokens || 0) > 0;
}

function formatInteger(value) {
    return numberFormatter.format(safeNumber(value));
}

function formatCompact(value) {
    const safeValue = safeNumber(value);
    if (safeValue >= 1000000000) {
        return `${trimFraction(safeValue / 1000000000)}B`;
    }
    if (safeValue >= 1000000) {
        return `${trimFraction(safeValue / 1000000)}M`;
    }
    if (safeValue >= 1000) {
        return `${trimFraction(safeValue / 1000)}k`;
    }
    return String(Math.round(safeValue));
}

function trimFraction(value) {
    const rounded = value >= 100 ? value.toFixed(0) : value.toFixed(1);
    return rounded.replace(/\.0$/, '');
}

function safeNumber(value) {
    const parsed = Number(value || 0);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
}
