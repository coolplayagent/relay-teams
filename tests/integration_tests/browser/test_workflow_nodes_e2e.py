from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import re

import httpx
import pytest
from playwright.sync_api import (
    Browser,
    Locator,
    Page,
    Playwright,
    expect,
    sync_playwright,
)

from integration_tests.support.environment import IntegrationEnvironment
from integration_tests.support.api_helpers import (
    create_custom_workflow,
    create_run,
    create_session,
    dispatch_workflow_next,
    new_session_id,
    stream_run_until_terminal,
)


def test_dag_nodes_show_status_badges(
    api_client: httpx.Client,
    integration_env: IntegrationEnvironment,
    page: Page,
) -> None:
    session_id = prepare_workflow_run(api_client, dispatch_rounds=1)

    page.goto(integration_env.api_base_url, wait_until="networkidle")
    select_session(page, session_id)

    nodes = page.locator(".dag-node")
    expect(nodes).to_have_count(2, timeout=15000)

    first_status = nodes.nth(0).locator(".node-state").inner_text().strip()
    second_status = nodes.nth(1).locator(".node-state").inner_text().strip()
    assert first_status in {
        "Pending",
        "Running",
        "Completed",
        "Failed",
        "Timeout",
        "Stopped",
        "Unknown",
    }
    assert second_status in {
        "Pending",
        "Running",
        "Completed",
        "Failed",
        "Timeout",
        "Stopped",
        "Unknown",
    }
    assert first_status != ""
    assert second_status != ""


def test_clicking_different_nodes_keeps_single_active(
    api_client: httpx.Client,
    integration_env: IntegrationEnvironment,
    page: Page,
) -> None:
    session_id = prepare_workflow_run(api_client, dispatch_rounds=2)

    page.goto(integration_env.api_base_url, wait_until="networkidle")
    select_session(page, session_id)

    nodes = page.locator(".dag-node")
    expect(nodes).to_have_count(2, timeout=15000)
    first_node = nodes.nth(0)
    second_node = nodes.nth(1)

    expect(first_node).to_have_attribute(
        "data-instance-id", re.compile(".+"), timeout=10000
    )
    expect(second_node).to_have_attribute(
        "data-instance-id", re.compile(".+"), timeout=10000
    )

    first_node.click()
    page.wait_for_timeout(120)
    assert active_node_count(page) == 1
    assert has_class(first_node, "active-tab")
    assert not has_class(second_node, "active-tab")

    second_node.click()
    page.wait_for_timeout(120)
    assert active_node_count(page) == 1
    assert not has_class(first_node, "active-tab")
    assert has_class(second_node, "active-tab")


@pytest.fixture()
def page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        yield page
    finally:
        context.close()


@pytest.fixture(scope="session")
def browser(playwright: Playwright) -> Iterator[Browser]:
    chromium_path = Path(playwright.chromium.executable_path)
    if not chromium_path.exists():
        pytest.skip(
            "Chromium is not installed. Run: uv run playwright install chromium"
        )
    browser = playwright.chromium.launch(headless=True)
    try:
        yield browser
    finally:
        browser.close()


@pytest.fixture(scope="session")
def playwright() -> Iterator[Playwright]:
    with sync_playwright() as p:
        yield p


def prepare_workflow_run(client: httpx.Client, *, dispatch_rounds: int) -> str:
    session_id = create_session(client, session_id=new_session_id("session-ui"))
    run_id = create_run(
        client,
        session_id=session_id,
        intent="创建一个用于UI回归的流程",
        execution_mode="manual",
    )
    _ = stream_run_until_terminal(client, run_id=run_id)

    workflow = create_custom_workflow(
        client,
        run_id=run_id,
        objective="ui e2e workflow",
    )
    workflow_id = workflow.get("workflow_id")
    if not isinstance(workflow_id, str) or not workflow_id:
        raise AssertionError(f"Invalid workflow payload: {workflow}")

    for _ in range(dispatch_rounds):
        dispatch = dispatch_workflow_next(
            client,
            run_id=run_id,
            workflow_id=workflow_id,
            max_dispatch=1,
        )
        if dispatch.get("ok") is not True:
            raise AssertionError(f"Workflow dispatch failed: {dispatch}")
    return session_id


def select_session(page: Page, session_id: str) -> None:
    _ = session_id
    target = page.locator(".session-item").first
    expect(target).to_be_visible(timeout=20000)
    target.click()


def active_node_count(page: Page) -> int:
    return int(page.locator(".dag-node.active-tab").count())


def has_class(node: Locator, class_name: str) -> bool:
    value = node.get_attribute("class")
    return class_name in (value or "").split()
