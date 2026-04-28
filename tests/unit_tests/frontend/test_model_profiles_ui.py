# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

import json
from pathlib import Path
import subprocess
from typing import cast

from .css_helpers import load_components_css

DEFAULT_MOCK_API_SOURCE = """
export async function fetchModelProfiles() {
    return {
        default: {
            provider: "openai_compatible",
            model: "fake-chat-model",
            base_url: "http://127.0.0.1:8001/v1",
            api_key: "saved-secret-key",
            has_api_key: true,
            is_default: true,
            temperature: 0.3,
            top_p: 0.8,
            max_tokens: 512,
            context_window: 128000,
            connect_timeout_seconds: 15,
            capabilities: {
                input: { text: true, image: true, audio: false, video: false, pdf: false },
                output: { text: true, image: false, audio: false, video: false, pdf: false },
            },
            input_modalities: ["image"],
        },
        "ui-regression-profile": {
            provider: "openai_compatible",
            model: "fake-chat-model",
            base_url: "http://127.0.0.1:8001/v1",
            api_key: "saved-secret-key",
            has_api_key: true,
            is_default: false,
            temperature: 0.3,
            top_p: 0.8,
            max_tokens: 512,
            context_window: 64000,
            connect_timeout_seconds: 15,
            capabilities: {
                input: { text: true, image: false, audio: false, video: false, pdf: false },
                output: { text: true, image: false, audio: false, video: false, pdf: false },
            },
            input_modalities: [],
        },
    };
}

export async function fetchModelFallbackConfig() {
    return {
        policies: [
            {
                policy_id: "same_provider_then_other_provider",
                name: "Same Provider Then Other Provider",
                enabled: true,
            },
            {
                policy_id: "other_provider_only",
                name: "Other Provider Only",
                enabled: true,
            },
        ],
    };
}

export async function fetchModelCatalog() {
    globalThis.__fetchModelCatalogCount = (globalThis.__fetchModelCatalogCount || 0) + 1;
    return {
        ok: true,
        source_url: "https://models.dev/api.json",
        cache_age_seconds: 0,
        providers: [
            {
                id: "openai",
                name: "OpenAI",
                api: "https://api.openai.com/v1",
                env: ["OPENAI_API_KEY"],
                models: [
                    {
                        id: "gpt-4o",
                        name: "GPT-4o",
                        context_window: 128000,
                        output_limit: 16384,
                        reasoning: false,
                        tool_call: true,
                        capabilities: {
                            input: { text: true, image: true, audio: false, video: false, pdf: false },
                            output: { text: true, image: false, audio: false, video: false, pdf: false },
                        },
                        input_modalities: ["image"],
                    },
                ],
            },
        ],
    };
}

export async function refreshModelCatalog() {
    globalThis.__refreshModelCatalogCount = (globalThis.__refreshModelCatalogCount || 0) + 1;
    return fetchModelCatalog();
}

export async function probeModelConnection(payload) {
    globalThis.__probePayload = payload;
    return {
        ok: true,
        latency_ms: 42,
        token_usage: {
            total_tokens: 9,
        },
    };
}

export async function discoverModelCatalog(payload) {
    globalThis.__discoverPayload = payload;
    return {
        ok: true,
        latency_ms: 37,
        models: ["fake-chat-model", "reasoning-model"],
        model_entries: [
            {
                model: "fake-chat-model",
                context_window: 128000,
                capabilities: {
                    input: { text: true, image: true, audio: false, video: false, pdf: false },
                    output: { text: true, image: false, audio: false, video: false, pdf: false },
                },
                input_modalities: ["image"],
            },
            {
                model: "reasoning-model",
                context_window: null,
                capabilities: {
                    input: { text: true, image: false, audio: false, video: false, pdf: false },
                    output: { text: true, image: false, audio: false, video: false, pdf: false },
                },
                input_modalities: [],
            },
        ],
    };
}

export async function saveModelProfile(name, profile) {
    globalThis.__savedProfile = { name, profile };
}

export async function startCodeAgentOAuth() {
    globalThis.__codeAgentOAuthStartCalls = (globalThis.__codeAgentOAuthStartCalls || 0) + 1;
    return {
        auth_session_id: "mock-auth-session",
        authorization_url: "https://example.test/codeagent-sso",
    };
}

export async function fetchCodeAgentOAuthSession(authSessionId) {
    globalThis.__codeAgentOAuthSessionChecks = globalThis.__codeAgentOAuthSessionChecks || [];
    globalThis.__codeAgentOAuthSessionChecks.push(authSessionId);
    return {
        completed: true,
    };
}

export async function verifyCodeAgentAuth(profileName) {
    globalThis.__codeAgentAuthVerifyCalls = globalThis.__codeAgentAuthVerifyCalls || [];
    globalThis.__codeAgentAuthVerifyCalls.push(profileName);
    return {
        status: "valid",
        checked_at: "2026-04-27T02:00:00Z",
        detail: null,
    };
}

export async function reloadModelConfig() {
    globalThis.__reloadCalled = true;
}

export async function deleteModelProfile(name) {
    globalThis.__deletedProfileName = name;
}
""".strip()


def test_codeagent_sso_button_uses_shared_form_control_height() -> None:
    components_css = load_components_css()
    rule_start = components_css.index(".codeagent-sso-login-btn {")
    rule_end = components_css.index(".codeagent-sso-login-btn:disabled {", rule_start)
    button_rule = components_css[rule_start:rule_end]
    override_rule_start = components_css.index(
        ".settings-list-action.codeagent-sso-login-btn {"
    )
    override_rule_end = components_css.index(
        ".settings-list-action:hover {", override_rule_start
    )
    override_rule = components_css[override_rule_start:override_rule_end]

    assert "min-height: 42px;" in button_rule
    assert "white-space: normal;" in button_rule
    assert "min-height: 42px;" in override_rule
    assert "width: fit-content;" in override_rule
    assert (
        "display: flex;"
        in components_css[
            components_css.index(
                ".profile-codeagent-credentials-row {"
            ) : components_css.index(".profile-codeagent-auth-method-row {")
        ]
    )
    maas_rule_start = components_css.index(".profile-maas-credentials-row {")
    maas_rule_end = components_css.index(
        ".profile-codeagent-credentials-row {", maas_rule_start
    )
    maas_rule = components_css[maas_rule_start:maas_rule_end]
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in maas_rule


def test_codeagent_auth_fields_use_split_rows_and_i18n_markup() -> None:
    template_source = (
        Path(__file__).resolve().parents[3]
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "settings"
        / "modelProfiles"
        / "template.js"
    ).read_text(encoding="utf-8")

    assert "profile-codeagent-auth-method-row" in template_source
    assert "profile-codeagent-auth-detail-row" in template_source
    assert 'data-i18n="settings.model.codeagent_auth_method"' in template_source
    assert 'data-i18n="settings.model.codeagent_sso_field"' in template_source
    assert 'data-i18n="settings.model.codeagent_username"' in template_source
    assert 'data-i18n="settings.model.codeagent_password"' in template_source
    assert (
        'data-i18n-placeholder="settings.model.codeagent_username_placeholder"'
        in template_source
    )
    assert (
        'data-i18n-placeholder="settings.model.codeagent_password_placeholder"'
        in template_source
    )


def test_maas_auth_fields_use_i18n_markup() -> None:
    template_source = Path(
        "frontend/dist/js/components/settings/modelProfiles/template.js"
    ).read_text(encoding="utf-8")

    assert (
        'class="profile-credentials-row profile-maas-credentials-row"'
        in template_source
    )
    assert (
        'for="profile-maas-username" data-i18n="settings.model.username"'
        in template_source
    )
    assert (
        'id="profile-maas-username" placeholder="username" '
        'data-i18n-placeholder="settings.model.username_placeholder"' in template_source
    )
    assert (
        'for="profile-maas-password" data-i18n="settings.model.password"'
        in template_source
    )
    assert (
        'id="profile-maas-password" placeholder="password" '
        'data-i18n-placeholder="settings.model.password_placeholder"' in template_source
    )


def test_codeagent_password_field_runtime_placeholder_uses_i18n(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-provider-codeagent-btn").onclick();
document.getElementById("profile-codeagent-auth-method").value = "password";
document.getElementById("profile-codeagent-auth-method").onchange();

console.log(JSON.stringify({
    placeholder: document.getElementById("profile-codeagent-password").placeholder,
}));
""".strip(),
        mock_i18n_source="""
const translations = {
    "settings.model.profile_name": "Profile Name",
    "settings.model.profile_name_placeholder": "profile",
    "settings.model.default_model_action": "Set as default model",
    "settings.model.step_provider_model": "Model Provider and Model",
    "settings.model.provider_external": "Model Marketplace",
    "settings.model.provider_external_copy": "Choose provider and model from the marketplace",
    "settings.model.provider_maas": "MaaS Model",
    "settings.model.provider_maas_copy": "Hosted model service platform",
    "settings.model.provider_codeagent": "CodeAgent Model",
    "settings.model.provider_codeagent_copy": "Use CodeAgent models with SSO or username/password sign-in",
    "settings.model.provider_custom": "Custom Model",
    "settings.model.provider_custom_copy": "Enter endpoint and model id manually",
    "settings.model.base_url": "Base URL",
    "settings.model.custom_base_url_placeholder": "Base URL",
    "settings.model.catalog_title": "Model Catalog",
    "settings.model.catalog_loading": "Loading model catalog...",
    "settings.model.catalog_refresh": "Refresh",
    "settings.model.provider": "Provider",
    "settings.model.catalog_provider_search": "Search providers",
    "settings.model.model": "Model",
    "settings.model.catalog_model_search": "Search models",
    "settings.model.custom_model_placeholder": "Model placeholder",
    "settings.model.api_key": "API Key",
    "settings.model.codeagent_auth_method": "Authentication Method",
    "settings.model.codeagent_auth_method_sso": "SSO Sign-In",
    "settings.model.codeagent_auth_method_password": "Username and Password",
    "settings.model.codeagent_sso_field": "SSO Sign-In",
    "settings.model.codeagent_sign_in_sso": "Sign in with SSO",
    "settings.model.codeagent_username": "Username",
    "settings.model.codeagent_username_placeholder": "localized username",
    "settings.model.codeagent_password": "Password",
    "settings.model.codeagent_password_placeholder": "localized password",
    "settings.model.temperature": "Temperature",
    "settings.model.top_p": "Top P",
    "settings.model.max_output_tokens": "Max Output Tokens",
    "settings.model.optional": "Optional",
    "settings.model.context_window": "Context Window",
    "settings.model.connect_timeout": "Connect Timeout",
    "settings.proxy.default_ssl": "SSL Verification",
    "settings.proxy.inherit_default": "Inherit Default",
    "settings.proxy.verify": "Verify",
    "settings.proxy.skip_verify": "Skip Verify",
    "settings.model.step_advanced": "Advanced Options",
    "settings.model.image_capability": "Image Input",
    "settings.model.image_capability_follow": "Follow detection",
    "settings.model.image_capability_supported": "Supports image input",
    "settings.model.image_capability_unsupported": "Text only",
    "settings.model.step_fallback": "Fallback Strategy",
    "settings.model.fallback_strategy": "Fallback Strategy",
    "settings.model.fallback_priority": "Fallback Priority",
    "settings.model.codeagent_credentials_ready": "Credentials ready",
    "settings.model.show_password": "Show password",
    "settings.model.hide_password": "Hide password",
    "settings.model.catalog_select_provider_first": "Select a provider first.",
    "settings.model.catalog_empty": "No providers match the search.",
    "settings.model.catalog_no_models": "No models match the search.",
    "settings.model.catalog_loaded": "{providers} providers, {models} models",
    "settings.model.catalog_cache_current": "just updated",
};

export function t(key) {
    return translations[key] || key;
}
""".strip(),
    )

    assert payload["placeholder"] == "localized password"


def test_maas_password_field_runtime_placeholder_uses_i18n(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-provider-maas-btn").onclick();

console.log(JSON.stringify({
    placeholder: document.getElementById("profile-maas-password").placeholder,
}));
""".strip(),
        mock_i18n_source="""
const translations = {
    "settings.model.profile_name": "Profile Name",
    "settings.model.profile_name_placeholder": "profile",
    "settings.model.default_model_action": "Set as default model",
    "settings.model.step_provider_model": "Model Provider and Model",
    "settings.model.provider_external": "Model Marketplace",
    "settings.model.provider_external_copy": "Choose provider and model from the marketplace",
    "settings.model.provider_maas": "MaaS Model",
    "settings.model.provider_maas_copy": "Hosted model service platform",
    "settings.model.provider_codeagent": "CodeAgent Model",
    "settings.model.provider_codeagent_copy": "Use CodeAgent models with SSO or username/password sign-in",
    "settings.model.provider_custom": "Custom Model",
    "settings.model.provider_custom_copy": "Enter endpoint and model id manually",
    "settings.model.base_url": "Base URL",
    "settings.model.custom_base_url_placeholder": "Base URL",
    "settings.model.catalog_title": "Model Catalog",
    "settings.model.catalog_loading": "Loading model catalog...",
    "settings.model.catalog_refresh": "Refresh",
    "settings.model.provider": "Provider",
    "settings.model.catalog_provider_search": "Search providers",
    "settings.model.model": "Model",
    "settings.model.catalog_model_search": "Search models",
    "settings.model.custom_model_placeholder": "Model placeholder",
    "settings.model.api_key": "API Key",
    "settings.model.username": "Username",
    "settings.model.username_placeholder": "localized username",
    "settings.model.password": "Password",
    "settings.model.password_placeholder": "localized password",
    "settings.model.temperature": "Temperature",
    "settings.model.top_p": "Top P",
    "settings.model.max_output_tokens": "Max Output Tokens",
    "settings.model.optional": "Optional",
    "settings.model.context_window": "Context Window",
    "settings.model.connect_timeout": "Connect Timeout",
    "settings.proxy.default_ssl": "SSL Verification",
    "settings.proxy.inherit_default": "Inherit Default",
    "settings.proxy.verify": "Verify",
    "settings.proxy.skip_verify": "Skip Verify",
    "settings.model.step_advanced": "Advanced Options",
    "settings.model.image_capability": "Image Input",
    "settings.model.image_capability_follow": "Follow detection",
    "settings.model.image_capability_supported": "Supports image input",
    "settings.model.image_capability_unsupported": "Text only",
    "settings.model.show_password": "Show password",
    "settings.model.hide_password": "Hide password",
    "settings.model.catalog_select_provider_first": "Select a provider first.",
    "settings.model.catalog_empty": "No providers match the search.",
    "settings.model.catalog_no_models": "No models match the search.",
    "settings.model.catalog_loaded": "{providers} providers, {models} models",
    "settings.model.catalog_cache_current": "just updated",
    "settings.model.catalog_cache_age": "{seconds}s old",
    "settings.model.catalog_reasoning": "reasoning",
    "settings.model.catalog_tools": "tools",
    "settings.model.capability_section": "Capabilities",
    "settings.model.show_api_key": "Show API key",
    "settings.model.hide_api_key": "Hide API key",
    "settings.model.advanced_summary": "Temperature {temperature}",
    "settings.model.catalog_selected": "Selected Model",
    "settings.model.catalog_selected_empty": "Choose a model from the catalog.",
    "settings.model.capability_image_input": "Image input",
    "settings.model.capability_text_only": "Text only",
    "settings.model.capability_unknown": "Capability unknown",
    "settings.model.no_model": "No model",
    "settings.model.no_endpoint": "No endpoint",
    "settings.model.fallback_policy_same_provider_then_other_provider": "Same Provider Then Other Provider",
    "settings.model.fallback_policy_other_provider_only": "Other Provider Only",
    "settings.model.unknown": "Unknown",
    "settings.action.test": "Test",
    "settings.action.edit": "Edit",
    "settings.action.delete": "Delete",
    "settings.action.cancel": "Cancel"
};

export function t(key) {
    return translations[key] || key;
}
""".strip(),
    )

    assert payload["placeholder"] == "localized password"


def test_saving_model_profile_restores_profile_list_visibility(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-name").value = "ui-regression-profile";
document.getElementById("profile-provider").value = "openai_compatible";
document.getElementById("profile-is-default").checked = true;
document.getElementById("profile-model").value = "fake-chat-model";
document.getElementById("profile-base-url").value = "http://127.0.0.1:8001/v1";
document.getElementById("profile-api-key").value = "test-api-key";
document.getElementById("profile-temperature").value = "0.3";
document.getElementById("profile-top-p").value = "0.8";
document.getElementById("profile-max-tokens").value = "512";
document.getElementById("profile-context-window").value = "128000";

await document.getElementById("save-profile-btn").onclick();

console.log(JSON.stringify({
    notifications,
    listDisplay: document.getElementById("profiles-list").style.display,
    editorDisplay: document.getElementById("profile-editor").style.display,
    addButtonDisplay: document.getElementById("add-profile-btn").style.display,
    renderedHtml: document.getElementById("profiles-list").innerHTML,
    savedProfile: globalThis.__savedProfile,
}));
""".strip(),
    )

    rendered_html = cast(str, payload["renderedHtml"])
    notifications = cast(list[dict[str, JsonValue]], payload["notifications"])
    saved_profile = cast(dict[str, JsonValue], payload["savedProfile"])
    saved_profile_body = cast(dict[str, JsonValue], saved_profile["profile"])
    assert notifications == [
        {
            "title": "Profile Saved",
            "message": "Profile saved and reloaded.",
            "tone": "success",
        }
    ]
    assert payload["listDisplay"] == "block"
    assert payload["editorDisplay"] == "none"
    assert payload["addButtonDisplay"] == "inline-flex"
    assert "ui-regression-profile" in rendered_html
    assert saved_profile_body["provider"] == "openai_compatible"
    assert saved_profile_body["is_default"] is True
    assert saved_profile_body["context_window"] == 128000
    assert saved_profile_body["fallback_policy_id"] is None
    assert saved_profile_body["fallback_priority"] == 0


def test_profile_list_renders_input_capability_chip(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

console.log(JSON.stringify({
    renderedHtml: document.getElementById("profiles-list").innerHTML,
}));
""".strip(),
    )

    rendered_html = cast(str, payload["renderedHtml"])
    assert "Image input" in rendered_html
    assert "Text only" in rendered_html
    assert "profile-card-chip-capability-image" in rendered_html


def test_model_catalog_renders_provider_and_model_choices(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

const beforeAdd = {
    fetchCount: globalThis.__fetchModelCatalogCount || 0,
    panelDisplay: document.getElementById("model-catalog-panel").style.display,
};

document.getElementById("add-profile-btn").onclick();
await Promise.resolve();
await Promise.resolve();

console.log(JSON.stringify({
    beforeAdd,
    providerHtml: document.getElementById("model-catalog-provider-list").innerHTML,
    modelHtml: document.getElementById("model-catalog-model-list").innerHTML,
    providerListDisplay: document.getElementById("model-catalog-provider-list").style.display,
    modelListDisplay: document.getElementById("model-catalog-model-list").style.display,
    statusText: document.getElementById("model-catalog-status").textContent,
    panelDisplay: document.getElementById("model-catalog-panel").style.display,
    fetchCount: globalThis.__fetchModelCatalogCount || 0,
}));
""".strip(),
    )

    before_add = cast(dict[str, JsonValue], payload["beforeAdd"])
    assert before_add == {"fetchCount": 0, "panelDisplay": "none"}
    assert "OpenAI" in cast(str, payload["providerHtml"])
    assert "Select a provider first" in cast(str, payload["modelHtml"])
    assert payload["providerListDisplay"] == "none"
    assert payload["modelListDisplay"] == "none"
    assert "1 providers, 1 models" in cast(str, payload["statusText"])
    assert payload["panelDisplay"] == "flex"
    assert cast(int, payload["fetchCount"]) >= 1


def test_model_catalog_search_inputs_open_provider_and_model_lists(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("add-profile-btn").onclick();
await Promise.resolve();
await Promise.resolve();
document.getElementById("model-catalog-provider-search").onclick();
const providerListDisplay = document.getElementById("model-catalog-provider-list").style.display;
document.getElementById("model-catalog-model-search").onclick();
const modelListDisplay = document.getElementById("model-catalog-model-list").style.display;

console.log(JSON.stringify({
    providerListDisplay,
    modelListDisplay,
    modelHtml: document.getElementById("model-catalog-model-list").innerHTML,
    providerInputValue: document.getElementById("model-catalog-provider-search").value,
    modelInputValue: document.getElementById("model-catalog-model-search").value,
}));
""".strip(),
    )

    assert payload["providerListDisplay"] == "block"
    assert payload["modelListDisplay"] == "block"
    assert "Select a provider first" in cast(str, payload["modelHtml"])
    assert payload["providerInputValue"] == ""
    assert payload["modelInputValue"] == ""


