/**
 * components/messageTimeline/keys.js
 * Stable identity helpers for historical and live message streams.
 */

const PRIMARY_KEY = 'primary';

export function normalizeTimelineScope(scope = {}) {
    const sessionId = normalizeKeyPart(scope.sessionId || scope.session_id);
    const runId = normalizeKeyPart(scope.runId || scope.run_id || scope.traceId || scope.trace_id);
    const instanceId = normalizeKeyPart(scope.instanceId || scope.instance_id);
    const roleId = normalizeKeyPart(scope.roleId || scope.role_id);
    const view = normalizeKeyPart(scope.view || scope.scope || 'main') || 'main';
    const streamKey = normalizeStreamKey({
        streamKey: scope.streamKey || scope.stream_key,
        instanceId,
        roleId,
        isPrimary: scope.isPrimary === true,
    });
    return {
        sessionId,
        runId,
        instanceId,
        roleId,
        streamKey,
        view,
    };
}

export function timelineStreamId(scope = {}) {
    const normalized = normalizeTimelineScope(scope);
    return [
        normalized.sessionId || 'session',
        normalized.runId || 'run',
        normalized.view,
        normalized.streamKey,
    ].join('::');
}

export function timelinePartId(scope = {}, part = {}) {
    const streamId = timelineStreamId(scope);
    const toolCallId = normalizeKeyPart(part.toolCallId || part.tool_call_id);
    if (toolCallId) return `${streamId}::tool::${toolCallId}`;
    const messageId = normalizeKeyPart(part.messageId || part.message_id);
    if (messageId) return `${streamId}::message::${messageId}`;
    const eventId = normalizeKeyPart(part.eventId || part.event_id);
    const partIndex = normalizeKeyPart(part.partIndex ?? part.part_index ?? '');
    const kind = normalizeKeyPart(part.kind || part.part_kind || 'part');
    return `${streamId}::${kind}::${partIndex || eventId || 'tail'}`;
}

export function normalizeStreamKey({
    streamKey = '',
    instanceId = '',
    roleId = '',
    isPrimary = false,
} = {}) {
    const safeStreamKey = normalizeKeyPart(streamKey);
    if (safeStreamKey) {
        return safeStreamKey === 'coordinator' ? PRIMARY_KEY : safeStreamKey;
    }
    const safeInstanceId = normalizeKeyPart(instanceId);
    if (isPrimary || !safeInstanceId || safeInstanceId === 'coordinator') {
        return PRIMARY_KEY;
    }
    return safeInstanceId || (roleId ? `role:${normalizeKeyPart(roleId)}` : PRIMARY_KEY);
}

export function normalizeKeyPart(value) {
    return String(value ?? '').trim();
}
