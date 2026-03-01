/**
 * components/agentPanel.js
 * Right-drawer panel for a single subagent: shows message history,
 * live streaming output, gate confirmation cards, and an inject input.
 */
import { state } from '../core/state.js';
import { fetchAgentMessages } from '../core/api.js';
import { renderHistoricalMessageList, getOrCreateStreamBlock, appendStreamChunk, finalizeStream } from './messageRenderer.js';
import { resolveGate } from '../core/api.js';
import { parseMarkdown } from '../utils/markdown.js';

// ─── Panel registry ─────────────────────────────────────────────────────────
// Map<instanceId, { panelEl, scrollEl, instanceId, roleId }>
const _panels = new Map();
let _activeInstanceId = null;

// ─── Drawer container ────────────────────────────────────────────────────────
function _getDrawer() {
    return document.getElementById('agent-drawer');
}

// ─── Public API ──────────────────────────────────────────────────────────────

/**
 * Open (or switch to) the drawer for a given instance.
 * If the instance panel doesn't exist yet, create it.
 */
export function openAgentPanel(instanceId, roleId) {
    const drawer = _getDrawer();
    if (!drawer) return;

    // Hide any currently visible panel
    _panels.forEach((p, id) => {
        if (id !== instanceId) p.panelEl.style.display = 'none';
    });

    let panel = _panels.get(instanceId);
    if (!panel) {
        panel = _createPanel(instanceId, roleId);
        _panels.set(instanceId, panel);
        // Load history if session exists
        if (state.currentSessionId) {
            loadAgentHistory(instanceId, roleId);
        }
    }

    panel.panelEl.style.display = 'flex';
    _activeInstanceId = instanceId;

    // Open drawer
    drawer.classList.add('open');

    // Highlight DAG node
    _highlightNode(roleId, instanceId);
}

export function closeAgentPanel() {
    const drawer = _getDrawer();
    if (drawer) drawer.classList.remove('open');
    _activeInstanceId = null;
    document.querySelectorAll('.dag-node').forEach(n => n.classList.remove('active-tab'));
}

export function clearAllPanels() {
    const drawer = _getDrawer();
    if (!drawer) return;
    _panels.forEach(p => p.panelEl.remove());
    _panels.clear();
    _activeInstanceId = null;
    drawer.classList.remove('open');
}

/** Load historical messages from API into the panel */
export async function loadAgentHistory(instanceId, roleId) {
    const panel = _panels.get(instanceId);
    if (!panel) return;
    const scrollEl = panel.scrollEl;
    try {
        scrollEl.innerHTML = `<div class="panel-loading">Loading history…</div>`;
        const messages = await fetchAgentMessages(state.currentSessionId, instanceId);
        scrollEl.innerHTML = '';
        if (messages.length === 0) {
            scrollEl.innerHTML = `<div class="panel-empty">No messages yet.</div>`;
        } else {
            renderHistoricalMessageList(scrollEl, messages);
        }
    } catch (e) {
        scrollEl.innerHTML = `<div class="panel-empty" style="color:var(--danger)">Failed to load history.</div>`;
    }
}

/**
 * Get the scroll container for a panel (used by eventRouter for streaming).
 * Creates the panel if it doesn't exist yet.
 */
export function getPanelScrollContainer(instanceId, roleId) {
    let panel = _panels.get(instanceId);
    if (!panel) {
        panel = _createPanel(instanceId, roleId);
        _panels.set(instanceId, panel);
    }
    return panel.scrollEl;
}

/**
 * Show the gate confirmation card inside the subagent's panel.
 * If panel isn't open, auto-open it.
 */
