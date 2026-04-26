/**
 * components/rounds/navigator.js
 * Docked round timeline rendering and active-state sync.
 */
import { esc, roundStateLabel, roundStateTone } from './utils.js';
import { formatMessage, t } from '../../utils/i18n.js';
import { normalizeRoundTodoSnapshot } from './todo.js';

let navRounds = [];
let navActiveRunId = null;
let navOnSelectRound = null;
let navLayoutFrame = 0;
let navLayoutReason = 'idle';
let navViewportTimer = 0;
let navScrollFrame = 0;
let navScrollToken = 0;
let navManualScrollUntil = 0;
let navProgrammaticScrollUntil = 0;
let navSelectionFollowUntil = 0;
let navResizeObserver = null;
let observedChatEl = null;
let observedInputEl = null;
let windowResizeBound = false;
let popoverFrame = 0;
const navTodoSignatureByRunId = new Map();

const ROUND_NAV_ID = 'round-nav-float';
const ROUND_NAV_VISIBLE_CLASS = 'rounds-timeline-visible';
const ROUND_NAV_MIN_GAP = 8;
const ROUND_NAV_EDGE_GAP = 4;
const ROUND_NAV_DEFAULT_NODE_HEIGHT = 34;
const ROUND_NAV_CONTEXT_TARGET = 5;
const ROUND_NAV_TOP_ZONE = 0.38;
const ROUND_NAV_BOTTOM_ZONE = 0.62;
const ROUND_NAV_VIEWPORT_DEBOUNCE_MS = 140;
const ROUND_NAV_MANUAL_SCROLL_LOCK_MS = 2200;
const ROUND_NAV_PROGRAMMATIC_SCROLL_LOCK_MS = 180;
const ROUND_NAV_SELECTION_FOLLOW_MS = 2600;
const ROUND_NAV_DENSITIES = {
    full: { width: 228, safeGap: 28, minMessageWidth: 520 },
    compact: { width: 128, safeGap: 24, minMessageWidth: 384 },
    dot: { width: 96, safeGap: 20, minMessageWidth: 274 },
};

export function renderRoundNavigator(rounds, onSelectRound, options = {}) {
    navRounds = Array.isArray(rounds) ? rounds : [];
    navOnSelectRound = onSelectRound;
    if (Object.prototype.hasOwnProperty.call(options, 'activeRunId')) {
        navActiveRunId = String(options.activeRunId || '').trim() || null;
    }
    const nav = ensureRoundNavigator();
    if (navRounds.length === 0) {
        hideRoundNavigator();
        nav.innerHTML = '';
        return;
    }

    const list = nav.querySelector('.round-nav-list');
    const preservedScrollTop = list?.scrollTop || 0;
    renderNavigatorDom(nav, {
        scrollTop: preservedScrollTop,
        layoutReason: options.layoutReason || 'structure',
    });
    installRoundNavLayoutWatch();
    scheduleRoundNavLayout(options.layoutReason || 'structure');
}

export function hideRoundNavigator() {
    const nav = document.getElementById(ROUND_NAV_ID);
    setTimelineVisibility(false);
    if (!nav) {
        return;
    }
    nav.style.display = 'none';
    cancelRoundNavLayout();
}

export function clearRoundNavigator() {
    navRounds = [];
    navActiveRunId = null;
    navOnSelectRound = null;
    navTodoSignatureByRunId.clear();
    const nav = document.getElementById(ROUND_NAV_ID);
    setTimelineVisibility(false);
    if (!nav) {
        return;
    }
    nav.style.display = 'none';
    nav.innerHTML = '';
    cancelRoundNavLayout();
}

export function setActiveRoundNav(runId, options = {}) {
    navActiveRunId = String(runId || '').trim() || null;
    const nav = document.getElementById(ROUND_NAV_ID);
    if (!nav || navRounds.length === 0) return;
    syncRoundNavActiveState(nav);
    installRoundNavLayoutWatch();
    const reason = options.layoutReason || (options.follow === false ? 'active' : 'follow-active');
    scheduleRoundNavLayout(reason);
}

export function patchRoundNavigatorTodo(runId, todoSnapshot) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) {
        return false;
    }

    const nav = document.getElementById(ROUND_NAV_ID);
    const selector = `.round-nav-node[data-run-id="${escapeSelectorValue(safeRunId)}"]`;
    const node = nav?.querySelector?.(selector) || null;
    if (!nav || !node || nav.style.display === 'none') {
        return false;
    }

    const normalizedTodo = normalizeRoundTodoSnapshot(todoSnapshot, safeRunId);
    let patchedRound = null;
    navRounds = navRounds.map(round => {
        if (String(round?.run_id || '') !== safeRunId) {
            return round;
        }
        patchedRound = normalizedTodo
            ? { ...round, todo: normalizedTodo }
            : omitRoundTodo(round);
        return patchedRound;
    });

    if (!patchedRound) {
        return false;
    }

    node.__round = patchedRound;
    node.classList.toggle('has-todo', normalizedTodo !== null);
    const nextSignature = buildRoundTodoSignature(normalizedTodo);
    const previousSignature = navTodoSignatureByRunId.get(safeRunId) || '';
    navTodoSignatureByRunId.set(safeRunId, nextSignature);
    if (nextSignature === previousSignature) {
        return true;
    }
    patchRoundNavDetail(node, patchedRound, normalizedTodo);
    return true;
}

function ensureRoundNavigator() {
    let nav = document.getElementById(ROUND_NAV_ID);
    const host = getNavigatorHost();
    if (!nav) {
        nav = document.createElement('aside');
        nav.id = ROUND_NAV_ID;
        nav.className = 'round-nav-float round-nav-timeline';
    }
    if (nav.parentNode !== host) {
        host.appendChild(nav);
    }
    setTimelineVisibility(true);
    return nav;
}

