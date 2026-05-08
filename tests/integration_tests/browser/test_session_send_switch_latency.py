from __future__ import annotations

from collections.abc import Iterator
import os
import re
import time

import httpx
from playwright.sync_api import Page, expect, sync_playwright
import pytest

from integration_tests.browser.test_browser_smoke import (
    _CONNECTED_LABEL,
    _WAIT_TIMEOUT_MS,
    _resolve_playwright_browser_root,
)
from integration_tests.support.api_helpers import create_session, new_session_id
from integration_tests.support.api_helpers import create_run, stream_run_until_terminal
from integration_tests.support.environment import IntegrationEnvironment


_SWITCH_TARGET_MS = 1000
_TERMINAL_TARGET_MS = 3000
_VIEWPORT_WIDTH = 1600
_VIEWPORT_HEIGHT = 1200


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


@pytest.mark.timeout(120)
def test_send_then_immediate_session_switch_back_loads_under_one_second(
    browser_page: Page,
    integration_env: IntegrationEnvironment,
    api_client: httpx.Client,
) -> None:
    session_a = create_session(
        api_client,
        session_id=new_session_id("browser-send-switch-a"),
    )
    session_b = create_session(
        api_client,
        session_id=new_session_id("browser-send-switch-b"),
    )
    seeded_run_b = create_run(
        api_client,
        session_id=session_b,
        intent="你好",
        execution_mode="ai",
        yolo=True,
    )
    stream_run_until_terminal(api_client, run_id=seeded_run_b, timeout_seconds=20.0)

    page = browser_page
    failed_requests: list[str] = []
    page.on(
        "response",
        lambda response: (
            failed_requests.append(response.url) if response.status >= 500 else None
        ),
    )
    _open_app(page, integration_env)
    _click_session(page, session_a)

    expect(page.locator("#prompt-input")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    page.locator("#prompt-input").fill("你好")

    _install_send_switch_probe(
        page, source_session_id=session_a, target_session_id=session_b
    )
    _click_send_and_target_without_waiting(page, session_b)
    _wait_for_session_active(page, session_b, timeout_ms=_SWITCH_TARGET_MS)
    _wait_for_main_session_content_ready(
        page,
        session_id=session_b,
        previous_session_id=session_a,
        timeout_ms=_SWITCH_TARGET_MS,
    )
    probe_summary = _wait_for_send_switch_probe(page, timeout_ms=_SWITCH_TARGET_MS)
    target_click_after_send_ms = probe_summary["target_click_after_send_ms"]
    target_stable_after_target_click_ms = probe_summary[
        "target_stable_after_target_click_ms"
    ]
    assert isinstance(target_click_after_send_ms, int | float)
    assert isinstance(target_stable_after_target_click_ms, int | float)
    assert target_click_after_send_ms <= 50, probe_summary
    assert target_stable_after_target_click_ms < _SWITCH_TARGET_MS, probe_summary
    expect(_session_item(page, session_a)).to_contain_text(
        "你好",
        timeout=_SWITCH_TARGET_MS,
    )
    expect(_session_item(page, session_a)).to_have_class(
        re.compile(r"has-run-indicator-(running|unread|failed|stopped)"),
        timeout=_SWITCH_TARGET_MS,
    )
    expect(_session_item(page, session_a)).to_have_class(
        re.compile(r"has-run-indicator-(unread|failed|stopped)"),
        timeout=_TERMINAL_TARGET_MS,
    )

    switch_back_started = time.perf_counter()
    _click_session(page, session_a)
    _wait_for_session_active(page, session_a, timeout_ms=_SWITCH_TARGET_MS)
    expect(
        page.locator(".session-run-start-placeholder, .session-round-section").first
    ).to_be_visible(timeout=_SWITCH_TARGET_MS)
    _wait_for_switch_settled(page, timeout_ms=_SWITCH_TARGET_MS)
    switch_back_ms = int((time.perf_counter() - switch_back_started) * 1000)

    assert switch_back_ms < _SWITCH_TARGET_MS
    expect(_session_item(page, session_a)).not_to_have_class(
        re.compile(r"has-run-indicator-(unread|failed|stopped)"),
        timeout=_SWITCH_TARGET_MS,
    )
    assert failed_requests == []


def _open_app(page: Page, integration_env: IntegrationEnvironment) -> None:
    page.goto(integration_env.api_base_url, wait_until="domcontentloaded")
    expect(page.locator("#backend-status-label")).to_contain_text(
        _CONNECTED_LABEL,
        timeout=_WAIT_TIMEOUT_MS,
    )
    expect(page.locator("#projects-list")).to_be_visible(timeout=_WAIT_TIMEOUT_MS)


def _click_session(page: Page, session_id: str) -> None:
    item = _session_item(page, session_id)
    expect(item).to_be_visible(timeout=_WAIT_TIMEOUT_MS)
    item.click(timeout=_WAIT_TIMEOUT_MS)


def _click_send_and_target_without_waiting(page: Page, target_session_id: str) -> None:
    page.evaluate(
        """
        ({ targetSessionId }) => {
            const dispatchClick = element => {
                if (!element) {
                    throw new Error('Missing click target');
                }
                element.dispatchEvent(new PointerEvent('pointerdown', {
                    bubbles: true,
                    cancelable: true,
                    pointerId: 1,
                    pointerType: 'mouse',
                    isPrimary: true,
                }));
                element.dispatchEvent(new MouseEvent('mousedown', {
                    bubbles: true,
                    cancelable: true,
                    button: 0,
                }));
                element.dispatchEvent(new PointerEvent('pointerup', {
                    bubbles: true,
                    cancelable: true,
                    pointerId: 1,
                    pointerType: 'mouse',
                    isPrimary: true,
                }));
                element.dispatchEvent(new MouseEvent('mouseup', {
                    bubbles: true,
                    cancelable: true,
                    button: 0,
                }));
                element.dispatchEvent(new MouseEvent('click', {
                    bubbles: true,
                    cancelable: true,
                    button: 0,
                }));
            };
            dispatchClick(document.querySelector('#send-btn'));
            dispatchClick(document.querySelector(
                `.session-item[data-session-id="${targetSessionId}"]`,
            ));
        }
        """,
        arg={"targetSessionId": target_session_id},
    )


def _session_item(page: Page, session_id: str):
    return page.locator(f'.session-item[data-session-id="{session_id}"]').first


def _wait_for_session_active(
    page: Page,
    session_id: str,
    *,
    timeout_ms: int,
) -> None:
    expect(page.locator(".session-item.active")).to_have_attribute(
        "data-session-id",
        session_id,
        timeout=timeout_ms,
    )


def _wait_for_switch_settled(page: Page, *, timeout_ms: int) -> None:
    expect(
        page.locator(
            ".chat-container.is-session-switch-pending, "
            ".chat-container.is-session-switching"
        )
    ).to_have_count(0, timeout=timeout_ms)


def _wait_for_main_session_content_ready(
    page: Page,
    *,
    session_id: str,
    previous_session_id: str,
    timeout_ms: int,
) -> None:
    page.wait_for_function(
        """
        ({ sessionId, previousSessionId }) => {
            const visible = el => {
                if (!el) {
                    return false;
                }
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && rect.width > 0
                    && rect.height > 0;
            };
            const mainText = document.querySelector('main')?.innerText || '';
            const activeSessionId = document
                .querySelector('.session-item.active')
                ?.getAttribute('data-session-id') || '';
            const switchBusy = !!document.querySelector(
                '.chat-container.is-session-switch-pending, '
                + '.chat-container.is-session-switching',
            ) || Array.from(
                document.querySelectorAll(
                    '.session-switch-loading, .subagent-main-session-loading',
                ),
            ).some(visible);
            return activeSessionId === sessionId
                && !switchBusy
                && mainText.includes(sessionId)
                && !mainText.includes(previousSessionId)
                && Array.from(
                    document.querySelectorAll(
                        '.session-round-section, .session-run-start-placeholder',
                    ),
                ).some(visible);
        }
        """,
        arg={"sessionId": session_id, "previousSessionId": previous_session_id},
        timeout=timeout_ms,
    )


def _install_send_switch_probe(
    page: Page,
    *,
    source_session_id: str,
    target_session_id: str,
) -> None:
    page.evaluate(
        """
        ({ sourceSessionId, targetSessionId }) => {
            if (window.__sendSwitchProbe?.stop) {
                window.__sendSwitchProbe.stop();
            }
            const visible = el => {
                if (!el) {
                    return false;
                }
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && rect.width > 0
                    && rect.height > 0;
            };
            const probe = {
                sourceSessionId,
                targetSessionId,
                startedAt: performance.now(),
                sendClickMs: null,
                targetClickMs: null,
                samples: [],
                stopped: false,
                listener: null,
                timer: null,
            };
            probe.sample = () => {
                const now = performance.now() - probe.startedAt;
                const main = document.querySelector('main');
                const mainText = main?.innerText || '';
                const activeSessionId = document
                    .querySelector('.session-item.active')
                    ?.getAttribute('data-session-id') || '';
                const switchBusy = !!document.querySelector(
                    '.chat-container.is-session-switch-pending, '
                    + '.chat-container.is-session-switching',
                ) || Array.from(
                    document.querySelectorAll(
                        '.session-switch-loading, .subagent-main-session-loading',
                    ),
                ).some(visible);
                const visibleRound = Array.from(
                    document.querySelectorAll(
                        '.session-round-section, .session-run-start-placeholder',
                    ),
                ).some(visible);
                const sourcePlaceholder = !!document.querySelector(
                    `.session-run-start-placeholder[data-session-id="${sourceSessionId}"]`,
                );
                const sourceLiveLeak = sourcePlaceholder
                    || mainText.includes(sourceSessionId)
                    || mainText.includes('LIVE')
                    || mainText.includes('运行中')
                    || mainText.includes('正在加载对话')
                    || mainText.includes('后端繁忙');
                const stable = activeSessionId === targetSessionId
                    && !switchBusy
                    && visibleRound
                    && mainText.includes(targetSessionId)
                    && !sourceLiveLeak;
                probe.samples.push({
                    ms: Math.round(now),
                    activeSessionId,
                    switchBusy,
                    visibleRound,
                    sourceLiveLeak,
                    stable,
                    text: mainText.slice(-500),
                });
            };
            probe.listener = event => {
                const target = event.target;
                if (!(target instanceof Element)) {
                    return;
                }
                if (target.closest('#send-btn')) {
                    probe.sendClickMs = Math.round(performance.now() - probe.startedAt);
                    return;
                }
                if (target.closest(`.session-item[data-session-id="${targetSessionId}"]`)) {
                    probe.targetClickMs = Math.round(performance.now() - probe.startedAt);
                }
            };
            probe.summary = () => {
                const targetActiveSample = probe.samples.find(
                    sample => sample.activeSessionId === targetSessionId,
                );
                const stableSample = probe.samples.find(sample => sample.stable);
                const targetActiveAfterTargetClickMs = (
                    targetActiveSample && probe.targetClickMs !== null
                ) ? Math.max(0, targetActiveSample.ms - probe.targetClickMs) : null;
                const targetStableAfterTargetClickMs = (
                    stableSample && probe.targetClickMs !== null
                ) ? Math.max(0, stableSample.ms - probe.targetClickMs) : null;
                const targetClickAfterSendMs = (
                    probe.sendClickMs !== null && probe.targetClickMs !== null
                ) ? Math.max(0, probe.targetClickMs - probe.sendClickMs) : null;
                return {
                    send_started_ms: probe.sendClickMs,
                    target_click_ms: probe.targetClickMs,
                    target_click_after_send_ms: targetClickAfterSendMs,
                    target_active_after_target_click_ms: targetActiveAfterTargetClickMs,
                    target_stable_after_target_click_ms: targetStableAfterTargetClickMs,
                    sample_count: probe.samples.length,
                    last_sample: probe.samples[probe.samples.length - 1] || null,
                    samples: probe.samples,
                };
            };
            probe.stop = () => {
                if (probe.stopped) {
                    return;
                }
                probe.stopped = true;
                clearInterval(probe.timer);
                document.removeEventListener('click', probe.listener, true);
            };
            document.addEventListener('click', probe.listener, true);
            probe.timer = setInterval(probe.sample, 50);
            probe.sample();
            window.__sendSwitchProbe = probe;
        }
        """,
        arg={
            "sourceSessionId": source_session_id,
            "targetSessionId": target_session_id,
        },
    )


def _wait_for_send_switch_probe(page: Page, *, timeout_ms: int) -> dict[str, object]:
    page.wait_for_function(
        """
        () => {
            const summary = window.__sendSwitchProbe?.summary?.();
            return !!(
                summary
                && summary.target_click_after_send_ms !== null
                && summary.target_stable_after_target_click_ms !== null
            );
        }
        """,
        timeout=timeout_ms,
    )
    summary = page.evaluate("() => window.__sendSwitchProbe.summary()")
    page.evaluate(
        """
        value => {
            window.__agentTeamsUiDiagnostics?.record?.(
                'send_switch_target_stable_ms',
                value,
                { scenario: 'send-switch-existing-session' },
            );
            window.__sendSwitchProbe?.stop?.();
        }
        """,
        arg=summary["target_stable_after_target_click_ms"],
    )
    return summary