def test_model_catalog_supports_keyboard_selection(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

function keyEvent(key) {
    return {
        key,
        defaultPrevented: false,
        preventDefault() {
            this.defaultPrevented = true;
        },
    };
}

document.getElementById("add-profile-btn").onclick();
await Promise.resolve();
await Promise.resolve();
document.getElementById("model-catalog-provider-search").onkeydown(keyEvent("ArrowDown"));
const providerHtmlAfterArrow = document.getElementById("model-catalog-provider-list").innerHTML;
document.getElementById("model-catalog-provider-search").onkeydown(keyEvent("Enter"));
document.getElementById("model-catalog-model-search").onkeydown(keyEvent("ArrowDown"));
document.getElementById("model-catalog-model-search").onkeydown(keyEvent("ArrowDown"));
const modelHtmlAfterArrow = document.getElementById("model-catalog-model-list").innerHTML;
document.getElementById("model-catalog-model-search").onkeydown(keyEvent("Enter"));

console.log(JSON.stringify({
    providerHtmlAfterArrow,
    modelHtmlAfterArrow,
    providerInputValue: document.getElementById("model-catalog-provider-search").value,
    modelInputValue: document.getElementById("model-catalog-model-search").value,
    modelValue: document.getElementById("profile-model").value,
    summary: document.getElementById("profile-model-summary").textContent,
}));
""".strip(),
    )

    assert "is-keyboard-active" in cast(str, payload["providerHtmlAfterArrow"])
    assert "is-keyboard-active" in cast(str, payload["modelHtmlAfterArrow"])
    assert payload["providerInputValue"] == "OpenAI"
    assert payload["modelInputValue"] == "GPT-4o"
    assert payload["modelValue"] == "gpt-4o"
    assert payload["summary"] == "Model Marketplace · OpenAI · GPT-4o"


def test_catalog_model_selection_prefills_and_saves_metadata(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("add-profile-btn").onclick();
await Promise.resolve();
await Promise.resolve();
document.getElementById("model-catalog-provider-list").querySelectorAll(".model-catalog-provider-btn")[0].onclick();
document.getElementById("model-catalog-model-list").querySelectorAll(".model-catalog-model-btn")[0].onclick();
document.getElementById("profile-api-key").value = "test-api-key";

const draft = {
    name: document.getElementById("profile-name").value,
    provider: document.getElementById("profile-provider").value,
    model: document.getElementById("profile-model").value,
    baseUrl: document.getElementById("profile-base-url").value,
    contextWindow: document.getElementById("profile-context-window").value,
    maxTokens: document.getElementById("profile-max-tokens").value,
    catalogDisplay: document.getElementById("model-catalog-panel").style.display,
    summary: document.getElementById("profile-model-summary").textContent,
};

await document.getElementById("save-profile-btn").onclick();

console.log(JSON.stringify({
    draft,
    savedProfile: globalThis.__savedProfile,
}));
""".strip(),
    )

    draft = cast(dict[str, JsonValue], payload["draft"])
    saved_profile = cast(dict[str, JsonValue], payload["savedProfile"])
    saved_profile_body = cast(dict[str, JsonValue], saved_profile["profile"])
    assert draft == {
        "name": "openai-gpt-4o",
        "provider": "openai_compatible",
        "model": "gpt-4o",
        "baseUrl": "https://api.openai.com/v1",
        "contextWindow": "128000",
        "maxTokens": "16384",
        "catalogDisplay": "flex",
        "summary": "Model Marketplace · OpenAI · GPT-4o",
    }
    assert saved_profile["name"] == "openai-gpt-4o"
    assert saved_profile_body["catalog_provider_id"] == "openai"
    assert saved_profile_body["catalog_provider_name"] == "OpenAI"
    assert saved_profile_body["catalog_model_name"] == "GPT-4o"
    assert saved_profile_body["context_window"] == 128000
    assert saved_profile_body["max_tokens"] == 16384
    assert cast(dict[str, JsonValue], saved_profile_body["capabilities"])["input"] == {
        "text": True,
        "image": True,
        "audio": False,
        "video": False,
        "pdf": False,
    }


def test_catalog_model_without_provider_api_keeps_base_url_editable(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("add-profile-btn").onclick();
await Promise.resolve();
await Promise.resolve();
document.getElementById("model-catalog-provider-list").querySelectorAll(".model-catalog-provider-btn")[0].onclick();
document.getElementById("model-catalog-model-list").querySelectorAll(".model-catalog-model-btn")[0].onclick();
const baseUrlFieldsDisplay = document.getElementById("profile-base-url-fields").style.display;
const baseUrlBeforeInput = document.getElementById("profile-base-url").value;
document.getElementById("profile-base-url").value = "https://manual.example/v1";
document.getElementById("profile-base-url").oninput();
document.getElementById("profile-api-key").value = "test-api-key";
await document.getElementById("save-profile-btn").onclick();

console.log(JSON.stringify({
    baseUrlFieldsDisplay,
    baseUrlBeforeInput,
    baseUrlFieldsDisplayAfterInput: document.getElementById("profile-base-url-fields").style.display,
    savedProfile: globalThis.__savedProfile,
}));
""".strip(),
        mock_api_source=DEFAULT_MOCK_API_SOURCE.replace(
            'api: "https://api.openai.com/v1",',
            'api: "",',
            1,
        ),
    )

    saved_profile = cast(dict[str, JsonValue], payload["savedProfile"])
    saved_profile_body = cast(dict[str, JsonValue], saved_profile["profile"])
    assert payload["baseUrlFieldsDisplay"] == "block"
    assert payload["baseUrlBeforeInput"] == ""
    assert payload["baseUrlFieldsDisplayAfterInput"] == "block"
    assert saved_profile_body["base_url"] == "https://manual.example/v1"


def test_saving_after_leaving_catalog_clears_catalog_metadata(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn").find(
    button => button.dataset.name === "default",
).onclick();
await Promise.resolve();
await Promise.resolve();
document.getElementById("profile-provider-custom-btn").onclick();
await document.getElementById("save-profile-btn").onclick();

console.log(JSON.stringify({
    savedProfile: globalThis.__savedProfile,
}));
""".strip(),
        mock_api_source=DEFAULT_MOCK_API_SOURCE.replace(
            'input_modalities: ["image"],\n        },',
            (
                'input_modalities: ["image"],\n'
                '            catalog_provider_id: "openai",\n'
                '            catalog_provider_name: "OpenAI",\n'
                '            catalog_model_name: "Fake Chat",\n'
                "        },"
            ),
            1,
        ),
    )

    saved_profile = cast(dict[str, JsonValue], payload["savedProfile"])
    saved_profile_body = cast(dict[str, JsonValue], saved_profile["profile"])
    assert saved_profile["name"] == "default"
    assert saved_profile_body["catalog_provider_id"] is None
    assert saved_profile_body["catalog_provider_name"] is None
    assert saved_profile_body["catalog_model_name"] is None


def test_catalog_custom_model_uses_simple_inline_input(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("add-profile-btn").onclick();
await Promise.resolve();
await Promise.resolve();
document.getElementById("model-catalog-provider-list").querySelectorAll(".model-catalog-provider-btn")[0].onclick();
document.getElementById("model-catalog-model-search").onclick();
document.getElementById("model-catalog-model-list").querySelectorAll(".model-catalog-custom-model-btn")[0].onclick();

console.log(JSON.stringify({
    modelHtml: document.getElementById("model-catalog-model-list").innerHTML,
    profileModelGroupDisplay: document.getElementById("profile-model-group").style.display,
}));
""".strip(),
    )

    model_html = cast(str, payload["modelHtml"])
    assert 'id="model-catalog-custom-model-input"' in model_html
    assert 'id="model-catalog-custom-model-apply-btn"' in model_html
    assert 'id="open-profile-model-menu-btn"' not in model_html
    assert 'id="fetch-profile-models-btn"' not in model_html
    assert payload["profileModelGroupDisplay"] == "none"


def test_catalog_custom_model_input_does_not_rerender_while_typing() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_text = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "settings"
        / "modelProfiles.js"
    ).read_text(encoding="utf-8")
    function_start = source_text.index("function handleCatalogCustomModelInput")
    function_end = source_text.index(
        "function handleCatalogCustomModelKeydown", function_start
    )
    function_body = source_text[function_start:function_end]

    assert "syncDraftModelValueWithoutRender(value)" in function_body
    assert "setDraftModelValue(value)" not in function_body
    assert "renderProfileEditorState()" not in function_body
    assert "renderModelCatalog()" not in function_body


def test_edit_profile_does_not_show_or_load_model_catalog(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn")[0].onclick();
await Promise.resolve();
await Promise.resolve();

console.log(JSON.stringify({
    panelDisplay: document.getElementById("model-catalog-panel").style.display,
    fetchCount: globalThis.__fetchModelCatalogCount || 0,
}));
""".strip(),
    )

    assert payload["panelDisplay"] == "none"
    assert payload["fetchCount"] == 0


def test_edit_profile_switching_to_marketplace_loads_model_catalog(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn")[0].onclick();
await Promise.resolve();
await Promise.resolve();
const fetchCountBeforeSwitch = globalThis.__fetchModelCatalogCount || 0;
document.getElementById("profile-provider-external-btn").onclick();
await Promise.resolve();
await Promise.resolve();

console.log(JSON.stringify({
    fetchCountBeforeSwitch,
    fetchCountAfterSwitch: globalThis.__fetchModelCatalogCount || 0,
    panelDisplay: document.getElementById("model-catalog-panel").style.display,
    providerInputValue: document.getElementById("model-catalog-provider-search").value,
}));
""".strip(),
    )

    assert payload["fetchCountBeforeSwitch"] == 0
    assert cast(int, payload["fetchCountAfterSwitch"]) >= 1
    assert payload["panelDisplay"] == "flex"
    assert payload["providerInputValue"] == ""


def test_editing_legacy_provider_uses_preserving_provider_setter() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_text = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "settings"
        / "modelProfiles.js"
    ).read_text(encoding="utf-8")
    edit_start = source_text.index("function handleEditProfile")
    edit_end = source_text.index("function handleCancelProfile", edit_start)
    edit_body = source_text[edit_start:edit_end]
    setter_start = source_text.index("function setDraftProviderValue")
    setter_end = source_text.index("function readDraftMaasAuth", setter_start)
    setter_body = source_text[setter_start:setter_end]

    assert "setDraftProviderValue(profile.provider || 'openai_compatible')" in edit_body
    assert "ensureProviderOption(providerInput, normalized)" in setter_body
    assert "document.createElement('option')" in setter_body


def test_edit_external_catalog_profile_shows_saved_market_choice(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn")[0].onclick();
await Promise.resolve();
await Promise.resolve();

console.log(JSON.stringify({
    panelDisplay: document.getElementById("model-catalog-panel").style.display,
    summary: document.getElementById("profile-model-summary").textContent,
    providerInputValue: document.getElementById("model-catalog-provider-search").value,
    modelInputValue: document.getElementById("model-catalog-model-search").value,
    providerListDisplay: document.getElementById("model-catalog-provider-list").style.display,
    modelListDisplay: document.getElementById("model-catalog-model-list").style.display,
    fetchCount: globalThis.__fetchModelCatalogCount || 0,
    providerHtml: document.getElementById("model-catalog-provider-list").innerHTML,
    modelHtml: document.getElementById("model-catalog-model-list").innerHTML,
}));
""".strip(),
        mock_api_source=DEFAULT_MOCK_API_SOURCE.replace(
            'input_modalities: ["image"],',
            'input_modalities: ["image"],\n            catalog_provider_id: "openai",\n            catalog_provider_name: "OpenAI",\n            catalog_model_name: "Fake Chat",',
            1,
        ),
    )

    assert payload["panelDisplay"] == "flex"
    assert payload["summary"] == "Model Marketplace · OpenAI · Fake Chat"
    assert payload["providerInputValue"] == "OpenAI"
    assert payload["modelInputValue"] == "Fake Chat"
    assert payload["providerListDisplay"] == "none"
    assert payload["modelListDisplay"] == "none"
    fetch_count = int(cast(float | int | str, payload["fetchCount"]))
    assert fetch_count >= 1
    assert "OpenAI" in cast(str, payload["providerHtml"])
    assert "is-active" in cast(str, payload["providerHtml"])


def test_edit_external_catalog_profile_can_open_saved_catalog_choice(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn")[0].onclick();
await Promise.resolve();
await Promise.resolve();
document.getElementById("model-catalog-model-search").onclick();

console.log(JSON.stringify({
    panelDisplay: document.getElementById("model-catalog-panel").style.display,
    modelListDisplay: document.getElementById("model-catalog-model-list").style.display,
    providerHtml: document.getElementById("model-catalog-provider-list").innerHTML,
    modelHtml: document.getElementById("model-catalog-model-list").innerHTML,
}));
""".strip(),
        mock_api_source=DEFAULT_MOCK_API_SOURCE.replace(
            'name: "GPT-4o",',
            'name: "Fake Chat",',
            1,
        ).replace(
            'input_modalities: ["image"],',
            'input_modalities: ["image"],\n            catalog_provider_id: "openai",\n            catalog_provider_name: "OpenAI",\n            catalog_model_name: "Fake Chat",',
            1,
        ),
    )

    assert payload["panelDisplay"] == "flex"
    assert payload["modelListDisplay"] == "block"
    assert "is-active" in cast(str, payload["providerHtml"])
    assert "Fake Chat" in cast(str, payload["modelHtml"])
    assert "model-catalog-model-btn is-active" in cast(str, payload["modelHtml"])


def test_add_profile_refreshes_model_catalog_in_background(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();
document.getElementById("add-profile-btn").onclick();
await Promise.resolve();
await Promise.resolve();
await Promise.resolve();
document.getElementById("model-catalog-provider-list").querySelectorAll(".model-catalog-provider-btn")[0].onclick();

console.log(JSON.stringify({
    fetchCount: globalThis.__fetchModelCatalogCount || 0,
    refreshCount: globalThis.__refreshModelCatalogCount || 0,
    modelHtml: document.getElementById("model-catalog-model-list").innerHTML,
}));
""".strip(),
    )

    assert cast(int, payload["fetchCount"]) >= 2
    assert payload["refreshCount"] == 1
    assert "GPT-4o" in cast(str, payload["modelHtml"])


def test_manual_refresh_button_is_only_available_in_add_profile_catalog(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

const listPanelDisplay = document.getElementById("model-catalog-panel").style.display;
document.getElementById("add-profile-btn").onclick();
await Promise.resolve();
await Promise.resolve();
const addPanelDisplay = document.getElementById("model-catalog-panel").style.display;
await document.getElementById("refresh-model-catalog-btn").onclick();

console.log(JSON.stringify({
    listPanelDisplay,
    addPanelDisplay,
    refreshCount: globalThis.__refreshModelCatalogCount || 0,
}));
""".strip(),
    )

    assert payload["listPanelDisplay"] == "none"
    assert payload["addPanelDisplay"] == "flex"
    assert cast(int, payload["refreshCount"]) >= 2


def test_catalog_provider_mapping_only_special_cases_maas(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();
document.getElementById("add-profile-btn").onclick();
await Promise.resolve();
await Promise.resolve();

document.getElementById("model-catalog-provider-list").querySelectorAll(".model-catalog-provider-btn")[0].onclick();
document.getElementById("model-catalog-model-list").querySelectorAll(".model-catalog-model-btn")[0].onclick();
const minimaxProvider = document.getElementById("profile-provider").value;

document.getElementById("model-catalog-provider-list").querySelectorAll(".model-catalog-provider-btn")[1].onclick();
document.getElementById("model-catalog-model-list").querySelectorAll(".model-catalog-model-btn")[0].onclick();
const maasProvider = document.getElementById("profile-provider").value;
const maasAuthDisplay = document.getElementById("profile-maas-auth-fields").style.display;
const apiKeyDisplay = document.getElementById("profile-api-key-group").style.display;
const modelGroupDisplay = document.getElementById("profile-model-group").style.display;
const catalogPanelDisplay = document.getElementById("model-catalog-panel").style.display;

console.log(JSON.stringify({
    minimaxProvider,
    maasProvider,
    maasAuthDisplay,
    apiKeyDisplay,
    modelGroupDisplay,
    catalogPanelDisplay,
}));
""".strip(),
        mock_api_source="""
export async function fetchModelProfiles() {
    return {};
}

export async function fetchModelFallbackConfig() {
    return { policies: [] };
}

export async function fetchModelCatalog() {
    return {
        ok: true,
        source_url: "https://models.dev/api.json",
        providers: [
            {
                id: "minimax",
                name: "MiniMax",
                api: "https://api.minimaxi.com/v1",
                env: ["MINIMAX_API_KEY"],
                models: [
                    {
                        id: "minimax-text-01",
                        name: "MiniMax Text 01",
                        capabilities: {
                            input: { text: true, image: false, audio: false, video: false, pdf: false },
                            output: { text: true, image: false, audio: false, video: false, pdf: false },
                        },
                        input_modalities: [],
                    },
                ],
            },
            {
                id: "maas",
                name: "MAAS",
                api: "http://snapengine.cida.cce.prod-szv-g.dragon.tools.huawei.com/api/v2/",
                env: [],
                models: [
                    {
                        id: "maas-chat",
                        name: "MAAS Chat",
                        capabilities: {
                            input: { text: true, image: false, audio: false, video: false, pdf: false },
                            output: { text: true, image: false, audio: false, video: false, pdf: false },
                        },
                        input_modalities: [],
                    },
                ],
            },
        ],
    };
}

export async function refreshModelCatalog() {
    return fetchModelCatalog();
}

export async function probeModelConnection() {
    return { ok: true, latency_ms: 1 };
}

export async function discoverModelCatalog() {
    return { ok: true, latency_ms: 1, models: [] };
}

export async function saveModelProfile(name, profile) {
    globalThis.__savedProfile = { name, profile };
}

export async function reloadModelConfig() {
    globalThis.__reloadCalled = true;
}

export async function deleteModelProfile(name) {
    globalThis.__deletedProfileName = name;
}
""".strip(),
    )

    assert payload["minimaxProvider"] == "openai_compatible"
    assert payload["maasProvider"] == "maas"
    assert payload["maasAuthDisplay"] == "grid"
    assert payload["apiKeyDisplay"] == "none"
    assert payload["modelGroupDisplay"] == "block"
    assert payload["catalogPanelDisplay"] == "none"