function getNavigatorHost() {
    return document.querySelector('.chat-container')
        || document.body;
}

function setTimelineVisibility(visible) {
    const host = document.querySelector('.chat-container');
    if (!host) {
        return;
    }
    host.classList.toggle(ROUND_NAV_VISIBLE_CLASS, visible === true);
    if (!visible) {
        delete host.dataset.roundTimelineDensity;
    }
}

function renderNavigatorDom(nav, options = {}) {
    nav.style.display = 'block';
    nav.className = 'round-nav-float round-nav-timeline';
    nav.setAttribute('aria-label', 'Rounds');

    const { list, track } = ensureRoundNavigatorStructure(nav);
    if (!list) return;
    bindRoundNavListIntent(list);
    const existingNodes = new Map(
        Array.from(track.querySelectorAll('.round-nav-node')).map(node => [
            String(node.dataset.runId || ''),
            node,
        ]),
    );
    const nextNodes = [];
    navRounds.forEach(round => {
        const runId = String(round?.run_id || '').trim();
        if (!runId) {
            return;
        }
        const node = existingNodes.get(runId) || buildRoundNavNode();
        existingNodes.delete(runId);
        updateRoundNavNode(node, round);
        nextNodes.push(node);
    });
    existingNodes.forEach(node => {
        navTodoSignatureByRunId.delete(String(node.dataset.runId || ''));
    });
    replaceElementChildren(track, ...nextNodes);

    if (typeof options.scrollTop === 'number') {
        list.scrollTop = options.scrollTop;
    }
}

function ensureRoundNavigatorStructure(nav) {
    let list = nav.querySelector('.round-nav-list');
    if (!list) {
        replaceElementChildren(nav);
        list = document.createElement('div');
        list.className = 'round-nav-list';
        list.setAttribute('role', 'list');
        nav.appendChild(list);
    }

    let track = list.querySelector('.round-nav-track');
    if (!track) {
        track = document.createElement('div');
        track.className = 'round-nav-track';
        replaceElementChildren(list, track);
    }

    return { list, track };
}

function replaceElementChildren(element, ...children) {
    if (!element) {
        return;
    }
    if (typeof element.replaceChildren === 'function') {
        element.replaceChildren(...children);
        return;
    }

    const childNodes = Array.from(element.childNodes || []);
    childNodes.forEach(child => {
        if (typeof child.remove === 'function') {
            child.remove();
        } else if (typeof element.removeChild === 'function') {
            element.removeChild(child);
        }
    });
    if (Array.isArray(element.childNodes)) {
        element.childNodes.length = 0;
    }
    children.forEach(child => {
        if (child && typeof element.appendChild === 'function') {
            element.appendChild(child);
        }
    });
}

function bindRoundNavListIntent(list) {
    if (!list || list.dataset.roundNavIntentBound === 'true') {
        return;
    }
    list.dataset.roundNavIntentBound = 'true';
    const markManual = () => {
        if (nowMs() < navProgrammaticScrollUntil) {
            return;
        }
        navManualScrollUntil = nowMs() + ROUND_NAV_MANUAL_SCROLL_LOCK_MS;
        cancelRoundNavListScroll();
    };
    list.addEventListener('wheel', markManual, { passive: true });
    list.addEventListener('touchstart', markManual, { passive: true });
    list.addEventListener('pointerdown', markManual, { passive: true });
    list.addEventListener('keydown', event => {
        const key = String(event?.key || '');
        if ([
            'ArrowUp',
            'ArrowDown',
            'PageUp',
            'PageDown',
            'Home',
            'End',
            ' ',
        ].includes(key)) {
            markManual();
        }
    });
    list.addEventListener('scroll', () => {
        if (nowMs() < navProgrammaticScrollUntil) {
            return;
        }
        navManualScrollUntil = nowMs() + ROUND_NAV_MANUAL_SCROLL_LOCK_MS;
    }, { passive: true });
}

function buildRoundNavNode() {
    const node = document.createElement('div');
    node.className = 'round-nav-node';
    node.setAttribute('role', 'listitem');

    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'round-nav-item';
    item.onclick = () => {
        const round = node.__round;
        const runId = String(round?.run_id || node.dataset.runId || '').trim();
        if (!runId) {
            return;
        }
        navManualScrollUntil = 0;
        navSelectionFollowUntil = nowMs() + ROUND_NAV_SELECTION_FOLLOW_MS;
        navActiveRunId = runId;
        const nav = document.getElementById(ROUND_NAV_ID);
        if (nav) {
            syncRoundNavActiveState(nav);
        }
        if (navOnSelectRound && round) navOnSelectRound(round);
    };
    node.addEventListener('mouseenter', () => showRoundNavPopover(node));
    node.addEventListener('mouseleave', () => scheduleHideRoundNavPopover(node));
    item.addEventListener('focusin', () => showRoundNavPopover(node));
    item.addEventListener('focusout', () => scheduleHideRoundNavPopover(node));

    node.appendChild(item);
    return node;
}

