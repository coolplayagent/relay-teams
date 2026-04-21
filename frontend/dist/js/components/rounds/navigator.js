/**
 * components/rounds/navigator.js
 * Inline round navigator rendered inside the chat flow.
 */
import { esc, roundStateLabel, roundStateTone } from './utils.js';
import { formatMessage, t } from '../../utils/i18n.js';
import { normalizeRoundTodoSnapshot } from './todo.js';

let navRounds = [];
let navActiveRunId = null;
let navOnSelectRound = null;
const ROUND_NAV_COLLAPSED_KEY = 'agent_teams_round_nav_collapsed';
const collapsedTodoRunIds = new Set();
const expandedTodoRunIds = new Set();

export function renderRoundNavigator(rounds, onSelectRound, options = {}) {
    navRounds = Array.isArray(rounds) ? rounds : [];
    navOnSelectRound = onSelectRound;
    if (Object.prototype.hasOwnProperty.call(options, 'activeRunId')) {
        navActiveRunId = String(options.activeRunId || '').trim() || null;
    }

    const host = getChatMessagesHost();
    if (!host) {
        return;
    }

    let nav = host.querySelector('#round-nav-float');
    if (navRounds.length === 0) {
        nav?.remove();
        return;
    }
    if (!nav) {
        nav = document.createElement('section');
        nav.id = 'round-nav-float';
        nav.className = 'round-nav-float round-nav-inline';
    }

    renderNavigatorDom(nav);
    if (host.firstElementChild !== nav) {
        host.prepend(nav);
    }
}

export function hideRoundNavigator() {
    getChatMessagesHost()?.querySelector?.('#round-nav-float')?.remove?.();
}

export function setActiveRoundNav(runId) {
    navActiveRunId = String(runId || '').trim() || null;
    const nav = getChatMessagesHost()?.querySelector?.('#round-nav-float');
    if (!nav || navRounds.length === 0) {
        return;
    }
    renderNavigatorDom(nav);
    nav.querySelector('.round-nav-item.active')?.scrollIntoView?.({ block: 'nearest' });
}

function renderNavigatorDom(nav) {
    const isCollapsed = loadCollapsedState();
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
            saveCollapsedState(!loadCollapsedState());
            renderNavigatorDom(nav);
        };
    }

    const list = nav.querySelector('.round-nav-list');
    if (!list) {
        return;
    }

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
            if (navOnSelectRound) {
                navOnSelectRound(round);
            }
        };
        node.appendChild(item);

        const todoBranch = buildRoundNavTodoBranch(round, { isCollapsed });
        if (todoBranch) {
            node.classList.add('has-todo');
            node.appendChild(todoBranch);
        }
        list.appendChild(node);
    });
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

function loadCollapsedState() {
    try {
        return window.localStorage?.getItem(ROUND_NAV_COLLAPSED_KEY) === '1';
    } catch {
        return false;
    }
}

function saveCollapsedState(collapsed) {
    try {
        if (collapsed) {
            window.localStorage?.setItem(ROUND_NAV_COLLAPSED_KEY, '1');
            return;
        }
        window.localStorage?.removeItem(ROUND_NAV_COLLAPSED_KEY);
    } catch {
        return;
    }
}

function getChatMessagesHost() {
    return document?.getElementById?.('chat-messages')
        || document?.querySelector?.('#chat-messages')
        || document?.body
        || null;
}