def test_saving_model_profile_includes_fallback_settings(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-name").value = "fallback-profile";
document.getElementById("profile-provider").value = "openai_compatible";
document.getElementById("profile-model").value = "reasoning-model";
document.getElementById("profile-base-url").value = "http://127.0.0.1:8001/v1";
document.getElementById("profile-api-key").value = "test-api-key";
document.getElementById("profile-fallback-policy").value = "other_provider_only";
document.getElementById("profile-fallback-priority").value = "9";

await document.getElementById("save-profile-btn").onclick();

console.log(JSON.stringify({
    savedProfile: globalThis.__savedProfile,
}));
""".strip(),
    )

    saved_profile = cast(dict[str, JsonValue], payload["savedProfile"])
    saved_profile_body = cast(dict[str, JsonValue], saved_profile["profile"])
    assert saved_profile_body["fallback_policy_id"] == "other_provider_only"
    assert saved_profile_body["fallback_priority"] == 9


def test_model_profiles_list_rerenders_dynamic_fallback_copy_on_language_change(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

const beforeHtml = document.getElementById("profiles-list").innerHTML;
globalThis.__language = "alt";
document.dispatchEvent(new CustomEvent("agent-teams-language-changed"));

console.log(JSON.stringify({
    beforeHtml,
    afterHtml: document.getElementById("profiles-list").innerHTML,
}));
""".strip(),
        mock_i18n_source="""
const translations = {
    en: {
        "settings.model.add_profile": "Add Profile",
        "settings.model.edit_profile": "Edit Profile",
        "settings.model.empty_title": "No profiles configured",
        "settings.model.empty_copy": "Create a profile to define the model endpoint, request limits, and sampling defaults.",
        "settings.model.saved_title": "Profile Saved",
        "settings.model.saved_message_detail": "Profile saved and reloaded.",
        "settings.model.save_failed_title": "Save Failed",
        "settings.model.save_failed_detail": "Failed to save: {error}",
        "settings.model.testing": "Testing connection...",
        "settings.model.probe_failed": "Probe failed: {error}",
        "settings.model.delete_title": "Delete Profile",
        "settings.model.delete_message": "Delete profile \\"{name}\\"?",
        "settings.model.deleted_title": "Profile Deleted",
        "settings.model.deleted_message_detail": "Profile deleted and reloaded.",
        "settings.model.delete_failed_title": "Delete Failed",
        "settings.model.delete_failed_detail": "Failed to delete: {error}",
        "settings.model.fetching_models": "Fetching models...",
        "settings.model.fetch_failed": "Fetch failed: {error}",
        "settings.model.fetch_models": "Fetch Models",
        "settings.model.validation_test_new": "Model, base URL, and API key are required before testing a new profile.",
        "settings.model.validation_fetch_models": "Base URL and API key are required before fetching models for a new profile.",
        "settings.model.probe_success": "Connected in {latency_ms}ms{usage_text}",
        "settings.model.connection_failed": "Connection failed: {reason}",
        "settings.model.probe_no_models": "Connected in {latency_ms}ms, but the endpoint returned no models.",
        "settings.model.models_fetched": "Fetched {count} models in {latency_ms}ms.",
        "settings.model.usage_tokens": " · {tokens} tokens",
        "settings.model.context_window_compact": "{count} ctx",
        "settings.model.show_models": "Show Models",
        "settings.model.no_models_loaded": "No Models Loaded",
        "settings.model.capability_section": "Capabilities",
        "settings.model.image_capability": "Image Input",
        "settings.model.image_capability_follow": "Follow detection",
        "settings.model.image_capability_supported": "Supports image input",
        "settings.model.image_capability_unsupported": "Text only",
        "settings.model.show_api_key": "Show API key",
        "settings.model.hide_api_key": "Hide API key",
        "settings.model.show_password": "Show password",
        "settings.model.hide_password": "Hide password",
        "settings.model.default_badge": "Default",
        "settings.model.capability_image_input": "Image input",
        "settings.model.capability_text_only": "Text only",
        "settings.model.capability_unknown": "Capability unknown",
        "settings.model.no_model": "No model",
        "settings.model.no_endpoint": "No endpoint",
        "settings.model.fallback_disabled": "Fallback disabled",
        "settings.model.priority_compact": "Priority {priority}",
        "settings.model.disabled": "Disabled",
        "settings.model.unknown": "Unknown",
        "settings.action.test": "Test",
        "settings.action.edit": "Edit",
        "settings.action.delete": "Delete",
        "settings.action.cancel": "Cancel",
    },
    alt: {
        "settings.model.add_profile": "Add Profile ALT",
        "settings.model.edit_profile": "Edit Profile ALT",
        "settings.model.empty_title": "No profiles configured ALT",
        "settings.model.empty_copy": "Create a profile ALT",
        "settings.model.saved_title": "Profile Saved ALT",
        "settings.model.saved_message_detail": "Profile saved and reloaded ALT.",
        "settings.model.save_failed_title": "Save Failed ALT",
        "settings.model.save_failed_detail": "Failed to save ALT: {error}",
        "settings.model.testing": "Testing ALT...",
        "settings.model.probe_failed": "Probe failed ALT: {error}",
        "settings.model.delete_title": "Delete Profile ALT",
        "settings.model.delete_message": "Delete profile ALT \\"{name}\\"?",
        "settings.model.deleted_title": "Profile Deleted ALT",
        "settings.model.deleted_message_detail": "Profile deleted and reloaded ALT.",
        "settings.model.delete_failed_title": "Delete Failed ALT",
        "settings.model.delete_failed_detail": "Failed to delete ALT: {error}",
        "settings.model.fetching_models": "Fetching models ALT...",
        "settings.model.fetch_failed": "Fetch failed ALT: {error}",
        "settings.model.fetch_models": "Fetch Models ALT",
        "settings.model.validation_test_new": "Validation ALT",
        "settings.model.validation_fetch_models": "Fetch validation ALT",
        "settings.model.probe_success": "Connected ALT in {latency_ms}ms{usage_text}",
        "settings.model.connection_failed": "Connection failed ALT: {reason}",
        "settings.model.probe_no_models": "No models ALT",
        "settings.model.models_fetched": "Fetched ALT {count} models in {latency_ms}ms.",
        "settings.model.usage_tokens": " ALT {tokens} tokens",
        "settings.model.context_window_compact": "{count} ctx ALT",
        "settings.model.show_models": "Show Models ALT",
        "settings.model.no_models_loaded": "No Models Loaded ALT",
        "settings.model.capability_section": "Capabilities ALT",
        "settings.model.image_capability": "Image Input ALT",
        "settings.model.image_capability_follow": "Follow detection ALT",
        "settings.model.image_capability_supported": "Supports image input ALT",
        "settings.model.image_capability_unsupported": "Text only ALT",
        "settings.model.show_api_key": "Show API key ALT",
        "settings.model.hide_api_key": "Hide API key ALT",
        "settings.model.show_password": "Show password ALT",
        "settings.model.hide_password": "Hide password ALT",
        "settings.model.default_badge": "Default ALT",
        "settings.model.capability_image_input": "Image input ALT",
        "settings.model.capability_text_only": "Text only ALT",
        "settings.model.capability_unknown": "Capability unknown ALT",
        "settings.model.no_model": "No model ALT",
        "settings.model.no_endpoint": "No endpoint ALT",
        "settings.model.fallback_disabled": "Fallback disabled ALT",
        "settings.model.priority_compact": "Priority ALT {priority}",
        "settings.model.disabled": "Disabled ALT",
        "settings.model.unknown": "Unknown ALT",
        "settings.action.test": "Test ALT",
        "settings.action.edit": "Edit ALT",
        "settings.action.delete": "Delete ALT",
        "settings.action.cancel": "Cancel ALT",
    },
};

globalThis.__language = globalThis.__language || "en";

export function t(key) {
    return translations[globalThis.__language]?.[key] || key;
}
""".strip(),
    )

    before_html = cast(str, payload["beforeHtml"])
    after_html = cast(str, payload["afterHtml"])
    assert "Fallback disabled" in before_html
    assert "Priority 0" in before_html
    assert "Fallback disabled ALT" in after_html
    assert "Priority ALT 0" in after_html


def test_model_profile_editor_refreshes_fallback_options_on_language_change(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn")[0].onclick();
document.getElementById("profile-fallback-policy").value = "other_provider_only";
globalThis.__language = "alt";
document.dispatchEvent(new CustomEvent("agent-teams-language-changed"));

console.log(JSON.stringify({
    titleText: document.getElementById("profile-editor-title").textContent,
    fallbackOptionsHtml: document.getElementById("profile-fallback-policy").innerHTML,
    fallbackValue: document.getElementById("profile-fallback-policy").value,
}));
""".strip(),
        mock_i18n_source="""
const translations = {
    en: {
        "settings.model.add_profile": "Add Profile",
        "settings.model.edit_profile": "Edit Profile",
        "settings.model.disabled": "Disabled",
        "settings.model.show_api_key": "Show API key",
        "settings.model.hide_api_key": "Hide API key",
        "settings.model.show_password": "Show password",
        "settings.model.hide_password": "Hide password",
        "settings.model.image_capability_follow": "Follow detection",
        "settings.model.image_capability_supported": "Supports image input",
        "settings.model.image_capability_unsupported": "Text only",
        "settings.model.testing": "Testing connection...",
        "settings.model.fetching_models": "Fetching models...",
        "settings.model.no_models_loaded": "No Models Loaded",
        "settings.model.context_window_compact": "{count} ctx",
        "settings.model.capability_image_input": "Image input",
        "settings.model.capability_text_only": "Text only",
        "settings.model.capability_unknown": "Capability unknown",
        "settings.model.unknown": "Unknown",
        "settings.model.no_model": "No model",
        "settings.model.no_endpoint": "No endpoint",
        "settings.model.fallback_disabled": "Fallback disabled",
        "settings.model.priority_compact": "Priority {priority}",
        "settings.action.test": "Test",
        "settings.action.edit": "Edit",
        "settings.action.delete": "Delete",
        "settings.action.cancel": "Cancel",
    },
    alt: {
        "settings.model.add_profile": "Add Profile ALT",
        "settings.model.edit_profile": "Edit Profile ALT",
        "settings.model.disabled": "Disabled ALT",
        "settings.model.show_api_key": "Show API key ALT",
        "settings.model.hide_api_key": "Hide API key ALT",
        "settings.model.show_password": "Show password ALT",
        "settings.model.hide_password": "Hide password ALT",
        "settings.model.image_capability_follow": "Follow detection ALT",
        "settings.model.image_capability_supported": "Supports image input ALT",
        "settings.model.image_capability_unsupported": "Text only ALT",
        "settings.model.testing": "Testing ALT...",
        "settings.model.fetching_models": "Fetching models ALT...",
        "settings.model.no_models_loaded": "No Models Loaded ALT",
        "settings.model.context_window_compact": "{count} ctx ALT",
        "settings.model.capability_image_input": "Image input ALT",
        "settings.model.capability_text_only": "Text only ALT",
        "settings.model.capability_unknown": "Capability unknown ALT",
        "settings.model.unknown": "Unknown ALT",
        "settings.model.no_model": "No model ALT",
        "settings.model.no_endpoint": "No endpoint ALT",
        "settings.model.fallback_disabled": "Fallback disabled ALT",
        "settings.model.priority_compact": "Priority ALT {priority}",
        "settings.action.test": "Test ALT",
        "settings.action.edit": "Edit ALT",
        "settings.action.delete": "Delete ALT",
        "settings.action.cancel": "Cancel ALT",
    },
};

globalThis.__language = globalThis.__language || "en";

export function t(key) {
    return translations[globalThis.__language]?.[key] || key;
}
""".strip(),
    )

    assert payload["titleText"] == "Edit Profile ALT"
    assert "Disabled ALT" in cast(str, payload["fallbackOptionsHtml"])
    assert payload["fallbackValue"] == "other_provider_only"


def test_model_profile_list_can_set_default_profile(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

const defaultButtons = document.getElementById("profiles-list").querySelectorAll(".set-default-profile-btn");
await defaultButtons.find(button => button.dataset.name === "ui-regression-profile").onclick();

console.log(JSON.stringify({
    savedProfile: globalThis.__savedProfile,
    reloadCalled: globalThis.__reloadCalled === true,
    notifications,
}));
""".strip(),
    )

    saved_profile = cast(dict[str, object], payload["savedProfile"])
    saved_body = cast(dict[str, object], saved_profile["profile"])

    assert saved_profile["name"] == "ui-regression-profile"
    assert saved_body["is_default"] is True
    assert saved_body["model"] == "fake-chat-model"
    assert payload["reloadCalled"] is True
    assert cast(list[object], payload["notifications"])[0] == {
        "title": "Default Model Updated",
        "message": "ui-regression-profile is now the default model.",
        "tone": "success",
    }


def test_model_profiles_localize_builtin_fallback_policy_summary_labels(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

const beforeHtml = document.getElementById("profiles-list").innerHTML;
globalThis.__language = "alt";
document.dispatchEvent(new CustomEvent("agent-teams-language-changed"));

console.log(JSON.stringify({
    beforeHtml,
    afterHtml: document.getElementById("profiles-list").innerHTML,
}));
""".strip(),
        mock_api_source="""
export async function fetchModelProfiles() {
    return {
        translated: {
            provider: "openai_compatible",
            model: "fake-chat-model",
            base_url: "http://127.0.0.1:8001/v1",
            api_key: "saved-secret-key",
            has_api_key: true,
            is_default: true,
            fallback_policy_id: "same_provider_then_other_provider",
            fallback_priority: 3,
            capabilities: {
                input: { text: true, image: false, audio: false, video: false, pdf: false },
                output: { text: true, image: false, audio: false, video: false, pdf: false },
            },
            input_modalities: [],
        },
    };
}

export async function fetchModelFallbackConfig() {
    return {
        policies: [
            {
                policy_id: "same_provider_then_other_provider",
                name: "Same Provider Then Other Provider",
                enabled: true,
            },
            {
                policy_id: "other_provider_only",
                name: "Other Provider Only",
                enabled: true,
            },
        ],
    };
}

export async function probeModelConnection(payload) {
    globalThis.__probePayload = payload;
    return { ok: true, latency_ms: 42 };
}

export async function discoverModelCatalog(payload) {
    globalThis.__discoverPayload = payload;
    return { ok: true, latency_ms: 37, models: [] };
}

export async function saveModelProfile(name, profile) {
    globalThis.__savedProfile = { name, profile };
}

export async function reloadModelConfig() {
    globalThis.__reloadCalled = true;
}

export async function deleteModelProfile(name) {
    globalThis.__deletedProfileName = name;
}
""".strip(),
        mock_i18n_source="""
const translations = {
    en: {
        "settings.model.add_profile": "Add Profile",
        "settings.model.edit_profile": "Edit Profile",
        "settings.model.testing": "Testing connection...",
        "settings.model.fallback_disabled": "Fallback disabled",
        "settings.model.fallback_policy_same_provider_then_other_provider": "Same Provider Then Other Provider",
        "settings.model.fallback_policy_other_provider_only": "Other Provider Only",
        "settings.model.priority_compact": "Priority {priority}",
        "settings.model.default_badge": "Default",
        "settings.model.capability_image_input": "Image input",
        "settings.model.capability_text_only": "Text only",
        "settings.model.capability_unknown": "Capability unknown",
        "settings.model.no_model": "No model",
        "settings.model.no_endpoint": "No endpoint",
        "settings.model.unknown": "Unknown",
        "settings.action.test": "Test",
        "settings.action.edit": "Edit",
        "settings.action.delete": "Delete",
        "settings.action.cancel": "Cancel",
    },
    alt: {
        "settings.model.add_profile": "Add Profile ALT",
        "settings.model.edit_profile": "Edit Profile ALT",
        "settings.model.testing": "Testing ALT...",
        "settings.model.fallback_disabled": "Fallback disabled ALT",
        "settings.model.fallback_policy_same_provider_then_other_provider": "Same Provider Then Other Provider ALT",
        "settings.model.fallback_policy_other_provider_only": "Other Provider Only ALT",
        "settings.model.priority_compact": "Priority ALT {priority}",
        "settings.model.default_badge": "Default ALT",
        "settings.model.capability_image_input": "Image input ALT",
        "settings.model.capability_text_only": "Text only ALT",
        "settings.model.capability_unknown": "Capability unknown ALT",
        "settings.model.no_model": "No model ALT",
        "settings.model.no_endpoint": "No endpoint ALT",
        "settings.model.unknown": "Unknown ALT",
        "settings.action.test": "Test ALT",
        "settings.action.edit": "Edit ALT",
        "settings.action.delete": "Delete ALT",
        "settings.action.cancel": "Cancel ALT",
    },
};

globalThis.__language = globalThis.__language || "en";

export function t(key) {
    return translations[globalThis.__language]?.[key] || key;
}
""".strip(),
    )

    before_html = cast(str, payload["beforeHtml"])
    after_html = cast(str, payload["afterHtml"])
    assert "Same Provider Then Other Provider" in before_html
    assert "Priority 3" in before_html
    assert "Same Provider Then Other Provider ALT" in after_html
    assert "Priority ALT 3" in after_html


def test_model_profile_editor_localizes_builtin_fallback_policy_options(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn")[0].onclick();
globalThis.__language = "alt";
document.dispatchEvent(new CustomEvent("agent-teams-language-changed"));

console.log(JSON.stringify({
    fallbackOptionsHtml: document.getElementById("profile-fallback-policy").innerHTML,
    fallbackValue: document.getElementById("profile-fallback-policy").value,
}));
""".strip(),
        mock_api_source="""
export async function fetchModelProfiles() {
    return {
        translated: {
            provider: "openai_compatible",
            model: "fake-chat-model",
            base_url: "http://127.0.0.1:8001/v1",
            api_key: "saved-secret-key",
            has_api_key: true,
            is_default: true,
            fallback_policy_id: "same_provider_then_other_provider",
            fallback_priority: 3,
            capabilities: {
                input: { text: true, image: false, audio: false, video: false, pdf: false },
                output: { text: true, image: false, audio: false, video: false, pdf: false },
            },
            input_modalities: [],
        },
    };
}

export async function fetchModelFallbackConfig() {
    return {
        policies: [
            {
                policy_id: "same_provider_then_other_provider",
                name: "Same Provider Then Other Provider",
                enabled: true,
            },
            {
                policy_id: "other_provider_only",
                name: "Other Provider Only",
                enabled: true,
            },
        ],
    };
}

export async function probeModelConnection(payload) {
    globalThis.__probePayload = payload;
    return { ok: true, latency_ms: 42 };
}

export async function discoverModelCatalog(payload) {
    globalThis.__discoverPayload = payload;
    return { ok: true, latency_ms: 37, models: [] };
}

export async function saveModelProfile(name, profile) {
    globalThis.__savedProfile = { name, profile };
}

export async function reloadModelConfig() {
    globalThis.__reloadCalled = true;
}

export async function deleteModelProfile(name) {
    globalThis.__deletedProfileName = name;
}
""".strip(),
        mock_i18n_source="""
const translations = {
    en: {
        "settings.model.add_profile": "Add Profile",
        "settings.model.edit_profile": "Edit Profile",
        "settings.model.disabled": "Disabled",
        "settings.model.testing": "Testing connection...",
        "settings.model.fetching_models": "Fetching models...",
        "settings.model.no_models_loaded": "No Models Loaded",
        "settings.model.show_api_key": "Show API key",
        "settings.model.hide_api_key": "Hide API key",
        "settings.model.show_password": "Show password",
        "settings.model.hide_password": "Hide password",
        "settings.model.image_capability_follow": "Follow detection",
        "settings.model.image_capability_supported": "Supports image input",
        "settings.model.image_capability_unsupported": "Text only",
        "settings.model.context_window_compact": "{count} ctx",
        "settings.model.capability_image_input": "Image input",
        "settings.model.capability_text_only": "Text only",
        "settings.model.capability_unknown": "Capability unknown",
        "settings.model.no_model": "No model",
        "settings.model.no_endpoint": "No endpoint",
        "settings.model.fallback_disabled": "Fallback disabled",
        "settings.model.fallback_policy_same_provider_then_other_provider": "Same Provider Then Other Provider",
        "settings.model.fallback_policy_other_provider_only": "Other Provider Only",
        "settings.model.priority_compact": "Priority {priority}",
        "settings.model.unknown": "Unknown",
        "settings.action.test": "Test",
        "settings.action.edit": "Edit",
        "settings.action.delete": "Delete",
        "settings.action.cancel": "Cancel",
    },
    alt: {
        "settings.model.add_profile": "Add Profile ALT",
        "settings.model.edit_profile": "Edit Profile ALT",
        "settings.model.disabled": "Disabled ALT",
        "settings.model.testing": "Testing ALT...",
        "settings.model.fetching_models": "Fetching models ALT...",
        "settings.model.no_models_loaded": "No Models Loaded ALT",
        "settings.model.show_api_key": "Show API key ALT",
        "settings.model.hide_api_key": "Hide API key ALT",
        "settings.model.show_password": "Show password ALT",
        "settings.model.hide_password": "Hide password ALT",
        "settings.model.image_capability_follow": "Follow detection ALT",
        "settings.model.image_capability_supported": "Supports image input ALT",
        "settings.model.image_capability_unsupported": "Text only ALT",
        "settings.model.context_window_compact": "{count} ctx ALT",
        "settings.model.capability_image_input": "Image input ALT",
        "settings.model.capability_text_only": "Text only ALT",
        "settings.model.capability_unknown": "Capability unknown ALT",
        "settings.model.no_model": "No model ALT",
        "settings.model.no_endpoint": "No endpoint ALT",
        "settings.model.fallback_disabled": "Fallback disabled ALT",
        "settings.model.fallback_policy_same_provider_then_other_provider": "Same Provider Then Other Provider ALT",
        "settings.model.fallback_policy_other_provider_only": "Other Provider Only ALT",
        "settings.model.priority_compact": "Priority ALT {priority}",
        "settings.model.unknown": "Unknown ALT",
        "settings.action.test": "Test ALT",
        "settings.action.edit": "Edit ALT",
        "settings.action.delete": "Delete ALT",
        "settings.action.cancel": "Cancel ALT",
    },
};

globalThis.__language = globalThis.__language || "en";

export function t(key) {
    return translations[globalThis.__language]?.[key] || key;
}
""".strip(),
    )

    options_html = cast(str, payload["fallbackOptionsHtml"])
    assert "Same Provider Then Other Provider ALT" in options_html
    assert "Other Provider Only ALT" in options_html
    assert payload["fallbackValue"] == "same_provider_then_other_provider"


