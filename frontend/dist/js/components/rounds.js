/**
 * components/rounds.js
 * Session timeline + floating round navigator.
 */
import { els } from '../utils/dom.js';
import { state } from '../core/state.js';
import { fetchSessionRounds } from '../core/api.js';
import { renderNativeDAG } from './workflow.js';
import { renderHistoricalMessageList, clearAllStreamState } from './messageRenderer.js';
import { clearAllPanels } from './agentPanel.js';

export let currentRounds = [];
export let currentRound = null;

let _scrollBound = false;
let _activeRunId = null;
let _activeVisibility = 0;
const ROUND_PAGE_SIZE = 8;
let _roundPaging = {
    hasMore: false,
    nextCursor: null,
    loading: false,
};

export async function loadSessionRounds(sessionId) {
    try {
        const page = await fetchSessionRounds(sessionId, { limit: ROUND_PAGE_SIZE });
        _applyRoundPage(page, { prepend: false });
        renderSessionTimeline(currentRounds, { preserveScroll: false });
    } catch (e) {
        console.error('Failed loading rounds', e);
    }
}

export function createLiveRound(intentText) {
    const liveRound = {
        run_id: '__live__',
        created_at: new Date().toISOString(),
        intent: intentText,
        coordinator_messages: [],
        workflows: [],
        instance_role_map: {},
        role_instance_map: {},
    };

    currentRounds = [...currentRounds, liveRound];
    renderSessionTimeline(currentRounds, { preserveScroll: false });

    const section = document.getElementById(_roundSectionId('__live__'));
    if (section) {
        section.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}

export function selectRound(round) {
    if (!round) return;
    const section = document.getElementById(_roundSectionId(round.run_id));
    if (!section) return;
    section.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderSessionTimeline(rounds, opts = { preserveScroll: true }) {
    const container = els.chatMessages;
    if (!container) return;

    const oldScroll = container.scrollTop;
    container.innerHTML = '';

    clearAllPanels();
    clearAllStreamState();
    _activeRunId = null;
    _activeVisibility = 0;

    if (!rounds || rounds.length === 0) {
        currentRound = null;
        state.instanceRoleMap = {};
        _activeRunId = null;
        _renderRoundNavigator([]);
        _updateWorkflowByRound(null);
        container.innerHTML = `
            <div class="system-intro">
                <div class="intro-icon">🛸</div>
                <h1>Welcome to Agent Teams</h1>
                <p>Select a session or create a new one to begin.</p>
            </div>`;
        return;
    }

    rounds.forEach((round, index) => {
        const section = document.createElement('section');
        section.className = 'session-round-section';
        section.dataset.runId = round.run_id;
        section.id = _roundSectionId(round.run_id);

        const time = new Date(round.created_at).toLocaleString();
        const header = document.createElement('div');
        header.className = 'round-detail-header';
        header.innerHTML = `
            <div class="round-detail-label">Round ${index + 1}${round.run_id === '__live__' ? ' <span class="live-badge">LIVE</span>' : ''}</div>
            <div class="round-detail-time">${time}</div>
            <div class="round-detail-intent">
                <span class="intent-label">Intent:</span>
                <span class="intent-text">${_esc(round.intent || 'No intent')}</span>
            </div>`;
        section.appendChild(header);

        const pendingCoordinatorApprovals = (round.pending_tool_approvals || []).filter(item => {
            const roleId = item?.role_id || '';
            return roleId === '' || roleId === 'coordinator_agent';
        });

        if (round.coordinator_messages?.length > 0) {
            renderHistoricalMessageList(section, round.coordinator_messages, {
                pendingToolApprovals: pendingCoordinatorApprovals,
            });
        } else if (pendingCoordinatorApprovals.length > 0) {
            renderHistoricalMessageList(section, [], {
                pendingToolApprovals: pendingCoordinatorApprovals,
            });
        } else if (round.run_id !== '__live__') {
            const empty = document.createElement('div');
            empty.className = 'panel-empty';
            empty.textContent = 'No coordinator messages in this round.';
            section.appendChild(empty);
        }

        container.appendChild(section);
    });

    _renderRoundNavigator(rounds);
    _bindScrollSync();

    if (opts.preserveScroll) {
        container.scrollTop = oldScroll;
    } else {
        container.scrollTop = container.scrollHeight;
    }

    _syncActiveRoundFromScroll();
}

function _bindScrollSync() {
    if (_scrollBound || !els.chatMessages) return;
    els.chatMessages.addEventListener('scroll', _syncActiveRoundFromScroll, { passive: true });
    _scrollBound = true;
}

function _syncActiveRoundFromScroll() {
    const container = els.chatMessages;
    if (!container) return;

    const sections = Array.from(container.querySelectorAll('.session-round-section'));
    if (sections.length === 0) return;

    const atTop = container.scrollTop <= 2;
    const atBottom = container.scrollTop + container.clientHeight >= container.scrollHeight - 2;
    if (atTop) {
        _activateRoundSection(sections[0], Number.POSITIVE_INFINITY);
        void _loadOlderRounds();
        return;
    }
    if (atBottom) {
        _activateRoundSection(sections[sections.length - 1], Number.POSITIVE_INFINITY);
        return;
    }

    const containerRect = container.getBoundingClientRect();
    let best = null;
    let bestVisible = -1;

    sections.forEach(sec => {
        const rect = sec.getBoundingClientRect();
        const visibleTop = Math.max(rect.top, containerRect.top);
        const visibleBottom = Math.min(rect.bottom, containerRect.bottom);
        const visible = Math.max(0, visibleBottom - visibleTop);
        if (visible > bestVisible) {
            bestVisible = visible;
            best = sec;
        }
    });
    _activateRoundSection(best, bestVisible);
}

function _activateRoundSection(section, visibleScore) {
    const runId = section?.dataset?.runId || null;
    if (!runId) return;

    // Hysteresis: avoid rapid toggling when two rounds have similar visibility.
    if (
        _activeRunId &&
        runId !== _activeRunId &&
        visibleScore < _activeVisibility * 1.08
    ) {
        return;
    }
    if (runId === _activeRunId) {
        _activeVisibility = visibleScore;
        return;
    }

    _activeRunId = runId;
    _activeVisibility = visibleScore;
    const round = currentRounds.find(r => r.run_id === runId) || null;
    currentRound = round;

    document.querySelectorAll('.round-nav-item').forEach(el => {
        el.classList.toggle('active', el.dataset.runId === runId);
    });

    _updateWorkflowByRound(round);
}

function _updateWorkflowByRound(round) {
    if (!els.workflowCount || !els.workflowCollapsed || !els.workflowPanel) return;
    const canvas = document.getElementById('workflow-canvas');

    if (!round) {
        els.workflowCount.textContent = '0';
        els.workflowCollapsed.style.display = 'none';
        els.workflowPanel.style.display = 'none';
        state.instanceRoleMap = {};
        if (canvas) canvas.innerHTML = '';
        return;
    }

    state.instanceRoleMap = round.instance_role_map || {};

    const workflowCount = round.workflows?.length ?? 0;
    els.workflowCount.textContent = String(workflowCount);
    // Keep panel height stable to avoid flicker loops when switching rounds.
    els.workflowPanel.style.display = 'flex';
    els.workflowCollapsed.style.display = 'none';
    if (workflowCount > 0) {
        renderNativeDAG(round.workflows[workflowCount - 1]);
    } else if (canvas) {
        canvas.innerHTML = '<div class="panel-empty">No workflow graph for this round.</div>';
    }
}

function _renderRoundNavigator(rounds) {
    let nav = document.getElementById('round-nav-float');
    if (!nav) {
        nav = document.createElement('div');
        nav.id = 'round-nav-float';
        nav.className = 'round-nav-float';
        const chatContainer = document.querySelector('.chat-container');
        if (chatContainer) chatContainer.appendChild(nav);
    }

    if (!rounds || rounds.length === 0) {
        nav.style.display = 'none';
        nav.innerHTML = '';
        if (els.workflowPanel) els.workflowPanel.style.display = 'none';
        if (els.workflowCollapsed) els.workflowCollapsed.style.display = 'none';
        return;
    }

    nav.style.display = 'flex';
    nav.innerHTML = `
        <div class="round-nav-title">Rounds</div>
        <div class="round-nav-list"></div>
    `;

    const list = nav.querySelector('.round-nav-list');
    rounds.forEach((round, idx) => {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'round-nav-item';
        item.dataset.runId = round.run_id;
        item.innerHTML = `
            <span class="idx">${idx + 1}</span>
            <span class="txt">${_esc(round.intent || 'No intent')}</span>
        `;
        item.onclick = () => selectRound(round);
        list.appendChild(item);
    });
}

export function toggleWorkflow() {
    if (!els.workflowPanel || !els.workflowCollapsed) return;
    const isHidden = els.workflowPanel.style.display === 'none' || els.workflowPanel.style.display === '';
    if (isHidden) {
        els.workflowPanel.style.display = 'flex';
        els.workflowCollapsed.style.display = 'none';
        if (currentRound?.workflows?.length > 0) {
            renderNativeDAG(currentRound.workflows[currentRound.workflows.length - 1]);
        }
    } else {
        els.workflowPanel.style.display = 'none';
        els.workflowCollapsed.style.display = 'block';
    }
}

export function goBackToSessions() {
    // Legacy no-op: session list always visible now.
}

function _roundSectionId(runId) {
    return `round-${String(runId).replace(/[^a-zA-Z0-9_-]/g, '_')}`;
}

function _esc(text) {
    if (!text) return '';
    const d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
}

function _applyRoundPage(page, { prepend }) {
    const rawItems = Array.isArray(page?.items) ? page.items : [];
    const sortedItems = rawItems.slice().sort((a, b) =>
        new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
    );

    if (!prepend) {
        currentRounds = sortedItems;
    } else if (sortedItems.length > 0) {
        const existing = new Set(currentRounds.map(r => r.run_id));
        const toAdd = sortedItems.filter(r => !existing.has(r.run_id));
        currentRounds = [...toAdd, ...currentRounds];
    }

    _roundPaging = {
        hasMore: !!page?.has_more,
        nextCursor: page?.next_cursor || null,
        loading: false,
    };
}

async function _loadOlderRounds() {
    if (!_roundPaging.hasMore || _roundPaging.loading || !state.currentSessionId) return;

    const container = els.chatMessages;
    if (!container) return;

    _roundPaging.loading = true;
    const oldHeight = container.scrollHeight;
    const oldTop = container.scrollTop;
    try {
        const page = await fetchSessionRounds(state.currentSessionId, {
            limit: ROUND_PAGE_SIZE,
            cursorRunId: _roundPaging.nextCursor,
        });
        _applyRoundPage(page, { prepend: true });
        renderSessionTimeline(currentRounds, { preserveScroll: true });
        const newHeight = container.scrollHeight;
        container.scrollTop = newHeight - oldHeight + oldTop;
    } catch (e) {
        console.error('Failed loading older rounds', e);
        _roundPaging.loading = false;
    }
}
