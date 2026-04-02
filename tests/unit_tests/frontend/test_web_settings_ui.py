# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

import json
from pathlib import Path
import subprocess
from typing import cast


def test_web_settings_panel_loads_and_saves_optional_api_key(tmp_path: Path) -> None:
    payload = _run_web_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindWebSettingsHandlers, loadWebSettingsPanel } from "./webSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindWebSettingsHandlers();
await loadWebSettingsPanel();

document.getElementById("web-provider").value = "exa";
document.getElementById("web-fallback-provider").value = "searxng";
document.getElementById("web-api-key").value = "secret";
document.getElementById("web-searxng-instance-url").value = "https://search.example.test";

await document.getElementById("save-web-btn").onclick();

console.log(JSON.stringify({
    notifications,
    provider: document.getElementById("web-provider").value,
    fallbackProvider: document.getElementById("web-fallback-provider").value,
    apiKey: document.getElementById("web-api-key").value,
    searxngInstanceUrl: document.getElementById("web-searxng-instance-url").value,
    savePayload: globalThis.__saveWebPayload,
    providerSiteHref: document.getElementById("web-provider-site-link").href,
}));
""".strip(),
    )

    notifications = cast(list[dict[str, JsonValue]], payload["notifications"])
    assert payload["provider"] == "exa"
    assert payload["fallbackProvider"] == "searxng"
    assert payload["apiKey"] == "secret"
    assert payload["searxngInstanceUrl"] == "https://search.example.test"
    assert payload["savePayload"] == {
        "provider": "exa",
        "api_key": "secret",
        "fallback_provider": "searxng",
        "searxng_instance_url": "https://search.example.test",
    }
    assert payload["providerSiteHref"] == "https://exa.ai"
    assert notifications == [
        {
            "title": "Web Settings Saved",
            "message": "Web settings saved.",
            "tone": "success",
        }
    ]


def _run_web_settings_script(tmp_path: Path, runner_source: str) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "settings"
        / "webSettings.js"
    )

    mock_api_path = tmp_path / "mockApi.mjs"
    mock_feedback_path = tmp_path / "mockFeedback.mjs"
    mock_i18n_path = tmp_path / "mockI18n.mjs"
    mock_logger_path = tmp_path / "mockLogger.mjs"
    module_under_test_path = tmp_path / "webSettings.mjs"
    runner_path = tmp_path / "runner.mjs"

    mock_api_path.write_text(
        """
let currentConfig = {
    provider: "exa",
    api_key: null,
    fallback_provider: null,
    searxng_instance_url: null,
};

export async function fetchWebConfig() {
    return currentConfig;
}

export async function saveWebConfig(payload) {
    globalThis.__saveWebPayload = payload;
    currentConfig = payload;
    return { status: "ok" };
}
""".strip(),
        encoding="utf-8",
    )
    mock_feedback_path.write_text(
        """
export function showToast(payload) {
    globalThis.__feedbackNotifications.push(payload);
}
""".strip(),
        encoding="utf-8",
    )
    mock_i18n_path.write_text(
        """
const translations = {
    "settings.web.load_failed": "Load Failed",
    "settings.web.saved": "Web Settings Saved",
    "settings.web.saved_message": "Web settings saved.",
    "settings.web.save_failed": "Save Failed",
};

export function t(key) {
    return translations[key] || key;
}
""".strip(),
        encoding="utf-8",
    )
    mock_logger_path.write_text(
        """
export function errorToPayload(error, extra = {}) {
    return {
        error_message: String(error?.message || error || ""),
        ...extra,
    };
}

export function logError() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../../core/api.js", "./mockApi.mjs")
        .replace("../../utils/feedback.js", "./mockFeedback.mjs")
        .replace("../../utils/i18n.js", "./mockI18n.mjs")
        .replace("../../utils/logger.js", "./mockLogger.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    runner_path.write_text(
        f"""
function createElement(initialDisplay = "block") {{
    return {{
        style: {{ display: initialDisplay }},
        value: "",
        disabled: false,
        textContent: "",
        innerHTML: "",
        className: "",
        onclick: null,
    }};
}}

function createElements() {{
    return new Map([
        ["web-provider", createElement("block")],
        ["web-fallback-provider", createElement("block")],
        ["web-api-key", createElement("block")],
        ["web-searxng-instance-url", createElement("block")],
        ["web-provider-site-link", createElement("block")],
        ["save-web-btn", createElement("block")],
    ]);
}}

function installGlobals(elements, notifications) {{
    globalThis.document = {{
        getElementById(id) {{
            const element = elements.get(id);
            if (!element) {{
                throw new Error(`Missing element: ${{id}}`);
            }}
            return element;
        }},
    }};
    globalThis.__feedbackNotifications = notifications;
    globalThis.__saveWebPayload = null;
}}

{runner_source}
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["node", str(runner_path)],
        capture_output=True,
        check=False,
        cwd=str(repo_root),
        text=True,
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    return json.loads(completed.stdout)