def test_draft_probe_updates_inline_status_and_payload(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-model").value = "draft-model";
document.getElementById("profile-base-url").value = "https://draft.test/v1";
document.getElementById("profile-api-key").value = "draft-api-key";
document.getElementById("profile-temperature").value = "0.4";
document.getElementById("profile-top-p").value = "0.9";
document.getElementById("profile-max-tokens").value = "256";
document.getElementById("profile-ssl-verify").value = "false";

await document.getElementById("test-profile-btn").onclick();

console.log(JSON.stringify({
    notifications,
    testButtonText: document.getElementById("test-profile-btn").textContent,
    probeStatusText: document.getElementById("profile-probe-inline-status").textContent,
    probeStatusDisplay: document.getElementById("profile-probe-inline-status").style.display,
    probeStatusClass: document.getElementById("profile-probe-inline-status").className,
    probePayload: globalThis.__probePayload,
}));
""".strip(),
    )

    probe_payload = cast(dict[str, JsonValue], payload["probePayload"])
    probe_override = cast(dict[str, JsonValue], probe_payload["override"])
    probe_status_text = cast(str, payload["probeStatusText"])
    assert payload["notifications"] == []
    assert payload["testButtonText"] == "Test"
    assert payload["probeStatusDisplay"] == "inline-flex"
    assert "profile-probe-inline-status-success" in cast(
        str, payload["probeStatusClass"]
    )
    assert "Connected in 42ms" in probe_status_text
    assert "9 tokens" in probe_status_text
    assert probe_payload["timeout_ms"] == 15000
    assert probe_override["model"] == "draft-model"
    assert probe_override["base_url"] == "https://draft.test/v1"
    assert probe_override["api_key"] == "draft-api-key"
    assert probe_override["ssl_verify"] is False


def test_discover_models_populates_model_select_and_prefills_value(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-base-url").value = "https://draft.test/v1";
document.getElementById("profile-api-key").value = "draft-api-key";

await document.getElementById("fetch-profile-models-btn").onclick();

console.log(JSON.stringify({
    discoverPayload: globalThis.__discoverPayload,
    modelValue: document.getElementById("profile-model").value,
    modelMenuDisplay: document.getElementById("profile-model-menu").style.display,
    modelMenuHtml: document.getElementById("profile-model-menu").innerHTML,
    discoveryStatusText: document.getElementById("profile-model-discovery-status").textContent,
}));
""".strip(),
    )

    discover_payload = cast(dict[str, JsonValue], payload["discoverPayload"])
    discover_override = cast(dict[str, JsonValue], discover_payload["override"])
    assert discover_payload["timeout_ms"] == 15000
    assert discover_override["provider"] == "openai_compatible"
    assert discover_override["base_url"] == "https://draft.test/v1"
    assert discover_override["api_key"] == "draft-api-key"
    assert payload["modelValue"] == "fake-chat-model"
    assert payload["modelMenuDisplay"] == "block"
    assert 'data-model-name="fake-chat-model"' in cast(str, payload["modelMenuHtml"])
    assert 'data-model-name="reasoning-model"' in cast(str, payload["modelMenuHtml"])
    assert payload["discoveryStatusText"] == "Fetched 2 models in 37ms."


def test_discover_models_menu_renders_input_capability_chip(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-base-url").value = "https://draft.test/v1";
document.getElementById("profile-api-key").value = "draft-api-key";

await document.getElementById("fetch-profile-models-btn").onclick();

console.log(JSON.stringify({
    modelMenuHtml: document.getElementById("profile-model-menu").innerHTML,
}));
""".strip(),
    )

    rendered_html = cast(str, payload["modelMenuHtml"])
    assert "Image input" in rendered_html
    assert "Text only" in rendered_html
    assert "128,000 ctx" in rendered_html


def test_saving_selected_discovered_model_persists_capabilities(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-name").value = "discovered-profile";
document.getElementById("profile-base-url").value = "https://draft.test/v1";
document.getElementById("profile-api-key").value = "draft-api-key";

await document.getElementById("fetch-profile-models-btn").onclick();
document.getElementById("profile-model").value = "fake-chat-model";

await document.getElementById("save-profile-btn").onclick();

console.log(JSON.stringify({
    savedProfile: globalThis.__savedProfile,
}));
""".strip(),
    )

    saved_profile = cast(dict[str, JsonValue], payload["savedProfile"])
    saved_profile_body = cast(dict[str, JsonValue], saved_profile["profile"])
    capabilities = cast(dict[str, JsonValue], saved_profile_body["capabilities"])
    input_capabilities = cast(dict[str, JsonValue], capabilities["input"])
    assert input_capabilities["image"] is None
    assert input_capabilities["text"] is True


def test_saving_manual_image_capability_override_persists_supported(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-name").value = "manual-image-profile";
document.getElementById("profile-base-url").value = "https://draft.test/v1";
document.getElementById("profile-api-key").value = "draft-api-key";

await document.getElementById("fetch-profile-models-btn").onclick();
document.getElementById("profile-model").value = "reasoning-model";
document.getElementById("profile-image-capability").value = "supported";
document.getElementById("profile-image-capability").onchange();

await document.getElementById("save-profile-btn").onclick();

console.log(JSON.stringify({
    savedProfile: globalThis.__savedProfile,
    dispatchedEvents: globalThis.__dispatchedEvents,
}));
""".strip(),
    )

    saved_profile = cast(dict[str, JsonValue], payload["savedProfile"])
    saved_profile_body = cast(dict[str, JsonValue], saved_profile["profile"])
    capabilities = cast(dict[str, JsonValue], saved_profile_body["capabilities"])
    input_capabilities = cast(dict[str, JsonValue], capabilities["input"])
    dispatched_events = cast(list[str], payload["dispatchedEvents"])
    assert input_capabilities["image"] is True
    assert "agent-teams-model-profiles-updated" in dispatched_events


def test_editing_profile_restores_manual_image_capability_override(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn").find(btn => btn.dataset.name === "ui-regression-profile").onclick();

console.log(JSON.stringify({
    imageCapabilityValue: document.getElementById("profile-image-capability").value,
}));
""".strip(),
    )

    assert payload["imageCapabilityValue"] == "unsupported"


def test_discover_models_prefills_context_window_when_metadata_is_available(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-base-url").value = "https://draft.test/v1";
document.getElementById("profile-api-key").value = "draft-api-key";

await document.getElementById("fetch-profile-models-btn").onclick();

console.log(JSON.stringify({
    modelValue: document.getElementById("profile-model").value,
    contextWindowValue: document.getElementById("profile-context-window").value,
}));
""".strip(),
        mock_api_source="""
export async function fetchModelProfiles() {
    return {};
}

export async function probeModelConnection(payload) {
    globalThis.__probePayload = payload;
    return { ok: true, latency_ms: 42 };
}

export async function discoverModelCatalog(payload) {
    globalThis.__discoverPayload = payload;
    return {
        ok: true,
        latency_ms: 37,
        models: ["fake-chat-model", "reasoning-model"],
        model_entries: [
            { model: "fake-chat-model", context_window: 256000 },
            { model: "reasoning-model", context_window: null },
        ],
    };
}

export async function saveModelProfile(name, profile) {
    globalThis.__savedProfile = { name, profile };
}

export async function reloadModelConfig() {
    globalThis.__reloadCalled = true;
}

export async function deleteModelProfile(name) {
    globalThis.__deletedProfileName = name;
}
""".strip(),
    )

    assert payload["modelValue"] == "fake-chat-model"
    assert payload["contextWindowValue"] == "256000"


def test_saving_model_profile_preserves_bigmodel_provider_value(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-name").value = "glm-profile";
document.getElementById("profile-provider").value = "bigmodel";
document.getElementById("profile-model").value = "glm-4.5";
document.getElementById("profile-base-url").value = "https://open.bigmodel.cn/api/coding/paas/v4";
document.getElementById("profile-api-key").value = "test-api-key";

await document.getElementById("save-profile-btn").onclick();

console.log(JSON.stringify({
    savedProfile: globalThis.__savedProfile,
}));
""".strip(),
    )

    saved_profile = cast(dict[str, JsonValue], payload["savedProfile"])
    saved_profile_body = cast(dict[str, JsonValue], saved_profile["profile"])
    assert saved_profile_body["provider"] == "bigmodel"


def test_selecting_bigmodel_does_not_prefill_default_base_url(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-provider").value = "bigmodel";
document.getElementById("profile-provider").onchange();

console.log(JSON.stringify({
    providerValue: document.getElementById("profile-provider").value,
    baseUrlValue: document.getElementById("profile-base-url").value,
}));
""".strip(),
    )

    assert payload["providerValue"] == "bigmodel"
    assert payload["baseUrlValue"] == ""


def test_selecting_bigmodel_does_not_override_existing_base_url(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-base-url").value = "https://custom.example/v1";
document.getElementById("profile-provider").value = "bigmodel";
document.getElementById("profile-provider").onchange();

console.log(JSON.stringify({
    baseUrlValue: document.getElementById("profile-base-url").value,
}));
""".strip(),
    )

    assert payload["baseUrlValue"] == "https://custom.example/v1"


def test_switching_through_provider_without_default_keeps_default_url_provenance(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-provider").value = "minimax";
document.getElementById("profile-provider").onchange();
document.getElementById("profile-provider").value = "openai_compatible";
document.getElementById("profile-provider").onchange();
document.getElementById("profile-provider").value = "bigmodel";
document.getElementById("profile-provider").onchange();

console.log(JSON.stringify({
    providerValue: document.getElementById("profile-provider").value,
    baseUrlValue: document.getElementById("profile-base-url").value,
}));
""".strip(),
    )

    assert payload["providerValue"] == "bigmodel"
    assert payload["baseUrlValue"] == ""


def test_edit_profile_switching_to_bigmodel_keeps_existing_base_url(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn")[0].onclick();
document.getElementById("profile-provider").value = "bigmodel";
document.getElementById("profile-provider").onchange();

console.log(JSON.stringify({
    providerValue: document.getElementById("profile-provider").value,
    baseUrlValue: document.getElementById("profile-base-url").value,
}));
""".strip(),
    )

    assert payload["providerValue"] == "bigmodel"
    assert payload["baseUrlValue"] == "http://127.0.0.1:8001/v1"


def test_edit_profile_switching_through_provider_without_default_uses_new_default_base_url(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn")[0].onclick();
document.getElementById("profile-provider").value = "openai_compatible";
document.getElementById("profile-provider").onchange();
document.getElementById("profile-provider").value = "bigmodel";
document.getElementById("profile-provider").onchange();

console.log(JSON.stringify({
    providerValue: document.getElementById("profile-provider").value,
    baseUrlValue: document.getElementById("profile-base-url").value,
}));
""".strip(),
        mock_api_source="""
export async function fetchModelProfiles() {
    return {
        default: {
            provider: "minimax",
            model: "MiniMax-M1",
            base_url: "https://api.minimaxi.com/v1",
            api_key: "saved-secret-key",
            has_api_key: true,
            is_default: true,
            temperature: 0.3,
            top_p: 0.8,
            connect_timeout_seconds: 15,
        },
    };
}

export async function probeModelConnection(payload) {
    globalThis.__probePayload = payload;
    return {
        ok: true,
        latency_ms: 42,
    };
}

export async function discoverModelCatalog(payload) {
    globalThis.__discoverPayload = payload;
    return {
        ok: true,
        latency_ms: 37,
        models: [],
    };
}

export async function saveModelProfile(name, profile) {
    globalThis.__savedProfile = { name, profile };
}

export async function reloadModelConfig() {
    globalThis.__reloadCalled = true;
}

export async function deleteModelProfile(name) {
    globalThis.__deletedProfileName = name;
}
""".strip(),
    )

    assert payload["providerValue"] == "bigmodel"
    assert payload["baseUrlValue"] == "https://api.minimaxi.com/v1"


def test_edit_profile_switching_provider_keeps_manually_changed_base_url(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn")[0].onclick();
document.getElementById("profile-base-url").value = "https://custom.example/v1";
document.getElementById("profile-base-url").oninput();
document.getElementById("profile-provider").value = "bigmodel";
document.getElementById("profile-provider").onchange();

console.log(JSON.stringify({
    baseUrlValue: document.getElementById("profile-base-url").value,
}));
""".strip(),
    )

    assert payload["baseUrlValue"] == "https://custom.example/v1"


def test_edit_bigmodel_profile_non_provider_changes_do_not_reset_custom_base_url(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn")[0].onclick();
document.getElementById("profile-api-key").value = "updated-secret";
document.getElementById("profile-api-key").oninput();

console.log(JSON.stringify({
    providerValue: document.getElementById("profile-provider").value,
    baseUrlValue: document.getElementById("profile-base-url").value,
}));
""".strip(),
        mock_api_source="""
export async function fetchModelProfiles() {
    return {
        default: {
            provider: "bigmodel",
            model: "glm-4.5",
            base_url: "https://custom.bigmodel.example/v4",
            api_key: "saved-secret-key",
            has_api_key: true,
            is_default: true,
            temperature: 0.3,
            top_p: 0.8,
            connect_timeout_seconds: 15,
        },
    };
}

export async function probeModelConnection(payload) {
    globalThis.__probePayload = payload;
    return {
        ok: true,
        latency_ms: 42,
    };
}

export async function discoverModelCatalog(payload) {
    globalThis.__discoverPayload = payload;
    return {
        ok: true,
        latency_ms: 37,
        models: [],
    };
}

export async function saveModelProfile(name, profile) {
    globalThis.__savedProfile = { name, profile };
}

export async function reloadModelConfig() {
    globalThis.__reloadCalled = true;
}

export async function deleteModelProfile(name) {
    globalThis.__deletedProfileName = name;
}
""".strip(),
    )

    assert payload["providerValue"] == "bigmodel"
    assert payload["baseUrlValue"] == "https://custom.bigmodel.example/v4"


def test_selecting_minimax_does_not_prefill_default_base_url(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-provider").value = "minimax";
document.getElementById("profile-provider").onchange();

console.log(JSON.stringify({
    providerValue: document.getElementById("profile-provider").value,
    baseUrlValue: document.getElementById("profile-base-url").value,
}));
""".strip(),
    )

    assert payload["providerValue"] == "minimax"
    assert payload["baseUrlValue"] == ""


def test_selecting_minimax_does_not_override_existing_base_url(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-base-url").value = "https://custom.example/v1";
document.getElementById("profile-provider").value = "minimax";
document.getElementById("profile-provider").onchange();

console.log(JSON.stringify({
    baseUrlValue: document.getElementById("profile-base-url").value,
}));
""".strip(),
    )

    assert payload["baseUrlValue"] == "https://custom.example/v1"


def test_selecting_maas_prefills_fixed_base_url_and_disables_input(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-provider").value = "maas";
document.getElementById("profile-provider").onchange();

console.log(JSON.stringify({
    baseUrlValue: document.getElementById("profile-base-url").value,
    baseUrlDisabled: document.getElementById("profile-base-url").disabled,
}));
""".strip(),
    )

    assert (
        payload["baseUrlValue"]
        == "http://snapengine.cida.cce.prod-szv-g.dragon.tools.huawei.com/api/v2/"
    )
    assert payload["baseUrlDisabled"] is True


def test_switching_from_maas_to_other_provider_clears_base_url(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-provider").value = "maas";
document.getElementById("profile-provider").onchange();
document.getElementById("profile-provider").value = "openai_compatible";
document.getElementById("profile-provider").onchange();

console.log(JSON.stringify({
    providerValue: document.getElementById("profile-provider").value,
    baseUrlValue: document.getElementById("profile-base-url").value,
    baseUrlDisabled: document.getElementById("profile-base-url").disabled,
}));
""".strip(),
    )

    assert payload["providerValue"] == "openai_compatible"
    assert payload["baseUrlValue"] == ""
    assert payload["baseUrlDisabled"] is False


def test_switching_to_maas_keeps_model_step_and_toggles_credentials(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
const initialParentId = document.getElementById("profile-model-group").parentElement?.id || null;
document.getElementById("profile-provider").value = "maas";
document.getElementById("profile-provider").onchange();
const maasParentId = document.getElementById("profile-model-group").parentElement?.id || null;
const maasPrimaryRowDisplay = document.getElementById("profile-primary-credentials-row").style.display;
document.getElementById("profile-provider").value = "openai_compatible";
document.getElementById("profile-provider").onchange();

console.log(JSON.stringify({
    initialParentId,
    maasParentId,
    maasPrimaryRowDisplay,
    finalParentId: document.getElementById("profile-model-group").parentElement?.id || null,
    primaryRowDisplay: document.getElementById("profile-primary-credentials-row").style.display,
}));
""".strip(),
    )

    assert payload["initialParentId"] == "profile-model-field-home"
    assert payload["maasParentId"] == "profile-maas-model-slot"
    assert payload["maasPrimaryRowDisplay"] == "none"
    assert payload["finalParentId"] == "profile-model-field-home"
    assert payload["primaryRowDisplay"] == "grid"


def test_switching_from_codeagent_to_custom_restores_base_url_and_api_key(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-provider-codeagent-btn").onclick();

const codeagentState = {
    providerValue: document.getElementById("profile-provider").value,
    baseUrlValue: document.getElementById("profile-base-url").value,
    baseUrlDisabled: document.getElementById("profile-base-url").disabled,
    baseUrlGroupDisplay: document.getElementById("profile-base-url").closest(".form-group")?.style.display || null,
    apiKeyDisplay: document.getElementById("profile-api-key-group").style.display,
    codeagentDisplay: document.getElementById("profile-codeagent-auth-fields").style.display,
    summary: document.getElementById("profile-model-summary").textContent,
};

document.getElementById("profile-provider-custom-btn").onclick();

console.log(JSON.stringify({
    codeagentState,
    customState: {
        providerValue: document.getElementById("profile-provider").value,
        baseUrlValue: document.getElementById("profile-base-url").value,
        baseUrlDisabled: document.getElementById("profile-base-url").disabled,
        baseUrlGroupDisplay: document.getElementById("profile-base-url").closest(".form-group")?.style.display || null,
        apiKeyDisplay: document.getElementById("profile-api-key-group").style.display,
        codeagentDisplay: document.getElementById("profile-codeagent-auth-fields").style.display,
    },
}));
""".strip(),
    )

    codeagent_state = cast(dict[str, JsonValue], payload["codeagentState"])
    custom_state = cast(dict[str, JsonValue], payload["customState"])
    assert codeagent_state["providerValue"] == "codeagent"
    assert (
        codeagent_state["baseUrlValue"]
        == "https://codeagentcli.rnd.huawei.com/codeAgentPro"
    )
    assert codeagent_state["baseUrlDisabled"] is True
    assert codeagent_state["baseUrlGroupDisplay"] == "none"
    assert codeagent_state["apiKeyDisplay"] == "none"
    assert codeagent_state["codeagentDisplay"] == "flex"
    assert codeagent_state["summary"] == "CodeAgent Model · No model"
    assert custom_state["providerValue"] == "openai_compatible"
    assert custom_state["baseUrlValue"] == ""
    assert custom_state["baseUrlDisabled"] is False
    assert custom_state["baseUrlGroupDisplay"] == "block"
    assert custom_state["apiKeyDisplay"] == "block"
    assert custom_state["codeagentDisplay"] == "none"


def test_fetching_models_keeps_full_browser_list_when_model_input_is_partial(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-model").value = "reason";
document.getElementById("profile-base-url").value = "https://draft.test/v1";
document.getElementById("profile-api-key").value = "draft-api-key";

await document.getElementById("fetch-profile-models-btn").onclick();

console.log(JSON.stringify({
    modelValue: document.getElementById("profile-model").value,
    modelMenuDisplay: document.getElementById("profile-model-menu").style.display,
    modelMenuHtml: document.getElementById("profile-model-menu").innerHTML,
}));
""".strip(),
    )

    assert payload["modelValue"] == "reason"
    assert payload["modelMenuDisplay"] == "block"
    assert 'data-model-name="fake-chat-model"' in cast(str, payload["modelMenuHtml"])
    assert 'data-model-name="reasoning-model"' in cast(str, payload["modelMenuHtml"])


