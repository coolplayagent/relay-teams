/**
 * components/messageRenderer/helpers/block.js
 * Message block and part rendering helpers.
 */
import { isCoordinatorRoleId } from '../../../core/state.js';
import { parseMarkdown } from '../../../utils/markdown.js';
import {
    applyToolReturn,
    buildToolBlock,
    indexPendingToolBlock,
    resolvePendingToolBlock,
    setToolValidationFailureState,
} from './toolBlocks.js';

export function renderMessageBlock(container, role, label, parts = []) {
    const safeLabel = label || 'Agent';
    if (container) {
        const empty = container.querySelector('.panel-empty');
        if (empty) empty.remove();
    }
    const wrapper = document.createElement('div');
    wrapper.className = 'message';
    wrapper.dataset.role = role;

    const roleClass = roleClassName(role, safeLabel);
    wrapper.innerHTML = `
        <div class="msg-header">
            <span class="msg-role ${roleClass}">${safeLabel.toUpperCase()}</span>
        </div>
        <div class="msg-content"></div>
    `;
    container.appendChild(wrapper);
    scrollBottom(container);

    const contentEl = wrapper.querySelector('.msg-content');
    const pendingToolBlocks = {};

    if (parts.length > 0) {
        renderParts(contentEl, parts, pendingToolBlocks);
    }

    return { wrapper, contentEl, pendingToolBlocks };
}

export function renderParts(contentEl, parts, pendingToolBlocks) {
    let combinedText = '';

    const flushText = () => {
        if (combinedText.trim()) {
            const textEl = document.createElement('div');
            textEl.className = 'msg-text';
            textEl.innerHTML = parseMarkdown(combinedText.trim());
            contentEl.appendChild(textEl);
            combinedText = '';
        }
    };

    parts.forEach(part => {
        const kind = part.part_kind;

        if (kind === 'text' || kind === 'user-prompt') {
            combinedText += (part.content || '') + '\n\n';
        } else if (kind === 'tool-call' || (part.tool_name && part.args !== undefined)) {
            flushText();
            const tb = buildToolBlock(part.tool_name, part.args, part.tool_call_id);
            contentEl.appendChild(tb);
            indexPendingToolBlock(pendingToolBlocks, tb, part.tool_name, part.tool_call_id);
        } else if (kind === 'tool-return') {
            const toolBlock = resolvePendingToolBlock(
                pendingToolBlocks,
                part.tool_name,
                part.tool_call_id,
            );
            if (toolBlock) applyToolReturn(toolBlock, part.content);
        } else if (kind === 'retry-prompt' && part.tool_name) {
            let toolBlock = resolvePendingToolBlock(
                pendingToolBlocks,
                part.tool_name,
                part.tool_call_id,
            );
            if (!toolBlock) {
                toolBlock = buildToolBlock(part.tool_name, {}, part.tool_call_id);
                contentEl.appendChild(toolBlock);
                indexPendingToolBlock(
                    pendingToolBlocks,
                    toolBlock,
                    part.tool_name,
                    part.tool_call_id,
                );
            }
            setToolValidationFailureState(toolBlock, {
                reason: 'Input validation failed before tool execution.',
                details: part.content,
            });
        }
    });

    flushText();
}

export function labelFromRole(role, roleId, instanceId) {
    if (role === 'user') return 'System';
    if (isCoordinatorRoleId(roleId)) return 'Coordinator';
    if (roleId) return roleId;
    return instanceId ? instanceId.slice(0, 8) : 'Agent';
}

export function scrollBottom(container) {
    if (container) container.scrollTop = container.scrollHeight;
}

function roleClassName(role, label) {
    if (label?.toLowerCase().includes('coordinator')) return 'role-coordinator';
    if (role === 'user') return 'role-user';
    return 'role-agent';
}
