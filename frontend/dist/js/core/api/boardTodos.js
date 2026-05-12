/**
 * core/api/boardTodos.js
 * Workspace TODO board API wrappers.
 */
import { requestJson } from './request.js';

export async function fetchBoardTodos({ workspaceId, includeArchived = false } = {}) {
    const query = new URLSearchParams();
    query.set('workspace_id', String(workspaceId || '').trim());
    if (includeArchived) {
        query.set('include_archived', 'true');
    }
    return requestJson(
        `/api/boards/todos?${query.toString()}`,
        {},
        'Failed to fetch board TODOs',
    );
}

export async function fetchBoardTodoChanges({ workspaceId, includeArchived = false, afterRevision = 0 } = {}) {
    const query = new URLSearchParams();
    query.set('workspace_id', String(workspaceId || '').trim());
    query.set('after_revision', String(Number.isFinite(Number(afterRevision)) ? Number(afterRevision) : 0));
    if (includeArchived) {
        query.set('include_archived', 'true');
    }
    return requestJson(
        `/api/boards/todos:changes?${query.toString()}`,
        {},
        'Failed to fetch board TODO changes',
    );
}

export async function syncBoardTodos({ workspaceId, includeArchived = false } = {}) {
    return requestJson(
        '/api/boards/todos:sync',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                workspace_id: String(workspaceId || '').trim(),
                include_archived: includeArchived === true,
            }),
        },
        'Failed to sync board TODOs',
    );
}

export async function syncBoardTodoChanges({ workspaceId, includeArchived = false, afterRevision = 0, forceFull = false } = {}) {
    return requestJson(
        '/api/boards/todos:sync-changes',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                workspace_id: String(workspaceId || '').trim(),
                include_archived: includeArchived === true,
                after_revision: Number.isFinite(Number(afterRevision)) ? Number(afterRevision) : 0,
                force_full: forceFull === true,
            }),
        },
        'Failed to sync board TODO changes',
    );
}

export async function fetchBoardTodoSources({ workspaceId } = {}) {
    const query = new URLSearchParams();
    query.set('workspace_id', String(workspaceId || '').trim());
    return requestJson(
        `/api/boards/todo-sources?${query.toString()}`,
        {},
        'Failed to fetch board TODO sources',
    );
}

export async function createBoardTodoSource(payload = {}) {
    return requestJson(
        '/api/boards/todo-sources',
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to create board TODO source',
    );
}

export async function updateBoardTodoSource(sourceId, payload = {}) {
    return requestJson(
        `/api/boards/todo-sources/${encodeURIComponent(sourceId)}`,
        {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to update board TODO source',
    );
}

export async function deleteBoardTodoSource(sourceId) {
    return requestJson(
        `/api/boards/todo-sources/${encodeURIComponent(sourceId)}`,
        {
            method: 'DELETE',
        },
        'Failed to delete board TODO source',
    );
}

export async function previewStartBoardTodo(todoId, payload = {}) {
    return requestJson(
        `/api/boards/todos/${encodeURIComponent(todoId)}:preview-start`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to preview board TODO prompt',
    );
}

export async function startBoardTodo(todoId, payload = {}) {
    return requestJson(
        `/api/boards/todos/${encodeURIComponent(todoId)}:start`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to start board TODO',
    );
}

export async function requestBoardTodoChanges(todoId, payload = {}) {
    return requestJson(
        `/api/boards/todos/${encodeURIComponent(todoId)}:request-changes`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to request board TODO changes',
    );
}

export async function markBoardTodoDone(todoId, payload = {}) {
    return requestJson(
        `/api/boards/todos/${encodeURIComponent(todoId)}:mark-done`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to mark board TODO done',
    );
}

export async function archiveBoardTodo(todoId, payload = {}) {
    return requestJson(
        `/api/boards/todos/${encodeURIComponent(todoId)}:archive`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to archive board TODO',
    );
}

export async function restoreBoardTodo(todoId) {
    return requestJson(
        `/api/boards/todos/${encodeURIComponent(todoId)}:restore`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
        },
        'Failed to restore board TODO',
    );
}

export async function linkBoardTodoPullRequest(todoId, payload = {}) {
    return requestJson(
        `/api/boards/todos/${encodeURIComponent(todoId)}:link-pr`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        },
        'Failed to link board TODO pull request',
    );
}
