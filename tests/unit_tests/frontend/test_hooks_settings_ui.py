# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import cast


def test_hooks_settings_panel_renders_loaded_hooks(tmp_path: Path) -> None:
    payload = _run_hooks_settings_script(
        tmp_path=tmp_path,
        hooks_config={
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Write",
                        "role_ids": ["coordinator"],
                        "session_modes": ["normal"],
                        "run_kinds": ["foreground"],
                        "hooks": [
                            {
                                "type": "command",
                                "name": "lint changed files",
                                "if": "Write(*.py)",
                                "command": "python lint.py",
                            }
                        ],
                    }
                ]
            }
        },
        runtime_view={
            "sources": [
                {"scope": "project", "path": "/workspace/.relay-teams/hooks.json"}
            ],
            "loaded_hooks": [
                {
                    "name": "python policy.py",
                    "handler_type": "command",
                    "event_name": "PreToolUse",
                    "matcher": "shell",
                    "if": "Bash(git *)",
                    "role_ids": ["coordinator"],
                    "session_modes": ["normal"],
                    "run_kinds": ["foreground"],
                    "timeout_seconds": 5.0,
                    "run_async": False,
                    "on_error": "ignore",
                    "source": {
                        "scope": "project",
                        "path": "/workspace/.relay-teams/hooks.json",
                    },
                }
            ],
        },
    )

    html = cast(str, payload["html"])
    assert "lint changed files" in html
    assert '<div class="mcp-status-card-name">lint changed files</div>' in html
    assert "Edit" in html
    assert "Delete Hook" in html
    assert "python policy.py" in html
    assert "PreToolUse" in html
    assert "mcp-status-toolbar" not in html
    assert "source files" not in html
    assert "shell" in html
    assert "command" in html
    assert ">Handler Count</div>" in html
    assert ">1</div>" in html
    assert "If Rule" in html
    assert "Bash(git *)" in html
    assert "/workspace/.relay-teams/hooks.json" not in html
    assert "mcp-status-card hooks-runtime-card" in html
    assert "hooks-runtime-detail-list status-list" in html
    assert "hooks-runtime-detail-row status-list-row" in html
    assert "hooks-runtime-detail-item status-list-copy" in html
    assert "hooks-runtime-detail-label status-list-name" in html
    assert "hooks-runtime-detail-value status-list-description" in html
    assert "hooks-runtime-overview-table" not in html


def test_hooks_settings_panel_groups_multiple_matchers_under_one_event(
    tmp_path: Path,
) -> None:
    payload = _run_hooks_settings_script(
        tmp_path=tmp_path,
        hooks_config={
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Write",
                        "hooks": [
                            {
                                "type": "command",
                                "name": "lint changed files",
                                "command": "python lint.py",
                            }
                        ],
                    },
                    {
                        "matcher": "Edit",
                        "hooks": [
                            {
                                "type": "command",
                                "name": "format changed files",
                                "command": "python format.py",
                            }
                        ],
                    },
                ]
            }
        },
        runtime_view={"sources": [], "loaded_hooks": []},
    )

    html = cast(str, payload["html"])
    assert html.count("<h5>PreToolUse</h5>") == 1
    assert "lint changed files" in html
    assert "format changed files" in html


def test_hooks_settings_panel_renders_empty_and_error_states(tmp_path: Path) -> None:
    empty_payload = _run_hooks_settings_script(
        tmp_path=tmp_path / "empty",
        hooks_config={"hooks": {}},
        runtime_view={"sources": [], "loaded_hooks": []},
    )
    assert "No hooks configured" in str(empty_payload["html"])

    error_payload = _run_hooks_settings_script(
        tmp_path=tmp_path / "error",
        hooks_config=None,
        runtime_view=None,
        error_message="boom",
    )
    assert "Load Failed" in str(error_payload["html"])
    assert "boom" in str(error_payload["html"])


def test_hooks_settings_panel_keeps_editor_when_runtime_view_load_fails(
    tmp_path: Path,
) -> None:
    payload = _run_hooks_settings_script(
        tmp_path=tmp_path,
        hooks_config={
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Write",
                        "hooks": [
                            {
                                "type": "command",
                                "name": "lint changed files",
                                "command": "python lint.py",
                            }
                        ],
                    }
                ]
            }
        },
        runtime_view=None,
        api_source="""
export async function fetchHooksConfig() {
    return {
        hooks: {
            PreToolUse: [
                {
                    matcher: 'Write',
                    hooks: [
                        {
                            type: 'command',
                            name: 'lint changed files',
                            command: 'python lint.py',
                        },
                    ],
                },
            ],
        },
    };
}

export async function fetchHookRuntimeView() {
    throw new Error('runtime exploded');
}

export async function saveHooksConfig() {
    return { status: 'ok' };
}

export async function validateHooksConfig() {
    return { status: 'ok' };
}
""",
    )

    html = cast(str, payload["html"])
    assert "lint changed files" in html
    assert "Runtime View Unavailable" in html
    assert "runtime exploded" in html
    assert "Load Failed" not in html


