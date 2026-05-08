/**
 * core/uiDiagnostics.js
 * Lightweight counters used by browser pressure tests and local debugging.
 */

const MAX_SAMPLES = 256;

const diagnostics = {
    stream_duplicate_count: 0,
    stream_gap_count: 0,
    wrong_target_render_count: 0,
    terminal_loop_count: 0,
    running_indicator_missing_count: 0,
    switch_latencies: [],
    load_latencies: [],
    send_switch_target_stable_ms: [],
};

export function recordUiDiagnostic(name, value = 1, metadata = null) {
    const safeName = String(name || '').trim();
    if (!safeName) {
        return;
    }
    const sampleListByName = {
        load_latency_ms: 'load_latencies',
        send_switch_target_stable_ms: 'send_switch_target_stable_ms',
        switch_latency_ms: 'switch_latencies',
    };
    const sampleListName = sampleListByName[safeName] || '';
    if (sampleListName) {
        diagnostics[sampleListName].push({
            value: Number(value) || 0,
            metadata: metadata && typeof metadata === 'object' ? { ...metadata } : null,
        });
        if (diagnostics[sampleListName].length > MAX_SAMPLES) {
            diagnostics[sampleListName].splice(
                0,
                diagnostics[sampleListName].length - MAX_SAMPLES,
            );
        }
        return;
    }
    if (!Object.prototype.hasOwnProperty.call(diagnostics, safeName)) {
        diagnostics[safeName] = 0;
    }
    diagnostics[safeName] += Number(value) || 0;
}

export function resetUiDiagnostics() {
    Object.keys(diagnostics).forEach(key => {
        if (Array.isArray(diagnostics[key])) {
            diagnostics[key] = [];
        } else {
            diagnostics[key] = 0;
        }
    });
}

export function getUiDiagnostics() {
    return JSON.parse(JSON.stringify(diagnostics));
}

if (typeof globalThis !== 'undefined') {
    globalThis.__agentTeamsUiDiagnostics = {
        get: getUiDiagnostics,
        record: recordUiDiagnostic,
        reset: resetUiDiagnostics,
    };
}
