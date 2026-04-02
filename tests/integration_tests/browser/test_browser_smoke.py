from __future__ import annotations

import asyncio
from collections.abc import Iterator
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import cast
from uuid import uuid4

import httpx
from agent_teams.gateway.acp_stdio import AcpGatewayServer, _AcpRequestContext
from agent_teams.gateway.gateway_cli import _build_acp_stdio_runtime
from pydantic import JsonValue
from playwright.sync_api import Page
from playwright.sync_api import expect
from playwright.sync_api import sync_playwright
import pytest

from integration_tests.support.environment import IntegrationEnvironment


_CONNECTED_LABEL = re.compile(r"(Backend Connected|后端已连接)")
_PROBE_SUCCESS_LABEL = re.compile(r"(Connected|连接成功)")
_GATEWAY_SIGNALS_LABEL = re.compile(r"(Gateway Signals|Gateway 信号)")
_GATEWAY_BREAKDOWN_LABEL = re.compile(r"(Gateway Breakdown|Gateway 拆解)")
_GATEWAY_CALLS_LABEL = re.compile(r"(Gateway Calls|Gateway 调用)")
_GATEWAY_FIRST_UPDATE_LABEL = re.compile(r"(Prompt First Update ms|首个更新 ms)")
_GATEWAY_LATENCY_LABEL = re.compile(r"(Gateway Latency|Gateway 时延)")
_GATEWAY_COLD_STARTS_LABEL = re.compile(r"(Gateway Cold Starts|Gateway 冷启动)")
_LANG_PATTERN = re.compile(r"^(en|en-US|zh-CN)$")
_VIEWPORT_WIDTH = 1600
_VIEWPORT_HEIGHT = 1200
_WAIT_TIMEOUT_MS = 30_000


@pytest.fixture()
def browser_page() -> Iterator[Page]:
    browser_root = _resolve_playwright_browser_root()
    previous_browser_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_root)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": _VIEWPORT_WIDTH, "height": _VIEWPORT_HEIGHT},
                color_scheme="dark",
            )
            page = context.new_page()
            try:
                yield page
            finally:
                context.close()
                browser.close()
    finally:
        if previous_browser_root is None:
            os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        else:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = previous_browser_root