def test_hooks_settings_panel_ignores_out_of_order_load_results(tmp_path: Path) -> None:
    payload = _run_hooks_settings_script(
        tmp_path=tmp_path,
        hooks_config=None,
        runtime_view=None,
        api_source="""
let callCount = 0;

export async function fetchHooksConfig() {
    return { hooks: {} };
}

export async function fetchHookRuntimeView() {
    callCount += 1;
    if (callCount === 1) {
        await new Promise(resolve => setTimeout(resolve, 30));
        return {
            sources: [{ scope: 'project', path: '/workspace/.relay-teams/hooks-old.json' }],
            loaded_hooks: [{ name: 'stale hook', handler_type: 'command', event_name: 'PreToolUse', matcher: 'shell', source: { scope: 'project', path: '/workspace/.relay-teams/hooks-old.json' } }],
        };
    }
    await new Promise(resolve => setTimeout(resolve, 5));
    return {
        sources: [{ scope: 'project', path: '/workspace/.relay-teams/hooks-new.json' }],
        loaded_hooks: [{ name: 'fresh hook', handler_type: 'command', event_name: 'PreToolUse', matcher: 'shell', source: { scope: 'project', path: '/workspace/.relay-teams/hooks-new.json' } }],
    };
}

export async function saveHooksConfig() {
    return { status: 'ok' };
}

export async function validateHooksConfig() {
    return { status: 'ok' };
}
""",
        runner_source="""
const host = { innerHTML: '' };
globalThis.document = {
    getElementById(id) {
        return id === 'hooks-runtime-status' ? host : null;
    },
};

const { bindHooksSettingsHandlers, loadHooksSettingsPanel } = await import('./hooksSettings.mjs');
bindHooksSettingsHandlers();
const firstLoad = loadHooksSettingsPanel();
const secondLoad = loadHooksSettingsPanel();
await Promise.all([firstLoad, secondLoad]);
console.log(JSON.stringify({ html: host.innerHTML }));
""",
    )

    html = cast(str, payload["html"])
    assert "fresh hook" in html
    assert "stale hook" not in html
    assert "Loading loaded hooks..." not in html


def test_hooks_settings_panel_switches_card_into_edit_mode(tmp_path: Path) -> None:
    payload = _run_hooks_settings_script(
        tmp_path=tmp_path,
        hooks_config={
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Write",
                        "hooks": [
                            {
                                "type": "command",
                                "name": "lint changed files",
                                "command": "python lint.py",
                            }
                        ],
                    }
                ]
            }
        },
        runtime_view={"sources": [], "loaded_hooks": []},
        runner_source="""
const listeners = {};
const host = { innerHTML: '' };
globalThis.document = {
    addEventListener(name, handler) {
        listeners[name] = handler;
    },
    getElementById(id) {
        return id === 'hooks-runtime-status' ? host : null;
    },
};

const { bindHooksSettingsHandlers, loadHooksSettingsPanel } = await import('./hooksSettings.mjs');
bindHooksSettingsHandlers();
await loadHooksSettingsPanel();

await listeners.click({
    target: {
        closest(selector) {
            if (selector === '[data-hooks-action]') {
                return { dataset: { hooksAction: 'edit-group', groupId: '1' } };
            }
            return null;
        },
    },
});

console.log(JSON.stringify({ html: host.innerHTML }));
""",
    )

    html = cast(str, payload["html"])
    assert "Delete Handler" in html
    assert "Command" in html
    assert "Timeout" in html
    assert html.index(">Name</label>") < html.index(">Type</label>")
    assert "settings-checkbox-field" not in html
    assert ">Roles</label>" not in html
    assert ">Session Modes</label>" not in html
    assert ">Run Kinds</label>" not in html
    assert 'data-hooks-field="event_name"' not in html
    assert '<div class="status-list-description">PreToolUse</div>' in html


def test_hooks_settings_multiline_fields_use_shared_textarea_style(
    tmp_path: Path,
) -> None:
    payload = _run_hooks_settings_script(
        tmp_path=tmp_path,
        hooks_config={
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Write",
                        "hooks": [
                            {
                                "type": "http",
                                "name": "notify endpoint",
                                "url": "https://example.test/hook",
                                "headers": {"Authorization": "Bearer test"},
                            }
                        ],
                    }
                ]
            }
        },
        runtime_view={"sources": [], "loaded_hooks": []},
        runner_source="""
const listeners = {};
const host = { innerHTML: '' };
globalThis.document = {
    addEventListener(name, handler) {
        listeners[name] = handler;
    },
    getElementById(id) {
        return id === 'hooks-runtime-status' ? host : null;
    },
};

const { bindHooksSettingsHandlers, loadHooksSettingsPanel } = await import('./hooksSettings.mjs');
bindHooksSettingsHandlers();
await loadHooksSettingsPanel();

await listeners.click({
    target: {
        closest(selector) {
            if (selector === '[data-hooks-action]') {
                return { dataset: { hooksAction: 'edit-group', groupId: '1' } };
            }
            return null;
        },
    },
});

console.log(JSON.stringify({ html: host.innerHTML }));
""",
    )

    html = cast(str, payload["html"])
    assert 'class="config-textarea"' in html


