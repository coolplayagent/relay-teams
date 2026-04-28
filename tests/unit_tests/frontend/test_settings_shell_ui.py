# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

import json
from pathlib import Path
import subprocess
from typing import cast

from .css_helpers import load_components_css


def test_settings_modal_uses_flat_content_stacks_and_switches_tabs(
    tmp_path: Path,
) -> None:
    payload = _run_settings_script(
        tmp_path=tmp_path,
        runner_source="""
const { initSettings, openSettings } = await import("./index.mjs");

initSettings();
openSettings();

const tabs = document.querySelectorAll(".settings-tab");
const notificationsTab = tabs.find(tab => tab.dataset.tab === "notifications");
await notificationsTab.onclick();

console.log(JSON.stringify({
    modalClassName: document.getElementById("settings-modal").className,
    modalDisplay: document.getElementById("settings-modal").style.display,
    modalHtml: document.getElementById("settings-modal").innerHTML,
    panelTitle: document.getElementById("settings-panel-title").textContent,
    modelPanelDisplay: document.getElementById("model-panel").style.display,
    notificationsPanelDisplay: document.getElementById("notifications-panel").style.display,
    loadCalls: globalThis.__loadCalls,
}));
""".strip(),
    )

    modal_html = cast(str, payload["modalHtml"])
    load_calls = cast(dict[str, JsonValue], payload["loadCalls"])
    assert "settings-content-frame" not in modal_html
    assert "settings-content-stack" in modal_html
    assert "settings-model-stack" in modal_html
    assert "status-stack" in modal_html
    assert "settings-actions-bar" in modal_html
    assert "Proxy Settings" in modal_html
    assert "Connectivity Test" in modal_html
    assert 'class="proxy-editor-form"' in modal_html
    assert 'class="profile-editor proxy-editor-shell"' not in modal_html
    assert "settings-tab-desc" not in modal_html
    assert (
        "Runtime configuration for models, notifications, and extensions."
        not in modal_html
    )
    assert "Roles" in modal_html
    assert "Web" in modal_html
    assert "Proxy" in modal_html
    assert "Providers, endpoints, sampling" not in modal_html
    assert "Browser and toast delivery rules" not in modal_html
    assert "Loaded servers and reload actions" not in modal_html
    assert "Registry state and refresh" not in modal_html
    assert "Each profile is saved server-side" not in modal_html
    assert (
        "Shows the server names currently loaded into the runtime registry."
        not in modal_html
    )
    assert (
        "Lists the skills discovered by the runtime and lets you reload the registry."
        not in modal_html
    )
    assert "notifications-actions" not in modal_html
    assert 'id="web-provider-site-link"' in modal_html
    assert 'class="web-provider-link-card"' in modal_html
    assert payload["modalDisplay"] == "flex"
    assert "settings-modal-visible" in str(payload["modalClassName"])
    assert payload["panelTitle"] == "Notifications"
    assert payload["modelPanelDisplay"] == "none"
    assert payload["notificationsPanelDisplay"] == "block"
    assert load_calls["notifications"] == 1
    assert load_calls["model"] == 0
    assert load_calls["agents"] == 0


def test_settings_panel_actions_use_primary_buttons_for_add_and_reload(
    tmp_path: Path,
) -> None:
    payload = _run_settings_script(
        tmp_path=tmp_path,
        runner_source="""
const { initSettings, openSettings } = await import("./index.mjs");

initSettings();
openSettings();

const tabs = document.querySelectorAll(".settings-tab");
const rolesTab = tabs.find(tab => tab.dataset.tab === "roles");
const agentsTab = tabs.find(tab => tab.dataset.tab === "agents");
const notificationsTab = tabs.find(tab => tab.dataset.tab === "notifications");
const webTab = tabs.find(tab => tab.dataset.tab === "web");
const proxyTab = tabs.find(tab => tab.dataset.tab === "proxy");
const workspaceTab = tabs.find(tab => tab.dataset.tab === "workspace");
const mcpTab = tabs.find(tab => tab.dataset.tab === "mcp");

const modelTab = tabs.find(tab => tab.dataset.tab === "model");
await modelTab.onclick();
const modelAddDisplay = document.getElementById("add-profile-btn").style.display;
await agentsTab.onclick();
const agentAddDisplay = document.getElementById("add-agent-btn").style.display;
await rolesTab.onclick();
const roleAddDisplay = document.getElementById("add-role-btn").style.display;
await notificationsTab.onclick();
const notificationsSaveDisplay = document.getElementById("save-notifications-btn").style.display;
await webTab.onclick();
const webSaveDisplay = document.getElementById("save-web-btn").style.display;
await proxyTab.onclick();
const proxySaveDisplay = document.getElementById("save-proxy-btn").style.display;
await workspaceTab.onclick();
const workspaceAddDisplay = document.getElementById("add-ssh-profile-btn").style.display;
await mcpTab.onclick();
const mcpReloadDisplay = document.getElementById("reload-mcp-btn").style.display;
const hasGitHubSaveButton = Boolean(document.getElementById("save-github-btn"));

console.log(JSON.stringify({
    modelAddDisplay,
    agentAddDisplay,
    roleAddDisplay,
    notificationsSaveDisplay,
    webSaveDisplay,
    proxySaveDisplay,
    workspaceAddDisplay,
    mcpReloadDisplay,
    hasGitHubSaveButton,
}));
""".strip(),
    )

    assert payload["modelAddDisplay"] == "inline-flex"
    assert payload["agentAddDisplay"] == "inline-flex"
    assert payload["roleAddDisplay"] == "inline-flex"
    assert payload["notificationsSaveDisplay"] == "inline-flex"
    assert payload["webSaveDisplay"] == "inline-flex"
    assert payload["proxySaveDisplay"] == "inline-flex"
    assert payload["workspaceAddDisplay"] == "inline-flex"
    assert payload["mcpReloadDisplay"] == "inline-flex"
    assert payload["hasGitHubSaveButton"] is False


