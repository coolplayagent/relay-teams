# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import cast

from pydantic import JsonValue


def test_plugins_settings_marketplace_update_uses_version_select() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "settings"
        / "pluginsSettings.js"
    ).read_text(encoding="utf-8")

    assert "async function promptAndUpdateMarketplacePlugin(plugin)" in source
    assert "pluginMarketplaceRequestOptions(plugin)" in source
    assert "type: 'select'" in source
    assert "settings.plugins.version_latest" in source
    assert "await updatePlugin(plugin.name, {" in source
    assert "allow_missing_digest: true" in source
    assert "function renderMarketplaceVersionDetails(version)" in source
    assert "settings.plugins.marketplace_version_details" in source
    assert "source.ref" in source


def test_plugins_settings_clawhub_marketplace_loads_full_safe_browsing() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "settings"
        / "pluginsSettings.js"
    ).read_text(encoding="utf-8")
    i18n_source = (
        repo_root / "frontend" / "dist" / "js" / "utils" / "i18n.js"
    ).read_text(encoding="utf-8")

    assert "CLAWHUB_MARKETPLACE_PAGE_SIZE" not in source
    assert "function visibleMarketplacePlugins()" in source
    assert "function isLowRiskClawHubMarketplacePlugin(plugin)" in source
    assert "function isLowRiskClawHubVersion(version)" not in source
    assert "function marketplaceCompatibilityLabel(plugin)" not in source
    assert "function marketplaceCompatibilityDetail(plugin)" not in source
    assert "inspectPluginMarketplace(" not in source
    assert 'data-plugin-action="inspect-marketplace-plugin"' not in source
    assert 'data-plugin-action="load-more-marketplace"' not in source
    assert "include_details: false" in source
    assert "fetch_all: true" in source
    assert "return compatibility === 'direct';" in source
    assert (
        "return versions.filter(version => !versionUnsupportedReason(version));"
        in source
    )
    assert "settings.plugins.compatibility_direct" in i18n_source
    assert "settings.plugins.warning_clawhub_executes_code" in source
    assert "settings.plugins.warning_clawhub_executes_code" in i18n_source
    assert "marketplace_loaded_hidden_risky" not in source
    assert "show_unavailable_marketplace" not in source
    assert "payload.allow_missing_digest = true;" in source


def test_plugins_settings_marketplace_update_excludes_unsupported_versions(
    tmp_path: Path,
) -> None:
    payload = _run_plugins_settings_script(
        tmp_path=tmp_path,
        fetch_registry={
            "plugins": [
                {
                    "name": "quality",
                    "version": "1.0.0",
                    "scope": "user",
                    "enabled": True,
                    "source": {
                        "kind": "marketplace",
                        "value": "quality",
                        "marketplace": "claude-plugins-official",
                        "marketplace_provider": "claude",
                        "marketplace_source": "",
                    },
                    "manifest": {},
                    "user_config": {},
                    "component_counts": {},
                }
            ],
            "diagnostics": [],
        },
        marketplace={
            "plugins": [
                {
                    "name": "quality",
                    "latest": "2.0.0",
                    "versions": [
                        {
                            "version": "1.0.0",
                            "source": {
                                "kind": "local",
                                "value": "C:/plugins/quality",
                            },
                        },
                        {
                            "version": "2.0.0",
                            "unsupported_reason": "npm is not supported",
                            "source": {
                                "kind": "unsupported",
                                "value": "@example/plugin",
                            },
                        },
                    ],
                }
            ]
        },
        runner_source="""
import { bindPluginsSettingsHandlers, loadPluginsSettingsPanel } from "./pluginsSettings.mjs";

const root = installGlobals();
globalThis.__dialogResult = { version: "1.0.0" };
bindPluginsSettingsHandlers();
await loadPluginsSettingsPanel();

await root.dispatch("click", root.findButton("update", "user:quality"));
await flush();

console.log(JSON.stringify({
    dialogOptions: globalThis.__dialogPayloads[0].fields[0].options,
    marketplaceRequest: globalThis.__marketplaceRequests[0],
    updatePayload: globalThis.__updatePayloads[0],
}));
""".strip(),
    )

    assert payload["dialogOptions"] == [
        {"value": "1.0.0", "label": "1.0.0"},
    ]
    assert payload["marketplaceRequest"] == {
        "marketplacePath": "claude-plugins-official",
        "options": {
            "marketplace_provider": "claude",
            "marketplace_source": "",
            "marketplace_ref": "",
            "refresh": True,
        },
    }
    assert payload["updatePayload"] == {
        "name": "quality",
        "payload": {"scope": "user", "version": "1.0.0"},
    }


def test_plugins_settings_clawhub_update_fetches_all_detailed_entries(
    tmp_path: Path,
) -> None:
    payload = _run_plugins_settings_script(
        tmp_path=tmp_path,
        fetch_registry={
            "plugins": [
                {
                    "name": "market-plugin",
                    "version": "1.0.0",
                    "scope": "user",
                    "enabled": True,
                    "source": {
                        "kind": "marketplace",
                        "value": "market-plugin",
                        "marketplace": "clawhub",
                        "marketplace_provider": "clawhub",
                        "marketplace_source": "https://clawhub.ai",
                    },
                    "manifest": {},
                    "user_config": {},
                    "component_counts": {},
                }
            ],
            "diagnostics": [],
        },
        marketplace={
            "plugins": [
                {
                    "name": "market-plugin",
                    "latest": "1.1.0",
                    "compatibility": "direct",
                    "versions": [
                        {
                            "version": "1.1.0",
                            "source": {
                                "kind": "http_archive",
                                "value": "https://clawhub.test/archive.zip",
                                "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                            },
                        }
                    ],
                }
            ]
        },
        runner_source="""
import { bindPluginsSettingsHandlers, loadPluginsSettingsPanel } from "./pluginsSettings.mjs";

const root = installGlobals();
globalThis.__dialogResult = { version: "1.1.0" };
bindPluginsSettingsHandlers();
await loadPluginsSettingsPanel();

await root.dispatch("click", root.findButton("update", "user:market-plugin"));
await flush();

console.log(JSON.stringify({
    marketplaceRequest: globalThis.__marketplaceRequests[0],
    updatePayload: globalThis.__updatePayloads[0],
}));
""".strip(),
    )

    assert payload["marketplaceRequest"] == {
        "marketplacePath": "clawhub",
        "options": {
            "marketplace_provider": "clawhub",
            "marketplace_source": "https://clawhub.ai",
            "marketplace_ref": "",
            "include_details": True,
            "fetch_all": True,
            "allow_missing_digest": True,
            "refresh": True,
        },
    }
    assert payload["updatePayload"] == {
        "name": "market-plugin",
        "payload": {
            "scope": "user",
            "version": "1.1.0",
            "allow_missing_digest": True,
        },
    }


def test_plugins_settings_config_fields_preserve_declared_types() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "settings"
        / "pluginsSettings.js"
    ).read_text(encoding="utf-8")

    assert "data-plugin-config-type" in source
    assert "function readPluginConfigInputValue(input, fieldType)" in source
    assert "return input.checked;" in source
    assert "const rawValue = String(input.value || '');" in source
    assert "const value = rawValue.trim();" in source
    assert "return value ? Number(value) : '';" in source
    assert "textarea" in source
    assert "JSON.parse(value)" in source
    assert "formatPluginConfigJsonValue(value)" in source
    assert "return rawValue;" in source