def test_hooks_settings_agent_editor_renders_prompt_field(tmp_path: Path) -> None:
    payload = _run_hooks_settings_script(
        tmp_path=tmp_path,
        hooks_config={
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "agent",
                                "name": "verify output",
                                "role_id": "Reviewer",
                                "prompt": "review the final answer",
                                "model": "gpt-test",
                            }
                        ],
                    }
                ]
            }
        },
        runtime_view={"sources": [], "loaded_hooks": []},
        runner_source="""
const listeners = {};
const host = { innerHTML: '' };
globalThis.document = {
    addEventListener(name, handler) {
        listeners[name] = handler;
    },
    getElementById(id) {
        return id === 'hooks-runtime-status' ? host : null;
    },
};

const { bindHooksSettingsHandlers, loadHooksSettingsPanel } = await import('./hooksSettings.mjs');
bindHooksSettingsHandlers();
await loadHooksSettingsPanel();

await listeners.click({
    target: {
        closest(selector) {
            if (selector === '[data-hooks-action]') {
                return { dataset: { hooksAction: 'edit-group', groupId: '1' } };
            }
            return null;
        },
    },
});

console.log(JSON.stringify({ html: host.innerHTML }));
""",
    )

    html = cast(str, payload["html"])
    assert 'data-hooks-field="role_id"' in html
    assert 'data-hooks-field="prompt"' in html
    assert "review the final answer" in html


def test_hooks_settings_new_card_allows_event_selection(tmp_path: Path) -> None:
    payload = _run_hooks_settings_script(
        tmp_path=tmp_path,
        hooks_config={"hooks": {}},
        runtime_view={"sources": [], "loaded_hooks": []},
        runner_source="""
const host = { innerHTML: '' };
globalThis.document = {
    addEventListener() {},
    getElementById(id) {
        if (id === 'hooks-runtime-status') return host;
        if (id === 'add-hook-btn') return { addEventListener(type, handler) { globalThis.__addHookClick = handler; } };
        if (id === 'validate-hooks-btn') return { addEventListener() {} };
        if (id === 'save-hooks-btn') return { addEventListener() {} };
        return null;
    },
};

const { bindHooksSettingsHandlers, loadHooksSettingsPanel } = await import('./hooksSettings.mjs');
bindHooksSettingsHandlers();
await loadHooksSettingsPanel();
globalThis.__addHookClick();
console.log(JSON.stringify({ html: host.innerHTML }));
""",
    )

    html = cast(str, payload["html"])
    assert 'data-hooks-field="event_name"' in html


def test_hooks_settings_cancel_reverts_unsaved_changes(tmp_path: Path) -> None:
    payload = _run_hooks_settings_script(
        tmp_path=tmp_path,
        hooks_config={
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Write",
                        "hooks": [
                            {
                                "type": "command",
                                "name": "lint changed files",
                                "command": "python lint.py",
                            }
                        ],
                    }
                ]
            }
        },
        runtime_view={"sources": [], "loaded_hooks": []},
        runner_source="""
const listeners = {};
const host = { innerHTML: '' };
globalThis.document = {
    addEventListener(name, handler) {
        listeners[name] = handler;
    },
    getElementById(id) {
        return id === 'hooks-runtime-status' ? host : null;
    },
};

const { bindHooksSettingsHandlers, loadHooksSettingsPanel } = await import('./hooksSettings.mjs');
bindHooksSettingsHandlers();
await loadHooksSettingsPanel();

await listeners.click({
    target: {
        closest(selector) {
            if (selector === '[data-hooks-action]') {
                return { dataset: { hooksAction: 'edit-group', groupId: '1' } };
            }
            return null;
        },
    },
});

listeners.input({
    target: {
        dataset: { hooksField: 'name', groupId: '1', handlerId: '1' },
        value: 'edited name',
    },
});

await listeners.click({
    target: {
        closest(selector) {
            if (selector === '[data-hooks-action]') {
                return { dataset: { hooksAction: 'edit-group', groupId: '1' } };
            }
            return null;
        },
    },
});

console.log(JSON.stringify({ html: host.innerHTML }));
""",
    )

    html = cast(str, payload["html"])
    assert "edited name" not in html
    assert "lint changed files" in html


