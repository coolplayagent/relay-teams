# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

import json
from pathlib import Path
import subprocess
from typing import cast

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
""".strip()


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
    probeStatusText: document.getElementById("profile-probe-status").textContent,
    probeStatusDisplay: document.getElementById("profile-probe-status").style.display,
    probePayload: globalThis.__probePayload,
}));
""".strip(),
    )

    probe_payload = cast(dict[str, JsonValue], payload["probePayload"])
    probe_override = cast(dict[str, JsonValue], probe_payload["override"])
    probe_status_text = cast(str, payload["probeStatusText"])
    assert payload["notifications"] == []
    assert payload["testButtonText"] == "Test"
    assert payload["probeStatusDisplay"] == "block"
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


def test_selecting_bigmodel_prefills_default_base_url(tmp_path: Path) -> None:
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
    assert payload["baseUrlValue"] == "https://open.bigmodel.cn/api/coding/paas/v4"


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
    assert payload["baseUrlValue"] == "https://open.bigmodel.cn/api/coding/paas/v4"


def test_edit_profile_switching_to_bigmodel_prefills_default_base_url(
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
    assert payload["baseUrlValue"] == "https://open.bigmodel.cn/api/coding/paas/v4"


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
    assert payload["baseUrlValue"] == "https://open.bigmodel.cn/api/coding/paas/v4"


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


def test_selecting_minimax_prefills_default_base_url(tmp_path: Path) -> None:
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
    assert payload["baseUrlValue"] == "https://api.minimaxi.com/v1"


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


def test_switching_to_maas_moves_model_group_below_credentials(tmp_path: Path) -> None:
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
document.getElementById("profile-provider").value = "openai_compatible";
document.getElementById("profile-provider").onchange();

console.log(JSON.stringify({
    initialParentId,
    maasParentId,
    finalParentId: document.getElementById("profile-model-group").parentElement?.id || null,
    primaryRowDisplay: document.getElementById("profile-primary-credentials-row").style.display,
}));
""".strip(),
    )

    assert payload["initialParentId"] == "profile-primary-credentials-row"
    assert payload["maasParentId"] == "profile-maas-model-slot"
    assert payload["finalParentId"] == "profile-primary-credentials-row"
    assert payload["primaryRowDisplay"] == "grid"


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
    mock_i18n_path.write_text(
        """
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
    "settings.model.show_models": "Show Models",
    "settings.model.no_models_loaded": "No Models Loaded",
    "settings.model.show_api_key": "Show API key",
    "settings.model.hide_api_key": "Hide API key",
    "settings.model.show_password": "Show password",
    "settings.model.hide_password": "Hide password",
    "settings.model.default_badge": "Default",
    "settings.model.no_model": "No model",
    "settings.model.no_endpoint": "No endpoint",
    "settings.model.unknown": "Unknown",
    "settings.action.test": "Test",
    "settings.action.edit": "Edit",
    "settings.action.delete": "Delete",
    "settings.action.cancel": "Cancel",
};

export function t(key) {
    return translations[key] || key;
}
""".strip(),
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
            [".profile-card-test-btn", "profile-card-test-btn"],
            [".profile-model-menu-item", "profile-model-menu-item"],
        ]);
        const className = selectorToClass.get(selector);
        if (!className) {{
            return [];
        }}
        const dataAttribute = className === "profile-model-menu-item" ? "data-model-name" : "data-name";
        const datasetKey = className === "profile-model-menu-item" ? "modelName" : "name";
        const pattern = new RegExp(`class="[^"]*${{className}}[^"]*"[^>]*${{dataAttribute}}="([^"]+)"`, "g");
        const matches = [];
        let match = pattern.exec(source);
        while (match) {{
            matches.push({{
                dataset: {{ [datasetKey]: match[1] }},
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
        const entries = [
            ["profiles-list", createElement("block", "profiles-list")],
            ["profile-editor", createElement("none", "profile-editor")],
            ["add-profile-btn", createElement("block", "add-profile-btn")],
            ["save-profile-btn", createElement("block", "save-profile-btn")],
            ["test-profile-btn", createElement("block", "test-profile-btn")],
            ["fetch-profile-models-btn", createElement("block", "fetch-profile-models-btn")],
            ["open-profile-model-menu-btn", createElement("block", "open-profile-model-menu-btn")],
            ["cancel-profile-btn", createElement("block", "cancel-profile-btn")],
            ["profile-probe-status", createElement("none", "profile-probe-status")],
            ["profile-model-discovery-status", createElement("none", "profile-model-discovery-status")],
            ["profile-editor-title", createElement("block", "profile-editor-title")],
            ["profile-name", createElement("block", "profile-name")],
            ["profile-provider", createElement("block", "profile-provider")],
            ["profile-provider-options", createElement("block", "profile-provider-options")],
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
            ["profile-maas-username", createElement("block", "profile-maas-username")],
            ["profile-maas-password", createElement("block", "profile-maas-password")],
            ["toggle-profile-maas-password-btn", createElement("none", "toggle-profile-maas-password-btn")],
            ["profile-temperature", createElement("block", "profile-temperature")],
            ["profile-top-p", createElement("block", "profile-top-p")],
            ["profile-max-tokens", createElement("block", "profile-max-tokens")],
            ["profile-context-window", createElement("block", "profile-context-window")],
            ["profile-connect-timeout", createElement("block", "profile-connect-timeout")],
            ["profile-ssl-verify", createElement("block", "profile-ssl-verify")],
            ["profile-fallback-policy", createElement("block", "profile-fallback-policy")],
            ["profile-fallback-priority", createElement("block", "profile-fallback-priority")],
        ];
        const elements = new Map(entries);
        elements.get("profile-primary-credentials-row")?.appendChild(elements.get("profile-api-key-group"));
        elements.get("profile-primary-credentials-row")?.appendChild(elements.get("profile-model-group"));
        elements.get("profile-maas-auth-fields")?.appendChild(elements.get("profile-maas-model-slot"));
        return elements;
    }}

function installGlobals(elements, notifications) {{
    function collectDocumentMatches(selector) {{
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
    }};
    globalThis.__feedbackNotifications = notifications;
    globalThis.__feedbackConfirms = [];
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
