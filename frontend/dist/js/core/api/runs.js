/**
 * core/api/runs.js
 * Run, gate, and tool-approval related API wrappers.
 */
import { requestJson } from './request.js';

export async function sendUserPrompt(
    sessionId,
    prompt,
    yolo = false,
    thinking = null,
    targetRoleId = null,
    inputParts = null,
) {
    const resolvedInput = Array.isArray(inputParts) && inputParts.length > 0
        ? inputParts
        : [{ kind: 'text', text: prompt }];
    return requestJson(
        '/api/runs',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: sessionId,
                input: resolvedInput,
                run_kind: 'conversation',
                execution_mode: 'ai',
                yolo: yolo === true,
                thinking: thinking || { enabled: false, effort: null },
                target_role_id: targetRoleId || null,
            }),
        },
        'Failed to create run',
    );
}

export async function resolveGate(runId, taskId, action, feedback = '') {
    return requestJson(
        `/api/runs/${runId}/gates/${taskId}/resolve`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action, feedback }),
        },
        'Failed to resolve gate',
    );
}

export async function resolveToolApproval(runId, toolCallId, action, feedback = '') {
    return requestJson(
        `/api/runs/${runId}/tool-approvals/${toolCallId}/resolve`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action, feedback }),
        },
        'Failed to resolve tool approval',
    );
}

export async function listUserQuestions(runId) {
    return requestJson(
        `/api/runs/${runId}/questions`,
        undefined,
        'Failed to fetch user questions',
    );
}

export async function answerUserQuestion(runId, questionId, answers) {
    return requestJson(
        `/api/runs/${runId}/questions/${questionId}:answer`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ answers }),
        },
        'Failed to answer user question',
    );
}

export async function dispatchHumanTask(sessionId, runId, taskId) {
    return requestJson(
        `/api/runs/${runId}/dispatch`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: taskId, session_id: sessionId }),
        },
        'Failed to dispatch task',
    );
}
export async function injectMessage(runId, content) {
    return requestJson(
        `/api/runs/${runId}/inject`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content }),
        },
        'Failed to inject message',
    );
}

export async function stopRun(runId, { scope = 'main', instanceId = null } = {}) {
    const payload = scope === 'subagent'
        ? { scope, instance_id: instanceId }
        : { scope: 'main' };
    return requestJson(
        `/api/runs/${runId}/stop`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to stop run',
    );
}

export async function resumeRun(runId) {
    return requestJson(
        `/api/runs/${runId}:resume`,
        {
            method: 'POST',
        },
        'Failed to resume run',
    );
}

export async function fetchRunBackgroundTasks(runId) {
    return requestJson(
        `/api/runs/${runId}/background-tasks`,
        undefined,
        'Failed to fetch background tasks',
    );
}

export async function fetchRunBackgroundTask(runId, backgroundTaskId) {
    return requestJson(
        `/api/runs/${runId}/background-tasks/${backgroundTaskId}`,
        undefined,
        'Failed to fetch background task',
    );
}

export async function stopBackgroundTask(runId, backgroundTaskId) {
    return requestJson(
        `/api/runs/${runId}/background-tasks/${backgroundTaskId}:stop`,
        { method: 'POST' },
        'Failed to stop background task',
    );
}

export async function injectSubagentMessage(runId, instanceId, content) {
    return requestJson(
        `/api/runs/${runId}/subagents/${instanceId}/inject`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content }),
        },
        'Failed to send message to subagent',
    );
}