def test_plugins_settings_git_install_supports_source_ref() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "settings"
        / "pluginsSettings.js"
    ).read_text(encoding="utf-8")

    assert "source_ref: ''" in source
    assert 'name="source_ref"' in source
    assert "payload.source_ref = installDraft.source_ref;" in source
    assert "settings.plugins.git_ref_help" in source


def test_plugins_settings_does_not_render_search_box() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    component_source = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "settings"
        / "pluginsSettings.js"
    ).read_text(encoding="utf-8")
    style_source = (
        repo_root
        / "frontend"
        / "dist"
        / "css"
        / "components"
        / "settings"
        / "plugins.css"
    ).read_text(encoding="utf-8")
    i18n_source = (
        repo_root / "frontend" / "dist" / "js" / "utils" / "i18n.js"
    ).read_text(encoding="utf-8")

    assert "plugins-search" not in component_source
    assert "pluginSearchQuery" not in component_source
    assert "filteredPlugins" not in component_source
    assert "plugins-search" not in style_source
    assert "settings.plugins.search_placeholder" not in i18n_source


def test_plugins_settings_empty_state_only_shows_add_plugin_action() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    component_source = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "settings"
        / "pluginsSettings.js"
    ).read_text(encoding="utf-8")
    shell_source = (
        repo_root / "frontend" / "dist" / "js" / "components" / "settings" / "index.js"
    ).read_text(encoding="utf-8")
    i18n_source = (
        repo_root / "frontend" / "dist" / "js" / "utils" / "i18n.js"
    ).read_text(encoding="utf-8")

    assert (
        "pluginPanelMode === 'list' && pluginsRegistry.plugins.length === 0"
        in component_source
    )
    assert "if (refreshBtn) refreshBtn.style.display = 'none';" in component_source
    assert "if (validateBtn) validateBtn.style.display = 'none';" in component_source
    assert (
        "if (installBtn) installBtn.style.display = 'inline-flex';" in component_source
    )
    assert 'id="install-plugin-btn"' in shell_source
    assert ">Add Plugin</button>" in shell_source
    assert (
        "type=\"submit\">${escapeHtml(t('settings.action.save'))}</button>"
        in component_source
    )
    assert "'settings.plugins.install': 'Add Plugin'" in i18n_source
    assert "'settings.plugins.install': '新增插件'" in i18n_source
    assert "安装插件" not in i18n_source


def test_plugins_settings_install_only_shows_marketplace_fields_in_marketplace_mode(
    tmp_path: Path,
) -> None:
    payload = _run_plugins_settings_script(
        tmp_path=tmp_path,
        fetch_registry={"plugins": [], "diagnostics": []},
        marketplace={"plugins": []},
        runner_source="""
import { bindPluginsSettingsHandlers, loadPluginsSettingsPanel } from "./pluginsSettings.mjs";

const root = installGlobals();
bindPluginsSettingsHandlers();
await loadPluginsSettingsPanel();

document.getElementById("install-plugin-btn").onclick();
let form = document.getElementById("plugin-install-form");
const localHtml = root.innerHTML;

form.elements.source_kind.value = "git";
await root.dispatch("change", form.elements.source_kind);
form = document.getElementById("plugin-install-form");
const gitHtml = root.innerHTML;

form.elements.source_kind.value = "marketplace";
await root.dispatch("change", form.elements.source_kind);
const marketplaceHtml = root.innerHTML;

console.log(JSON.stringify({ localHtml, gitHtml, marketplaceHtml }));
""".strip(),
    )

    assert "settings.plugins.marketplace_path" not in str(payload["localHtml"])
    assert "settings.plugins.version" not in str(payload["localHtml"])
    assert "settings.plugins.enabled_after_install" not in str(payload["localHtml"])
    assert 'name="marketplace"' not in str(payload["localHtml"])
    assert 'name="version"' not in str(payload["localHtml"])
    assert 'name="enabled"' not in str(payload["localHtml"])
    assert 'data-plugin-action="validate-install-source"' in str(payload["localHtml"])
    assert "settings.plugins.marketplace_path" not in str(payload["gitHtml"])
    assert "settings.plugins.version" not in str(payload["gitHtml"])
    assert "settings.plugins.enabled_after_install" not in str(payload["gitHtml"])
    assert 'name="marketplace"' not in str(payload["gitHtml"])
    assert 'name="version"' not in str(payload["gitHtml"])
    assert 'name="enabled"' not in str(payload["gitHtml"])
    assert 'data-plugin-action="validate-install-source"' in str(payload["gitHtml"])
    assert "settings.plugins.git_ref" in str(payload["gitHtml"])
    assert "settings.plugins.marketplace_path" in str(payload["marketplaceHtml"])
    assert "settings.plugins.version" in str(payload["marketplaceHtml"])
    assert "settings.plugins.enabled_after_install" not in str(
        payload["marketplaceHtml"]
    )
    assert 'name="marketplace"' in str(payload["marketplaceHtml"])
    assert 'name="version"' in str(payload["marketplaceHtml"])
    assert 'name="enabled"' not in str(payload["marketplaceHtml"])
    assert 'data-plugin-action="validate-install-source"' not in str(
        payload["marketplaceHtml"]
    )


def test_plugins_settings_install_submits_local_and_git_payloads(
    tmp_path: Path,
) -> None:
    payload = _run_plugins_settings_script(
        tmp_path=tmp_path,
        fetch_registry={"plugins": [], "diagnostics": []},
        marketplace={"plugins": []},
        runner_source="""
import { bindPluginsSettingsHandlers, loadPluginsSettingsPanel } from "./pluginsSettings.mjs";

const root = installGlobals();
bindPluginsSettingsHandlers();
await loadPluginsSettingsPanel();

document.getElementById("install-plugin-btn").onclick();
let form = document.getElementById("plugin-install-form");
form.elements.source.value = "C:/plugins/local-quality";
await root.dispatch("input", form.elements.source);
await root.dispatch("submit", form);
await flush();

document.getElementById("install-plugin-btn").onclick();
form = document.getElementById("plugin-install-form");
form.elements.source_kind.value = "git";
await root.dispatch("change", form.elements.source_kind);
form = document.getElementById("plugin-install-form");
form.elements.source.value = "https://example.test/plugins/quality.git";
form.elements.source_ref.value = "v1.2.0";
await root.dispatch("input", form.elements.source);
await root.dispatch("input", form.elements.source_ref);
await root.dispatch("submit", form);
await flush();

console.log(JSON.stringify({
    installPayloads: globalThis.__installPayloads,
    notifications: globalThis.__notifications,
}));
""".strip(),
    )

    assert payload["installPayloads"] == [
        {
            "source": "C:/plugins/local-quality",
            "scope": "user",
            "enabled": True,
            "source_kind": "local",
        },
        {
            "source": "https://example.test/plugins/quality.git",
            "scope": "user",
            "enabled": True,
            "source_kind": "git",
            "source_ref": "v1.2.0",
        },
    ]


