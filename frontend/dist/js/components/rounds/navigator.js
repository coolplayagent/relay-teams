/**
 * components/rounds/navigator.js
 * Floating round navigator rendering and active-state sync.
 */
import { esc, roundStateLabel, roundStateTone } from './utils.js';
import { formatMessage, t } from '../../utils/i18n.js';
import { normalizeRoundTodoSnapshot } from './todo.js';

let navRounds = [];
let navActiveRunId = null;
let navOnSelectRound = null;
const ROUND_NAV_COLLAPSED_KEY = 'agent_teams_round_nav_collapsed';
const ROUND_NAV_POSITION_KEY = 'agent_teams_round_nav_position';
const ROUND_NAV_WIDTH_KEY = 'agent_teams_round_nav_width';
const collapsedTodoRunIds = new Set();
const expandedTodoRunIds = new Set();
const DEFAULT_NAV_WIDTH = 270;
const COLLAPSED_NAV_WIDTH = 180;
const MIN_NAV_WIDTH = 220;
const MAX_NAV_WIDTH = 560;

/** Persistent offset relative to chat container: { fromRight, fromTop }. */
let currentOffset = null;
let resizeObserver = null;
let scheduledOffsetFrame = 0;
let currentWidth = DEFAULT_NAV_WIDTH;

export function renderRoundNavigator(rounds, onSelectRound, options = {}) {
    navRounds = Array.isArray(rounds) ? rounds : [];
    navOnSelectRound = onSelectRound;
    if (Object.prototype.hasOwnProperty.call(options, 'activeRunId')) {
        navActiveRunId = String(options.activeRunId || '').trim() || null;
    }

    let nav = document.getElementById('round-nav-float');
    if (!nav) {
        nav = document.createElement('div');
        nav.id = 'round-nav-float';
        nav.className = 'round-nav-float';
        document.body.appendChild(nav);
        currentOffset = loadOffset();
        currentWidth = loadWidth();
        applyNavWidth(nav, currentWidth);
        installDrag(nav);
        installResize(nav);
        installResizeWatch(nav);
    }

    if (navRounds.length === 0) {
        nav.style.display = 'none';
        nav.innerHTML = '';
        return;
    }

    const list = nav.querySelector('.round-nav-list');
    const preservedScrollTop = list?.scrollTop || 0;
    renderNavigatorDom(nav, { scrollTop: preservedScrollTop });
    scheduleOffsetApply(nav);
}

export function hideRoundNavigator() {
    const nav = document.getElementById('round-nav-float');
    if (!nav) {
        return;
    }
    nav.style.display = 'none';
}

export function setActiveRoundNav(runId) {
    navActiveRunId = runId || null;
    const nav = document.getElementById('round-nav-float');
    if (!nav || navRounds.length === 0) return;
    const list = nav.querySelector('.round-nav-list');
    const preservedScrollTop = list?.scrollTop || 0;
    renderNavigatorDom(nav, { scrollTop: preservedScrollTop });
    scheduleOffsetApply(nav);
    const active = nav.querySelector('.round-nav-item.active');
    if (active) {
        active.scrollIntoView({ block: 'nearest' });
    }
}

/* ---- Chat container bounds ---- */

function getChatRect() {
    const chat = document.querySelector('.chat-container');
    if (chat) return chat.getBoundingClientRect();
    return new DOMRect(0, 0, window.innerWidth, window.innerHeight);
}

/* ---- Rendering ---- */

