# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import cast

from pydantic import JsonValue

from relay_teams.env.web_config_models import (
    DEFAULT_SEARXNG_INSTANCE_SEEDS,
    DEFAULT_SEARXNG_INSTANCE_URL,
)


def test_web_settings_panel_saves_exa_key_and_fallback_settings(
    tmp_path: Path,
) -> None:
    payload = _run_web_settings_script(
        tmp_path=tmp_path,
        fetch_config={
            "provider": "exa",
            "exa_api_key": None,
            "fallback_provider": "searxng",
            "searxng_instance_url": None,
        },
        runner_source="""
import { bindWebSettingsHandlers, loadWebSettingsPanel } from "./webSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindWebSettingsHandlers();
await loadWebSettingsPanel();

const initialLabel = document.getElementById("web-api-key-label").textContent;
const initialPlaceholder = document.getElementById("web-api-key").placeholder;

document.getElementById("web-api-key").value = "draft-exa-key";
document.getElementById("web-api-key").oninput();
document.getElementById("web-fallback-provider").value = "searxng";
document.getElementById("web-fallback-provider").onchange();

const defaultInstanceValue = document.getElementById("web-searxng-instance-url").value;
const revealedDisplay = document.getElementById("web-searxng-instance-url-field").style.display;
const builtinsDisplay = document.getElementById("web-searxng-builtins-field").style.display;
const builtinsHtml = document.getElementById("web-searxng-builtins-list").innerHTML;

document.getElementById("web-searxng-instance-url").value = "https://search.example.test/";

await document.getElementById("save-web-btn").onclick();

console.log(JSON.stringify({
    notifications,
    initialLabel,
    initialPlaceholder,
    defaultInstanceValue,
    revealedDisplay,
    builtinsDisplay,
    builtinsHtml,
    savePayload: globalThis.__saveWebPayload,
}));
""".strip(),
    )

    notifications = cast(list[dict[str, JsonValue]], payload["notifications"])
    assert payload["initialLabel"] == "Exa API Key"
    assert payload["initialPlaceholder"] == "Optional for higher rate limits"
    assert payload["defaultInstanceValue"] == DEFAULT_SEARXNG_INSTANCE_URL
    assert payload["revealedDisplay"] == "grid"
    assert payload["builtinsDisplay"] == "grid"
    builtins_html = str(payload["builtinsHtml"])
    assert builtins_html.count("trigger-readonly-value-mono") == len(
        DEFAULT_SEARXNG_INSTANCE_SEEDS
    )
    for instance_url in DEFAULT_SEARXNG_INSTANCE_SEEDS:
        assert instance_url in builtins_html
    assert payload["savePayload"] == {
        "provider": "exa",
        "exa_api_key": "draft-exa-key",
        "fallback_provider": "searxng",
        "searxng_instance_url": "https://search.example.test/",
    }
    assert notifications == [
        {
            "title": "Web Settings Saved",
            "message": "Web settings saved.",
            "tone": "success",
        }
    ]


def test_web_settings_panel_preserves_saved_exa_key_when_left_unchanged(
    tmp_path: Path,
) -> None:
    payload = _run_web_settings_script(
        tmp_path=tmp_path,
        fetch_config={
            "provider": "exa",
            "exa_api_key": "saved-exa-key",
            "fallback_provider": "searxng",
            "searxng_instance_url": "https://search.example.test/",
        },
        runner_source="""
import { bindWebSettingsHandlers, loadWebSettingsPanel } from "./webSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindWebSettingsHandlers();
await loadWebSettingsPanel();

await document.getElementById("save-web-btn").onclick();

console.log(JSON.stringify({
    apiKeyValue: document.getElementById("web-api-key").value,
    apiKeyPlaceholder: document.getElementById("web-api-key").placeholder,
    apiKeyType: document.getElementById("web-api-key").type,
    toggleDisplay: document.getElementById("toggle-web-api-key-btn").style.display,
    savePayload: globalThis.__saveWebPayload,
}));
""".strip(),
    )

    assert payload["apiKeyValue"] == ""
    assert payload["apiKeyPlaceholder"] == "************"
    assert payload["apiKeyType"] == "password"
    assert payload["toggleDisplay"] == "inline-flex"
    assert payload["savePayload"] == {
        "provider": "exa",
        "exa_api_key": "saved-exa-key",
        "fallback_provider": "searxng",
        "searxng_instance_url": "https://search.example.test/",
    }