function updateRoundNavNode(node, round) {
    const runId = String(round?.run_id || '').trim();
    node.__round = round;
    node.dataset.runId = runId;
    const isActive = navActiveRunId && navActiveRunId === runId;
    node.classList.toggle('active', isActive);

    const todo = normalizeRoundTodoSnapshot(round.todo, runId);
    const approvalCount = Number(round?.pending_tool_approval_count || 0);
    const stateTone = approvalCount > 0 ? 'warning' : roundStateTone(round);
    const stateLabel = approvalCount > 0
        ? t('rounds.pending_approvals').replace('{count}', String(approvalCount))
        : roundStateLabel(round);
    node.dataset.stateTone = stateTone;
    node.classList.toggle('has-todo', todo !== null);

    const item = node.querySelector('.round-nav-item');
    if (item) {
        item.dataset.runId = runId;
        item.title = String(round.intent || t('rounds.no_intent'));
        item.setAttribute('aria-current', isActive ? 'true' : 'false');
        item.classList.toggle('active', isActive);
        const timeText = formatRoundNavTime(round.created_at);
        const previewText = String(round.intent || t('rounds.no_intent'));
        if (item.dataset.renderKey !== `${stateLabel}\u001f${timeText}\u001f${previewText}`) {
            item.innerHTML = `
                <span class="round-nav-marker">
                    <span class="round-nav-dot" title="${esc(stateLabel)}" aria-label="${esc(stateLabel)}"></span>
                </span>
                <span class="round-nav-copy">
                    <span class="round-nav-time">${esc(timeText)}</span>
                    <span class="txt">${esc(previewText)}</span>
                </span>
            `;
            item.dataset.renderKey = `${stateLabel}\u001f${timeText}\u001f${previewText}`;
        }
    }

    const nextSignature = buildRoundTodoSignature(todo);
    const previousSignature = navTodoSignatureByRunId.get(runId) || '';
    navTodoSignatureByRunId.set(runId, nextSignature);
    if (!node.querySelector('.round-nav-detail')) {
        node.appendChild(buildRoundNavDetail(round, todo));
        return;
    }
    if (nextSignature !== previousSignature) {
        patchRoundNavDetail(node, round, todo);
    }
}

function showRoundNavPopover(node) {
    if (!node || !canShowRoundNavPopover(node)) {
        return;
    }
    if (node.__popoverHideTimer) {
        clearTimeout(node.__popoverHideTimer);
        node.__popoverHideTimer = 0;
    }
    const detail = node.querySelector?.('.round-nav-detail') || null;
    const item = node.querySelector?.('.round-nav-item') || null;
    if (!detail || !item) {
        return;
    }
    positionRoundNavPopover(node, item, detail);
    node.dataset.popoverPositioned = 'true';
    scheduleRoundNavPopoverOpen(node);
}

function hideRoundNavPopover(node) {
    if (!node) {
        return;
    }
    if (node.__popoverHideTimer) {
        clearTimeout(node.__popoverHideTimer);
        node.__popoverHideTimer = 0;
    }
    if (popoverFrame && typeof window !== 'undefined') {
        window.cancelAnimationFrame?.(popoverFrame);
        popoverFrame = 0;
    }
    delete node.dataset.popoverOpen;
    delete node.dataset.popoverPositioned;
    const detail = node.querySelector?.('.round-nav-detail') || null;
    if (detail) {
        detail.style.left = '';
        detail.style.top = '';
    }
}

function scheduleHideRoundNavPopover(node) {
    if (!node) {
        return;
    }
    if (node.__popoverHideTimer) {
        clearTimeout(node.__popoverHideTimer);
    }
    const hide = () => hideRoundNavPopover(node);
    if (typeof window !== 'undefined' && typeof window.setTimeout === 'function') {
        node.__popoverHideTimer = window.setTimeout(hide, 90);
        return;
    }
    hide();
}

function scheduleRoundNavPopoverOpen(node) {
    if (popoverFrame && typeof window !== 'undefined') {
        window.cancelAnimationFrame?.(popoverFrame);
        popoverFrame = 0;
    }
    const open = () => {
        popoverFrame = 0;
        if (node.dataset.popoverPositioned === 'true') {
            node.dataset.popoverOpen = 'true';
        }
    };
    if (typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function') {
        popoverFrame = window.requestAnimationFrame(open);
        return;
    }
    open();
}

function canShowRoundNavPopover(node) {
    if (node.dataset.roundNavHidden === 'true') {
        return false;
    }
    const round = node.__round || {};
    const todo = normalizeRoundTodoSnapshot(round.todo, String(round?.run_id || node.dataset.runId || ''));
    if (!todo || !Array.isArray(todo.items) || todo.items.length === 0) {
        return false;
    }
    return true;
}

function positionRoundNavPopover(node, item, detail) {
    const itemRect = item.getBoundingClientRect?.() || null;
    const detailRect = detail.getBoundingClientRect?.() || null;
    const chatRect = document.querySelector('.chat-container')?.getBoundingClientRect?.() || null;
    if (!itemRect || !chatRect) {
        return;
    }
    const width = Math.max(220, Number(detailRect?.width || 260));
    const height = Math.max(80, Number(detailRect?.height || detail.scrollHeight || 120));
    const gap = 14;
    const left = Math.max(
        Number(chatRect.left || 0) + 12,
        Number(itemRect.left || 0) - width - gap,
    );
    const topMin = Number(chatRect.top || 0) + 12;
    const topMax = Math.max(topMin, Number(chatRect.bottom || 0) - height - 12);
    const naturalTop = Number(itemRect.top || 0) - 8;
    const top = Math.max(topMin, Math.min(topMax, naturalTop));
    detail.style.left = `${Math.round(left)}px`;
    detail.style.top = `${Math.round(top)}px`;
}

function syncRoundNavActiveState(nav) {
    Array.from(nav.querySelectorAll('.round-nav-node')).forEach(node => {
        const runId = String(node.dataset.runId || '').trim();
        const isActive = !!navActiveRunId && navActiveRunId === runId;
        node.classList.toggle('active', isActive);
        const item = node.querySelector('.round-nav-item');
        if (item) {
            item.classList.toggle('active', isActive);
            item.setAttribute('aria-current', isActive ? 'true' : 'false');
        }
    });
}