function renderNavigatorDom(nav, options = {}) {
    const isCollapsed = loadCollapsedState();
    nav.style.display = 'flex';
    nav.classList.toggle('collapsed', isCollapsed);
    if (isCollapsed) {
        applyCollapsedWidth(nav);
    } else {
        applyNavWidth(nav, currentWidth);
    }
    nav.innerHTML = `
        <div class="round-nav-header">
            <div class="round-nav-title">Rounds</div>
            <button type="button" class="round-nav-toggle" aria-label="${isCollapsed ? 'Expand rounds' : 'Collapse rounds'}">
                ${isCollapsed ? 'Show' : 'Hide'}
            </button>
        </div>
        <div class="round-nav-list"></div>
        <div class="round-nav-resizer" aria-hidden="true"></div>
    `;

    const toggle = nav.querySelector('.round-nav-toggle');
    if (toggle) {
        toggle.onclick = () => {
            // Record right edge before resize
            const navRect = nav.getBoundingClientRect();
            const chatRect = getChatRect();
            const fromRight = chatRect.right - navRect.right;

            const next = !loadCollapsedState();
            saveCollapsedState(next);
            renderNavigatorDom(nav);

            // Pin right edge: compute new fromRight keeping the right side anchored
            currentOffset = { fromRight, fromTop: currentOffset ? currentOffset.fromTop : (navRect.top - chatRect.top) };
            scheduleOffsetApply(nav);
            persistOffset();
        };
    }

    const list = nav.querySelector('.round-nav-list');
    if (!list) return;
    navRounds.forEach((round, idx) => {
        const node = document.createElement('div');
        node.className = 'round-nav-node';
        node.dataset.runId = round.run_id;
        const isActive = navActiveRunId && navActiveRunId === round.run_id;
        if (isActive) {
            node.classList.add('active');
        }

        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'round-nav-item';
        item.dataset.runId = round.run_id;
        item.title = String(round.intent || 'No intent');
        if (isActive) {
            item.classList.add('active');
        }
        const stateLabel = roundStateLabel(round);
        const approvalCount = Number(round?.pending_tool_approval_count || 0);
        item.innerHTML = `
            <span class="idx">${idx + 1}</span>
            <span class="round-nav-copy">
                <span class="txt">${esc(round.intent || t('rounds.no_intent'))}</span>
                <span class="round-nav-meta">
                    ${stateLabel ? `<span class="round-nav-state round-nav-state-${roundStateTone(round)}">${esc(stateLabel)}</span>` : ''}
                    ${approvalCount > 0 ? `<span class="round-nav-state round-nav-state-warning">${esc(t('rounds.pending_approvals').replace('{count}', String(approvalCount)))}</span>` : ''}
                </span>
            </span>
        `;
        item.onclick = () => {
            navActiveRunId = round.run_id;
            setActiveRoundNav(round.run_id);
            if (navOnSelectRound) navOnSelectRound(round);
        };
        node.appendChild(item);

        const todoBranch = buildRoundNavTodoBranch(round, { isCollapsed });
        if (todoBranch) {
            node.classList.add('has-todo');
            node.appendChild(todoBranch);
        }

        list.appendChild(node);
    });

    if (typeof options.scrollTop === 'number') {
        list.scrollTop = options.scrollTop;
    }
}

function buildRoundNavTodoBranch(round, { isCollapsed }) {
    if (isCollapsed || !shouldRenderRoundTodo(round)) {
        return null;
    }
    const todo = normalizeRoundTodoSnapshot(round.todo, round.run_id);
    if (todo === null) {
        return null;
    }
    const branch = document.createElement('div');
    branch.className = 'round-nav-todo-branch';
    branch.dataset.runId = round.run_id;
    branch.appendChild(buildRoundTodoCard(round, todo));
    return branch;
}

function buildRoundTodoCard(round, todo) {
    const inProgressCount = todo.items.filter(item => item.status === 'in_progress').length;
    const hasIncompleteItems = roundTodoHasIncompleteItems(todo);
    const details = document.createElement('details');
    details.className = 'round-todo-card';
    details.dataset.runId = round.run_id;
    if (resolveTodoCardDefaultOpen(round.run_id, hasIncompleteItems)) {
        details.open = true;
    }
    details.innerHTML = `
        <summary class="round-todo-summary">
            <span class="round-todo-summary-copy">
                <span class="round-todo-title-row">
                    <span class="round-todo-title">${esc(t('rounds.todo.title'))}</span>
                    <span class="round-todo-count">${esc(formatMessage('rounds.todo.items', { count: todo.items.length }))}</span>
                    ${inProgressCount > 0 ? `<span class="round-todo-count round-todo-count-progress">${esc(formatMessage('rounds.todo.in_progress_count', { count: inProgressCount }))}</span>` : ''}
                </span>
            </span>
            <span class="round-todo-toggle">${esc(details.open ? t('rounds.collapse') : t('rounds.expand'))}</span>
        </summary>
        <div class="round-todo-body">
            <ul class="round-todo-list">
                ${todo.items.map((item, index) => `
                    <li class="round-todo-item" data-status="${esc(item.status)}">
                        <span class="round-todo-index">${index + 1}</span>
                        <span class="round-todo-item-copy">
                            <span class="round-todo-item-text" title="${esc(item.content)}">${esc(item.content)}</span>
                        </span>
                        <span class="round-todo-status${item.status === 'completed' ? ' round-todo-status-simple' : ''}" data-status="${esc(item.status)}">${esc(t(`rounds.todo.status.${item.status}`))}</span>
                    </li>
                `).join('')}
            </ul>
        </div>
    `;
    const toggleEl = details.querySelector('.round-todo-toggle');
    details.addEventListener('toggle', () => {
        updateTodoCardPreference(round.run_id, hasIncompleteItems, details.open);
        if (toggleEl) {
            toggleEl.textContent = details.open ? t('rounds.collapse') : t('rounds.expand');
        }
    });
    return details;
}

