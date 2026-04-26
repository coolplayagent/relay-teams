/**
 * components/messageTimeline/virtualList.js
 * Round-level visibility helper. It intentionally keeps running rounds mounted.
 */

export function computeVisibleRoundWindow(rounds, container, options = {}) {
    const items = Array.isArray(rounds) ? rounds : [];
    if (!container || items.length === 0) {
        return { start: 0, end: items.length };
    }
    const overscan = Math.max(1, Number(options.overscan || 3));
    const averageHeight = Math.max(120, Number(options.averageHeight || 360));
    const scrollTop = Math.max(0, Number(container.scrollTop || 0));
    const clientHeight = Math.max(0, Number(container.clientHeight || 0));
    const estimatedStart = Math.floor(scrollTop / averageHeight);
    const estimatedEnd = Math.ceil((scrollTop + clientHeight) / averageHeight);
    return {
        start: Math.max(0, estimatedStart - overscan),
        end: Math.min(items.length, estimatedEnd + overscan),
    };
}

export function shouldMountRound(round, index, windowRange, latestIndex) {
    if (index === latestIndex) return true;
    if (String(round?.run_status || '').toLowerCase() === 'running') return true;
    return index >= windowRange.start && index < windowRange.end;
}