def test_picking_model_from_browser_updates_model_input(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-base-url").value = "https://draft.test/v1";
document.getElementById("profile-api-key").value = "draft-api-key";

await document.getElementById("fetch-profile-models-btn").onclick();
document.getElementById("profile-model").onfocus();
document.getElementById("profile-model-menu").querySelectorAll(".profile-model-menu-item")[1].onclick();

console.log(JSON.stringify({
    modelValue: document.getElementById("profile-model").value,
    currentValue: document.getElementById("profile-model").dataset.currentValue,
    modelMenuDisplay: document.getElementById("profile-model-menu").style.display,
}));
""".strip(),
    )

    assert payload["modelValue"] == "reasoning-model"
    assert payload["currentValue"] == "reasoning-model"
    assert payload["modelMenuDisplay"] == "none"


def test_model_input_focus_opens_full_menu_after_partial_value(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-model").value = "fake";
document.getElementById("profile-base-url").value = "https://draft.test/v1";
document.getElementById("profile-api-key").value = "draft-api-key";

await document.getElementById("fetch-profile-models-btn").onclick();
document.getElementById("profile-model").onfocus();

console.log(JSON.stringify({
    modelMenuDisplay: document.getElementById("profile-model-menu").style.display,
    modelMenuHtml: document.getElementById("profile-model-menu").innerHTML,
}));
""".strip(),
    )

    assert payload["modelMenuDisplay"] == "block"
    assert 'data-model-name="fake-chat-model"' in cast(str, payload["modelMenuHtml"])
    assert 'data-model-name="reasoning-model"' in cast(str, payload["modelMenuHtml"])


def test_model_menu_toggle_button_opens_and_closes_menu(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-base-url").value = "https://draft.test/v1";
document.getElementById("profile-api-key").value = "draft-api-key";

await document.getElementById("fetch-profile-models-btn").onclick();
document.getElementById("open-profile-model-menu-btn").onclick();

console.log(JSON.stringify({
    menuAfterClose: document.getElementById("profile-model-menu").style.display,
    buttonDisabled: document.getElementById("open-profile-model-menu-btn").disabled,
}));
""".strip(),
    )

    assert payload["menuAfterClose"] == "none"
    assert payload["buttonDisabled"] is False


def test_edit_profile_preserves_existing_api_key_when_left_blank(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn")[0].onclick();
document.getElementById("profile-top-p").value = "0.95";

await document.getElementById("save-profile-btn").onclick();

console.log(JSON.stringify({
    notifications,
    apiKeyPlaceholder: document.getElementById("profile-api-key").placeholder,
    apiKeyType: document.getElementById("profile-api-key").type,
    toggleDisplay: document.getElementById("toggle-profile-api-key-btn").style.display,
    savedProfile: globalThis.__savedProfile,
}));
""".strip(),
    )

    saved_profile = cast(dict[str, JsonValue], payload["savedProfile"])
    saved_profile_body = cast(dict[str, JsonValue], saved_profile["profile"])
    notifications = cast(list[dict[str, JsonValue]], payload["notifications"])
    assert notifications == [
        {
            "title": "Profile Saved",
            "message": "Profile saved and reloaded.",
            "tone": "success",
        }
    ]
    assert payload["apiKeyPlaceholder"] == "************"
    assert payload["apiKeyType"] == "password"
    assert payload["toggleDisplay"] == "inline-flex"
    assert saved_profile["name"] == "default"
    assert "api_key" not in saved_profile_body
    assert saved_profile_body["top_p"] == 0.95


def test_edit_profile_ignores_unfocused_autofilled_saved_api_key(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn")[0].onclick();
document.activeElement = null;
document.getElementById("profile-api-key").value = "browser_password";
document.getElementById("profile-api-key").oninput();

await document.getElementById("save-profile-btn").onclick();

console.log(JSON.stringify({
    apiKeyValue: document.getElementById("profile-api-key").value,
    apiKeyPlaceholder: document.getElementById("profile-api-key").placeholder,
    toggleDisplay: document.getElementById("toggle-profile-api-key-btn").style.display,
    savedProfile: globalThis.__savedProfile,
}));
""".strip(),
    )

    saved_profile = cast(dict[str, JsonValue], payload["savedProfile"])
    saved_profile_body = cast(dict[str, JsonValue], saved_profile["profile"])
    assert payload["apiKeyValue"] == ""
    assert payload["apiKeyPlaceholder"] == "************"
    assert payload["toggleDisplay"] == "inline-flex"
    assert "api_key" not in saved_profile_body


def test_edit_profile_allows_replacing_saved_api_key_after_focus(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn")[0].onclick();
document.activeElement = null;
document.getElementById("profile-api-key").onfocus();
document.getElementById("profile-api-key").value = "replacement-secret-key";
document.getElementById("profile-api-key").oninput();

await document.getElementById("save-profile-btn").onclick();

console.log(JSON.stringify({
    toggleDisplay: document.getElementById("toggle-profile-api-key-btn").style.display,
    savedProfile: globalThis.__savedProfile,
}));
""".strip(),
    )

    saved_profile = cast(dict[str, JsonValue], payload["savedProfile"])
    saved_profile_body = cast(dict[str, JsonValue], saved_profile["profile"])
    assert payload["toggleDisplay"] == "inline-flex"
    assert saved_profile_body["api_key"] == "replacement-secret-key"


def test_edit_profile_api_key_toggle_reveals_saved_value(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn")[0].onclick();
document.getElementById("toggle-profile-api-key-btn").onclick();

console.log(JSON.stringify({
    apiKeyValue: document.getElementById("profile-api-key").value,
    apiKeyType: document.getElementById("profile-api-key").type,
    toggleTitle: document.getElementById("toggle-profile-api-key-btn").title,
}));
""".strip(),
    )

    assert payload["apiKeyValue"] == "saved-secret-key"
    assert payload["apiKeyType"] == "text"
    assert payload["toggleTitle"] == "Hide API key"


def test_edit_maas_profile_ignores_unfocused_autofilled_saved_password(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn").find(btn => btn.dataset.name === "maas-profile").onclick();
document.activeElement = null;
document.getElementById("profile-maas-password").value = "browser_password";
document.getElementById("profile-maas-password").oninput();

await document.getElementById("save-profile-btn").onclick();

console.log(JSON.stringify({
    passwordValue: document.getElementById("profile-maas-password").value,
    passwordPlaceholder: document.getElementById("profile-maas-password").placeholder,
    toggleDisplay: document.getElementById("toggle-profile-maas-password-btn").style.display,
    savedProfile: globalThis.__savedProfile,
}));
""".strip(),
        mock_api_source="""
export async function fetchModelProfiles() {
    return {
        "maas-profile": {
            provider: "maas",
            model: "maas-chat",
            base_url: "http://snapengine.cida.cce.prod-szv-g.dragon.tools.huawei.com/api/v2/",
            maas_auth: {
                username: "saved-user",
                password: "saved-password",
                has_password: true,
            },
            is_default: false,
            temperature: 0.7,
            top_p: 1.0,
            connect_timeout_seconds: 15,
        },
    };
}

export async function probeModelConnection(payload) {
    globalThis.__probePayload = payload;
    return { ok: true, latency_ms: 42, token_usage: { total_tokens: 9 } };
}

export async function discoverModelCatalog(payload) {
    globalThis.__discoverPayload = payload;
    return { ok: true, latency_ms: 37, models: ["maas-chat"] };
}

export async function saveModelProfile(name, profile) {
    globalThis.__savedProfile = { name, profile };
}

export async function reloadModelConfig() {
    globalThis.__reloadCalled = true;
}

export async function deleteModelProfile(name) {
    globalThis.__deletedProfileName = name;
}
""".strip(),
    )

    saved_profile = cast(dict[str, JsonValue], payload["savedProfile"])
    saved_profile_body = cast(dict[str, JsonValue], saved_profile["profile"])
    saved_maas_auth = cast(dict[str, JsonValue], saved_profile_body["maas_auth"])
    assert payload["passwordValue"] == ""
    assert payload["passwordPlaceholder"] == "************"
    assert payload["toggleDisplay"] == "inline-flex"
    assert saved_maas_auth == {
        "username": "saved-user",
    }


def test_edit_maas_profile_allows_replacing_saved_password_after_focus(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn").find(btn => btn.dataset.name === "maas-profile").onclick();
document.activeElement = null;
document.getElementById("profile-maas-password").onfocus();
document.getElementById("profile-maas-password").value = "replacement-maas-password";
document.getElementById("profile-maas-password").oninput();

await document.getElementById("save-profile-btn").onclick();

console.log(JSON.stringify({
    passwordValue: document.getElementById("profile-maas-password").value,
    passwordPlaceholder: document.getElementById("profile-maas-password").placeholder,
    toggleDisplay: document.getElementById("toggle-profile-maas-password-btn").style.display,
    savedProfile: globalThis.__savedProfile,
}));
""".strip(),
        mock_api_source="""
export async function fetchModelProfiles() {
    return {
        "maas-profile": {
            provider: "maas",
            model: "maas-chat",
            base_url: "http://snapengine.cida.cce.prod-szv-g.dragon.tools.huawei.com/api/v2/",
            maas_auth: {
                username: "saved-user",
                password: "saved-password",
                has_password: true,
            },
            is_default: false,
            temperature: 0.7,
            top_p: 1.0,
            connect_timeout_seconds: 15,
        },
    };
}

export async function probeModelConnection(payload) {
    globalThis.__probePayload = payload;
    return { ok: true, latency_ms: 42, token_usage: { total_tokens: 9 } };
}

export async function discoverModelCatalog(payload) {
    globalThis.__discoverPayload = payload;
    return { ok: true, latency_ms: 37, models: ["maas-chat"] };
}

export async function saveModelProfile(name, profile) {
    globalThis.__savedProfile = { name, profile };
}

export async function reloadModelConfig() {
    globalThis.__reloadCalled = true;
}

export async function deleteModelProfile(name) {
    globalThis.__deletedProfileName = name;
}
""".strip(),
    )

    saved_profile = cast(dict[str, JsonValue], payload["savedProfile"])
    saved_profile_body = cast(dict[str, JsonValue], saved_profile["profile"])
    saved_maas_auth = cast(dict[str, JsonValue], saved_profile_body["maas_auth"])
    assert payload["toggleDisplay"] == "inline-flex"
    assert saved_maas_auth == {
        "username": "saved-user",
        "password": "replacement-maas-password",
    }


def test_maas_password_toggle_reveals_and_masks_draft_value(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-provider").value = "maas";
document.getElementById("profile-provider").onchange();
document.getElementById("profile-maas-password").value = "relay-password";
document.getElementById("profile-maas-password").oninput();
const beforeToggle = {
    passwordType: document.getElementById("profile-maas-password").type,
    toggleDisplay: document.getElementById("toggle-profile-maas-password-btn").style.display,
    toggleTitle: document.getElementById("toggle-profile-maas-password-btn").title,
};
document.getElementById("toggle-profile-maas-password-btn").onclick();
const revealed = {
    passwordType: document.getElementById("profile-maas-password").type,
    passwordValue: document.getElementById("profile-maas-password").value,
    toggleTitle: document.getElementById("toggle-profile-maas-password-btn").title,
    toggleClassName: document.getElementById("toggle-profile-maas-password-btn").className,
};
document.getElementById("toggle-profile-maas-password-btn").onclick();

console.log(JSON.stringify({
    beforeToggle,
    revealed,
    finalType: document.getElementById("profile-maas-password").type,
    finalValue: document.getElementById("profile-maas-password").value,
    finalToggleTitle: document.getElementById("toggle-profile-maas-password-btn").title,
}));
""".strip(),
    )

    before_toggle = cast(dict[str, JsonValue], payload["beforeToggle"])
    revealed = cast(dict[str, JsonValue], payload["revealed"])
    assert before_toggle["passwordType"] == "password"
    assert before_toggle["toggleDisplay"] == "inline-flex"
    assert before_toggle["toggleTitle"] == "Show password"
    assert revealed["passwordType"] == "text"
    assert revealed["passwordValue"] == "relay-password"
    assert revealed["toggleTitle"] == "Hide password"
    assert revealed["toggleClassName"] == "secure-input-btn is-active"
    assert payload["finalType"] == "password"
    assert payload["finalValue"] == "relay-password"
    assert payload["finalToggleTitle"] == "Show password"


def test_edit_maas_profile_toggle_reveals_saved_password(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn")[0].onclick();
const beforeToggle = {
    passwordType: document.getElementById("profile-maas-password").type,
    passwordPlaceholder: document.getElementById("profile-maas-password").placeholder,
    toggleDisplay: document.getElementById("toggle-profile-maas-password-btn").style.display,
    toggleTitle: document.getElementById("toggle-profile-maas-password-btn").title,
};
document.getElementById("toggle-profile-maas-password-btn").onclick();

console.log(JSON.stringify({
    beforeToggle,
    revealedType: document.getElementById("profile-maas-password").type,
    revealedValue: document.getElementById("profile-maas-password").value,
    toggleTitle: document.getElementById("toggle-profile-maas-password-btn").title,
    toggleClassName: document.getElementById("toggle-profile-maas-password-btn").className,
}));
""".strip(),
        mock_api_source="""
export async function fetchModelProfiles() {
    return {
        default: {
            provider: "maas",
            model: "maas-chat",
            base_url: "http://snapengine.cida.cce.prod-szv-g.dragon.tools.huawei.com/api/v2/",
            maas_auth: {
                username: "relay-user",
                password: "relay-password",
                has_password: true,
            },
            is_default: true,
            temperature: 0.7,
            top_p: 1.0,
            connect_timeout_seconds: 15,
        },
    };
}

export async function probeModelConnection(payload) {
    globalThis.__probePayload = payload;
    return { ok: true, latency_ms: 42 };
}

export async function discoverModelCatalog(payload) {
    globalThis.__discoverPayload = payload;
    return { ok: true, latency_ms: 37, models: [] };
}

export async function saveModelProfile(name, profile) {
    globalThis.__savedProfile = { name, profile };
}

export async function reloadModelConfig() {
    globalThis.__reloadCalled = true;
}

export async function deleteModelProfile(name) {
    globalThis.__deletedProfileName = name;
}
""".strip(),
    )

    before_toggle = cast(dict[str, JsonValue], payload["beforeToggle"])
    assert before_toggle["passwordType"] == "password"
    assert before_toggle["passwordPlaceholder"] == "************"
    assert before_toggle["toggleDisplay"] == "inline-flex"
    assert before_toggle["toggleTitle"] == "Show password"
    assert payload["revealedType"] == "text"
    assert payload["revealedValue"] == "relay-password"
    assert payload["toggleTitle"] == "Hide password"
    assert payload["toggleClassName"] == "secure-input-btn is-active"


def test_edit_profile_allows_renaming_and_sends_source_name(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn")[0].onclick();
document.getElementById("profile-name").value = "renamed-profile";

await document.getElementById("save-profile-btn").onclick();

console.log(JSON.stringify({
    titleText: document.getElementById("profile-editor-title").textContent,
    savedProfile: globalThis.__savedProfile,
}));
""".strip(),
    )

    saved_profile = cast(dict[str, JsonValue], payload["savedProfile"])
    saved_profile_body = cast(dict[str, JsonValue], saved_profile["profile"])
    assert payload["titleText"] == "Edit Profile"
    assert saved_profile["name"] == "renamed-profile"
    assert saved_profile_body["source_name"] == "default"


def test_edit_profile_prefills_standard_name_input(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn")[0].onclick();

console.log(JSON.stringify({
    titleDisplay: document.getElementById("profile-editor-title").style.display,
    titleText: document.getElementById("profile-editor-title").textContent,
    nameValue: document.getElementById("profile-name").value,
    providerValue: document.getElementById("profile-provider").value,
    defaultChecked: document.getElementById("profile-is-default").checked,
}));
""".strip(),
    )

    assert payload["titleDisplay"] == "block"
    assert payload["titleText"] == "Edit Profile"
    assert payload["nameValue"] == "default"
    assert payload["providerValue"] == "openai_compatible"
    assert payload["defaultChecked"] is True


def test_saved_profile_probe_uses_profile_connect_timeout(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
await loadModelProfilesPanel();

await document.getElementById("profiles-list").querySelectorAll(".profile-card-test-btn")[0].onclick();

console.log(JSON.stringify({
    probePayload: globalThis.__probePayload,
}));
""".strip(),
    )

    probe_payload = cast(dict[str, JsonValue], payload["probePayload"])
    assert probe_payload["profile_name"] == "default"
    assert probe_payload["timeout_ms"] == 15000


def test_model_profile_cards_render_inline_probe_region(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
await loadModelProfilesPanel();

console.log(JSON.stringify({
    renderedHtml: document.getElementById("profiles-list").innerHTML,
}));
""".strip(),
    )

    rendered_html = cast(str, payload["renderedHtml"])
    assert "profile-records" in rendered_html
    assert "profile-card-inline-status" in rendered_html
    assert "profile-cards" not in rendered_html
    assert "Default" in rendered_html
    assert "API Key" not in rendered_html
    assert "Temperature" not in rendered_html
    assert "Top P" not in rendered_html
    assert "Max Output Tokens" not in rendered_html
    assert "Connect Timeout" not in rendered_html


def test_model_profiles_panel_renders_empty_state_when_no_profiles_exist(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
await loadModelProfilesPanel();

console.log(JSON.stringify({
    renderedHtml: document.getElementById("profiles-list").innerHTML,
}));
""".strip(),
        mock_api_source="""
export async function fetchModelProfiles() {
    return {};
}

export async function probeModelConnection(payload) {
    globalThis.__probePayload = payload;
    return { ok: true, latency_ms: 42 };
}

export async function discoverModelCatalog(payload) {
    globalThis.__discoverPayload = payload;
    return { ok: true, latency_ms: 37, models: [] };
}

export async function saveModelProfile(name, profile) {
    globalThis.__savedProfile = { name, profile };
}

export async function reloadModelConfig() {
    globalThis.__reloadCalled = true;
}

export async function deleteModelProfile(name) {
    globalThis.__deletedProfileName = name;
}
""".strip(),
    )

    rendered_html = cast(str, payload["renderedHtml"])
    assert "No profiles configured" in rendered_html
    assert (
        "Create a profile to define the model endpoint, request limits, and sampling defaults."
        in rendered_html
    )
    assert "profile-records" not in rendered_html
    assert "OpenAI Compatible" not in rendered_html
    assert "fake-chat-model" not in rendered_html
    assert "http://127.0.0.1:8001/v1" not in rendered_html
    assert "Default" not in rendered_html


