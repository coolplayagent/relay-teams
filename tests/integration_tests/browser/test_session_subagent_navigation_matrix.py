from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import re
import time
from typing import NamedTuple

import httpx
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import expect
from playwright.sync_api import sync_playwright
import pytest

from integration_tests.support.api_helpers import (
    create_run,
    create_session,
    new_session_id,
    stream_run_until_terminal,
)
from integration_tests.support.environment import IntegrationEnvironment


_VIEWPORT_WIDTH = 1440
_VIEWPORT_HEIGHT = 1000
_WAIT_TIMEOUT_MS = 20_000
_SWITCH_LOAD_TIMEOUT_MS = 5_000
_ROOT_COUNT = 3
_SUBAGENTS_PER_ROOT = 3
_CONNECTED_LABEL = re.compile(r"(Backend Connected|后端已连接)")


class NavigationTarget(NamedTuple):
    kind: str
    session_id: str
    run_id: str
    instance_id: str


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


@pytest.mark.timeout(240)
def test_browser_root_subagent_navigation_matrix_preserves_live_ui_state(
    browser_page: Page,
    integration_env: IntegrationEnvironment,
    api_client: httpx.Client,
) -> None:
    page = browser_page
    console_errors: list[str] = []
    browser_subagent_requests: list[str] = []
    page.on(
        "console",
        lambda message: (
            console_errors.append(message.text) if message.type == "error" else None
        ),
    )
    page.on("pageerror", lambda error: console_errors.append(str(error)))
    page.on(
        "request",
        lambda request: (
            browser_subagent_requests.append(request.url)
            if "/api/sessions/" in request.url and "/subagents" in request.url
            else None
        ),
    )

    session_ids = [
        create_session(
            api_client,
            session_id=new_session_id(f"browser-ui-matrix-{index:02d}"),
        )
        for index in range(_ROOT_COUNT)
    ]
    run_ids = _start_matrix_runs(api_client, session_ids)

    _open_app(page, integration_env)
    _wait_for_session_items(page, session_ids)
    _wait_for_subagent_counts(
        api_client, session_ids, expected_count=_SUBAGENTS_PER_ROOT
    )
    _wait_for_visible_subagent_toggles(page, session_ids)
    assert browser_subagent_requests == []

    targets = _load_navigation_targets_from_ui(page, session_ids, run_ids)
    assert len([target for target in targets if target.kind == "root"]) == _ROOT_COUNT
    assert len([target for target in targets if target.kind == "subagent"]) >= (
        _ROOT_COUNT * _SUBAGENTS_PER_ROOT
    )

    switch_durations: list[int] = []
    sequence = _navigation_matrix_sequence(targets)
    for target in sequence:
        started = time.perf_counter()
        _activate_target(page, target)
        _wait_for_switch_settled(page)
        duration_ms = int((time.perf_counter() - started) * 1000)
        switch_durations.append(duration_ms)
        assert duration_ms < _SWITCH_LOAD_TIMEOUT_MS, (
            f"switch to {target.kind} session={target.session_id} "
            f"run={target.run_id} instance={target.instance_id} took {duration_ms}ms"
        )
        _assert_target_rendered(page, target)
        _assert_no_permanent_loading(page)
        _assert_no_cross_target_obvious_leak(page, target, targets)

    _wait_for_terminal_runs(api_client, run_ids)
    _wait_for_terminal_subagent_runs(api_client, targets)
    for target in targets:
        _activate_target(page, target)
        _wait_for_switch_settled(page)
        _assert_target_rendered(page, target)
        _assert_terminal_indicator_not_spinning(page, target)

    diagnostics = page.evaluate("() => window.__agentTeamsUiDiagnostics?.get?.() || {}")
    assert diagnostics.get("wrong_target_render_count", 0) == 0
    assert diagnostics.get("stream_gap_count", 0) == 0
    assert diagnostics.get("terminal_loop_count", 0) == 0
    assert diagnostics.get("running_indicator_missing_count", 0) == 0
    assert max(switch_durations) < _SWITCH_LOAD_TIMEOUT_MS
    assert console_errors == []


def _start_matrix_runs(
    client: httpx.Client,
    session_ids: list[str],
) -> dict[str, str]:
    def start_one(index_and_session: tuple[int, str]) -> tuple[str, str]:
        index, session_id = index_and_session
        with httpx.Client(base_url=str(client.base_url), timeout=60.0) as run_client:
            run_id = create_run(
                run_client,
                session_id=session_id,
                intent=(
                    "[session-extreme-pressure "
                    "main_calls=24 main_batch=6 subagents=3 subagent_calls=15 "
                    "subagent_batch=5 subagent_spawn_batch=3 path=AGENTS.md "
                    f"tag=browser{index}] "
                    "exercise mixed root and subagent tool streams for browser UI."
                ),
                execution_mode="ai",
                yolo=True,
            )
        return session_id, run_id

    with ThreadPoolExecutor(max_workers=len(session_ids)) as executor:
        return dict(executor.map(start_one, enumerate(session_ids)))


