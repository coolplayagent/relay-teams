# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

from pydantic import JsonValue


def test_trigger_settings_renders_feishu_provider_and_expands_records(
    tmp_path: Path,
) -> None:
    payload = _run_trigger_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindTriggerSettingsHandlers, loadTriggerSettingsPanel } from "./triggerSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindTriggerSettingsHandlers();
await loadTriggerSettingsPanel();

const collapsedHtml = document.getElementById("trigger-platform-list").innerHTML;
await document.getElementById("trigger-platform-list").querySelectorAll(".trigger-platform-open-btn")[0].onclick({ stopPropagation() {} });

console.log(JSON.stringify({
    notifications,
    collapsedHtml,
    expandedHtml: document.getElementById("trigger-platform-list").innerHTML,
    platformDisplay: document.getElementById("trigger-platform-list").style.display,
    detailDisplay: document.getElementById("trigger-provider-detail-panel").style.display,
    addDisplay: document.getElementById("add-trigger-btn").style.display,
    saveDisplay: document.getElementById("save-trigger-btn").style.display,
    cancelDisplay: document.getElementById("cancel-trigger-btn").style.display,
}));
""".strip(),
    )

    assert payload["notifications"] == []
    collapsed_html = cast(str, payload["collapsedHtml"])
    expanded_html = cast(str, payload["expandedHtml"])
    assert "Feishu" in collapsed_html
    assert "1 robots" in collapsed_html
    assert "Credentials Missing" in collapsed_html
    assert "feishu_main" in expanded_html
    assert "Disable robot" in expanded_html
    assert "http_bridge" not in expanded_html
    assert payload["platformDisplay"] == "block"
    assert payload["detailDisplay"] == "none"
    assert payload["addDisplay"] == "inline-flex"
    assert payload["saveDisplay"] == "none"
    assert payload["cancelDisplay"] == "none"


def test_trigger_settings_opens_editor_as_separate_view(
    tmp_path: Path,
) -> None:
    payload = _run_trigger_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindTriggerSettingsHandlers, loadTriggerSettingsPanel } from "./triggerSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindTriggerSettingsHandlers();
await loadTriggerSettingsPanel();
await document.getElementById("trigger-platform-list").querySelectorAll(".trigger-platform-open-btn")[0].onclick({ stopPropagation() {} });
await document.getElementById("trigger-platform-list").querySelectorAll(".trigger-record-edit-btn")[0].onclick({ stopPropagation() {} });

console.log(JSON.stringify({
    platformDisplay: document.getElementById("trigger-platform-list").style.display,
    detailDisplay: document.getElementById("trigger-provider-detail-panel").style.display,
    detailHtml: document.getElementById("trigger-provider-detail").innerHTML,
    addDisplay: document.getElementById("add-trigger-btn").style.display,
    saveDisplay: document.getElementById("save-trigger-btn").style.display,
    cancelDisplay: document.getElementById("cancel-trigger-btn").style.display,
}));
""".strip(),
    )

    detail_html = cast(str, payload["detailHtml"])
    assert payload["platformDisplay"] == "none"
    assert payload["detailDisplay"] == "block"
    assert "Robot Editor" in detail_html
    assert "Display Name" not in detail_html
    assert payload["addDisplay"] == "none"
    assert payload["saveDisplay"] == "inline-flex"
    assert payload["cancelDisplay"] == "inline-flex"


def test_trigger_settings_adds_feishu_trigger_with_embedded_bot_config(
    tmp_path: Path,
) -> None:
    payload = _run_trigger_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindTriggerSettingsHandlers, loadTriggerSettingsPanel } from "./triggerSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);
globalThis.__triggerFixtures = [];

bindTriggerSettingsHandlers();
await loadTriggerSettingsPanel();
await document.getElementById("trigger-platform-list").querySelectorAll(".trigger-platform-open-btn")[0].onclick({ stopPropagation() {} });
await document.getElementById("add-trigger-btn").onclick();

