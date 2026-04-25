/**
 * components/messageTimeline/store.js
 * In-memory timeline state shared by history replay and live streams.
 */
import {
    normalizeTimelineScope,
    timelinePartId,
    timelineStreamId,
} from './keys.js';

const streams = new Map();
const seenEventsByRun = new Map();
const subscribers = new Set();

export function applyTimelineAction(action) {
    if (!action || typeof action !== 'object') return null;
    if (isDuplicateEvent(action)) {
        return getTimelineStream(action.scope || {});
    }
    const scope = normalizeTimelineScope(action.scope || {});
    const streamId = timelineStreamId(scope);
    const stream = ensureStream(scope);
    const type = String(action.type || '').trim();

    if (type === 'hydrate_parts') {
        stream.source = 'history';
        stream.parts = normalizeHydratedParts(scope, action.parts || []);
        stream.status = action.status || stream.status;
        notifySubscribers(streamId, stream, action);
        return stream;
    }

    stream.source = 'live';
    if (type === 'text_delta') {
        appendTextPart(stream, scope, String(action.text || ''), action);
    } else if (type === 'output_parts') {
        appendOutputParts(stream, scope, action.parts || [], action);
    } else if (type === 'thinking_started') {
        startThinkingPart(stream, scope, action);
    } else if (type === 'thinking_delta') {
        appendThinkingDelta(stream, scope, action);
    } else if (type === 'thinking_finished') {
        finishThinkingPart(stream, action);
    } else if (type === 'tool_call') {
        upsertToolPart(stream, scope, action, { status: 'pending' });
    } else if (type === 'tool_result') {
        upsertToolPart(stream, scope, action, {
            status: action.isError ? 'error' : 'completed',
            result: action.result,
        });
    } else if (type === 'tool_input_validation_failed') {
        upsertToolPart(stream, scope, action, {
            status: 'validation_failed',
            validation: action.validation || action.payload || {},
        });
    } else if (type === 'tool_approval_requested') {
        upsertToolPart(stream, scope, action, { approvalStatus: 'requested' });
    } else if (type === 'tool_approval_resolved') {
        upsertToolPart(stream, scope, action, {
            approvalStatus: String(action.approvalStatus || action.action || 'resolved').toLowerCase(),
        });
    } else if (type === 'stream_idle') {
        stream.idleCursor = true;
        stream.textStreaming = false;
    } else if (type === 'stream_finished') {
        finishStream(stream);
    } else if (type === 'clear_run') {
        clearTimelineRun(scope.runId);
        return null;
    }

    stream.updatedAt = Date.now();
    notifySubscribers(streamId, stream, action);
    return stream;
}

export function getTimelineStream(scope = {}) {
    return streams.get(timelineStreamId(scope)) || null;
}

export function getTimelineSnapshot(scope = {}) {
    const stream = getTimelineStream(scope);
    return stream ? cloneStream(stream) : null;
}

export function getRunTimelineSnapshot(runId) {
    const safeRunId = String(runId || '').trim();
    const coordinator = [];
    const byInstance = {};
    streams.forEach(stream => {
        if (safeRunId && stream.scope.runId !== safeRunId) return;
        const cloned = cloneStream(stream);
        if (stream.scope.streamKey === 'primary') {
            coordinator.push(cloned);
            return;
        }
        if (stream.scope.instanceId) {
            byInstance[stream.scope.instanceId] = cloned;
        }
    });
    return {
        coordinator: coordinator[coordinator.length - 1] || null,
        byInstance,
    };
}

export function clearTimelineRun(runId) {
    const safeRunId = String(runId || '').trim();
    if (!safeRunId) return;
    Array.from(streams.keys()).forEach(key => {
        const stream = streams.get(key);
        if (stream?.scope?.runId === safeRunId) {
            streams.delete(key);
        }
    });
    seenEventsByRun.delete(safeRunId);
}

export function clearTimelineState(options = {}) {
    streams.clear();
    if (options.preserveEventIds !== true) {
        seenEventsByRun.clear();
    }
}

export function subscribeTimeline(listener) {
    if (typeof listener !== 'function') {
        return () => {};
    }
    subscribers.add(listener);
    return () => {
        subscribers.delete(listener);
    };
}