def test_hooks_settings_cancel_discards_unsaved_new_card(tmp_path: Path) -> None:
    payload = _run_hooks_settings_script(
        tmp_path=tmp_path,
        hooks_config={"hooks": {}},
        runtime_view={"sources": [], "loaded_hooks": []},
        runner_source="""
const listeners = {};
const host = { innerHTML: '' };
globalThis.document = {
    addEventListener(name, handler) {
        listeners[name] = handler;
    },
    getElementById(id) {
        if (id === 'hooks-runtime-status') return host;
        if (id === 'add-hook-btn') return { addEventListener(type, handler) { globalThis.__addHookClick = handler; } };
        if (id === 'validate-hooks-btn') return { addEventListener() {} };
        if (id === 'save-hooks-btn') return { addEventListener() {} };
        return null;
    },
};

const { bindHooksSettingsHandlers, loadHooksSettingsPanel } = await import('./hooksSettings.mjs');
bindHooksSettingsHandlers();
await loadHooksSettingsPanel();
globalThis.__addHookClick();

await listeners.click({
    target: {
        closest(selector) {
            if (selector === '[data-hooks-action]') {
                return { dataset: { hooksAction: 'edit-group', groupId: '1' } };
            }
            return null;
        },
    },
});

console.log(JSON.stringify({ html: host.innerHTML }));
""",
    )

    html = cast(str, payload["html"])
    assert "No hooks configured" in html


def test_hooks_settings_text_input_does_not_rerender_panel(tmp_path: Path) -> None:
    payload = _run_hooks_settings_script(
        tmp_path=tmp_path,
        hooks_config={
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Write",
                        "hooks": [
                            {
                                "type": "command",
                                "name": "lint changed files",
                                "command": "python lint.py",
                            }
                        ],
                    }
                ]
            }
        },
        runtime_view={"sources": [], "loaded_hooks": []},
        runner_source="""
const listeners = {};
let renderCount = 0;
const host = {
    _innerHTML: '',
    get innerHTML() {
        return this._innerHTML;
    },
    set innerHTML(value) {
        renderCount += 1;
        this._innerHTML = value;
    },
};
globalThis.document = {
    addEventListener(name, handler) {
        listeners[name] = handler;
    },
    getElementById(id) {
        return id === 'hooks-runtime-status' ? host : null;
    },
};

const { bindHooksSettingsHandlers, loadHooksSettingsPanel } = await import('./hooksSettings.mjs');
bindHooksSettingsHandlers();
await loadHooksSettingsPanel();
await listeners.click({
    target: {
        closest(selector) {
            if (selector === '[data-hooks-action]') {
                return { dataset: { hooksAction: 'edit-group', groupId: '1' } };
            }
            return null;
        },
    },
});

renderCount = 0;
listeners.input({
    target: {
        dataset: { hooksField: 'name', groupId: '1', handlerId: '1' },
        value: 'edited name',
    },
});

console.log(JSON.stringify({ renderCount }));
""",
    )

    assert payload["renderCount"] == 0


def test_hooks_settings_validate_shows_result_dialog(tmp_path: Path) -> None:
    payload = _run_hooks_settings_script(
        tmp_path=tmp_path,
        hooks_config={"hooks": {}},
        runtime_view={"sources": [], "loaded_hooks": []},
        runner_source="""
const listeners = {};
const host = { innerHTML: '' };
globalThis.document = {
    addEventListener(name, handler) {
        listeners[name] = handler;
    },
    getElementById(id) {
        if (id === 'hooks-runtime-status') return host;
        if (id === 'validate-hooks-btn') return { addEventListener(type, handler) { globalThis.__validateClick = handler; } };
        if (id === 'add-hook-btn') return { addEventListener() {} };
        if (id === 'save-hooks-btn') return { addEventListener() {} };
        return null;
    },
};

globalThis.__feedbackCalls = [];
const { bindHooksSettingsHandlers, loadHooksSettingsPanel } = await import('./hooksSettings.mjs');
bindHooksSettingsHandlers();
await loadHooksSettingsPanel();
await globalThis.__validateClick();
await new Promise(resolve => setTimeout(resolve, 0));
console.log(JSON.stringify({ feedbackCalls: globalThis.__feedbackCalls, html: host.innerHTML }));
""",
    )

    feedback_calls = cast(list[dict[str, object]], payload["feedbackCalls"])
    assert feedback_calls == [
        {
            "title": "Validation Result",
            "message": "Hooks config is valid.",
            "tone": "success",
        }
    ]