document.getElementById("feishu-trigger-name-input").value = "feishu_ops";
document.getElementById("feishu-trigger-name-input").oninput();
document.getElementById("feishu-app-name-input").value = "Agent Teams Bot";
document.getElementById("feishu-app-name-input").oninput();
document.getElementById("feishu-app-id-input").value = "cli_demo";
document.getElementById("feishu-app-id-input").oninput();
document.getElementById("feishu-app-secret-input").value = "secret-demo";
document.getElementById("feishu-app-secret-input").oninput();
document.getElementById("feishu-trigger-workspace-id-input").value = "workspace-ops";
document.getElementById("feishu-trigger-workspace-id-input").onchange();
document.getElementById("feishu-session-mode-input").value = "orchestration";
document.getElementById("feishu-session-mode-input").onchange();
document.getElementById("feishu-orchestration-preset-id-input").value = "default";
document.getElementById("feishu-orchestration-preset-id-input").onchange();
document.getElementById("feishu-trigger-thinking-enabled-input").value = "true";
document.getElementById("feishu-trigger-thinking-enabled-input").onchange();
document.getElementById("feishu-thinking-effort-input").value = "high";
document.getElementById("feishu-thinking-effort-input").onchange();
document.getElementById("feishu-trigger-yolo-input").value = "false";
document.getElementById("feishu-trigger-yolo-input").onchange();

await document.getElementById("save-trigger-btn").onclick();

console.log(JSON.stringify({
    notifications,
    createCalls: globalThis.__createTriggerCalls,
    updateCalls: globalThis.__updateTriggerCalls,
    enableCalls: globalThis.__enableTriggerCalls,
    disableCalls: globalThis.__disableTriggerCalls,
    platformDisplay: document.getElementById("trigger-platform-list").style.display,
    saveDisplay: document.getElementById("save-trigger-btn").style.display,
}));
""".strip(),
    )

    notifications = cast(list[dict[str, JsonValue]], payload["notifications"])
    create_calls = cast(list[dict[str, JsonValue]], payload["createCalls"])
    assert payload["updateCalls"] == []
    assert payload["enableCalls"] == []
    assert payload["disableCalls"] == []
    assert len(create_calls) == 1
    create_payload = cast(dict[str, JsonValue], create_calls[0]["payload"])
    assert create_payload["name"] == "feishu_ops"
    assert create_payload["display_name"] is None
    assert create_payload["source_type"] == "im"
    assert create_payload["enabled"] is True
    assert create_payload["source_config"] == {
        "provider": "feishu",
        "trigger_rule": "mention_only",
        "app_id": "cli_demo",
        "app_name": "Agent Teams Bot",
    }
    assert create_payload["target_config"] == {
        "workspace_id": "workspace-ops",
        "session_mode": "orchestration",
        "orchestration_preset_id": "default",
        "yolo": False,
        "thinking": {"enabled": True, "effort": "high"},
    }
    assert create_payload["secret_config"] == {
        "app_secret": "secret-demo",
    }
    assert notifications == [
        {
            "title": "Robot Settings Saved",
            "message": "Feishu robot settings saved.",
            "tone": "success",
        }
    ]
    assert payload["platformDisplay"] == "block"
    assert payload["saveDisplay"] == "none"


def test_trigger_settings_app_secret_toggle_matches_model_style_behavior(
    tmp_path: Path,
) -> None:
    payload = _run_trigger_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindTriggerSettingsHandlers, loadTriggerSettingsPanel } from "./triggerSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindTriggerSettingsHandlers();
await loadTriggerSettingsPanel();
await document.getElementById("trigger-platform-list").querySelectorAll(".trigger-platform-open-btn")[0].onclick({ stopPropagation() {} });
await document.getElementById("trigger-platform-list").querySelectorAll(".trigger-record-edit-btn")[0].onclick({ stopPropagation() {} });

const input = document.getElementById("feishu-app-secret-input");
const toggle = document.getElementById("toggle-feishu-app-secret-btn");
const initial = {
    type: input.type,
    value: input.value,
    placeholder: input.placeholder,
    toggleDisplay: toggle.style.display,
    toggleClassName: toggle.className,
};

toggle.onclick();

const revealed = {
    type: input.type,
    value: input.value,
    placeholder: input.placeholder,
    toggleTitle: toggle.title,
    toggleClassName: toggle.className,
};

toggle.onclick();

console.log(JSON.stringify({
    initial,
    revealed,
    final: {
        type: input.type,
        value: input.value,
        placeholder: input.placeholder,
        toggleTitle: toggle.title,
        toggleClassName: toggle.className,
    }
}));
""".strip(),
    )

    initial = cast(dict[str, str], payload["initial"])
    revealed = cast(dict[str, str], payload["revealed"])
    final = cast(dict[str, str], payload["final"])

    assert initial["type"] == "password"
    assert initial["value"] == ""
    assert initial["placeholder"] == "************"
    assert initial["toggleDisplay"] == "inline-flex"
    assert initial["toggleClassName"] == "secure-input-btn"

    assert revealed["type"] == "text"
    assert revealed["value"] == "secret-demo"
    assert revealed["placeholder"] == ""
    assert revealed["toggleTitle"] == "Hide App Secret"
    assert revealed["toggleClassName"] == "secure-input-btn is-active"

    assert final["type"] == "password"
    assert final["value"] == ""
    assert final["placeholder"] == "************"
    assert final["toggleTitle"] == "Show App Secret"
    assert final["toggleClassName"] == "secure-input-btn"