def test_browser_run_flow_uses_canonical_input_payload(
    browser_page: Page,
    integration_env: IntegrationEnvironment,
) -> None:
    page = browser_page
    _open_app(page, integration_env)

    session_id = _create_session_via_sidebar(page)
    prompt = "请用一句话确认当前系统可正常响应。"

    with page.expect_request(
        lambda request: (
            request.method == "POST"
            and request.url == f"{integration_env.api_base_url}/api/runs"
        )
    ) as run_request_info:
        page.locator("#prompt-input").fill(prompt)
        page.locator("#send-btn").click()

    payload = json.loads(run_request_info.value.post_data or "{}")
    assert payload["session_id"] == session_id
    assert payload["input"] == [{"kind": "text", "text": prompt}]
    assert "intent" not in payload

    round_section = page.locator(".session-round-section").first
    expect(round_section).to_contain_text(prompt, timeout=_WAIT_TIMEOUT_MS)
    expect(round_section).to_contain_text(
        f"[fake-llm] {prompt}",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator(".session-item.active")).to_have_attribute(
        "data-session-id",
        session_id,
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#send-btn")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    expect(page.locator("#stop-btn")).to_be_hidden(timeout=_WAIT_TIMEOUT_MS)


def test_browser_webfetch_approval_reuses_host_scoped_ticket(
    browser_page: Page,
    integration_env: IntegrationEnvironment,
    api_client: httpx.Client,
) -> None:
    page = browser_page
    _open_app(page, integration_env)

    session_id = _create_session_via_sidebar(page)
    _set_checkbox(page, "#yolo-toggle", False)
    prompt = (
        "[webfetch-approval-validation] 连续两次调用同一个 host 的 webfetch，"
        "只在第一次审批。"
    )

    with (
        page.expect_request(
            lambda request: (
                request.method == "POST"
                and request.url == f"{integration_env.api_base_url}/api/runs"
            )
        ) as run_request_info,
        page.expect_response(
            lambda response: (
                response.request.method == "POST"
                and response.url == f"{integration_env.api_base_url}/api/runs"
                and response.ok
            )
        ) as run_response_info,
    ):
        page.locator("#prompt-input").fill(prompt)
        page.locator("#send-btn").click()

    run_request_payload = json.loads(run_request_info.value.post_data or "{}")
    assert run_request_payload["session_id"] == session_id
    assert run_request_payload["yolo"] is False
    assert run_request_payload["input"] == [{"kind": "text", "text": prompt}]

    run_payload = run_response_info.value.json()
    run_id = str(run_payload["run_id"])
    assert run_payload["session_id"] == session_id

    approvals = _wait_for_open_tool_approvals(
        api_client,
        run_id=run_id,
        expected_count=1,
    )
    assert approvals[0]["tool_call_id"] == "call-webfetch-1"
    assert approvals[0]["tool_name"] == "webfetch"
    assert "https://localhost/one" in approvals[0]["args_preview"]

    approval_items = page.locator(".recovery-approval-item")
    expect(approval_items).to_have_count(1, timeout=_WAIT_TIMEOUT_MS)
    expect(page.locator("#recovery-banner-host")).to_be_visible(
        timeout=_WAIT_TIMEOUT_MS
    )

    with page.expect_request(
        lambda request: (
            request.method == "POST"
            and request.url
            == (
                f"{integration_env.api_base_url}/api/runs/{run_id}/tool-approvals/"
                "call-webfetch-1/resolve"
            )
        )
    ) as resolve_request_info:
        page.locator('[data-approval-action="approve"]').click()

    resolve_payload = json.loads(resolve_request_info.value.post_data or "{}")
    assert resolve_payload == {"action": "approve", "feedback": ""}

    round_section = page.locator(f'.session-round-section[data-run-id="{run_id}"]')
    expect(round_section).to_contain_text(prompt, timeout=_WAIT_TIMEOUT_MS)
    expect(round_section).to_contain_text(
        "[fake-llm] Webfetch approval validation completed after one "
        "host-scoped approval.",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(approval_items).to_have_count(0, timeout=_WAIT_TIMEOUT_MS)

    remaining_approvals = _wait_for_open_tool_approvals(
        api_client,
        run_id=run_id,
        expected_count=0,
    )
    assert remaining_approvals == []
    expect(page.locator("#send-btn")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    expect(page.locator("#stop-btn")).to_be_hidden(timeout=_WAIT_TIMEOUT_MS)


def test_browser_shell_settings_and_session_management(
    browser_page: Page,
    integration_env: IntegrationEnvironment,
) -> None:
    page = browser_page
    _emit_gateway_observability_probe()
    _open_app(page, integration_env)

    baseline_count = len(_session_ids(page))
    session_id = _create_session_via_sidebar(page)
    renamed_title = "Browser Smoke Session"

    with page.expect_request(
        lambda request: (
            request.method == "PATCH"
            and request.url
            == f"{integration_env.api_base_url}/api/sessions/{session_id}"
        )
    ):
        page.locator(f'.session-rename-btn[data-session-id="{session_id}"]').click(
            force=True
        )
        expect(page.locator(".feedback-dialog-input")).to_be_visible(
            timeout=_WAIT_TIMEOUT_MS
        )
        page.locator(".feedback-dialog-input").fill(renamed_title)
        page.locator("[data-feedback-confirm]").click()

    expect(
        page.locator(
            f'.session-item[data-session-id="{session_id}"] .session-label-text'
        )
    ).to_have_text(renamed_title, timeout=_WAIT_TIMEOUT_MS)

    initial_lang = _html_lang(page)
    with page.expect_request(
        lambda request: (
            request.method == "PUT"
            and request.url
            == f"{integration_env.api_base_url}/api/system/configs/ui-language"
        )
    ) as language_request_info:
        page.locator("#language-toggle-btn").click()
    language_payload = json.loads(language_request_info.value.post_data or "{}")
    page.wait_for_function(
        "expectedLang => document.documentElement.lang !== expectedLang",
        arg=initial_lang,
        timeout=_WAIT_TIMEOUT_MS,
    )
    toggled_lang = _html_lang(page)
    assert toggled_lang != initial_lang
    assert _LANG_PATTERN.fullmatch(toggled_lang) is not None
    assert language_payload["language"] == toggled_lang

    with page.expect_request(
        lambda request: (
            request.method == "PUT"
            and request.url
            == f"{integration_env.api_base_url}/api/system/configs/ui-language"
        )
    ):
        page.locator("#language-toggle-btn").click()
    page.wait_for_function(
        "expectedLang => document.documentElement.lang === expectedLang",
        arg=initial_lang,
        timeout=_WAIT_TIMEOUT_MS,
    )

    initial_background = _body_background(page)
    page.locator("#toggle-theme").click()
    page.wait_for_function(
        "expectedColor => getComputedStyle(document.body).backgroundColor !== expectedColor",
        arg=initial_background,
        timeout=_WAIT_TIMEOUT_MS,
    )
    toggled_background = _body_background(page)
    assert toggled_background != initial_background

    page.locator("#settings-btn").click()
    expect(page.locator("#settings-modal")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    for tab_name in (
        "appearance",
        "model",
        "skills",
        "mcp",
        "agents",
        "roles",
        "orchestration",
        "triggers",
        "notifications",
        "web",
        "github",
        "proxy",
        "environment",
    ):
        page.locator(f'.settings-tab[data-tab="{tab_name}"]').click()
        expect(page.locator(f"#{tab_name}-panel")).to_be_visible(
            timeout=_WAIT_TIMEOUT_MS
        )

    page.locator('.settings-tab[data-tab="model"]').click()
    with page.expect_request(
        lambda request: (
            request.method == "POST"
            and request.url
            == f"{integration_env.api_base_url}/api/system/configs/model:probe"
        )
    ):
        page.locator(".profile-card-test-btn").first.click()
    expect(page.locator(".profile-card-probe-status").first).to_contain_text(
        _PROBE_SUCCESS_LABEL,
        timeout=_WAIT_TIMEOUT_MS,
    )

    page.locator('.settings-tab[data-tab="skills"]').click()
    with page.expect_request(
        lambda request: (
            request.method == "POST"
            and request.url
            == f"{integration_env.api_base_url}/api/system/configs/skills:reload"
        )
    ):
        page.locator("#reload-skills-btn").click()

    page.locator('.settings-tab[data-tab="mcp"]').click()
    with page.expect_request(
        lambda request: (
            request.method == "POST"
            and request.url
            == f"{integration_env.api_base_url}/api/system/configs/mcp:reload"
        )
    ):
        page.locator("#reload-mcp-btn").click()

    page.locator("#settings-close").click()
    expect(page.locator("#settings-modal")).to_be_hidden(timeout=_WAIT_TIMEOUT_MS)

    with page.expect_request(
        lambda request: (
            request.method == "GET"
            and request.url.startswith(
                f"{integration_env.api_base_url}/api/observability/overview"
            )
            and "scope=session" in request.url
        )
    ):
        page.locator("#observability-btn").click()
    expect(page.locator("#observability-view")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)

    with page.expect_request(
        lambda request: (
            request.method == "GET"
            and request.url.startswith(
                f"{integration_env.api_base_url}/api/observability/overview"
            )
            and "scope=global" in request.url
        )
    ):
        page.locator("#observability-global-btn").click()

    gateway_signals = page.locator('[data-observability-section="gateway-signals"]')
    expect(gateway_signals).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    expect(gateway_signals).to_contain_text(
        _GATEWAY_SIGNALS_LABEL,
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator('[data-observability-metric="gateway_calls"]')).to_contain_text(
        _GATEWAY_CALLS_LABEL, timeout=_WAIT_TIMEOUT_MS
    )
    expect(page.locator('[data-observability-metric="gateway_calls"]')).to_contain_text(
        "3", timeout=_WAIT_TIMEOUT_MS
    )
    expect(
        page.locator('[data-observability-metric="gateway_prompt_avg_first_update_ms"]')
    ).to_contain_text(_GATEWAY_FIRST_UPDATE_LABEL, timeout=_WAIT_TIMEOUT_MS)

    gateway_breakdowns = page.locator(
        '[data-observability-section="gateway-breakdowns"]'
    )
    expect(gateway_breakdowns).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    expect(gateway_breakdowns).to_contain_text(
        _GATEWAY_BREAKDOWN_LABEL,
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(
        page.locator('[data-observability-chart="gateway-breakdown-calls"]')
    ).to_contain_text(_GATEWAY_CALLS_LABEL, timeout=_WAIT_TIMEOUT_MS)
    expect(
        page.locator('[data-observability-chart="gateway-breakdown-duration"]')
    ).to_contain_text(_GATEWAY_LATENCY_LABEL, timeout=_WAIT_TIMEOUT_MS)
    expect(
        page.locator('[data-observability-chart="gateway-breakdown-cold-starts"]')
    ).to_contain_text(_GATEWAY_COLD_STARTS_LABEL, timeout=_WAIT_TIMEOUT_MS)

    page.locator("#observability-back-btn").click()
    expect(page.locator("#observability-view")).to_be_hidden(timeout=_WAIT_TIMEOUT_MS)

    with page.expect_request(
        lambda request: (
            request.method == "DELETE"
            and request.url
            == f"{integration_env.api_base_url}/api/sessions/{session_id}"
        )
    ):
        page.locator(f'.session-delete-btn[data-session-id="{session_id}"]').click(
            force=True
        )
        expect(page.locator('[role="alertdialog"]')).to_be_visible(
            timeout=_WAIT_TIMEOUT_MS
        )
        page.locator("[data-feedback-confirm]").click()

    expect(
        page.locator(f'.session-item[data-session-id="{session_id}"]')
    ).to_have_count(0, timeout=_WAIT_TIMEOUT_MS)
    assert len(_session_ids(page)) == baseline_count


def test_browser_environment_variables_and_session_topology(
    browser_page: Page,
    integration_env: IntegrationEnvironment,
) -> None:
    page = browser_page
    _open_app(page, integration_env)

    session_id = _create_session_via_sidebar(page)
    env_key = "BROWSER_SMOKE_ENV"
    env_value = "browser-smoke-value"
    orchestration_id = "browser_smoke_orchestration"
    orchestration_name = "Browser Smoke Orchestration"
    orchestration_prompt = "Delegate to the best role and keep the final answer brief."

    page.locator("#settings-btn").click()
    expect(page.locator("#settings-modal")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)

    page.locator('.settings-tab[data-tab="environment"]').click()
    expect(page.locator("#environment-panel")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)

    page.locator("#add-env-btn").click()
    expect(page.locator("#env-key-input")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    page.locator("#env-key-input").fill(env_key)
    page.locator("#env-value-input").fill(env_value)

    with page.expect_request(
        lambda request: (
            request.method == "PUT"
            and request.url
            == f"{integration_env.api_base_url}/api/system/configs/environment-variables/app/{env_key}"
        )
    ) as save_env_request_info:
        page.locator("#save-env-btn").click()
    save_env_payload = json.loads(save_env_request_info.value.post_data or "{}")
    assert save_env_payload == {"source_key": None, "value": env_value}
    expect(
        page.locator(f'.env-record[data-env-scope="app"][data-env-key="{env_key}"]')
    ).to_be_visible(timeout=_WAIT_TIMEOUT_MS)

    with page.expect_request(
        lambda request: (
            request.method == "DELETE"
            and request.url
            == f"{integration_env.api_base_url}/api/system/configs/environment-variables/app/{env_key}"
        )
    ):
        page.locator(f'[data-env-delete="app::{env_key}"]').click(force=True)
        expect(page.locator('[role="alertdialog"]')).to_be_visible(
            timeout=_WAIT_TIMEOUT_MS
        )
        page.locator("[data-feedback-confirm]").click()
    expect(
        page.locator(f'.env-record[data-env-scope="app"][data-env-key="{env_key}"]')
    ).to_have_count(0, timeout=_WAIT_TIMEOUT_MS)

    page.locator('.settings-tab[data-tab="orchestration"]').click()
    expect(page.locator("#orchestration-panel")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    page.locator("#add-orchestration-preset-btn").click()
    expect(page.locator("#orchestration-id-input")).to_be_visible(
        timeout=_WAIT_TIMEOUT_MS
    )
    page.locator("#orchestration-id-input").fill(orchestration_id)
    page.locator("#orchestration-name-input").fill(orchestration_name)
    page.locator("#orchestration-description-input").fill("Browser smoke test preset.")
    if not page.locator("#orchestration-default-input").is_checked():
        page.locator("#orchestration-default-input").check()
    expect(
        page.locator('#orchestration-role-picker input[type="checkbox"]').first
    ).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    if (
        page.locator(
            '#orchestration-role-picker input[type="checkbox"]:checked'
        ).count()
        == 0
    ):
        page.locator('#orchestration-role-picker input[type="checkbox"]').first.check()
    page.locator("#orchestration-prompt-input").fill(orchestration_prompt)

    with page.expect_request(
        lambda request: (
            request.method == "PUT"
            and request.url
            == f"{integration_env.api_base_url}/api/system/configs/orchestration"
        )
    ) as save_orchestration_request_info:
        page.locator("#save-orchestration-btn").click()
    save_orchestration_payload = json.loads(
        save_orchestration_request_info.value.post_data or "{}"
    )
    orchestration_config = save_orchestration_payload["config"]
    assert orchestration_config["default_orchestration_preset_id"] == orchestration_id
    saved_preset = next(
        preset
        for preset in orchestration_config["presets"]
        if preset["preset_id"] == orchestration_id
    )
    assert saved_preset["name"] == orchestration_name
    assert saved_preset["orchestration_prompt"] == orchestration_prompt
    assert "is_default" not in saved_preset
    expect(
        page.locator(f'.role-record[data-orchestration-id="{orchestration_id}"]')
    ).to_be_visible(timeout=_WAIT_TIMEOUT_MS)

    page.locator("#settings-close").click()
    expect(page.locator("#settings-modal")).to_be_hidden(timeout=_WAIT_TIMEOUT_MS)

    expect(page.locator("#session-mode-orchestration-btn")).to_be_enabled(
        timeout=_WAIT_TIMEOUT_MS
    )
    with page.expect_request(
        lambda request: (
            request.method == "PATCH"
            and request.url
            == f"{integration_env.api_base_url}/api/sessions/{session_id}/topology"
        )
    ) as topology_request_info:
        page.locator("#session-mode-orchestration-btn").click()
    topology_payload = json.loads(topology_request_info.value.post_data or "{}")
    assert topology_payload["session_mode"] == "orchestration"
    assert isinstance(topology_payload["orchestration_preset_id"], str)
    assert topology_payload["orchestration_preset_id"]
    assert "normal_root_role_id" not in topology_payload
    expect(page.locator("#session-mode-label")).to_contain_text(
        re.compile(r"(Orchestrated Mode|编排模式)"),
        timeout=_WAIT_TIMEOUT_MS,
    )
    with page.expect_request(
        lambda request: (
            request.method == "PATCH"
            and request.url
            == f"{integration_env.api_base_url}/api/sessions/{session_id}/topology"
        )
    ) as preset_selection_request_info:
        page.locator("#orchestration-preset-select").select_option(orchestration_id)
    preset_selection_payload = json.loads(
        preset_selection_request_info.value.post_data or "{}"
    )
    assert preset_selection_payload == {
        "session_mode": "orchestration",
        "orchestration_preset_id": orchestration_id,
    }

    with page.expect_request(
        lambda request: (
            request.method == "PATCH"
            and request.url
            == f"{integration_env.api_base_url}/api/sessions/{session_id}/topology"
        )
    ) as reset_topology_request_info:
        page.locator("#session-mode-normal-btn").click()
    reset_topology_payload = json.loads(
        reset_topology_request_info.value.post_data or "{}"
    )
    assert reset_topology_payload["session_mode"] == "normal"
    assert reset_topology_payload["orchestration_preset_id"] is None
    assert isinstance(reset_topology_payload["normal_root_role_id"], str)
    assert reset_topology_payload["normal_root_role_id"]


def test_browser_settings_save_role_and_agent_configs(
    browser_page: Page,
    integration_env: IntegrationEnvironment,
    api_client: httpx.Client,
) -> None:
    page = browser_page
    _open_app(page, integration_env)

    web_response = api_client.get("/api/system/configs/web")
    web_response.raise_for_status()
    initial_web_api_key = str(web_response.json().get("api_key") or "")

    github_response = api_client.get("/api/system/configs/github")
    github_response.raise_for_status()
    initial_github_token = str(github_response.json().get("token") or "")

    proxy_response = api_client.get("/api/system/configs/proxy")
    proxy_response.raise_for_status()
    initial_proxy_payload = proxy_response.json()
    initial_proxy_http = str(initial_proxy_payload.get("http_proxy") or "")

    notification_enabled_id = "notif-run_stopped-enabled"
    notification_browser_id = "notif-run_stopped-browser"
    web_api_key = f"browser-web-{uuid4().hex[:8]}"
    github_token = f"ghp_browser_{uuid4().hex[:12]}"
    proxy_url = "http://127.0.0.1:7890"
    agent_id = f"browser_agent_{uuid4().hex[:8]}"
    role_id = f"browser_role_{uuid4().hex[:8]}"
    role_prompt = "# Browser Role Prompt\nKeep outputs concise."

    page.locator("#settings-btn").click()
    expect(page.locator("#settings-modal")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)

    with page.expect_response(
        lambda response: (
            response.request.method == "GET"
            and response.url
            == f"{integration_env.api_base_url}/api/system/configs/notifications"
            and response.ok
        )
    ):
        page.locator('.settings-tab[data-tab="notifications"]').click()
    expect(page.locator("#notifications-panel")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    _set_checkbox(page, f"#{notification_enabled_id}", True)
    _set_checkbox(page, f"#{notification_browser_id}", True)
    _set_checkbox(page, "#notif-run_stopped-toast", False)
    with page.expect_request(
        lambda request: (
            request.method == "PUT"
            and request.url
            == f"{integration_env.api_base_url}/api/system/configs/notifications"
        )
    ) as save_notification_request_info:
        page.locator("#save-notifications-btn").click()
    notification_payload = json.loads(
        save_notification_request_info.value.post_data or "{}"
    )
    run_stopped_rule = notification_payload["config"]["run_stopped"]
    assert run_stopped_rule["enabled"] is True
    assert run_stopped_rule["channels"] == ["browser"]
    expect(page.locator(f"#{notification_enabled_id}")).to_be_checked(
        timeout=_WAIT_TIMEOUT_MS
    )
    expect(page.locator(f"#{notification_browser_id}")).to_be_checked(
        timeout=_WAIT_TIMEOUT_MS
    )

    with page.expect_response(
        lambda response: (
            response.request.method == "GET"
            and response.url == f"{integration_env.api_base_url}/api/system/configs/web"
            and response.ok
        )
    ):
        page.locator('.settings-tab[data-tab="web"]').click()
    expect(page.locator("#web-panel")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    expect(page.locator("#web-api-key")).to_have_value(
        initial_web_api_key,
        timeout=_WAIT_TIMEOUT_MS,
    )
    page.locator("#web-api-key").fill(web_api_key)
    with page.expect_request(
        lambda request: (
            request.method == "PUT"
            and request.url == f"{integration_env.api_base_url}/api/system/configs/web"
        )
    ) as save_web_request_info:
        page.locator("#save-web-btn").click()
    web_payload = json.loads(save_web_request_info.value.post_data or "{}")
    assert web_payload == {"provider": "exa", "api_key": web_api_key}
    expect(page.locator("#web-api-key")).to_have_value(
        web_api_key,
        timeout=_WAIT_TIMEOUT_MS,
    )

    with page.expect_response(
        lambda response: (
            response.request.method == "GET"
            and response.url
            == f"{integration_env.api_base_url}/api/system/configs/github"
            and response.ok
        )
    ):
        page.locator('.settings-tab[data-tab="github"]').click()
    expect(page.locator("#github-panel")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    expect(page.locator("#github-token")).to_have_value(
        initial_github_token,
        timeout=_WAIT_TIMEOUT_MS,
    )
    page.locator("#github-token").fill(github_token)
    with page.expect_request(
        lambda request: (
            request.method == "PUT"
            and request.url
            == f"{integration_env.api_base_url}/api/system/configs/github"
        )
    ) as save_github_request_info:
        page.locator("#save-github-btn").click()
    github_payload = json.loads(save_github_request_info.value.post_data or "{}")
    assert github_payload == {"token": github_token}
    expect(page.locator("#github-token")).to_have_value(
        github_token,
        timeout=_WAIT_TIMEOUT_MS,
    )

    with page.expect_response(
        lambda response: (
            response.request.method == "GET"
            and response.url
            == f"{integration_env.api_base_url}/api/system/configs/proxy"
            and response.ok
        )
    ):
        page.locator('.settings-tab[data-tab="proxy"]').click()
    expect(page.locator("#proxy-panel")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    expect(page.locator("#proxy-http-proxy")).to_have_value(
        initial_proxy_http,
        timeout=_WAIT_TIMEOUT_MS,
    )
    page.locator("#proxy-http-proxy").fill(proxy_url)
    page.locator("#proxy-https-proxy").fill(proxy_url)
    page.locator("#proxy-no-proxy").fill("localhost,127.0.0.1")
    page.locator("#proxy-ssl-verify").select_option("false")
    with page.expect_request(
        lambda request: (
            request.method == "PUT"
            and request.url
            == f"{integration_env.api_base_url}/api/system/configs/proxy"
        )
    ) as save_proxy_request_info:
        page.locator("#save-proxy-btn").click()
    proxy_payload = json.loads(save_proxy_request_info.value.post_data or "{}")
    assert proxy_payload == {
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "all_proxy": "",
        "no_proxy": "localhost,127.0.0.1",
        "proxy_username": "",
        "proxy_password": "",
        "ssl_verify": False,
    }
    expect(page.locator("#proxy-http-proxy")).to_have_value(
        proxy_url,
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#proxy-ssl-verify")).to_have_value(
        "false",
        timeout=_WAIT_TIMEOUT_MS,
    )

    page.locator('.settings-tab[data-tab="agents"]').click()
    expect(page.locator("#agents-panel")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    page.locator("#add-agent-btn").click()
    expect(page.locator("#agent-editor-form")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    page.locator("#agent-id-input").fill(agent_id)
    page.locator("#agent-name-input").fill("Browser Agent")
    page.locator("#agent-description-input").fill("Browser integration agent.")
    page.locator("#agent-stdio-command-input").fill("python")
    page.locator("#agent-stdio-args-input").fill("-m\nagent_teams")
    with page.expect_request(
        lambda request: (
            request.method == "PUT"
            and request.url
            == f"{integration_env.api_base_url}/api/system/configs/agents/{agent_id}"
        )
    ) as save_agent_request_info:
        page.locator("#save-agent-btn").click()
    agent_payload = json.loads(save_agent_request_info.value.post_data or "{}")
    assert agent_payload == {
        "agent_id": agent_id,
        "name": "Browser Agent",
        "description": "Browser integration agent.",
        "transport": {
            "transport": "stdio",
            "command": "python",
            "args": ["-m", "agent_teams"],
            "env": [],
        },
    }
    expect(page.locator("#agent-id-input")).to_have_value(
        agent_id,
        timeout=_WAIT_TIMEOUT_MS,
    )

    with page.expect_response(
        lambda response: (
            response.request.method == "GET"
            and response.url == f"{integration_env.api_base_url}/api/roles:options"
            and response.ok
        )
    ):
        page.locator('.settings-tab[data-tab="roles"]').click()
    expect(page.locator("#roles-panel")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    page.locator("#add-role-btn").click()
    expect(page.locator("#role-editor-form")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    page.locator("#role-id-input").fill(role_id)
    page.locator("#role-name-input").fill("Browser Role")
    page.locator("#role-description-input").fill("Browser integration role.")
    page.locator("#role-version-input").fill("1.0.0")
    expect(
        page.locator(f'#role-bound-agent-input option[value="{agent_id}"]')
    ).to_have_count(1, timeout=_WAIT_TIMEOUT_MS)
    page.locator("#role-bound-agent-input").select_option(agent_id)
    page.locator("#role-system-prompt-input").fill(role_prompt)
    page.locator("#role-prompt-preview-tab").click()
    expect(page.locator("#role-system-prompt-preview")).to_contain_text(
        "Browser Role Prompt",
        timeout=_WAIT_TIMEOUT_MS,
    )
    with page.expect_request(
        lambda request: (
            request.method == "POST"
            and request.url
            == f"{integration_env.api_base_url}/api/roles:validate-config"
        )
    ) as validate_role_request_info:
        page.locator("#validate-role-btn").click()
    validate_role_payload = json.loads(
        validate_role_request_info.value.post_data or "{}"
    )
    assert validate_role_payload["role_id"] == role_id
    assert validate_role_payload["bound_agent_id"] == agent_id
    assert validate_role_payload["system_prompt"] == role_prompt

    with page.expect_request(
        lambda request: (
            request.method == "PUT"
            and request.url
            == f"{integration_env.api_base_url}/api/roles/configs/{role_id}"
        )
    ) as save_role_request_info:
        page.locator("#save-role-btn").click()
    save_role_payload = json.loads(save_role_request_info.value.post_data or "{}")
    assert save_role_payload["role_id"] == role_id
    assert save_role_payload["bound_agent_id"] == agent_id
    assert save_role_payload["system_prompt"] == role_prompt
    expect(page.locator("#role-id-input")).to_have_value(
        role_id,
        timeout=_WAIT_TIMEOUT_MS,
    )

    page.locator("#cancel-role-btn").click()
    expect(page.locator("#roles-list")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    expect(
        page.locator(f'.role-record-delete-btn[data-role-id="{role_id}"]')
    ).to_have_count(1, timeout=_WAIT_TIMEOUT_MS)
    with page.expect_request(
        lambda request: (
            request.method == "DELETE"
            and request.url
            == f"{integration_env.api_base_url}/api/roles/configs/{role_id}"
        )
    ):
        page.locator(f'.role-record-delete-btn[data-role-id="{role_id}"]').evaluate(
            "(button) => button.click()"
        )
        expect(page.locator('[role="alertdialog"]')).to_be_visible(
            timeout=_WAIT_TIMEOUT_MS
        )
        page.locator("[data-feedback-confirm]").click()
    expect(page.locator(f'.role-record[data-role-id="{role_id}"]')).to_have_count(
        0,
        timeout=_WAIT_TIMEOUT_MS,
    )

    page.locator('.settings-tab[data-tab="agents"]').click()
    expect(page.locator("#agents-panel")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    page.locator(f'.agent-record-edit-btn[data-agent-id="{agent_id}"]').click()
    expect(page.locator("#agent-editor-form")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    with page.expect_request(
        lambda request: (
            request.method == "DELETE"
            and request.url
            == f"{integration_env.api_base_url}/api/system/configs/agents/{agent_id}"
        )
    ):
        page.locator("#delete-agent-btn").click()
    expect(page.locator(f'.role-record[data-agent-id="{agent_id}"]')).to_have_count(
        0,
        timeout=_WAIT_TIMEOUT_MS,
    )

    page.locator("#settings-close").click()
    expect(page.locator("#settings-modal")).to_be_hidden(timeout=_WAIT_TIMEOUT_MS)


def test_browser_workspace_and_automation_project_views(
    browser_page: Page,
    integration_env: IntegrationEnvironment,
    api_client: httpx.Client,
    tmp_path: Path,
) -> None:
    workspace_id = f"browser-workspace-{uuid4().hex[:8]}"
    workspace_root = tmp_path / workspace_id
    (workspace_root / "docs").mkdir(parents=True)
    (workspace_root / "README.md").write_text(
        "Browser workspace root.\n",
        encoding="utf-8",
    )
    (workspace_root / "docs" / "guide.md").write_text(
        "Guide for browser integration.\n",
        encoding="utf-8",
    )
    create_workspace_response = api_client.post(
        "/api/workspaces",
        json={
            "workspace_id": workspace_id,
            "root_path": str(workspace_root),
        },
    )
    create_workspace_response.raise_for_status()

    page = browser_page
    _open_app(page, integration_env)

    workspace_card = _project_card(page, workspace_id)
    with page.expect_request(
        lambda request: (
            request.method == "GET"
            and request.url
            == f"{integration_env.api_base_url}/api/workspaces/{workspace_id}/snapshot"
        )
    ):
        workspace_card.locator(".project-title-btn").click()
    expect(page.locator("#project-view")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    expect(page.locator("#project-view-title")).to_contain_text(
        workspace_id,
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator(".workspace-tree-root")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)

    with page.expect_request(
        lambda request: (
            request.method == "GET"
            and request.url.startswith(
                f"{integration_env.api_base_url}/api/workspaces/{workspace_id}/tree?"
            )
        )
    ):
        page.locator('.workspace-tree-toggle[data-tree-toggle-path="docs"]').click()
    expect(
        page.locator('.workspace-tree-file[data-tree-file-path="docs/guide.md"]')
    ).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    page.locator("#project-view-close").click()
    expect(page.locator("#project-view")).to_be_hidden(timeout=_WAIT_TIMEOUT_MS)

    with page.expect_request(
        lambda request: (
            request.method == "DELETE"
            and request.url
            == f"{integration_env.api_base_url}/api/workspaces/{workspace_id}"
        )
    ):
        workspace_card.locator(".project-options-btn").click(force=True)
        expect(workspace_card.locator(".project-remove-workspace-btn")).to_be_visible(
            timeout=_WAIT_TIMEOUT_MS
        )
        workspace_card.locator(".project-remove-workspace-btn").click()
        expect(page.locator('[role="alertdialog"]')).to_be_visible(
            timeout=_WAIT_TIMEOUT_MS
        )
        page.locator("[data-feedback-confirm]").click()
    expect(_project_card(page, workspace_id)).to_have_count(0, timeout=_WAIT_TIMEOUT_MS)

    automation_name = f"Browser Automation {uuid4().hex[:6]}"
    automation_prompt = "Summarize the repo status every morning."
    with page.expect_request(
        lambda request: (
            request.method == "POST"
            and request.url == f"{integration_env.api_base_url}/api/automation/projects"
        )
    ) as create_automation_request_info:
        page.locator(".projects-toolbar-new-automation-btn").click()
        expect(page.locator('[data-feedback-form-input="display_name"]')).to_be_visible(
            timeout=_WAIT_TIMEOUT_MS
        )
        expect(
            page.locator(
                '[data-feedback-form-input="workspace_id"] option[value="default"]'
            )
        ).to_have_count(1, timeout=_WAIT_TIMEOUT_MS)
        page.locator('[data-feedback-form-input="display_name"]').fill(automation_name)
        page.locator('[data-feedback-form-input="workspace_id"]').select_option(
            "default"
        )
        page.locator('[data-feedback-form-input="prompt"]').fill(automation_prompt)
        page.locator('[data-feedback-form-input="cron_expression"]').fill("0 9 * * *")
        page.locator("[data-feedback-confirm]").click()
    create_automation_payload = json.loads(
        create_automation_request_info.value.post_data or "{}"
    )
    assert create_automation_payload["display_name"] == automation_name
    assert create_automation_payload["workspace_id"] == "default"
    assert create_automation_payload["prompt"] == automation_prompt
    expect(_project_card(page, automation_name, automation=True)).to_be_visible(
        timeout=_WAIT_TIMEOUT_MS
    )

    automation_card = _project_card(page, automation_name, automation=True)
    with page.expect_request(
        lambda request: (
            request.method == "GET"
            and "/api/automation/projects/" in request.url
            and not request.url.endswith("/sessions")
        )
    ):
        automation_card.locator(".project-title-btn").click()
    expect(page.locator("#project-view")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    expect(page.locator("#project-view-title")).to_contain_text(
        automation_name,
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator(".automation-prompt-card")).to_contain_text(
        automation_prompt,
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("[data-automation-toggle]")).to_have_count(
        1,
        timeout=_WAIT_TIMEOUT_MS,
    )

    with page.expect_request(
        lambda request: (
            request.method == "POST"
            and "/api/automation/projects/" in request.url
            and request.url.endswith(":disable")
        )
    ):
        page.locator("[data-automation-toggle]").evaluate("(button) => button.click()")
    expect(page.locator("[data-automation-toggle]")).to_contain_text(
        re.compile(r"(Enable|启用)"),
        timeout=_WAIT_TIMEOUT_MS,
    )

    with page.expect_request(
        lambda request: (
            request.method == "POST"
            and "/api/automation/projects/" in request.url
            and request.url.endswith(":enable")
        )
    ):
        page.locator("[data-automation-toggle]").evaluate("(button) => button.click()")

    session_ids_before_run = set(_session_ids(page))
    with page.expect_request(
        lambda request: (
            request.method == "POST"
            and "/api/automation/projects/" in request.url
            and request.url.endswith(":run")
        )
    ):
        page.locator("[data-automation-run]").click()
    new_session_id = _wait_for_new_session_id(page, session_ids_before_run)
    expect(page.locator(".session-item.active")).to_have_attribute(
        "data-session-id",
        new_session_id,
        timeout=_WAIT_TIMEOUT_MS,
    )

    automation_card = _project_card(page, automation_name, automation=True)
    with page.expect_request(
        lambda request: (
            request.method == "DELETE" and "/api/automation/projects/" in request.url
        )
    ):
        automation_card.locator(".project-options-btn").click(force=True)
        expect(automation_card.locator(".project-remove-automation-btn")).to_be_visible(
            timeout=_WAIT_TIMEOUT_MS
        )
        automation_card.locator(".project-remove-automation-btn").click()
        expect(page.locator('[role="alertdialog"]')).to_be_visible(
            timeout=_WAIT_TIMEOUT_MS
        )
        page.locator("[data-feedback-confirm]").click()
    expect(_project_card(page, automation_name, automation=True)).to_have_count(
        0,
        timeout=_WAIT_TIMEOUT_MS,
    )


def _open_app(page: Page, integration_env: IntegrationEnvironment) -> None:
    page.goto(integration_env.api_base_url, wait_until="domcontentloaded")
    expect(page.locator("#backend-status-label")).to_contain_text(
        _CONNECTED_LABEL,
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#projects-list")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)


def _create_session_via_sidebar(page: Page) -> str:
    existing_session_ids = set(_session_ids(page))
    with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.endswith("/api/sessions")
            and response.ok
        ),
        timeout=_WAIT_TIMEOUT_MS,
    ) as response_info:
        page.locator(".project-new-session-btn").first.click(force=True)

    response_payload = response_info.value.json()
    session_id = (
        str(response_payload.get("session_id") or "").strip()
        if isinstance(response_payload, dict)
        else ""
    )
    if not session_id:
        session_id = _wait_for_new_session_id(page, existing_session_ids)
    else:
        expect(
            page.locator(f'.session-item[data-session-id="{session_id}"]')
        ).to_have_count(1, timeout=_WAIT_TIMEOUT_MS)
    expect(page.locator(".session-item.active")).to_have_attribute(
        "data-session-id",
        session_id,
        timeout=_WAIT_TIMEOUT_MS,
    )
    return session_id


def _wait_for_new_session_id(page: Page, existing_session_ids: set[str]) -> str:
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        current_session_ids = _session_ids(page)
        new_session_ids = [
            session_id
            for session_id in current_session_ids
            if session_id not in existing_session_ids
        ]
        if len(new_session_ids) == 1:
            return new_session_ids[0]
        page.wait_for_timeout(200)
    raise AssertionError("Timed out waiting for a new session to appear in the UI.")


def _wait_for_open_tool_approvals(
    client: httpx.Client,
    *,
    run_id: str,
    expected_count: int,
    timeout_seconds: float = 15.0,
) -> list[dict[str, str]]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}/tool-approvals")
        response.raise_for_status()
        approvals = response.json()
        if not isinstance(approvals, list):
            raise AssertionError(f"Invalid tool approvals response: {approvals}")
        if len(approvals) == expected_count:
            return approvals
        time.sleep(0.2)
    raise AssertionError(
        f"Timed out waiting for {expected_count} tool approvals for run {run_id}."
    )


def _set_checkbox(page: Page, selector: str, checked: bool) -> None:
    page.locator(selector).evaluate(
        """(input, nextChecked) => {
            input.checked = nextChecked;
            input.dispatchEvent(new Event('change', { bubbles: true }));
        }""",
        checked,
    )


def _session_ids(page: Page) -> list[str]:
    raw_session_ids = page.locator(".session-item").evaluate_all(
        "elements => elements.map(element => element.getAttribute('data-session-id') || '')"
    )
    return [
        str(session_id).strip()
        for session_id in raw_session_ids
        if str(session_id).strip()
    ]


def _html_lang(page: Page) -> str:
    return str(
        page.locator("html").evaluate("element => element.getAttribute('lang') || ''")
    ).strip()


def _body_background(page: Page) -> str:
    return str(
        page.locator("body").evaluate(
            "element => getComputedStyle(element).backgroundColor"
        )
    ).strip()


def _project_card(page: Page, label: str, *, automation: bool = False):
    selector = (
        ".automation-project-card"
        if automation
        else ".project-card:not(.automation-project-card)"
    )
    return page.locator(selector).filter(has_text=label).first


def _emit_gateway_observability_probe() -> None:
    previous_computer_runtime = os.environ.get("AGENT_TEAMS_COMPUTER_RUNTIME")
    os.environ["AGENT_TEAMS_COMPUTER_RUNTIME"] = "fake"
    try:
        runtime = _build_acp_stdio_runtime()
        server = cast(AcpGatewayServer, getattr(runtime, "_server"))

        async def discard_notify(_message: dict[str, JsonValue]) -> None:
            return None

        server.set_notify(discard_notify)
        failure: list[BaseException] = []

        def runner() -> None:
            try:
                asyncio.run(_run_gateway_observability_probe(server))
            except BaseException as exc:  # pragma: no cover - re-raised below
                failure.append(exc)

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join(timeout=30.0)
        if thread.is_alive():
            raise AssertionError(
                "Timed out while emitting gateway observability probe."
            )
        if failure:
            raise failure[0]
    finally:
        if previous_computer_runtime is None:
            os.environ.pop("AGENT_TEAMS_COMPUTER_RUNTIME", None)
        else:
            os.environ["AGENT_TEAMS_COMPUTER_RUNTIME"] = previous_computer_runtime


async def _run_gateway_observability_probe(server: AcpGatewayServer) -> None:
    initialize_response = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": 2},
        },
        request_context=_AcpRequestContext(
            cold_start=True,
            framed_input=False,
            runtime_uptime_ms=0,
        ),
    )
    assert isinstance(initialize_response, dict)
    initialize_result = initialize_response.get("result")
    assert isinstance(initialize_result, dict)

    session_response = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/new",
            "params": {
                "cwd": str(Path(__file__).resolve().parents[3]),
            },
        },
        request_context=_AcpRequestContext(
            cold_start=False,
            framed_input=False,
            runtime_uptime_ms=1,
        ),
    )
    assert isinstance(session_response, dict)
    session_result = session_response.get("result")
    assert isinstance(session_result, dict)
    session_id = session_result.get("sessionId")
    assert isinstance(session_id, str)
    assert session_id.strip()

    prompt_response = await server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "session/prompt",
            "params": {
                "sessionId": session_id,
                "prompt": [
                    {
                        "type": "text",
                        "text": "请用一句话确认 gateway observability 浏览器视图已接通。",
                    }
                ],
            },
        },
        request_context=_AcpRequestContext(
            cold_start=False,
            framed_input=False,
            runtime_uptime_ms=2,
        ),
    )
    assert isinstance(prompt_response, dict)
    prompt_result = prompt_response.get("result")
    assert isinstance(prompt_result, dict)
    assert prompt_result.get("runStatus") == "completed"


def _resolve_playwright_browser_root() -> Path:
    candidates: list[Path] = []

    configured_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if configured_root:
        candidates.append(Path(configured_root).expanduser())
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data).expanduser() / "ms-playwright")
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        candidates.append(
            Path(user_profile).expanduser() / "AppData" / "Local" / "ms-playwright"
        )

    try:
        import pwd

        candidates.append(
            Path(pwd.getpwuid(os.getuid()).pw_dir) / ".cache" / "ms-playwright"
        )
    except (ImportError, KeyError, OSError):
        pass

    for candidate in candidates:
        if any(candidate.glob("chromium-*")):
            return candidate

    raise AssertionError("Playwright browser cache was not found on this machine.")