def _open_app(page: Page, integration_env: IntegrationEnvironment) -> None:
    page.goto(integration_env.api_base_url, wait_until="domcontentloaded")
    expect(page.locator("#backend-status-label")).to_contain_text(
        _CONNECTED_LABEL,
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#projects-list")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    page.evaluate("() => window.__agentTeamsUiDiagnostics?.reset?.()")


def _wait_for_session_items(page: Page, session_ids: list[str]) -> None:
    for session_id in session_ids:
        expect(
            page.locator(f'.session-item[data-session-id="{session_id}"]')
        ).to_have_count(
            1,
            timeout=_WAIT_TIMEOUT_MS,
        )


def _wait_for_subagent_counts(
    client: httpx.Client,
    session_ids: list[str],
    *,
    expected_count: int,
) -> None:
    deadline = time.monotonic() + 70.0
    while time.monotonic() < deadline:
        if all(
            _subagent_count(client, session_id) >= expected_count
            for session_id in session_ids
        ):
            return
        time.sleep(0.25)
    counts = {
        session_id: _subagent_count(client, session_id) for session_id in session_ids
    }
    raise AssertionError(f"Timed out waiting for subagents: {counts}")


def _wait_for_visible_subagent_toggles(page: Page, session_ids: list[str]) -> None:
    for session_id in session_ids:
        parent = page.locator(f'.session-item[data-session-id="{session_id}"]').first
        expect(parent).to_have_count(1, timeout=_WAIT_TIMEOUT_MS)
        page.evaluate(
            """
            sessionId => {
                const item = document.querySelector(`.session-item[data-session-id="${sessionId}"]`);
                item?.scrollIntoView?.({ block: 'center' });
                const list = document.querySelector('#projects-list');
                list?.dispatchEvent?.(new Event('scroll'));
            }
            """,
            session_id,
        )
        expect(parent).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
        expect(parent.locator(".session-subagents-toggle")).to_have_count(
            1,
            timeout=_SWITCH_LOAD_TIMEOUT_MS,
        )


def _subagent_count(client: httpx.Client, session_id: str) -> int:
    response = client.get(f"/api/sessions/{session_id}/subagents?force_refresh=1")
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("value", payload) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return 0
    return len(rows)


def _load_navigation_targets_from_ui(
    page: Page,
    session_ids: list[str],
    run_ids: dict[str, str],
) -> list[NavigationTarget]:
    targets = [
        NavigationTarget(
            kind="root",
            session_id=session_id,
            run_id=run_ids[session_id],
            instance_id="",
        )
        for session_id in session_ids
    ]
    for session_id in session_ids:
        _expand_parent_session(page, session_id)
        locator = page.locator(
            f'.session-subagent-item[data-session-id="{session_id}"]'
        )
        expect(locator).to_have_count(_SUBAGENTS_PER_ROOT, timeout=_WAIT_TIMEOUT_MS)
        rows = page.evaluate(
            """
            sessionId => Array.from(
                document.querySelectorAll(`.session-subagent-item[data-session-id="${sessionId}"]`)
            ).map(item => ({
                runId: String(item.getAttribute('data-subagent-run-id') || '').trim(),
                instanceId: String(item.getAttribute('data-subagent-instance-id') || '').trim(),
            }))
            """,
            session_id,
        )
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            run_id = str(row.get("runId") or "").strip()
            instance_id = str(row.get("instanceId") or "").strip()
            if run_id and instance_id:
                targets.append(
                    NavigationTarget(
                        kind="subagent",
                        session_id=session_id,
                        run_id=run_id,
                        instance_id=instance_id,
                    )
                )
    return targets


def _navigation_matrix_sequence(
    targets: list[NavigationTarget],
) -> list[NavigationTarget]:
    roots = [target for target in targets if target.kind == "root"]
    subagents = [target for target in targets if target.kind == "subagent"]
    sequence: list[NavigationTarget] = []
    for index, root in enumerate(roots):
        sequence.append(root)
        own_subagents = [
            target for target in subagents if target.session_id == root.session_id
        ]
        if own_subagents:
            sequence.append(own_subagents[0])
        sequence.append(roots[(index + 1) % len(roots)])
        other_subagents = [
            target for target in subagents if target.session_id != root.session_id
        ]
        if other_subagents:
            sequence.append(other_subagents[index % len(other_subagents)])
        if len(own_subagents) > 1:
            sequence.append(own_subagents[1])
    return sequence * 2


def _activate_target(page: Page, target: NavigationTarget) -> None:
    if target.kind == "root":
        _click_visible_session_item(page, target.session_id)
        return
    _expand_parent_session(page, target.session_id)
    locator = page.locator(
        ".session-subagent-item"
        f'[data-session-id="{target.session_id}"]'
        f'[data-subagent-instance-id="{target.instance_id}"]'
    )
    expect(locator).to_have_count(1, timeout=_WAIT_TIMEOUT_MS)
    expect(locator).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    locator.scroll_into_view_if_needed(timeout=_WAIT_TIMEOUT_MS)
    locator.click(timeout=_WAIT_TIMEOUT_MS)
    expect(locator).to_have_class(
        re.compile(r"(^|\s)active(\s|$)"),
        timeout=_SWITCH_LOAD_TIMEOUT_MS,
    )


def _click_visible_session_item(page: Page, session_id: str) -> None:
    locator = page.locator(f'.session-item[data-session-id="{session_id}"]').first
    expect(locator).to_have_count(1, timeout=_WAIT_TIMEOUT_MS)
    clicked = page.evaluate(
        """
        sessionId => {
            const item = document.querySelector(`.session-item[data-session-id="${sessionId}"]`);
            if (!item) return false;
            item.scrollIntoView?.({ block: 'center' });
            item.click?.();
            return true;
        }
        """,
        session_id,
    )
    assert clicked is True


def _expand_parent_session(page: Page, session_id: str) -> None:
    page.wait_for_function(
        """
        sessionId => {
            const item = document.querySelector(`.session-item[data-session-id="${sessionId}"]`);
            if (!item) return false;
            item.scrollIntoView?.({ block: 'center' });
            return true;
        }
        """,
        arg=session_id,
        timeout=_WAIT_TIMEOUT_MS,
    )
    parent = page.locator(f'.session-item[data-session-id="{session_id}"]').first
    expect(parent).to_be_visible(timeout=_SWITCH_LOAD_TIMEOUT_MS)
    toggle = parent.locator(".session-subagents-toggle")
    expect(toggle).to_have_count(1, timeout=_WAIT_TIMEOUT_MS)
    expanded = toggle.get_attribute("aria-expanded")
    if expanded != "true":
        clicked = page.evaluate(
            """
            sessionId => {
                const parent = document.querySelector(`.session-item[data-session-id="${sessionId}"]`);
                const toggle = parent?.querySelector('.session-subagents-toggle');
                if (!toggle) return false;
                toggle.scrollIntoView?.({ block: 'center' });
                toggle.click?.();
                return true;
            }
            """,
            session_id,
        )
        assert clicked is True
    expect(
        page.locator(f'.session-subagent-item[data-session-id="{session_id}"]').first
    ).to_be_visible(timeout=_WAIT_TIMEOUT_MS)


def _wait_for_switch_settled(page: Page) -> None:
    expect(
        page.locator(
            ".chat-container.is-session-switch-pending, "
            ".chat-container.is-session-switching"
        )
    ).to_have_count(0, timeout=_SWITCH_LOAD_TIMEOUT_MS)


def _assert_target_rendered(page: Page, target: NavigationTarget) -> None:
    if target.kind == "root":
        expect(page.locator(".subagent-session-view")).to_have_count(
            0,
            timeout=_SWITCH_LOAD_TIMEOUT_MS,
        )
        expect(page.locator("main")).to_contain_text(
            target.session_id,
            timeout=_SWITCH_LOAD_TIMEOUT_MS,
        )
        expect(page.locator("#prompt-input")).to_be_enabled(
            timeout=_SWITCH_LOAD_TIMEOUT_MS
        )
        return
    expect(page.locator(".subagent-session-view")).to_be_visible(
        timeout=_SWITCH_LOAD_TIMEOUT_MS,
    )
    expect(page.locator(".session-subagent-item.active")).to_have_attribute(
        "data-subagent-instance-id",
        target.instance_id,
        timeout=_SWITCH_LOAD_TIMEOUT_MS,
    )
    expect(page.locator("#prompt-input")).to_be_hidden(timeout=_SWITCH_LOAD_TIMEOUT_MS)


def _assert_no_permanent_loading(page: Page) -> None:
    expect(
        page.locator(
            ".chat-container.is-session-switch-pending, "
            ".chat-container.is-session-switching"
        )
    ).to_have_count(0, timeout=_SWITCH_LOAD_TIMEOUT_MS)
    page.wait_for_function(
        """
        () => Array.from(document.querySelectorAll('.session-switch-loading'))
            .every(node => Number(getComputedStyle(node).opacity || 0) <= 0.01)
        """,
        timeout=_SWITCH_LOAD_TIMEOUT_MS,
    )
    expect(page.locator(".subagent-session-loading")).to_be_hidden(
        timeout=_SWITCH_LOAD_TIMEOUT_MS,
    )
    loading_text = page.get_by_text("正在加载对话", exact=False).filter(visible=True)
    try:
        assert (
            loading_text.evaluate_all(
                """
            nodes => nodes.filter(node => {
                let current = node;
                while (current) {
                    const style = getComputedStyle(current);
                    if (style.display === 'none' || style.visibility === 'hidden') {
                        return false;
                    }
                    if (Number(style.opacity || 0) <= 0.01) {
                        return false;
                    }
                    current = current.parentElement;
                }
                return true;
            }).length
            """
            )
            == 0
        )
    except (AssertionError, PlaywrightError) as exc:
        raise AssertionError("Conversation loading did not settle.") from exc


def _assert_no_cross_target_obvious_leak(
    page: Page,
    target: NavigationTarget,
    targets: list[NavigationTarget],
) -> None:
    if target.kind != "subagent":
        return
    other_instances = [
        item.instance_id[:8]
        for item in targets
        if item.kind == "subagent" and item.instance_id != target.instance_id
    ]
    body_text = page.locator("main").inner_text(timeout=_SWITCH_LOAD_TIMEOUT_MS)
    leaked = [
        instance for instance in other_instances if instance and instance in body_text
    ]
    assert leaked == []


def _wait_for_terminal_runs(client: httpx.Client, run_ids: dict[str, str]) -> None:
    for run_id in run_ids.values():
        events = stream_run_until_terminal(client, run_id=run_id, timeout_seconds=90.0)
        assert events[-1]["event_type"] == "run_completed"


def _wait_for_terminal_subagent_runs(
    client: httpx.Client,
    targets: list[NavigationTarget],
) -> None:
    seen_run_ids: set[str] = set()
    for target in targets:
        if target.kind != "subagent" or target.run_id in seen_run_ids:
            continue
        seen_run_ids.add(target.run_id)
        events = stream_run_until_terminal(
            client,
            run_id=target.run_id,
            timeout_seconds=90.0,
        )
        assert events[-1]["event_type"] in {
            "run_completed",
            "run_failed",
            "run_stopped",
        }
        _wait_for_subagent_projection_terminal(client, target)


def _wait_for_subagent_projection_terminal(
    client: httpx.Client,
    target: NavigationTarget,
) -> None:
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        response = client.get(
            f"/api/sessions/{target.session_id}/subagents?force_refresh=1"
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("value", payload) if isinstance(payload, dict) else payload
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                instance_id = str(
                    row.get("instance_id") or row.get("subagent_instance_id") or ""
                ).strip()
                run_status = str(row.get("run_status") or row.get("status") or "")
                if instance_id == target.instance_id and run_status in {
                    "completed",
                    "failed",
                    "stopped",
                }:
                    return
        time.sleep(0.2)
    raise AssertionError(
        "Timed out waiting for terminal subagent projection: "
        f"session={target.session_id} run={target.run_id} "
        f"instance={target.instance_id}"
    )


def _assert_terminal_indicator_not_spinning(
    page: Page, target: NavigationTarget
) -> None:
    if target.kind == "root":
        session = page.locator(
            f'.session-item[data-session-id="{target.session_id}"]'
        ).first
        class_name = session.get_attribute("class") or ""
        assert "has-run-indicator-running" not in class_name
        expect(session.locator(".session-run-indicator-running")).to_have_count(0)
        return
    badge = page.locator(".subagent-session-badge")
    expect(badge).to_be_visible(timeout=_SWITCH_LOAD_TIMEOUT_MS)
    expect(badge).not_to_contain_text(
        re.compile("running", re.IGNORECASE),
        timeout=_SWITCH_LOAD_TIMEOUT_MS,
    )
    expect(badge).not_to_contain_text(
        re.compile("stopping|queued", re.IGNORECASE),
        timeout=_SWITCH_LOAD_TIMEOUT_MS,
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

    for candidate in candidates:
        if any(candidate.glob("chromium-*")):
            return candidate

    raise AssertionError("Playwright browser cache was not found on this machine.")
