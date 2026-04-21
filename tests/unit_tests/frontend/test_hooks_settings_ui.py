# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import cast


def test_hooks_settings_panel_renders_loaded_hooks(tmp_path: Path) -> None:
    payload = _run_hooks_settings_script(
        tmp_path=tmp_path,
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
                    "if_condition": "tool_name == 'shell'",
                    "tool_names": ["shell"],
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
    assert "python policy.py" in html
    assert "PreToolUse" in html
    assert "shell" in html
    assert "command" in html
    assert "Scope" in html
    assert "Project" in html
    assert "Condition" in html
    assert "tool_name == &#39;shell&#39;" in html
    assert "Tools" in html
    assert "Roles" in html
    assert "Session Modes" in html
    assert "Run Kinds" in html
    assert "coordinator" in html
    assert "normal" in html
    assert "foreground" in html
    assert "/workspace/.relay-teams/hooks.json" not in html
    assert "mcp-status-card hooks-runtime-card" in html
    assert "hooks-runtime-detail-list status-list" in html
    assert "hooks-runtime-detail-row status-list-row" in html
    assert "hooks-runtime-detail-item status-list-copy" in html
    assert "hooks-runtime-detail-label status-list-name" in html
    assert "hooks-runtime-detail-value status-list-description" in html
    assert "hooks-runtime-overview-table" not in html


def test_hooks_settings_panel_renders_empty_and_error_states(tmp_path: Path) -> None:
    empty_payload = _run_hooks_settings_script(
        tmp_path=tmp_path / "empty",
        runtime_view={"sources": [], "loaded_hooks": []},
    )
    assert "No hooks loaded" in str(empty_payload["html"])

    error_payload = _run_hooks_settings_script(
        tmp_path=tmp_path / "error",
        runtime_view=None,
        error_message="boom",
    )
    assert "Load Failed" in str(error_payload["html"])
    assert "boom" in str(error_payload["html"])


def test_hooks_settings_panel_ignores_out_of_order_load_results(tmp_path: Path) -> None:
    payload = _run_hooks_settings_script(
        tmp_path=tmp_path,
        runtime_view=None,
        api_source="""
let callCount = 0;

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


def _run_hooks_settings_script(
    *,
    tmp_path: Path,
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
    source = source.replace("../../utils/i18n.js", "./i18n.mjs")
    source = source.replace("../../utils/logger.js", "./logger.mjs")

    (tmp_path / "hooksSettings.mjs").write_text(source, encoding="utf-8")
    if api_source is not None:
        api_module_source = api_source.strip()
    elif error_message is None:
        api_module_source = f"""
export async function fetchHookRuntimeView() {{
    return {json.dumps(runtime_view)};
}}
"""
    else:
        api_module_source = f"""
export async function fetchHookRuntimeView() {{
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
    (tmp_path / "i18n.mjs").write_text(
        """
const STRINGS = {
    'settings.hooks.summary': '{count} loaded hooks across {source_count} source files',
    'settings.hooks.loading': 'Loading loaded hooks...',
    'settings.hooks.none': 'No hooks loaded',
    'settings.hooks.none_copy': 'No hook config files are currently contributing runtime hooks for this workspace.',
    'settings.hooks.load_failed': 'Load Failed',
    'settings.hooks.load_failed_detail': 'Unable to load runtime hooks: {error}',
    'settings.hooks.name': 'Name',
    'settings.hooks.trigger': 'Trigger',
    'settings.hooks.matcher': 'Matcher',
    'settings.hooks.type': 'Type',
    'settings.hooks.scope': 'Scope',
    'settings.hooks.if_condition': 'Condition',
    'settings.hooks.tool_names': 'Tools',
    'settings.hooks.role_ids': 'Roles',
    'settings.hooks.session_modes': 'Session Modes',
    'settings.hooks.run_kinds': 'Run Kinds',
    'settings.hooks.all': 'All',
    'settings.hooks.unnamed': 'Unnamed hook',
    'settings.hooks.scope_project': 'Project',
    'settings.hooks.scope_project_local': 'Project Local',
    'settings.hooks.scope_user': 'User',
    'settings.hooks.scope_unknown': 'Unknown source',
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
        r"'settings.panel.hooks.description': '查看当前工作区已加载的 Hook 处理器。'"
        in source
    )
    assert r"'settings.hooks.none': '当前没有已加载 Hook'" in source
    assert r"'settings.hooks.load_failed': '加载失败'" in source
    assert r"'settings.hooks.scope_project': '项目'" in source
    assert r"'settings.hooks.if_condition': '条件'" in source