export function showGateCard(instanceId, roleId, gatePayload) {
    openAgentPanel(instanceId, roleId);
    const panel = _panels.get(instanceId);
    if (!panel) return;

    // Remove any existing gate card
    panel.scrollEl.querySelectorAll('.gate-card').forEach(c => c.remove());

    const { session_id, run_id, task_id, summary, role_id } = gatePayload;

    const card = document.createElement('div');
    card.className = 'gate-card';
    card.dataset.taskId = task_id;
    card.innerHTML = `
        <div class="gate-header">⏸ 子任务完成 — 请确认</div>
        <div class="gate-summary">${parseMarkdown(summary || '')}</div>
        <div class="gate-role">角色: <strong>${role_id || roleId || ''}</strong></div>
        <div class="gate-actions">
            <button class="gate-approve-btn">✅ 进入下一步</button>
            <button class="gate-revise-btn">✏️ 要求修改</button>
        </div>
        <div class="gate-feedback-area" style="display:none">
            <textarea class="gate-feedback-input" placeholder="请描述修改意见…" rows="3"></textarea>
            <button class="gate-submit-revise-btn">提交修改意见</button>
        </div>
    `;

    async function doResolve(action, feedback = '') {
        card.querySelectorAll('button').forEach(b => { b.disabled = true; });
        try {
            await resolveGate(state.currentSessionId, state.activeRunId, task_id, action, feedback);
        } catch (e) {
            card.querySelectorAll('button').forEach(b => { b.disabled = false; });
        }
    }

    card.querySelector('.gate-approve-btn').onclick = () => doResolve('approve');
    card.querySelector('.gate-revise-btn').onclick = () => {
        const area = card.querySelector('.gate-feedback-area');
        area.style.display = area.style.display === 'none' ? 'block' : 'none';
    };
    card.querySelector('.gate-submit-revise-btn').onclick = () => {
        const feedback = card.querySelector('.gate-feedback-input').value.trim();
        doResolve('revise', feedback);
    };

    panel.scrollEl.appendChild(card);
    panel.scrollEl.scrollTop = panel.scrollEl.scrollHeight;
}

export function removeGateCard(instanceId, taskId) {
    const panel = _panels.get(instanceId);
    if (!panel) return;
    const el = panel.scrollEl.querySelector(`.gate-card[data-task-id="${taskId}"]`);
    if (el) el.remove();
}

export function getActiveInstanceId() {
    return _activeInstanceId;
}

// ─── Panel factory ────────────────────────────────────────────────────────────

function _createPanel(instanceId, roleId) {
    const drawer = _getDrawer();

    const panelEl = document.createElement('div');
    panelEl.className = 'agent-panel';
    panelEl.dataset.instanceId = instanceId;
    panelEl.style.display = 'none';

    const friendlyRole = roleId ? roleId.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase()) : instanceId.slice(0, 8);

    panelEl.innerHTML = `
        <div class="agent-panel-header">
            <div class="agent-panel-title">
                <span class="panel-icon">⚡</span>
                <span class="panel-role">${friendlyRole}</span>
                <span class="panel-id">${instanceId.slice(0, 8)}</span>
            </div>
            <button class="agent-panel-close" title="关闭">✕</button>
        </div>
        <div class="agent-panel-scroll"></div>
        <div class="agent-panel-input">
            <div class="panel-input-wrapper">
                <textarea class="panel-inject-input" placeholder="向此 Agent 注入消息…" rows="1"></textarea>
                <button class="panel-send-btn" title="发送">
                    <svg viewBox="0 0 24 24" fill="none"><path d="M22 2L11 13M22 2L15 22L11 13M11 13L2 9L22 2Z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>
                </button>
            </div>
        </div>
    `;

    panelEl.querySelector('.agent-panel-close').onclick = closeAgentPanel;

    // Inject send handler
    const textarea = panelEl.querySelector('.panel-inject-input');
    const sendBtn = panelEl.querySelector('.panel-send-btn');
    async function sendInject() {
        const text = textarea.value.trim();
        if (!text || !state.activeRunId) return;
        textarea.value = '';
        try {
            const { injectMessage } = await import('../core/api.js');
            await injectMessage(state.activeRunId, text);
        } catch (e) {
            // ignore
        }
    }
    sendBtn.onclick = sendInject;
    textarea.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendInject(); }
    });

    drawer.appendChild(panelEl);

    return {
        panelEl,
        scrollEl: panelEl.querySelector('.agent-panel-scroll'),
        instanceId,
        roleId,
    };
}

function _highlightNode(roleId, instanceId) {
    document.querySelectorAll('.dag-node').forEach(n => {
        n.classList.remove('active-tab');
        if (n.dataset.instanceId === instanceId || n.dataset.role === roleId) {
            n.classList.add('active-tab');
        }
    });
}
