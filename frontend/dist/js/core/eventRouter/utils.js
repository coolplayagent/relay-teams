/**
 * core/eventRouter/utils.js
 * Shared helpers used by SSE event handlers.
 */
import { state } from '../state.js';
import { els } from '../../utils/dom.js';
import { sysLog } from '../../utils/logger.js';
import { dispatchHumanTask } from '../api.js';
import { formatMessage, t } from '../../utils/i18n.js';

export function coordinatorContainerFor(eventMeta) {
    const runId = eventMeta?.trace_id || eventMeta?.run_id || state.activeRunId;
    if (runId) {
        const section = document.querySelector(`.session-round-section[data-run-id="${runId}"]`);
        if (section) return section;
    }
    const latest = els.chatMessages?.querySelector('.session-round-section:last-of-type');
    if (latest) return latest;
    return els.chatMessages;
}

export function renderHumanDispatchPanel(payload) {
    document.querySelectorAll('.human-dispatch-panel').forEach(el => el.remove());
    const container = coordinatorContainerFor({ trace_id: state.activeRunId });
    if (!container) return;

    const panel = document.createElement('div');
    panel.className = 'human-dispatch-panel';

    const tasks = payload.pending_tasks || [];
    const taskRows = tasks.map(t => `
        <div class="dispatch-task-row">
            <span class="dispatch-task-obj">${t.objective || t.task_id}</span>
            <span class="dispatch-task-role">${t.role_id || ''}</span>
            <button class="dispatch-btn" data-task-id="${t.task_id}">&#x25B6; ${t('human_dispatch.run')}</button>
        </div>
    `).join('');

    panel.innerHTML = `
        <div class="dispatch-header">${t('human_dispatch.title')}</div>
        ${taskRows || `<div class="dispatch-empty">${t('human_dispatch.empty')}</div>`}
    `;

    panel.querySelectorAll('.dispatch-btn').forEach(btn => {
        btn.onclick = async () => {
            const taskId = btn.dataset.taskId;
            if (!state.activeRunId || !state.currentSessionId) return;
            btn.disabled = true;
            btn.textContent = t('human_dispatch.dispatching');
            try {
                await dispatchHumanTask(state.currentSessionId, state.activeRunId, taskId);
            } catch (e) {
                sysLog(formatMessage('human_dispatch.error.dispatch_failed', { error: e.message }), 'log-error');
                btn.disabled = false;
                btn.textContent = t('human_dispatch.run');
            }
        };
    });

    container.appendChild(panel);
    container.scrollTop = container.scrollHeight;
}