def test_deleting_profile_uses_custom_confirm_and_success_notification(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();
await loadModelProfilesPanel();

await document.getElementById("profiles-list").querySelectorAll(".delete-profile-btn")[0].onclick();

console.log(JSON.stringify({
    notifications,
    confirms: globalThis.__feedbackConfirms,
    deletedProfileName: globalThis.__deletedProfileName,
}));
""".strip(),
    )

    notifications = cast(list[dict[str, JsonValue]], payload["notifications"])
    confirms = cast(list[dict[str, JsonValue]], payload["confirms"])
    assert payload["deletedProfileName"] == "default"
    assert confirms == [
        {
            "title": "Delete Profile",
            "message": 'Delete profile "default"?',
            "tone": "warning",
            "confirmLabel": "Delete",
            "cancelLabel": "Cancel",
        }
    ]
    assert notifications == [
        {
            "title": "Profile Deleted",
            "message": "Profile deleted and reloaded.",
            "tone": "success",
        }
    ]


def test_codeagent_sso_login_blocks_repeated_clicks_while_in_progress(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

globalThis.setTimeout = callback => {
    callback();
    return 0;
};

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-provider").value = "codeagent";
document.getElementById("profile-provider").onchange();

const loginPromise = document.getElementById("profile-codeagent-login-status").onclick();
const disabledWhileStarting = document.getElementById("profile-codeagent-login-status").disabled;
const windowOpensBeforeResolve = globalThis.__windowOpens.slice();

document.getElementById("profile-codeagent-login-status").onclick();

globalThis.__resolveCodeAgentOAuthStart({
    auth_session_id: "auth-session-1",
    authorization_url: "https://example.test/codeagent-sso",
});

await loginPromise;

console.log(JSON.stringify({
    startCalls: globalThis.__codeAgentOAuthStartCalls,
    sessionChecks: globalThis.__codeAgentOAuthSessionChecks,
    disabledWhileStarting,
    disabledAfterCompletion: document.getElementById("profile-codeagent-login-status").disabled,
    authStatus: document.getElementById("profile-codeagent-login-status-message").textContent,
    windowOpensBeforeResolve,
    windowOpens: globalThis.__windowOpens,
    windowOpenNavigations: globalThis.__windowOpenNavigations,
}));
""".strip(),
        mock_api_source="""
export async function fetchModelProfiles() {
    return {};
}

export async function fetchModelFallbackConfig() {
    return { policies: [] };
}

export async function probeModelConnection(payload) {
    globalThis.__probePayload = payload;
    return { ok: true, latency_ms: 42 };
}

export async function discoverModelCatalog(payload) {
    globalThis.__discoverPayload = payload;
    return { ok: true, latency_ms: 37, models: ["codeagent-chat"] };
}

export async function saveModelProfile(name, profile) {
    globalThis.__savedProfile = { name, profile };
}

export async function startCodeAgentOAuth() {
    globalThis.__codeAgentOAuthStartCalls = (globalThis.__codeAgentOAuthStartCalls || 0) + 1;
    return new Promise(resolve => {
        globalThis.__resolveCodeAgentOAuthStart = resolve;
    });
}

export async function fetchCodeAgentOAuthSession(authSessionId) {
    globalThis.__codeAgentOAuthSessionChecks = globalThis.__codeAgentOAuthSessionChecks || [];
    globalThis.__codeAgentOAuthSessionChecks.push(authSessionId);
    return { completed: true };
}

export async function reloadModelConfig() {
    globalThis.__reloadCalled = true;
}

export async function deleteModelProfile(name) {
    globalThis.__deletedProfileName = name;
}
""".strip(),
    )

    assert payload["startCalls"] == 1
    assert payload["sessionChecks"] == ["auth-session-1"]
    assert payload["disabledWhileStarting"] is True
    assert payload["disabledAfterCompletion"] is False
    assert payload["authStatus"] == "Signed in"
    assert payload["windowOpensBeforeResolve"] == [["about:blank", "_blank"]]
    assert payload["windowOpens"] == [
        [
            "about:blank",
            "_blank",
        ]
    ]
    assert payload["windowOpenNavigations"] == ["https://example.test/codeagent-sso"]


def test_codeagent_sso_allows_retry_when_popup_is_unavailable(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

globalThis.__windowOpenReturnsNull = true;
globalThis.setTimeout = callback => {
    callback();
    return 0;
};

const elements = createElements();
installGlobals(elements, notifications);
globalThis.__windowOpenReturnsNull = true;
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-provider").value = "codeagent";
document.getElementById("profile-provider").onchange();

await document.getElementById("profile-codeagent-login-status").onclick();
const blockedStatus = document.getElementById("profile-codeagent-login-status-message").textContent;
const buttonDisabledAfterBlocked = document.getElementById("profile-codeagent-login-status").disabled;

globalThis.__windowOpenReturnsNull = false;
await document.getElementById("profile-codeagent-login-status").onclick();

console.log(JSON.stringify({
    startCalls: globalThis.__codeAgentOAuthStartCalls,
    windowOpens: globalThis.__windowOpens,
    windowOpenNavigations: globalThis.__windowOpenNavigations,
    blockedStatus,
    buttonDisabledAfterBlocked,
    authStatus: document.getElementById("profile-codeagent-login-status-message").textContent,
}));
""".strip(),
        mock_api_source="""
export async function fetchModelProfiles() {
    return {};
}

export async function fetchModelFallbackConfig() {
    return { policies: [] };
}

export async function probeModelConnection(payload) {
    globalThis.__probePayload = payload;
    return { ok: true, latency_ms: 42 };
}

export async function discoverModelCatalog(payload) {
    globalThis.__discoverPayload = payload;
    return { ok: true, latency_ms: 37, models: ["codeagent-chat"] };
}

export async function saveModelProfile(name, profile) {
    globalThis.__savedProfile = { name, profile };
}

export async function startCodeAgentOAuth() {
    globalThis.__codeAgentOAuthStartCalls = (globalThis.__codeAgentOAuthStartCalls || 0) + 1;
    return {
        auth_session_id: "auth-session-fallback",
        authorization_url: "https://example.test/codeagent-sso-fallback",
    };
}

export async function fetchCodeAgentOAuthSession(authSessionId) {
    globalThis.__codeAgentOAuthSessionChecks = globalThis.__codeAgentOAuthSessionChecks || [];
    globalThis.__codeAgentOAuthSessionChecks.push(authSessionId);
    return { completed: true };
}

export async function reloadModelConfig() {
    globalThis.__reloadCalled = true;
}

export async function deleteModelProfile(name) {
    globalThis.__deletedProfileName = name;
}
""".strip(),
    )

    assert payload["startCalls"] == 1
    assert payload["windowOpens"] == [
        ["about:blank", "_blank"],
        ["https://example.test/codeagent-sso-fallback", "_blank"],
    ]
    assert payload["windowOpenNavigations"] == []
    assert payload["blockedStatus"] == (
        "SSO popup was blocked. Click Sign in with SSO again to continue."
    )
    assert payload["buttonDisabledAfterBlocked"] is False
    assert payload["authStatus"] == "Signed in"


def test_failed_codeagent_sso_does_not_send_incomplete_oauth_session_id(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

globalThis.setTimeout = callback => {
    callback();
    return 0;
};

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

await loadModelProfilesPanel();
document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn").find(btn => btn.dataset.name === "codeagent-profile").onclick();

await document.getElementById("profile-codeagent-login-status").onclick();
await document.getElementById("save-profile-btn").onclick();

console.log(JSON.stringify({
    savedProfile: globalThis.__savedProfile,
    authStatus: document.getElementById("profile-codeagent-login-status-message").textContent,
}));
""".strip(),
        mock_api_source="""
export async function fetchModelProfiles() {
    return {
        "codeagent-profile": {
            provider: "codeagent",
            model: "codeagent-chat",
            base_url: "https://codeagentcli.rnd.huawei.com/codeAgentPro",
            codeagent_auth: {
                has_access_token: true,
                has_refresh_token: true,
            },
            is_default: false,
            temperature: 0.7,
            top_p: 1.0,
            connect_timeout_seconds: 15,
        },
    };
}

export async function fetchModelFallbackConfig() {
    return { policies: [] };
}

export async function probeModelConnection(payload) {
    globalThis.__probePayload = payload;
    return { ok: true, latency_ms: 42 };
}

export async function discoverModelCatalog(payload) {
    globalThis.__discoverPayload = payload;
    return { ok: true, latency_ms: 37, models: ["codeagent-chat"] };
}

export async function saveModelProfile(name, profile) {
    globalThis.__savedProfile = { name, profile };
}

export async function startCodeAgentOAuth() {
    return {
        auth_session_id: "failed-auth-session",
        authorization_url: "https://example.test/codeagent-sso",
    };
}

export async function fetchCodeAgentOAuthSession(authSessionId) {
    throw new Error(`poll failed for ${authSessionId}`);
}

export async function reloadModelConfig() {
    globalThis.__reloadCalled = true;
}

export async function deleteModelProfile(name) {
    globalThis.__deletedProfileName = name;
}
""".strip(),
    )

    saved_profile = cast(dict[str, JsonValue], payload["savedProfile"])
    saved_profile_body = cast(dict[str, JsonValue], saved_profile["profile"])
    codeagent_auth = cast(dict[str, JsonValue], saved_profile_body["codeagent_auth"])

    assert saved_profile["name"] == "codeagent-profile"
    assert "oauth_session_id" not in codeagent_auth
    assert codeagent_auth["has_refresh_token"] is True
    assert payload["authStatus"] == "SSO failed: poll failed for failed-auth-session"


def test_editing_saved_codeagent_profile_verifies_persisted_sign_in(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

await loadModelProfilesPanel();
document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn").find(btn => btn.dataset.name === "codeagent-profile").onclick();
await Promise.resolve();
await Promise.resolve();

console.log(JSON.stringify({
    verifyCalls: globalThis.__codeAgentAuthVerifyCalls,
    authStatus: document.getElementById("profile-codeagent-login-status-message").textContent,
}));
""".strip(),
        mock_api_source="""
export async function fetchModelProfiles() {
    return {
        "codeagent-profile": {
            provider: "codeagent",
            model: "codeagent-chat",
            base_url: "https://codeagentcli.rnd.huawei.com/codeAgentPro",
            codeagent_auth: {
                has_access_token: true,
                has_refresh_token: true,
            },
            is_default: false,
            temperature: 0.7,
            top_p: 1.0,
            connect_timeout_seconds: 15,
        },
    };
}

export async function fetchModelFallbackConfig() {
    return { policies: [] };
}

export async function verifyCodeAgentAuth(profileName) {
    globalThis.__codeAgentAuthVerifyCalls = globalThis.__codeAgentAuthVerifyCalls || [];
    globalThis.__codeAgentAuthVerifyCalls.push(profileName);
    return { status: "valid", checked_at: "2026-04-27T02:00:00Z", detail: null };
}
""".strip(),
    )

    assert payload["verifyCalls"] == ["codeagent-profile"]
    assert payload["authStatus"] == "Signed in"


def test_saved_codeagent_profile_clears_verifying_after_provider_switch_during_verify(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

await loadModelProfilesPanel();
document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn").find(btn => btn.dataset.name === "codeagent-profile").onclick();
await Promise.resolve();

const verifyingWhilePending = document.getElementById("profile-codeagent-login-status").disabled;
document.getElementById("profile-provider").value = "openai_compatible";
document.getElementById("profile-provider").onchange();

globalThis.__resolveCodeAgentAuthVerify({
    status: "valid",
    checked_at: "2026-04-27T02:00:00Z",
    detail: null,
});
await Promise.resolve();

document.getElementById("profile-provider").value = "codeagent";
document.getElementById("profile-provider").onchange();
await Promise.resolve();

console.log(JSON.stringify({
    verifyCalls: globalThis.__codeAgentAuthVerifyCalls,
    verifyingWhilePending,
    loginDisabledAfterResolve: document.getElementById("profile-codeagent-login-status").disabled,
    authStatus: document.getElementById("profile-codeagent-login-status-message").textContent,
    providerValue: document.getElementById("profile-provider").value,
}));
""".strip(),
        mock_api_source="""
export async function fetchModelProfiles() {
    return {
        "codeagent-profile": {
            provider: "codeagent",
            model: "codeagent-chat",
            base_url: "https://codeagentcli.rnd.huawei.com/codeAgentPro",
            codeagent_auth: {
                has_access_token: true,
                has_refresh_token: true,
            },
            is_default: false,
            temperature: 0.7,
            top_p: 1.0,
            connect_timeout_seconds: 15,
        },
    };
}

export async function fetchModelFallbackConfig() {
    return { policies: [] };
}

export async function verifyCodeAgentAuth(profileName) {
    globalThis.__codeAgentAuthVerifyCalls = globalThis.__codeAgentAuthVerifyCalls || [];
    globalThis.__codeAgentAuthVerifyCalls.push(profileName);
    return new Promise(resolve => {
        globalThis.__resolveCodeAgentAuthVerify = resolve;
    });
}
""".strip(),
    )

    assert payload["verifyCalls"] == ["codeagent-profile"]
    assert payload["verifyingWhilePending"] is True
    assert payload["loginDisabledAfterResolve"] is False
    assert payload["authStatus"] == "Signed in"
    assert payload["providerValue"] == "codeagent"


def test_saved_codeagent_profile_ignores_stale_verify_response_after_reopen(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

await loadModelProfilesPanel();
const editButtons = document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn");
editButtons.find(btn => btn.dataset.name === "codeagent-profile").onclick();
await Promise.resolve();

document.getElementById("cancel-profile-btn").onclick();
editButtons.find(btn => btn.dataset.name === "codeagent-profile").onclick();
await Promise.resolve();

globalThis.__verifyResolvers[1]({
    status: "valid",
    checked_at: "2026-04-27T02:00:01Z",
    detail: null,
});
await Promise.resolve();

globalThis.__verifyResolvers[0]({
    status: "reauth_required",
    checked_at: "2026-04-27T02:00:00Z",
    detail: "expired session",
});
await Promise.resolve();

console.log(JSON.stringify({
    verifyCalls: globalThis.__codeAgentAuthVerifyCalls,
    authStatus: document.getElementById("profile-codeagent-login-status-message").textContent,
    loginDisabled: document.getElementById("profile-codeagent-login-status").disabled,
}));
""".strip(),
        mock_api_source="""
export async function fetchModelProfiles() {
    return {
        "codeagent-profile": {
            provider: "codeagent",
            model: "codeagent-chat",
            base_url: "https://codeagentcli.rnd.huawei.com/codeAgentPro",
            codeagent_auth: {
                has_access_token: true,
                has_refresh_token: true,
            },
            is_default: false,
            temperature: 0.7,
            top_p: 1.0,
            connect_timeout_seconds: 15,
        },
    };
}

export async function fetchModelFallbackConfig() {
    return { policies: [] };
}

export async function verifyCodeAgentAuth(profileName) {
    globalThis.__codeAgentAuthVerifyCalls = globalThis.__codeAgentAuthVerifyCalls || [];
    globalThis.__codeAgentAuthVerifyCalls.push(profileName);
    globalThis.__verifyResolvers = globalThis.__verifyResolvers || [];
    return new Promise(resolve => {
        globalThis.__verifyResolvers.push(resolve);
    });
}
""".strip(),
    )

    assert payload["verifyCalls"] == ["codeagent-profile", "codeagent-profile"]
    assert payload["authStatus"] == "Signed in"
    assert payload["loginDisabled"] is False


def test_saved_codeagent_profile_with_expired_sign_in_requires_reauth_before_discovery(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

await loadModelProfilesPanel();
document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn").find(btn => btn.dataset.name === "codeagent-profile").onclick();
await Promise.resolve();
await Promise.resolve();
await document.getElementById("fetch-profile-models-btn").onclick();

console.log(JSON.stringify({
    verifyCalls: globalThis.__codeAgentAuthVerifyCalls,
    discoverPayload: globalThis.__discoverPayload || null,
    authStatus: document.getElementById("profile-codeagent-login-status-message").textContent,
    discoveryStatus: document.getElementById("profile-model-discovery-status").textContent,
}));
""".strip(),
        mock_api_source="""
export async function fetchModelProfiles() {
    return {
        "codeagent-profile": {
            provider: "codeagent",
            model: "codeagent-chat",
            base_url: "https://codeagentcli.rnd.huawei.com/codeAgentPro",
            codeagent_auth: {
                has_access_token: true,
                has_refresh_token: true,
            },
            is_default: false,
            temperature: 0.7,
            top_p: 1.0,
            connect_timeout_seconds: 15,
        },
    };
}

export async function fetchModelFallbackConfig() {
    return { policies: [] };
}

export async function verifyCodeAgentAuth(profileName) {
    globalThis.__codeAgentAuthVerifyCalls = globalThis.__codeAgentAuthVerifyCalls || [];
    globalThis.__codeAgentAuthVerifyCalls.push(profileName);
    return {
        status: "reauth_required",
        checked_at: "2026-04-27T02:00:00Z",
        detail: "未识别到用户认证信息",
    };
}

export async function discoverModelCatalog(payload) {
    globalThis.__discoverPayload = payload;
    return { ok: true, latency_ms: 37, models: ["codeagent-chat"] };
}
""".strip(),
    )

    assert payload["verifyCalls"] == ["codeagent-profile"]
    assert payload["discoverPayload"] is None
    assert payload["authStatus"] == "Saved sign-in expired. Sign in with SSO again."
    assert payload["discoveryStatus"] == (
        "SSO login expired. Sign in with SSO again before fetching CodeAgent models."
    )


