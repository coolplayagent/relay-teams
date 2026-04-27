/**
 * components/sessionSearch.js
 * Lightweight command-palette style search for sessions.
 */
import { t } from '../utils/i18n.js';

const MAX_RESULTS = 20;
const CLOSE_ANIMATION_MS = 160;

let entries = [];
let activeIndex = 0;
let rootEl = null;
let inputEl = null;
let resultsEl = null;
let selectHandler = null;
let shortcutBound = false;
let selecting = false;

export function configureSessionSearch({ onSelect } = {}) {
    if (typeof onSelect === 'function') {
        selectHandler = onSelect;
    }
    bindGlobalShortcut();
}

export function setSessionSearchEntries(nextEntries = []) {
    entries = (Array.isArray(nextEntries) ? nextEntries : [])
        .map(normalizeEntry)
        .filter(Boolean)
        .sort(sortEntriesByRecent);
    if (rootEl) {
        renderResults({ animate: false });
    }
}

export function openSessionSearch(initialQuery = '') {
    if (!ensureSearchRoot()) {
        return;
    }
    selecting = false;
    activeIndex = 0;
    inputEl.value = String(initialQuery || '');
    renderResults({ animate: true });
    rootEl.setAttribute('aria-hidden', 'false');
    requestFrame(() => {
        rootEl?.classList?.add('is-active');
        inputEl?.focus?.();
        inputEl?.select?.();
    });
}

export function closeSessionSearch() {
    if (!rootEl) {
        return;
    }
    setSearchSelecting(false);
    const closingRoot = rootEl;
    rootEl.classList?.remove('is-active');
    rootEl.setAttribute('aria-hidden', 'true');
    rootEl = null;
    inputEl = null;
    resultsEl = null;
    globalThis.setTimeout(() => {
        closingRoot.remove?.();
    }, CLOSE_ANIMATION_MS);
}

export function buildSessionSearchResults(sourceEntries, query) {
    const safeEntries = (Array.isArray(sourceEntries) ? sourceEntries : [])
        .map(normalizeEntry)
        .filter(Boolean);
    const normalizedQuery = normalizeSearchText(query);
    const ranked = [];
    for (const entry of safeEntries) {
        const score = matchScore(entry, normalizedQuery);
        if (score < 0) {
            continue;
        }
        ranked.push({ entry, score });
    }
    ranked.sort((left, right) => (
        left.score - right.score
        || right.entry.updatedAtMs - left.entry.updatedAtMs
        || left.entry.title.localeCompare(right.entry.title)
    ));
    return ranked.slice(0, MAX_RESULTS).map(({ entry }, index) => ({
        ...entry,
        index,
        titleHtml: highlightSessionSearchText(entry.title, normalizedQuery),
        projectHtml: highlightSessionSearchText(entry.projectLabel, normalizedQuery),
        shortcut: shortcutLabel(index),
    }));
}

export function highlightSessionSearchText(value, query) {
    const text = String(value || '');
    const tokens = tokenizeQuery(query);
    if (tokens.length === 0) {
        return escapeHtml(text);
    }
    const lowerText = text.toLocaleLowerCase();
    const ranges = [];
    for (const token of tokens) {
        const lowerToken = token.toLocaleLowerCase();
        let start = 0;
        while (start < lowerText.length) {
            const index = lowerText.indexOf(lowerToken, start);
            if (index < 0) {
                break;
            }
            ranges.push([index, index + lowerToken.length]);
            start = index + Math.max(lowerToken.length, 1);
        }
    }
    const mergedRanges = mergeRanges(ranges);
    if (mergedRanges.length === 0) {
        return escapeHtml(text);
    }
    let cursor = 0;
    let output = '';
    for (const [start, end] of mergedRanges) {
        output += escapeHtml(text.slice(cursor, start));
        output += `<mark class="session-search-mark">${escapeHtml(text.slice(start, end))}</mark>`;
        cursor = end;
    }
    output += escapeHtml(text.slice(cursor));
    return output;
}

function ensureSearchRoot() {
    if (rootEl) {
        return true;
    }
    if (typeof document === 'undefined' || !document.body?.appendChild) {
        return false;
    }
    const root = document.createElement('div');
    root.className = 'session-search-root';
    root.setAttribute('aria-hidden', 'true');
    root.innerHTML = `
        <div class="session-search-backdrop" data-session-search-close="true"></div>
        <section class="session-search-panel" role="dialog" aria-modal="true" aria-label="${escapeHtml(t('sidebar.search_conversations_title'))}">
            <div class="session-search-input-wrap">
                <input class="session-search-input" type="search" autocomplete="off" spellcheck="false" placeholder="${escapeHtml(t('sidebar.search_placeholder'))}" aria-label="${escapeHtml(t('sidebar.search_conversations_title'))}">
            </div>
            <div class="session-search-results" role="listbox"></div>
        </section>
    `;
    document.body.appendChild(root);
    rootEl = root;
    inputEl = root.querySelector('.session-search-input');
    resultsEl = root.querySelector('.session-search-results');
    bindSearchRoot(root);
    return !!inputEl && !!resultsEl;
}

