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
from relay_teams.env.web_config_models import (
    DEFAULT_SEARXNG_INSTANCE_SEEDS,
    DEFAULT_SEARXNG_INSTANCE_URL,
)
from relay_teams.gateway.acp_stdio import AcpGatewayServer, _AcpRequestContext
from relay_teams.interfaces.cli.gateway_cli import _build_acp_stdio_runtime
from pydantic import JsonValue
from playwright.sync_api import Locator, Page
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
_REMOTE_WORKSPACE_LABEL = re.compile(r"(Remote Workspace|远端工作区)")
_LANG_PATTERN = re.compile(r"^(en|en-US|zh-CN)$")
_VIEWPORT_WIDTH = 1600
_VIEWPORT_HEIGHT = 1200
_WAIT_TIMEOUT_MS = 30_000
_ROW_ALIGNMENT_TOLERANCE_PX = 9.0


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


@pytest.mark.skip(reason="Flaky on CI - timing issues with browser automation")
def test_browser_run_flow_uses_canonical_input_payload(
    browser_page: Page,
    integration_env: IntegrationEnvironment,
) -> None:
    page = browser_page
    _open_app(page, integration_env)

    session_id = _create_session_via_sidebar(page)
    prompt = "请用一句话确认当前系统可正常响应。"

    expect(page.locator("#prompt-input")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
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
    expect(round_section).to_contain_text(prompt, timeout=90_000)
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


@pytest.mark.skip(reason="Flaky on CI - timing issues with browser automation")
def test_browser_webfetch_approval_reuses_host_scoped_ticket(
    browser_page: Page,
    integration_env: IntegrationEnvironment,
    api_client: httpx.Client,
) -> None:
    page = browser_page
    _open_app(page, integration_env)

    session_id = _create_session_via_sidebar(page)
    expect(page.locator("#yolo-toggle")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    _set_checkbox(page, "#yolo-toggle", False)
    prompt = (
        "[webfetch-approval-validation] 连续两次调用同一个 host 的 webfetch，"
        "只在第一次审批。"
    )

    expect(page.locator("#prompt-input")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    with page.expect_request(
        lambda request: (
            request.method == "POST"
            and request.url == f"{integration_env.api_base_url}/api/runs"
        )
    ) as run_request_info:
        page.locator("#prompt-input").fill(prompt)
        page.locator("#send-btn").click()

    run_request_payload = json.loads(run_request_info.value.post_data or "{}")
    assert run_request_payload["session_id"] == session_id
    assert run_request_payload["yolo"] is False
    assert run_request_payload["input"] == [{"kind": "text", "text": prompt}]

    run_id = _wait_for_run_id(api_client, session_id)

    approvals = _wait_for_open_tool_approvals(
        api_client,
        run_id=run_id,
        expected_count=1,
    )
    assert approvals[0]["tool_call_id"] == "call-webfetch-1"
    assert approvals[0]["tool_name"] == "webfetch"
    assert "https://localhost/one" in approvals[0]["args_preview"]

    approval_items = page.locator(".recovery-approval-card")
    expect(approval_items).to_have_count(1, timeout=_WAIT_TIMEOUT_MS)
    expect(page.locator("#recovery-approval-host")).to_be_visible(
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


def test_browser_ask_question_recovery_card_submits_answers(
    browser_page: Page,
    integration_env: IntegrationEnvironment,
    api_client: httpx.Client,
) -> None:
    page = browser_page
    _open_app(page, integration_env)

    session_id = _create_session_via_sidebar(page)
    prompt = "[ask-question-validation] 用 ask_question 收集标签和备注。"

    expect(page.locator("#prompt-input")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    with page.expect_request(
        lambda request: (
            request.method == "POST"
            and request.url == f"{integration_env.api_base_url}/api/runs"
        )
    ) as run_request_info:
        page.locator("#prompt-input").fill(prompt)
        page.locator("#send-btn").click()

    run_request_payload = json.loads(run_request_info.value.post_data or "{}")
    assert run_request_payload["session_id"] == session_id
    assert run_request_payload["input"] == [{"kind": "text", "text": prompt}]

    run_id = _wait_for_run_id(api_client, session_id)
    questions = _wait_for_open_user_questions(
        api_client,
        run_id=run_id,
        expected_count=1,
    )
    assert questions[0]["question_id"] == "call-question-1"

    question_cards = page.locator(".recovery-question-card")
    expect(question_cards).to_have_count(1, timeout=_WAIT_TIMEOUT_MS)
    expect(page.locator("#recovery-question-host")).to_be_visible(
        timeout=_WAIT_TIMEOUT_MS
    )
    expect(question_cards.first).to_contain_text(
        "Pick the labels to apply",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(question_cards.first).to_contain_text("Pick the handoff mode")
    expect(question_cards.first).to_contain_text("以上都不是")
    docs_supplement_selector = (
        '[data-user-question-answer="supplement"]'
        '[data-question-id="call-question-1"]'
        '[data-prompt-index="0"]'
        '[data-option-label="Docs"]'
    )
    fallback_supplement_selector = (
        '[data-user-question-answer="supplement"]'
        '[data-question-id="call-question-1"]'
        '[data-prompt-index="1"]'
        '[data-option-label="__none_of_the_above__"]'
    )

    page.locator(
        '[data-user-question-answer="option"][data-question-id="call-question-1"][data-option-label="Ship"]'
    ).check()
    page.locator(
        '[data-user-question-answer="option"][data-question-id="call-question-1"][data-option-label="Docs"]'
    ).check()
    docs_supplement = page.locator(docs_supplement_selector)
    assert docs_supplement.evaluate("el => el.tagName") == "INPUT"
    docs_supplement.fill("Ship code now, docs follow immediately.")
    docs_supplement.focus()
    page.evaluate(
        """selector => {
            const input = document.querySelector(selector);
            if (input) {
                input.dispatchEvent(new CompositionEvent('compositionstart', { data: '测' }));
            }
        }""",
        docs_supplement_selector,
    )
    with page.expect_response(
        lambda response: (
            response.request.method == "GET"
            and response.url
            == f"{integration_env.api_base_url}/api/sessions/{session_id}/recovery"
        )
    ):
        page.evaluate("window.dispatchEvent(new Event('focus'))")
    expect(docs_supplement).to_be_focused(timeout=_WAIT_TIMEOUT_MS)
    assert docs_supplement.input_value() == "Ship code now, docs follow immediately."
    page.evaluate(
        """selector => {
            const input = document.querySelector(selector);
            if (input) {
                input.dispatchEvent(new CompositionEvent('compositionend', { data: '试' }));
            }
        }""",
        docs_supplement_selector,
    )
    page.locator(
        '[data-user-question-answer="option"][data-question-id="call-question-1"][data-option-label="__none_of_the_above__"][name="question-call-question-1-1"]'
    ).check()
    fallback_supplement = page.locator(fallback_supplement_selector)
    assert fallback_supplement.evaluate("el => el.tagName") == "INPUT"
    fallback_supplement.fill("Ship now, docs follow immediately.")

    with page.expect_request(
        lambda request: (
            request.method == "POST"
            and request.url
            == (
                f"{integration_env.api_base_url}/api/runs/{run_id}/questions/"
                "call-question-1:answer"
            )
        )
    ) as answer_request_info:
        page.locator('[data-user-question-submit="true"]').click()

    answer_payload = json.loads(answer_request_info.value.post_data or "{}")
    assert answer_payload == {
        "answers": [
            {
                "selections": [
                    {"label": "Ship"},
                    {
                        "label": "Docs",
                        "supplement": "Ship code now, docs follow immediately.",
                    },
                ]
            },
            {
                "selections": [
                    {
                        "label": "__none_of_the_above__",
                        "supplement": "Ship now, docs follow immediately.",
                    }
                ]
            },
        ]
    }

    round_section = page.locator(f'.session-round-section[data-run-id="{run_id}"]')
    expect(round_section).to_contain_text(
        "[fake-llm] Ask question validation completed after collecting labels and a handoff note.",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(question_cards).to_have_count(0, timeout=_WAIT_TIMEOUT_MS)

    remaining_questions = _wait_for_open_user_questions(
        api_client,
        run_id=run_id,
        expected_count=0,
    )
    assert remaining_questions == []


def test_browser_shell_settings_and_session_management(
    browser_page: Page,
    integration_env: IntegrationEnvironment,
) -> None:
    page = browser_page
    _emit_gateway_observability_probe()
    _open_app(page, integration_env)

    baseline_session_ids = _wait_for_session_ids_snapshot(page)
    session_id = _create_session_via_sidebar(page)
    renamed_title = "Browser Smoke Session"

    with page.expect_request(
        lambda request: (
            request.method == "PATCH"
            and request.url
            == f"{integration_env.api_base_url}/api/sessions/{session_id}"
        )
    ):
        page.locator(f'.session-item[data-session-id="{session_id}"]').hover()
        rename_button = page.locator(
            f'.session-rename-btn[data-session-id="{session_id}"]'
        )
        expect(rename_button).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
        rename_button.click()
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
        page.locator("#language-toggle-btn").evaluate("(button) => button.click()")
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
        "mcp",
        "agents",
        "roles",
        "orchestration",
        "notifications",
        "web",
        "proxy",
        "environment",
    ):
        page.locator(f'.settings-tab[data-tab="{tab_name}"]').click()
        expect(page.locator(f"#{tab_name}-panel")).to_be_visible(
            timeout=_WAIT_TIMEOUT_MS
        )
    expect(page.locator('.settings-tab[data-tab="skills"]')).to_have_count(0)
    expect(page.locator('.settings-tab[data-tab="triggers"]')).to_have_count(0)

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

    page.locator("#settings-close").click()
    expect(page.locator("#settings-modal")).to_be_hidden(timeout=_WAIT_TIMEOUT_MS)

    page.locator('.home-feature-item[data-feature-id="skills"]').click()
    expect(page.locator("#project-view")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    expect(
        page.locator(".project-view-toolbar-actions [data-project-view-reload]")
    ).to_have_count(
        0,
        timeout=_WAIT_TIMEOUT_MS,
    )

    feishu_name = f"feishu-main-{uuid4().hex[:6]}"
    with page.expect_request(
        lambda request: (
            request.method == "POST"
            and request.url
            == f"{integration_env.api_base_url}/api/gateway/feishu/accounts"
        )
    ) as create_feishu_request_info:
        page.locator('.home-feature-item[data-feature-id="gateway"]').click()
        expect(page.locator("#project-view-title")).to_contain_text(
            re.compile(r"(IM Gateway|IM 接入)"),
            timeout=_WAIT_TIMEOUT_MS,
        )
        page.locator("[data-feature-gateway-add-feishu]").click()
        expect(page.locator("#feishu-trigger-name-input")).to_be_visible(
            timeout=_WAIT_TIMEOUT_MS
        )
        page.locator("#feishu-trigger-name-input").fill(feishu_name)
        page.locator("#feishu-display-name-input").fill("Feishu Main")
        page.locator("#feishu-app-name-input").fill("Agent Teams Bot")
        page.locator("#feishu-app-id-input").fill("cli_test")
        page.locator("#feishu-app-secret-input").fill("secret_test")
        page.locator("[data-feature-feishu-save]").click()
    create_feishu_payload = json.loads(
        create_feishu_request_info.value.post_data or "{}"
    )
    assert create_feishu_payload["name"] == feishu_name
    assert create_feishu_payload["source_config"]["provider"] == "feishu"
    assert create_feishu_payload["source_config"]["app_id"] == "cli_test"
    expect(page.locator("#feishu-trigger-name-input")).to_have_count(
        0,
        timeout=_WAIT_TIMEOUT_MS,
    )

    page.locator("#settings-btn").click()
    expect(page.locator("#settings-modal")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
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
    page.locator("#project-view-close").click()
    expect(page.locator("#project-view")).to_be_hidden(timeout=_WAIT_TIMEOUT_MS)
    page.locator(f'.session-item[data-session-id="{session_id}"]').click()

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
    assert _wait_for_session_ids_snapshot(page) == baseline_session_ids


def test_browser_model_profile_custom_provider_keeps_manual_base_url(
    browser_page: Page,
    integration_env: IntegrationEnvironment,
) -> None:
    page = browser_page
    _open_app(page, integration_env)

    page.locator("#settings-btn").click()
    expect(page.locator("#settings-modal")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    with page.expect_response(
        lambda response: (
            response.request.method == "GET"
            and response.url
            == f"{integration_env.api_base_url}/api/system/configs/model/profiles"
            and response.ok
        )
    ):
        page.locator('.settings-tab[data-tab="model"]').click()
    expect(page.locator("#model-panel")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    page.locator(".edit-profile-btn").first.click()
    expect(page.locator("#profile-editor")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    expect(page.locator("#profile-base-url")).to_have_value(
        integration_env.fake_llm_v1_base_url,
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#profile-provider")).to_be_hidden(timeout=_WAIT_TIMEOUT_MS)
    expect(page.locator('#profile-provider option[value="bigmodel"]')).to_have_count(0)

    page.locator("#profile-provider-custom-btn").click()

    expect(page.locator("#profile-base-url")).to_have_value(
        integration_env.fake_llm_v1_base_url,
        timeout=_WAIT_TIMEOUT_MS,
    )


def test_browser_settings_modal_does_not_close_after_dragging_out_of_content(
    browser_page: Page,
    integration_env: IntegrationEnvironment,
) -> None:
    page = browser_page
    _open_app(page, integration_env)

    page.locator("#settings-btn").click()
    settings_modal = page.locator("#settings-modal")
    settings_content = page.locator(".settings-modal-content")
    expect(settings_modal).to_be_visible(timeout=_WAIT_TIMEOUT_MS)

    content_box = settings_content.bounding_box()
    modal_box = settings_modal.bounding_box()
    assert content_box is not None
    assert modal_box is not None

    start_x = content_box["x"] + content_box["width"] / 2
    start_y = content_box["y"] + content_box["height"] / 2
    end_x = modal_box["x"] + 8
    end_y = modal_box["y"] + 8

    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move(end_x, end_y)
    page.mouse.up()

    expect(settings_modal).to_be_visible(timeout=_WAIT_TIMEOUT_MS)

    page.mouse.click(end_x, end_y)
    expect(settings_modal).to_be_hidden(timeout=_WAIT_TIMEOUT_MS)


def test_browser_remote_workspace_settings_group_ssh_fields(
    browser_page: Page,
    integration_env: IntegrationEnvironment,
) -> None:
    page = browser_page
    _open_app(page, integration_env)
    _open_workspace_settings_panel(page, integration_env)

    expect(page.locator('.settings-tab[data-tab="workspace"]')).to_contain_text(
        _REMOTE_WORKSPACE_LABEL,
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#settings-panel-title")).to_contain_text(
        _REMOTE_WORKSPACE_LABEL,
        timeout=_WAIT_TIMEOUT_MS,
    )

    page.locator("#add-ssh-profile-btn").click()
    expect(page.locator("#workspace-ssh-profile-editor")).to_be_visible(
        timeout=_WAIT_TIMEOUT_MS
    )
    expect(
        page.locator(".workspace-auth-grid #workspace-ssh-profile-username")
    ).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    auth_title = page.locator(".profile-editor-subsection-header h5")
    auth_copy = page.locator(".profile-editor-subsection-header p").first
    auth_system_copy = page.locator(".profile-editor-subsection-header p").nth(1)
    _assert_locator_below(
        auth_title,
        auth_copy,
    )
    _assert_locator_below(
        auth_copy,
        auth_system_copy,
    )
    _assert_locators_share_left_edge(
        auth_title,
        auth_copy,
        auth_system_copy,
    )
    expect(auth_system_copy).to_contain_text("系统 SSH 认证材料")
    expect(page.locator("#workspace-ssh-profile-private-key-name")).to_have_attribute(
        "type",
        "hidden",
        timeout=_WAIT_TIMEOUT_MS,
    )
    private_key_name_label = page.locator(
        'label[for="workspace-ssh-profile-private-key-name"]'
    )
    expect(private_key_name_label).to_have_count(0, timeout=_WAIT_TIMEOUT_MS)

    _assert_locators_share_row(
        page.locator("#workspace-ssh-profile-id"),
        page.locator("#workspace-ssh-profile-host"),
        page.locator("#workspace-ssh-profile-port"),
    )
    _assert_locators_share_row(
        page.locator("#workspace-ssh-profile-shell"),
        page.locator("#workspace-ssh-profile-timeout"),
    )
    _assert_locators_share_row(
        page.locator("#workspace-ssh-profile-username"),
        page.locator("#workspace-ssh-profile-password"),
    )
    _assert_locators_share_left_edge(
        page.locator("#workspace-ssh-profile-username"),
        page.locator("#workspace-ssh-profile-private-key"),
    )
    private_key_label_row = page.locator(".workspace-private-key-label-row")
    private_key_field = page.locator("#workspace-ssh-profile-private-key")
    _assert_locator_below(
        private_key_label_row,
        private_key_field,
    )
    assert _vertical_gap_between(private_key_label_row, private_key_field) < 12.0
    _assert_locators_share_right_edge(
        page.locator("#workspace-ssh-profile-private-key"),
        page.locator("#workspace-ssh-profile-import-private-key-btn"),
    )


@pytest.mark.skip(reason="Flaky on CI - timing issues with browser automation")
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

    expect(page.locator("#settings-btn")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
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
    initial_web_exa_api_key = str(web_response.json().get("exa_api_key") or "")
    initial_web_fallback_provider = str(
        web_response.json().get("fallback_provider") or ""
    )
    initial_web_searxng_instance_url = str(
        web_response.json().get("searxng_instance_url") or ""
    )

    proxy_response = api_client.get("/api/system/configs/proxy")
    proxy_response.raise_for_status()
    initial_proxy_payload = proxy_response.json()
    initial_proxy_http = str(initial_proxy_payload.get("http_proxy") or "")

    notification_enabled_id = "notif-run_stopped-enabled"
    notification_browser_id = "notif-run_stopped-browser"
    web_exa_api_key = f"browser-web-exa-{uuid4().hex[:8]}"
    web_searxng_instance_url = "https://search.example.test/"
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
    page.locator("#web-provider").select_option("exa")
    if initial_web_exa_api_key:
        expect(page.locator("#web-api-key")).to_have_value("", timeout=_WAIT_TIMEOUT_MS)
        expect(page.locator("#web-api-key")).to_have_attribute(
            "placeholder",
            "************",
            timeout=_WAIT_TIMEOUT_MS,
        )
    else:
        expect(page.locator("#web-api-key")).to_have_value(
            "",
            timeout=_WAIT_TIMEOUT_MS,
        )
    expect(page.locator("#web-fallback-provider")).to_have_value(
        initial_web_fallback_provider,
        timeout=_WAIT_TIMEOUT_MS,
    )
    if initial_web_fallback_provider == "searxng":
        expect(page.locator("#web-searxng-instance-url-field")).to_be_visible(
            timeout=_WAIT_TIMEOUT_MS
        )
        expect(page.locator("#web-searxng-builtins-field")).to_be_visible(
            timeout=_WAIT_TIMEOUT_MS
        )
        expect(page.locator("#web-searxng-instance-url")).to_have_value(
            initial_web_searxng_instance_url,
            timeout=_WAIT_TIMEOUT_MS,
        )
    else:
        expect(page.locator("#web-searxng-instance-url-field")).to_be_hidden(
            timeout=_WAIT_TIMEOUT_MS
        )
        expect(page.locator("#web-searxng-builtins-field")).to_be_hidden(
            timeout=_WAIT_TIMEOUT_MS
        )

    page.locator("#web-api-key").fill(web_exa_api_key)
    page.locator("#web-fallback-provider").select_option("searxng")
    expect(page.locator("#web-searxng-instance-url-field")).to_be_visible(
        timeout=_WAIT_TIMEOUT_MS
    )
    expect(page.locator("#web-searxng-builtins-field")).to_be_visible(
        timeout=_WAIT_TIMEOUT_MS
    )
    expect(page.locator("#web-searxng-builtins-list")).to_contain_text(
        "https://search.mdosch.de/",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#web-searxng-builtins-list")).to_contain_text(
        "https://search.seddens.net/",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#web-searxng-builtins-list")).to_contain_text(
        "https://search.wdpserver.com/",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#web-searxng-instance-url")).to_have_value(
        initial_web_searxng_instance_url or DEFAULT_SEARXNG_INSTANCE_URL,
        timeout=_WAIT_TIMEOUT_MS,
    )
    page.locator("#web-searxng-instance-url").fill(web_searxng_instance_url)
    with page.expect_request(
        lambda request: (
            request.method == "PUT"
            and request.url == f"{integration_env.api_base_url}/api/system/configs/web"
        )
    ) as save_web_request_info:
        page.locator("#save-web-btn").click()
    web_payload = json.loads(save_web_request_info.value.post_data or "{}")
    assert web_payload == {
        "provider": "exa",
        "exa_api_key": web_exa_api_key,
        "fallback_provider": "searxng",
        "searxng_instance_url": web_searxng_instance_url,
    }
    expect(page.locator("#web-api-key")).to_have_value("", timeout=_WAIT_TIMEOUT_MS)
    expect(page.locator("#web-api-key")).to_have_attribute(
        "placeholder",
        "************",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#web-fallback-provider")).to_have_value(
        "searxng",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#web-searxng-instance-url-field")).to_be_visible(
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#web-searxng-builtins-field")).to_be_visible(
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#web-searxng-instance-url")).to_have_value(
        web_searxng_instance_url,
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
        "proxy_password": None,
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
    page.locator("#agent-stdio-args-input").fill("-m\nrelay_teams")
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
            "args": ["-m", "relay_teams"],
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


def test_browser_web_settings_complex_fallback_roundtrip(
    browser_page: Page,
    integration_env: IntegrationEnvironment,
    api_client: httpx.Client,
) -> None:
    page = browser_page
    web_exa_api_key = f"browser-web-complex-{uuid4().hex[:8]}"
    custom_searxng_instance_url = "https://complex-search.example.test/"

    reset_response = api_client.put(
        "/api/system/configs/web",
        json={
            "provider": "exa",
            "exa_api_key": web_exa_api_key,
            "fallback_provider": "searxng",
            "searxng_instance_url": DEFAULT_SEARXNG_INSTANCE_URL,
        },
    )
    reset_response.raise_for_status()

    _open_app(page, integration_env)
    _open_web_settings_panel(page, integration_env)

    expect(page.locator("#web-provider")).to_have_value("exa", timeout=_WAIT_TIMEOUT_MS)
    expect(page.locator("#web-fallback-provider")).to_have_value(
        "searxng",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#web-api-key")).to_have_value("", timeout=_WAIT_TIMEOUT_MS)
    expect(page.locator("#web-api-key")).to_have_attribute(
        "placeholder",
        "************",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#web-searxng-instance-url-field")).to_be_visible(
        timeout=_WAIT_TIMEOUT_MS
    )
    expect(page.locator("#web-searxng-builtins-field")).to_be_visible(
        timeout=_WAIT_TIMEOUT_MS
    )
    expect(page.locator("#web-searxng-instance-url")).to_have_value(
        DEFAULT_SEARXNG_INSTANCE_URL,
        timeout=_WAIT_TIMEOUT_MS,
    )
    _assert_builtin_searxng_instances(page)

    page.locator("#web-searxng-instance-url").fill(custom_searxng_instance_url)
    with page.expect_request(
        lambda request: (
            request.method == "PUT"
            and request.url == f"{integration_env.api_base_url}/api/system/configs/web"
        )
    ) as save_with_fallback_request_info:
        page.locator("#save-web-btn").click()
    save_with_fallback_payload = json.loads(
        save_with_fallback_request_info.value.post_data or "{}"
    )
    assert save_with_fallback_payload == {
        "provider": "exa",
        "exa_api_key": web_exa_api_key,
        "fallback_provider": "searxng",
        "searxng_instance_url": custom_searxng_instance_url,
    }

    page.reload(wait_until="domcontentloaded")
    _open_app(page, integration_env)
    _open_web_settings_panel(page, integration_env)

    expect(page.locator("#web-provider")).to_have_value("exa", timeout=_WAIT_TIMEOUT_MS)
    expect(page.locator("#web-fallback-provider")).to_have_value(
        "searxng",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#web-api-key")).to_have_value("", timeout=_WAIT_TIMEOUT_MS)
    expect(page.locator("#web-api-key")).to_have_attribute(
        "placeholder",
        "************",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#web-searxng-instance-url-field")).to_be_visible(
        timeout=_WAIT_TIMEOUT_MS
    )
    expect(page.locator("#web-searxng-builtins-field")).to_be_visible(
        timeout=_WAIT_TIMEOUT_MS
    )
    expect(page.locator("#web-searxng-instance-url")).to_have_value(
        custom_searxng_instance_url,
        timeout=_WAIT_TIMEOUT_MS,
    )
    _assert_builtin_searxng_instances(page)

    page.locator("#web-fallback-provider").select_option("disabled")
    expect(page.locator("#web-searxng-instance-url-field")).to_be_hidden(
        timeout=_WAIT_TIMEOUT_MS
    )
    expect(page.locator("#web-searxng-builtins-field")).to_be_hidden(
        timeout=_WAIT_TIMEOUT_MS
    )
    with page.expect_request(
        lambda request: (
            request.method == "PUT"
            and request.url == f"{integration_env.api_base_url}/api/system/configs/web"
        )
    ) as save_without_fallback_request_info:
        page.locator("#save-web-btn").click()
    save_without_fallback_payload = json.loads(
        save_without_fallback_request_info.value.post_data or "{}"
    )
    assert save_without_fallback_payload == {
        "provider": "exa",
        "exa_api_key": web_exa_api_key,
        "fallback_provider": "disabled",
        "searxng_instance_url": custom_searxng_instance_url,
    }

    page.reload(wait_until="domcontentloaded")
    _open_app(page, integration_env)
    _open_web_settings_panel(page, integration_env)

    expect(page.locator("#web-provider")).to_have_value("exa", timeout=_WAIT_TIMEOUT_MS)
    expect(page.locator("#web-fallback-provider")).to_have_value(
        "disabled",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#web-searxng-instance-url-field")).to_be_hidden(
        timeout=_WAIT_TIMEOUT_MS
    )
    expect(page.locator("#web-searxng-builtins-field")).to_be_hidden(
        timeout=_WAIT_TIMEOUT_MS
    )

    page.locator("#web-fallback-provider").select_option("searxng")
    expect(page.locator("#web-searxng-instance-url-field")).to_be_visible(
        timeout=_WAIT_TIMEOUT_MS
    )
    expect(page.locator("#web-searxng-builtins-field")).to_be_visible(
        timeout=_WAIT_TIMEOUT_MS
    )
    expect(page.locator("#web-searxng-instance-url")).to_have_value(
        custom_searxng_instance_url,
        timeout=_WAIT_TIMEOUT_MS,
    )
    _assert_builtin_searxng_instances(page)


def test_browser_web_settings_ui_matches_declared_defaults(
    browser_page: Page,
    integration_env: IntegrationEnvironment,
    api_client: httpx.Client,
) -> None:
    page = browser_page
    web_exa_api_key = f"browser-web-strict-{uuid4().hex[:8]}"

    language_response = api_client.put(
        "/api/system/configs/ui-language",
        json={"language": "zh-CN"},
    )
    language_response.raise_for_status()

    web_response = api_client.put(
        "/api/system/configs/web",
        json={
            "provider": "exa",
            "exa_api_key": web_exa_api_key,
            "fallback_provider": None,
            "searxng_instance_url": DEFAULT_SEARXNG_INSTANCE_URL,
        },
    )
    web_response.raise_for_status()

    _open_app(page, integration_env)
    page.wait_for_function(
        "expectedLang => document.documentElement.lang === expectedLang",
        arg="zh-CN",
        timeout=_WAIT_TIMEOUT_MS,
    )
    _open_web_settings_panel(page, integration_env)

    expect(page.locator('label[for="web-provider"]')).to_have_text(
        "提供商",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator('label[for="web-api-key"]')).to_have_text(
        "Exa API Key",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator('label[for="web-fallback-provider"]')).to_have_text(
        "回退提供商",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#web-provider-site-badge")).to_have_text(
        "Exa",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#web-provider-site-url")).to_have_text(
        "https://exa.ai",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator(".web-provider-inline-label")).to_have_text(
        "提供商网站：",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator(".web-provider-link-note")).to_have_text(
        "官方文档与账户概览",
        timeout=_WAIT_TIMEOUT_MS,
    )

    assert _select_option_pairs(page, "#web-provider") == [("exa", "Exa")]
    assert _select_option_pairs(page, "#web-fallback-provider") == [
        ("searxng", "SearXNG"),
        ("disabled", "Disabled"),
    ]

    expect(page.locator("#web-provider")).to_have_value("exa", timeout=_WAIT_TIMEOUT_MS)
    expect(page.locator("#web-fallback-provider")).to_have_value(
        "searxng",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#web-provider-site-link")).to_have_attribute(
        "href",
        re.compile(r"^https://exa\.ai/?$"),
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#web-provider-site-link")).to_have_attribute(
        "title",
        re.compile(r"^https://exa\.ai/?$"),
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#web-provider-site-link")).to_have_attribute(
        "aria-label",
        re.compile(r"^https://exa\.ai/?$"),
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#web-searxng-instance-url-field")).to_be_visible(
        timeout=_WAIT_TIMEOUT_MS
    )
    expect(page.locator("#web-searxng-builtins-field")).to_be_visible(
        timeout=_WAIT_TIMEOUT_MS
    )
    expect(page.locator("#web-searxng-instance-url")).to_be_enabled(
        timeout=_WAIT_TIMEOUT_MS
    )
    expect(page.locator("#web-searxng-instance-url")).to_have_value(
        DEFAULT_SEARXNG_INSTANCE_URL,
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#web-searxng-instance-url")).to_have_attribute(
        "placeholder",
        "默认值：https://search.mdosch.de/",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator('label[for="web-searxng-instance-url"]')).to_have_text(
        "SearXNG 实例 URL",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator(".web-searxng-builtins-label")).to_have_text(
        "内置实例",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#web-searxng-instance-url")).to_be_enabled(
        timeout=_WAIT_TIMEOUT_MS
    )
    _assert_builtin_searxng_instances(page)

    page.locator("#web-fallback-provider").select_option("disabled")
    expect(page.locator("#web-searxng-instance-url-field")).to_be_hidden(
        timeout=_WAIT_TIMEOUT_MS
    )
    expect(page.locator("#web-searxng-builtins-field")).to_be_hidden(
        timeout=_WAIT_TIMEOUT_MS
    )
    expect(page.locator("#web-searxng-instance-url")).to_be_disabled(
        timeout=_WAIT_TIMEOUT_MS
    )

    page.locator("#web-fallback-provider").select_option("searxng")
    expect(page.locator("#web-searxng-instance-url-field")).to_be_visible(
        timeout=_WAIT_TIMEOUT_MS
    )
    expect(page.locator("#web-searxng-builtins-field")).to_be_visible(
        timeout=_WAIT_TIMEOUT_MS
    )

    with page.expect_request(
        lambda request: (
            request.method == "PUT"
            and request.url
            == f"{integration_env.api_base_url}/api/system/configs/ui-language"
        )
    ) as language_request_info:
        page.locator("#language-toggle-btn").evaluate("(button) => button.click()")
    language_payload = json.loads(language_request_info.value.post_data or "{}")
    assert language_payload == {"language": "en-US"}
    page.wait_for_function(
        "expectedLang => document.documentElement.lang === expectedLang",
        arg="en-US",
        timeout=_WAIT_TIMEOUT_MS,
    )

    expect(page.locator('label[for="web-provider"]')).to_have_text(
        "Provider",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator('label[for="web-fallback-provider"]')).to_have_text(
        "Fallback Provider",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator('label[for="web-searxng-instance-url"]')).to_have_text(
        "SearXNG Instance URL",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator(".web-searxng-builtins-label")).to_have_text(
        "Built-in Instances",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator(".web-provider-inline-label")).to_have_text(
        "Provider website:",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator(".web-provider-link-note")).to_have_text(
        "Official docs and account overview",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#web-searxng-instance-url")).to_have_attribute(
        "placeholder",
        "Default: https://search.mdosch.de/",
        timeout=_WAIT_TIMEOUT_MS,
    )
    assert _select_option_pairs(page, "#web-provider") == [("exa", "Exa")]
    assert _select_option_pairs(page, "#web-fallback-provider") == [
        ("searxng", "SearXNG"),
        ("disabled", "Disabled"),
    ]
    _assert_builtin_searxng_instances(page)

    with page.expect_request(
        lambda request: (
            request.method == "PUT"
            and request.url
            == f"{integration_env.api_base_url}/api/system/configs/ui-language"
        )
    ) as reset_language_request_info:
        page.locator("#language-toggle-btn").evaluate("(button) => button.click()")
    reset_language_payload = json.loads(
        reset_language_request_info.value.post_data or "{}"
    )
    assert reset_language_payload == {"language": "zh-CN"}
    page.wait_for_function(
        "expectedLang => document.documentElement.lang === expectedLang",
        arg="zh-CN",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator('label[for="web-provider"]')).to_have_text(
        "提供商",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator(".web-searxng-builtins-label")).to_have_text(
        "内置实例",
        timeout=_WAIT_TIMEOUT_MS,
    )
    _assert_builtin_searxng_instances(page)


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
        expect(
            page.locator('[data-feedback-form-input="remove_directory"]')
        ).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
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
        page.locator('.home-feature-item[data-feature-id="automation"]').click()
        expect(page.locator("#project-view")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
        page.locator(
            ".project-view-toolbar-actions [data-feature-automation-create]"
        ).click()
        expect(page.locator("#automation-editor-display-name-input")).to_be_visible(
            timeout=_WAIT_TIMEOUT_MS
        )
        page.locator("#automation-editor-display-name-input").fill(automation_name)
        page.locator("#automation-editor-workspace-id-input").select_option("default")
        page.locator("#automation-editor-prompt-input").fill(automation_prompt)
        page.locator("#automation-editor-schedule-kind-input").select_option("weekdays")
        page.locator("#automation-editor-time-input").fill("09:00")
        page.locator("[data-automation-editor-save]").click()
    create_automation_payload = json.loads(
        create_automation_request_info.value.post_data or "{}"
    )
    assert create_automation_payload["display_name"] == automation_name
    assert create_automation_payload["workspace_id"] == "default"
    assert create_automation_payload["prompt"] == automation_prompt
    assert create_automation_payload["cron_expression"] == "0 9 * * 1-5"
    assert create_automation_payload["timezone"] == "Asia/Shanghai"
    expect(
        page.locator("[data-automation-home-project-id]").filter(
            has_text=automation_name
        )
    ).to_be_visible(timeout=_WAIT_TIMEOUT_MS)

    with page.expect_request(
        lambda request: (
            request.method == "GET"
            and "/api/automation/projects/" in request.url
            and not request.url.endswith("/sessions")
        )
    ):
        page.locator("[data-automation-home-project-id]").filter(
            has_text=automation_name
        ).click()
    expect(page.locator("#project-view")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    expect(page.locator("#project-view-title")).to_contain_text(
        re.compile(r"(Automation|自动化)"),
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator(".automation-prompt-inline")).to_contain_text(
        automation_prompt,
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("[data-automation-toggle]")).to_have_count(
        1,
        timeout=_WAIT_TIMEOUT_MS,
    )

    toggle_label = page.locator("[data-automation-toggle]").text_content() or ""
    expected_first_suffix = (
        ":disable" if re.search(r"(Disable|停用)", toggle_label) else ":enable"
    )
    expected_second_pattern = (
        re.compile(r"(Enable|启用)")
        if expected_first_suffix == ":disable"
        else re.compile(r"(Disable|停用)")
    )
    expected_second_suffix = (
        ":enable" if expected_first_suffix == ":disable" else ":disable"
    )
    with page.expect_request(
        lambda request: (
            request.method == "POST"
            and "/api/automation/projects/" in request.url
            and request.url.endswith(expected_first_suffix)
        )
    ):
        page.locator("[data-automation-toggle]").click()
    expect(page.locator("[data-automation-toggle]")).to_contain_text(
        expected_second_pattern,
        timeout=_WAIT_TIMEOUT_MS,
    )

    with page.expect_request(
        lambda request: (
            request.method == "POST"
            and "/api/automation/projects/" in request.url
            and request.url.endswith(expected_second_suffix)
        )
    ):
        page.locator("[data-automation-toggle]").click()

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
    expect(
        page.locator(f'.session-item[data-session-id="{new_session_id}"]')
    ).to_have_count(
        1,
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#project-view-title")).to_contain_text(
        re.compile(r"(Automation|自动化)"),
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("[data-automation-delete]")).to_be_visible(
        timeout=_WAIT_TIMEOUT_MS,
    )

    with page.expect_request(
        lambda request: (
            request.method == "DELETE" and "/api/automation/projects/" in request.url
        )
    ):
        page.locator("[data-automation-delete]").click(force=True)
        expect(page.locator('[role="alertdialog"]')).to_be_visible(
            timeout=_WAIT_TIMEOUT_MS
        )
        page.locator("[data-feedback-confirm]").click()
    expect(
        page.locator("[data-automation-home-project-id]").filter(
            has_text=automation_name
        )
    ).to_have_count(
        0,
        timeout=_WAIT_TIMEOUT_MS,
    )


def test_browser_gateway_xiaoluban_and_automation_binding_flow(
    browser_page: Page,
    integration_env: IntegrationEnvironment,
) -> None:
    page = browser_page
    _open_app(page, integration_env)

    xiaoluban_token = "uidself_1234567890abcdef1234567890abcdef"
    with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url
            == f"{integration_env.api_base_url}/api/gateway/xiaoluban/accounts"
        )
    ) as create_xiaoluban_response_info:
        page.locator('.home-feature-item[data-feature-id="gateway"]').click()
        expect(page.locator("#project-view")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
        page.locator("[data-feature-gateway-add-xiaoluban]").click()
        expect(page.locator('[role="alertdialog"]')).to_be_visible(
            timeout=_WAIT_TIMEOUT_MS
        )
        expect(page.locator('[data-feedback-form-input="display_name"]')).to_have_value(
            re.compile(r"(Xiaoluban|小鲁班)"),
            timeout=_WAIT_TIMEOUT_MS,
        )
        expect(page.locator('[data-feedback-form-input="base_url"]')).to_have_count(0)
        expect(page.locator('[data-feedback-form-input="enabled"]')).to_have_count(0)
        page.locator('[data-feedback-form-input="token"]').fill(xiaoluban_token)
        page.locator("[data-feedback-confirm]").click()
    create_xiaoluban_response = create_xiaoluban_response_info.value
    assert create_xiaoluban_response.ok
    create_xiaoluban_payload = json.loads(
        create_xiaoluban_response.request.post_data or "{}"
    )
    assert create_xiaoluban_payload["display_name"] in {"Xiaoluban", "小鲁班"}
    assert create_xiaoluban_payload["token"] == xiaoluban_token
    assert "base_url" not in create_xiaoluban_payload
    assert "enabled" not in create_xiaoluban_payload
    created_account = create_xiaoluban_response.json()
    assert str(created_account.get("display_name") or "") in {"Xiaoluban", "小鲁班"}
    assert str(created_account.get("derived_uid") or "") == "uidself"
    expect(page.locator("#project-view-content")).to_contain_text(
        re.compile(r"(Xiaoluban|小鲁班)"),
        timeout=_WAIT_TIMEOUT_MS,
    )
    account_id = str(created_account["account_id"])

    automation_name = f"Browser Xiaoluban Automation {uuid4().hex[:6]}"
    with page.expect_request(
        lambda request: (
            request.method == "POST"
            and request.url == f"{integration_env.api_base_url}/api/automation/projects"
        )
    ) as create_automation_request_info:
        page.locator('.home-feature-item[data-feature-id="automation"]').click()
        expect(page.locator("#project-view")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
        page.locator(
            ".project-view-toolbar-actions [data-feature-automation-create]"
        ).click()
        expect(page.locator("#automation-editor-display-name-input")).to_be_visible(
            timeout=_WAIT_TIMEOUT_MS
        )
        page.locator("#automation-editor-display-name-input").fill(automation_name)
        page.locator("#automation-editor-workspace-id-input").select_option("default")
        page.locator("#automation-editor-prompt-input").fill(
            "Send the Xiaoluban automation summary."
        )
        page.locator("#automation-editor-schedule-kind-input").select_option("weekdays")
        page.locator("#automation-editor-time-input").fill("10:15")
        page.locator("#automation-editor-delivery-binding-input").select_option(
            f"xiaoluban::{account_id}"
        )
        page.locator("#automation-editor-delivery-started-input").uncheck()
        page.locator("#automation-editor-delivery-completed-input").check()
        page.locator("#automation-editor-delivery-failed-input").uncheck()
        page.locator("[data-automation-editor-save]").click()
    create_automation_payload = json.loads(
        create_automation_request_info.value.post_data or "{}"
    )
    assert create_automation_payload["delivery_binding"] == {
        "provider": "xiaoluban",
        "account_id": account_id,
        "display_name": str(created_account["display_name"]),
        "derived_uid": "uidself",
        "source_label": "发送给自己（uidself）",
    }
    assert create_automation_payload["delivery_events"] == ["completed"]
    expect(
        page.locator("[data-automation-home-project-id]").filter(
            has_text=automation_name
        )
    ).to_be_visible(timeout=_WAIT_TIMEOUT_MS)


def test_browser_sidebar_lazy_loads_subagent_sessions_on_initial_open(
    browser_page: Page,
    integration_env: IntegrationEnvironment,
    api_client: httpx.Client,
    tmp_path: Path,
) -> None:
    workspace_count = 4
    sessions_per_workspace = 12
    for index in range(workspace_count):
        workspace_id = f"perf-workspace-{index}-{uuid4().hex[:6]}"
        workspace_root = tmp_path / workspace_id
        workspace_root.mkdir(parents=True, exist_ok=True)
        (workspace_root / "README.md").write_text(
            f"Workspace {index}.\n",
            encoding="utf-8",
        )
        workspace_response = api_client.post(
            "/api/workspaces",
            json={
                "workspace_id": workspace_id,
                "root_path": str(workspace_root),
            },
        )
        workspace_response.raise_for_status()
        for _ in range(sessions_per_workspace):
            session_response = api_client.post(
                "/api/sessions",
                json={"workspace_id": workspace_id},
            )
            session_response.raise_for_status()

    page = browser_page
    subagent_request_urls: list[str] = []
    page.on(
        "request",
        lambda request: (
            subagent_request_urls.append(request.url)
            if (
                request.method == "GET"
                and request.url.startswith(
                    f"{integration_env.api_base_url}/api/sessions/"
                )
                and request.url.endswith("/subagents")
            )
            else None
        ),
    )

    _open_app(page, integration_env)
    expect(page.locator(".session-item.active")).to_have_count(
        1,
        timeout=_WAIT_TIMEOUT_MS,
    )
    page.wait_for_timeout(2500)

    assert len(subagent_request_urls) == 1


def _open_app(page: Page, integration_env: IntegrationEnvironment) -> None:
    page.goto(integration_env.api_base_url, wait_until="domcontentloaded")
    expect(page.locator("#backend-status-label")).to_contain_text(
        _CONNECTED_LABEL,
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#projects-list")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)


def _open_web_settings_panel(
    page: Page, integration_env: IntegrationEnvironment
) -> None:
    page.locator("#settings-btn").click()
    expect(page.locator("#settings-modal")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    with page.expect_response(
        lambda response: (
            response.request.method == "GET"
            and response.url == f"{integration_env.api_base_url}/api/system/configs/web"
            and response.ok
        )
    ):
        page.locator('.settings-tab[data-tab="web"]').click()
    expect(page.locator("#web-panel")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)


def _open_workspace_settings_panel(
    page: Page, integration_env: IntegrationEnvironment
) -> None:
    page.locator("#settings-btn").click()
    expect(page.locator("#settings-modal")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    with page.expect_response(
        lambda response: (
            response.request.method == "GET"
            and response.url
            == f"{integration_env.api_base_url}/api/system/configs/workspace/ssh-profiles"
            and response.ok
        )
    ):
        page.locator('.settings-tab[data-tab="workspace"]').click()
    expect(page.locator("#workspace-panel")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)


def _assert_builtin_searxng_instances(page: Page) -> None:
    assert _locator_texts(
        page.locator("#web-searxng-builtins-list .trigger-readonly-value")
    ) == list(DEFAULT_SEARXNG_INSTANCE_SEEDS)


def _assert_locators_share_row(*locators: Locator) -> None:
    top_positions: list[float] = []
    bottom_positions: list[float] = []
    for locator in locators:
        expect(locator).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
        box = locator.bounding_box()
        assert box is not None
        top_positions.append(box["y"])
        bottom_positions.append(box["y"] + box["height"])
    assert max(top_positions) - min(top_positions) < _ROW_ALIGNMENT_TOLERANCE_PX
    assert max(bottom_positions) - min(bottom_positions) < _ROW_ALIGNMENT_TOLERANCE_PX


def _assert_locators_share_left_edge(*locators: Locator) -> None:
    left_positions: list[float] = []
    for locator in locators:
        expect(locator).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
        box = locator.bounding_box()
        assert box is not None
        left_positions.append(box["x"])
    assert max(left_positions) - min(left_positions) < 2.0


def _assert_locators_share_right_edge(*locators: Locator) -> None:
    right_positions: list[float] = []
    for locator in locators:
        expect(locator).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
        box = locator.bounding_box()
        assert box is not None
        right_positions.append(box["x"] + box["width"])
    assert max(right_positions) - min(right_positions) < 2.0


def _assert_locator_below(anchor: Locator, subject: Locator) -> None:
    expect(anchor).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    expect(subject).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    anchor_box = anchor.bounding_box()
    subject_box = subject.bounding_box()
    assert anchor_box is not None
    assert subject_box is not None
    assert subject_box["y"] > anchor_box["y"] + anchor_box["height"]


def _assert_vertical_gap_matches(
    reference_upper: Locator,
    reference_lower: Locator,
    subject_upper: Locator,
    subject_lower: Locator,
) -> None:
    reference_gap = _vertical_gap_between(reference_upper, reference_lower)
    subject_gap = _vertical_gap_between(subject_upper, subject_lower)
    assert abs(reference_gap - subject_gap) < 2.0


def _vertical_gap_between(upper: Locator, lower: Locator) -> float:
    expect(upper).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    expect(lower).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    upper_box = upper.bounding_box()
    lower_box = lower.bounding_box()
    assert upper_box is not None
    assert lower_box is not None
    return lower_box["y"] - (upper_box["y"] + upper_box["height"])


def _create_session_via_sidebar(page: Page) -> str:
    existing_session_ids = set(_session_ids(page))
    first_project_row = page.locator(".project-row").first
    first_project_row.hover()
    expect(page.locator(".project-new-session-btn").first).to_be_visible(
        timeout=_WAIT_TIMEOUT_MS
    )
    page.locator(".project-new-session-btn").first.click()
    expect(page.locator(".new-session-draft-page")).to_be_visible(
        timeout=_WAIT_TIMEOUT_MS
    )

    workspace_id = _first_workspace_id(page)
    response = page.request.post(
        f"{page.url.rstrip('/')}/api/sessions",
        data=json.dumps({"workspace_id": workspace_id}),
        headers={"Content-Type": "application/json"},
        timeout=_WAIT_TIMEOUT_MS,
    )
    assert response.ok
    response_payload = cast(JsonValue, response.json())
    session_id = (
        str(response_payload.get("session_id") or "").strip()
        if isinstance(response_payload, dict)
        else ""
    )
    if not session_id:
        session_id = _wait_for_new_session_id(page, existing_session_ids)
    else:
        page.reload(wait_until="domcontentloaded")
        expect(page.locator("#backend-status-label")).to_contain_text(
            _CONNECTED_LABEL,
            timeout=_WAIT_TIMEOUT_MS,
        )
        expect(
            page.locator(f'.session-item[data-session-id="{session_id}"]')
        ).to_have_count(1, timeout=_WAIT_TIMEOUT_MS)
    page.locator(f'.session-item[data-session-id="{session_id}"]').click(force=True)
    expect(page.locator(".session-item.active")).to_have_attribute(
        "data-session-id",
        session_id,
        timeout=_WAIT_TIMEOUT_MS,
    )
    return session_id


def _first_workspace_id(page: Page) -> str:
    response = page.request.get(
        f"{page.url.rstrip('/')}/api/workspaces",
        timeout=_WAIT_TIMEOUT_MS,
    )
    assert response.ok
    payload = cast(JsonValue, response.json())
    if not isinstance(payload, list):
        raise AssertionError("Workspace list response was not an array.")
    for item in payload:
        if not isinstance(item, dict):
            continue
        workspace_id = str(item.get("workspace_id") or "").strip()
        if workspace_id:
            return workspace_id
    raise AssertionError("No workspace was available for browser session creation.")


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


def _wait_for_session_ids_snapshot(
    page: Page, *, timeout_seconds: float = 15.0
) -> set[str]:
    deadline = time.monotonic() + timeout_seconds
    previous_snapshot: set[str] | None = None
    stable_count = 0
    while time.monotonic() < deadline:
        current_snapshot = set(_session_ids(page))
        if current_snapshot == previous_snapshot:
            stable_count += 1
            if stable_count >= 2:
                return current_snapshot
        else:
            previous_snapshot = current_snapshot
            stable_count = 0
        page.wait_for_timeout(200)
    raise AssertionError("Timed out waiting for the session list to stabilize.")


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


def _wait_for_open_user_questions(
    client: httpx.Client,
    *,
    run_id: str,
    expected_count: int,
    timeout_seconds: float = 15.0,
) -> list[dict[str, object]]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}/questions")
        response.raise_for_status()
        questions = response.json()
        if not isinstance(questions, list):
            raise AssertionError(f"Invalid user questions response: {questions}")
        if len(questions) == expected_count:
            return questions
        time.sleep(0.2)
    raise AssertionError(
        f"Timed out waiting for {expected_count} user questions for run {run_id}."
    )


def _wait_for_run_id(
    client: httpx.Client,
    session_id: str,
    *,
    timeout_seconds: float = 30.0,
) -> str:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f"/api/sessions/{session_id}/rounds")
        response.raise_for_status()
        rounds = response.json()
        items = rounds.get("items", [])
        if items:
            last_item = items[-1]
            run_id = last_item.get("run_id")
            if run_id:
                return str(run_id)
        time.sleep(0.3)
    raise AssertionError(f"Timed out waiting for run ID for session {session_id}.")


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


def _locator_texts(locator) -> list[str]:
    raw_texts = locator.evaluate_all(
        """elements => elements.map(
            element => (element.textContent || '').trim()
        )"""
    )
    return [str(text).strip() for text in raw_texts if str(text).strip()]


def _select_option_pairs(page: Page, selector: str) -> list[tuple[str, str]]:
    raw_options = page.locator(f"{selector} option").evaluate_all(
        """options => options.map(
            option => [option.value, (option.textContent || '').trim()]
        )"""
    )
    return [
        (str(value).strip(), str(text).strip())
        for value, text in cast(list[list[str]], raw_options)
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
        failure: list[BaseException] = []

        def runner() -> None:
            try:
                runtime = _build_acp_stdio_runtime()
                server = cast(AcpGatewayServer, getattr(runtime, "_server"))

                async def discard_notify(_message: dict[str, JsonValue]) -> None:
                    return None

                server.set_notify(discard_notify)
                asyncio.run(
                    asyncio.wait_for(_run_gateway_observability_probe(server), 30.0)
                )
            except BaseException as exc:  # pragma: no cover - re-raised below
                failure.append(exc)

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join(timeout=35.0)
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
    assert isinstance(prompt_result, dict), (
        f"unexpected ACP prompt response: {prompt_response!r}"
    )
    assert prompt_result.get("runStatus") == "completed"


def test_browser_round_todo_card_renders_and_collapses(
    browser_page: Page,
    integration_env: IntegrationEnvironment,
    api_client: httpx.Client,
) -> None:
    page = browser_page
    _open_app(page, integration_env)
    if not page.locator("body").evaluate(
        "element => element.classList.contains('light-theme')"
    ):
        page.locator("#toggle-theme").click()
        page.wait_for_function(
            "document.body.classList.contains('light-theme')",
            timeout=_WAIT_TIMEOUT_MS,
        )

    session_id = _create_session_via_sidebar(page)
    prompt = "[todo-validation] 维护当前 run 的 todo，并完成一次持久化校验。"

    expect(page.locator("#prompt-input")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    page.locator("#prompt-input").fill(prompt)
    page.locator("#send-btn").click()

    run_id = _wait_for_run_id(api_client, session_id)
    round_nav = page.locator("#round-nav-float")
    round_node = round_nav.locator(f'.round-nav-node[data-run-id="{run_id}"]')
    todo_card = round_node.locator(".round-nav-todo-branch .round-todo-card")

    expect(todo_card).to_have_count(1, timeout=_WAIT_TIMEOUT_MS)
    expect(round_node).to_have_class(
        re.compile(r".*\bactive\b.*"), timeout=_WAIT_TIMEOUT_MS
    )
    expect(round_nav.locator(".round-nav-resizer")).to_have_count(
        1,
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(todo_card).not_to_have_attribute("open", "", timeout=_WAIT_TIMEOUT_MS)
    todo_card.locator(".round-todo-summary").click()
    expect(todo_card).to_have_attribute("open", "", timeout=_WAIT_TIMEOUT_MS)
    expect(todo_card.locator(".round-todo-item")).to_have_count(
        3,
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(todo_card).to_contain_text(
        "Inspect issue 399 requirements",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(todo_card).to_contain_text(
        "Implement run todo persistence",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(todo_card.locator(".round-todo-item-text").first).to_have_attribute(
        "title",
        "Inspect issue 399 requirements",
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator(".round-todo-card")).to_have_count(1, timeout=_WAIT_TIMEOUT_MS)
    expect(page.locator(".session-round-section .round-todo-card")).to_have_count(
        0,
        timeout=_WAIT_TIMEOUT_MS,
    )

    expanded_width = round_nav.evaluate(
        "element => element.getBoundingClientRect().width"
    )
    round_nav.locator(".round-nav-toggle").click()
    collapsed_width = round_nav.evaluate(
        "element => element.getBoundingClientRect().width"
    )
    assert collapsed_width < expanded_width

    round_nav.locator(".round-nav-toggle").click()
    expect(todo_card).to_have_attribute("open", "", timeout=_WAIT_TIMEOUT_MS)

    todo_card.locator(".round-todo-summary").click()
    expect(todo_card).not_to_have_attribute("open", "", timeout=_WAIT_TIMEOUT_MS)
    expect(todo_card.locator(".round-todo-body")).to_be_hidden(timeout=_WAIT_TIMEOUT_MS)

    todo_card.locator(".round-todo-summary").click()
    expect(todo_card).to_have_attribute("open", "", timeout=_WAIT_TIMEOUT_MS)
    expect(todo_card.locator(".round-todo-body")).to_be_visible(
        timeout=_WAIT_TIMEOUT_MS
    )


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