def test_hooks_settings_tab_shows_add_validate_and_save_actions(
    tmp_path: Path,
) -> None:
    payload = _run_settings_script(
        tmp_path=tmp_path,
        runner_source="""
const { initSettings, openSettings } = await import("./index.mjs");

initSettings();
openSettings("hooks");

console.log(JSON.stringify({
    panelTitle: document.getElementById("settings-panel-title").textContent,
    hooksPanelDisplay: document.getElementById("hooks-panel").style.display,
    addHookDisplay: document.getElementById("add-hook-btn").style.display,
    validateHooksDisplay: document.getElementById("validate-hooks-btn").style.display,
    saveHooksDisplay: document.getElementById("save-hooks-btn").style.display,
    loadCalls: globalThis.__loadCalls,
}));
""".strip(),
    )

    load_calls = cast(dict[str, JsonValue], payload["loadCalls"])
    assert payload["panelTitle"] == "Hooks"
    assert payload["hooksPanelDisplay"] == "block"
    assert payload["addHookDisplay"] == "inline-flex"
    assert payload["validateHooksDisplay"] == "inline-flex"
    assert payload["saveHooksDisplay"] == "inline-flex"
    assert load_calls["hooks"] == 1


def test_settings_tab_order_and_labels_are_simplified() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_text = (
        repo_root / "frontend" / "dist" / "js" / "components" / "settings" / "index.js"
    ).read_text(encoding="utf-8")

    tabs_start = source_text.index('<div class="settings-tabs"')
    tabs_end = source_text.index("</div>\n            </aside>", tabs_start)
    tabs_html = source_text[tabs_start:tabs_end]

    assert tabs_html.index('data-tab="appearance"') < tabs_html.index(
        'data-tab="model"'
    )
    assert tabs_html.index('data-tab="model"') < tabs_html.index('data-tab="mcp"')
    assert tabs_html.index('data-tab="mcp"') < tabs_html.index('data-tab="commands"')
    assert tabs_html.index('data-tab="commands"') < tabs_html.index('data-tab="hooks"')
    assert tabs_html.index('data-tab="hooks"') < tabs_html.index('data-tab="agents"')
    assert tabs_html.index('data-tab="agents"') < tabs_html.index('data-tab="roles"')
    assert tabs_html.index('data-tab="roles"') < tabs_html.index(
        'data-tab="orchestration"'
    )
    assert tabs_html.index('data-tab="orchestration"') < tabs_html.index(
        'data-tab="notifications"'
    )
    assert tabs_html.index('data-tab="notifications"') < tabs_html.index(
        'data-tab="web"'
    )
    assert tabs_html.index('data-tab="web"') < tabs_html.index('data-tab="proxy"')
    assert tabs_html.index('data-tab="proxy"') < tabs_html.index('data-tab="workspace"')
    assert tabs_html.index('data-tab="workspace"') < tabs_html.index(
        'data-tab="environment"'
    )
    assert ">Model</span>" in tabs_html
    assert ">MCP</span>" in tabs_html
    assert ">Commands</span>" in tabs_html
    assert ">Hooks</span>" in tabs_html
    assert ">Agents</span>" in tabs_html
    assert ">Web</span>" in tabs_html
    assert ">Remote Workspace</span>" in tabs_html
    assert ">Environment</span>" in tabs_html
    assert ">GitHub</span>" not in tabs_html
    assert ">Skills</span>" not in tabs_html
    assert ">Gateway</span>" not in tabs_html
    assert ">Model Profiles</span>" not in tabs_html
    assert ">MCP Config</span>" not in tabs_html


def test_settings_content_stack_does_not_draw_duplicate_top_divider() -> None:
    components_css = load_components_css()
    start = components_css.index(".settings-content-stack {")
    end = components_css.index(".settings-model-stack {", start)
    stack_rule = components_css[start:end]

    assert ".settings-content-stack {" in stack_rule
    assert "border-top: 1px solid var(--settings-divider);" not in stack_rule