function buildRoundNavDetail(round, todo) {
    const detail = document.createElement('div');
    detail.className = 'round-nav-detail';
    detail.innerHTML = todo ? renderRoundNavTodo(todo) : '';
    return detail;
}

function omitRoundTodo(round) {
    const { todo: _todo, ...rest } = round;
    return rest;
}

function renderRoundNavTodo(todo) {
    const items = Array.isArray(todo.items) ? todo.items : [];
    if (items.length === 0) {
        return '';
    }
    const inProgressCount = items.filter(item => item.status === 'in_progress').length;
    return `
        <div class="round-nav-todo" data-run-id="${esc(todo.run_id)}">
            <div class="round-nav-todo-head">
                <span class="round-nav-todo-title">${esc(t('rounds.todo.title'))}</span>
                <span class="round-nav-todo-count">${esc(formatMessage('rounds.todo.items', { count: items.length }))}</span>
                ${inProgressCount > 0 ? `<span class="round-nav-todo-count">${esc(formatMessage('rounds.todo.in_progress_count', { count: inProgressCount }))}</span>` : ''}
            </div>
            <ul class="round-nav-todo-list">
                ${items.map(item => `
                    <li class="round-nav-todo-item" data-status="${esc(item.status)}">
                        <span class="round-nav-todo-text" title="${esc(item.content)}">${esc(item.content)}</span>
                        <span class="round-nav-todo-status" data-status="${esc(item.status)}">${esc(t(`rounds.todo.status.${item.status}`))}</span>
                    </li>
                `).join('')}
            </ul>
        </div>
    `;
}

function buildRoundNavTodoElement(todo) {
    const detail = document.createElement('div');
    detail.className = 'round-nav-detail';
    detail.innerHTML = renderRoundNavTodo(todo);
    return detail.querySelector?.('.round-nav-todo') || null;
}

function patchRoundNavTodo(existingTodo, todo) {
    const nextTodo = todo ? buildRoundNavTodoElement(todo) : null;
    if (existingTodo && nextTodo) {
        const existingItems = Array.from(existingTodo.querySelectorAll('.round-nav-todo-item'));
        const items = Array.isArray(todo.items) ? todo.items : [];
        const counts = Array.from(existingTodo.querySelectorAll('.round-nav-todo-count'));
        const hasProgressCount = items.some(item => item.status === 'in_progress');
        if (existingItems.length !== items.length || (counts.length > 1) !== hasProgressCount) {
            existingTodo.replaceWith(nextTodo);
            return nextTodo;
        }
        patchRoundNavTodoHead(existingTodo, todo);
        existingItems.forEach((itemEl, index) => {
            patchRoundNavTodoItem(itemEl, items[index]);
        });
        return existingTodo;
    }
    if (nextTodo) {
        return nextTodo;
    }
    return null;
}

function patchRoundNavTodoHead(todoEl, todo) {
    const counts = Array.from(todoEl.querySelectorAll('.round-nav-todo-count'));
    const items = Array.isArray(todo?.items) ? todo.items : [];
    if (counts[0]) {
        counts[0].textContent = formatMessage('rounds.todo.items', { count: items.length });
    }
    const inProgressCount = items.filter(item => item.status === 'in_progress').length;
    if (counts[1]) {
        counts[1].textContent = inProgressCount > 0
            ? formatMessage('rounds.todo.in_progress_count', { count: inProgressCount })
            : '';
    }
}

function patchRoundNavTodoItem(itemEl, item) {
    if (!itemEl || !item) {
        return;
    }
    const status = String(item.status || 'pending');
    itemEl.dataset.status = status;
    const textEl = itemEl.querySelector('.round-nav-todo-text');
    if (textEl) {
        const content = String(item.content || '');
        if (textEl.textContent !== content) {
            textEl.textContent = content;
        }
        textEl.title = content;
    }
    const statusEl = itemEl.querySelector('.round-nav-todo-status');
    if (statusEl) {
        statusEl.dataset.status = status;
        const label = t(`rounds.todo.status.${status}`);
        if (statusEl.textContent !== label) {
            statusEl.textContent = label;
        }
    }
}

function buildRoundTodoSignature(todo) {
    const items = Array.isArray(todo?.items) ? todo.items : [];
    return items.map(item => [
        String(item?.content || ''),
        String(item?.status || ''),
    ].join('\u001f')).join('\u001e');
}

function patchRoundNavDetail(node, round, todo) {
    node.classList.toggle('has-todo', todo !== null);
    let detail = node.querySelector('.round-nav-detail');
    if (!detail) {
        detail = buildRoundNavDetail(round, todo);
        node.appendChild(detail);
    }
    const existingTodo = detail.querySelector('.round-nav-todo');
    const patchedTodo = patchRoundNavTodo(existingTodo, todo);
    if (!existingTodo && patchedTodo) {
        detail.appendChild(patchedTodo);
    } else if (existingTodo && !patchedTodo) {
        existingTodo.remove?.();
    }
    if (node.dataset.popoverOpen === 'true') {
        const item = node.querySelector?.('.round-nav-item') || null;
        if (item) {
            positionRoundNavPopover(node, item, detail);
        }
    }
}

function formatRoundNavTime(value) {
    const parsed = value ? new Date(value) : null;
    if (!parsed || Number.isNaN(parsed.getTime())) {
        return '';
    }
    return parsed.toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
    });
}

