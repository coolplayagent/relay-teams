/**
 * components/rounds/navigator.js
 * Floating round navigator rendering and active-state sync.
 */
import { esc, roundStateLabel, roundStateTone } from './utils.js';
import { t } from '../../utils/i18n.js';

let navRounds = [];
let navActiveRunId = null;
let navOnSelectRound = null;
const ROUND_NAV_COLLAPSED_KEY = 'agent_teams_round_nav_collapsed';
const ROUND_NAV_POSITION_KEY = 'agent_teams_round_nav_position';

/** Persistent offset relative to chat container: { fromRight, fromTop }. */
let currentOffset = null;
let resizeObserver = null;

export function renderRoundNavigator(rounds, onSelectRound) {
    navRounds = Array.isArray(rounds) ? rounds : [];
    navOnSelectRound = onSelectRound;

    let nav = document.getElementById('round-nav-float');
    if (!nav) {
        nav = document.createElement('div');
        nav.id = 'round-nav-float';
        nav.className = 'round-nav-float';
        document.body.appendChild(nav);
        currentOffset = loadOffset();
        installDrag(nav);
        installResizeWatch(nav);
    }

    if (navRounds.length === 0) {
        nav.style.display = 'none';
        nav.innerHTML = '';
        return;
    }

    renderNavigatorDom(nav);
    applyOffset(nav);
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

    nav.querySelectorAll('.round-nav-item').forEach(el => {
        el.classList.toggle('active', el.dataset.runId === runId);
    });
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

function renderNavigatorDom(nav) {
    const isCollapsed = loadCollapsedState();
    nav.style.display = 'flex';
    nav.classList.toggle('collapsed', isCollapsed);
    nav.innerHTML = `
        <div class="round-nav-header">
            <div class="round-nav-title">Rounds</div>
            <button type="button" class="round-nav-toggle" aria-label="${isCollapsed ? 'Expand rounds' : 'Collapse rounds'}">
                ${isCollapsed ? 'Show' : 'Hide'}
            </button>
        </div>
        <div class="round-nav-list"></div>
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
            const newWidth = nav.offsetWidth;
            const oldWidth = navRect.width;
            const widthDelta = newWidth - oldWidth;
            currentOffset = { fromRight, fromTop: currentOffset ? currentOffset.fromTop : (navRect.top - chatRect.top) };
            // fromRight stays same, so left shifts by widthDelta automatically
            applyOffset(nav);
            persistOffset();
        };
    }

    const list = nav.querySelector('.round-nav-list');
    if (!list) return;
    navRounds.forEach((round, idx) => {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'round-nav-item';
        item.dataset.runId = round.run_id;
        item.title = String(round.intent || 'No intent');
        if (navActiveRunId && navActiveRunId === round.run_id) {
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
        list.appendChild(item);
    });
}

/* ---- Offset positioning ---- */

const DEFAULT_FROM_RIGHT = 16;
const DEFAULT_FROM_TOP = 12;

/** Convert stored offset -> viewport left/top, clamped to chat bounds. */
function applyOffset(nav) {
    const chatRect = getChatRect();
    const navW = nav.offsetWidth;
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

/* ---- Resize watch: re-clamp when chat container changes ---- */

function installResizeWatch(nav) {
    const chat = document.querySelector('.chat-container');
    if (!chat) return;
    resizeObserver = new ResizeObserver(() => {
        if (nav.style.display === 'none') return;
        applyOffset(nav);
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

function persistOffset() {
    try {
        if (!currentOffset) return;
        localStorage.setItem(ROUND_NAV_POSITION_KEY, JSON.stringify(currentOffset));
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