def test_settings_active_tab_uses_surface_background_and_primary_accent() -> None:
    components_css = load_components_css()

    active_start = components_css.index(".settings-tab.active {")
    active_end = components_css.index(".settings-tab-label {", active_start)
    active_rule = components_css[active_start:active_end]

    assert "background: var(--settings-surface-bg);" in active_rule
    assert "border-left-color: var(--primary);" in active_rule
    assert "box-shadow: inset 0 0 0 1px var(--settings-border-soft);" in active_rule


def test_settings_hover_tab_keeps_visible_feedback() -> None:
    components_css = load_components_css()

    hover_start = components_css.index(".settings-tab:hover {")
    hover_end = components_css.index(".settings-tab.active {", hover_start)
    hover_rule = components_css[hover_start:hover_end]

    assert "background: var(--settings-row-hover-bg);" in hover_rule
    assert "border-left-color: var(--settings-border-default);" in hover_rule
    assert "box-shadow: inset 0 0 0 1px var(--settings-border-soft);" in hover_rule


def test_settings_layout_uses_scrolling_body_with_footer_actions() -> None:
    components_css = load_components_css()

    assert ".settings-main {" in components_css
    assert "overflow: hidden;" in components_css
    assert ".settings-modal-content {" in components_css
    assert "width: min(1240px, 96vw);" in components_css
    assert "height: min(90vh, 960px);" in components_css
    assert "min-height: 760px;" in components_css
    assert ".settings-body {" in components_css
    assert "overflow-y: auto;" in components_css
    assert ".settings-actions-bar {" in components_css
    assert ".settings-panel {" in components_css
    assert "height: 100%;" in components_css
    assert ".settings-action {" in components_css
    assert ".settings-panel-actions-group {" in components_css
    assert ".settings-panel-actions-group-end {" in components_css
    assert ".settings-sidebar::-webkit-scrollbar {" in components_css
    assert ".settings-body::-webkit-scrollbar {" in components_css
    assert ".profiles-list::-webkit-scrollbar {" in components_css


