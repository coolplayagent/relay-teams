/**
 * components/agentPanel/index.js
 * Public API for session-level subagent panels and gate cards.
 */
import { resolveGate } from '../../core/api.js';
import { state } from '../../core/state.js';
import { parseMarkdown } from '../../utils/markdown.js';
import { closeDrawerUi, getDrawer, openDrawerUi } from './dom.js';
import { schedulePanelContextPreview } from '../contextIndicators.js';
import { loadAgentHistory } from './history.js';
import { createPanel } from './panelFactory.js';
import {
    clearPanels,
    forEachPanel,
    getPanel,
    getPanels,
    getPendingApprovalsForPanel,
    getActiveRoundRunId,
    setActiveRoundContext,
    setActiveInstanceId,
    setPanel,
} from './state.js';
import { getInstanceStreamOverlay } from '../messageRenderer.js';

function ensurePanel(instanceId, roleId) {
    let panel = getPanel(instanceId);
    if (!panel) {
        panel = createPanel(instanceId, roleId, closeAgentPanel);
        if (!panel) return null;
        setPanel(instanceId, panel);
    }
    return panel;
}

export function openAgentPanel(
    instanceId,
    roleId,
    { reveal = false, forceRefresh = false } = {},
) {
    const drawer = getDrawer();
    if (!drawer) return;

    forEachPanel((panelRecord, currentId) => {
        panelRecord.panelEl.style.display = currentId === instanceId ? 'flex' : 'none';
    });

    const existing = getPanel(instanceId);
    const panel = ensurePanel(instanceId, roleId);
    if (!panel) return;
    const activeRunId = state.activeRunId || getActiveRoundRunId();
    const shouldRefreshHistory = !!(
        state.currentSessionId
        && (
            forceRefresh
            || !existing
            || panel.loadedSessionId !== (state.currentSessionId || '')
            || panel.loadedRunId !== (activeRunId || '')
            || !state.isGenerating
        )
    );
    if (shouldRefreshHistory) {
        void loadAgentHistory(instanceId, roleId);
    } else if (existing && state.currentSessionId) {
        const approvals = getPendingApprovalsForPanel(instanceId, roleId);
        const overlay = getInstanceStreamOverlay(activeRunId, instanceId);
        if (approvals.length > 0 || overlay) {
            void loadAgentHistory(instanceId, roleId);
        }
    }

    panel.panelEl.style.display = 'flex';
    setActiveInstanceId(instanceId);
    schedulePanelContextPreview(instanceId, { immediate: true });
    state.selectedRoleId = roleId || state.selectedRoleId;
    const roleSelect = document.getElementById('subagent-role-select');
    if (roleSelect && roleId) {
        roleSelect.value = roleId;
    }
    if (reveal) {
        openDrawerUi();
    }
}

export function closeAgentPanel() {
    closeDrawerUi();
    setActiveInstanceId(null);
}

export function clearAllPanels() {
    if (!getDrawer()) return;
    forEachPanel(panel => panel.panelEl.remove());
    clearPanels();
    setActiveRoundContext('', []);
    setActiveInstanceId(null);
}

export function getPanelScrollContainer(instanceId, roleId) {
    const panel = ensurePanel(instanceId, roleId);
    return panel ? panel.scrollEl : null;
}

export function showGateCard(instanceId, roleId, gatePayload) {
    openAgentPanel(instanceId, roleId, { reveal: true, forceRefresh: false });
    const panel = getPanel(instanceId);
    if (!panel) return;

    panel.scrollEl.querySelectorAll('.gate-card').forEach(card => card.remove());
    const { run_id, task_id, summary, role_id } = gatePayload;

    const card = document.createElement('div');
    card.className = 'gate-card';
    card.dataset.taskId = task_id;
    card.innerHTML = `
        <div class="gate-header">Sub-task completed - please confirm</div>
        <div class="gate-summary">${parseMarkdown(summary || '')}</div>
        <div class="gate-role">Role: <strong>${role_id || roleId || ''}</strong></div>
        <div class="gate-actions">
            <button class="gate-approve-btn">Approve</button>
            <button class="gate-revise-btn">Request Revision</button>
        </div>
        <div class="gate-feedback-area" style="display:none">
            <textarea class="gate-feedback-input" placeholder="Please describe required changes..." rows="3"></textarea>
            <button class="gate-submit-revise-btn">Submit</button>
        </div>
    `;

    async function doResolve(action, feedback = '') {
        card.querySelectorAll('button').forEach(button => {
            button.disabled = true;
        });
        try {
            await resolveGate(run_id || state.activeRunId, task_id, action, feedback);
        } catch (e) {
            card.querySelectorAll('button').forEach(button => {
                button.disabled = false;
            });
        }
    }

    const approveBtn = card.querySelector('.gate-approve-btn');
    const reviseBtn = card.querySelector('.gate-revise-btn');
    const submitBtn = card.querySelector('.gate-submit-revise-btn');

    if (approveBtn) approveBtn.onclick = () => doResolve('approve');
    if (reviseBtn) {
        reviseBtn.onclick = () => {
            const area = card.querySelector('.gate-feedback-area');
            area.style.display = area.style.display === 'none' ? 'block' : 'none';
        };
    }
    if (submitBtn) {
        submitBtn.onclick = () => {
            const feedback = card.querySelector('.gate-feedback-input').value.trim();
            void doResolve('revise', feedback);
        };
    }

    panel.scrollEl.appendChild(card);
    panel.scrollEl.scrollTop = panel.scrollEl.scrollHeight;
}

export function removeGateCard(instanceId, taskId) {
    const panel = getPanel(instanceId);
    if (!panel) return;
    const el = panel.scrollEl.querySelector(`.gate-card[data-task-id="${taskId}"]`);
    if (el) el.remove();
}

export function setRoundPendingApprovals(runId, pendingApprovals) {
    setActiveRoundContext(runId, pendingApprovals);
}

export { getActiveInstanceId, getActiveRoundRunId, getPanels } from './state.js';
export { loadAgentHistory } from './history.js';
