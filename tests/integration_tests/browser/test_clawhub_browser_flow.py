# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections.abc import Iterator
import json
import os
from pathlib import Path

import httpx
from playwright.sync_api import Page
from playwright.sync_api import expect
from playwright.sync_api import sync_playwright
import pytest

from integration_tests.support.environment import IntegrationEnvironment


_VIEWPORT_WIDTH = 1600
_VIEWPORT_HEIGHT = 1200
_WAIT_TIMEOUT_MS = 30_000


def _set_windows_proactor_event_loop_policy() -> asyncio.AbstractEventLoopPolicy | None:
    if os.name != "nt" or not hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
        return None
    previous_policy = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    return previous_policy


@pytest.fixture()
def browser_page() -> Iterator[Page]:
    browser_root = _resolve_playwright_browser_root()
    previous_browser_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    previous_event_loop_policy = _set_windows_proactor_event_loop_policy()
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
        if previous_event_loop_policy is not None:
            asyncio.set_event_loop_policy(previous_event_loop_policy)


def test_browser_clawhub_saved_token_wins_over_autofilled_dom_value(
    browser_page: Page,
    integration_env: IntegrationEnvironment,
    api_client: httpx.Client,
) -> None:
    saved_token = "ch_browser_saved_token"
    response = api_client.put(
        "/api/system/configs/clawhub",
        json={"token": saved_token},
    )
    response.raise_for_status()

    page = browser_page
    page.goto(integration_env.api_base_url, wait_until="domcontentloaded")
    expect(page.locator("#projects-list")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)

    with page.expect_response(
        lambda response: (
            response.request.method == "GET"
            and response.url
            == f"{integration_env.api_base_url}/api/system/configs/clawhub"
            and response.ok
        ),
        timeout=_WAIT_TIMEOUT_MS,
    ):
        page.locator('.home-feature-item[data-feature-id="skills"]').click()

    expect(page.locator("#project-view")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    token_input = page.locator("#feature-clawhub-token")
    expect(token_input).to_have_attribute("autocomplete", "new-password")
    expect(token_input).to_have_value("", timeout=_WAIT_TIMEOUT_MS)

    token_input.evaluate("(input) => { input.value = 'browser_password'; }")

    captured_probe_payload: dict[str, object] = {}

    def handle_probe(route) -> None:
        request = route.request
        captured_probe_payload.update(json.loads(request.post_data or "{}"))
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "clawhub_version": "clawhub 0.4.2",
                    "latency_ms": 12,
                    "diagnostics": {"installed_during_probe": False},
                }
            ),
        )

    probe_url = f"{integration_env.api_base_url}/api/system/configs/clawhub:probe"
    page.route(probe_url, handle_probe)
    try:
        page.locator("#feature-test-clawhub-btn").click()
        expect(page.locator("#feature-clawhub-probe-status")).to_contain_text(
            "clawhub 0.4.2",
            timeout=_WAIT_TIMEOUT_MS,
        )
    finally:
        page.unroute(probe_url, handle_probe)

    assert captured_probe_payload == {"token": saved_token}

    with page.expect_request(
        lambda request: (
            request.method == "PUT"
            and request.url
            == f"{integration_env.api_base_url}/api/system/configs/clawhub"
        ),
        timeout=_WAIT_TIMEOUT_MS,
    ) as save_request_info:
        page.locator("#feature-save-clawhub-token-btn").click()

    save_payload = json.loads(save_request_info.value.post_data or "{}")
    assert save_payload == {"token": saved_token}


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