def test_hooks_settings_save_failure_shows_result_dialog(tmp_path: Path) -> None:
    payload = _run_hooks_settings_script(
        tmp_path=tmp_path,
        hooks_config={"hooks": {}},
        runtime_view={"sources": [], "loaded_hooks": []},
        api_source="""
export async function fetchHooksConfig() {
    return { hooks: {} };
}

export async function fetchHookRuntimeView() {
    return { sources: [], loaded_hooks: [] };
}

export async function saveHooksConfig() {
    throw new Error('save exploded');
}

export async function validateHooksConfig() {
    return { status: 'ok' };
}
""",
        runner_source="""
const host = { innerHTML: '' };
globalThis.document = {
    addEventListener() {},
    getElementById(id) {
        if (id === 'hooks-runtime-status') return host;
        if (id === 'validate-hooks-btn') return { addEventListener() {} };
        if (id === 'add-hook-btn') return { addEventListener() {} };
        if (id === 'save-hooks-btn') return { addEventListener(type, handler) { globalThis.__saveClick = handler; } };
        return null;
    },
};

globalThis.__feedbackCalls = [];
const { bindHooksSettingsHandlers, loadHooksSettingsPanel } = await import('./hooksSettings.mjs');
bindHooksSettingsHandlers();
await loadHooksSettingsPanel();
await globalThis.__saveClick();
await new Promise(resolve => setTimeout(resolve, 0));
console.log(JSON.stringify({ feedbackCalls: globalThis.__feedbackCalls, html: host.innerHTML }));
""",
    )

    feedback_calls = cast(list[dict[str, object]], payload["feedbackCalls"])
    assert feedback_calls == [
        {
            "title": "Save Result",
            "message": "Failed to save hooks config: save exploded",
            "tone": "error",
        }
    ]


def test_hooks_settings_save_success_is_not_blocked_by_runtime_refresh_failure(
    tmp_path: Path,
) -> None:
    payload = _run_hooks_settings_script(
        tmp_path=tmp_path,
        hooks_config={"hooks": {}},
        runtime_view={"sources": [], "loaded_hooks": []},
        api_source="""
let runtimeCalls = 0;

export async function fetchHooksConfig() {
    return { hooks: {} };
}

export async function fetchHookRuntimeView() {
    runtimeCalls += 1;
    if (runtimeCalls > 1) {
        throw new Error('runtime refresh exploded');
    }
    return { sources: [], loaded_hooks: [] };
}

export async function saveHooksConfig() {
    return { status: 'ok' };
}

export async function validateHooksConfig() {
    return { status: 'ok' };
}
""",
        runner_source="""
const host = { innerHTML: '' };
globalThis.document = {
    addEventListener() {},
    getElementById(id) {
        if (id === 'hooks-runtime-status') return host;
        if (id === 'validate-hooks-btn') return { addEventListener() {} };
        if (id === 'add-hook-btn') return { addEventListener() {} };
        if (id === 'save-hooks-btn') return { addEventListener(type, handler) { globalThis.__saveClick = handler; } };
        return null;
    },
};

globalThis.__feedbackCalls = [];
const { bindHooksSettingsHandlers, loadHooksSettingsPanel } = await import('./hooksSettings.mjs');
bindHooksSettingsHandlers();
await loadHooksSettingsPanel();
await globalThis.__saveClick();
await new Promise(resolve => setTimeout(resolve, 0));
console.log(JSON.stringify({ feedbackCalls: globalThis.__feedbackCalls, html: host.innerHTML }));
""",
    )

    feedback_calls = cast(list[dict[str, object]], payload["feedbackCalls"])
    assert feedback_calls == [
        {
            "title": "Save Result",
            "message": "Hooks config saved.",
            "tone": "success",
        }
    ]
    html = cast(str, payload["html"])
    assert "Runtime View Unavailable" in html
    assert "runtime refresh exploded" in html


def test_hooks_settings_serializes_agent_prompt_on_save(tmp_path: Path) -> None:
    payload = _run_hooks_settings_script(
        tmp_path=tmp_path,
        hooks_config={
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "agent",
                                "name": "verify output",
                                "role_id": "Reviewer",
                                "prompt": "review the final answer",
                                "model": "gpt-test",
                            }
                        ],
                    }
                ]
            }
        },
        runtime_view={"sources": [], "loaded_hooks": []},
        api_source="""
export async function fetchHooksConfig() {
    return {
        hooks: {
            Stop: [
                {
                    hooks: [
                        {
                            type: 'agent',
                            name: 'verify output',
                            role_id: 'Reviewer',
                            prompt: 'review the final answer',
                            model: 'gpt-test',
                        },
                    ],
                },
            ],
        },
    };
}

export async function fetchHookRuntimeView() {
    return { sources: [], loaded_hooks: [] };
}

export async function saveHooksConfig(payload) {
    globalThis.__savedPayload = payload;
    return { status: 'ok' };
}

export async function validateHooksConfig() {
    return { status: 'ok' };
}
""",
        runner_source="""
const host = { innerHTML: '' };
globalThis.document = {
    addEventListener() {},
    getElementById(id) {
        if (id === 'hooks-runtime-status') return host;
        if (id === 'validate-hooks-btn') return { addEventListener() {} };
        if (id === 'add-hook-btn') return { addEventListener() {} };
        if (id === 'save-hooks-btn') return { addEventListener(type, handler) { globalThis.__saveClick = handler; } };
        return null;
    },
};

globalThis.__feedbackCalls = [];
const { bindHooksSettingsHandlers, loadHooksSettingsPanel } = await import('./hooksSettings.mjs');
bindHooksSettingsHandlers();
await loadHooksSettingsPanel();
await globalThis.__saveClick();
await new Promise(resolve => setTimeout(resolve, 0));
console.log(JSON.stringify({ savedPayload: globalThis.__savedPayload }));
""",
    )

    saved_payload = cast(dict[str, object], payload["savedPayload"])
    saved_hooks = cast(dict[str, object], saved_payload["hooks"])
    stop_groups = cast(list[dict[str, object]], saved_hooks["Stop"])
    first_stop_group = cast(dict[str, object], stop_groups[0])
    handlers = cast(list[dict[str, object]], first_stop_group["hooks"])
    assert handlers == [
        {
            "type": "agent",
            "name": "verify output",
            "timeout": 5,
            "async": False,
            "on_error": "ignore",
            "role_id": "Reviewer",
            "prompt": "review the final answer",
            "model": "gpt-test",
        }
    ]