def test_trigger_settings_updates_existing_trigger_without_create_only_fields(
    tmp_path: Path,
) -> None:
    payload = _run_trigger_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindTriggerSettingsHandlers, loadTriggerSettingsPanel } from "./triggerSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindTriggerSettingsHandlers();
await loadTriggerSettingsPanel();
await document.getElementById("trigger-platform-list").querySelectorAll(".trigger-platform-open-btn")[0].onclick({ stopPropagation() {} });
await document.getElementById("trigger-platform-list").querySelectorAll(".trigger-record-edit-btn")[0].onclick({ stopPropagation() {} });

document.getElementById("feishu-trigger-name-input").value = "feishu_main";
document.getElementById("feishu-trigger-name-input").oninput();
document.getElementById("feishu-app-id-input").value = "cli_existing";
document.getElementById("feishu-app-id-input").oninput();
document.getElementById("feishu-app-name-input").value = "Agent Teams Bot";
document.getElementById("feishu-app-name-input").oninput();
document.getElementById("feishu-trigger-workspace-id-input").value = "default";
document.getElementById("feishu-trigger-workspace-id-input").onchange();
document.getElementById("feishu-session-mode-input").value = "normal";
document.getElementById("feishu-session-mode-input").onchange();
document.getElementById("feishu-normal-root-role-id-input").value = "SpecCoder";
document.getElementById("feishu-normal-root-role-id-input").onchange();

await document.getElementById("save-trigger-btn").onclick();

console.log(JSON.stringify({
    updateCalls: globalThis.__updateTriggerCalls,
    enableCalls: globalThis.__enableTriggerCalls,
    disableCalls: globalThis.__disableTriggerCalls,
}));
""".strip(),
    )

    update_calls = cast(list[dict[str, JsonValue]], payload["updateCalls"])
    assert len(update_calls) == 1
    update_payload = cast(dict[str, JsonValue], update_calls[0]["payload"])
    assert "source_type" not in update_payload
    assert "enabled" not in update_payload
    assert update_payload["display_name"] is None
    assert update_payload["target_config"] == {
        "workspace_id": "default",
        "session_mode": "normal",
        "normal_root_role_id": "SpecCoder",
        "yolo": True,
        "thinking": {"enabled": False, "effort": None},
    }
    assert payload["enableCalls"] == []
    assert payload["disableCalls"] == []


def test_trigger_settings_toggles_trigger_from_record_list(
    tmp_path: Path,
) -> None:
    payload = _run_trigger_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindTriggerSettingsHandlers, loadTriggerSettingsPanel } from "./triggerSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindTriggerSettingsHandlers();
await loadTriggerSettingsPanel();
await document.getElementById("trigger-platform-list").querySelectorAll(".trigger-platform-open-btn")[0].onclick({ stopPropagation() {} });
await document.getElementById("trigger-platform-list").querySelectorAll(".trigger-record-toggle-btn")[0].onclick({ stopPropagation() {} });

console.log(JSON.stringify({
    enableCalls: globalThis.__enableTriggerCalls,
    disableCalls: globalThis.__disableTriggerCalls,
}));
""".strip(),
    )

    assert payload["enableCalls"] == []
    assert payload["disableCalls"] == ["trigger-feishu-1"]


def test_trigger_settings_deletes_trigger_from_record_list(
    tmp_path: Path,
) -> None:
    payload = _run_trigger_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindTriggerSettingsHandlers, loadTriggerSettingsPanel } from "./triggerSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindTriggerSettingsHandlers();
await loadTriggerSettingsPanel();
await document.getElementById("trigger-platform-list").querySelectorAll(".trigger-platform-open-btn")[0].onclick({ stopPropagation() {} });
await document.getElementById("trigger-platform-list").querySelectorAll(".trigger-record-delete-btn")[0].onclick({ stopPropagation() {} });

