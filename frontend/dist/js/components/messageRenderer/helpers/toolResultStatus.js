/**
 * Shared display semantics for tool results.
 */

export function isToolResultError(result, options = {}) {
    if (options?.isError === true) {
        return true;
    }
    if (!result || typeof result !== 'object') {
        return false;
    }
    if (result.ok === false || result.error === true) {
        return true;
    }
    if (hasFailedToolData(result)) {
        return true;
    }
    if (Object.prototype.hasOwnProperty.call(result, 'data')) {
        return hasFailedToolData(result.data);
    }
    return false;
}

function hasFailedToolData(data) {
    if (!data || typeof data !== 'object') {
        return false;
    }
    const status = String(data.status || '').trim().toLowerCase();
    if (status === 'failed' || status === 'error') {
        return true;
    }
    const exitCode = normalizedExitCode(data.exit_code);
    return exitCode !== null && exitCode !== 0;
}

function normalizedExitCode(value) {
    if (typeof value === 'number' && Number.isFinite(value)) {
        return value;
    }
    if (typeof value === 'string' && value.trim()) {
        const parsed = Number(value);
        if (Number.isFinite(parsed)) {
            return parsed;
        }
    }
    return null;
}