function roundTodoHasIncompleteItems(todo) {
    return todo.items.some(item => item.status !== 'completed');
}

function resolveTodoCardDefaultOpen(runId, hasIncompleteItems) {
    if (hasIncompleteItems) {
        expandedTodoRunIds.delete(runId);
        return !collapsedTodoRunIds.has(runId);
    }
    collapsedTodoRunIds.delete(runId);
    return expandedTodoRunIds.has(runId);
}

function updateTodoCardPreference(runId, hasIncompleteItems, isOpen) {
    if (hasIncompleteItems) {
        expandedTodoRunIds.delete(runId);
        if (isOpen) {
            collapsedTodoRunIds.delete(runId);
        } else {
            collapsedTodoRunIds.add(runId);
        }
        return;
    }
    collapsedTodoRunIds.delete(runId);
    if (isOpen) {
        expandedTodoRunIds.add(runId);
    } else {
        expandedTodoRunIds.delete(runId);
    }
}

function shouldRenderRoundTodo(round) {
    const safeRunId = String(round?.run_id || '').trim();
    if (!safeRunId) {
        return false;
    }
    if (safeRunId !== resolveVisibleTodoRunId()) {
        return false;
    }
    return normalizeRoundTodoSnapshot(round.todo, safeRunId) !== null;
}

function resolveVisibleTodoRunId() {
    const activeRunId = String(navActiveRunId || '').trim();
    if (activeRunId && navRounds.some(round => round.run_id === activeRunId)) {
        return activeRunId;
    }
    const latestTodoRound = [...navRounds]
        .reverse()
        .find(round => normalizeRoundTodoSnapshot(round.todo, round.run_id) !== null);
    return String(latestTodoRound?.run_id || '').trim();
}

/* ---- Offset positioning ---- */

const DEFAULT_FROM_RIGHT = 16;
const DEFAULT_FROM_TOP = 12;

/** Convert stored offset -> viewport left/top, clamped to chat bounds. */
function applyOffset(nav) {
    const chatRect = getChatRect();
    const navW = loadCollapsedState() ? COLLAPSED_NAV_WIDTH : currentWidth;
    const navH = nav.offsetHeight;

    if (!currentOffset) {
        currentOffset = { fromRight: DEFAULT_FROM_RIGHT, fromTop: DEFAULT_FROM_TOP };
    }

    // Compute desired left/top from offset
    let left = chatRect.right - currentOffset.fromRight - navW;
    let top = chatRect.top + currentOffset.fromTop;

    // Clamp within chat container
    left = Math.max(chatRect.left, Math.min(left, chatRect.right - navW));
    top = Math.max(chatRect.top, Math.min(top, chatRect.bottom - navH));

    nav.style.left = left + 'px';
    nav.style.top = top + 'px';
    nav.style.right = 'auto';
}

function clampNavWidth(width) {
    const chatRect = getChatRect();
    const maxWithinChat = Math.max(MIN_NAV_WIDTH, Math.floor(chatRect.width - 24));
    return Math.max(MIN_NAV_WIDTH, Math.min(Math.min(MAX_NAV_WIDTH, maxWithinChat), Math.floor(width)));
}

function applyNavWidth(nav, width) {
    const nextWidth = clampNavWidth(width);
    currentWidth = nextWidth;
    nav.style.width = `${nextWidth}px`;
}

function applyCollapsedWidth(nav) {
    nav.style.width = `${COLLAPSED_NAV_WIDTH}px`;
}

function scheduleOffsetApply(nav) {
    if (scheduledOffsetFrame) {
        window.cancelAnimationFrame(scheduledOffsetFrame);
    }
    scheduledOffsetFrame = window.requestAnimationFrame(() => {
        scheduledOffsetFrame = 0;
        applyOffset(nav);
    });
}

/** Derive offset from current viewport position. */
function captureOffset(nav) {
    const chatRect = getChatRect();
    const navRect = nav.getBoundingClientRect();
    currentOffset = {
        fromRight: chatRect.right - navRect.right,
        fromTop: navRect.top - chatRect.top,
    };
}

/* ---- Drag support ---- */

