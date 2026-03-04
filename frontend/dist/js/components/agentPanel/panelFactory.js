/**
 * components/agentPanel/panelFactory.js
 * Panel DOM factory and inject-message bindings.
 */
import { injectSubagentMessage, stopRun } from '../../core/api.js';
import { state } from '../../core/state.js';
import { sysLog } from '../../utils/logger.js';
import { getDrawer } from './dom.js';

export function createPanel(instanceId, roleId, onClose) {
    const drawer = getDrawer();
    if (!drawer) return null;

    const panelEl = document.createElement('div');
    panelEl.className = 'agent-panel';
    panelEl.dataset.instanceId = instanceId;
    panelEl.style.display = 'none';

    const friendlyRole = roleId
        ? roleId.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())
        : instanceId.slice(0, 8);

    panelEl.innerHTML = `
        <div class="agent-panel-header">
            <div class="agent-panel-title">
                <span class="panel-icon">*</span>
                <span class="panel-role">${friendlyRole}</span>
                <span class="panel-id">${instanceId.slice(0, 8)}</span>
            </div>
            <div class="agent-token-usage" data-instance-id="${instanceId}"></div>
            <button class="agent-panel-stop" title="Stop this subagent">Stop</button>
            <button class="agent-panel-close" title="Close">x</button>
        </div>
        <div class="agent-panel-scroll"></div>
        <div class="agent-panel-input">
            <div class="panel-input-wrapper">
                <textarea class="panel-inject-input" placeholder="Inject message to this agent..." rows="1"></textarea>
                <button class="panel-send-btn" title="Send">
                    <svg viewBox="0 0 24 24" fill="none"><path d="M22 2L11 13M22 2L15 22L11 13M11 13L2 9L22 2Z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>
                </button>
            </div>
        </div>
    `;

    const closeBtn = panelEl.querySelector('.agent-panel-close');
    if (closeBtn) closeBtn.onclick = onClose;
    const stopBtn = panelEl.querySelector('.agent-panel-stop');
    if (stopBtn) {
        stopBtn.onclick = async () => {
            if (!state.activeRunId) return;
            try {
                await stopRun(state.activeRunId, { scope: 'subagent', instanceId });
                state.pausedSubagent = { runId: state.activeRunId, instanceId, roleId };
                sysLog(`Subagent paused: ${roleId || instanceId}`, 'log-info');
            } catch (e) {
                sysLog(`Failed to pause subagent: ${e.message}`, 'log-error');
            }
        };
    }

    const textarea = panelEl.querySelector('.panel-inject-input');
    const sendBtn = panelEl.querySelector('.panel-send-btn');
    async function sendInject() {
        const text = textarea.value.trim();
        if (!text || !state.activeRunId) return;
        textarea.value = '';
        textarea.style.height = 'auto';
        try {
            await injectSubagentMessage(state.activeRunId, instanceId, text);
            if (state.pausedSubagent && state.pausedSubagent.instanceId === instanceId) {
                state.pausedSubagent = null;
            }
        } catch (e) {
            sysLog(`Failed to message subagent: ${e.message}`, 'log-error');
        }
    }
    textarea.addEventListener('input', () => {
        textarea.style.height = 'auto';
        textarea.style.height = `${textarea.scrollHeight}px`;
    });
    sendBtn.onclick = sendInject;
    textarea.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendInject();
        }
    });

    drawer.appendChild(panelEl);
    return {
        panelEl,
        scrollEl: panelEl.querySelector('.agent-panel-scroll'),
        instanceId,
        roleId,
    };
}