function installRoundNavLayoutWatch() {
    if (!windowResizeBound && typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
        window.addEventListener('resize', scheduleRoundNavViewportLayout, { passive: true });
        windowResizeBound = true;
    }

    installRoundNavResizeObserver();
    updateRoundTimelineBottom();
}

function installRoundNavResizeObserver() {
    if (typeof ResizeObserver !== 'function') {
        return;
    }
    if (!navResizeObserver) {
        navResizeObserver = new ResizeObserver(scheduleRoundNavViewportLayout);
    }

    const chatEl = document.querySelector('.chat-container');
    const inputEl = document.getElementById('input-container');
    if (chatEl && chatEl !== observedChatEl) {
        if (observedChatEl) {
            navResizeObserver.unobserve(observedChatEl);
        }
        observedChatEl = chatEl;
        navResizeObserver.observe(observedChatEl);
    }
    if (inputEl && inputEl !== observedInputEl) {
        if (observedInputEl) {
            navResizeObserver.unobserve(observedInputEl);
        }
        observedInputEl = inputEl;
        navResizeObserver.observe(observedInputEl);
    }
}

function scheduleRoundNavViewportLayout() {
    if (navViewportTimer) {
        clearTimeout(navViewportTimer);
        navViewportTimer = 0;
    }
    if (typeof window !== 'undefined' && typeof window.setTimeout === 'function') {
        navViewportTimer = window.setTimeout(() => {
            navViewportTimer = 0;
            scheduleRoundNavLayout('viewport');
        }, ROUND_NAV_VIEWPORT_DEBOUNCE_MS);
        return;
    }
    scheduleRoundNavLayout('viewport');
}

function scheduleRoundNavLayout(reason = 'structure') {
    navLayoutReason = mergeRoundNavLayoutReason(navLayoutReason, reason);
    if (navLayoutFrame) {
        return;
    }
    const run = () => {
        const reasonToRun = navLayoutReason;
        navLayoutReason = 'idle';
        navLayoutFrame = 0;
        syncRoundNavLayout(reasonToRun);
    };
    if (typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function') {
        navLayoutFrame = -1;
        const frameId = window.requestAnimationFrame(run);
        if (navLayoutFrame === -1) {
            navLayoutFrame = frameId;
        }
        return;
    }
    run();
}

function cancelRoundNavLayout() {
    if (navViewportTimer) {
        clearTimeout(navViewportTimer);
        navViewportTimer = 0;
    }
    if (!navLayoutFrame) {
        cancelRoundNavListScroll();
        return;
    }
    if (typeof window !== 'undefined' && typeof window.cancelAnimationFrame === 'function') {
        window.cancelAnimationFrame(navLayoutFrame);
    }
    navLayoutFrame = 0;
    cancelRoundNavListScroll();
}

function mergeRoundNavLayoutReason(current, next) {
    const weights = {
        idle: 0,
        todo: 2,
        active: 3,
        'follow-active': 7,
        'new-latest': 8,
        'sync-visible-active': 7,
        viewport: 5,
        structure: 6,
    };
    return (weights[next] || 0) > (weights[current] || 0) ? next : current;
}

function syncRoundNavLayout(reason = 'structure') {
    const nav = document.getElementById(ROUND_NAV_ID);
    const list = nav?.querySelector?.('.round-nav-list');
    const track = list?.querySelector?.('.round-nav-track');
    const chat = document.querySelector('.chat-container');
    if (!nav || !list || !track || !chat || nav.style.display === 'none') {
        return;
    }

    updateRoundTimelineBottom();

    const density = syncRoundNavDensity(nav, chat);
    if (density === 'hidden') {
        return;
    }

    if (isCompactRoundTimeline()) {
        Array.from(track.querySelectorAll('.round-nav-node')).forEach(node => {
            node.style.transform = '';
            node.style.position = '';
            delete node.dataset.anchorState;
            delete node.dataset.roundNavHidden;
        });
        track.style.minHeight = '';
        return;
    }

    const chatRect = chat.getBoundingClientRect();
    const nodes = Array.from(track.querySelectorAll('.round-nav-node'));
    const positioned = nodes.map((node, index) => {
        const anchor = findRoundAnchor(node.dataset.runId);
        const anchorRect = anchor?.getBoundingClientRect?.() || null;
        const height = resolveNodeHeight(node);
        const anchorState = anchorRect ? resolveAnchorState(anchorRect, chatRect) : 'unloaded';
        node.dataset.anchorState = anchorState;
        node.style.position = '';
        const item = {
            node,
            index,
            height,
            anchorRect,
            anchorState,
            anchorZone: 'middle',
            y: 0,
        };
        item.anchorZone = resolveRoundNavAnchorZone(item, chatRect);
        node.dataset.anchorZone = item.anchorZone;
        return item;
    });

    nav.classList.toggle('round-nav-animated', shouldAnimateRoundNavLayout(reason));
    positioned.forEach(item => {
        writeRoundNavTransform(item.node, '');
        item.node.style.zIndex = item.node.classList.contains('active') ? '2' : '1';
        item.node.dataset.roundNavHidden = 'false';
    });
    scrollRoundNavListToFocus(list, positioned, reason);
    nav.dataset.hasPreviousRounds = Number(list.scrollTop || 0) > 1 ? 'true' : 'false';
    nav.dataset.hasNextRounds = Number(list.scrollTop || 0) + Number(list.clientHeight || 0)
        < Number(list.scrollHeight || 0) - 1 ? 'true' : 'false';
    track.style.minHeight = '';
}