function bindSearchRoot(root) {
    root.addEventListener('mousedown', event => {
        if (selecting) {
            return;
        }
        const closeTarget = event?.target?.getAttribute?.('data-session-search-close');
        if (closeTarget === 'true') {
            closeSessionSearch();
        }
    });
    root.addEventListener('keydown', handleSearchKeydown);
    inputEl?.addEventListener('input', () => {
        if (selecting) {
            return;
        }
        activeIndex = 0;
        renderResults({ animate: false });
    });
    resultsEl?.addEventListener('click', event => {
        if (selecting) {
            event.preventDefault?.();
            return;
        }
        const button = event?.target?.closest?.('.session-search-result');
        const index = Number(button?.getAttribute?.('data-index') || -1);
        if (Number.isInteger(index) && index >= 0) {
            event.preventDefault?.();
            void selectResult(index);
        }
    });
}

function renderResults({ animate = false } = {}) {
    if (!resultsEl || !inputEl) {
        return;
    }
    resultsEl.classList?.toggle?.('is-animated', animate === true);
    const query = String(inputEl.value || '');
    const results = buildSessionSearchResults(entries, query);
    if (results.length === 0) {
        activeIndex = 0;
        resultsEl.innerHTML = `<div class="session-search-empty">${escapeHtml(t('sidebar.search_no_matches'))}</div>`;
        return;
    }
    if (activeIndex >= results.length) {
        activeIndex = results.length - 1;
    }
    const label = normalizeSearchText(query)
        ? t('sidebar.search_results')
        : t('sidebar.search_recent');
    resultsEl.innerHTML = `
        <div class="session-search-section-label">${escapeHtml(label)}</div>
        <div class="session-search-list">
            ${results.map(result => renderResult(result)).join('')}
        </div>
    `;
}

function renderResult(result) {
    const active = result.index === activeIndex;
    const shortcutClass = result.shortcut
        ? 'session-search-shortcut'
        : 'session-search-shortcut is-empty';
    const shortcutHtml = result.shortcut
        ? `<span class="${shortcutClass}">${escapeHtml(result.shortcut)}</span>`
        : `<span class="${shortcutClass}" aria-hidden="true"></span>`;
    return `
        <button class="session-search-result${active ? ' is-active' : ''}" type="button" role="option" aria-selected="${active ? 'true' : 'false'}" data-index="${result.index}" data-session-id="${escapeHtml(result.sessionId)}" title="${escapeHtml(result.title)}" style="--session-search-result-index: ${result.index};">
            <span class="session-search-result-marker" aria-hidden="true">-</span>
            <span class="session-search-result-main">
                <span class="session-search-result-title" title="${escapeHtml(result.title)}">${result.titleHtml}</span>
            </span>
            <span class="session-search-result-project" title="${escapeHtml(result.projectLabel)}">${result.projectHtml}</span>
            ${shortcutHtml}
        </button>
    `;
}

function handleSearchKeydown(event) {
    if (!rootEl) {
        return;
    }
    if (selecting) {
        event.preventDefault?.();
        return;
    }
    const results = buildSessionSearchResults(entries, inputEl?.value || '');
    if (event.key === 'Escape') {
        event.preventDefault?.();
        closeSessionSearch();
        return;
    }
    if (event.key === 'ArrowDown') {
        event.preventDefault?.();
        if (results.length === 0) {
            activeIndex = 0;
            return;
        }
        activeIndex = Math.min(results.length - 1, activeIndex + 1);
        updateActiveResultVisuals();
        return;
    }
    if (event.key === 'ArrowUp') {
        event.preventDefault?.();
        if (results.length === 0) {
            activeIndex = 0;
            return;
        }
        activeIndex = Math.max(0, activeIndex - 1);
        updateActiveResultVisuals();
        return;
    }
    if (event.key === 'Enter') {
        event.preventDefault?.();
        void selectResult(activeIndex);
        return;
    }
    const shortcutIndex = shortcutIndexFromEvent(event);
    if (shortcutIndex >= 0) {
        event.preventDefault?.();
        void selectResult(shortcutIndex);
    }
}

function updateActiveResultVisuals() {
    const buttons = Array.from(resultsEl?.querySelectorAll?.('.session-search-result') || []);
    buttons.forEach((button, index) => {
        const active = index === activeIndex;
        button.classList?.toggle?.('is-active', active);
        button.setAttribute?.('aria-selected', active ? 'true' : 'false');
        if (active) {
            button.scrollIntoView?.({ block: 'nearest' });
        }
    });
}

