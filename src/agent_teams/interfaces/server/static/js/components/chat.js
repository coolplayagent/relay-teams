/**
 * components/chat.js
 * Renders Pydantic-AI historical block data and active execution text deltas.
 */
import { els } from '../utils/dom.js';
import { sysLog } from '../utils/logger.js';
import { parseMarkdown } from '../utils/markdown.js';
import { state } from '../core/state.js';
import { fetchAgentMessages } from '../core/api.js';

export async function switchTab(targetId) {
    if (state.activeView === targetId) return;

    Object.values(state.agentViews).forEach(view => {
        view.style.display = 'none';
    });
    const targetView = state.agentViews[targetId];
    if (!targetView) return;

    targetView.style.display = 'block';
    state.activeView = targetId;

    if (targetId !== 'main' && targetView.innerHTML === '') {
        try {
            targetView.innerHTML = '<div style="text-align:center; padding:2rem; color:var(--text-secondary);">Loading messages...</div>';
            const messages = await fetchAgentMessages(state.currentSessionId, targetId);

            targetView.innerHTML = '';
            if (messages.length === 0) {
                targetView.innerHTML = '<div style="text-align:center; padding:2rem; color:var(--text-secondary);">No individual history yet</div>';
            } else {
                renderHistoricalMessages(targetView, messages, targetId);
            }
        } catch (e) {
            targetView.innerHTML = `<div style="color:var(--danger); padding:1rem;">Failed to load history</div>`;
        }
    }

    targetView.scrollTop = targetView.scrollHeight;
}

export function addAgentTab(roleId, instanceId, makeActive = false) {
    if (!state.agentViews) state.agentViews = {};
    if (state.agentViews[instanceId]) return;

    const view = document.createElement('div');
    view.className = 'chat-scroll';
    view.id = `view-${instanceId}`;
    view.dataset.role = roleId;
    view.style.display = 'none';

    const parent = els.chatMessages.parentElement;
    const inputContainer = parent.querySelector('.input-container');
    parent.insertBefore(view, inputContainer);

    state.agentViews[instanceId] = view;

    if (makeActive) switchTab(instanceId);
}

export function scrollToBottom(container = els.chatMessages) {
    if (container) {
        container.scrollTop = container.scrollHeight;
    }
}

export function renderHistoricalMessages(container, messages, instanceId) {
    let pendingToolBlocks = {}; // maps tool_name -> result container DOM

    messages.forEach(msgItem => {
        const role = msgItem.role;
        const msgObj = msgItem.message;
        if (!msgObj) return;

        // Strip raw ToolReturnParts and insert into previous DOM Tool Blocks instead
        let isPureToolReturn = false;
        if (role === 'user' && msgObj.parts && msgObj.parts.length > 0) {
            isPureToolReturn = msgObj.parts.every(p => p.part_kind === 'tool-return' || (p.tool_name !== undefined && p.content !== undefined && p.args === undefined));
        }

        if (isPureToolReturn) {
            msgObj.parts.forEach(part => {
                const resultDiv = pendingToolBlocks[part.tool_name];
                if (resultDiv) {
                    resultDiv.innerHTML = parseMarkdown(typeof part.content === 'object' ? JSON.stringify(part.content, null, 2) : String(part.content));
                }
            });
            return;
        }

        const wrapper = document.createElement('div');
        wrapper.className = 'message';
        wrapper.dataset.role = role;

        const label = document.createElement('div');
        label.className = 'msg-header';

        // Pydantic-AI historically labels Coordinator injected intent as "user"
        const isUser = role === 'user';
        const roleClass = isUser ? 'role-coordinator_agent' : 'role-agent';
        const roleName = isUser ? 'System/Instruction' : (document.querySelector(`.agent-tab[data-target="${instanceId}"]`)?.textContent.replace('🤖', '').trim() || "Assistant");

        label.innerHTML = `<span class="msg-role ${roleClass}">${roleName.toUpperCase()}</span>`;
        wrapper.appendChild(label);

        const contentDiv = document.createElement('div');
        contentDiv.className = 'msg-content';

        let combinedMarkdown = "";

        if (msgObj.parts) {
            msgObj.parts.forEach(part => {
                if (part.content !== undefined && part.tool_name === undefined) {
                    combinedMarkdown += part.content + "\\n\\n";
                }

                if (part.part_kind === 'tool-call' || (part.tool_name && part.args)) {
                    const tb = document.createElement('div');
                    tb.className = 'tool-block';
                    tb.innerHTML = `
                        <div class="tool-header" onclick="this.nextElementSibling.classList.toggle('open')">
                            <div class="tool-title">
                                <svg viewBox="0 0 24 24" fill="none" class="icon" style="width:14px; height:14px;"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" stroke="currentColor" stroke-width="2"/></svg>
                                Used Tool: <span class="name">${part.tool_name}</span>
                            </div>
                            <div class="tool-status"><svg class="status-icon status-success" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg></div>
                        </div>
                        <div class="tool-body">
                            <div class="tool-args">${JSON.stringify(part.args || {}, null, 2)}</div>
                            <div class="tool-result"></div>
                        </div>
                    `;
                    contentDiv.appendChild(tb);
                    pendingToolBlocks[part.tool_name] = tb.querySelector('.tool-result');
                }
            });
        }

        if (combinedMarkdown) {
            const mdDiv = document.createElement('div');
            mdDiv.innerHTML = parseMarkdown(combinedMarkdown);
            if (contentDiv.firstChild) {
                contentDiv.insertBefore(mdDiv, contentDiv.firstChild);
            } else {
                contentDiv.appendChild(mdDiv);
            }
        }

        wrapper.appendChild(contentDiv);
        container.appendChild(wrapper);
    });

    scrollToBottom(container);
}

export function buildAgentContainer(roleId, targetContainer) {
    const div = document.createElement('div');
    div.className = 'message';
    div.dataset.role = roleId;

    const friendlyName = roleId.replace('_', ' ').replace(/\\b\\w/g, l => l.toUpperCase());
    const roleClass = roleId === 'coordinator_agent' ? 'role-coordinator_agent' : 'role-agent';

    div.innerHTML = `
        <div class="msg-header">
            <span class="msg-role ${roleClass}">${friendlyName}</span>
        </div>
        <div class="msg-content">
            <div class="msg-text"></div>
            <div class="typing-indicator" id="typing-${roleId}">
                <div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>
            </div>
        </div>
    `;
    targetContainer.appendChild(div);

    if (targetContainer.id && targetContainer.id.startsWith('view-')) {
        targetContainer.scrollTop = targetContainer.scrollHeight;
    } else {
        scrollToBottom(targetContainer);
    }

    return {
        div: div,
        content: div.querySelector('.msg-text')
    };
}