function resolveRoundNavWindowFocus(items, reason) {
    const activeIndex = items.findIndex(item => item.node.classList.contains('active'));
    const reasonText = String(reason || '');
    if (
        activeIndex >= 0
        && ['active', 'follow-active', 'new-latest', 'sync-visible-active'].includes(reasonText)
    ) {
        return {
            index: activeIndex,
            zone: reasonText === 'new-latest' ? 'bottom' : 'middle',
        };
    }

    if (activeIndex >= 0 && items[activeIndex].anchorState === 'visible') {
        return {
            index: activeIndex,
            zone: items[activeIndex].anchorZone || 'middle',
        };
    }

    const visibleRange = resolveVisibleRoundNavRange(items);
    if (visibleRange) {
        const focusIndex = chooseVisibleRoundNavFocus(items, visibleRange);
        return {
            index: focusIndex,
            zone: items[focusIndex].anchorZone || 'middle',
        };
    }

    const focusIndex = resolveRoundNavWindowIndex(items, reason);
    return {
        index: focusIndex,
        zone: String(reason || '') === 'new-latest' ? 'bottom' : 'middle',
    };
}

function resolveRoundNavWindowIndex(items, reason) {
    const reasonText = String(reason || '');
    const activeIndex = items.findIndex(item => item.node.classList.contains('active'));
    if (activeIndex >= 0) {
        return activeIndex;
    }
    if (reasonText === 'new-latest') {
        return resolveLastLoadedRoundNavIndex(items);
    }
    const firstLoaded = items.findIndex(item => item.anchorState !== 'unloaded');
    return firstLoaded >= 0 ? firstLoaded : 0;
}

function resolveVisibleRoundNavRange(items) {
    let start = -1;
    let end = -1;
    items.forEach((item, index) => {
        if (item.anchorState !== 'visible') {
            return;
        }
        if (start < 0) {
            start = index;
        }
        end = index;
    });
    return start >= 0 ? { start, end } : null;
}

function chooseVisibleRoundNavFocus(items, visibleRange) {
    const start = Math.max(0, Number(visibleRange?.start || 0));
    const end = Math.max(start, Math.min(items.length - 1, Number(visibleRange?.end || start)));
    for (let index = start; index <= end; index += 1) {
        if (items[index].node.classList.contains('active')) {
            return index;
        }
    }
    return Math.round((start + end) / 2);
}

function scrollRoundNavListToFocus(list, items, reason) {
    if (!list || items.length === 0) {
        return;
    }
    if (!shouldAutoScrollRoundNavList(reason)) {
        return;
    }
    const focus = resolveRoundNavWindowFocus(items, reason);
    const focusItem = items[focus.index] || null;
    if (!focusItem?.node) {
        return;
    }
    if (
        String(reason || '') === 'follow-active'
        && isRoundNavFocusComfortablyVisible(list, items, focus.index)
    ) {
        return;
    }
    const targetTop = resolveRoundNavListScrollTop(list, focusItem, items, focus.zone, reason);
    const animate = shouldAnimateRoundNavLayout(reason);
    scrollRoundNavListTo(list, targetTop, { animate });
}

function shouldAutoScrollRoundNavList(reason) {
    const reasonText = String(reason || '');
    if (reasonText === 'new-latest') {
        navManualScrollUntil = 0;
        return true;
    }
    if (reasonText === 'sync-visible-active' && nowMs() < navSelectionFollowUntil) {
        return true;
    }
    if (!['follow-active', 'sync-visible-active'].includes(reasonText)) {
        return false;
    }
    return nowMs() >= navManualScrollUntil;
}

function isRoundNavFocusComfortablyVisible(list, items, focusIndex) {
    if (!list || focusIndex < 0 || focusIndex >= items.length) {
        return false;
    }
    const metrics = buildRoundNavListMetrics(list, items);
    const currentTop = Number(list.scrollTop || 0);
    const itemTop = metrics.tops[focusIndex] || 0;
    const itemBottom = itemTop + (metrics.heights[focusIndex] || ROUND_NAV_DEFAULT_NODE_HEIGHT);
    const comfortGap = Math.min(ROUND_NAV_DEFAULT_NODE_HEIGHT * 2, metrics.viewportHeight * 0.22);
    return itemTop >= currentTop + comfortGap
        && itemBottom <= currentTop + metrics.viewportHeight - comfortGap;
}

function resolveRoundNavListScrollTop(list, item, items, zone, reason) {
    const currentTop = Number(list.scrollTop || 0);
    const metrics = buildRoundNavListMetrics(list, items);
    const itemIndex = Math.max(0, Number(item.index || 0));
    let target = resolveRoundNavContextWindowTop(metrics, itemIndex);
    if (zone === 'top') {
        target = metrics.tops[itemIndex] - ROUND_NAV_DEFAULT_NODE_HEIGHT;
    } else if (zone === 'bottom' || String(reason || '') === 'new-latest') {
        target = metrics.tops[itemIndex]
            - metrics.viewportHeight
            + metrics.heights[itemIndex]
            + ROUND_NAV_DEFAULT_NODE_HEIGHT;
    }
    const scrollHeight = Math.max(Number(list.scrollHeight || 0), metrics.scrollHeight);
    const maxTop = Math.max(0, scrollHeight - metrics.viewportHeight);
    const nextTarget = Number.isFinite(target) ? target : currentTop;
    return Math.max(0, Math.min(maxTop, nextTarget));
}