def test_model_profile_editor_labels_max_output_tokens_and_uses_short_footer_labels(
    tmp_path: Path,
) -> None:
    payload = _run_settings_script(
        tmp_path=tmp_path,
        runner_source="""
const { initSettings, openSettings } = await import("./index.mjs");

initSettings();
openSettings();

console.log(JSON.stringify({
    modalHtml: document.getElementById("settings-modal").innerHTML,
}));
""".strip(),
    )

    modal_html = cast(str, payload["modalHtml"])
    assert "Max Output Tokens" in modal_html
    assert "Context Window" in modal_html
    assert 'id="profile-name"' in modal_html
    assert 'id="profile-provider"' in modal_html
    assert 'id="profile-is-default"' in modal_html
    assert (
        '<select id="profile-provider" aria-hidden="true" tabindex="-1">' in modal_html
    )
    assert 'value="openai_compatible"' in modal_html
    assert 'value="bigmodel"' not in modal_html
    assert 'value="minimax"' not in modal_html
    assert 'value="maas"' in modal_html
    assert 'value="codeagent"' in modal_html
    assert 'value="echo"' not in modal_html
    assert 'id="profile-model"' in modal_html
    assert (
        'data-i18n-placeholder="settings.model.custom_model_placeholder"' in modal_html
    )
    assert 'id="open-profile-model-menu-btn"' in modal_html
    assert 'id="profile-model-options"' not in modal_html
    assert 'id="profile-model-menu"' in modal_html
    assert 'id="model-catalog-selected"' not in modal_html
    assert 'class="model-catalog-search-field"' in modal_html
    assert 'id="fetch-profile-models-btn"' in modal_html
    assert 'title="Fetch Models"' in modal_html
    assert 'id="profile-primary-credentials-row"' in modal_html
    assert 'id="profile-base-url-fields" style="display:none;"' in modal_html
    assert 'id="profile-provider-custom-btn"' in modal_html
    assert 'data-provider-mode="custom"' in modal_html
    assert 'id="profile-model-group"' in modal_html
    assert 'id="profile-model-group" style="display:none;"' in modal_html
    assert 'id="toggle-profile-api-key-btn"' in modal_html
    assert 'id="profile-probe-status"' not in modal_html
    assert 'id="profile-probe-inline-status"' in modal_html
    assert (
        'id="profile-api-key" placeholder="sk-..." autocomplete="new-password" autocapitalize="off" autocorrect="off" spellcheck="false"'
        in modal_html
    )
    assert 'id="profile-maas-auth-fields"' in modal_html
    assert 'id="profile-maas-username"' in modal_html
    assert 'id="profile-maas-password"' in modal_html
    assert (
        'id="profile-maas-password" placeholder="password" data-i18n-placeholder="settings.model.password_placeholder" autocomplete="new-password" autocapitalize="off" autocorrect="off" spellcheck="false"'
        in modal_html
    )
    assert 'id="profile-maas-model-slot"' in modal_html
    assert 'id="toggle-profile-maas-password-btn"' in modal_html
    assert 'id="profile-provider-codeagent-btn"' in modal_html
    assert 'data-i18n="settings.model.provider_codeagent"' in modal_html
    assert 'data-i18n="settings.model.provider_codeagent_copy"' in modal_html
    assert "CodeAgent Model" in modal_html
    assert "Use CodeAgent models with SSO or username/password sign-in" in modal_html
    assert 'id="profile-codeagent-auth-fields"' in modal_html
    assert 'id="profile-codeagent-login-status"' in modal_html
    assert 'id="profile-codeagent-login-status-message"' in modal_html
    assert 'id="profile-codeagent-model-slot"' in modal_html
    assert 'id="toggle-web-api-key-btn"' in modal_html
    assert (
        'id="toggle-web-api-key-btn" type="button" title="Show API key" aria-label="Show API key"'
        in modal_html
    )
    assert (
        'id="web-api-key" placeholder="可选，用于更高频率限制" data-i18n-placeholder="settings.web.api_key_placeholder" autocomplete="new-password" autocapitalize="off" autocorrect="off" spellcheck="false"'
        in modal_html
    )
    assert 'id="toggle-github-token-btn"' not in modal_html
    assert 'id="toggle-proxy-password-btn"' in modal_html
    assert (
        'id="proxy-password" placeholder="Optional proxy password" data-i18n-placeholder="settings.proxy.password_placeholder" autocomplete="new-password" autocapitalize="off" autocorrect="off" spellcheck="false"'
        in modal_html
    )
    assert 'id="web-searxng-instance-url-field"' in modal_html
    assert 'id="web-searxng-builtins-field"' in modal_html
    assert 'id="web-searxng-builtins-list"' in modal_html
    assert 'placeholder="默认值：{default}"' in modal_html
    assert modal_html.count('<option value="searxng">SearXNG</option>') == 1
    assert modal_html.count('<option value="disabled">Disabled</option>') == 1
    assert 'id="edit-profile-name-btn"' not in modal_html
    assert 'id="edit-profile-name-input"' not in modal_html
    assert ">Fetch</button>" not in modal_html
    assert modal_html.index('label for="profile-name"') < modal_html.index(
        'data-profile-step="model"'
    )
    assert modal_html.index('label for="profile-model"') < modal_html.index(
        'label for="profile-api-key"'
    )
    assert modal_html.index('id="profile-maas-password"') < modal_html.index(
        'id="profile-maas-model-slot"'
    )
    assert modal_html.index('label for="web-provider"') < modal_html.index(
        'label for="web-api-key"'
    )
    assert modal_html.index('label for="web-api-key"') < modal_html.index(
        'label for="web-fallback-provider"'
    )
    assert modal_html.index('id="profile-max-tokens"') < modal_html.index(
        'id="profile-context-window"'
    )
    assert modal_html.index('id="profile-is-default"') < modal_html.index(
        'id="profile-context-window"'
    )
    assert "Model Selection" not in modal_html
    assert (
        "Fetch the endpoint catalog for quick selection, or enter a model name manually."
        not in modal_html
    )
    assert 'id="profile-max-tokens" value=""' in modal_html
    assert 'placeholder="Optional"' in modal_html
    assert "Max Tokens</label>" not in modal_html
    assert ">Test</button>" in modal_html
    assert ">Test URL</button>" in modal_html
    assert ">Validate</button>" in modal_html
    assert ">Save Role</button>" not in modal_html
    assert ">Save Notifications</button>" not in modal_html
    assert "notification-toggle-check" in modal_html
    assert "notification-toggle-label" in modal_html
    assert '<select id="role-model-profile-input"></select>' in modal_html


def test_settings_action_button_order_keeps_cancel_on_far_right() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_text = (
        repo_root / "frontend" / "dist" / "js" / "components" / "settings" / "index.js"
    ).read_text(encoding="utf-8")

    actions_html_start = source_text.index('<div class="settings-panel-actions"')
    actions_html_end = source_text.index(
        "</div>\n                </div>", actions_html_start
    )
    actions_html = source_text[actions_html_start:actions_html_end]
    assert "settings-panel-actions-group-start" in actions_html
    assert "settings-panel-actions-group-end" in actions_html
    assert actions_html.index('id="test-profile-btn"') < actions_html.index(
        'id="profile-probe-inline-status"'
    )
    assert actions_html.index('id="profile-probe-inline-status"') < actions_html.index(
        'id="save-profile-btn"'
    )
    assert 'id="test-proxy-web-btn"' not in actions_html
    assert actions_html.index('id="save-profile-btn"') < actions_html.index(
        'id="cancel-profile-btn"'
    )
    assert actions_html.index('id="validate-role-btn"') < actions_html.index(
        'id="save-role-btn"'
    )
    assert actions_html.index('id="save-role-btn"') < actions_html.index(
        'id="cancel-role-btn"'
    )


