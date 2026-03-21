/**
 * components/agentPanel/index.js
 * Public API for session-level subagent panels and gate cards.
 */
import { resolveGate } from '../../core/api.js';
import { state } from '../../core/state.js';
import { t } from '../../utils/i18n.js';
import { parseMarkdown } from '../../utils/markdown.js';
import { closeDrawerUi, getDrawer, openDrawerUi } from './dom.js';
import { schedulePanelContextPreview } from '../contextIndicators.js';
import { loadAgentHistory, syncAgentPanelState } from './history.js';
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
    syncAgentPanelState(instanceId, roleId);
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
    _syncRailHeader(instanceId, roleId, panel);
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
    _resetRailHeader();
}

function _syncRailHeader(instanceId, roleId, panel) {
    const nameEl = document.getElementById('subagent-rail-agent-name');
    const idEl = document.getElementById('subagent-rail-agent-id');
    const railReflect = document.getElementById('subagent-rail-reflect');
    const railStop = document.getElementById('subagent-rail-stop');

    const friendlyRole = roleId
        ? roleId.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())
        : instanceId.slice(0, 8);

    if (nameEl) nameEl.textContent = friendlyRole;
    if (idEl) idEl.textContent = instanceId.slice(0, 8);

    const hiddenReflect = panel.panelEl.querySelector('.agent-panel-refresh-reflection');
    const hiddenStop = panel.panelEl.querySelector('.agent-panel-stop');

    if (railReflect) {
        railReflect.hidden = false;
        railReflect.onclick = hiddenReflect ? () => hiddenReflect.click() : null;
    }
    if (railStop) {
        railStop.hidden = false;
        railStop.onclick = hiddenStop ? () => hiddenStop.click() : null;
    }
}

function _resetRailHeader() {
    const nameEl = document.getElementById('subagent-rail-agent-name');
    const idEl = document.getElementById('subagent-rail-agent-id');
    const railTokenBadge = document.getElementById('subagent-rail-token-badge');
    const railReflect = document.getElementById('subagent-rail-reflect');
    const railStop = document.getElementById('subagent-rail-stop');

    if (nameEl) nameEl.textContent = t('subagent.header');
    if (idEl) idEl.textContent = '';
    if (railTokenBadge) railTokenBadge.innerHTML = '';
    if (railReflect) { railReflect.hidden = true; railReflect.onclick = null; }
    if (railStop) { railStop.hidden = true; railStop.onclick = null; }
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
        <div class="gate-header">${t('subagent.gate_header')}</div>
        <div class="gate-summary">${parseMarkdown(summary || '')}</div>
        <div class="gate-role">${t('subagent.gate_role')} <strong>${role_id || roleId || ''}</strong></div>
        <div class="gate-actions">
            <button class="gate-approve-btn">${t('subagent.gate_approve')}</button>
            <button class="gate-revise-btn">${t('subagent.gate_revise')}</button>
        </div>
        <div class="gate-feedback-area" style="display:none">
            <textarea class="gate-feedback-input" placeholder="${t('subagent.gate_feedback_placeholder')}" rows="3"></textarea>
            <button class="gate-submit-revise-btn">${t('subagent.gate_submit')}</button>
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