def test_plugins_settings_marketplace_install_submits_selected_version(
    tmp_path: Path,
) -> None:
    payload = _run_plugins_settings_script(
        tmp_path=tmp_path,
        fetch_registry={"plugins": [], "diagnostics": []},
        marketplace={
            "plugins": [
                {
                    "name": "quality",
                    "latest": "1.2.0",
                    "versions": [
                        {
                            "version": "1.1.0",
                            "source": {"kind": "git", "value": "https://repo/quality"},
                        },
                        {
                            "version": "1.2.0",
                            "source": {
                                "kind": "git",
                                "value": "https://repo/quality",
                                "ref": "v1.2.0",
                            },
                        },
                    ],
                }
            ]
        },
        runner_source="""
import { bindPluginsSettingsHandlers, loadPluginsSettingsPanel } from "./pluginsSettings.mjs";

const root = installGlobals();
bindPluginsSettingsHandlers();
await loadPluginsSettingsPanel();

document.getElementById("install-plugin-btn").onclick();
let form = document.getElementById("plugin-install-form");
form.elements.source_kind.value = "marketplace";
await root.dispatch("change", form.elements.source_kind);

form = document.getElementById("plugin-install-form");
form.elements.marketplace.value = "C:/plugins/marketplace.json";
await root.dispatch("input", form.elements.marketplace);
await root.dispatch("click", root.findButton("load-marketplace"));
await flush();

form = document.getElementById("plugin-install-form");
form.elements.version.value = "1.2.0";
await root.dispatch("change", form.elements.version);
const htmlBeforeSubmit = root.innerHTML;
await root.dispatch("submit", form);
await flush();

console.log(JSON.stringify({
    installPayload: globalThis.__installPayloads[0],
    htmlBeforeSubmit,
    notifications: globalThis.__notifications,
}));
""".strip(),
    )

    notifications = cast(list[dict[str, JsonValue]], payload["notifications"])
    assert payload["installPayload"] == {
        "source": "quality",
        "scope": "user",
        "enabled": True,
        "marketplace": "C:/plugins/marketplace.json",
        "version": "1.2.0",
    }
    assert "v1.2.0" in str(payload["htmlBeforeSubmit"])
    assert any(item.get("tone") == "success" for item in notifications)


def test_plugins_settings_claude_marketplace_submits_provider_payload(
    tmp_path: Path,
) -> None:
    payload = _run_plugins_settings_script(
        tmp_path=tmp_path,
        fetch_registry={"plugins": [], "diagnostics": []},
        marketplace={
            "plugins": [
                {
                    "name": "github",
                    "latest": "1.0.0",
                    "versions": [
                        {
                            "version": "1.0.0",
                            "source": {
                                "kind": "git",
                                "value": "https://github.com/anthropics/github.git",
                            },
                        }
                    ],
                }
            ]
        },
        runner_source="""
import { bindPluginsSettingsHandlers, loadPluginsSettingsPanel } from "./pluginsSettings.mjs";

const root = installGlobals();
bindPluginsSettingsHandlers();
await loadPluginsSettingsPanel();

document.getElementById("install-plugin-btn").onclick();
let form = document.getElementById("plugin-install-form");
form.elements.source_kind.value = "marketplace";
await root.dispatch("change", form.elements.source_kind);
form = document.getElementById("plugin-install-form");
form.elements.marketplace_provider.value = "claude";
await root.dispatch("change", form.elements.marketplace_provider);

form = document.getElementById("plugin-install-form");
await root.dispatch("click", root.findButton("load-marketplace"));
await flush();

form = document.getElementById("plugin-install-form");
const htmlBeforeSubmit = root.innerHTML;
await root.dispatch("submit", form);
await flush();

console.log(JSON.stringify({
    installPayload: globalThis.__installPayloads[0],
    marketplaceRequest: globalThis.__marketplaceRequests[0],
    htmlBeforeSubmit,
}));
""".strip(),
    )

    assert payload["marketplaceRequest"] == {
        "marketplacePath": "claude-plugins-official",
        "options": {
            "marketplace_provider": "claude",
            "marketplace_source": "anthropics/claude-plugins-official",
            "marketplace_ref": "",
            "refresh": True,
        },
    }
    assert payload["installPayload"] == {
        "source": "github",
        "scope": "user",
        "enabled": True,
        "marketplace": "claude-plugins-official",
        "version": None,
        "marketplace_provider": "claude",
        "marketplace_source": "anthropics/claude-plugins-official",
        "marketplace_ref": "",
    }
    assert "settings.plugins.source_type_claude_marketplace" not in str(
        payload["htmlBeforeSubmit"]
    )
    assert "settings.plugins.marketplace_provider" in str(payload["htmlBeforeSubmit"])
    assert "settings.plugins.marketplace_source" in str(payload["htmlBeforeSubmit"])


