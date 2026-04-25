/**
 * components/messageTimeline/renderer.js
 * Minimal keyed renderer used by new timeline-aware surfaces.
 */
import {
    appendMessageText,
    appendStructuredContentPart,
    appendThinkingText,
    updateThinkingText,
    buildToolBlock,
    applyToolReturn,
    setToolStatus,
    setToolValidationFailureState,
} from '../messageRenderer/helpers.js';

export function renderTimelineStream(container, stream, options = {}) {
    if (!container || !stream) return null;
    const label = String(options.label || stream.scope?.roleId || stream.scope?.instanceId || 'Agent');
    let wrapper = container.querySelector?.(`.message[data-timeline-stream-id="${escapeSelector(stream.id)}"]`) || null;
    if (!wrapper) {
        wrapper = document.createElement('div');
        wrapper.className = 'message';
        wrapper.dataset.timelineStreamId = stream.id;
        wrapper.dataset.streamKey = stream.scope?.streamKey || '';
        wrapper.dataset.runId = stream.scope?.runId || '';
        wrapper.innerHTML = `
            <div class="msg-header">
                <span class="msg-role role-agent"></span>
            </div>
            <div class="msg-content"></div>
        `;
        container.appendChild(wrapper);
    }
    const roleEl = wrapper.querySelector('.msg-role');
    if (roleEl) roleEl.textContent = label.toUpperCase();
    const contentEl = wrapper.querySelector('.msg-content');
    if (!contentEl) return wrapper;
    syncParts(contentEl, stream.parts || [], stream.scope || {});
    return wrapper;
}

function syncParts(contentEl, parts, scope = {}) {
    const wanted = new Set();
    parts.forEach(part => {
        const id = String(part.id || '').trim();
        if (!id) return;
        wanted.add(id);
        let partEl = contentEl.querySelector?.(`[data-timeline-part-id="${escapeSelector(id)}"]`) || null;
        if (!partEl) {
            partEl = renderPart(part, scope);
            if (!partEl) return;
            partEl.dataset.timelinePartId = id;
            contentEl.appendChild(partEl);
            return;
        }
        updatePart(partEl, part, scope);
    });
    Array.from(contentEl.querySelectorAll?.('[data-timeline-part-id]') || []).forEach(partEl => {
        if (!wanted.has(String(partEl.dataset.timelinePartId || ''))) {
            partEl.remove();
        }
    });
}

function renderPart(part, scope = {}) {
    if (part.kind === 'text') {
        return appendMessageText(document.createElement('div'), part.content || '', {
            streaming: part.streaming === true,
        });
    }
    if (part.kind === 'thinking') {
        const host = document.createElement('div');
        const textEl = appendThinkingText(host, part.content || '', {
            partIndex: part.id,
            streaming: part.streaming === true,
            runId: scope.runId || scope.run_id || '',
            instanceId: scope.instanceId || scope.instance_id || '',
            streamKey: scope.streamKey || scope.stream_key || '',
        });
        return textEl?.closest?.('.thinking-block') || host.firstElementChild;
    }
    if (part.kind === 'tool') {
        const block = buildToolBlock(part.tool_name || 'unknown_tool', part.args || {}, part.tool_call_id || null);
        applyToolState(block, part);
        return block;
    }
    if (part.kind === 'media_ref') {
        const host = document.createElement('div');
        appendStructuredContentPart(host, part);
        return host.firstElementChild;
    }
    return null;
}

function updatePart(partEl, part, scope = {}) {
    if (part.kind === 'text') {
        const textEl = partEl.classList?.contains('msg-text') ? partEl : partEl.querySelector('.msg-text');
        if (textEl && textEl.textContent !== String(part.content || '')) {
            textEl.textContent = String(part.content || '');
        }
        return;
    }
    if (part.kind === 'tool') {
        applyToolState(partEl, part);
        return;
    }
    if (part.kind === 'thinking') {
        const textEl = partEl.classList?.contains('thinking-text')
            ? partEl
            : partEl.querySelector('.thinking-text');
        if (textEl) {
            updateThinkingText(textEl, part.content || '', {
                streaming: part.streaming === true,
                runId: scope.runId || scope.run_id || '',
                instanceId: scope.instanceId || scope.instance_id || '',
                streamKey: scope.streamKey || scope.stream_key || '',
                partIndex: part.id,
            });
        }
    }
}

function applyToolState(block, part) {
    if (part.validation) {
        setToolValidationFailureState(block, part.validation);
        return;
    }
    if (part.result !== undefined) {
        applyToolReturn(block, part.result);
        return;
    }
    const status = String(part.status || 'pending');
    if (status === 'error') {
        setToolStatus(block, 'error');
    } else if (status === 'completed') {
        setToolStatus(block, 'completed');
    } else {
        setToolStatus(block, 'running');
    }
}

function escapeSelector(value) {
    if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') {
        return CSS.escape(String(value || ''));
    }
    return String(value || '').replaceAll('\\', '\\\\').replaceAll('"', '\\"');
}
