/**
 * core/eventRouter.js
 * Processes SSE `RunEventType` payloads and dispatches rendering to UI components.
 */
import { state } from './state.js';
import { els } from '../utils/dom.js';
import { sysLog } from '../utils/logger.js';
import { parseMarkdown } from '../utils/markdown.js';
import { updateDagActiveNode } from '../components/workflow.js';
import { buildAgentContainer, scrollToBottom, addAgentTab } from '../components/chat.js';

// Variables tracking current streaming state
export let currentAgentDiv = null;
export let currentAgentContent = null;
export let currentToolBlock = null;
export let rawMarkdownBuffer = "";

export function resetDomStreams() {
    currentAgentDiv = null;
    currentAgentContent = null;
    currentToolBlock = null;
    rawMarkdownBuffer = "";
}

export function routeEvent(evType, payload, eventMeta, isHistorical = false) {
    if (evType === 'run_started') {
        sysLog(`Run started (trace: ${eventMeta.trace_id})`);
        state.activeAgentRoleId = payload.role_id || 'coordinator_agent';
        updateDagActiveNode();
    }
    else if (evType === 'model_step_started') {
        if (payload.instance_id && payload.role_id) {
            addAgentTab(payload.role_id, payload.instance_id, false);
        }
        state.activeAgentRoleId = payload.role_id || 'coordinator_agent';
        updateDagActiveNode();
    }
    else if (evType === 'text_delta') {
        const roleId = payload.role_id || 'agent';
        const instanceId = payload.instance_id || 'main';
        let targetContainer = state.agentViews['main'];

        if (payload.instance_id && payload.role_id) {
            addAgentTab(payload.role_id, payload.instance_id, false);
        }

        if (!currentAgentDiv || currentAgentDiv.dataset.role !== roleId || currentAgentDiv.parentElement !== targetContainer) {
            const result = buildAgentContainer(roleId, targetContainer);
            currentAgentDiv = result.div;
            currentAgentContent = result.content;
            rawMarkdownBuffer = "";
        }

        const typing = currentAgentDiv.querySelector('.typing-indicator');
        if (typing) typing.remove();

        if (roleId === 'user') {
            currentAgentContent.innerHTML = payload.text.replace(/\\n/g, '<br>');
        } else {
            rawMarkdownBuffer += payload.text;
            currentAgentContent.innerHTML = parseMarkdown(rawMarkdownBuffer);
        }

        if (targetContainer.id && targetContainer.id.startsWith('view-')) {
            targetContainer.scrollTop = targetContainer.scrollHeight;
        } else {
            scrollToBottom(targetContainer);
        }
    }
    else if (evType === 'tool_call') {
        const roleId = payload.role_id || 'agent';
        const instanceId = payload.instance_id || 'main';
        let targetContainer = state.agentViews['main'];

        if (payload.instance_id && payload.role_id) {
            addAgentTab(payload.role_id, payload.instance_id, false);
        }

        if (!currentAgentDiv || currentAgentDiv.dataset.role !== roleId || currentAgentDiv.parentElement !== targetContainer) {
            const result = buildAgentContainer(roleId, targetContainer);
            currentAgentDiv = result.div;
            currentAgentContent = result.content;
            rawMarkdownBuffer = "";
        }

        const toolBlock = document.createElement('div');
        toolBlock.className = 'tool-block';
        toolBlock.innerHTML = `
            <div class="tool-header" onclick="this.nextElementSibling.classList.toggle('open')">
                <div class="tool-title">
                    <svg viewBox="0 0 24 24" fill="none" class="icon" style="width:14px; height:14px;"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" stroke="currentColor" stroke-width="2"/></svg>
                    Used Tool: <span class="name">${payload.tool_name}</span>
                </div>
                <div class="tool-status" id="status-${eventMeta.trace_id || eventMeta.id}">
                    <div class="spinner"></div>
                </div>
            </div>
            <div class="tool-body">
                <div class="tool-args">${JSON.stringify(payload.args, null, 2)}</div>
                <div class="tool-result" id="result-${eventMeta.trace_id || eventMeta.id}">Processing...</div>
            </div>
        `;

        let containerEl = currentAgentDiv.querySelector('.msg-content');
        if (containerEl) {
            containerEl.appendChild(toolBlock);
        } else {
            currentAgentDiv.appendChild(toolBlock);
        }
        currentToolBlock = toolBlock;

        targetContainer = currentAgentDiv.parentElement;
        if (targetContainer && targetContainer.id && targetContainer.id.startsWith('view-')) {
            targetContainer.scrollTop = targetContainer.scrollHeight;
        } else {
            scrollToBottom(targetContainer);
        }

        sysLog(`[Tool] Calling ${payload.tool_name}...`);
    }
    else if (evType === 'tool_result') {
        if (currentToolBlock) {
            const statusIcon = currentToolBlock.querySelector('.tool-status');
            const resultContainer = currentToolBlock.querySelector('.tool-result');

            if (payload.error) {
                statusIcon.innerHTML = `<svg class="status-icon status-error" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>`;
                resultContainer.classList.add('error-text');
            } else {
                statusIcon.innerHTML = `<svg class="status-icon status-success" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>`;
                resultContainer.classList.remove('error-text');
            }

            let renderVal = payload.result;
            if (typeof renderVal === 'object') {
                renderVal = JSON.stringify(renderVal, null, 2);
            }
            resultContainer.innerHTML = parseMarkdown(String(renderVal));
        }
    }
    else if (evType === 'run_finished') {
        sysLog(`Run finished. (trace: ${eventMeta.trace_id})`);

        if (!eventMeta.instance_id) {
            state.activeAgentRoleId = null;
            updateDagActiveNode();
            state.isGenerating = false;
            els.sendBtn.disabled = false;
            els.promptInput.disabled = false;
            els.promptInput.focus();
            resetDomStreams();
        }
    }
    else {
        sysLog(`Unknown event type: ${evType} `, 'log-info');
    }
}