console.log(JSON.stringify({
    deleteCalls: globalThis.__deleteTriggerCalls,
    notifications,
}));
""".strip(),
    )

    assert payload["deleteCalls"] == ["trigger-feishu-1"]
    assert payload["notifications"] == [
        {
            "title": "Robot Deleted",
            "message": "Feishu robot deleted.",
            "tone": "success",
        }
    ]


def test_trigger_settings_renders_wechat_gateway_and_starts_qr_login(
    tmp_path: Path,
) -> None:
    payload = _run_trigger_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindTriggerSettingsHandlers, loadTriggerSettingsPanel } from "./triggerSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindTriggerSettingsHandlers();
await loadTriggerSettingsPanel();
await document.getElementById("trigger-platform-list").querySelectorAll(".trigger-platform-open-btn")[1].onclick({ stopPropagation() {} });
await document.getElementById("trigger-platform-list").querySelectorAll(".gateway-wechat-connect-btn")[0].onclick({ stopPropagation() {} });
await Promise.resolve();
await Promise.resolve();

console.log(JSON.stringify({
    html: document.getElementById("trigger-platform-list").innerHTML,
    notifications,
    startCalls: globalThis.__startWeChatLoginCalls.length,
    waitCalls: globalThis.__waitWeChatLoginCalls,
}));
""".strip(),
    )

    html = cast(str, payload["html"])
    wait_calls = cast(list[dict[str, JsonValue]], payload["waitCalls"])
    assert "WeChat" in html
    assert "wx-account-1" in html
    assert payload["startCalls"] == 1
    assert len(wait_calls) == 1
    assert wait_calls[0]["session_key"] == "wechat-login-1"
    assert payload["notifications"] == [
        {
            "title": "WeChat account connected.",
            "message": "WeChat account connected.",
            "tone": "success",
        }
    ]


def test_trigger_settings_updates_wechat_account_inline(
    tmp_path: Path,
) -> None:
    payload = _run_trigger_settings_script(
        tmp_path=tmp_path,
        runner_source="""
import { bindTriggerSettingsHandlers, loadTriggerSettingsPanel } from "./triggerSettings.mjs";

const notifications = [];
const elements = createElements();
installGlobals(elements, notifications);

bindTriggerSettingsHandlers();
await loadTriggerSettingsPanel();
await document.getElementById("trigger-platform-list").querySelectorAll(".trigger-platform-open-btn")[1].onclick({ stopPropagation() {} });
await document.getElementById("trigger-platform-list").querySelectorAll(".gateway-wechat-edit-btn")[0].onclick({ stopPropagation() {} });

document.getElementById("wechat-display-name-input").value = "Ops WeChat";
document.getElementById("wechat-display-name-input").oninput();
document.getElementById("wechat-workspace-id-input").value = "workspace-ops";
document.getElementById("wechat-workspace-id-input").onchange();
document.getElementById("wechat-session-mode-input").value = "orchestration";
document.getElementById("wechat-session-mode-input").onchange();
document.getElementById("wechat-orchestration-preset-id-input").value = "ops";
document.getElementById("wechat-orchestration-preset-id-input").onchange();
document.getElementById("wechat-thinking-enabled-input").value = "true";
document.getElementById("wechat-thinking-enabled-input").onchange();
document.getElementById("wechat-thinking-effort-input").value = "high";
document.getElementById("wechat-thinking-effort-input").onchange();
document.getElementById("wechat-yolo-input").value = "false";
document.getElementById("wechat-yolo-input").onchange();

await document.getElementById("save-wechat-account-btn").onclick();

console.log(JSON.stringify({
    notifications,
    updateCalls: globalThis.__updateWeChatAccountCalls,
}));
""".strip(),
    )

    update_calls = cast(list[dict[str, JsonValue]], payload["updateCalls"])
    assert len(update_calls) == 1
    assert update_calls[0]["accountId"] == "wx-account-1"
    assert update_calls[0]["payload"] == {
        "display_name": "Ops WeChat",
        "base_url": "https://wechat.example.test",
        "cdn_base_url": "https://cdn.example.test",
        "route_tag": "route-a",
        "workspace_id": "workspace-ops",
        "session_mode": "orchestration",
        "yolo": False,
        "thinking": {"enabled": True, "effort": "high"},
        "normal_root_role_id": None,
        "orchestration_preset_id": "ops",
    }
    assert payload["notifications"] == [
        {
            "title": "WeChat Account Saved",
            "message": "WeChat account settings saved.",
            "tone": "success",
        }
    ]