def test_hooks_settings_validate_failure_shows_invalid_headers_reason(
    tmp_path: Path,
) -> None:
    payload = _run_hooks_settings_script(
        tmp_path=tmp_path,
        hooks_config={
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Write",
                        "hooks": [
                            {
                                "type": "http",
                                "name": "notify",
                                "url": "https://example.invalid/hook",
                                "headers": {"Authorization": "Bearer test"},
                            }
                        ],
                    }
                ]
            }
        },
        runtime_view={"sources": [], "loaded_hooks": []},
        runner_source="""
const listeners = {};
const host = { innerHTML: '' };
globalThis.document = {
    addEventListener(name, handler) {
        listeners[name] = handler;
    },
    getElementById(id) {
        if (id === 'hooks-runtime-status') return host;
        if (id === 'validate-hooks-btn') return { addEventListener(type, handler) { globalThis.__validateClick = handler; } };
        if (id === 'add-hook-btn') return { addEventListener() {} };
        if (id === 'save-hooks-btn') return { addEventListener() {} };
        return null;
    },
};

globalThis.__feedbackCalls = [];
const { bindHooksSettingsHandlers, loadHooksSettingsPanel } = await import('./hooksSettings.mjs');
bindHooksSettingsHandlers();
await loadHooksSettingsPanel();
await listeners.click({
    target: {
        closest(selector) {
            if (selector === '[data-hooks-action]') {
                return { dataset: { hooksAction: 'edit-group', groupId: '1' } };
            }
            return null;
        },
    },
});
listeners.input({
    target: {
        dataset: { hooksField: 'headers', groupId: '1', handlerId: '1' },
        value: '{"Authorization": }',
        type: 'textarea',
    },
});
await globalThis.__validateClick();
await new Promise(resolve => setTimeout(resolve, 0));
console.log(JSON.stringify({ feedbackCalls: globalThis.__feedbackCalls }));
""",
    )

    feedback_calls = cast(list[dict[str, object]], payload["feedbackCalls"])
    assert feedback_calls == [
        {
            "title": "Validation Result",
            "message": "Failed to validate hooks config: Headers JSON must be a valid object with string values.",
            "tone": "error",
        }
    ]


def test_hooks_settings_validate_failure_uses_structured_detail_reason(
    tmp_path: Path,
) -> None:
    payload = _run_hooks_settings_script(
        tmp_path=tmp_path,
        hooks_config={"hooks": {}},
        runtime_view={"sources": [], "loaded_hooks": []},
        api_source="""
export async function fetchHooksConfig() {
    return { hooks: {} };
}

export async function fetchHookRuntimeView() {
    return { sources: [], loaded_hooks: [] };
}

export async function saveHooksConfig() {
    return { status: 'ok' };
}

export async function validateHooksConfig() {
    const error = new Error('generic failure');
    error.detail = [{ loc: ['hooks', 'PreToolUse', 0, 'hooks', 0, 'command'], msg: 'Field required' }];
    throw error;
}
""",
        runner_source="""
const host = { innerHTML: '' };
globalThis.document = {
    addEventListener() {},
    getElementById(id) {
        if (id === 'hooks-runtime-status') return host;
        if (id === 'validate-hooks-btn') return { addEventListener(type, handler) { globalThis.__validateClick = handler; } };
        if (id === 'add-hook-btn') return { addEventListener() {} };
        if (id === 'save-hooks-btn') return { addEventListener() {} };
        return null;
    },
};

globalThis.__feedbackCalls = [];
const { bindHooksSettingsHandlers, loadHooksSettingsPanel } = await import('./hooksSettings.mjs');
bindHooksSettingsHandlers();
await loadHooksSettingsPanel();
await globalThis.__validateClick();
await new Promise(resolve => setTimeout(resolve, 0));
console.log(JSON.stringify({ feedbackCalls: globalThis.__feedbackCalls }));
""",
    )

    feedback_calls = cast(list[dict[str, object]], payload["feedbackCalls"])
    assert feedback_calls == [
        {
            "title": "Validation Result",
            "message": "Failed to validate hooks config: hooks.PreToolUse.0.hooks.0.command: Field required",
            "tone": "error",
        }
    ]