function buildRoundNavListMetrics(list, items) {
    const rectHeight = Number(list.getBoundingClientRect?.().height || 0);
    const viewportHeight = Math.max(
        ROUND_NAV_DEFAULT_NODE_HEIGHT,
        Number(list.clientHeight || 0),
        rectHeight,
        1,
    );
    const heights = items.map(item => Math.max(
        ROUND_NAV_DEFAULT_NODE_HEIGHT,
        Number(item.node?.offsetHeight || 0),
        Number(item.height || 0),
    ));
    const fallbackRowHeight = Math.max(
        ROUND_NAV_DEFAULT_NODE_HEIGHT + ROUND_NAV_MIN_GAP,
        Math.round((heights.reduce((sum, height) => sum + height, 0) / Math.max(1, heights.length)) + ROUND_NAV_MIN_GAP),
    );
    const tops = [];
    let virtualTop = 0;
    items.forEach((item, index) => {
        const measuredTop = Number(item.node?.offsetTop || 0);
        const canUseMeasuredTop = measuredTop > 0 || index === 0;
        tops.push(canUseMeasuredTop && measuredTop >= virtualTop - 1 ? measuredTop : virtualTop);
        virtualTop = tops[index] + heights[index] + ROUND_NAV_MIN_GAP;
    });
    const scrollHeight = Math.max(
        Number(list.scrollHeight || 0),
        virtualTop,
        items.length * fallbackRowHeight,
    );
    return {
        fallbackRowHeight,
        heights,
        scrollHeight,
        tops,
        viewportHeight,
    };
}

function resolveRoundNavContextWindowTop(metrics, focusIndex) {
    const itemCount = metrics.tops.length;
    if (itemCount <= 0) {
        return 0;
    }
    const capacity = Math.max(1, Math.floor(
        (metrics.viewportHeight + ROUND_NAV_MIN_GAP) / metrics.fallbackRowHeight,
    ));
    const visibleCapacity = Math.min(itemCount, capacity);
    const context = Math.min(
        ROUND_NAV_CONTEXT_TARGET,
        Math.max(0, Math.floor((visibleCapacity - 1) / 2)),
    );
    let startIndex = Math.max(0, focusIndex - context);
    const maxStartIndex = Math.max(0, itemCount - visibleCapacity);
    startIndex = Math.min(startIndex, maxStartIndex);
    if (focusIndex >= startIndex + visibleCapacity) {
        startIndex = Math.max(0, Math.min(maxStartIndex, focusIndex - visibleCapacity + 1));
    }
    return metrics.tops[startIndex] || 0;
}

function scrollRoundNavListTo(list, targetTop, options = {}) {
    const fromTop = Number(list.scrollTop || 0);
    const viewportHeight = Math.max(
        ROUND_NAV_DEFAULT_NODE_HEIGHT,
        Number(list.clientHeight || 0),
        Number(list.getBoundingClientRect?.().height || 0),
    );
    const virtualHeight = Math.max(
        0,
        Number(list.querySelectorAll?.('.round-nav-node')?.length || 0)
            * (ROUND_NAV_DEFAULT_NODE_HEIGHT + ROUND_NAV_MIN_GAP),
    );
    const maxTop = Math.max(0, Math.max(Number(list.scrollHeight || 0), virtualHeight) - viewportHeight);
    const toTop = Math.max(0, Math.min(maxTop, Number(targetTop || 0)));
    const distance = Math.abs(toTop - fromTop);
    const durationMs = Math.round(Math.max(180, Math.min(420, 160 + (distance / 5))));
    if (
        Math.abs(toTop - fromTop) < 1
        || options.animate !== true
        || typeof window === 'undefined'
        || typeof window.requestAnimationFrame !== 'function'
    ) {
        navProgrammaticScrollUntil = nowMs() + ROUND_NAV_PROGRAMMATIC_SCROLL_LOCK_MS;
        list.scrollTop = toTop;
        return;
    }
    cancelRoundNavListScroll();
    navProgrammaticScrollUntil = nowMs() + durationMs + ROUND_NAV_PROGRAMMATIC_SCROLL_LOCK_MS;
    const token = navScrollToken + 1;
    navScrollToken = token;
    const startedAt = window.performance?.now?.() || Date.now();
    if (!Number.isFinite(durationMs) || durationMs <= 0) {
        navProgrammaticScrollUntil = nowMs() + ROUND_NAV_PROGRAMMATIC_SCROLL_LOCK_MS;
        list.scrollTop = toTop;
        return;
    }
    const step = nowValue => {
        if (token !== navScrollToken) {
            return;
        }
        if (typeof nowValue !== 'number') {
            list.scrollTop = toTop;
            navScrollFrame = 0;
            return;
        }
        const now = nowValue;
        const rawProgress = (now - startedAt) / durationMs;
        const progress = Number.isFinite(rawProgress)
            ? Math.min(1, Math.max(0, rawProgress))
            : 1;
        list.scrollTop = fromTop + ((toTop - fromTop) * easeRoundNavScroll(progress));
        if (progress >= 1) {
            list.scrollTop = toTop;
            navScrollFrame = 0;
            return;
        }
        navScrollFrame = window.requestAnimationFrame(step);
    };
    navScrollFrame = window.requestAnimationFrame(step);
}

function cancelRoundNavListScroll() {
    navScrollToken += 1;
    if (navScrollFrame && typeof window !== 'undefined') {
        window.cancelAnimationFrame?.(navScrollFrame);
    }
    navScrollFrame = 0;
}

function easeRoundNavScroll(value) {
    const progress = Math.max(0, Math.min(1, Number(value || 0)));
    return 1 - ((1 - progress) ** 3);
}

function nowMs() {
    return globalThis.performance?.now?.() || Date.now();
}