def _run_trigger_settings_script(
    tmp_path: Path,
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
        / "triggerSettings.js"
    )

    mock_api_path = tmp_path / "mockApi.mjs"
    mock_feedback_path = tmp_path / "mockFeedback.mjs"
    mock_i18n_path = tmp_path / "mockI18n.mjs"
    mock_logger_path = tmp_path / "mockLogger.mjs"
    module_under_test_path = tmp_path / "triggerSettings.mjs"
    runner_path = tmp_path / "runner.mjs"

    mock_api_path.write_text(
        """
export async function fetchTriggers() {
    return globalThis.__triggerFixtures;
}

export async function fetchWeChatGatewayAccounts() {
    return globalThis.__wechatAccountFixtures;
}

export async function fetchWorkspaces() {
    return globalThis.__workspaceFixtures;
}

export async function fetchRoleConfigOptions() {
    return globalThis.__roleOptionsFixture;
}

export async function fetchOrchestrationConfig() {
    return globalThis.__orchestrationFixture;
}

export async function createTrigger(payload) {
    globalThis.__createTriggerCalls.push({ payload });
    return { trigger_id: "trigger-created", ...payload };
}

export async function updateTrigger(triggerId, payload) {
    globalThis.__updateTriggerCalls.push({ triggerId, payload });
    return { trigger_id: triggerId, ...payload };
}

export async function deleteTrigger(triggerId) {
    globalThis.__deleteTriggerCalls.push(triggerId);
    return { status: "ok" };
}

export async function enableTrigger(triggerId) {
    globalThis.__enableTriggerCalls.push(triggerId);
    return { status: "enabled" };
}

export async function disableTrigger(triggerId) {
    globalThis.__disableTriggerCalls.push(triggerId);
    return { status: "disabled" };
}

export async function startWeChatGatewayLogin() {
    globalThis.__startWeChatLoginCalls.push(true);
    return {
        session_key: "wechat-login-1",
        qr_code_url: "https://example.test/wechat-qr.png",
    };
}

export async function waitWeChatGatewayLogin(payload) {
    globalThis.__waitWeChatLoginCalls.push(payload);
    return {
        connected: true,
        account_id: "wx-account-new",
        message: "WeChat account connected.",
    };
}

export async function updateWeChatGatewayAccount(accountId, payload) {
    globalThis.__updateWeChatAccountCalls.push({ accountId, payload });
    return { account_id: accountId, ...payload };
}

export async function enableWeChatGatewayAccount(accountId) {
    globalThis.__enableWeChatAccountCalls.push(accountId);
    return { status: "enabled" };
}

export async function disableWeChatGatewayAccount(accountId) {
    globalThis.__disableWeChatAccountCalls.push(accountId);
    return { status: "disabled" };
}

export async function deleteWeChatGatewayAccount(accountId) {
    globalThis.__deleteWeChatAccountCalls.push(accountId);
    return { status: "ok" };
}
""".strip(),
        encoding="utf-8",
    )
    mock_feedback_path.write_text(
        """
export async function showConfirmDialog() {
    return true;
}

export function showToast(payload) {
    globalThis.__feedbackNotifications.push(payload);
}
""".strip(),
        encoding="utf-8",
    )
    mock_i18n_path.write_text(
        """
const translations = {
    "settings.triggers.feishu": "Feishu",
    "settings.triggers.configure": "Configure",
    "settings.triggers.collapse": "Collapse",
    "settings.triggers.ready": "Ready",
    "settings.triggers.credentials_missing": "Credentials Missing",
    "settings.triggers.credentials_ready": "Credentials ready",
    "settings.triggers.credentials_missing_count": "{count} credentials missing",
    "settings.triggers.trigger_count": "{count} robots",
    "settings.triggers.enabled_count": "{count} enabled",
    "settings.triggers.none": "No Feishu robots",
    "settings.triggers.none_copy": "Add a Feishu robot.",
    "settings.triggers.editor": "Robot Editor",
    "settings.triggers.bot_configuration": "Bot Configuration",
    "settings.triggers.session_configuration": "Session Configuration",
    "settings.triggers.trigger_name": "Robot Name",
    "settings.triggers.workspace": "Workspace",
    "settings.triggers.rule": "Trigger Rule",
    "settings.triggers.mode": "Session Mode",
    "settings.triggers.normal_root_role_id": "Normal Root Role",
    "settings.triggers.orchestration_preset_id": "Orchestration Preset",
    "settings.triggers.thinking_effort": "Thinking Effort",
    "settings.triggers.yolo": "YOLO",
    "settings.triggers.thinking_enabled": "Thinking Enabled",
    "settings.triggers.enable_trigger": "Enable robot",
    "settings.triggers.disable_trigger": "Disable robot",
    "settings.triggers.saved": "Robot Settings Saved",
    "settings.triggers.saved_message": "Feishu robot settings saved.",
    "settings.triggers.save_failed": "Save Failed",
    "settings.triggers.delete_trigger": "Delete robot",
    "settings.triggers.delete_confirm_title": "Delete robot",
    "settings.triggers.delete_confirm_message": "Delete robot {name}?",
    "settings.triggers.deleted": "Robot Deleted",
    "settings.triggers.deleted_message": "Feishu robot deleted.",
    "settings.triggers.delete_failed": "Delete Failed",
    "settings.triggers.load_failed": "Load Failed",
    "settings.triggers.missing_name": "Robot name is required.",
    "settings.triggers.missing_workspace": "Workspace is required.",
    "settings.triggers.missing_app_id": "App ID is required.",
    "settings.triggers.missing_app_name": "App name is required.",
    "settings.triggers.missing_app_secret": "App secret is required.",
    "settings.triggers.missing_orchestration_preset_id": "Preset is required in orchestration mode.",
    "settings.triggers.unnamed": "Unnamed robot",
    "settings.triggers.feishu_app_id": "Feishu App ID",
    "settings.triggers.feishu_app_id_placeholder": "cli_xxx",
    "settings.triggers.feishu_app_name": "Feishu App Name",
    "settings.triggers.feishu_app_name_placeholder": "Agent Teams Bot",
    "settings.triggers.feishu_app_secret": "Feishu App Secret",
    "settings.triggers.feishu_app_secret_placeholder": "App secret",
    "settings.triggers.secret_keep_placeholder": "Configured. Leave blank to keep current value.",
    "settings.triggers.no_workspaces": "No workspaces",
    "settings.triggers.option_enabled": "Enabled",
    "settings.triggers.option_disabled": "Disabled",
    "settings.gateway.wechat": "WeChat",
    "settings.gateway.gateway_count": "{count} accounts",
    "settings.gateway.running_count": "{count} running",
    "settings.gateway.wechat_none": "No WeChat accounts",
    "settings.gateway.wechat_none_copy": "Connect a WeChat account by scanning the QR code.",
    "settings.gateway.connect_wechat": "Connect WeChat",
    "settings.gateway.connecting": "Connecting",
    "settings.gateway.qr_title": "Scan To Connect",
    "settings.gateway.qr_copy": "Scan this QR code in WeChat. The account will appear here after login succeeds.",
    "settings.gateway.login_waiting": "Waiting for QR scan confirmation...",
    "settings.gateway.login_success": "WeChat account connected.",
    "settings.gateway.login_failed": "WeChat login failed.",
    "settings.gateway.account_editor": "WeChat Account",
    "settings.gateway.account_id": "Account ID",
    "settings.gateway.display_name": "Display Name",
    "settings.gateway.base_url": "Base URL",
    "settings.gateway.cdn_base_url": "CDN Base URL",
    "settings.gateway.route_tag": "Route Tag",
    "settings.gateway.status_running": "Running",
    "settings.gateway.last_error": "Last Error",
    "settings.gateway.enable_account": "Enable account",
    "settings.gateway.disable_account": "Disable account",
    "settings.gateway.delete_account": "Delete account",
    "settings.gateway.delete_confirm_title": "Delete account",
    "settings.gateway.delete_confirm_message": "Delete account {name}?",
    "settings.gateway.saved": "WeChat Account Saved",
    "settings.gateway.saved_message": "WeChat account settings saved.",
    "settings.gateway.deleted": "WeChat Account Deleted",
    "settings.gateway.deleted_message": "WeChat account deleted.",
    "settings.gateway.save_failed": "Save Failed",
    "settings.gateway.delete_failed": "Delete Failed",
    "settings.gateway.missing_display_name": "Display name is required.",
    "settings.gateway.missing_workspace": "Workspace is required.",
    "settings.gateway.missing_orchestration_preset_id": "Preset is required in orchestration mode.",
    "composer.mode_normal": "Normal Mode",
    "composer.mode_orchestration": "Orchestrated Mode",
    "composer.no_roles": "No roles",
    "composer.no_presets": "No presets",
    "settings.field.enabled": "Enabled",
    "settings.roles.disabled": "Disabled",
    "settings.roles.edit": "Edit",
    "settings.action.cancel": "Cancel"
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
    const element = {{
        style: {{ display: initialDisplay }},
        value: "",
        textContent: "",
        innerText: "",
        onclick: null,
        oninput: null,
        onchange: null,
        dataset: {{}},
        type: "text",
        tagName: "INPUT",
        __selectorCache: new Map(),
    }};

    element.classList = {{
        add() {{
            return undefined;
        }},
        remove() {{
            return undefined;
        }},
    }};

    Object.defineProperty(element, "innerHTML", {{
        get() {{
            return this.__html || "";
        }},
        set(value) {{
            this.__html = String(value || "");
            this.__selectorCache = new Map();
        }},
    }});

    element.querySelectorAll = selector => {{
        if (!element.__selectorCache.has(selector)) {{
            element.__selectorCache.set(selector, parseSelector(element.innerHTML, selector));
        }}
        return element.__selectorCache.get(selector);
    }};
    return element;
}}

function parseSelector(html, selector) {{
    const configs = {{
        ".trigger-platform-open-btn": /class="[^"]*trigger-platform-open-btn[^"]*"[^>]*data-trigger-platform="([^"]+)"/g,
        ".trigger-platform-record": /class="[^"]*trigger-platform-record[^"]*"[^>]*data-trigger-platform="([^"]+)"/g,
        ".trigger-record": /class="[^"]*trigger-record[^"]*"[^>]*data-trigger-id="([^"]+)"/g,
        ".trigger-record-edit-btn": /class="[^"]*trigger-record-edit-btn[^"]*"[^>]*data-trigger-id="([^"]+)"/g,
        ".trigger-record-toggle-btn": /class="[^"]*trigger-record-toggle-btn[^"]*"[^>]*data-trigger-id="([^"]+)"/g,
        ".trigger-record-delete-btn": /class="[^"]*trigger-record-delete-btn[^"]*"[^>]*data-trigger-id="([^"]+)"/g,
        ".gateway-wechat-connect-btn": /class="[^"]*gateway-wechat-connect-btn[^"]*"/g,
        ".gateway-wechat-edit-btn": /class="[^"]*gateway-wechat-edit-btn[^"]*"[^>]*data-account-id="([^"]+)"/g,
        ".gateway-wechat-toggle-btn": /class="[^"]*gateway-wechat-toggle-btn[^"]*"[^>]*data-account-id="([^"]+)"/g,
        ".gateway-wechat-delete-btn": /class="[^"]*gateway-wechat-delete-btn[^"]*"[^>]*data-account-id="([^"]+)"/g,
    }};
    const config = configs[selector];
    if (!config) {{
        return [];
    }}
    const matches = [];
    for (const match of html.matchAll(config)) {{
        const child = createElement();
        if (selector === ".trigger-platform-open-btn" || selector === ".trigger-platform-record") {{
            child.dataset.triggerPlatform = match[1];
        }} else if (selector === ".gateway-wechat-connect-btn") {{
            child.dataset.accountId = "";
        }} else {{
            if (selector.startsWith(".gateway-wechat-")) {{
                child.dataset.accountId = match[1];
            }} else {{
                child.dataset.triggerId = match[1];
            }}
        }}
        matches.push(child);
    }}
    return matches;
}}

function createElements() {{
    const elements = new Map();
    [
        "settings-actions-bar",
        "add-trigger-btn",
        "save-trigger-btn",
        "cancel-trigger-btn",
        "trigger-platform-list",
        "trigger-provider-detail-panel",
        "trigger-provider-detail",
        "trigger-editor-status",
        "feishu-app-id-input",
        "feishu-app-name-input",
        "feishu-app-secret-input",
        "toggle-feishu-app-secret-btn",
        "feishu-trigger-name-input",
        "feishu-normal-role-field",
        "feishu-preset-field",
        "feishu-thinking-effort-field",
        "save-wechat-account-btn",
        "cancel-wechat-account-btn",
        "wechat-display-name-input",
        "wechat-base-url-input",
        "wechat-cdn-base-url-input",
        "wechat-route-tag-input",
        "wechat-normal-role-field",
        "wechat-preset-field",
        "wechat-thinking-effort-field",
    ].forEach(id => elements.set(id, createElement(id === "settings-actions-bar" ? "flex" : "none")));

    [
        "feishu-trigger-rule-input",
        "feishu-session-mode-input",
        "feishu-thinking-effort-input",
        "feishu-trigger-workspace-id-input",
        "feishu-normal-root-role-id-input",
        "feishu-orchestration-preset-id-input",
        "feishu-trigger-yolo-input",
        "feishu-trigger-thinking-enabled-input",
        "wechat-workspace-id-input",
        "wechat-session-mode-input",
        "wechat-normal-root-role-id-input",
        "wechat-orchestration-preset-id-input",
        "wechat-yolo-input",
        "wechat-thinking-enabled-input",
        "wechat-thinking-effort-input",
    ].forEach(id => {{
        const element = elements.get(id) || createElement("none");
        element.tagName = "SELECT";
        elements.set(id, element);
    }});

    elements.get("trigger-platform-list").style.display = "block";
    return elements;
}}

function installGlobals(elements, notifications) {{
    globalThis.__feedbackNotifications = notifications;
    globalThis.__createTriggerCalls = [];
    globalThis.__deleteTriggerCalls = [];
    globalThis.__updateTriggerCalls = [];
    globalThis.__enableTriggerCalls = [];
    globalThis.__disableTriggerCalls = [];
    globalThis.__startWeChatLoginCalls = [];
    globalThis.__waitWeChatLoginCalls = [];
    globalThis.__updateWeChatAccountCalls = [];
    globalThis.__enableWeChatAccountCalls = [];
    globalThis.__disableWeChatAccountCalls = [];
    globalThis.__deleteWeChatAccountCalls = [];
    globalThis.__workspaceFixtures = [
        {{
            workspace_id: "default",
            root_path: "/work/default"
        }},
        {{
            workspace_id: "workspace-ops",
            root_path: "/work/ops"
        }}
    ];
    globalThis.__roleOptionsFixture = {{
        normal_mode_roles: [
            {{ role_id: "MainAgent", name: "Main Agent" }},
            {{ role_id: "SpecCoder", name: "Spec Coder" }}
        ]
    }};
    globalThis.__orchestrationFixture = {{
        default_orchestration_preset_id: "default",
        presets: [
            {{ preset_id: "default", name: "Default Preset" }},
            {{ preset_id: "ops", name: "Ops Preset" }}
        ]
    }};
    globalThis.__triggerFixtures = [
        {{
            trigger_id: "trigger-feishu-1",
            name: "feishu_main",
            display_name: "Feishu Main",
            source_type: "im",
            status: "enabled",
            source_config: {{
                provider: "feishu",
                trigger_rule: "mention_only",
                app_id: "cli_existing",
                app_name: "Agent Teams Bot"
            }},
            secret_config: {{
                app_secret: "secret-demo"
            }},
            target_config: {{
                workspace_id: "default",
                session_mode: "normal",
                normal_root_role_id: "MainAgent",
                yolo: true,
                thinking: {{ enabled: false, effort: "medium" }}
            }},
            secret_status: {{
                app_secret_configured: false
            }}
        }},
        {{
            trigger_id: "trigger-webhook-1",
            name: "http_bridge",
            display_name: "HTTP Bridge",
            source_type: "webhook",
            status: "enabled",
            source_config: {{
                provider: "custom"
            }},
            target_config: {{
                workspace_id: "default"
            }}
        }}
    ];
    globalThis.__wechatAccountFixtures = [
        {{
            account_id: "wx-account-1",
            display_name: "WeChat Main",
            base_url: "https://wechat.example.test",
            cdn_base_url: "https://cdn.example.test",
            route_tag: "route-a",
            status: "enabled",
            workspace_id: "default",
            session_mode: "normal",
            normal_root_role_id: "MainAgent",
            orchestration_preset_id: "",
            yolo: true,
            thinking: {{ enabled: false, effort: null }},
            running: true,
            last_error: "",
        }}
    ];
    globalThis.document = {{
        getElementById(id) {{
            return elements.get(id) || null;
        }},
        addEventListener() {{
            return undefined;
        }},
    }};
    globalThis.window = {{
        location: {{
            origin: "https://example.test",
        }},
    }};
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