def test_hooks_actions_are_grouped_on_the_right_with_consistent_order() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source_text = (
        repo_root / "frontend" / "dist" / "js" / "components" / "settings" / "index.js"
    ).read_text(encoding="utf-8")

    start_group_start = source_text.index(
        '<div class="settings-panel-actions-group settings-panel-actions-group-start">'
    )
    start_group_end = source_text.index("</div>", start_group_start)
    start_group_html = source_text[start_group_start:start_group_end]

    end_group_start = source_text.index(
        '<div class="settings-panel-actions-group settings-panel-actions-group-end">'
    )
    end_group_end = source_text.index("</div>", end_group_start)
    end_group_html = source_text[end_group_start:end_group_end]

    assert 'id="validate-hooks-btn"' not in start_group_html
    assert 'id="add-hook-btn"' in end_group_html
    assert 'id="validate-hooks-btn"' in end_group_html
    assert 'id="save-hooks-btn"' in end_group_html
    assert end_group_html.index('id="add-hook-btn"') < end_group_html.index(
        'id="validate-hooks-btn"'
    )
    assert end_group_html.index('id="validate-hooks-btn"') < end_group_html.index(
        'id="save-hooks-btn"'
    )


def _run_settings_script(tmp_path: Path, runner_source: str) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[3]
    source_path = (
        repo_root / "frontend" / "dist" / "js" / "components" / "settings" / "index.js"
    )

    mock_model_profiles_path = tmp_path / "mockModelProfiles.mjs"
    mock_commands_settings_path = tmp_path / "mockCommandsSettings.mjs"
    model_profiles_template_path = tmp_path / "modelProfilesTemplate.mjs"
    mock_hooks_settings_path = tmp_path / "mockHooksSettings.mjs"
    mock_agents_settings_path = tmp_path / "mockAgentsSettings.mjs"
    mock_environment_path = tmp_path / "mockEnvironmentVariables.mjs"
    mock_notifications_path = tmp_path / "mockNotifications.mjs"
    mock_orchestration_settings_path = tmp_path / "mockOrchestrationSettings.mjs"
    mock_proxy_settings_path = tmp_path / "mockProxySettings.mjs"
    mock_roles_settings_path = tmp_path / "mockRolesSettings.mjs"
    mock_trigger_settings_path = tmp_path / "mockTriggerSettings.mjs"
    mock_web_settings_path = tmp_path / "mockWebSettings.mjs"
    mock_workspace_settings_path = tmp_path / "mockWorkspaceSettings.mjs"
    mock_clawhub_settings_path = tmp_path / "mockClawHubSettings.mjs"
    mock_github_settings_path = tmp_path / "mockGitHubSettings.mjs"
    mock_system_status_path = tmp_path / "mockSystemStatus.mjs"
    mock_appearance_path = tmp_path / "mockAppearanceSettings.mjs"
    mock_i18n_path = tmp_path / "mockI18n.mjs"
    module_under_test_path = tmp_path / "index.mjs"
    runner_path = tmp_path / "runner.mjs"

    mock_model_profiles_path.write_text(
        """
export function bindModelProfileHandlers() {
    globalThis.__bindCalls.model += 1;
}

export async function loadModelProfilesPanel() {
    globalThis.__loadCalls.model += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_commands_settings_path.write_text(
        """
export function bindCommandsSettingsHandlers() {
    globalThis.__bindCalls.commands += 1;
}

export async function loadCommandsSettingsPanel() {
    globalThis.__loadCalls.commands += 1;
}

export function syncCommandsSettingsActions() {}
""".strip(),
        encoding="utf-8",
    )
    model_profiles_template_path.write_text(
        (
            repo_root
            / "frontend"
            / "dist"
            / "js"
            / "components"
            / "settings"
            / "modelProfiles"
            / "template.js"
        ).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    mock_environment_path.write_text(
        """
export function bindEnvironmentVariableSettingsHandlers() {
    globalThis.__bindCalls.environment += 1;
}

export async function loadEnvironmentVariablesPanel() {
    globalThis.__loadCalls.environment += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_hooks_settings_path.write_text(
        """
export function bindHooksSettingsHandlers() {
    globalThis.__bindCalls.hooks += 1;
}

export async function loadHooksSettingsPanel() {
    globalThis.__loadCalls.hooks += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_agents_settings_path.write_text(
        """
export function bindAgentSettingsHandlers() {
    globalThis.__bindCalls.agents += 1;
}

export async function loadAgentSettingsPanel() {
    globalThis.__loadCalls.agents += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_notifications_path.write_text(
        """
export function bindNotificationSettingsHandlers() {
    globalThis.__bindCalls.notifications += 1;
}

export async function loadNotificationSettingsPanel() {
    globalThis.__loadCalls.notifications += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_orchestration_settings_path.write_text(
        """
export function bindOrchestrationSettingsHandlers() {
    globalThis.__bindCalls.orchestration += 1;
}

export async function loadOrchestrationSettingsPanel() {
    globalThis.__loadCalls.orchestration += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_roles_settings_path.write_text(
        """
export function bindRoleSettingsHandlers() {
    globalThis.__bindCalls.roles += 1;
}

export async function loadRoleSettingsPanel() {
    globalThis.__loadCalls.roles += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_trigger_settings_path.write_text(
        """
export function bindTriggerSettingsHandlers() {
    globalThis.__bindCalls.triggers += 1;
}

export async function loadTriggerSettingsPanel() {
    globalThis.__loadCalls.triggers += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_proxy_settings_path.write_text(
        """
export function bindProxySettingsHandlers() {
    globalThis.__bindCalls.proxy += 1;
}

export async function loadProxyStatusPanel() {
    globalThis.__loadCalls.proxy += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_web_settings_path.write_text(
        """
export function bindWebSettingsHandlers() {
    globalThis.__bindCalls.web += 1;
}

export async function loadWebSettingsPanel() {
    globalThis.__loadCalls.web += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_workspace_settings_path.write_text(
        """
export function bindWorkspaceSettingsHandlers() {
    globalThis.__bindCalls.workspace += 1;
}

export async function loadWorkspaceSettingsPanel() {
    globalThis.__loadCalls.workspace += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_clawhub_settings_path.write_text(
        """
export function bindClawHubSettingsHandlers() {
    globalThis.__bindCalls.clawhub += 1;
}

export async function loadClawHubSettingsPanel() {
    globalThis.__loadCalls.clawhub += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_github_settings_path.write_text(
        """
export function bindGitHubSettingsHandlers() {
    globalThis.__bindCalls.github += 1;
}

export async function loadGitHubSettingsPanel() {
    globalThis.__loadCalls.github += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_system_status_path.write_text(
        """
export function bindSystemStatusHandlers() {
    globalThis.__bindCalls.system += 1;
}

export async function loadMcpStatusPanel() {
    globalThis.__loadCalls.mcp += 1;
}

export async function loadSkillsStatusPanel() {
    globalThis.__loadCalls.skills += 1;
}
""".strip(),
        encoding="utf-8",
    )
    mock_appearance_path.write_text(
        """
export function bindAppearanceHandlers() {
    globalThis.__bindCalls.appearance = (globalThis.__bindCalls.appearance || 0) + 1;
}

export function loadAppearancePanel() {
    globalThis.__loadCalls.appearance = (globalThis.__loadCalls.appearance || 0) + 1;
}

export function initAppearanceOnStartup() {}
""".strip(),
        encoding="utf-8",
    )
    mock_i18n_path.write_text(
        """
export function t(key) {
    return {
        'settings.tab.workspace': 'Workspace',
        'settings.panel.appearance.title': 'Appearance',
        'settings.panel.appearance.description': 'Customize accent color, background, fonts, and sizing.',
        'settings.panel.model.title': 'Model',
        'settings.panel.model.description': 'Manage providers, endpoints, request limits, and sampling defaults.',
        'settings.panel.skills.title': 'Skills',
        'settings.panel.skills.description': 'Check installed skills and refresh the server-side registry.',
        'settings.panel.mcp.title': 'MCP',
        'settings.panel.mcp.description': 'Review the currently loaded MCP servers and reload the runtime view.',
        'settings.panel.commands.title': 'Commands',
        'settings.panel.commands.description': 'Review slash commands discovered for the active workspace.',
        'settings.panel.hooks.title': 'Hooks',
        'settings.panel.hooks.description': 'View currently loaded hooks and provide custom editing.',
        'settings.panel.agents.title': 'Agents',
        'settings.panel.agents.description': 'Configure ACP-compatible external agents and make them available for role bindings.',
        'settings.panel.roles.title': 'Roles',
        'settings.panel.roles.description': 'Edit role metadata, allowed tools, memory profile, and prompt text.',
        'settings.panel.orchestration.title': 'Orchestration',
        'settings.panel.orchestration.description': 'Manage orchestrations for Orchestrated Mode. Main Agent and Coordinator base prompts are edited in Roles.',
        'settings.panel.triggers.title': 'Gateway',
        'settings.panel.triggers.description': 'Manage conversational gateways and provider-specific inbound channel accounts.',
        'settings.panel.notifications.title': 'Notifications',
        'settings.panel.notifications.description': 'Choose which run events notify you and where they are delivered.',
        'settings.panel.web.title': 'Web',
        'settings.panel.web.description': 'Choose the web search provider and optionally store an API key for higher limits.',
        'settings.panel.github.title': 'GitHub',
        'settings.panel.github.description': 'Store a GitHub token for the bundled gh CLI and verify the current shell integration.',
        'settings.panel.proxy.title': 'Proxy',
        'settings.panel.proxy.description': 'Edit runtime proxy values, default network SSL policy, and test outbound web connectivity.',
        'settings.panel.workspace.title': 'Workspace',
        'settings.panel.workspace.description': 'Manage reusable SSH profiles referenced by workspace mounts.',
        'settings.panel.environment.title': 'Environment',
        'settings.panel.environment.description': 'Inspect effective runtime environment values and manage Agent Teams app environment variables.',
    }[key] || key;
}

export function translateDocument() {
    return undefined;
}
""".strip(),
        encoding="utf-8",
    )

    source_text = (
        source_path.read_text(encoding="utf-8")
        .replace("./agentsSettings.js", "./mockAgentsSettings.mjs")
        .replace("./commandsSettings.js", "./mockCommandsSettings.mjs")
        .replace("./hooksSettings.js", "./mockHooksSettings.mjs")
        .replace("./modelProfiles/template.js", "./modelProfilesTemplate.mjs")
        .replace("./modelProfiles.js", "./mockModelProfiles.mjs")
        .replace("./environmentVariables.js", "./mockEnvironmentVariables.mjs")
        .replace("./notifications.js", "./mockNotifications.mjs")
        .replace("./orchestrationSettings.js", "./mockOrchestrationSettings.mjs")
        .replace("./triggerSettings.js", "./mockTriggerSettings.mjs")
        .replace("./webSettings.js", "./mockWebSettings.mjs")
        .replace("./workspaceSettings.js", "./mockWorkspaceSettings.mjs")
        .replace("./clawhubSettings.js", "./mockClawHubSettings.mjs")
        .replace("./githubSettings.js", "./mockGitHubSettings.mjs")
        .replace("./proxySettings.js", "./mockProxySettings.mjs")
        .replace("./rolesSettings.js", "./mockRolesSettings.mjs")
        .replace("./systemStatus.js", "./mockSystemStatus.mjs")
        .replace("./appearanceSettings.js", "./mockAppearanceSettings.mjs")
        .replace("../../utils/i18n.js", "./mockI18n.mjs")
    )
    module_under_test_path.write_text(source_text, encoding="utf-8")

    runner_path.write_text(
        f"""
function createClassList(element) {{
    const classes = new Set();

    function sync() {{
        element.className = Array.from(classes).join(" ");
    }}

    return {{
        add(...tokens) {{
            tokens.filter(Boolean).forEach(token => classes.add(token));
            sync();
        }},
        remove(...tokens) {{
            tokens.forEach(token => classes.delete(token));
            sync();
        }},
        toggle(token, force) {{
            const shouldAdd = force === undefined ? !classes.has(token) : Boolean(force);
            if (shouldAdd) {{
                classes.add(token);
            }} else {{
                classes.delete(token);
            }}
            sync();
            return shouldAdd;
        }},
        contains(token) {{
            return classes.has(token);
        }},
        resetFromString(value) {{
            classes.clear();
            String(value || "")
                .split(/\\s+/)
                .filter(Boolean)
                .forEach(token => classes.add(token));
            sync();
        }},
    }};
}}

function createElement(tagName = "div") {{
    const element = {{
        tagName,
        id: "",
        style: {{}},
        dataset: {{}},
        children: [],
        textContent: "",
        onclick: null,
        parentNode: null,
        appendChild(child) {{
            child.parentNode = this;
            this.children.push(child);
        }},
        querySelectorAll(selector) {{
            if (selector !== ".settings-action") {{
                return [];
            }}
            const matches = [];
            for (const match of this.innerHTML.matchAll(/class="[^"]*settings-action[^"]*"[^>]*id="([^"]+)"/g)) {{
                const child = createElement("button");
                child.id = match[1];
                matches.push(child);
            }}
            return matches;
        }},
    }};

    element.classList = createClassList(element);
    let html = "";
    Object.defineProperty(element, "innerHTML", {{
        get() {{
            return html;
        }},
        set(value) {{
            html = String(value);
        }},
    }});
    Object.defineProperty(element, "className", {{
        get() {{
            return element.__className || "";
        }},
        set(value) {{
            element.__className = String(value || "");
        }},
    }});

    return element;
}}

function createDocument() {{
    const elements = new Map();
    const tabs = [];
    const panels = [];
    const body = createElement("body");

    function registerElement(id, element) {{
        if (!id) {{
            return;
        }}
        element.id = id;
        if (!elements.has(id)) {{
            elements.set(id, element);
        }}
    }}

    function parseInnerHtml(target) {{
        tabs.length = 0;
        panels.length = 0;

        const html = target.innerHTML;

        for (const match of html.matchAll(/id="([^"]+)"/g)) {{
            registerElement(match[1], createElement());
        }}

        for (const match of html.matchAll(/class="settings-tab([^"]*)" data-tab="([^"]+)"/g)) {{
            const tab = createElement("button");
            tab.dataset.tab = match[2];
            tab.classList.resetFromString(`settings-tab${{match[1]}}`);
            tabs.push(tab);
        }}

        for (const match of html.matchAll(/class="settings-panel" id="([^"]+)"(?: style="display:none;")?/g)) {{
            const panel = elements.get(match[1]) || createElement();
            panel.id = match[1];
            panel.style.display = html.includes(`id="${{match[1]}}" style="display:none;"`) ? "none" : "block";
            panels.push(panel);
            elements.set(match[1], panel);
        }}
    }}

    const originalAppendChild = body.appendChild.bind(body);
    body.appendChild = (child) => {{
        originalAppendChild(child);
        registerElement(child.id, child);
        parseInnerHtml(child);
    }};

        return {{
            body,
            createElement,
            getElementById(id) {{
                return elements.get(id) || null;
            }},
        querySelectorAll(selector) {{
            if (selector === ".settings-tab") {{
                return tabs;
            }}
            if (selector === ".settings-panel") {{
                return panels;
            }}
            return [];
        }},
    }};
}}

globalThis.__bindCalls = {{
    model: 0,
    commands: 0,
    hooks: 0,
    agents: 0,
    roles: 0,
    orchestration: 0,
    triggers: 0,
    environment: 0,
    notifications: 0,
    web: 0,
    workspace: 0,
    clawhub: 0,
    github: 0,
    proxy: 0,
    system: 0,
}};
globalThis.__loadCalls = {{
    model: 0,
    commands: 0,
    hooks: 0,
    agents: 0,
    roles: 0,
    orchestration: 0,
    triggers: 0,
    environment: 0,
    notifications: 0,
    web: 0,
    workspace: 0,
    clawhub: 0,
    github: 0,
    proxy: 0,
    mcp: 0,
    skills: 0,
}};

globalThis.document = createDocument();
globalThis.window = {{}};

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
        encoding="utf-8",
        timeout=30,
    )

    if completed.returncode != 0:
        raise AssertionError(
            "Node runner failed:\n"
            f"STDOUT:\n{completed.stdout}\n"
            f"STDERR:\n{completed.stderr}"
        )

    return json.loads(completed.stdout)


def test_environment_settings_tab_uses_add_variable_action(
    tmp_path: Path,
) -> None:
    payload = _run_settings_script(
        tmp_path=tmp_path,
        runner_source="""
const { initSettings, openSettings } = await import("./index.mjs");

initSettings();
openSettings();

const tabs = document.querySelectorAll(".settings-tab");
const environmentTab = tabs.find(tab => tab.dataset.tab === "environment");
await environmentTab.onclick();

console.log(JSON.stringify({
    panelTitle: document.getElementById("settings-panel-title").textContent,
    envPanelDisplay: document.getElementById("environment-panel").style.display,
    envAddDisplay: document.getElementById("add-env-btn").style.display,
    loadCalls: globalThis.__loadCalls,
}));
""".strip(),
    )

    load_calls = cast(dict[str, JsonValue], payload["loadCalls"])
    assert payload["panelTitle"] == "Environment"
    assert payload["envPanelDisplay"] == "block"
    assert payload["envAddDisplay"] == "inline-flex"
    assert load_calls["environment"] == 1


def test_workspace_settings_tab_uses_add_ssh_profile_action(
    tmp_path: Path,
) -> None:
    payload = _run_settings_script(
        tmp_path=tmp_path,
        runner_source="""
const { initSettings, openSettings } = await import("./index.mjs");

initSettings();
openSettings("workspace");

console.log(JSON.stringify({
    panelTitle: document.getElementById("settings-panel-title").textContent,
    workspacePanelDisplay: document.getElementById("workspace-panel").style.display,
    workspaceAddDisplay: document.getElementById("add-ssh-profile-btn").style.display,
    loadCalls: globalThis.__loadCalls,
}));
""".strip(),
    )

    load_calls = cast(dict[str, JsonValue], payload["loadCalls"])
    assert payload["panelTitle"] == "Workspace"
    assert payload["workspacePanelDisplay"] == "block"
    assert payload["workspaceAddDisplay"] == "inline-flex"
    assert load_calls["workspace"] == 1


def test_settings_modal_only_closes_for_direct_overlay_click(tmp_path: Path) -> None:
    payload = _run_settings_script(
        tmp_path=tmp_path,
        runner_source="""
const { initSettings, openSettings } = await import("./index.mjs");

initSettings();
openSettings();

const settingsModal = document.getElementById("settings-modal");
const modalContent = settingsModal.children[0];

settingsModal.onmousedown({ target: modalContent });
settingsModal.onclick({ target: settingsModal });
const afterDraggedReleaseDisplay = settingsModal.style.display;

openSettings();
settingsModal.onmousedown({ target: settingsModal });
settingsModal.onclick({ target: settingsModal });
const afterDirectOverlayClickDisplay = settingsModal.style.display;

console.log(JSON.stringify({
    afterDraggedReleaseDisplay,
    afterDirectOverlayClickDisplay,
}));
""".strip(),
    )

    assert payload["afterDraggedReleaseDisplay"] == "flex"
    assert payload["afterDirectOverlayClickDisplay"] == "none"