def test_plugins_settings_clawhub_load_and_install_payload(
    tmp_path: Path,
) -> None:
    payload = _run_plugins_settings_script(
        tmp_path=tmp_path,
        fetch_registry={"plugins": [], "diagnostics": []},
        marketplace={
            "plugins": [
                {
                    "name": "market-plugin",
                    "description": "Market data",
                    "latest": "1.0.1",
                    "compatibility": "direct",
                    "versions": [
                        {
                            "version": "1.0.1",
                            "source": {
                                "kind": "http_archive",
                                "value": "https://clawhub.test/archive.zip",
                                "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                            },
                            "warnings": ["ClawHub package executes code."],
                        }
                    ],
                },
                {
                    "name": "partial-plugin",
                    "description": "Native-assisted package",
                    "latest": "1.0.0",
                    "compatibility": "partial",
                    "versions": [
                        {
                            "version": "1.0.0",
                            "source": {
                                "kind": "http_archive",
                                "value": "https://clawhub.test/partial.zip",
                                "sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                            },
                            "warnings": [],
                        }
                    ],
                },
                {
                    "name": "unknown-plugin",
                    "description": "Needs inspection",
                    "latest": "1.0.0",
                    "compatibility": "unknown",
                    "versions": [
                        {
                            "version": "1.0.0",
                            "source": {
                                "kind": "http_archive",
                                "value": "https://clawhub.test/unknown.zip",
                                "sha": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
                            },
                            "warnings": [],
                        }
                    ],
                },
                {
                    "name": "warning-plugin",
                    "description": "Warned package",
                    "latest": "1.0.0",
                    "compatibility": "direct",
                    "versions": [
                        {
                            "version": "1.0.0",
                            "source": {
                                "kind": "http_archive",
                                "value": "https://clawhub.test/warning.zip",
                                "sha": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
                            },
                            "warnings": ["ClawHub package executes code."],
                        }
                    ],
                },
            ]
        },
        runner_source="""
import { bindPluginsSettingsHandlers, loadPluginsSettingsPanel } from "./pluginsSettings.mjs";

const root = installGlobals();
bindPluginsSettingsHandlers();
await loadPluginsSettingsPanel();

document.getElementById("install-plugin-btn").onclick();
let form = document.getElementById("plugin-install-form");
form.elements.source_kind.value = "marketplace";
await root.dispatch("change", form.elements.source_kind);
form = document.getElementById("plugin-install-form");
form.elements.marketplace_provider.value = "clawhub";
await root.dispatch("change", form.elements.marketplace_provider);
form = document.getElementById("plugin-install-form");
await root.dispatch("click", root.findButton("load-marketplace"));
await flush();

const htmlBeforeSubmit = root.innerHTML;
form = document.getElementById("plugin-install-form");
await root.dispatch("submit", form);
await flush();

console.log(JSON.stringify({
    htmlBeforeSubmit,
    marketplaceRequest: globalThis.__marketplaceRequests[0],
    inspectRequestCount: globalThis.__marketplaceInspectRequests.length,
    searchRequestCount: globalThis.__marketplaceSearchRequests.length,
    installPayload: globalThis.__installPayloads[0],
}));
""".strip(),
    )

    assert payload["marketplaceRequest"] == {
        "marketplacePath": "clawhub",
        "options": {
            "marketplace_provider": "clawhub",
            "marketplace_source": "https://clawhub.ai",
            "marketplace_ref": "",
            "include_details": False,
            "fetch_all": True,
            "refresh": True,
        },
    }
    assert payload["searchRequestCount"] == 0
    assert payload["inspectRequestCount"] == 0
    assert payload["installPayload"] == {
        "source": "market-plugin",
        "scope": "user",
        "enabled": True,
        "marketplace": "clawhub",
        "version": None,
        "marketplace_provider": "clawhub",
        "marketplace_source": "https://clawhub.ai",
        "marketplace_ref": "",
        "allow_missing_digest": True,
    }
    assert "settings.plugins.marketplace_provider_clawhub" in str(
        payload["htmlBeforeSubmit"]
    )
    assert "inspect-marketplace-plugin" not in str(payload["htmlBeforeSubmit"])
    assert "marketplace_loaded_hidden_risky" not in str(payload["htmlBeforeSubmit"])
    assert "market-plugin" in str(payload["htmlBeforeSubmit"])
    assert "settings.plugins.warning_clawhub_executes_code" in str(
        payload["htmlBeforeSubmit"]
    )
    assert "ClawHub package executes code." not in str(payload["htmlBeforeSubmit"])
    assert "Direct" not in str(payload["htmlBeforeSubmit"])
    assert "ClawHub bundle-plugin" not in str(payload["htmlBeforeSubmit"])
    assert "Bundle plugin with static components" not in str(
        payload["htmlBeforeSubmit"]
    )
    assert "partial-plugin" not in str(payload["htmlBeforeSubmit"])
    assert "unknown-plugin" not in str(payload["htmlBeforeSubmit"])
    assert "warning-plugin" in str(payload["htmlBeforeSubmit"])


def test_plugins_settings_marketplace_provider_switch_replaces_defaults(
    tmp_path: Path,
) -> None:
    payload = _run_plugins_settings_script(
        tmp_path=tmp_path,
        fetch_registry={"plugins": [], "diagnostics": []},
        marketplace={"plugins": []},
        runner_source="""
import { bindPluginsSettingsHandlers, loadPluginsSettingsPanel } from "./pluginsSettings.mjs";

const root = installGlobals();
bindPluginsSettingsHandlers();
await loadPluginsSettingsPanel();

document.getElementById("install-plugin-btn").onclick();
let form = document.getElementById("plugin-install-form");
form.elements.source_kind.value = "marketplace";
await root.dispatch("change", form.elements.source_kind);

form = document.getElementById("plugin-install-form");
form.elements.marketplace_provider.value = "clawhub";
await root.dispatch("change", form.elements.marketplace_provider);
form = document.getElementById("plugin-install-form");
const clawhub = {
    marketplace: form.elements.marketplace.value,
    marketplaceSource: form.elements.marketplace_source?.value || "",
};

form.elements.marketplace_provider.value = "claude";
await root.dispatch("change", form.elements.marketplace_provider);
form = document.getElementById("plugin-install-form");
const claude = {
    marketplace: form.elements.marketplace.value,
    marketplaceSource: form.elements.marketplace_source?.value || "",
};

form.elements.marketplace_provider.value = "relay";
await root.dispatch("change", form.elements.marketplace_provider);
form = document.getElementById("plugin-install-form");
const relay = {
    marketplace: form.elements.marketplace.value,
    marketplaceSource: form.elements.marketplace_source?.value || "",
};

console.log(JSON.stringify({ clawhub, claude, relay }));
""".strip(),
    )

    assert payload == {
        "clawhub": {
            "marketplace": "clawhub",
            "marketplaceSource": "https://clawhub.ai",
        },
        "claude": {
            "marketplace": "claude-plugins-official",
            "marketplaceSource": "anthropics/claude-plugins-official",
        },
        "relay": {"marketplace": "", "marketplaceSource": ""},
    }


def test_plugins_settings_blocks_unsupported_marketplace_source(
    tmp_path: Path,
) -> None:
    payload = _run_plugins_settings_script(
        tmp_path=tmp_path,
        fetch_registry={"plugins": [], "diagnostics": []},
        marketplace={
            "plugins": [
                {
                    "name": "npm-plugin",
                    "latest": "1.0.0",
                    "versions": [
                        {
                            "version": "1.0.0",
                            "unsupported_reason": "npm is not supported",
                            "source": {
                                "kind": "unsupported",
                                "value": "@example/plugin",
                            },
                        }
                    ],
                }
            ]
        },
        runner_source="""
import { bindPluginsSettingsHandlers, loadPluginsSettingsPanel } from "./pluginsSettings.mjs";

const root = installGlobals();
bindPluginsSettingsHandlers();
await loadPluginsSettingsPanel();

document.getElementById("install-plugin-btn").onclick();
let form = document.getElementById("plugin-install-form");
form.elements.source_kind.value = "marketplace";
await root.dispatch("change", form.elements.source_kind);
form = document.getElementById("plugin-install-form");
form.elements.marketplace_provider.value = "claude";
await root.dispatch("change", form.elements.marketplace_provider);

form = document.getElementById("plugin-install-form");
await root.dispatch("click", root.findButton("load-marketplace"));
await flush();

form = document.getElementById("plugin-install-form");
const htmlBeforeSubmit = root.innerHTML;
await root.dispatch("submit", form);
await flush();

console.log(JSON.stringify({
    installPayloads: globalThis.__installPayloads,
    notifications: globalThis.__notifications,
    htmlBeforeSubmit,
}));
""".strip(),
    )

    assert payload["installPayloads"] == []
    assert "npm is not supported" in str(payload["htmlBeforeSubmit"])
    assert "settings.plugins.unsupported" in str(payload["htmlBeforeSubmit"])
    assert any(
        item.get("tone") == "warning" and item.get("message") == "npm is not supported"
        for item in cast(list[dict[str, JsonValue]], payload["notifications"])
    )


def test_plugins_settings_loads_claude_marketplace_with_refresh(
    tmp_path: Path,
) -> None:
    payload = _run_plugins_settings_script(
        tmp_path=tmp_path,
        fetch_registry={"plugins": [], "diagnostics": []},
        marketplace={"plugins": []},
        runner_source="""
import { bindPluginsSettingsHandlers, loadPluginsSettingsPanel } from "./pluginsSettings.mjs";

const root = installGlobals();
bindPluginsSettingsHandlers();
await loadPluginsSettingsPanel();

document.getElementById("install-plugin-btn").onclick();
let form = document.getElementById("plugin-install-form");
form.elements.source_kind.value = "marketplace";
await root.dispatch("change", form.elements.source_kind);
form = document.getElementById("plugin-install-form");
form.elements.marketplace_provider.value = "claude";
await root.dispatch("change", form.elements.marketplace_provider);

form = document.getElementById("plugin-install-form");
await root.dispatch("click", root.findButton("load-marketplace"));
await flush();

console.log(JSON.stringify({
    marketplaceRequest: globalThis.__marketplaceRequests[0],
    html: root.innerHTML,
}));
""".strip(),
    )

    assert payload["marketplaceRequest"] == {
        "marketplacePath": "claude-plugins-official",
        "options": {
            "marketplace_provider": "claude",
            "marketplace_source": "anthropics/claude-plugins-official",
            "marketplace_ref": "",
            "refresh": True,
        },
    }
    assert "settings.plugins.marketplace_ref" not in str(payload["html"])


def test_plugins_settings_marketplace_without_latest_uses_semantic_version_details(
    tmp_path: Path,
) -> None:
    payload = _run_plugins_settings_script(
        tmp_path=tmp_path,
        fetch_registry={"plugins": [], "diagnostics": []},
        marketplace={
            "plugins": [
                {
                    "name": "quality",
                    "versions": [
                        {
                            "version": "1.0.0-alpha.1",
                            "sha256": "sha-alpha-1",
                            "source": {
                                "kind": "git",
                                "value": "https://repo/quality",
                                "ref": "v1.0.0-alpha.1",
                            },
                        },
                        {
                            "version": "1.0.0-alpha.beta",
                            "sha256": "sha-alpha-beta",
                            "source": {
                                "kind": "git",
                                "value": "https://repo/quality",
                                "ref": "v1.0.0-alpha.beta",
                            },
                        },
                    ],
                }
            ]
        },
        runner_source="""
import { bindPluginsSettingsHandlers, loadPluginsSettingsPanel } from "./pluginsSettings.mjs";

const root = installGlobals();
bindPluginsSettingsHandlers();
await loadPluginsSettingsPanel();

document.getElementById("install-plugin-btn").onclick();
let form = document.getElementById("plugin-install-form");
form.elements.source_kind.value = "marketplace";
await root.dispatch("change", form.elements.source_kind);

form = document.getElementById("plugin-install-form");
form.elements.marketplace.value = "C:/plugins/marketplace.json";
await root.dispatch("input", form.elements.marketplace);
await root.dispatch("click", root.findButton("load-marketplace"));
await flush();

console.log(JSON.stringify({
    htmlAfterLoad: root.innerHTML,
}));
""".strip(),
    )

    assert "sha-alpha-beta" in str(payload["htmlAfterLoad"])


def test_plugins_settings_configure_submits_typed_user_config(
    tmp_path: Path,
) -> None:
    payload = _run_plugins_settings_script(
        tmp_path=tmp_path,
        fetch_registry={
            "plugins": [
                {
                    "name": "quality",
                    "version": "1.0.0",
                    "scope": "user",
                    "enabled": True,
                    "source": {"kind": "local", "value": "C:/plugins/quality"},
                    "manifest": {
                        "description": "Quality plugin",
                        "user_config": {
                            "paths": {"type": "array", "title": "Paths"},
                            "options": {"type": "object", "title": "Options"},
                            "enabled": {"type": "boolean", "title": "Enabled"},
                            "limit": {"type": "integer", "title": "Limit"},
                            "token": {
                                "type": "string",
                                "title": "Token",
                                "sensitive": True,
                            },
                        },
                    },
                    "user_config": {"token": "<configured>"},
                    "component_counts": {},
                }
            ],
            "diagnostics": [],
        },
        marketplace={"plugins": []},
        runner_source="""
import { bindPluginsSettingsHandlers, loadPluginsSettingsPanel } from "./pluginsSettings.mjs";

const root = installGlobals();
bindPluginsSettingsHandlers();
await loadPluginsSettingsPanel();

await root.dispatch("click", root.findButton("configure", "user:quality"));
await flush();

const form = document.getElementById("plugin-config-form");
form.elements.paths.value = "[\\"src\\",\\"tests\\"]";
form.elements.options.value = "{\\"strict\\":true}";
form.elements.enabled.checked = true;
form.elements.limit.value = "1e2";
form.elements.token.value = "";

await root.dispatch("submit", form);
await flush();

console.log(JSON.stringify({
    configurePayload: globalThis.__configurePayloads[0],
    notifications: globalThis.__notifications,
}));
""".strip(),
    )

    assert payload["configurePayload"] == {
        "name": "quality",
        "payload": {
            "scope": "user",
            "user_config": {
                "paths": ["src", "tests"],
                "options": {"strict": True},
                "enabled": True,
                "limit": 100,
            },
        },
    }


def test_plugins_settings_configure_round_trips_json_string_config(
    tmp_path: Path,
) -> None:
    payload = _run_plugins_settings_script(
        tmp_path=tmp_path,
        fetch_registry={
            "plugins": [
                {
                    "name": "quality",
                    "version": "1.0.0",
                    "scope": "user",
                    "enabled": True,
                    "source": {"kind": "local", "value": "C:/plugins/quality"},
                    "manifest": {
                        "description": "Quality plugin",
                        "user_config": {
                            "payload": {"type": "json", "title": "Payload"},
                        },
                    },
                    "user_config": {"payload": "token"},
                    "component_counts": {},
                }
            ],
            "diagnostics": [],
        },
        marketplace={"plugins": []},
        runner_source="""
import { bindPluginsSettingsHandlers, loadPluginsSettingsPanel } from "./pluginsSettings.mjs";

const root = installGlobals();
bindPluginsSettingsHandlers();
await loadPluginsSettingsPanel();

await root.dispatch("click", root.findButton("configure", "user:quality"));
await flush();

const form = document.getElementById("plugin-config-form");
const renderedValue = form.elements.payload.value;

await root.dispatch("submit", form);
await flush();

console.log(JSON.stringify({
    renderedValue,
    configurePayload: globalThis.__configurePayloads[0],
    notifications: globalThis.__notifications,
}));
""".strip(),
    )

    assert payload["renderedValue"] == '"token"'
    assert payload["configurePayload"] == {
        "name": "quality",
        "payload": {
            "scope": "user",
            "user_config": {
                "payload": "token",
            },
        },
    }


def test_plugins_settings_configure_rejects_fractional_integer_config(
    tmp_path: Path,
) -> None:
    payload = _run_plugins_settings_script(
        tmp_path=tmp_path,
        fetch_registry={
            "plugins": [
                {
                    "name": "quality",
                    "version": "1.0.0",
                    "scope": "user",
                    "enabled": True,
                    "source": {"kind": "local", "value": "C:/plugins/quality"},
                    "manifest": {
                        "description": "Quality plugin",
                        "user_config": {
                            "limit": {"type": "integer", "title": "Limit"},
                        },
                    },
                    "user_config": {},
                    "component_counts": {},
                }
            ],
            "diagnostics": [],
        },
        marketplace={"plugins": []},
        runner_source="""
import { bindPluginsSettingsHandlers, loadPluginsSettingsPanel } from "./pluginsSettings.mjs";

const root = installGlobals();
bindPluginsSettingsHandlers();
await loadPluginsSettingsPanel();

await root.dispatch("click", root.findButton("configure", "user:quality"));
await flush();

const form = document.getElementById("plugin-config-form");
form.elements.limit.value = "2.9";

await root.dispatch("submit", form);
await flush();

console.log(JSON.stringify({
    configurePayloads: globalThis.__configurePayloads,
    notifications: globalThis.__notifications,
}));
""".strip(),
    )

    notifications = cast(list[dict[str, JsonValue]], payload["notifications"])
    assert payload["configurePayloads"] == []
    assert {
        "tone": "warning",
        "message": "settings.plugins.invalid_integer_config",
    } in notifications


def test_plugins_settings_configure_omits_blank_optional_typed_fields(
    tmp_path: Path,
) -> None:
    payload = _run_plugins_settings_script(
        tmp_path=tmp_path,
        fetch_registry={
            "plugins": [
                {
                    "name": "quality",
                    "version": "1.0.0",
                    "scope": "user",
                    "enabled": True,
                    "source": {"kind": "local", "value": "C:/plugins/quality"},
                    "manifest": {
                        "description": "Quality plugin",
                        "user_config": {
                            "paths": {"type": "array", "title": "Paths"},
                            "options": {"type": "object", "title": "Options"},
                            "limit": {"type": "integer", "title": "Limit"},
                            "label": {"type": "string", "title": "Label"},
                        },
                    },
                    "user_config": {},
                    "component_counts": {},
                }
            ],
            "diagnostics": [],
        },
        marketplace={"plugins": []},
        runner_source="""
import { bindPluginsSettingsHandlers, loadPluginsSettingsPanel } from "./pluginsSettings.mjs";

const root = installGlobals();
bindPluginsSettingsHandlers();
await loadPluginsSettingsPanel();

await root.dispatch("click", root.findButton("configure", "user:quality"));
await flush();

const form = document.getElementById("plugin-config-form");
form.elements.label.value = "ci";

await root.dispatch("submit", form);
await flush();

console.log(JSON.stringify({
    configurePayload: globalThis.__configurePayloads[0],
    notifications: globalThis.__notifications,
}));
""".strip(),
    )

    assert payload["configurePayload"] == {
        "name": "quality",
        "payload": {
            "scope": "user",
            "user_config": {
                "label": "ci",
            },
        },
    }


def test_plugins_settings_configure_submits_blank_existing_optional_fields(
    tmp_path: Path,
) -> None:
    payload = _run_plugins_settings_script(
        tmp_path=tmp_path,
        fetch_registry={
            "plugins": [
                {
                    "name": "quality",
                    "version": "1.0.0",
                    "scope": "user",
                    "enabled": True,
                    "source": {"kind": "local", "value": "C:/plugins/quality"},
                    "manifest": {
                        "description": "Quality plugin",
                        "user_config": {
                            "limit": {"type": "integer", "title": "Limit"},
                            "label": {"type": "string", "title": "Label"},
                        },
                    },
                    "user_config": {"limit": 3, "label": "ci"},
                    "component_counts": {},
                }
            ],
            "diagnostics": [],
        },
        marketplace={"plugins": []},
        runner_source="""
import { bindPluginsSettingsHandlers, loadPluginsSettingsPanel } from "./pluginsSettings.mjs";

const root = installGlobals();
bindPluginsSettingsHandlers();
await loadPluginsSettingsPanel();

await root.dispatch("click", root.findButton("configure", "user:quality"));
await flush();

const form = document.getElementById("plugin-config-form");
form.elements.limit.value = "";
form.elements.label.value = "";

await root.dispatch("submit", form);
await flush();

console.log(JSON.stringify({
    configurePayload: globalThis.__configurePayloads[0],
    notifications: globalThis.__notifications,
}));
""".strip(),
    )

    assert payload["configurePayload"] == {
        "name": "quality",
        "payload": {
            "scope": "user",
            "user_config": {
                "limit": "",
                "label": "",
            },
        },
    }


def test_plugins_settings_configure_shorthand_user_config_as_string(
    tmp_path: Path,
) -> None:
    payload = _run_plugins_settings_script(
        tmp_path=tmp_path,
        fetch_registry={
            "plugins": [
                {
                    "name": "quality",
                    "version": "1.0.0",
                    "scope": "user",
                    "enabled": True,
                    "source": {"kind": "local", "value": "C:/plugins/quality"},
                    "manifest": {
                        "description": "Quality plugin",
                        "user_config": {
                            "endpoint": {"title": "Endpoint"},
                        },
                    },
                    "user_config": {},
                    "component_counts": {},
                }
            ],
            "diagnostics": [],
        },
        marketplace={"plugins": []},
        runner_source="""
import { bindPluginsSettingsHandlers, loadPluginsSettingsPanel } from "./pluginsSettings.mjs";

const root = installGlobals();
bindPluginsSettingsHandlers();
await loadPluginsSettingsPanel();

await root.dispatch("click", root.findButton("configure", "user:quality"));
await flush();

const form = document.getElementById("plugin-config-form");
form.elements.endpoint.value = "https://docs.test";

await root.dispatch("submit", form);
await flush();

console.log(JSON.stringify({
    tagName: form.elements.endpoint.tagName,
    configurePayload: globalThis.__configurePayloads[0],
    notifications: globalThis.__notifications,
}));
""".strip(),
    )

    assert payload["tagName"] == "INPUT"
    assert payload["configurePayload"] == {
        "name": "quality",
        "payload": {
            "scope": "user",
            "user_config": {
                "endpoint": "https://docs.test",
            },
        },
    }


def test_plugins_settings_configure_preserves_configured_sensitive_boolean_when_unchanged(
    tmp_path: Path,
) -> None:
    payload = _run_plugins_settings_script(
        tmp_path=tmp_path,
        fetch_registry={
            "plugins": [
                {
                    "name": "quality",
                    "version": "1.0.0",
                    "scope": "user",
                    "enabled": True,
                    "source": {"kind": "local", "value": "C:/plugins/quality"},
                    "manifest": {
                        "description": "Quality plugin",
                        "user_config": {
                            "allow": {
                                "type": "boolean",
                                "title": "Allow",
                                "sensitive": True,
                            },
                        },
                    },
                    "user_config": {"allow": "<configured>"},
                    "component_counts": {},
                }
            ],
            "diagnostics": [],
        },
        marketplace={"plugins": []},
        runner_source="""
import { bindPluginsSettingsHandlers, loadPluginsSettingsPanel } from "./pluginsSettings.mjs";

const root = installGlobals();
bindPluginsSettingsHandlers();
await loadPluginsSettingsPanel();

await root.dispatch("click", root.findButton("configure", "user:quality"));
await flush();

const form = document.getElementById("plugin-config-form");

await root.dispatch("submit", form);
await flush();

console.log(JSON.stringify({
    checked: form.elements.allow.checked,
    configured: form.elements.allow.getAttribute("data-plugin-config-configured"),
    sensitive: form.elements.allow.getAttribute("data-plugin-config-sensitive"),
    configurePayload: globalThis.__configurePayloads[0],
    notifications: globalThis.__notifications,
}));
""".strip(),
    )

    assert payload["checked"] is True
    assert payload["configured"] == "true"
    assert payload["sensitive"] == "true"
    assert payload["configurePayload"] == {
        "name": "quality",
        "payload": {
            "scope": "user",
            "user_config": {},
        },
    }


def test_plugins_settings_configure_submits_changed_configured_sensitive_boolean(
    tmp_path: Path,
) -> None:
    payload = _run_plugins_settings_script(
        tmp_path=tmp_path,
        fetch_registry={
            "plugins": [
                {
                    "name": "quality",
                    "version": "1.0.0",
                    "scope": "user",
                    "enabled": True,
                    "source": {"kind": "local", "value": "C:/plugins/quality"},
                    "manifest": {
                        "name": "quality",
                        "version": "1.0.0",
                        "user_config": {
                            "allow": {
                                "type": "boolean",
                                "title": "Allow",
                                "sensitive": True,
                            },
                        },
                    },
                    "user_config": {"allow": "<configured>"},
                    "component_counts": {},
                }
            ],
            "diagnostics": [],
        },
        marketplace={"plugins": []},
        runner_source="""
import { bindPluginsSettingsHandlers, loadPluginsSettingsPanel } from "./pluginsSettings.mjs";

const root = installGlobals();
bindPluginsSettingsHandlers();
await loadPluginsSettingsPanel();

await root.dispatch("click", root.findButton("configure", "user:quality"));
await flush();

const form = document.getElementById("plugin-config-form");
form.elements.allow.checked = false;
await root.dispatch("change", form.elements.allow);

await root.dispatch("submit", form);
await flush();

console.log(JSON.stringify({
    dirty: form.elements.allow.getAttribute("data-plugin-config-dirty"),
    configurePayload: globalThis.__configurePayloads[0],
    notifications: globalThis.__notifications,
}));
""".strip(),
    )

    assert payload["dirty"] == "true"
    assert payload["configurePayload"] == {
        "name": "quality",
        "payload": {
            "scope": "user",
            "user_config": {
                "allow": False,
            },
        },
    }


def test_plugins_settings_configure_preserves_sensitive_json_when_unchanged(
    tmp_path: Path,
) -> None:
    payload = _run_plugins_settings_script(
        tmp_path=tmp_path,
        fetch_registry={
            "plugins": [
                {
                    "name": "quality",
                    "version": "1.0.0",
                    "scope": "user",
                    "enabled": True,
                    "source": {"kind": "local", "value": "C:/plugins/quality"},
                    "manifest": {
                        "description": "Quality plugin",
                        "user_config": {
                            "payload": {
                                "type": "object",
                                "title": "Payload",
                                "sensitive": True,
                            },
                        },
                    },
                    "user_config": {"payload": "<configured>"},
                    "component_counts": {},
                }
            ],
            "diagnostics": [],
        },
        marketplace={"plugins": []},
        runner_source="""
import { bindPluginsSettingsHandlers, loadPluginsSettingsPanel } from "./pluginsSettings.mjs";

const root = installGlobals();
bindPluginsSettingsHandlers();
await loadPluginsSettingsPanel();

await root.dispatch("click", root.findButton("configure", "user:quality"));
await flush();

const form = document.getElementById("plugin-config-form");

await root.dispatch("submit", form);
await flush();

console.log(JSON.stringify({
    value: form.elements.payload.value,
    configured: form.elements.payload.getAttribute("data-plugin-config-configured"),
    sensitive: form.elements.payload.getAttribute("data-plugin-config-sensitive"),
    configurePayload: globalThis.__configurePayloads[0],
    notifications: globalThis.__notifications,
}));
""".strip(),
    )

    assert payload["value"] == ""
    assert payload["configured"] == "true"
    assert payload["sensitive"] == "true"
    assert payload["configurePayload"] == {
        "name": "quality",
        "payload": {
            "scope": "user",
            "user_config": {},
        },
    }


def _run_plugins_settings_script(
    *,
    tmp_path: Path,
    fetch_registry: dict[str, JsonValue],
    marketplace: dict[str, JsonValue],
    runner_source: str,
) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "settings"
        / "pluginsSettings.js"
    )
    mock_api_path = tmp_path / "mockApi.mjs"
    mock_feedback_path = tmp_path / "mockFeedback.mjs"
    mock_i18n_path = tmp_path / "mockI18n.mjs"
    mock_logger_path = tmp_path / "mockLogger.mjs"
    module_under_test_path = tmp_path / "pluginsSettings.mjs"
    runner_path = tmp_path / "runner.mjs"

    mock_api_path.write_text(
        """
let registry = __FETCH_REGISTRY__;
const marketplace = __MARKETPLACE__;

export async function fetchPluginsRuntime() {
    return registry;
}

export async function fetchPluginMarketplace(marketplacePath, options = {}) {
    globalThis.__marketplaceRequests.push({ marketplacePath, options });
    return marketplace;
}

export async function searchPluginMarketplace(marketplacePath, query, options = {}) {
    globalThis.__marketplaceSearchRequests.push({ marketplacePath, query, options });
    return marketplace;
}

export async function inspectPluginMarketplace(marketplacePath, options = {}) {
    globalThis.__marketplaceInspectRequests.push({ marketplacePath, options });
    return globalThis.__marketplaceInspectRegistry;
}

export async function installPlugin(payload) {
    globalThis.__installPayloads.push(payload);
    return { status: "ok" };
}

export async function configurePlugin(name, payload) {
    globalThis.__configurePayloads.push({ name, payload });
    return { status: "ok" };
}

export async function deletePlugin() { return { status: "ok" }; }
export async function disablePlugin() { return { status: "ok" }; }
export async function enablePlugin() { return { status: "ok" }; }
export async function updatePlugin(name, payload) {
    globalThis.__updatePayloads.push({ name, payload });
    return { status: "ok" };
}
export async function validatePlugin() {
    return { plugins: [], diagnostics: [] };
}
""".replace("__FETCH_REGISTRY__", json.dumps(fetch_registry))
        .replace("__MARKETPLACE__", json.dumps(marketplace))
        .strip(),
        encoding="utf-8",
    )
    mock_feedback_path.write_text(
        """
export async function showFormDialog(payload) {
    globalThis.__dialogPayloads.push(payload);
    return globalThis.__dialogResult;
}

export async function showTextInputDialog() {
    return globalThis.__textInputResult;
}

export function showToast(payload) {
    globalThis.__notifications.push(payload);
}
""".strip(),
        encoding="utf-8",
    )
    mock_i18n_path.write_text(
        """
export function t(key) {
    return key;
}

export function formatMessage(key, values = {}) {
    return `${key}:${JSON.stringify(values)}`;
}

export function translateDocument() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )
    mock_logger_path.write_text(
        """
export function errorToPayload(error, extra = {}) {
    return { error_message: String(error?.message || error || ""), ...extra };
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
class FakeElement {{
    constructor(tagName = "div", id = "") {{
        this.tagName = tagName.toUpperCase();
        this.id = id;
        this.style = {{ display: "block" }};
        this.value = "";
        this.checked = false;
        this.type = "text";
        this.name = "";
        this.disabled = false;
        this.form = null;
        this._innerHTML = "";
        this.className = "";
        this.attributes = new Map();
        this.listeners = new Map();
    }}

    get innerHTML() {{
        return this._innerHTML;
    }}

    set innerHTML(value) {{
        this._innerHTML = String(value || "");
        if (this.id === "plugins-settings-root") {{
            parseRoot(this, this._innerHTML);
        }}
    }}

    get classList() {{
        return {{
            contains: name => this.className.split(/\\s+/).includes(name),
        }};
    }}

    addEventListener(type, handler) {{
        const handlers = this.listeners.get(type) || [];
        handlers.push(handler);
        this.listeners.set(type, handlers);
    }}

    async dispatch(type, target) {{
        const handlers = this.listeners.get(type) || [];
        const event = {{
            target,
            preventDefault() {{
                this.defaultPrevented = true;
            }},
        }};
        for (const handler of handlers) {{
            await handler(event);
        }}
        await flush();
    }}

    setAttribute(name, value) {{
        this.attributes.set(name, String(value));
        if (name === "class") this.className = String(value);
        if (name === "name") this.name = String(value);
        if (name === "id") this.id = String(value);
        if (name === "type") this.type = String(value);
    }}

    getAttribute(name) {{
        return this.attributes.get(name) || null;
    }}

    closest(selector) {{
        if (selector === "form") {{
            return this.form;
        }}
        if (selector === "[data-plugin-action]" && this.getAttribute("data-plugin-action")) {{
            return this;
        }}
        return null;
    }}

    findButton(action, key = "") {{
        return (this.buttons || []).find(button => {{
            const sameAction = button.getAttribute("data-plugin-action") === action;
            const sameKey = !key || button.getAttribute("data-plugin-key") === key;
            return sameAction && sameKey;
        }});
    }}
}}

class FakeInput extends FakeElement {{
    constructor() {{
        super("input");
    }}
}}

class FakeSelect extends FakeElement {{
    constructor() {{
        super("select");
    }}
}}

class FakeTextArea extends FakeElement {{
    constructor() {{
        super("textarea");
    }}
}}

class FakeButton extends FakeElement {{
    constructor() {{
        super("button");
    }}
}}

class FakeForm extends FakeElement {{
    constructor() {{
        super("form");
        this.elements = [];
    }}

    querySelectorAll(selector) {{
        if (selector !== "input[name], textarea[name]") {{
            return [];
        }}
        return this.elements.filter(element => ["INPUT", "TEXTAREA"].includes(element.tagName) && element.name);
    }}
}}

globalThis.HTMLInputElement = FakeInput;
globalThis.HTMLSelectElement = FakeSelect;
globalThis.HTMLTextAreaElement = FakeTextArea;
globalThis.HTMLButtonElement = FakeButton;
globalThis.HTMLFormElement = FakeForm;

function installGlobals() {{
    const root = new FakeElement("div", "plugins-settings-root");
    const panel = new FakeElement("div", "plugins-panel");
    panel.className = "active";
    const elements = new Map([
        ["plugins-settings-root", root],
        ["plugins-panel", panel],
        ["refresh-plugins-btn", new FakeButton()],
        ["validate-plugin-btn", new FakeButton()],
        ["install-plugin-btn", new FakeButton()],
    ]);
    for (const [id, element] of elements) {{
        element.id = id;
    }}
    globalThis.document = {{
        getElementById(id) {{
            const element = elements.get(id);
            if (!element) {{
                throw new Error(`Missing element: ${{id}}`);
            }}
            return element;
        }},
    }};
    root.__elements = elements;
    globalThis.__notifications = [];
    globalThis.__installPayloads = [];
    globalThis.__marketplaceRequests = [];
    globalThis.__marketplaceSearchRequests = [];
    globalThis.__marketplaceInspectRequests = [];
    globalThis.__marketplaceInspectRegistry = {{ plugins: [], diagnostics: [] }};
    globalThis.__configurePayloads = [];
    globalThis.__updatePayloads = [];
    globalThis.__dialogPayloads = [];
    globalThis.__dialogResult = null;
    globalThis.__textInputResult = null;
    return root;
}}

function parseRoot(root, html) {{
    for (const key of Array.from(root.__elements.keys())) {{
        if (key.startsWith("plugin-")) {{
            root.__elements.delete(key);
        }}
    }}
    root.buttons = [];
    for (const form of parseForms(html)) {{
        for (const button of parseButtons(form.innerHTML)) {{
            button.form = form;
            root.buttons.push(button);
        }}
        root.__elements.set(form.id, form);
    }}
    root.buttons.push(...parseButtons(html));
}}

function parseForms(html) {{
    const forms = [];
    const formPattern = /<form\\b([^>]*)>([\\s\\S]*?)<\\/form>/g;
    let match;
    while ((match = formPattern.exec(html)) !== null) {{
        const form = new FakeForm();
        applyAttributes(form, match[1]);
        form._innerHTML = match[2];
        form.elements = parseFormElements(form, match[2]);
        forms.push(form);
    }}
    return forms;
}}

function parseFormElements(form, html) {{
    const elements = [];
    for (const element of parseInputs(form, html)) elements.push(element);
    for (const element of parseSelects(form, html)) elements.push(element);
    for (const element of parseTextAreas(form, html)) elements.push(element);
    for (const element of parseButtons(html)) element.form = form;
    for (const element of elements) {{
        element.form = form;
        if (element.name) elements[element.name] = element;
    }}
    return elements;
}}

function parseInputs(form, html) {{
    const inputs = [];
    const pattern = /<input\\b([^>]*)>/g;
    let match;
    while ((match = pattern.exec(html)) !== null) {{
        const input = new FakeInput();
        applyAttributes(input, match[1]);
        input.form = form;
        input.value = input.getAttribute("value") || "";
        input.checked = /\\bchecked\\b/.test(match[1]);
        input.disabled = /\\bdisabled\\b/.test(match[1]);
        inputs.push(input);
    }}
    return inputs;
}}

function parseSelects(form, html) {{
    const selects = [];
    const pattern = /<select\\b([^>]*)>([\\s\\S]*?)<\\/select>/g;
    let match;
    while ((match = pattern.exec(html)) !== null) {{
        const select = new FakeSelect();
        applyAttributes(select, match[1]);
        select.form = form;
        select.value = selectedOptionValue(match[2]);
        selects.push(select);
    }}
    return selects;
}}

function parseTextAreas(form, html) {{
    const textAreas = [];
    const pattern = /<textarea\\b([^>]*)>([\\s\\S]*?)<\\/textarea>/g;
    let match;
    while ((match = pattern.exec(html)) !== null) {{
        const textArea = new FakeTextArea();
        applyAttributes(textArea, match[1]);
        textArea.form = form;
        textArea.value = unescapeHtml(match[2].trim());
        textAreas.push(textArea);
    }}
    return textAreas;
}}

function parseButtons(html) {{
    const buttons = [];
    const pattern = /<button\\b([^>]*)>/g;
    let match;
    while ((match = pattern.exec(html)) !== null) {{
        const button = new FakeButton();
        applyAttributes(button, match[1]);
        button.disabled = /\\bdisabled\\b/.test(match[1]);
        buttons.push(button);
    }}
    return buttons;
}}

function applyAttributes(element, source) {{
    const pattern = /([:\\w-]+)(?:="([^"]*)")?/g;
    let match;
    while ((match = pattern.exec(source)) !== null) {{
        const name = match[1];
        const value = unescapeHtml(match[2] || "");
        element.setAttribute(name, value);
    }}
}}

function selectedOptionValue(html) {{
    const options = Array.from(html.matchAll(/<option\\b([^>]*)>/g));
    const selected = options.find(option => /\\bselected\\b/.test(option[1])) || options[0];
    if (!selected) return "";
    const value = /value="([^"]*)"/.exec(selected[1]);
    return unescapeHtml(value ? value[1] : "");
}}

function unescapeHtml(value) {{
    return String(value || "")
        .replaceAll("&quot;", '"')
        .replaceAll("&#039;", "'")
        .replaceAll("&lt;", "<")
        .replaceAll("&gt;", ">")
        .replaceAll("&amp;", "&");
}}

async function flush() {{
    await Promise.resolve();
    await new Promise(resolve => setTimeout(resolve, 0));
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
