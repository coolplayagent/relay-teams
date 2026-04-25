from __future__ import annotations

import re
from pathlib import Path


CSS_MODULES = [
    "frontend/dist/css/components/messages/status.css",
    "frontend/dist/css/components/messages/base.css",
    "frontend/dist/css/components/messages/markdown.css",
    "frontend/dist/css/components/messages/thinking.css",
    "frontend/dist/css/components/messages/streaming.css",
    "frontend/dist/css/components/messages/blocks.css",
    "frontend/dist/css/components/messages/prompt.css",
    "frontend/dist/css/components/rounds/cards.css",
    "frontend/dist/css/components/rounds/detail.css",
    "frontend/dist/css/components/rounds/todo.css",
    "frontend/dist/css/components/rounds/history.css",
    "frontend/dist/css/components/rounds/retry.css",
    "frontend/dist/css/components/rounds/navigator.css",
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _significant_lines(text: str) -> list[str]:
    lines: list[str] = []
    in_comment = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("/*"):
            in_comment = "*/" not in stripped
            continue
        if in_comment:
            in_comment = "*/" not in stripped
            continue
        lines.append(stripped)
    return lines


def test_css_split_files_do_not_cross_rule_boundaries() -> None:
    property_line = re.compile(r"^[a-zA-Z-]+\s*:")

    for relative_path in CSS_MODULES:
        text = (_repo_root() / relative_path).read_text(encoding="utf-8")
        lines = _significant_lines(text)

        assert lines, relative_path
        assert text.count("{") == text.count("}"), relative_path
        assert not property_line.match(lines[0]), relative_path
        assert lines[0] != "}", relative_path
        assert not lines[-1].endswith("{"), relative_path
        assert not lines[-1].endswith(","), relative_path


def test_message_css_key_rules_live_in_their_modules() -> None:
    root = _repo_root()

    assert ".thinking-block {" in (
        root / "frontend/dist/css/components/messages/thinking.css"
    ).read_text(encoding="utf-8")
    assert ".streaming-cursor {" in (
        root / "frontend/dist/css/components/messages/streaming.css"
    ).read_text(encoding="utf-8")
    assert ".user-prompt-block {" in (
        root / "frontend/dist/css/components/messages/prompt.css"
    ).read_text(encoding="utf-8")
    assert ".msg-content blockquote," in (
        root / "frontend/dist/css/components/messages/blocks.css"
    ).read_text(encoding="utf-8")


def test_round_detail_header_keeps_status_badge_on_the_first_row() -> None:
    detail_css = (
        _repo_root() / "frontend/dist/css/components/rounds/detail.css"
    ).read_text(encoding="utf-8")

    assert ".round-detail-header {" in detail_css
    assert ".round-detail-topline {" in detail_css
    assert "display: grid;" in detail_css
    assert "grid-template-columns: minmax(0, 1fr) auto;" in detail_css
    assert ".round-detail-badges {" in detail_css
    assert "justify-self: end;" in detail_css
    assert "flex-wrap: nowrap;" in detail_css
    assert "white-space: nowrap;" in detail_css


def test_round_navigator_status_lives_on_timeline_dot() -> None:
    navigator_css = (
        _repo_root() / "frontend/dist/css/components/rounds/navigator.css"
    ).read_text(encoding="utf-8")
    navigator_js = (
        _repo_root() / "frontend/dist/js/components/rounds/navigator.js"
    ).read_text(encoding="utf-8")
    layout_css = (_repo_root() / "frontend/dist/css/layout.css").read_text(
        encoding="utf-8"
    )

    assert "border-left: 1px solid" not in navigator_css
    assert ".round-nav-node::before" not in navigator_css
    assert (
        "padding-right: calc(2rem + var(--round-timeline-width));" not in navigator_css
    )
    assert (
        '.chat-container[data-round-timeline-density="full"] .chat-scroll > * {'
        in navigator_css
    )
    assert (
        '.chat-container[data-round-timeline-density="compact"] .chat-scroll > * {'
        in navigator_css
    )
    assert (
        '.chat-container[data-round-timeline-density="dot"] .chat-scroll > * {'
        in navigator_css
    )
    assert (
        '.chat-container[data-round-timeline-density="hidden"] .chat-scroll > * {'
        in navigator_css
    )
    assert "width: min(1264px, calc(100% - 552px));" in navigator_css
    assert "width: min(1264px, calc(100% - 352px));" in navigator_css
    assert "width: min(1264px, calc(100% - 272px));" in navigator_css
    assert "width: min(1264px, 100%);" in navigator_css
    assert "bottom: var(--round-timeline-bottom, 9.5rem);" in navigator_css
    assert "background: transparent;" in navigator_css
    assert "pointer-events: none;" in navigator_css
    assert "pointer-events: auto;" in navigator_css
    assert ".round-nav-popover" not in navigator_css
    assert '[data-density="hidden"]' in navigator_css
    assert '[data-density="compact"] .round-nav-item .txt' in navigator_css
    assert '[data-density="dot"] .round-nav-item .txt' in navigator_css
    assert '[data-density="dot"] .round-nav-detail' in navigator_css
    assert "position: fixed;" in navigator_css
    assert "left: -9999px;" in navigator_css
    assert "top: -9999px;" in navigator_css
    assert "max-height: min(18rem, 52vh);" in navigator_css
    assert "overflow: auto;" in navigator_css
    assert "overscroll-behavior: contain;" in navigator_css
    assert "box-shadow: 0 8px 22px rgba(15, 23, 42, 0.08);" in navigator_css
    assert "transition: opacity 0.14s ease;" in navigator_css
    assert "transition: transform 0.16s ease, opacity 0.14s ease;" not in navigator_css
    assert ".round-nav-float.round-nav-animated .round-nav-node {" not in navigator_css
    assert (
        '.round-nav-node[data-round-nav-entering="true"] .round-nav-item {'
        not in navigator_css
    )
    assert "@keyframes round-nav-window-enter" not in navigator_css
    assert "animation-delay: var(--round-nav-enter-delay, 0ms);" not in navigator_css
    assert "visibility: hidden;" in navigator_css
    assert "visibility: visible;" in navigator_css
    assert (
        '[data-popover-positioned="true"][data-popover-open="true"] .round-nav-detail'
        in navigator_css
    )
    assert "@media (prefers-reduced-motion: reduce)" in navigator_css
    assert "will-change: transform;" not in navigator_css
    assert ".round-nav-list {\n    position: relative;" in navigator_css
    assert "overflow-y: auto;" in navigator_css
    assert ".round-nav-track {\n    position: relative;" in navigator_css
    assert ".round-nav-node {\n    position: relative;" in navigator_css
    assert "grid-template-columns: 0.64rem minmax(0, 1fr);" in navigator_css
    assert (
        ".round-nav-marker {\n    position: relative;\n    display: flex;\n    align-items: center;"
        in navigator_css
    )
    assert "min-height: 1.08rem;" in navigator_css
    assert "border: 0;" in navigator_css
    assert "box-shadow: none;" in navigator_css
    assert "border-color: color-mix(in srgb, var(--success)" not in navigator_css
    assert '.round-nav-node[data-anchor-state="above"],' in navigator_css
    assert '.round-nav-node[data-anchor-state="below"] {' in navigator_css
    assert (
        '.round-nav-node[data-state-tone="running"] .round-nav-dot {' in navigator_css
    )
    assert (
        '.round-nav-node[data-state-tone="success"] .round-nav-dot {' in navigator_css
    )
    assert (
        '.round-nav-node[data-state-tone="warning"] .round-nav-dot {' in navigator_css
    )
    assert '.round-nav-node[data-state-tone="danger"] .round-nav-dot {' in navigator_css
    assert "width: min(1264px, 100%);" in layout_css
    assert "pointerenter" not in navigator_js
    assert "pointerleave" not in navigator_js
    assert "scheduleRoundNavHoverLayout" not in navigator_js
    assert (
        "const item = node?.querySelector?.('.round-nav-item') || node;" in navigator_js
    )


def test_round_history_load_more_uses_explicit_button() -> None:
    timeline_js = (
        _repo_root() / "frontend/dist/js/components/rounds/timeline.js"
    ).read_text(encoding="utf-8")
    history_css = (
        _repo_root() / "frontend/dist/css/components/rounds/history.css"
    ).read_text(encoding="utf-8")
    navigator_css = (
        _repo_root() / "frontend/dist/css/components/rounds/navigator.css"
    ).read_text(encoding="utf-8")

    assert (
        "if (atTop) {\n"
        "        activateRoundSection(sections[0], estimateRoundVisibleScore());\n"
        "        return;\n"
        "    }"
    ) in timeline_js
    assert "renderHistoryLoadMoreControl()" in timeline_js
    assert "round-history-load-more-btn" in timeline_js
    assert "handleOlderRoundWheelIntent" in timeline_js
    assert "handleOlderRoundKeyIntent" in timeline_js
    assert "requestOlderRoundLoadFromUserIntent('scroll')" in timeline_js
    assert "requestOlderRoundLoadFromUserIntent('keyboard')" in timeline_js
    assert "loadOlderRounds({ source: 'button' })" in timeline_js
    assert "function roundsForNavigator()" in timeline_js
    assert "roundsState.timelineRounds.forEach" in timeline_js
    assert "roundsState.currentRounds.forEach" in timeline_js
    assert "...round," in timeline_js
    assert "return sortRoundsAscending(Array.from(byRunId.values()))" in timeline_js
    assert "loadOlderRoundPage({" in timeline_js
    assert "getRoundPageRunIds(page)" in timeline_js
    assert (
        "findRoundSectionForProgressiveHistoryPage(result.loadedRunIds)"
        not in timeline_js
    )
    assert "revealHistoryLoadMoreControl(container)" in timeline_js
    assert "let shouldRevealLoadControl = true;" not in timeline_js
    assert "revealLoadControl: true" in timeline_js
    assert "shouldRevealLoadControl = false;" not in timeline_js
    assert "options.revealLoadControl === true" in timeline_js
    assert "const page = await fetchOlderRoundsPage();" in timeline_js
    assert timeline_js.index(
        "await revealHistoryLoadMoreControl(container)"
    ) < timeline_js.index("const page = await fetchOlderRoundsPage();")
    assert "waitForHistoryLoadPaint(startedAt)" in timeline_js
    assert "anchor = captureHistoryLoadTopAnchor();" in timeline_js
    assert (
        "function captureHistoryLoadTopAnchor(container = els.chatMessages) {"
        in timeline_js
    )
    assert "roundsState.suppressNavigatorFollow = true;" in timeline_js
    assert "roundsState.suppressNavigatorFollow = false;" in timeline_js
    assert "animateChatScrollTo(container, nextTop" in timeline_js
    assert "syncHistoryLoadMoreAlignment(container)" in timeline_js
    assert "anchor.getBoundingClientRect" in timeline_js
    assert "control.style.marginLeft = 'auto'" in timeline_js
    assert "control.style.marginRight = 'auto'" in timeline_js
    assert "syncRoundContentCenter" not in timeline_js
    assert "--round-content-center-x" not in timeline_js
    assert "source: 'timeline'" in timeline_js
    assert "ROUND_TIMELINE_SCROLL_ANIMATION_MAX_MS = 2400" in timeline_js
    assert "lockProgrammaticRoundScroll(1600)" not in timeline_js
    assert "container.scrollTo({ top: nextTop, behavior: 'smooth' })" not in timeline_js
    assert "renderScrollToBottomIcon()" in timeline_js
    assert (
        "animateChatScrollToBottom({ durationMs: 520, lockMs: 900, syncActiveDuringScroll: true })"
        in timeline_js
    )
    assert "syncActiveRoundFromScroll({ allowProgrammatic: true })" in timeline_js
    assert "activateLatestRound(roundsState.currentRounds, {" in timeline_js
    assert "rounds.scroll_bottom.label" in timeline_js
    assert "rounds.load_more.label" in timeline_js
    assert "rounds.load_more.loading" in timeline_js
    assert "rounds.load_more.retry" in timeline_js
    assert ".chat-scroll.round-history-loading-older::before" not in history_css
    assert ".round-history-load-more {" in history_css
    assert ".round-history-load-more-btn {" in history_css
    assert ".round-history-load-more-icon {" in history_css
    assert ".round-history-load-more-arrow," in history_css
    assert ".round-scroll-bottom-btn {" in history_css
    assert '.round-scroll-bottom-btn[data-visible="true"]' in history_css
    assert ".round-scroll-bottom-icon {" in history_css
    assert "left: 50%;" in history_css
    assert "--round-content-center-x" not in history_css
    assert "border-top-color" not in history_css
    assert "@keyframes round-history-load-spin" in history_css
    assert (
        "grid-template-columns: minmax(0, 1fr) minmax(4.75rem, max-content);"
        in navigator_css
    )
    assert "min-width: 4.75rem;" in navigator_css
    assert "scrollbar-width: none;" in navigator_css
    assert ".round-nav-list::-webkit-scrollbar" in navigator_css
    assert "text-align: right;" in navigator_css