def test_saved_codeagent_password_profile_allows_probe_and_discovery_with_new_password_after_reauth_required(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

await loadModelProfilesPanel();
document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn").find(btn => btn.dataset.name === "codeagent-profile").onclick();
await Promise.resolve();
await Promise.resolve();

document.getElementById("profile-codeagent-password").value = "fresh-password";
document.getElementById("profile-codeagent-password").oninput();
await document.getElementById("test-profile-btn").onclick();
await document.getElementById("fetch-profile-models-btn").onclick();

console.log(JSON.stringify({
    probePayload: globalThis.__probePayload || null,
    discoverPayload: globalThis.__discoverPayload || null,
    authStatus: document.getElementById("profile-codeagent-login-status-message").textContent,
}));
""".strip(),
        mock_api_source="""
export async function fetchModelProfiles() {
    return {
        "codeagent-profile": {
            provider: "codeagent",
            model: "codeagent-chat",
            base_url: "https://codeagentcli.rnd.huawei.com/codeAgentPro",
            codeagent_auth: {
                auth_method: "password",
                username: "saved-user",
                has_password: true,
            },
            is_default: false,
            temperature: 0.7,
            top_p: 1.0,
            connect_timeout_seconds: 15,
        },
    };
}

export async function fetchModelFallbackConfig() {
    return { policies: [] };
}

export async function verifyCodeAgentAuth(profileName) {
    globalThis.__codeAgentAuthVerifyCalls = globalThis.__codeAgentAuthVerifyCalls || [];
    globalThis.__codeAgentAuthVerifyCalls.push(profileName);
    return {
        status: "reauth_required",
        checked_at: "2026-04-27T02:00:00Z",
        detail: "expired credentials",
    };
}

export async function probeModelConnection(payload) {
    globalThis.__probePayload = payload;
    return { ok: true, latency_ms: 42, token_usage: { total_tokens: 9 } };
}

export async function discoverModelCatalog(payload) {
    globalThis.__discoverPayload = payload;
    return { ok: true, latency_ms: 37, models: ["codeagent-chat"] };
}
""".strip(),
    )

    probe_payload = cast(dict[str, JsonValue], payload["probePayload"])
    discover_payload = cast(dict[str, JsonValue], payload["discoverPayload"])
    probe_override = cast(dict[str, JsonValue], probe_payload["override"])
    discover_override = cast(dict[str, JsonValue], discover_payload["override"])

    assert cast(dict[str, JsonValue], probe_override["codeagent_auth"]) == {
        "auth_method": "password",
        "username": "saved-user",
        "password": "fresh-password",
    }
    assert cast(dict[str, JsonValue], discover_override["codeagent_auth"]) == {
        "auth_method": "password",
        "username": "saved-user",
        "password": "fresh-password",
    }
    assert payload["authStatus"] == "Credentials ready"


def test_saved_codeagent_password_profile_accepts_same_password_reentry_after_reauth_required(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

await loadModelProfilesPanel();
document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn").find(btn => btn.dataset.name === "codeagent-profile").onclick();
await Promise.resolve();
await Promise.resolve();

document.getElementById("profile-codeagent-password").value = "same-password";
document.getElementById("profile-codeagent-password").oninput();
await document.getElementById("test-profile-btn").onclick();
await document.getElementById("fetch-profile-models-btn").onclick();

console.log(JSON.stringify({
    probePayload: globalThis.__probePayload || null,
    discoverPayload: globalThis.__discoverPayload || null,
    authStatus: document.getElementById("profile-codeagent-login-status-message").textContent,
}));
""".strip(),
        mock_api_source="""
export async function fetchModelProfiles() {
    return {
        "codeagent-profile": {
            provider: "codeagent",
            model: "codeagent-chat",
            base_url: "https://codeagentcli.rnd.huawei.com/codeAgentPro",
            codeagent_auth: {
                auth_method: "password",
                username: "saved-user",
                password: "same-password",
                has_password: true,
            },
            is_default: false,
            temperature: 0.7,
            top_p: 1.0,
            connect_timeout_seconds: 15,
        },
    };
}

export async function fetchModelFallbackConfig() {
    return { policies: [] };
}

export async function verifyCodeAgentAuth(profileName) {
    globalThis.__codeAgentAuthVerifyCalls = globalThis.__codeAgentAuthVerifyCalls || [];
    globalThis.__codeAgentAuthVerifyCalls.push(profileName);
    return {
        status: "reauth_required",
        checked_at: "2026-04-27T02:00:00Z",
        detail: "expired credentials",
    };
}

export async function probeModelConnection(payload) {
    globalThis.__probePayload = payload;
    return { ok: true, latency_ms: 42, token_usage: { total_tokens: 9 } };
}

export async function discoverModelCatalog(payload) {
    globalThis.__discoverPayload = payload;
    return { ok: true, latency_ms: 37, models: ["codeagent-chat"] };
}
""".strip(),
    )

    probe_payload = cast(dict[str, JsonValue], payload["probePayload"])
    discover_payload = cast(dict[str, JsonValue], payload["discoverPayload"])
    probe_override = cast(dict[str, JsonValue], probe_payload["override"])
    discover_override = cast(dict[str, JsonValue], discover_payload["override"])

    assert cast(dict[str, JsonValue], probe_override["codeagent_auth"]) == {
        "auth_method": "password",
        "username": "saved-user",
        "password": "same-password",
    }
    assert cast(dict[str, JsonValue], discover_override["codeagent_auth"]) == {
        "auth_method": "password",
        "username": "saved-user",
        "password": "same-password",
    }
    assert payload["authStatus"] == "Credentials ready"


def test_codeagent_password_auth_discovery_uses_username_and_password(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-provider").value = "codeagent";
document.getElementById("profile-provider").onchange();
document.getElementById("profile-codeagent-auth-method").value = "password";
document.getElementById("profile-codeagent-auth-method").onchange();
document.getElementById("profile-codeagent-username").value = "relay-user";
document.getElementById("profile-codeagent-username").oninput();
document.getElementById("profile-codeagent-password").onfocus();
document.getElementById("profile-codeagent-password").value = "relay-password";
document.getElementById("profile-codeagent-password").oninput();
document.getElementById("profile-model").value = "codeagent-chat";
document.getElementById("profile-model").oninput();

await document.getElementById("fetch-profile-models-btn").onclick();

console.log(JSON.stringify({
    discoverPayload: globalThis.__discoverPayload,
    authStatus: document.getElementById("profile-codeagent-login-status-message").textContent,
}));
""".strip(),
        mock_api_source="""
export async function fetchModelProfiles() {
    return {};
}

export async function fetchModelFallbackConfig() {
    return { policies: [] };
}

export async function discoverModelCatalog(payload) {
    globalThis.__discoverPayload = payload;
    return { ok: true, latency_ms: 37, models: ["codeagent-chat"] };
}
""".strip(),
    )

    discover_payload = cast(dict[str, JsonValue], payload["discoverPayload"])
    override = cast(dict[str, JsonValue], discover_payload["override"])
    codeagent_auth = cast(dict[str, JsonValue], override["codeagent_auth"])

    assert codeagent_auth == {
        "auth_method": "password",
        "username": "relay-user",
        "password": "relay-password",
    }
    assert payload["authStatus"] == "Credentials ready"


def test_editing_saved_codeagent_password_profile_requires_new_password_after_username_change(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

await loadModelProfilesPanel();
document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn").find(btn => btn.dataset.name === "codeagent-profile").onclick();
document.getElementById("profile-codeagent-username").value = "new-user";
document.getElementById("profile-codeagent-username").oninput();
await document.getElementById("save-profile-btn").onclick();

console.log(JSON.stringify({
    notifications,
    savedProfile: globalThis.__savedProfile || null,
}));
""".strip(),
        mock_api_source="""
export async function fetchModelProfiles() {
    return {
        "codeagent-profile": {
            provider: "codeagent",
            model: "codeagent-chat",
            base_url: "https://codeagentcli.rnd.huawei.com/codeAgentPro",
            codeagent_auth: {
                auth_method: "password",
                username: "old-user",
                has_password: true,
            },
        },
    };
}

export async function fetchModelFallbackConfig() {
    return { policies: [] };
}
""".strip(),
    )

    notifications = cast(list[dict[str, JsonValue]], payload["notifications"])

    assert payload["savedProfile"] is None
    assert notifications == [
        {
            "title": "Save Failed",
            "message": "Re-enter the CodeAgent password after changing the username.",
            "tone": "warning",
        }
    ]


def test_codeagent_sso_polling_stops_when_auth_session_changes(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

globalThis.setTimeout = callback => {
    callback();
    return 0;
};

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-provider").value = "codeagent";
document.getElementById("profile-provider").onchange();
await document.getElementById("profile-codeagent-login-status").onclick();

console.log(JSON.stringify({
    sessionChecks: globalThis.__codeAgentOAuthSessionChecks,
    loginButtonText: document.getElementById("profile-codeagent-login-status").textContent,
}));
""".strip(),
        mock_api_source="""
export async function fetchModelProfiles() {
    return {};
}

export async function fetchModelFallbackConfig() {
    return { policies: [] };
}

export async function probeModelConnection(payload) {
    globalThis.__probePayload = payload;
    return { ok: true, latency_ms: 42 };
}

export async function discoverModelCatalog(payload) {
    globalThis.__discoverPayload = payload;
    return { ok: true, latency_ms: 37, models: ["codeagent-chat"] };
}

export async function saveModelProfile(name, profile) {
    globalThis.__savedProfile = { name, profile };
}

export async function startCodeAgentOAuth() {
    return {
        auth_session_id: "stale-auth-session",
        authorization_url: "https://example.test/codeagent-sso",
    };
}

export async function fetchCodeAgentOAuthSession(authSessionId) {
    globalThis.__codeAgentOAuthSessionChecks = globalThis.__codeAgentOAuthSessionChecks || [];
    globalThis.__codeAgentOAuthSessionChecks.push(authSessionId);
    if (globalThis.__codeAgentOAuthSessionChecks.length === 1) {
        document.getElementById("cancel-profile-btn").onclick();
        return { completed: false };
    }
    throw new Error("unexpected extra poll");
}

export async function reloadModelConfig() {
    globalThis.__reloadCalled = true;
}

export async function deleteModelProfile(name) {
    globalThis.__deletedProfileName = name;
}
""".strip(),
    )

    assert payload["sessionChecks"] == ["stale-auth-session"]
    assert payload["loginButtonText"] == "Sign in with SSO"


def test_saving_maas_profile_sends_maas_auth_payload(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-name").value = "maas-profile";
document.getElementById("profile-provider").value = "maas";
document.getElementById("profile-provider").onchange();
document.getElementById("profile-model").value = "maas-chat";
document.getElementById("profile-maas-username").value = "relay-user";
document.getElementById("profile-maas-password").value = "relay-password";

await document.getElementById("save-profile-btn").onclick();

console.log(JSON.stringify({
    savedProfile: globalThis.__savedProfile,
    apiKeyGroupDisplay: document.getElementById("profile-api-key-group").style.display,
    maasFieldDisplay: document.getElementById("profile-maas-auth-fields").style.display,
}));
""".strip(),
    )

    saved_profile = cast(dict[str, JsonValue], payload["savedProfile"])
    saved_profile_body = cast(dict[str, JsonValue], saved_profile["profile"])
    maas_auth = cast(dict[str, JsonValue], saved_profile_body["maas_auth"])
    assert saved_profile["name"] == "maas-profile"
    assert saved_profile_body["provider"] == "maas"
    assert "api_key" not in saved_profile_body
    assert (
        saved_profile_body["base_url"]
        == "http://snapengine.cida.cce.prod-szv-g.dragon.tools.huawei.com/api/v2/"
    )
    assert maas_auth == {
        "username": "relay-user",
        "password": "relay-password",
    }
    assert payload["apiKeyGroupDisplay"] == "none"
    assert payload["maasFieldDisplay"] == "grid"


def test_selecting_maas_keeps_model_discovery_enabled(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-provider").value = "maas";
document.getElementById("profile-provider").onchange();

console.log(JSON.stringify({
    fetchDisabled: document.getElementById("fetch-profile-models-btn").disabled,
    fetchTitle: document.getElementById("fetch-profile-models-btn").title,
}));
""".strip(),
    )

    assert payload["fetchDisabled"] is False
    assert payload["fetchTitle"] == "Fetch Models"


def test_discover_models_for_new_maas_profile_sends_maas_auth(tmp_path: Path) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

document.getElementById("add-profile-btn").onclick();
document.getElementById("profile-provider").value = "maas";
document.getElementById("profile-provider").onchange();
document.getElementById("profile-maas-username").value = "relay-user";
document.getElementById("profile-maas-password").value = "relay-password";
document.getElementById("profile-maas-password").oninput();

await document.getElementById("fetch-profile-models-btn").onclick();

console.log(JSON.stringify({
    discoverPayload: globalThis.__discoverPayload,
    discoveryStatusText: document.getElementById("profile-model-discovery-status").textContent,
}));
""".strip(),
    )

    discover_payload = cast(dict[str, JsonValue], payload["discoverPayload"])
    discover_override = cast(dict[str, JsonValue], discover_payload["override"])
    maas_auth = cast(dict[str, JsonValue], discover_override["maas_auth"])
    assert discover_override["provider"] == "maas"
    assert discover_override["base_url"] == (
        "http://snapengine.cida.cce.prod-szv-g.dragon.tools.huawei.com/api/v2/"
    )
    assert maas_auth == {
        "username": "relay-user",
        "password": "relay-password",
    }
    assert payload["discoveryStatusText"] == "Fetched 2 models in 37ms."


def test_discover_models_for_existing_maas_profile_reuses_saved_password(
    tmp_path: Path,
) -> None:
    payload = _run_model_profiles_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindModelProfileHandlers, loadModelProfilesPanel } from "./modelProfiles.mjs";

const notifications = [];

const elements = createElements();
installGlobals(elements, notifications);
bindModelProfileHandlers();

await loadModelProfilesPanel();
document.getElementById("profiles-list").querySelectorAll(".edit-profile-btn").find(btn => btn.dataset.name === "maas-profile").onclick();
await document.getElementById("fetch-profile-models-btn").onclick();

console.log(JSON.stringify({
    discoverPayload: globalThis.__discoverPayload,
}));
""".strip(),
        mock_api_source="""
export async function fetchModelProfiles() {
    return {
        "maas-profile": {
            provider: "maas",
            model: "maas-chat",
            base_url: "http://snapengine.cida.cce.prod-szv-g.dragon.tools.huawei.com/api/v2/",
            maas_auth: {
                username: "saved-user",
                password: "saved-password",
                has_password: true,
            },
            is_default: false,
            temperature: 0.7,
            top_p: 1.0,
            connect_timeout_seconds: 15,
        },
    };
}

export async function probeModelConnection(payload) {
    globalThis.__probePayload = payload;
    return { ok: true, latency_ms: 42, token_usage: { total_tokens: 9 } };
}

export async function discoverModelCatalog(payload) {
    globalThis.__discoverPayload = payload;
    return { ok: true, latency_ms: 37, models: ["maas-chat"] };
}

export async function saveModelProfile(name, profile) {
    globalThis.__savedProfile = { name, profile };
}

export async function reloadModelConfig() {
    globalThis.__reloadCalled = true;
}

export async function deleteModelProfile(name) {
    globalThis.__deletedProfileName = name;
}
""".strip(),
    )

    discover_payload = cast(dict[str, JsonValue], payload["discoverPayload"])
    discover_override = cast(dict[str, JsonValue], discover_payload["override"])
    maas_auth = cast(dict[str, JsonValue], discover_override["maas_auth"])
    assert discover_payload["profile_name"] == "maas-profile"
    assert maas_auth == {
        "username": "saved-user",
    }


def _run_model_profiles_script(
    tmp_path: Path,
    runner_source: str,
    mock_api_source: str = DEFAULT_MOCK_API_SOURCE,
    mock_i18n_source: str | None = None,
) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root
        / "frontend"
        / "dist"
        / "js"
        / "components"
        / "settings"
        / "modelProfiles.js"
    )

    mock_api_path = tmp_path / "mockApi.mjs"
    mock_logger_path = tmp_path / "mockLogger.mjs"
    mock_feedback_path = tmp_path / "mockFeedback.mjs"
    mock_i18n_path = tmp_path / "mockI18n.mjs"
    module_under_test_path = tmp_path / "modelProfiles.mjs"
    runner_path = tmp_path / "runner.mjs"

    resolved_mock_api_source = mock_api_source
    if "fetchModelFallbackConfig" not in resolved_mock_api_source:
        resolved_mock_api_source = (
            f"{resolved_mock_api_source}\n\n"
            "export async function fetchModelFallbackConfig() {\n"
            "    return { policies: [] };\n"
            "}\n"
        )
    if "fetchModelProfiles" not in resolved_mock_api_source:
        resolved_mock_api_source = (
            f"{resolved_mock_api_source}\n\n"
            "export async function fetchModelProfiles() {\n"
            "    return {};\n"
            "}\n"
        )
    if "fetchModelCatalog" not in resolved_mock_api_source:
        resolved_mock_api_source = (
            f"{resolved_mock_api_source}\n\n"
            "export async function fetchModelCatalog() {\n"
            "    return { ok: true, providers: [] };\n"
            "}\n\n"
            "export async function refreshModelCatalog() {\n"
            "    globalThis.__refreshModelCatalogCount = (globalThis.__refreshModelCatalogCount || 0) + 1;\n"
            "    return fetchModelCatalog();\n"
            "}\n"
        )
    if "probeModelConnection" not in resolved_mock_api_source:
        resolved_mock_api_source = (
            f"{resolved_mock_api_source}\n\n"
            "export async function probeModelConnection(payload) {\n"
            "    globalThis.__probePayload = payload;\n"
            "    return { ok: true, latency_ms: 42 };\n"
            "}\n"
        )
    if "discoverModelCatalog" not in resolved_mock_api_source:
        resolved_mock_api_source = (
            f"{resolved_mock_api_source}\n\n"
            "export async function discoverModelCatalog(payload) {\n"
            "    globalThis.__discoverPayload = payload;\n"
            "    return { ok: true, latency_ms: 37, models: [] };\n"
            "}\n"
        )
    if "startCodeAgentOAuth" not in resolved_mock_api_source:
        resolved_mock_api_source = (
            f"{resolved_mock_api_source}\n\n"
            "export async function startCodeAgentOAuth() {\n"
            "    globalThis.__codeAgentOAuthStartCalls = (globalThis.__codeAgentOAuthStartCalls || 0) + 1;\n"
            "    return {\n"
            "        auth_session_id: 'mock-auth-session',\n"
            "        authorization_url: 'https://example.test/codeagent-sso',\n"
            "    };\n"
            "}\n"
        )
    if "fetchCodeAgentOAuthSession" not in resolved_mock_api_source:
        resolved_mock_api_source = (
            f"{resolved_mock_api_source}\n\n"
            "export async function fetchCodeAgentOAuthSession(authSessionId) {\n"
            "    globalThis.__codeAgentOAuthSessionChecks = globalThis.__codeAgentOAuthSessionChecks || [];\n"
            "    globalThis.__codeAgentOAuthSessionChecks.push(authSessionId);\n"
            "    return { completed: true };\n"
            "}\n"
        )
    if "verifyCodeAgentAuth" not in resolved_mock_api_source:
        resolved_mock_api_source = (
            f"{resolved_mock_api_source}\n\n"
            "export async function verifyCodeAgentAuth(profileName) {\n"
            "    globalThis.__codeAgentAuthVerifyCalls = globalThis.__codeAgentAuthVerifyCalls || [];\n"
            "    globalThis.__codeAgentAuthVerifyCalls.push(profileName);\n"
            "    return { status: 'valid', checked_at: '2026-04-27T02:00:00Z', detail: null };\n"
            "}\n"
        )
    if "saveModelProfile" not in resolved_mock_api_source:
        resolved_mock_api_source = (
            f"{resolved_mock_api_source}\n\n"
            "export async function saveModelProfile(name, profile) {\n"
            "    globalThis.__savedProfile = { name, profile };\n"
            "}\n"
        )
    if "reloadModelConfig" not in resolved_mock_api_source:
        resolved_mock_api_source = (
            f"{resolved_mock_api_source}\n\n"
            "export async function reloadModelConfig() {\n"
            "    globalThis.__reloadCalled = true;\n"
            "}\n"
        )
    if "deleteModelProfile" not in resolved_mock_api_source:
        resolved_mock_api_source = (
            f"{resolved_mock_api_source}\n\n"
            "export async function deleteModelProfile(name) {\n"
            "    globalThis.__deletedProfileName = name;\n"
            "}\n"
        )
    mock_api_path.write_text(resolved_mock_api_source, encoding="utf-8")
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
    mock_feedback_path.write_text(
        """
export function showToast(payload) {
    globalThis.__feedbackNotifications.push(payload);
}

export async function showConfirmDialog(payload) {
    globalThis.__feedbackConfirms.push(payload);
    return true;
}
""".strip(),
        encoding="utf-8",
    )
    resolved_mock_i18n_source = (
        mock_i18n_source
        or """