def test_hooks_settings_save_failure_uses_structured_detail_reason(
    tmp_path: Path,
) -> None:
    payload = _run_hooks_settings_script(
        tmp_path=tmp_path,
        hooks_config={"hooks": {}},
        runtime_view={"sources": [], "loaded_hooks": []},
        api_source="""
export async function fetchHooksConfig() {
    return { hooks: {} };
}

export async function fetchHookRuntimeView() {
    return { sources: [], loaded_hooks: [] };
}

export async function saveHooksConfig() {
    const error = new Error('generic failure');
    error.detail = [{ loc: ['hooks', 'PreToolUse', 0, 'matcher'], msg: 'Matcher is not supported for this event' }];
    throw error;
}

export async function validateHooksConfig() {
    return { status: 'ok' };
}
""",
        runner_source="""
const host = { innerHTML: '' };
globalThis.document = {
    addEventListener() {},
    getElementById(id) {
        if (id === 'hooks-runtime-status') return host;
        if (id === 'validate-hooks-btn') return { addEventListener() {} };
        if (id === 'add-hook-btn') return { addEventListener() {} };
        if (id === 'save-hooks-btn') return { addEventListener(type, handler) { globalThis.__saveClick = handler; } };
        return null;
    },
};

globalThis.__feedbackCalls = [];
const { bindHooksSettingsHandlers, loadHooksSettingsPanel } = await import('./hooksSettings.mjs');
bindHooksSettingsHandlers();
await loadHooksSettingsPanel();
await globalThis.__saveClick();
await new Promise(resolve => setTimeout(resolve, 0));
console.log(JSON.stringify({ feedbackCalls: globalThis.__feedbackCalls }));
""",
    )

    feedback_calls = cast(list[dict[str, object]], payload["feedbackCalls"])
    assert feedback_calls == [
        {
            "title": "Save Result",
            "message": "Failed to save hooks config: hooks.PreToolUse.0.matcher: Matcher is not supported for this event",
            "tone": "error",
        }
    ]