def test_web_settings_panel_reveals_and_clears_saved_exa_key(
    tmp_path: Path,
) -> None:
    payload = _run_web_settings_script(
        tmp_path=tmp_path,
        fetch_config={
            "provider": "exa",
            "exa_api_key": "saved-exa-key",
            "fallback_provider": "searxng",
            "searxng_instance_url": None,
        },
        runner_source="""
import { bindWebSettingsHandlers, loadWebSettingsPanel } from "./webSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindWebSettingsHandlers();
await loadWebSettingsPanel();

document.getElementById("toggle-web-api-key-btn").onclick();
const revealedValue = document.getElementById("web-api-key").value;
const revealedType = document.getElementById("web-api-key").type;
const toggleTitle = document.getElementById("toggle-web-api-key-btn").title;

document.getElementById("web-api-key").value = "";
document.getElementById("web-api-key").oninput();

await document.getElementById("save-web-btn").onclick();

console.log(JSON.stringify({
    notifications,
    revealedValue,
    revealedType,
    toggleTitle,
    clearedValue: document.getElementById("web-api-key").value,
    clearedPlaceholder: document.getElementById("web-api-key").placeholder,
    toggleDisplay: document.getElementById("toggle-web-api-key-btn").style.display,
    savePayload: globalThis.__saveWebPayload,
}));
""".strip(),
    )

    notifications = cast(list[dict[str, JsonValue]], payload["notifications"])
    assert payload["revealedValue"] == "saved-exa-key"
    assert payload["revealedType"] == "text"
    assert payload["toggleTitle"] == "Hide API key"
    assert payload["clearedValue"] == ""
    assert payload["clearedPlaceholder"] == "Optional for higher rate limits"
    assert payload["toggleDisplay"] == "inline-flex"
    assert payload["savePayload"] == {
        "provider": "exa",
        "exa_api_key": None,
        "fallback_provider": "searxng",
        "searxng_instance_url": DEFAULT_SEARXNG_INSTANCE_URL,
    }
    assert notifications == [
        {
            "title": "Web Settings Saved",
            "message": "Web settings saved.",
            "tone": "success",
        }
    ]


def test_web_settings_panel_renders_provider_website_card_for_exa(
    tmp_path: Path,
) -> None:
    payload = _run_web_settings_script(
        tmp_path=tmp_path,
        fetch_config={
            "provider": "exa",
            "exa_api_key": None,
            "fallback_provider": "searxng",
            "searxng_instance_url": None,
        },
        runner_source="""
import { bindWebSettingsHandlers, loadWebSettingsPanel } from "./webSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindWebSettingsHandlers();
await loadWebSettingsPanel();

console.log(JSON.stringify({
    providerSiteHref: document.getElementById("web-provider-site-link").href,
    providerSiteTitle: document.getElementById("web-provider-site-link").title,
    providerSiteAriaLabel: document.getElementById("web-provider-site-link").ariaLabel,
    providerSiteBadge: document.getElementById("web-provider-site-badge").textContent,
    providerSiteUrl: document.getElementById("web-provider-site-url").textContent,
}));
""".strip(),
    )

    assert payload["providerSiteHref"] == "https://exa.ai"
    assert payload["providerSiteTitle"] == "https://exa.ai"
    assert payload["providerSiteAriaLabel"] == "https://exa.ai"
    assert payload["providerSiteBadge"] == "Exa"
    assert payload["providerSiteUrl"] == "https://exa.ai"


