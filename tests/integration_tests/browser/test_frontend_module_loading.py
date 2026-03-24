from __future__ import annotations

from pathlib import Path

import subprocess


def test_subagent_rail_module_loads_with_real_frontend_graph() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    module_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "subagentRail.js"
    )

    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "const noop = () => undefined; "
                "const createClassList = () => ({ add: noop, remove: noop, toggle: noop, contains: () => false }); "
                "const createElement = () => ({"
                "innerHTML: '', textContent: '', value: '', hidden: false, disabled: false, style: {}, dataset: {}, "
                "classList: createClassList(), appendChild: noop, insertBefore: noop, remove: noop, "
                "setAttribute: noop, removeAttribute: noop, addEventListener: noop, removeEventListener: noop, "
                "querySelector: () => null, querySelectorAll: () => [], scrollIntoView: noop, focus: noop"
                "}); "
                "globalThis.window = globalThis; "
                "Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { language: 'en-US', clipboard: { writeText: async () => undefined } } }); "
                "Object.defineProperty(globalThis, 'location', { configurable: true, value: { origin: 'http://127.0.0.1:8000' } }); "
                "globalThis.matchMedia = () => ({ matches: false, addEventListener: noop, removeEventListener: noop }); "
                "globalThis.ResizeObserver = class ResizeObserver { observe() { return undefined; } unobserve() { return undefined; } disconnect() { return undefined; } }; "
                "globalThis.MutationObserver = class MutationObserver { observe() { return undefined; } disconnect() { return undefined; } }; "
                "globalThis.CustomEvent = class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail ?? null; } }; "
                "globalThis.EventSource = class EventSource { constructor() { this.readyState = 1; } close() { return undefined; } addEventListener() { return undefined; } removeEventListener() { return undefined; } }; "
                "globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({}), text: async () => '' }); "
                "globalThis.localStorage = { getItem: () => null, setItem: noop, removeItem: noop }; "
                "globalThis.sessionStorage = { getItem: () => null, setItem: noop, removeItem: noop }; "
                "const listeners = new Map(); "
                "globalThis.document = { "
                "body: createElement(), documentElement: createElement(), visibilityState: 'visible', "
                "getElementById: () => null, querySelector: () => null, querySelectorAll: () => [], "
                "createElement, addEventListener(type, listener) { "
                "if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(listener); "
                "}, "
                "removeEventListener(type, listener) { "
                "const next = (listeners.get(type) || []).filter(item => item !== listener); listeners.set(type, next); "
                "}, "
                "dispatchEvent(event) { (listeners.get(event.type) || []).forEach(listener => listener(event)); return true; } "
                "}; "
                f"const mod = await import({module_path.as_uri()!r}); "
                "console.log(typeof mod.initializeSubagentRail);"
            ),
        ],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node import failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    assert completed.stdout.strip() == "function"