function installDrag(nav) {
    let dragging = false;
    let startX = 0;
    let startY = 0;
    let origX = 0;
    let origY = 0;
    const DRAG_THRESHOLD = 4;
    let moved = false;

    const header = () => nav.querySelector('.round-nav-header');

    nav.addEventListener('pointerdown', (e) => {
        const hdr = header();
        if (!hdr || !hdr.contains(e.target)) return;
        if (e.target.closest('.round-nav-toggle')) return;
        if (e.target.closest('.round-nav-resizer')) return;

        dragging = true;
        moved = false;
        startX = e.clientX;
        startY = e.clientY;

        const rect = nav.getBoundingClientRect();
        origX = rect.left;
        origY = rect.top;

        nav.setPointerCapture(e.pointerId);
        e.preventDefault();
    });

    nav.addEventListener('pointermove', (e) => {
        if (!dragging) return;
        const dx = e.clientX - startX;
        const dy = e.clientY - startY;
        if (!moved && Math.abs(dx) < DRAG_THRESHOLD && Math.abs(dy) < DRAG_THRESHOLD) return;
        moved = true;

        nav.classList.add('dragging');

        const chatRect = getChatRect();
        let newLeft = origX + dx;
        let newTop = origY + dy;

        // Clamp within chat container
        newLeft = Math.max(chatRect.left, Math.min(newLeft, chatRect.right - nav.offsetWidth));
        newTop = Math.max(chatRect.top, Math.min(newTop, chatRect.bottom - nav.offsetHeight));

        nav.style.left = newLeft + 'px';
        nav.style.top = newTop + 'px';
        nav.style.right = 'auto';
    });

    nav.addEventListener('pointerup', (e) => {
        if (!dragging) return;
        dragging = false;
        nav.releasePointerCapture(e.pointerId);
        nav.classList.remove('dragging');

        if (moved) {
            captureOffset(nav);
            persistOffset();
        }
    });
}

function installResize(nav) {
    let resizing = false;
    let startX = 0;
    let startWidth = currentWidth;

    nav.addEventListener('pointerdown', (e) => {
        if (!e.target.closest('.round-nav-resizer')) return;
        resizing = true;
        startX = e.clientX;
        startWidth = currentWidth;
        nav.classList.add('resizing');
        nav.setPointerCapture(e.pointerId);
        e.preventDefault();
    });

    nav.addEventListener('pointermove', (e) => {
        if (!resizing) return;
        const deltaX = e.clientX - startX;
        applyNavWidth(nav, startWidth + deltaX);
        scheduleOffsetApply(nav);
    });

    nav.addEventListener('pointerup', (e) => {
        if (!resizing) return;
        resizing = false;
        nav.classList.remove('resizing');
        nav.releasePointerCapture(e.pointerId);
        persistWidth(currentWidth);
    });
}

/* ---- Resize watch: re-clamp when chat container changes ---- */

function installResizeWatch(nav) {
    const chat = document.querySelector('.chat-container');
    if (!chat) return;
    resizeObserver = new ResizeObserver(() => {
        if (nav.style.display === 'none') return;
        if (loadCollapsedState()) {
            applyCollapsedWidth(nav);
        } else {
            applyNavWidth(nav, currentWidth);
        }
        scheduleOffsetApply(nav);
    });
    resizeObserver.observe(chat);
}

/* ---- Persistence ---- */

function loadOffset() {
    try {
        const raw = localStorage.getItem(ROUND_NAV_POSITION_KEY);
        if (!raw) return null;
        const o = JSON.parse(raw);
        if (typeof o.fromRight === 'number' && typeof o.fromTop === 'number') return o;
    } catch { /* ignore */ }
    return null;
}

function loadWidth() {
    try {
        const raw = localStorage.getItem(ROUND_NAV_WIDTH_KEY);
        if (!raw || !/^\d+$/.test(raw)) {
            return DEFAULT_NAV_WIDTH;
        }
        return clampNavWidth(Number(raw));
    } catch {
        return DEFAULT_NAV_WIDTH;
    }
}

function persistOffset() {
    try {
        if (!currentOffset) return;
        localStorage.setItem(ROUND_NAV_POSITION_KEY, JSON.stringify(currentOffset));
    } catch { /* ignore */ }
}

function persistWidth(width) {
    try {
        localStorage.setItem(ROUND_NAV_WIDTH_KEY, String(clampNavWidth(width)));
    } catch { /* ignore */ }
}

/* ---- Collapse persistence ---- */

function loadCollapsedState() {
    try {
        return localStorage.getItem(ROUND_NAV_COLLAPSED_KEY) === '1';
    } catch {
        return false;
    }
}

function saveCollapsedState(collapsed) {
    try {
        localStorage.setItem(ROUND_NAV_COLLAPSED_KEY, collapsed ? '1' : '0');
    } catch {
        return;
    }
}
