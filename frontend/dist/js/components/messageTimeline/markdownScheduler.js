/**
 * components/messageTimeline/markdownScheduler.js
 * Frame-batched rich text updates for streaming message parts.
 */

const pendingUpdates = new Map();
let scheduledFrame = 0;

export function scheduleRichTextUpdate(targetEl, text, options = {}, renderFn) {
    if (!targetEl || typeof renderFn !== 'function') {
        return;
    }
    pendingUpdates.set(targetEl, {
        text: String(text || ''),
        options: { ...options },
        renderFn,
    });
    if (scheduledFrame) {
        return;
    }
    const schedule = typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function'
        ? window.requestAnimationFrame.bind(window)
        : callback => setTimeout(callback, 16);
    scheduledFrame = schedule(flushRichTextUpdates);
}

export function flushRichTextUpdate(targetEl = null) {
    if (targetEl) {
        const update = pendingUpdates.get(targetEl);
        if (!update) return;
        pendingUpdates.delete(targetEl);
        update.renderFn(targetEl, update.text, update.options);
        return;
    }
    flushRichTextUpdates();
}

export function flushRichTextUpdates() {
    scheduledFrame = 0;
    const updates = Array.from(pendingUpdates.entries());
    pendingUpdates.clear();
    updates.forEach(([targetEl, update]) => {
        if (!targetEl?.isConnected && typeof targetEl?.isConnected === 'boolean') {
            return;
        }
        update.renderFn(targetEl, update.text, update.options);
    });
}