const translations = {
    "settings.model.add_profile": "Add Profile",
    "settings.model.edit_profile": "Edit Profile",
    "settings.model.empty_title": "No profiles configured",
    "settings.model.empty_copy": "Create a profile to define the model endpoint, request limits, and sampling defaults.",
    "settings.model.saved_title": "Profile Saved",
    "settings.model.saved_message_detail": "Profile saved and reloaded.",
    "settings.model.save_failed_title": "Save Failed",
    "settings.model.save_failed_detail": "Failed to save: {error}",
    "settings.model.testing": "Testing connection...",
    "settings.model.probe_failed": "Probe failed: {error}",
    "settings.model.delete_title": "Delete Profile",
    "settings.model.delete_message": "Delete profile \\"{name}\\"?",
    "settings.model.deleted_title": "Profile Deleted",
    "settings.model.deleted_message_detail": "Profile deleted and reloaded.",
    "settings.model.delete_failed_title": "Delete Failed",
    "settings.model.delete_failed_detail": "Failed to delete: {error}",
    "settings.model.fetching_models": "Fetching models...",
    "settings.model.fetch_failed": "Fetch failed: {error}",
    "settings.model.fetch_models": "Fetch Models",
    "settings.model.validation_test_new": "Model, base URL, and API key are required before testing a new profile.",
    "settings.model.validation_fetch_models": "Base URL and API key are required before fetching models for a new profile.",
    "settings.model.probe_success": "Connected in {latency_ms}ms{usage_text}",
    "settings.model.connection_failed": "Connection failed: {reason}",
    "settings.model.probe_no_models": "Connected in {latency_ms}ms, but the endpoint returned no models.",
    "settings.model.models_fetched": "Fetched {count} models in {latency_ms}ms.",
    "settings.model.usage_tokens": " · {tokens} tokens",
    "settings.model.context_window_compact": "{count} ctx",
    "settings.model.show_models": "Show Models",
    "settings.model.no_models_loaded": "No Models Loaded",
    "settings.model.catalog_title": "Model Catalog",
    "settings.model.catalog_refresh": "Refresh",
    "settings.model.catalog_provider_search": "Search providers",
    "settings.model.catalog_model_search": "Search models",
    "settings.model.catalog_loading": "Loading model catalog...",
    "settings.model.catalog_refreshing": "Refreshing model catalog...",
    "settings.model.catalog_failed": "Catalog unavailable: {error}",
    "settings.model.catalog_empty": "No providers match the search.",
    "settings.model.catalog_no_models": "No models match the search.",
    "settings.model.catalog_select_provider_first": "Select a provider first.",
    "settings.model.catalog_loaded": "{providers} providers, {models} models · {age}",
    "settings.model.catalog_cache_current": "just updated",
    "settings.model.catalog_cache_age": "{seconds}s old",
    "settings.model.catalog_reasoning": "reasoning",
    "settings.model.catalog_tools": "tools",
    "settings.model.capability_section": "Capabilities",
    "settings.model.image_capability": "Image Input",
    "settings.model.image_capability_follow": "Follow detection",
    "settings.model.image_capability_supported": "Supports image input",
    "settings.model.image_capability_unsupported": "Text only",
    "settings.model.username": "Username",
    "settings.model.username_placeholder": "username",
    "settings.model.password": "Password",
    "settings.model.password_placeholder": "password",
    "settings.model.codeagent_auth_method": "Authentication Method",
    "settings.model.codeagent_auth_method_sso": "SSO Sign-In",
    "settings.model.codeagent_auth_method_password": "Username and Password",
    "settings.model.codeagent_sso_field": "SSO Sign-In",
    "settings.model.codeagent_username": "Username",
    "settings.model.codeagent_username_placeholder": "username",
    "settings.model.codeagent_password": "Password",
    "settings.model.codeagent_password_placeholder": "password",
    "settings.model.codeagent_sign_in_sso": "Sign in with SSO",
    "settings.model.codeagent_sso_starting": "Starting SSO login",
    "settings.model.codeagent_sso_saved": "Saved sign-in requires verification",
    "settings.model.codeagent_sso_verifying": "Verifying saved sign-in",
    "settings.model.codeagent_sso_waiting": "Waiting for SSO callback",
    "settings.model.codeagent_sso_signed_in": "Signed in",
    "settings.model.codeagent_sso_reauth_required": "Saved sign-in expired. Sign in with SSO again.",
    "settings.model.codeagent_sso_popup_blocked": "SSO popup was blocked. Click Sign in with SSO again to continue.",
    "settings.model.codeagent_sso_timed_out": "SSO login timed out",
    "settings.model.codeagent_sso_failed": "SSO failed: {error}",
    "settings.model.codeagent_credentials_saved": "Saved credentials require verification",
    "settings.model.codeagent_credentials_verifying": "Verifying saved credentials",
    "settings.model.codeagent_credentials_expired": "Saved credentials expired. Update username or password.",
    "settings.model.codeagent_password_reenter": "Re-enter password after changing username.",
    "settings.model.codeagent_credentials_ready": "Credentials ready",
    "settings.model.codeagent_credentials_verified": "Credentials verified",
    "settings.model.show_api_key": "Show API key",
    "settings.model.hide_api_key": "Hide API key",
    "settings.model.show_password": "Show password",
    "settings.model.hide_password": "Hide password",
    "settings.model.default_badge": "Default",
    "settings.model.default_model_action": "Set as default model",
    "settings.model.default_model_action_short": "Set default",
    "settings.model.default_saved_title": "Default Model Updated",
    "settings.model.default_saved_message": "{name} is now the default model.",
    "settings.model.provider_external": "Model Marketplace",
    "settings.model.provider_maas": "MaaS Model",
    "settings.model.provider_codeagent_copy": "Use CodeAgent models with SSO or username/password sign-in",
    "settings.model.provider_codeagent": "CodeAgent Model",
    "settings.model.provider_custom": "Custom Model",
    "settings.model.custom_model": "Custom model",
    "settings.model.custom_model_catalog_hint": "Enter a model name manually",
    "settings.model.use_custom_model": "Use",
    "settings.model.credentials_configured": "Credentials configured",
    "settings.model.credentials_missing": "Credentials missing",
    "settings.model.advanced_summary": "Temperature {temperature} · Top P {top_p}",
    "settings.model.catalog_selected": "Selected Model",
    "settings.model.catalog_selected_empty": "Choose a model from the catalog.",
    "settings.model.capability_image_input": "Image input",
    "settings.model.capability_text_only": "Text only",
    "settings.model.capability_unknown": "Capability unknown",
    "settings.model.no_model": "No model",
    "settings.model.no_endpoint": "No endpoint",
    "settings.model.fallback_policy_same_provider_then_other_provider": "Same Provider Then Other Provider",
    "settings.model.fallback_policy_other_provider_only": "Other Provider Only",
    "settings.model.unknown": "Unknown",
    "settings.action.test": "Test",
    "settings.action.edit": "Edit",
    "settings.action.delete": "Delete",
    "settings.action.cancel": "Cancel",
};

export function t(key) {
    return translations[key] || key;
}
""".strip()
    )
    mock_i18n_path.write_text(
        resolved_mock_i18n_source,
        encoding="utf-8",
    )

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("../../core/api.js", "./mockApi.mjs")
        .replace("../../utils/logger.js", "./mockLogger.mjs")
        .replace("../../utils/feedback.js", "./mockFeedback.mjs")
        .replace("../../utils/i18n.js", "./mockI18n.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    runner_path.write_text(
        f"""
function createElement(initialDisplay = "block", id = "") {{
    let lastQuerySource = "";
    const queryCache = new Map();

    function collectMatches(source, selector) {{
        const selectorToClass = new Map([
            [".edit-profile-btn", "edit-profile-btn"],
            [".delete-profile-btn", "delete-profile-btn"],
            [".set-default-profile-btn", "set-default-profile-btn"],
            [".profile-card-test-btn", "profile-card-test-btn"],
            [".profile-model-menu-item", "profile-model-menu-item"],
            [".model-catalog-provider-btn", "model-catalog-provider-btn"],
            [".model-catalog-model-btn", "model-catalog-model-btn"],
            [".model-catalog-custom-model-btn", "model-catalog-custom-model-btn"],
        ]);
        const className = selectorToClass.get(selector);
        if (!className) {{
            return [];
        }}
        const datasetSpecs = new Map([
            ["profile-model-menu-item", [["data-model-name", "modelName"]]],
            ["model-catalog-provider-btn", [["data-provider-id", "providerId"]]],
            ["model-catalog-model-btn", [["data-provider-id", "providerId"], ["data-model-id", "modelId"]]],
            ["model-catalog-custom-model-btn", []],
        ]);
        const specs = datasetSpecs.get(className) || [["data-name", "name"]];
        const pattern = new RegExp(`class="[^"]*${{className}}[^"]*"[^>]*>`, "g");
        const matches = [];
        let match = pattern.exec(source);
        while (match) {{
            const tag = match[0];
            const dataset = {{}};
            specs.forEach(([attributeName, datasetKey]) => {{
                const attributeMatch = new RegExp(`${{attributeName}}="([^"]*)"`).exec(tag);
                dataset[datasetKey] = attributeMatch ? attributeMatch[1] : "";
            }});
            matches.push({{
                dataset,
                onclick: null,
            }});
            match = pattern.exec(source);
        }}
        return matches;
    }}

    return {{
        id,
        style: {{ display: initialDisplay }},
        value: "",
        disabled: false,
        placeholder: "",
        type: "text",
        checked: false,
        title: "",
        ariaLabel: "",
        textContent: "",
        innerHTML: "",
        className: "",
        dataset: {{}},
        parentElement: null,
        children: [],
        onclick: null,
        oninput: null,
        onblur: null,
        onkeydown: null,
        focused: false,
        attributes: {{}},
        appendChild(child) {{
            if (child.parentElement) {{
                child.parentElement.children = child.parentElement.children.filter(candidate => candidate !== child);
            }}
            child.parentElement = this;
            this.children.push(child);
            return child;
        }},
        focus() {{
            this.focused = true;
        }},
        setAttribute(name, value) {{
            this.attributes[name] = value;
            if (name === "aria-label") {{
                this.ariaLabel = value;
            }}
        }},
        closest(selector) {{
            return this.closestElements?.[selector] || null;
        }},
        querySelectorAll(selector) {{
            if (this.innerHTML !== lastQuerySource) {{
                queryCache.clear();
                lastQuerySource = this.innerHTML;
            }}
            if (!queryCache.has(selector)) {{
                queryCache.set(selector, collectMatches(this.innerHTML, selector));
            }}
            return queryCache.get(selector) || [];
        }},
    }};
}}

function createElements() {{
        const baseUrlGroup = createElement("block", "profile-base-url-group");
        const entries = [
            ["profiles-list", createElement("block", "profiles-list")],
            ["profile-editor", createElement("none", "profile-editor")],
            ["profile-model-step", createElement("block", "profile-model-step")],
            ["profile-model-step-toggle", createElement("block", "profile-model-step-toggle")],
            ["profile-model-summary", createElement("block", "profile-model-summary")],
            ["model-catalog-panel", createElement("none", "model-catalog-panel")],
            ["refresh-model-catalog-btn", createElement("block", "refresh-model-catalog-btn")],
            ["model-catalog-provider-search", createElement("block", "model-catalog-provider-search")],
            ["model-catalog-model-search", createElement("block", "model-catalog-model-search")],
            ["model-catalog-status", createElement("block", "model-catalog-status")],
            ["model-catalog-provider-list", createElement("block", "model-catalog-provider-list")],
            ["model-catalog-model-list", createElement("block", "model-catalog-model-list")],
            ["profile-base-url-toggle-row", createElement("block", "profile-base-url-toggle-row")],
            ["toggle-profile-base-url-btn", createElement("block", "toggle-profile-base-url-btn")],
            ["profile-base-url-fields", createElement("none", "profile-base-url-fields")],
            ["profile-model-field-home", createElement("block", "profile-model-field-home")],
            ["add-profile-btn", createElement("block", "add-profile-btn")],
            ["save-profile-btn", createElement("block", "save-profile-btn")],
            ["test-profile-btn", createElement("block", "test-profile-btn")],
            ["fetch-profile-models-btn", createElement("block", "fetch-profile-models-btn")],
            ["open-profile-model-menu-btn", createElement("block", "open-profile-model-menu-btn")],
            ["cancel-profile-btn", createElement("block", "cancel-profile-btn")],
            ["profile-probe-inline-status", createElement("none", "profile-probe-inline-status")],
            ["profile-model-discovery-status", createElement("none", "profile-model-discovery-status")],
            ["profile-editor-title", createElement("block", "profile-editor-title")],
            ["profile-name", createElement("block", "profile-name")],
            ["profile-provider", createElement("block", "profile-provider")],
            ["profile-provider-options", createElement("block", "profile-provider-options")],
            ["profile-provider-external-btn", createElement("block", "profile-provider-external-btn")],
            ["profile-provider-maas-btn", createElement("block", "profile-provider-maas-btn")],
            ["profile-provider-codeagent-btn", createElement("block", "profile-provider-codeagent-btn")],
            ["profile-provider-custom-btn", createElement("block", "profile-provider-custom-btn")],
            ["profile-is-default", createElement("block", "profile-is-default")],
            ["profile-model", createElement("block", "profile-model")],
            ["profile-model-menu", createElement("none", "profile-model-menu")],
            ["profile-base-url", createElement("block", "profile-base-url")],
            ["profile-primary-credentials-row", createElement("grid", "profile-primary-credentials-row")],
            ["profile-api-key-group", createElement("block", "profile-api-key-group")],
            ["profile-api-key", createElement("block", "profile-api-key")],
            ["profile-model-group", createElement("block", "profile-model-group")],
            ["toggle-profile-api-key-btn", createElement("none", "toggle-profile-api-key-btn")],
            ["profile-maas-auth-fields", createElement("none", "profile-maas-auth-fields")],
            ["profile-maas-model-slot", createElement("block", "profile-maas-model-slot")],
            ["profile-codeagent-auth-fields", createElement("none", "profile-codeagent-auth-fields")],
            ["profile-codeagent-model-slot", createElement("block", "profile-codeagent-model-slot")],
            ["profile-codeagent-auth-method", createElement("block", "profile-codeagent-auth-method")],
            ["profile-codeagent-sso-group", createElement("block", "profile-codeagent-sso-group")],
            ["profile-codeagent-login-status", createElement("block", "profile-codeagent-login-status")],
            ["profile-codeagent-login-status-message", createElement("none", "profile-codeagent-login-status-message")],
            ["profile-codeagent-username-group", createElement("none", "profile-codeagent-username-group")],
            ["profile-codeagent-username", createElement("block", "profile-codeagent-username")],
            ["profile-codeagent-password-group", createElement("none", "profile-codeagent-password-group")],
            ["profile-codeagent-password", createElement("block", "profile-codeagent-password")],
            ["toggle-profile-codeagent-password-btn", createElement("none", "toggle-profile-codeagent-password-btn")],
            ["profile-maas-username", createElement("block", "profile-maas-username")],
            ["profile-maas-password", createElement("block", "profile-maas-password")],
            ["toggle-profile-maas-password-btn", createElement("none", "toggle-profile-maas-password-btn")],
            ["profile-temperature", createElement("block", "profile-temperature")],
            ["profile-top-p", createElement("block", "profile-top-p")],
            ["profile-max-tokens", createElement("block", "profile-max-tokens")],
            ["profile-context-window", createElement("block", "profile-context-window")],
            ["profile-connect-timeout", createElement("block", "profile-connect-timeout")],
            ["profile-ssl-verify", createElement("block", "profile-ssl-verify")],
            ["profile-image-capability", createElement("block", "profile-image-capability")],
            ["profile-fallback-policy", createElement("block", "profile-fallback-policy")],
            ["profile-fallback-priority", createElement("block", "profile-fallback-priority")],
        ];
        const elements = new Map(entries);
        const modelStep = elements.get("profile-model-step");
        if (modelStep) {{
            modelStep.className = "model-profile-step is-open";
            modelStep.dataset.profileStep = "model";
        }}
        const modelStepToggle = elements.get("profile-model-step-toggle");
        if (modelStepToggle) {{
            modelStepToggle.dataset.profileStepToggle = "model";
        }}
        elements.get("profile-provider-external-btn").dataset.providerMode = "external";
        elements.get("profile-provider-maas-btn").dataset.providerMode = "maas";
        elements.get("profile-provider-codeagent-btn").dataset.providerMode = "codeagent";
        elements.get("profile-provider-custom-btn").dataset.providerMode = "custom";
        elements.get("profile-primary-credentials-row")?.appendChild(elements.get("profile-api-key-group"));
        elements.get("profile-model-field-home")?.appendChild(elements.get("profile-model-group"));
        elements.get("profile-maas-auth-fields")?.appendChild(elements.get("profile-maas-model-slot"));
        elements.get("profile-codeagent-auth-fields")?.appendChild(elements.get("profile-codeagent-model-slot"));
        elements.get("profile-codeagent-auth-fields")?.appendChild(elements.get("profile-codeagent-auth-method"));
        elements.get("profile-codeagent-auth-fields")?.appendChild(elements.get("profile-codeagent-sso-group"));
        elements.get("profile-codeagent-auth-fields")?.appendChild(elements.get("profile-codeagent-username-group"));
        elements.get("profile-codeagent-auth-fields")?.appendChild(elements.get("profile-codeagent-password-group"));
        elements.get("profile-codeagent-sso-group")?.appendChild(elements.get("profile-codeagent-login-status"));
        elements.get("profile-codeagent-sso-group")?.appendChild(elements.get("profile-codeagent-login-status-message"));
        elements.get("profile-codeagent-username-group")?.appendChild(elements.get("profile-codeagent-username"));
        elements.get("profile-codeagent-password-group")?.appendChild(elements.get("profile-codeagent-password"));
        elements.get("profile-codeagent-password-group")?.appendChild(elements.get("toggle-profile-codeagent-password-btn"));
        elements.get("profile-base-url").closestElements = {{ ".form-group": baseUrlGroup }};
        return elements;
    }}

function installGlobals(elements, notifications) {{
    const documentListeners = new Map();

    function collectDocumentMatches(selector) {{
        if (selector === "[data-provider-value]") {{
            return [
                elements.get("profile-provider-external-btn"),
                elements.get("profile-provider-maas-btn"),
                elements.get("profile-provider-codeagent-btn"),
                elements.get("profile-provider-custom-btn"),
            ].filter(Boolean);
        }}
        if (selector === "[data-profile-step-toggle]") {{
            return [elements.get("profile-model-step-toggle")].filter(Boolean);
        }}
        if (selector === "[data-profile-step]") {{
            return [elements.get("profile-model-step")].filter(Boolean);
        }}
        if (selector !== ".profile-card") {{
            return [];
        }}
        const source = elements.get("profiles-list")?.innerHTML || "";
        const pattern = /data-profile-name="([^"]+)"/g;
        const matches = [];
        let match = pattern.exec(source);
        while (match) {{
            const profileName = match[1];
            matches.push({{
                dataset: {{ profileName }},
                querySelector(innerSelector) {{
                    if (innerSelector === ".profile-card-test-btn") {{
                        return elements
                            .get("profiles-list")
                            ?.querySelectorAll(".profile-card-test-btn")
                            .find(candidate => candidate.dataset.name === profileName) || null;
                    }}
                    if (innerSelector === "[data-profile-probe-container]") {{
                        return {{
                            innerHTML: "",
                        }};
                    }}
                    return null;
                }},
            }});
            match = pattern.exec(source);
        }}
        return matches;
    }}

    globalThis.document = {{
        getElementById(id) {{
            const element = elements.get(id);
            if (!element) {{
                throw new Error(`Missing element: ${{id}}`);
            }}
            return element;
        }},
        querySelectorAll(selector) {{
            return collectDocumentMatches(selector);
        }},
        addEventListener(type, listener) {{
            if (!documentListeners.has(type)) {{
                documentListeners.set(type, []);
            }}
            documentListeners.get(type).push(listener);
        }},
        removeEventListener(type, listener) {{
            const listeners = documentListeners.get(type) || [];
            documentListeners.set(
                type,
                listeners.filter(candidate => candidate !== listener),
            );
        }},
        dispatchEvent(event) {{
            const eventType = String(event?.type || "");
            globalThis.__dispatchedEvents.push(eventType);
            const listeners = documentListeners.get(eventType) || [];
            listeners.forEach(listener => listener(event));
            return true;
        }},
    }};
    globalThis.CustomEvent = class {{
        constructor(type) {{
            this.type = type;
        }}
    }};
    globalThis.window = {{
        open(...args) {{
            globalThis.__windowOpens.push(args);
            if (globalThis.__windowOpenReturnsNull) {{
                return null;
            }}
            return {{
                location: {{
                    replace(value) {{
                        globalThis.__windowOpenNavigations.push(value);
                    }},
                    set href(value) {{
                        globalThis.__windowOpenNavigations.push(value);
                    }},
                }},
                close() {{
                    globalThis.__windowClosed = true;
                }},
            }};
        }},
        location: {{
            assign(value) {{
                globalThis.__windowLocationNavigations.push(value);
            }},
            set href(value) {{
                globalThis.__windowLocationNavigations.push(value);
            }},
        }},
    }};
    globalThis.__feedbackNotifications = notifications;
    globalThis.__feedbackConfirms = [];
    globalThis.__dispatchedEvents = [];
    globalThis.__windowOpens = [];
    globalThis.__windowOpenNavigations = [];
    globalThis.__windowLocationNavigations = [];
    globalThis.__windowOpenReturnsNull = false;
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
        encoding="utf-8",
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