def _run_hooks_settings_script(
    *,
    tmp_path: Path,
    hooks_config: dict[str, object] | None,
    runtime_view: dict[str, object] | None,
    error_message: str | None = None,
    api_source: str | None = None,
    runner_source: str | None = None,
) -> dict[str, object]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "settings"
        / "hooksSettings.js"
    )
    source = source_path.read_text(encoding="utf-8")
    source = source.replace("../../core/api.js", "./api.mjs")
    source = source.replace("../../utils/feedback.js", "./feedback.mjs")
    source = source.replace("../../utils/i18n.js", "./i18n.mjs")
    source = source.replace("../../utils/logger.js", "./logger.mjs")

    (tmp_path / "hooksSettings.mjs").write_text(source, encoding="utf-8")
    if api_source is not None:
        api_module_source = api_source.strip()
    elif error_message is None:
        api_module_source = f"""
export async function fetchHooksConfig() {{
    return {json.dumps(hooks_config)};
}}

export async function fetchHookRuntimeView() {{
    return {json.dumps(runtime_view)};
}}

export async function saveHooksConfig() {{
    return {{ status: 'ok' }};
}}

export async function validateHooksConfig() {{
    return {{ status: 'ok' }};
}}
"""
    else:
        api_module_source = f"""
export async function fetchHooksConfig() {{
    throw new Error({json.dumps(error_message)});
}}

export async function fetchHookRuntimeView() {{
    throw new Error({json.dumps(error_message)});
}}

export async function saveHooksConfig() {{
    throw new Error({json.dumps(error_message)});
}}

export async function validateHooksConfig() {{
    throw new Error({json.dumps(error_message)});
}}
"""
    (tmp_path / "api.mjs").write_text(api_module_source.strip(), encoding="utf-8")
    (tmp_path / "logger.mjs").write_text(
        """
export function errorToPayload(error) {
    return { message: error?.message || '' };
}

export function logError() {}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "feedback.mjs").write_text(
        """
export async function showAlertDialog({ title = '', message = '', tone = 'info' } = {}) {
    globalThis.__feedbackCalls = globalThis.__feedbackCalls || [];
    globalThis.__feedbackCalls.push({ title, message, tone });
    return true;
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "i18n.mjs").write_text(
        """
const STRINGS = {
    'settings.hooks.summary': '{count} loaded hooks across {source_count} source files',
    'settings.hooks.summary_no_sources': '{count} hooks',
    'settings.hooks.loading': 'Loading loaded hooks...',
    'settings.hooks.none': 'No hooks loaded',
    'settings.hooks.none_copy': 'No hook config files are currently contributing runtime hooks for this workspace.',
    'settings.hooks.load_failed': 'Load Failed',
    'settings.hooks.load_failed_detail': 'Unable to load runtime hooks: {error}',
    'settings.hooks.runtime_load_failed': 'Runtime View Unavailable',
    'settings.hooks.runtime_load_failed_detail': 'Unable to load runtime hook view: {error}',
    'settings.hooks.name': 'Name',
    'settings.hooks.trigger': 'Trigger',
    'settings.hooks.matcher': 'Matcher',
    'settings.hooks.type': 'Type',
    'settings.hooks.scope': 'Scope',
    'settings.hooks.if_rule': 'If Rule',
    'settings.hooks.role_ids': 'Roles',
    'settings.hooks.session_modes': 'Session Modes',
    'settings.hooks.run_kinds': 'Run Kinds',
    'settings.hooks.all': 'All',
    'settings.hooks.unnamed': 'Unnamed hook',
    'settings.hooks.scope_project': 'Project',
    'settings.hooks.scope_project_local': 'Project Local',
    'settings.hooks.scope_user': 'User',
    'settings.hooks.scope_unknown': 'Unknown source',
    'settings.hooks.empty': 'No hooks configured',
    'settings.hooks.empty_copy': 'Add a hook to start building the hooks shown on this page.',
    'settings.hooks.add_group': 'Add Hook',
    'settings.hooks.edit_group': 'Edit',
    'settings.hooks.delete_group': 'Delete Hook',
    'settings.hooks.handlers': 'Handlers',
    'settings.hooks.handler_summary': 'Handler Count',
    'settings.hooks.handlers_count_suffix': 'handlers',
    'settings.hooks.add_handler': 'Add Handler',
    'settings.hooks.delete_handler': 'Delete Handler',
    'settings.hooks.event_name': 'Event',
    'settings.hooks.matcher_not_supported': 'Matcher is not supported for this event.',
    'settings.hooks.csv_placeholder': 'comma,separated,values',
    'settings.hooks.timeout': 'Timeout',
    'settings.hooks.run_async': 'Run Async',
    'settings.hooks.on_error': 'On Error',
    'settings.hooks.command': 'Command',
    'settings.hooks.url': 'URL',
    'settings.hooks.headers': 'Headers JSON',
    'settings.hooks.headers_invalid_json': 'Headers JSON must be a valid object with string values.',
    'settings.hooks.prompt': 'Prompt',
    'settings.hooks.model': 'Model',
    'settings.hooks.role_id': 'Agent Role',
    'settings.hooks.if_not_supported': 'If rules are only supported for tool events.',
    'settings.hooks.enabled': 'Enabled',
    'settings.hooks.disabled': 'Disabled',
    'settings.hooks.validate_success': 'Hooks config is valid.',
    'settings.hooks.validate_failed': 'Failed to validate hooks config.',
    'settings.hooks.validate_failed_detail': 'Failed to validate hooks config: {error}',
    'settings.hooks.validate_result_title': 'Validation Result',
    'settings.hooks.save_success': 'Hooks config saved.',
    'settings.hooks.save_failed': 'Failed to save hooks config.',
    'settings.hooks.save_failed_detail': 'Failed to save hooks config: {error}',
    'settings.hooks.save_result_title': 'Save Result',
    'settings.panel.hooks.title': 'Hooks',
    'settings.panel.hooks.description': 'View currently loaded hooks and provide custom editing.',
    'settings.action.validate': 'Validate',
    'settings.action.save': 'Save',
    'settings.action.cancel': 'Cancel',
};

export function t(key) {
    return STRINGS[key] || key;
}

export function formatMessage(key, values = {}) {
    return Object.entries(values).reduce((result, [name, value]) => result.replaceAll(`{${name}}`, String(value)), t(key));
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "runner.mjs").write_text(
        (
            runner_source
            or """
const host = { innerHTML: '' };
globalThis.document = {
    getElementById(id) {
        return id === 'hooks-runtime-status' ? host : null;
    },
};

const { bindHooksSettingsHandlers, loadHooksSettingsPanel } = await import('./hooksSettings.mjs');
bindHooksSettingsHandlers();
await loadHooksSettingsPanel();
console.log(JSON.stringify({ html: host.innerHTML }));
""".strip()
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(tmp_path / "runner.mjs")],
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
    return cast(dict[str, object], json.loads(completed.stdout))


def test_hooks_i18n_keys_exist_for_default_zh_cn_ui() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source = (repo_root / "frontend" / "dist" / "js" / "utils" / "i18n.js").read_text(
        encoding="utf-8"
    )

    assert r"'settings.tab.hooks': 'Hooks'" in source
    assert (
        r"'settings.panel.hooks.description': '查看当前已加载的 Hook 并提供自定义编辑。'"
        in source
    )
    assert r"'settings.hooks.validate_result_title': '校验结果'" in source
    assert r"'settings.hooks.save_result_title': '保存结果'" in source
    assert r"'settings.hooks.none': '当前没有已加载 Hook'" in source
    assert r"'settings.hooks.load_failed': '加载失败'" in source
    assert r"'settings.hooks.scope_project': '项目'" in source
    assert r"'settings.hooks.if_rule': 'If 规则'" in source
