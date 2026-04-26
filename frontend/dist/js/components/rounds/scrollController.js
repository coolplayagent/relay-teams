/**
 * components/rounds/scrollController.js
 * Round timeline scroll intent and viewport anchoring helpers.
 */

const DEFAULT_BOTTOM_THRESHOLD = 120;

export function shouldFollowLatestRoundAfterCompletion(
    container,
    latestRunId,
    {
        bottomThreshold = DEFAULT_BOTTOM_THRESHOLD,
    } = {},
) {
    if (!container) return false;
    if (isRoundScrollNearBottom(container, bottomThreshold)) {
        return true;
    }
    const safeRunId = String(latestRunId || '').trim();
    if (!safeRunId) {
        return false;
    }
    const selector = `.session-round-section[data-run-id="${escapeSelectorValue(safeRunId)}"]`;
    const section = container.querySelector(selector);
    const intent = section?.querySelector?.('.round-detail-intent')
        || section?.querySelector?.('.round-detail-header')
        || section;
    return isElementMeaningfullyVisible(intent, container);
}

export function captureChatScrollAnchor(container, getVisibleSections) {
    if (!container || typeof getVisibleSections !== 'function') {
        return null;
    }
    const containerRect = container.getBoundingClientRect();
    const sections = getVisibleSections(container);
    let best = null;
    let bestDistance = Number.POSITIVE_INFINITY;
    sections.forEach(section => {
        const rect = section.getBoundingClientRect();
        if (rect.bottom < containerRect.top || rect.top > containerRect.bottom) {
            return;
        }
        const distance = Math.abs(rect.top - containerRect.top);
        if (distance < bestDistance) {
            bestDistance = distance;
            best = {
                runId: String(section.dataset.runId || ''),
                topOffset: rect.top - containerRect.top,
            };
        }
    });
    return {
        scrollTop: Number(container.scrollTop || 0),
        visibleRunId: best?.runId || '',
        visibleTopOffset: Number(best?.topOffset || 0),
    };
}

export function restoreChatScrollAnchor(container, anchor) {
    if (!container || !anchor) {
        return false;
    }
    const runId = String(anchor.visibleRunId || '').trim();
    if (runId) {
        const selector = `.session-round-section[data-run-id="${escapeSelectorValue(runId)}"]`;
        const section = container.querySelector(selector);
        if (section) {
            const containerRect = container.getBoundingClientRect();
            const sectionRect = section.getBoundingClientRect();
            const currentOffset = sectionRect.top - containerRect.top;
            container.scrollTop += currentOffset - Number(anchor.visibleTopOffset || 0);
            return true;
        }
    }
    container.scrollTop = Number(anchor.scrollTop || 0);
    return true;
}

export function isRoundScrollNearBottom(container, threshold = DEFAULT_BOTTOM_THRESHOLD) {
    const distance = Number(container?.scrollHeight || 0)
        - Number(container?.scrollTop || 0)
        - Number(container?.clientHeight || 0);
    return distance <= threshold;
}

function isElementMeaningfullyVisible(element, container) {
    if (!element || !container) {
        return false;
    }
    const containerRect = container.getBoundingClientRect();
    const rect = element.getBoundingClientRect();
    const visibleTop = Math.max(rect.top, containerRect.top);
    const visibleBottom = Math.min(rect.bottom, containerRect.bottom);
    return visibleBottom - visibleTop >= Math.min(28, Math.max(1, rect.height || 1));
}

function escapeSelectorValue(value) {
    if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') {
        return CSS.escape(String(value));
    }
    return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}