function resolveRoundNavAnchorZone(item, chatRectOverride = null) {
    const rect = item?.anchorRect || null;
    const chatRect = chatRectOverride
        || document.querySelector('.chat-container')?.getBoundingClientRect?.()
        || null;
    if (!rect || !chatRect || Number(chatRect.height || 0) <= 0) {
        return 'middle';
    }
    const center = Number(rect.top || 0) + (Number(rect.height || 0) / 2);
    const ratio = (center - Number(chatRect.top || 0)) / Number(chatRect.height || 1);
    if (ratio <= ROUND_NAV_TOP_ZONE) {
        return 'top';
    }
    if (ratio >= ROUND_NAV_BOTTOM_ZONE) {
        return 'bottom';
    }
    return 'middle';
}

function resolveLastLoadedRoundNavIndex(items) {
    for (let index = items.length - 1; index >= 0; index -= 1) {
        if (items[index].anchorState !== 'unloaded') {
            return index;
        }
    }
    return Math.max(0, items.length - 1);
}

function shouldAnimateRoundNavLayout(reason) {
    return ['follow-active', 'new-latest', 'sync-visible-active'].includes(String(reason || ''));
}

function writeRoundNavTransform(node, y) {
    const next = y ? `translateY(${y}px)` : '';
    if (node.style.transform !== next) {
        node.style.transform = next;
    }
}

function syncRoundNavDensity(nav, chat) {
    if (isCompactRoundTimeline()) {
        return applyRoundNavDensity(nav, chat, 'full');
    }
    const chatWidth = Number(chat.getBoundingClientRect?.().width || 0);
    const density = resolveRoundNavDensity(chatWidth);
    return applyRoundNavDensity(nav, chat, density);
}

function resolveRoundNavDensity(chatWidth) {
    if (chatWidth <= 0) {
        return 'hidden';
    }
    if (canUseRoundNavDensity(chatWidth, ROUND_NAV_DENSITIES.full)) {
        return 'full';
    }
    if (canUseRoundNavDensity(chatWidth, ROUND_NAV_DENSITIES.compact)) {
        return 'compact';
    }
    if (canUseRoundNavDensity(chatWidth, ROUND_NAV_DENSITIES.dot)) {
        return 'dot';
    }
    return 'hidden';
}

function canUseRoundNavDensity(chatWidth, density) {
    const rightInset = 16;
    const maxCenteredMessageWidth = chatWidth - (2 * (density.width + density.safeGap + rightInset));
    return maxCenteredMessageWidth >= density.minMessageWidth;
}

function applyRoundNavDensity(nav, chat, density) {
    nav.dataset.density = density;
    chat.dataset.roundTimelineDensity = density;
    return density;
}

function updateRoundTimelineBottom() {
    const chat = document.querySelector('.chat-container');
    const input = document.getElementById('input-container');
    if (!chat || !input) {
        return;
    }
    const chatRect = chat.getBoundingClientRect();
    const inputRect = input.getBoundingClientRect();
    const overlapsChat = inputRect.top < chatRect.bottom && inputRect.bottom > chatRect.top;
    const bottom = overlapsChat
        ? Math.max(16, chatRect.bottom - inputRect.top + 16)
        : 16;
    const value = `${Math.ceil(bottom)}px`;
    if (typeof chat.style.setProperty === 'function') {
        chat.style.setProperty('--round-timeline-bottom', value);
    } else {
        chat.style['--round-timeline-bottom'] = value;
    }
}

function isCompactRoundTimeline() {
    return typeof window !== 'undefined'
        && typeof window.matchMedia === 'function'
        && window.matchMedia('(max-width: 1180px)').matches;
}

function findRoundAnchor(runId) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) {
        return null;
    }
    const selector = `.session-round-section[data-run-id="${escapeSelectorValue(safeRunId)}"]`;
    const section = document.querySelector(selector);
    if (!isUsableRoundAnchor(section)) {
        return null;
    }
    const header = section?.querySelector?.('.round-detail-header') || null;
    return isUsableRoundAnchor(header) ? header : section;
}

function isUsableRoundAnchor(element) {
    if (!isElementLike(element)) {
        return false;
    }
    if (hasHiddenAncestor(element)) {
        return false;
    }
    if ('offsetParent' in element && element.offsetParent === null) {
        return false;
    }
    const rect = element.getBoundingClientRect?.() || null;
    if (!rect) {
        return false;
    }
    return Number(rect.width || 0) > 0 || Number(rect.height || 0) > 0;
}

function isElementLike(element) {
    return !!element
        && typeof element === 'object'
        && typeof element.querySelector === 'function'
        && typeof element.getBoundingClientRect === 'function';
}

function hasHiddenAncestor(element) {
    let current = element;
    while (current && current !== document.body) {
        const hiddenAttr = typeof current.getAttribute === 'function'
            ? current.getAttribute('hidden')
            : null;
        if (current.hidden === true || (hiddenAttr !== null && hiddenAttr !== undefined)) {
            return true;
        }
        current = current.parentNode;
    }
    return false;
}

function resolveAnchorState(anchorRect, chatRect) {
    if (!anchorRect) {
        return 'below';
    }
    if (anchorRect.bottom < chatRect.top) {
        return 'above';
    }
    if (anchorRect.top > chatRect.bottom) {
        return 'below';
    }
    return 'visible';
}

function resolveNodeHeight(node) {
    const item = node?.querySelector?.('.round-nav-item') || node;
    const rectHeight = Number(item?.getBoundingClientRect?.().height || 0);
    const offsetHeight = Number(item?.offsetHeight || 0);
    return Math.max(ROUND_NAV_DEFAULT_NODE_HEIGHT, rectHeight, offsetHeight);
}

function escapeSelectorValue(value) {
    if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') {
        return CSS.escape(String(value));
    }
    return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}
