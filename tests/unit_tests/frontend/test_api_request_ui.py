# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_request_json_does_not_log_abort_errors(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "core" / "api" / "request.js"
    module_under_test_path = tmp_path / "request.mjs"
    runner_path = tmp_path / "runner-request.mjs"
    logger_path = tmp_path / "mockLogger.mjs"

    source_text = source_path.read_text(encoding="utf-8").replace(
        "../../utils/logger.js",
        "./mockLogger.mjs",
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")
    logger_path.write_text(
        """
export function errorToPayload(error, context = {}) {
    return { name: error?.name || '', ...context };
}

export function logError(...args) {
    globalThis.__logErrorCalls.push(args);
}
""".strip(),
        encoding="utf-8",
    )
    runner_path.write_text(
        """
globalThis.__logErrorCalls = [];
globalThis.fetch = async () => {
    throw new DOMException('aborted', 'AbortError');
};

const { requestJson } = await import('./request.mjs');
let caughtName = '';
try {
    await requestJson('/api/sessions/session-a', undefined, 'failed');
} catch (error) {
    caughtName = error?.name || '';
}

console.log(JSON.stringify({
    caughtName,
    logErrorCalls: globalThis.__logErrorCalls.length,
}));
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(runner_path)],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert json.loads(completed.stdout) == {
        "caughtName": "AbortError",
        "logErrorCalls": 0,
    }


def test_request_json_emits_backend_status_hints(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "core" / "api" / "request.js"
    module_under_test_path = tmp_path / "request.mjs"
    runner_path = tmp_path / "runner-request-status-hints.mjs"
    logger_path = tmp_path / "mockLogger.mjs"

    source_text = source_path.read_text(encoding="utf-8").replace(
        "../../utils/logger.js",
        "./mockLogger.mjs",
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")
    logger_path.write_text(
        """
export function errorToPayload(error, context = {}) {
    return { name: error?.name || '', ...context };
}

export function logError(...args) {
    globalThis.__logErrorCalls.push(args);
}
""".strip(),
        encoding="utf-8",
    )
    runner_path.write_text(
        """
globalThis.__logErrorCalls = [];
const hints = [];
globalThis.CustomEvent = class CustomEvent {
    constructor(type, init = {}) {
        this.type = type;
        this.detail = init.detail;
    }
};
globalThis.window = {
    dispatchEvent: event => hints.push({
        type: event.type,
        status: event.detail?.status || '',
    }),
};

let mode = 'ok';
globalThis.fetch = async () => {
    if (mode === 'network') {
        throw new Error('network down');
    }
    if (mode === 'abort') {
        throw new DOMException('aborted', 'AbortError');
    }
    return {
        ok: mode !== 'http-error',
        status: 503,
        json: async () => ({ detail: 'busy' }),
    };
};

const { requestJson } = await import('./request.mjs');
await requestJson('/api/sessions', undefined, 'failed');

mode = 'http-error';
try {
    await requestJson('/api/sessions', undefined, 'failed');
} catch (_) {
    // expected
}

mode = 'network';
try {
    await requestJson('/api/sessions', undefined, 'failed');
} catch (_) {
    // expected
}

mode = 'abort';
try {
    await requestJson('/api/sessions', undefined, 'failed');
} catch (_) {
    // expected
}

console.log(JSON.stringify({
    hints,
    logErrorCalls: globalThis.__logErrorCalls.length,
}));
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(runner_path)],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert json.loads(completed.stdout) == {
        "hints": [
            {
                "type": "agent-teams-backend-status-hint",
                "status": "online",
            },
            {
                "type": "agent-teams-backend-status-hint",
                "status": "online",
            },
            {
                "type": "agent-teams-backend-status-hint",
                "status": "offline",
            },
        ],
        "logErrorCalls": 2,
    }


def test_request_json_managed_coalesces_matching_gets(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "core" / "api" / "request.js"
    module_under_test_path = tmp_path / "request.mjs"
    runner_path = tmp_path / "runner-request-managed.mjs"
    logger_path = tmp_path / "mockLogger.mjs"

    source_text = source_path.read_text(encoding="utf-8").replace(
        "../../utils/logger.js",
        "./mockLogger.mjs",
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")
    logger_path.write_text(
        """
export function errorToPayload(error, context = {}) {
    return { name: error?.name || '', ...context };
}

export function logError(...args) {
    globalThis.__logErrorCalls.push(args);
}
""".strip(),
        encoding="utf-8",
    )
    runner_path.write_text(
        """
globalThis.__logErrorCalls = [];
let fetchCalls = 0;
let releaseFetch;
const fetchStarted = new Promise(resolve => {
    globalThis.fetch = async () => {
        fetchCalls += 1;
        resolve();
        await new Promise(done => {
            releaseFetch = done;
        });
        return {
            ok: true,
            json: async () => ({ ok: true, fetchCalls }),
        };
    };
});

const { requestJsonManaged } = await import('./request.mjs');
const first = requestJsonManaged('sessions:list', '/api/sessions', undefined, 'failed');
const second = requestJsonManaged('sessions:list', '/api/sessions', undefined, 'failed');
await fetchStarted;
releaseFetch();
const results = await Promise.all([first, second]);
const third = await requestJsonManaged('sessions:list', '/api/sessions', undefined, 'failed');

console.log(JSON.stringify({
    fetchCalls,
    first: results[0],
    second: results[1],
    third,
    logErrorCalls: globalThis.__logErrorCalls.length,
}));
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(runner_path)],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert json.loads(completed.stdout) == {
        "fetchCalls": 1,
        "first": {"ok": True, "fetchCalls": 1},
        "second": {"ok": True, "fetchCalls": 1},
        "third": {"ok": True, "fetchCalls": 1},
        "logErrorCalls": 0,
    }


def test_request_json_managed_can_invalidate_cached_gets(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "core" / "api" / "request.js"
    module_under_test_path = tmp_path / "request.mjs"
    runner_path = tmp_path / "runner-request-invalidate.mjs"
    logger_path = tmp_path / "mockLogger.mjs"

    source_text = source_path.read_text(encoding="utf-8").replace(
        "../../utils/logger.js",
        "./mockLogger.mjs",
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")
    logger_path.write_text(
        """
export function errorToPayload(error, context = {}) {
    return { name: error?.name || '', ...context };
}

export function logError(...args) {
    globalThis.__logErrorCalls.push(args);
}
""".strip(),
        encoding="utf-8",
    )
    runner_path.write_text(
        """
globalThis.__logErrorCalls = [];
let fetchCalls = 0;
globalThis.fetch = async () => {
    fetchCalls += 1;
    return {
        ok: true,
        json: async () => ({ fetchCalls }),
    };
};

const { invalidateManagedRequests, requestJsonManaged } = await import('./request.mjs');
const first = await requestJsonManaged('sessions:list', '/api/sessions', undefined, 'failed');
const cached = await requestJsonManaged('sessions:list', '/api/sessions', undefined, 'failed');
invalidateManagedRequests('sessions:');
const refreshed = await requestJsonManaged('sessions:list', '/api/sessions', undefined, 'failed');

console.log(JSON.stringify({
    fetchCalls,
    first,
    cached,
    refreshed,
    logErrorCalls: globalThis.__logErrorCalls.length,
}));
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(runner_path)],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert json.loads(completed.stdout) == {
        "fetchCalls": 2,
        "first": {"fetchCalls": 1},
        "cached": {"fetchCalls": 1},
        "refreshed": {"fetchCalls": 2},
        "logErrorCalls": 0,
    }


def test_request_json_managed_prunes_expired_cached_gets(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "core" / "api" / "request.js"
    module_under_test_path = tmp_path / "request.mjs"
    runner_path = tmp_path / "runner-request-expired-cache.mjs"
    logger_path = tmp_path / "mockLogger.mjs"

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../../utils/logger.js", "./mockLogger.mjs")
        .replace(
            "const COALESCED_CACHE = new Map();",
            "export const COALESCED_CACHE = new Map();",
        )
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")
    logger_path.write_text(
        """
export function errorToPayload(error, context = {}) {
    return { name: error?.name || '', ...context };
}

export function logError(...args) {
    globalThis.__logErrorCalls.push(args);
}
""".strip(),
        encoding="utf-8",
    )
    runner_path.write_text(
        """
globalThis.__logErrorCalls = [];
let fetchCalls = 0;
globalThis.fetch = async url => {
    fetchCalls += 1;
    return {
        ok: true,
        json: async () => ({ url, fetchCalls }),
    };
};

const { COALESCED_CACHE, requestJsonManaged } = await import('./request.mjs');
await requestJsonManaged('sessions:old:1', '/api/sessions/old-1', undefined, 'failed', { ttlMs: 1 });
await requestJsonManaged('sessions:old:2', '/api/sessions/old-2', undefined, 'failed', { ttlMs: 1 });
await new Promise(resolve => setTimeout(resolve, 5));
await requestJsonManaged('sessions:fresh', '/api/sessions/fresh', undefined, 'failed', { ttlMs: 1000 });

console.log(JSON.stringify({
    fetchCalls,
    cacheKeys: Array.from(COALESCED_CACHE.keys()),
    logErrorCalls: globalThis.__logErrorCalls.length,
}));
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(runner_path)],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert json.loads(completed.stdout) == {
        "fetchCalls": 3,
        "cacheKeys": ["sessions:fresh"],
        "logErrorCalls": 0,
    }


def test_request_json_managed_does_not_cache_invalidated_in_flight_get(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "core" / "api" / "request.js"
    module_under_test_path = tmp_path / "request.mjs"
    runner_path = tmp_path / "runner-request-stale-inflight.mjs"
    logger_path = tmp_path / "mockLogger.mjs"

    source_text = source_path.read_text(encoding="utf-8").replace(
        "../../utils/logger.js",
        "./mockLogger.mjs",
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")
    logger_path.write_text(
        """
export function errorToPayload(error, context = {}) {
    return { name: error?.name || '', ...context };
}

export function logError(...args) {
    globalThis.__logErrorCalls.push(args);
}
""".strip(),
        encoding="utf-8",
    )
    runner_path.write_text(
        """
globalThis.__logErrorCalls = [];
let fetchCalls = 0;
const releases = [];
async function waitForFetch(callNumber) {
    while (!releases[callNumber]) {
        await new Promise(resolve => setTimeout(resolve, 0));
    }
}
globalThis.fetch = async () => {
    fetchCalls += 1;
    const callNumber = fetchCalls;
    await new Promise(resolve => {
        releases[callNumber] = resolve;
    });
    return {
        ok: true,
        json: async () => ({ fetchCalls: callNumber }),
    };
};

const { invalidateManagedRequests, requestJsonManaged } = await import('./request.mjs');
const firstPromise = requestJsonManaged('sessions:list', '/api/sessions', undefined, 'failed');
await waitForFetch(1);
invalidateManagedRequests('sessions:');
releases[1]();
const first = await firstPromise;
const secondPromise = requestJsonManaged('sessions:list', '/api/sessions', undefined, 'failed');
await waitForFetch(2);
releases[2]();
const second = await secondPromise;

console.log(JSON.stringify({
    fetchCalls,
    first,
    second,
    logErrorCalls: globalThis.__logErrorCalls.length,
}));
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(runner_path)],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert json.loads(completed.stdout) == {
        "fetchCalls": 2,
        "first": {"fetchCalls": 1},
        "second": {"fetchCalls": 2},
        "logErrorCalls": 0,
    }


def test_request_json_managed_aborts_invalidated_in_flight_get(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "core" / "api" / "request.js"
    module_under_test_path = tmp_path / "request.mjs"
    runner_path = tmp_path / "runner-request-abort-invalidated-inflight.mjs"
    logger_path = tmp_path / "mockLogger.mjs"

    source_text = source_path.read_text(encoding="utf-8").replace(
        "../../utils/logger.js",
        "./mockLogger.mjs",
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")
    logger_path.write_text(
        """
export function errorToPayload(error, context = {}) {
    return { name: error?.name || '', ...context };
}

export function logError(...args) {
    globalThis.__logErrorCalls.push(args);
}
""".strip(),
        encoding="utf-8",
    )
    runner_path.write_text(
        """
globalThis.__logErrorCalls = [];
let fetchCalls = 0;
const releases = new Map();
const started = new Map();
const abortedUrls = [];

async function waitForStarted(url) {
    while (!started.has(url)) {
        await new Promise(resolve => setTimeout(resolve, 0));
    }
}

async function waitForFetchCalls(count) {
    while (fetchCalls < count) {
        await new Promise(resolve => setTimeout(resolve, 0));
    }
}

globalThis.fetch = async (url, options = {}) => {
    fetchCalls += 1;
    started.set(url, fetchCalls);
    return await new Promise((resolve, reject) => {
        releases.set(url, () => {
            resolve({
                ok: true,
                json: async () => ({ url, fetchCalls: started.get(url) }),
            });
        });
        options.signal?.addEventListener('abort', () => {
            abortedUrls.push(url);
            reject(new DOMException('aborted', 'AbortError'));
        }, { once: true });
    });
};

const { invalidateManagedRequests, requestJsonManaged } = await import('./request.mjs');
const stalePromise = requestJsonManaged('sessions:detail:1', '/api/sessions/1', undefined, 'failed');
await waitForStarted('/api/sessions/1');
invalidateManagedRequests('sessions:');

let staleName = '';
try {
    await stalePromise;
} catch (error) {
    staleName = error?.name || '';
}

const freshPromise = requestJsonManaged('sessions:detail:1', '/api/sessions/1', undefined, 'failed');
await waitForFetchCalls(2);
releases.get('/api/sessions/1')();
const freshResult = await freshPromise;

console.log(JSON.stringify({
    fetchCalls,
    staleName,
    freshResult,
    abortedUrls,
    logErrorCalls: globalThis.__logErrorCalls.length,
}));
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(runner_path)],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert json.loads(completed.stdout) == {
        "fetchCalls": 2,
        "staleName": "AbortError",
        "freshResult": {"url": "/api/sessions/1", "fetchCalls": 2},
        "abortedUrls": ["/api/sessions/1"],
        "logErrorCalls": 0,
    }


def test_request_json_managed_prunes_generation_after_invalidated_gets(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "core" / "api" / "request.js"
    module_under_test_path = tmp_path / "request.mjs"
    runner_path = tmp_path / "runner-request-generation-prune.mjs"
    logger_path = tmp_path / "mockLogger.mjs"

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../../utils/logger.js", "./mockLogger.mjs")
        .replace(
            "const COALESCED_GENERATIONS = new Map();",
            "export const COALESCED_GENERATIONS = new Map();",
        )
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")
    logger_path.write_text(
        """
export function errorToPayload(error, context = {}) {
    return { name: error?.name || '', ...context };
}

export function logError(...args) {
    globalThis.__logErrorCalls.push(args);
}
""".strip(),
        encoding="utf-8",
    )
    runner_path.write_text(
        """
globalThis.__logErrorCalls = [];
let fetchCalls = 0;
let activeFetchStarted;

function waitForActiveFetch() {
    return new Promise(resolve => {
        activeFetchStarted = resolve;
    });
}

globalThis.fetch = async (_url, options = {}) => {
    fetchCalls += 1;
    activeFetchStarted?.();
    return await new Promise((_resolve, reject) => {
        options.signal?.addEventListener('abort', () => {
            reject(new DOMException('aborted', 'AbortError'));
        }, { once: true });
    });
};

const {
    COALESCED_GENERATIONS,
    invalidateManagedRequests,
    requestJsonManaged,
} = await import('./request.mjs');

for (let index = 0; index < 20; index += 1) {
    const key = `sessions:detail:${index}`;
    const started = waitForActiveFetch();
    const stalePromise = requestJsonManaged(key, `/api/sessions/${index}`, undefined, 'failed');
    await started;
    invalidateManagedRequests(key);
    try {
        await stalePromise;
    } catch (_) {
        // expected abort
    }
}

console.log(JSON.stringify({
    fetchCalls,
    generationKeys: Array.from(COALESCED_GENERATIONS.keys()),
    logErrorCalls: globalThis.__logErrorCalls.length,
}));
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(runner_path)],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert json.loads(completed.stdout) == {
        "fetchCalls": 20,
        "generationKeys": [],
        "logErrorCalls": 0,
    }


def test_request_json_managed_old_finalizer_keeps_newer_in_flight_get(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "core" / "api" / "request.js"
    module_under_test_path = tmp_path / "request.mjs"
    runner_path = tmp_path / "runner-request-inflight-finalizer.mjs"
    logger_path = tmp_path / "mockLogger.mjs"

    source_text = source_path.read_text(encoding="utf-8").replace(
        "../../utils/logger.js",
        "./mockLogger.mjs",
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")
    logger_path.write_text(
        """
export function errorToPayload(error, context = {}) {
    return { name: error?.name || '', ...context };
}

export function logError(...args) {
    globalThis.__logErrorCalls.push(args);
}
""".strip(),
        encoding="utf-8",
    )
    runner_path.write_text(
        """
globalThis.__logErrorCalls = [];
let fetchCalls = 0;
const releases = [];
async function waitForFetch(callNumber) {
    while (!releases[callNumber]) {
        await new Promise(resolve => setTimeout(resolve, 0));
    }
}
globalThis.fetch = async () => {
    fetchCalls += 1;
    const callNumber = fetchCalls;
    await new Promise(resolve => {
        releases[callNumber] = resolve;
    });
    return {
        ok: true,
        json: async () => ({ fetchCalls: callNumber }),
    };
};

const { invalidateManagedRequests, requestJsonManaged } = await import('./request.mjs');
const firstPromise = requestJsonManaged('sessions:list', '/api/sessions', undefined, 'failed');
await waitForFetch(1);
invalidateManagedRequests('sessions:');
const secondPromise = requestJsonManaged('sessions:list', '/api/sessions', undefined, 'failed');
await waitForFetch(2);
releases[1]();
const first = await firstPromise;
const thirdPromise = requestJsonManaged('sessions:list', '/api/sessions', undefined, 'failed');
releases[2]();
const [second, third] = await Promise.all([secondPromise, thirdPromise]);

console.log(JSON.stringify({
    fetchCalls,
    first,
    second,
    third,
    logErrorCalls: globalThis.__logErrorCalls.length,
}));
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(runner_path)],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert json.loads(completed.stdout) == {
        "fetchCalls": 2,
        "first": {"fetchCalls": 1},
        "second": {"fetchCalls": 2},
        "third": {"fetchCalls": 2},
        "logErrorCalls": 0,
    }


def test_request_json_managed_aborts_unobserved_stale_get(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "core" / "api" / "request.js"
    module_under_test_path = tmp_path / "request.mjs"
    runner_path = tmp_path / "runner-request-abort-stale.mjs"
    logger_path = tmp_path / "mockLogger.mjs"

    source_text = source_path.read_text(encoding="utf-8").replace(
        "../../utils/logger.js",
        "./mockLogger.mjs",
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")
    logger_path.write_text(
        """
export function errorToPayload(error, context = {}) {
    return { name: error?.name || '', ...context };
}

export function logError(...args) {
    globalThis.__logErrorCalls.push(args);
}
""".strip(),
        encoding="utf-8",
    )
    runner_path.write_text(
        """
globalThis.__logErrorCalls = [];
let fetchCalls = 0;
let abortedByFetch = false;
globalThis.fetch = async (_url, options = {}) => {
    fetchCalls += 1;
    await new Promise((resolve, reject) => {
        options.signal?.addEventListener('abort', () => {
            abortedByFetch = true;
            reject(new DOMException('aborted', 'AbortError'));
        }, { once: true });
    });
};

const controller = new AbortController();
const { requestJsonManaged } = await import('./request.mjs');
const promise = requestJsonManaged(
    'sessions:stale:subagents',
    '/api/sessions/stale/subagents',
    { signal: controller.signal },
    'failed',
    { lane: 'heavy' },
);
controller.abort();

let caughtName = '';
try {
    await promise;
} catch (error) {
    caughtName = error?.name || '';
}
await new Promise(resolve => setTimeout(resolve, 0));

console.log(JSON.stringify({
    fetchCalls,
    caughtName,
    abortedByFetch,
    logErrorCalls: globalThis.__logErrorCalls.length,
}));
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(runner_path)],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert json.loads(completed.stdout) == {
        "fetchCalls": 0,
        "caughtName": "AbortError",
        "abortedByFetch": False,
        "logErrorCalls": 0,
    }


def test_request_json_managed_evicts_aborted_queued_get_for_fresh_consumer(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "core" / "api" / "request.js"
    module_under_test_path = tmp_path / "request.mjs"
    runner_path = tmp_path / "runner-request-aborted-queued-consumer.mjs"
    logger_path = tmp_path / "mockLogger.mjs"

    source_text = source_path.read_text(encoding="utf-8").replace(
        "../../utils/logger.js",
        "./mockLogger.mjs",
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")
    logger_path.write_text(
        """
export function errorToPayload(error, context = {}) {
    return { name: error?.name || '', ...context };
}

export function logError(...args) {
    globalThis.__logErrorCalls.push(args);
}
""".strip(),
        encoding="utf-8",
    )
    runner_path.write_text(
        """
globalThis.__logErrorCalls = [];
let fetchCalls = 0;
const releases = new Map();
const started = new Map();

async function waitForStarted(url) {
    while (!started.has(url)) {
        await new Promise(resolve => setTimeout(resolve, 0));
    }
}

globalThis.fetch = async (url, options = {}) => {
    fetchCalls += 1;
    started.set(url, fetchCalls);
    return await new Promise((resolve, reject) => {
        releases.set(url, () => {
            resolve({
                ok: true,
                json: async () => ({ url, fetchCalls: started.get(url) }),
            });
        });
        options.signal?.addEventListener('abort', () => {
            reject(new DOMException('aborted', 'AbortError'));
        }, { once: true });
    });
};

const { requestJsonManaged } = await import('./request.mjs');
const firstBlocker = requestJsonManaged(
    'blocker:1',
    '/api/blocker-1',
    undefined,
    'failed',
    { lane: 'heavy', ttlMs: 0 },
);
const secondBlocker = requestJsonManaged(
    'blocker:2',
    '/api/blocker-2',
    undefined,
    'failed',
    { lane: 'heavy', ttlMs: 0 },
);
await waitForStarted('/api/blocker-1');
await waitForStarted('/api/blocker-2');

const controller = new AbortController();
const abortedQueued = requestJsonManaged(
    'sessions:detail:1',
    '/api/sessions/1',
    { signal: controller.signal },
    'failed',
    { lane: 'heavy', ttlMs: 0 },
);
controller.abort();

let abortedName = '';
try {
    await abortedQueued;
} catch (error) {
    abortedName = error?.name || '';
}

const fresh = requestJsonManaged(
    'sessions:detail:1',
    '/api/sessions/1',
    undefined,
    'failed',
    { lane: 'heavy', ttlMs: 0 },
);
releases.get('/api/blocker-1')();
releases.get('/api/blocker-2')();
await waitForStarted('/api/sessions/1');
releases.get('/api/sessions/1')();
const freshResult = await fresh;
await Promise.allSettled([firstBlocker, secondBlocker]);

console.log(JSON.stringify({
    abortedName,
    fetchCalls,
    freshResult,
    logErrorCalls: globalThis.__logErrorCalls.length,
}));
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(runner_path)],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert json.loads(completed.stdout) == {
        "abortedName": "AbortError",
        "fetchCalls": 3,
        "freshResult": {"url": "/api/sessions/1", "fetchCalls": 3},
        "logErrorCalls": 0,
    }


def test_request_json_managed_removes_invalidated_queued_get(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = repo_root / "frontend" / "dist" / "js" / "core" / "api" / "request.js"
    module_under_test_path = tmp_path / "request.mjs"
    runner_path = tmp_path / "runner-request-remove-invalidated-queued.mjs"
    logger_path = tmp_path / "mockLogger.mjs"

    source_text = source_path.read_text(encoding="utf-8").replace(
        "../../utils/logger.js",
        "./mockLogger.mjs",
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")
    logger_path.write_text(
        """
export function errorToPayload(error, context = {}) {
    return { name: error?.name || '', ...context };
}

export function logError(...args) {
    globalThis.__logErrorCalls.push(args);
}
""".strip(),
        encoding="utf-8",
    )
    runner_path.write_text(
        """
globalThis.__logErrorCalls = [];
let fetchCalls = 0;
const releases = new Map();
const started = new Map();

async function waitForStarted(url) {
    while (!started.has(url)) {
        await new Promise(resolve => setTimeout(resolve, 0));
    }
}

globalThis.fetch = async (url) => {
    fetchCalls += 1;
    started.set(url, fetchCalls);
    return await new Promise(resolve => {
        releases.set(url, () => {
            resolve({
                ok: true,
                json: async () => ({ url, fetchCalls: started.get(url) }),
            });
        });
    });
};

const { invalidateManagedRequests, requestJsonManaged } = await import('./request.mjs');
const firstBlocker = requestJsonManaged(
    'blocker:1',
    '/api/blocker-1',
    undefined,
    'failed',
    { lane: 'heavy', ttlMs: 0 },
);
const secondBlocker = requestJsonManaged(
    'blocker:2',
    '/api/blocker-2',
    undefined,
    'failed',
    { lane: 'heavy', ttlMs: 0 },
);
await waitForStarted('/api/blocker-1');
await waitForStarted('/api/blocker-2');

const staleQueued = requestJsonManaged(
    'sessions:detail:1',
    '/api/sessions/1',
    undefined,
    'failed',
    { lane: 'heavy', ttlMs: 0 },
);
invalidateManagedRequests('sessions:');
const staleState = await Promise.race([
    staleQueued.then(
        () => 'resolved',
        error => error?.name || 'error',
    ),
    new Promise(resolve => setTimeout(() => resolve('pending'), 25)),
]);

const fresh = requestJsonManaged(
    'sessions:detail:1',
    '/api/sessions/1',
    undefined,
    'failed',
    { lane: 'heavy', ttlMs: 0 },
);
releases.get('/api/blocker-1')();
releases.get('/api/blocker-2')();
await waitForStarted('/api/sessions/1');
releases.get('/api/sessions/1')();
const freshResult = await fresh;
await Promise.allSettled([firstBlocker, secondBlocker, staleQueued]);

console.log(JSON.stringify({
    staleState,
    fetchCalls,
    freshResult,
    logErrorCalls: globalThis.__logErrorCalls.length,
}));
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(runner_path)],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert json.loads(completed.stdout) == {
        "staleState": "AbortError",
        "fetchCalls": 3,
        "freshResult": {"url": "/api/sessions/1", "fetchCalls": 3},
        "logErrorCalls": 0,
    }
