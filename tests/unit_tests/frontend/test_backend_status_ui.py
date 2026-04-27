# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess


def test_backend_status_fallback_confirms_main_liveness(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    backend_status_source = (
        repo_root / "frontend" / "dist" / "js" / "utils" / "backendStatus.js"
    ).read_text(encoding="utf-8")
    module_source = backend_status_source.replace(
        "import { els } from './dom.js';",
        "const els = globalThis.__backendStatusEls;",
    ).replace(
        "import { t } from './i18n.js';",
        "const t = key => key;",
    )
    module_path = tmp_path / "backendStatus.test.mjs"
    module_path.write_text(module_source, encoding="utf-8")
    runner_path = tmp_path / "runner.mjs"
    runner_path.write_text(
        """
const classNames = new Set();
const backendStatusEl = {
    classList: {
        remove: (...names) => names.forEach(name => classNames.delete(name)),
        add: name => classNames.add(name),
    },
    dataset: {},
    title: '',
    textContent: '',
};
const backendStatusLabel = { textContent: '' };
const storage = new Map();
const calls = [];

globalThis.__backendStatusEls = {
    backendStatus: backendStatusEl,
    backendStatusLabel,
};
globalThis.window = {
    location: new URL('http://127.0.0.1:8000/'),
    localStorage: {
        getItem: key => storage.get(key) || null,
        setItem: (key, value) => storage.set(key, value),
        removeItem: key => storage.delete(key),
    },
    setTimeout: globalThis.setTimeout.bind(globalThis),
    clearTimeout: globalThis.clearTimeout.bind(globalThis),
    setInterval: globalThis.setInterval.bind(globalThis),
};
globalThis.fetch = async url => {
    const safeUrl = String(url);
    calls.push(safeUrl);
    if (safeUrl === '/api/system/control-plane') {
        return { ok: false, json: async () => ({}) };
    }
    if (safeUrl === 'http://127.0.0.1:8001/live') {
        return {
            ok: true,
            json: async () => ({
                status: 'alive',
                main_base_url: 'http://127.0.0.1:8000',
            }),
        };
    }
    if (safeUrl === '/api/system/live') {
        return { ok: true, json: async () => ({ status: 'alive' }) };
    }
    throw new Error(`unexpected fetch: ${safeUrl}`);
};

const backendStatus = await import('./backendStatus.test.mjs');
const result = await backendStatus.refreshBackendStatus({ force: true });

console.log(JSON.stringify({
    calls,
    classNames: Array.from(classNames).sort(),
    label: backendStatusLabel.textContent,
    result,
    status: backendStatus.getBackendStatus(),
    storedUrl: storage.get('relayTeams.controlPlaneLiveUrl') || null,
}));
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", str(runner_path)],
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    payload = json.loads(result.stdout)

    assert payload["result"] is True
    assert payload["status"] == "online"
    assert payload["classNames"] == ["online"]
    assert payload["label"] == "backend.status.connected"
    assert payload["storedUrl"] == "http://127.0.0.1:8001/live"
    assert payload["calls"] == [
        "/api/system/control-plane",
        "http://127.0.0.1:8001/live",
        "/api/system/live",
    ]