if (typeof globalThis !== 'undefined') {
    globalThis.__relayTeamsMessageTimelineApplyAction = applyTimelineAction;
    globalThis.__relayTeamsMessageTimelineClearRun = clearTimelineRun;
    globalThis.__relayTeamsMessageTimelineClearState = clearTimelineState;
    globalThis.__relayTeamsMessageTimelineGetRunSnapshot = getRunTimelineSnapshot;
}

function ensureStream(scope) {
    const normalized = normalizeTimelineScope(scope);
    const streamId = timelineStreamId(normalized);
    let stream = streams.get(streamId);
    if (!stream) {
        stream = {
            id: streamId,
            scope: normalized,
            parts: [],
            textStreaming: false,
            idleCursor: false,
            status: 'running',
            updatedAt: Date.now(),
            source: 'live',
            activeThinkingByPart: new Map(),
            textSequence: 0,
            mediaSequence: 0,
            toolSequence: 0,
        };
        streams.set(streamId, stream);
    } else {
        stream.scope = { ...stream.scope, ...normalized };
        if (typeof stream.textSequence !== 'number') stream.textSequence = 0;
        if (typeof stream.mediaSequence !== 'number') stream.mediaSequence = 0;
        if (typeof stream.toolSequence !== 'number') stream.toolSequence = 0;
    }
    return stream;
}

function appendTextPart(stream, scope, text, action) {
    if (!text) return;
    const last = stream.parts[stream.parts.length - 1];
    if (last?.kind === 'text' && last.streaming === true) {
        last.content = String(last.content || '') + text;
        last.updatedAt = Date.now();
    } else {
        const eventId = String(action.eventId || '').trim();
        const partIndex = eventId ? '' : `text-${stream.textSequence++}`;
        stream.parts.push({
            id: timelinePartId(scope, { kind: 'text', eventId, partIndex }),
            kind: 'text',
            content: text,
            streaming: true,
            updatedAt: Date.now(),
        });
    }
    stream.textStreaming = true;
    stream.idleCursor = false;
}

function appendOutputParts(stream, scope, outputParts, action) {
    const parts = Array.isArray(outputParts) ? outputParts : [];
    const eventId = String(action.eventId || '').trim();
    parts.forEach((part, index) => {
        if (!part || typeof part !== 'object') return;
        if (part.kind === 'text') {
            appendTextPart(stream, scope, String(part.text || part.content || ''), {
                ...action,
                eventId: eventId ? `${eventId}:${index}` : '',
            });
            return;
        }
        if (part.kind === 'media_ref') {
            finishTextTail(stream);
            const mediaEventId = eventId ? `${eventId}:${index}` : '';
            const partIndex = mediaEventId ? '' : `media-${stream.mediaSequence++}`;
            stream.parts.push({
                id: timelinePartId(scope, {
                    kind: 'media_ref',
                    eventId: mediaEventId,
                    partIndex,
                }),
                kind: 'media_ref',
                ...part,
            });
        }
    });
}

function startThinkingPart(stream, scope, action) {
    finishTextTail(stream);
    const partIndex = String(action.partIndex ?? action.part_index ?? 0);
    const id = timelinePartId(scope, { kind: 'thinking', partIndex });
    const part = {
        id,
        kind: 'thinking',
        part_index: Number(partIndex),
        content: '',
        streaming: true,
        finished: false,
    };
    stream.parts.push(part);
    stream.activeThinkingByPart.set(partIndex, id);
    stream.textStreaming = false;
    stream.idleCursor = false;
}

function appendThinkingDelta(stream, scope, action) {
    const partIndex = String(action.partIndex ?? action.part_index ?? 0);
    const id = stream.activeThinkingByPart.get(partIndex)
        || timelinePartId(scope, { kind: 'thinking', partIndex });
    let part = stream.parts.find(item => item.id === id);
    if (!part) {
        startThinkingPart(stream, scope, action);
        part = stream.parts.find(item => item.id === stream.activeThinkingByPart.get(partIndex));
    }
    if (!part) return;
    part.content = String(part.content || '') + String(action.text || '');
    part.streaming = true;
    part.finished = false;
}

