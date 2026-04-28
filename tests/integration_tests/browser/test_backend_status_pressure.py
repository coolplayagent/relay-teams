from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
import os
import time

import httpx
from playwright.sync_api import Locator, Page, expect, sync_playwright
import pytest

from integration_tests.browser.test_browser_smoke import (
    _CONNECTED_LABEL,
    _WAIT_TIMEOUT_MS,
    _resolve_playwright_browser_root,
)
from integration_tests.support.api_helpers import create_session, new_session_id
from integration_tests.support.environment import IntegrationEnvironment
from integration_tests.support.session_tool_pressure import (
    PressureScenarioResult,
    assert_backend_probes_stayed_responsive,
    run_pressure_scenario,
)


@pytest.fixture()
def browser_page() -> Iterator[Page]:
    browser_root = _resolve_playwright_browser_root()
    previous_browser_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_root)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1600, "height": 1200},
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


@pytest.mark.timeout(180)
def test_browser_backend_status_stays_connected_during_tool_pressure(
    browser_page: Page,
    api_client: httpx.Client,
    integration_env: IntegrationEnvironment,
) -> None:
    normal_session_ids = [
        create_session(
            api_client,
            session_id=new_session_id(f"browser-normal-pressure-{index:02d}"),
        )
        for index in range(6)
    ]
    orchestration_session_ids = [
        create_session(
            api_client,
            session_id=new_session_id(f"browser-orch-pressure-{index:02d}"),
        )
        for index in range(3)
    ]
    for session_id in orchestration_session_ids:
        response = api_client.patch(
            f"/api/sessions/{session_id}/topology",
            json={"session_mode": "orchestration"},
        )
        response.raise_for_status()

    page = browser_page
    with ThreadPoolExecutor(max_workers=2) as executor:
        normal_future = executor.submit(
            run_pressure_scenario,
            integration_env=integration_env,
            session_ids=normal_session_ids,
            intent_template=(
                "[normal-tool-pressure count=4 delay=700 tag=browsernormal{index}] "
                "run browser-observed normal pressure."
            ),
            timeout_seconds=120.0,
        )
        orchestration_future = executor.submit(
            run_pressure_scenario,
            integration_env=integration_env,
            session_ids=orchestration_session_ids,
            intent_template=(
                "[orch-tool-pressure count=3 tools=3 delay=620] "
                "run browser-observed orchestrated pressure."
            ),
            timeout_seconds=140.0,
        )

        page.goto(integration_env.api_base_url, wait_until="domcontentloaded")
        status = page.locator("#backend-status")
        expect(page.locator("#backend-status-label")).to_contain_text(
            _CONNECTED_LABEL,
            timeout=_WAIT_TIMEOUT_MS,
        )
        observed_statuses = _observe_backend_status_until_done(
            page,
            status,
            normal_future,
            orchestration_future,
        )
        normal_result = normal_future.result(timeout=130.0)
        orchestration_result = orchestration_future.result(timeout=150.0)

    assert "online" in observed_statuses
    assert "busy" not in observed_statuses
    assert "offline" not in observed_statuses
    _assert_pressure_completed(normal_result, expected_text="normal tool pressure")
    _assert_pressure_completed(
        orchestration_result,
        expected_text="orchestration tool pressure",
    )
    assert_backend_probes_stayed_responsive(normal_result.probes)
    assert_backend_probes_stayed_responsive(orchestration_result.probes)


def _observe_backend_status_until_done(
    page: Page,
    status: Locator,
    normal_future: Future[PressureScenarioResult],
    orchestration_future: Future[PressureScenarioResult],
) -> set[str]:
    observed_statuses: set[str] = set()
    deadline = time.monotonic() + 25.0
    while time.monotonic() < deadline:
        current_status = str(status.get_attribute("data-status") or "").strip()
        if current_status:
            observed_statuses.add(current_status)
        assert current_status not in {"busy", "offline"}
        if normal_future.done() and orchestration_future.done():
            break
        page.wait_for_timeout(250)
    return observed_statuses


def _assert_pressure_completed(
    result: PressureScenarioResult,
    *,
    expected_text: str,
) -> None:
    assert {run.terminal_event_type for run in result.runs} == {"run_completed"}
    assert all(expected_text in run.output_text for run in result.runs)