async function selectResult(index) {
    if (selecting) {
        return;
    }
    const results = buildSessionSearchResults(entries, inputEl?.value || '');
    const result = results[index] || null;
    if (!result || typeof selectHandler !== 'function') {
        return;
    }
    activeIndex = result.index;
    updateActiveResultVisuals();
    setSearchSelecting(true, activeIndex);
    try {
        await selectHandler(result);
        closeSessionSearch();
    } catch {
        setSearchSelecting(false, activeIndex);
    }
}

function setSearchSelecting(nextSelecting, selectedIndex = activeIndex) {
    selecting = nextSelecting === true;
    rootEl?.classList?.toggle?.('is-selecting', selecting);
    resultsEl?.classList?.toggle?.('is-selecting', selecting);
    if (inputEl) {
        inputEl.disabled = selecting;
        inputEl.setAttribute('aria-disabled', selecting ? 'true' : 'false');
    }
    const buttons = Array.from(resultsEl?.querySelectorAll?.('.session-search-result') || []);
    buttons.forEach((button, index) => {
        const selected = selecting && index === selectedIndex;
        button.classList?.toggle?.('is-selecting', selected);
        button.disabled = selecting;
        if (selected) {
            button.setAttribute?.('aria-busy', 'true');
        } else {
            button.removeAttribute?.('aria-busy');
        }
    });
}

function bindGlobalShortcut() {
    if (shortcutBound || typeof document === 'undefined' || typeof document.addEventListener !== 'function') {
        return;
    }
    document.addEventListener('keydown', event => {
        if (!event || String(event.key || '').toLowerCase() !== 'k') {
            return;
        }
        if (!event.ctrlKey && !event.metaKey) {
            return;
        }
        event.preventDefault?.();
        openSessionSearch();
    });
    shortcutBound = true;
}

function shortcutIndexFromEvent(event) {
    if (!event?.ctrlKey && !event?.metaKey) {
        return -1;
    }
    const key = String(event.key || '').trim();
    if (!/^[1-9]$/.test(key)) {
        return -1;
    }
    return Number(key) - 1;
}

function matchScore(entry, normalizedQuery) {
    if (!normalizedQuery) {
        return 0;
    }
    if (normalizeSearchText(entry.title).includes(normalizedQuery)) {
        return 0;
    }
    if (normalizeSearchText(entry.projectLabel).includes(normalizedQuery)) {
        return 1;
    }
    if (normalizeSearchText(entry.sessionId).includes(normalizedQuery)) {
        return 2;
    }
    return -1;
}

function normalizeEntry(entry) {
    const sessionId = String(entry?.sessionId || '').trim();
    if (!sessionId) {
        return null;
    }
    const title = String(entry?.title || '').trim() || t('sidebar.untitled_session');
    const projectLabel = String(entry?.projectLabel || '').trim() || t('sidebar.project');
    const updatedAtMs = Number(entry?.updatedAtMs || 0);
    return {
        sessionId,
        title,
        projectLabel,
        groupKey: String(entry?.groupKey || '').trim(),
        updatedAtMs: Number.isFinite(updatedAtMs) ? updatedAtMs : 0,
    };
}

function sortEntriesByRecent(left, right) {
    return (
        right.updatedAtMs - left.updatedAtMs
        || left.title.localeCompare(right.title)
        || left.sessionId.localeCompare(right.sessionId)
    );
}

function normalizeSearchText(value) {
    return String(value || '').trim().replace(/\s+/g, ' ').toLocaleLowerCase();
}

function tokenizeQuery(query) {
    const normalized = normalizeSearchText(query);
    if (!normalized) {
        return [];
    }
    return normalized.split(' ').filter(Boolean);
}

function mergeRanges(ranges) {
    const sorted = ranges
        .filter(range => range[0] < range[1])
        .sort((left, right) => left[0] - right[0] || left[1] - right[1]);
    const merged = [];
    for (const range of sorted) {
        const previous = merged[merged.length - 1];
        if (!previous || range[0] > previous[1]) {
            merged.push([...range]);
            continue;
        }
        previous[1] = Math.max(previous[1], range[1]);
    }
    return merged;
}

function shortcutLabel(index) {
    if (index < 0 || index > 8) {
        return '';
    }
    const platform = String(globalThis.navigator?.platform || '').toLowerCase();
    const prefix = platform.includes('mac') ? 'Cmd' : 'Ctrl';
    return `${prefix}+${index + 1}`;
}

function requestFrame(callback) {
    if (typeof globalThis.requestAnimationFrame === 'function') {
        globalThis.requestAnimationFrame(callback);
        return;
    }
    globalThis.setTimeout(callback, 0);
}

function escapeHtml(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