function finishThinkingPart(stream, action) {
    const partIndex = String(action.partIndex ?? action.part_index ?? 0);
    const id = stream.activeThinkingByPart.get(partIndex);
    const part = stream.parts.find(item => item.id === id);
    if (part) {
        part.streaming = false;
        part.finished = true;
    }
    stream.activeThinkingByPart.delete(partIndex);
    stream.idleCursor = true;
}

function upsertToolPart(stream, scope, action, updates = {}) {
    finishTextTail(stream);
    const toolCallId = String(action.toolCallId || action.tool_call_id || action.payload?.tool_call_id || '').trim();
    const toolName = String(action.toolName || action.tool_name || action.payload?.tool_name || 'unknown_tool').trim();
    let part = stream.parts.find(item => (
        item.kind === 'tool'
        && toolCallId
        && String(item.tool_call_id || '') === toolCallId
    ));
    if (!part && !toolCallId && updates.status !== 'pending') {
        const unresolved = stream.parts.filter(item => (
            item.kind === 'tool'
            && String(item.tool_name || '') === toolName
            && !String(item.tool_call_id || '').trim()
            && item.result === undefined
            && item.validation === undefined
            && !['completed', 'error', 'validation_failed'].includes(String(item.status || '').trim().toLowerCase())
        ));
        if (unresolved.length === 1) {
            part = unresolved[0];
        }
    }
    if (!part) {
        const localToolKey = toolCallId
            ? ''
            : `${toolName}:${stream.toolSequence++}`;
        part = {
            id: timelinePartId(scope, {
                kind: 'tool',
                toolCallId: toolCallId || localToolKey,
                eventId: action.eventId,
            }),
            kind: 'tool',
            tool_call_id: toolCallId,
            local_tool_key: localToolKey,
            tool_name: toolName,
            args: normalizeObject(action.args || action.payload?.args),
            status: 'pending',
        };
        stream.parts.push(part);
    }
    if (toolName && part.tool_name === 'unknown_tool') part.tool_name = toolName;
    if (action.args || action.payload?.args) {
        part.args = normalizeObject(action.args || action.payload?.args);
    }
    Object.assign(part, updates);
    stream.textStreaming = false;
    stream.idleCursor = updates.status === 'completed' || updates.status === 'error';
}

function finishStream(stream) {
    finishTextTail(stream);
    stream.parts.forEach(part => {
        if (part.kind === 'thinking') {
            part.streaming = false;
            part.finished = true;
        }
    });
    stream.activeThinkingByPart.clear();
    stream.textStreaming = false;
    stream.idleCursor = false;
    stream.status = 'completed';
}

function finishTextTail(stream) {
    const last = stream.parts[stream.parts.length - 1];
    if (last?.kind === 'text') {
        last.streaming = false;
    }
    stream.textStreaming = false;
}

function normalizeHydratedParts(scope, parts) {
    return (Array.isArray(parts) ? parts : []).map((part, index) => ({
        id: part.id || timelinePartId(scope, {
            kind: part.kind || part.part_kind || 'part',
            partIndex: part.part_index ?? index,
            toolCallId: part.tool_call_id,
            messageId: part.message_id,
        }),
        ...part,
    }));
}

function normalizeObject(value) {
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

function isDuplicateEvent(action) {
    const eventId = String(action.eventId || action.event_id || action.meta?.event_id || '').trim();
    const runId = String(action.scope?.runId || action.scope?.run_id || action.meta?.run_id || action.meta?.trace_id || '').trim();
    if (!eventId || !runId) return false;
    let seen = seenEventsByRun.get(runId);
    if (!seen) {
        seen = new Set();
        seenEventsByRun.set(runId, seen);
    }
    const key = `${action.type || 'event'}:${eventId}`;
    if (seen.has(key)) return true;
    seen.add(key);
    return false;
}

function cloneStream(stream) {
    return {
        id: stream.id,
        scope: { ...stream.scope },
        parts: stream.parts.map(part => ({ ...part })),
        textStreaming: stream.textStreaming === true,
        idleCursor: stream.idleCursor === true,
        status: stream.status,
        updatedAt: stream.updatedAt,
        source: stream.source || '',
    };
}

function notifySubscribers(streamId, stream, action) {
    subscribers.forEach(listener => {
        listener(streamId, cloneStream(stream), action);
    });
}
