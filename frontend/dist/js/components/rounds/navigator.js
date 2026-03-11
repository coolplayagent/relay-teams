/**
 * components/rounds/navigator.js
 * Floating round navigator rendering and active-state sync.
 */
import { esc, roundStateLabel, roundStateTone } from './utils.js';

let navRounds = [];
let navActiveRunId = null;
let navOnSelectRound = null;
const ROUND_NAV_COLLAPSED_KEY = 'agent_teams_round_nav_collapsed';

export function renderRoundNavigator(rounds, onSelectRound) {
    navRounds = Array.isArray(rounds) ? rounds : [];
    navOnSelectRound = onSelectRound;

    let nav = document.getElementById('round-nav-float');
    if (!nav) {
        nav = document.createElement('div');
        nav.id = 'round-nav-float';
        nav.className = 'round-nav-float';
        const chatContainer = document.querySelector('.chat-container');
        if (chatContainer) chatContainer.appendChild(nav);
    }

    if (navRounds.length === 0) {
        nav.style.display = 'none';
        nav.innerHTML = '';
        return;
    }

    renderNavigatorDom(nav);
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
            const next = !loadCollapsedState();
            saveCollapsedState(next);
            renderNavigatorDom(nav);
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
                <span class="txt">${esc(round.intent || 'No intent')}</span>
                <span class="round-nav-meta">
                    ${stateLabel ? `<span class="round-nav-state round-nav-state-${roundStateTone(round)}">${esc(stateLabel)}</span>` : ''}
                    ${approvalCount > 0 ? `<span class="round-nav-state round-nav-state-warning">${approvalCount} approval${approvalCount === 1 ? '' : 's'}</span>` : ''}
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
