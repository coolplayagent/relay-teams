/**
 * components/rounds/todo.js
 * Shared todo snapshot helpers for round timeline and navigator UI.
 */
export function normalizeRoundTodoSnapshot(todo, fallbackRunId, sessionId = '') {
    if (!todo || typeof todo !== 'object') {
        return null;
    }
    const items = Array.isArray(todo.items)
        ? todo.items
            .filter(item => item && typeof item === 'object')
            .map(item => ({
                content: String(item.content || '').trim(),
                status: normalizeTodoStatus(item.status),
            }))
            .filter(item => item.content)
        : [];
    if (items.length === 0) {
        return null;
    }
    return {
        run_id: String(todo.run_id || fallbackRunId || '').trim(),
        session_id: String(todo.session_id || sessionId || '').trim(),
        items,
        version: Number(todo.version || 0),
        updated_at: String(todo.updated_at || '').trim(),
    };
}

export function normalizeTodoStatus(status) {
    const safeStatus = String(status || '').trim();
    if (safeStatus === 'completed' || safeStatus === 'in_progress') {
        return safeStatus;
    }
    return 'pending';
}

export function areRoundTodoSnapshotsEqual(left, right) {
    if (left === null || right === null) {
        return left === right;
    }
    if (
        left.run_id !== right.run_id
        || left.session_id !== right.session_id
        || left.version !== right.version
        || left.updated_at !== right.updated_at
        || left.items.length !== right.items.length
    ) {
        return false;
    }
    return left.items.every((item, index) => (
        item.content === right.items[index]?.content
        && item.status === right.items[index]?.status
    ));
}

export function buildRoundTodoPreview(items) {
    const preview = items
        .slice(0, 2)
        .map(item => item.content)
        .join(' · ');
    return preview || 'Todo';
}