def test_web_settings_panel_hides_searxng_field_until_needed(
    tmp_path: Path,
) -> None:
    payload = _run_web_settings_script(
        tmp_path=tmp_path,
        fetch_config={
            "provider": "exa",
            "exa_api_key": None,
            "fallback_provider": "disabled",
            "searxng_instance_url": None,
        },
        runner_source="""
import { bindWebSettingsHandlers, loadWebSettingsPanel } from "./webSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindWebSettingsHandlers();
await loadWebSettingsPanel();

const hiddenDisplay = document.getElementById("web-searxng-instance-url-field").style.display;
const hiddenDisabled = document.getElementById("web-searxng-instance-url").disabled;
const hiddenValue = document.getElementById("web-searxng-instance-url").value;
const hiddenBuiltinsDisplay = document.getElementById("web-searxng-builtins-field").style.display;
const hiddenBuiltinsHtml = document.getElementById("web-searxng-builtins-list").innerHTML;

document.getElementById("web-fallback-provider").value = "searxng";
document.getElementById("web-fallback-provider").onchange();

console.log(JSON.stringify({
    hiddenDisplay,
    hiddenDisabled,
    hiddenValue,
    hiddenBuiltinsDisplay,
    hiddenBuiltinsHtml,
    revealedDisplay: document.getElementById("web-searxng-instance-url-field").style.display,
    revealedDisabled: document.getElementById("web-searxng-instance-url").disabled,
    revealedValue: document.getElementById("web-searxng-instance-url").value,
    revealedBuiltinsDisplay: document.getElementById("web-searxng-builtins-field").style.display,
}));
""".strip(),
    )

    assert payload["hiddenDisplay"] == "none"
    assert payload["hiddenDisabled"] is True
    assert payload["hiddenValue"] == DEFAULT_SEARXNG_INSTANCE_URL
    assert payload["hiddenBuiltinsDisplay"] == "none"
    for instance_url in DEFAULT_SEARXNG_INSTANCE_SEEDS:
        assert instance_url in str(payload["hiddenBuiltinsHtml"])
    assert payload["revealedDisplay"] == "grid"
    assert payload["revealedDisabled"] is False
    assert payload["revealedValue"] == DEFAULT_SEARXNG_INSTANCE_URL
    assert payload["revealedBuiltinsDisplay"] == "grid"


def _run_web_settings_script(
    tmp_path: Path,
    runner_source: str,
    *,
    fetch_config: dict[str, JsonValue] | None = None,
) -> dict[str, object]:
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
    fetch_web_config = {
        "provider": "exa",
        "exa_api_key": None,
        "fallback_provider": "searxng",
        "searxng_instance_url": None,
        "searxng_instance_seeds": list(DEFAULT_SEARXNG_INSTANCE_SEEDS),
    }
    if fetch_config is not None:
        fetch_web_config.update(fetch_config)
    fetch_web_config_json = json.dumps(fetch_web_config)

    mock_api_path.write_text(
        """
let currentConfig = __FETCH_WEB_CONFIG__;

export async function fetchWebConfig() {
    return currentConfig;
}

export async function saveWebConfig(payload) {
    globalThis.__saveWebPayload = payload;
    currentConfig = {
        ...payload,
        searxng_instance_seeds: currentConfig.searxng_instance_seeds || [],
    };
    return { status: "ok" };
}
""".replace("__FETCH_WEB_CONFIG__", fetch_web_config_json).strip(),
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
    "settings.web.api_key_placeholder": "Optional for higher rate limits",
    "settings.web.exa_api_key": "Exa API Key",
    "settings.web.searxng_instance_url_placeholder": "Default: {default}",
    "settings.model.show_api_key": "Show API key",
    "settings.model.hide_api_key": "Hide API key",
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
        href: "",
        title: "",
        placeholder: "",
        type: "text",
        ariaLabel: "",
        textContent: "",
        innerHTML: "",
        className: "",
        onclick: null,
        onchange: null,
        oninput: null,
        setAttribute(name, value) {{
            if (name === "aria-label") {{
                this.ariaLabel = value;
                return;
            }}
            this[name] = value;
        }},
    }};
}}

function createElements() {{
    return new Map([
        ["web-provider", createElement("block")],
        ["web-fallback-provider", createElement("block")],
        ["web-api-key-label", createElement("block")],
        ["web-api-key", createElement("block")],
        ["toggle-web-api-key-btn", createElement("none")],
        ["web-searxng-instance-url-field", createElement("none")],
        ["web-searxng-instance-url", createElement("block")],
        ["web-searxng-builtins-field", createElement("none")],
        ["web-searxng-builtins-list", createElement("block")],
        ["web-provider-site-link", createElement("block")],
        ["web-provider-site-badge", createElement("block")],
        ["web-provider-site-url", createElement("block")],
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
