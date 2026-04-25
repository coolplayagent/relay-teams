/**
 * components/messageTimeline/actions.js
 * Converts SSE payloads and renderer compatibility calls into timeline actions.
 */
import { applyTimelineAction } from './store.js';

export function applyRunEventToTimeline(evType, payload = {}, eventMeta = {}, scope = {}) {
    const eventId = payload?.event_id || eventMeta?.event_id || '';
    const runId = scope.runId || eventMeta?.run_id || eventMeta?.trace_id || '';
    const baseScope = {
        ...scope,
        runId,
        instanceId: scope.instanceId || payload?.instance_id || eventMeta?.instance_id || '',
        roleId: scope.roleId || payload?.role_id || eventMeta?.role_id || '',
    };
    const type = mapEventType(evType);
    if (!type) return null;
    const action = {
        type,
        scope: baseScope,
        eventId,
        payload,
    };
    if (type === 'text_delta') {
        action.text = payload?.text || '';
    } else if (type === 'output_parts') {
        action.parts = Array.isArray(payload?.output) ? payload.output : [];
    } else if (type === 'thinking_delta') {
        action.text = payload?.text || '';
        action.partIndex = payload?.part_index ?? 0;
    } else if (type === 'thinking_started' || type === 'thinking_finished') {
        action.partIndex = payload?.part_index ?? 0;
    } else if (type.startsWith('tool_')) {
        action.toolName = payload?.tool_name || '';
        action.toolCallId = payload?.tool_call_id || '';
        action.args = payload?.args || {};
        action.result = payload?.result || {};
        action.isError = payload?.result?.ok === false || payload?.error === true;
        action.action = payload?.action || '';
    }
    return applyTimelineAction(action);
}

if (typeof globalThis !== 'undefined') {
    globalThis.__relayTeamsMessageTimelineApplyRunEvent = applyRunEventToTimeline;
}

function mapEventType(evType) {
    switch (String(evType || '').trim()) {
        case 'text_delta':
            return 'text_delta';
        case 'output_delta':
            return 'output_parts';
        case 'thinking_started':
            return 'thinking_started';
        case 'thinking_delta':
            return 'thinking_delta';
        case 'thinking_finished':
            return 'thinking_finished';
        case 'tool_call':
            return 'tool_call';
        case 'tool_result':
            return 'tool_result';
        case 'tool_input_validation_failed':
            return 'tool_input_validation_failed';
        case 'tool_approval_requested':
            return 'tool_approval_requested';
        case 'tool_approval_resolved':
            return 'tool_approval_resolved';
        case 'model_step_finished':
            return 'stream_finished';
        case 'run_completed':
        case 'run_failed':
        case 'run_stopped':
            return 'stream_finished';
        default:
            return '';
    }
}
