/**
 * components/rounds/timeline.js
 * Session timeline rendering, scroll-sync, and paging orchestration.
 */
import { els } from '../../utils/dom.js';
import { state } from '../../core/state.js';
import { fetchRunTokenUsage } from '../../core/api.js';
import { clearAllPanels, setRoundPendingApprovals } from '../agentPanel.js';
import { clearAllStreamState, renderHistoricalMessageList } from '../messageRenderer.js';
import { renderRoundNavigator, setActiveRoundNav } from './navigator.js';
import { applyRoundPage, fetchInitialRoundsPage, fetchOlderRoundsPage } from './paging.js';
import { roundsState } from './state.js';
import { roundSectionId, esc } from './utils.js';
import { updateWorkflowByRound } from './workflow.js';

export let currentRounds = [];
export let currentRound = null;

export async function loadSessionRounds(sessionId) {
    try {
        const page = await fetchInitialRoundsPage(sessionId);
        applyRoundPage(page, { prepend: false });
        syncExportedState();
        renderSessionTimeline(roundsState.currentRounds, { preserveScroll: false });
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

    roundsState.currentRounds = [...roundsState.currentRounds, liveRound];
    syncExportedState();
    renderSessionTimeline(roundsState.currentRounds, { preserveScroll: false });

    const section = document.getElementById(roundSectionId('__live__'));
    if (section) {
        section.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}

export function selectRound(round) {
    if (!round) return;
    const section = document.getElementById(roundSectionId(round.run_id));
    if (!section) return;
    section.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

export function goBackToSessions() {
    // Legacy no-op: session list always visible now.
}

function renderSessionTimeline(rounds, opts = { preserveScroll: true }) {
    const container = els.chatMessages;
    if (!container) return;

    const oldScroll = container.scrollTop;
    container.innerHTML = '';

    clearAllPanels();
    clearAllStreamState();
    roundsState.activeRunId = null;
    roundsState.activeVisibility = 0;

    if (!rounds || rounds.length === 0) {
        roundsState.currentRound = null;
        syncExportedState();
        state.instanceRoleMap = {};
        state.roleInstanceMap = {};
        state.taskInstanceMap = {};
        roundsState.activeRunId = null;
        setRoundPendingApprovals('', [], {});
        renderRoundNavigator([], selectRound);
        updateWorkflowByRound(null);
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
        section.id = roundSectionId(round.run_id);

        const time = new Date(round.created_at).toLocaleString();
        const header = document.createElement('div');
        header.className = 'round-detail-header';
        header.innerHTML = `
            <div class="round-detail-label">Round ${index + 1}${round.run_id === '__live__' ? ' <span class="live-badge">LIVE</span>' : ''}</div>
            <div class="round-detail-time">${time}</div>
            <div class="round-detail-intent">
                <span class="intent-label">Intent:</span>
                <span class="intent-text">${esc(round.intent || 'No intent')}</span>
            </div>`;
        section.appendChild(header);

        const pendingCoordinatorApprovals = (round.pending_tool_approvals || []).filter(item => {
            const roleId = item?.role_id || '';
            return roleId === '' || roleId === 'coordinator_agent';
        });
        const streamSnapshot = round.pending_streams || null;
        const pendingCoordinatorStreamText = String(
            streamSnapshot?.coordinator_text || streamSnapshot?.coordinatorText || '',
        );
        const pendingCoordinatorInstanceId = String(
            streamSnapshot?.coordinator_instance_id || streamSnapshot?.coordinatorInstanceId || '',
        );

        if (round.coordinator_messages?.length > 0) {
            renderHistoricalMessageList(section, round.coordinator_messages, {
                pendingToolApprovals: pendingCoordinatorApprovals,
                runId: round.run_id,
                pendingStreamText: pendingCoordinatorStreamText,
                pendingStreamRoleId: 'coordinator_agent',
                pendingStreamInstanceId: pendingCoordinatorInstanceId,
            });
        } else if (pendingCoordinatorApprovals.length > 0 || pendingCoordinatorStreamText.trim()) {
            renderHistoricalMessageList(section, [], {
                pendingToolApprovals: pendingCoordinatorApprovals,
                runId: round.run_id,
                pendingStreamText: pendingCoordinatorStreamText,
                pendingStreamRoleId: 'coordinator_agent',
                pendingStreamInstanceId: pendingCoordinatorInstanceId,
            });
        } else if (round.run_id !== '__live__') {
            const empty = document.createElement('div');
            empty.className = 'panel-empty';
            empty.textContent = 'No coordinator messages in this round.';
            section.appendChild(empty);
        }

        container.appendChild(section);

        // Lazily load token usage for completed (non-live) rounds
        if (round.run_id !== '__live__' && state.currentSessionId) {
            const headerEl = header;
            void fetchRunTokenUsage(state.currentSessionId, round.run_id).then(usage => {
                if (!usage || usage.total_tokens === 0) return;
                const fmt = n => n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
                const pill = document.createElement('div');
                pill.className = 'round-token-summary';
                pill.title = `Input: ${usage.total_input_tokens} | Output: ${usage.total_output_tokens} | Requests: ${usage.total_requests}`;
                pill.innerHTML = `
                    <span class="token-in">↑${fmt(usage.total_input_tokens)}</span>
                    <span class="token-out">↓${fmt(usage.total_output_tokens)}</span>
                    ${usage.total_tool_calls > 0 ? `<span class="token-tools">🔧${usage.total_tool_calls}</span>` : ''}
                `;
                headerEl.appendChild(pill);
            });
        }
    });

    renderRoundNavigator(rounds, selectRound);
    bindScrollSync();

    if (opts.preserveScroll) {
        container.scrollTop = oldScroll;
    } else {
        container.scrollTop = container.scrollHeight;
    }

    syncActiveRoundFromScroll();
}

function bindScrollSync() {
    if (roundsState.scrollBound || !els.chatMessages) return;
    els.chatMessages.addEventListener('scroll', syncActiveRoundFromScroll, { passive: true });
    roundsState.scrollBound = true;
}

function syncActiveRoundFromScroll() {
    const container = els.chatMessages;
    if (!container) return;

    const sections = Array.from(container.querySelectorAll('.session-round-section'));
    if (sections.length === 0) return;

    const atTop = container.scrollTop <= 2;
    const atBottom = container.scrollTop + container.clientHeight >= container.scrollHeight - 2;
    if (atTop) {
        activateRoundSection(sections[0], Number.POSITIVE_INFINITY);
        void loadOlderRounds();
        return;
    }
    if (atBottom) {
        activateRoundSection(sections[sections.length - 1], Number.POSITIVE_INFINITY);
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
    activateRoundSection(best, bestVisible);
}

function activateRoundSection(section, visibleScore) {
    const runId = section?.dataset?.runId || null;
    if (!runId) return;

    if (
        roundsState.activeRunId &&
        runId !== roundsState.activeRunId &&
        visibleScore < roundsState.activeVisibility * 1.08
    ) {
        return;
    }
    if (runId === roundsState.activeRunId) {
        roundsState.activeVisibility = visibleScore;
        return;
    }

    roundsState.activeRunId = runId;
    roundsState.activeVisibility = visibleScore;
    roundsState.currentRound = roundsState.currentRounds.find(r => r.run_id === runId) || null;
    const pendingApprovals = roundsState.currentRound?.pending_tool_approvals || [];
    const snapshot = roundsState.currentRound?.pending_streams || null;
    const pendingStreamsByInstance = snapshot?.by_instance || snapshot?.byInstance || {};
    setRoundPendingApprovals(runId, pendingApprovals, pendingStreamsByInstance);
    syncExportedState();

    setActiveRoundNav(runId);
    updateWorkflowByRound(roundsState.currentRound);
}

async function loadOlderRounds() {
    if (!roundsState.paging.hasMore || roundsState.paging.loading || !state.currentSessionId) return;

    const container = els.chatMessages;
    if (!container) return;

    roundsState.paging.loading = true;
    const oldHeight = container.scrollHeight;
    const oldTop = container.scrollTop;
    try {
        const page = await fetchOlderRoundsPage();
        if (!page) {
            roundsState.paging.loading = false;
            return;
        }
        applyRoundPage(page, { prepend: true });
        syncExportedState();
        renderSessionTimeline(roundsState.currentRounds, { preserveScroll: true });
        const newHeight = container.scrollHeight;
        container.scrollTop = newHeight - oldHeight + oldTop;
    } catch (e) {
        console.error('Failed loading older rounds', e);
        roundsState.paging.loading = false;
    }
}

function syncExportedState() {
    currentRounds = roundsState.currentRounds;
    currentRound = roundsState.currentRound;
}
