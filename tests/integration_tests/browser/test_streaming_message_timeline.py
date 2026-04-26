from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from typing import cast
from urllib.parse import unquote
from urllib.parse import urlsplit

from playwright.sync_api import Page
from playwright.sync_api import sync_playwright
import pytest


_VIEWPORT_WIDTH = 1280
_VIEWPORT_HEIGHT = 900
_WAIT_TIMEOUT_MS = 10_000


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
                color_scheme="light",
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


def test_streamed_tool_args_match_persisted_history_in_browser(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    page = browser_page
    _open_harness(page, tmp_path)

    payload = page.evaluate(
        """
        () => window.__streamTimelineHarness.renderToolArgsParity()
        """
    )

    assert payload["livePreview"] == "Anthropic funding 2026"
    assert payload["persistedPreview"] == "Anthropic funding 2026"
    assert payload["persistedToolCount"] == 1
    assert payload["overlayAfterPersist"] is None


def test_session_switching_keeps_stream_overlays_isolated_in_browser(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    page = browser_page
    _open_harness(page, tmp_path)

    payload = page.evaluate(
        """
        () => window.__streamTimelineHarness.renderSessionSwitchIsolation()
        """
    )

    assert "S1 private thought" not in payload["sessionTwoText"]
    assert "S2 visible thought" in payload["sessionTwoText"]
    assert payload["sessionOneThinkingCount"] == 1
    assert payload["hydratedSessionOneThinkingCount"] == 1
    assert payload["hydratedSessionOneText"].count("S1 private thought") == 1


def test_main_history_overlay_dedupes_primary_alias_in_browser(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    page = browser_page
    _open_harness(page, tmp_path)

    payload = page.evaluate(
        """
        () => window.__streamTimelineHarness.renderMainPrimaryAliasDedup()
        """
    )

    assert payload["thinkingCount"] == 1
    assert payload["toolCount"] == 1
    assert payload["text"].count("DUP_THINK") == 1
    assert payload["overlayAfterPersist"] is None


def test_repeated_session_switch_stress_does_not_duplicate_stream_blocks_in_browser(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    page = browser_page
    _open_harness(page, tmp_path)

    payload = page.evaluate(
        """
        () => window.__streamTimelineHarness.renderRepeatedSessionSwitchStress()
        """
    )

    assert payload["iterations"] == 120
    assert payload["maxIntroOccurrences"] == 1
    assert payload["maxToolDuplicateCount"] == 1
    assert payload["foreignLeakCount"] == 0
    assert payload["finalRunAThinkingCount"] == 3
    assert payload["finalRunBThinkingCount"] == 3
    assert payload["overlayAfterFullRunA"] is None
    assert payload["overlayAfterFullRunB"] is None


def test_partial_overlay_replay_does_not_duplicate_earlier_thinking_blocks_in_browser(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    page = browser_page
    _open_harness(page, tmp_path)

    payload = page.evaluate(
        """
        () => window.__streamTimelineHarness.renderPartialThinkingReplayStress()
        """
    )

    assert payload["introOccurrences"] == 1
    assert payload["planOccurrences"] == 1
    assert payload["thinkingCount"] == 2
    assert payload["toolCount"] == 2
    assert payload["overlayAfterPersist"] is None


def test_direct_stream_state_isolated_across_concurrent_primary_runs_in_browser(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    page = browser_page
    _open_harness(page, tmp_path)

    payload = page.evaluate(
        """
        () => window.__streamTimelineHarness.renderConcurrentPrimaryDirectStreamStress()
        """
    )

    assert payload["runAText"].count("A late") == 1
    assert "A late" not in payload["runBText"]
    assert payload["runBText"].count("B first") == 1
    assert payload["runACursorCountAfterFinalize"] == 0


def test_empty_active_thinking_overlay_survives_history_replay_in_browser(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    page = browser_page
    _open_harness(page, tmp_path)

    payload = page.evaluate(
        """
        () => window.__streamTimelineHarness.renderEmptyActiveThinkingOverlay()
        """
    )

    assert payload["thinkingCount"] == 1
    assert payload["overlayAfterReplay"]["parts"][0]["finished"] is False


def test_missing_tool_call_ids_create_new_pending_overlay_invocations_in_browser(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    page = browser_page
    _open_harness(page, tmp_path)

    payload = page.evaluate(
        """
        () => window.__streamTimelineHarness.renderMissingToolCallIdReinvocation()
        """
    )

    assert payload["toolPartCount"] == 2
    assert payload["statuses"] == ["completed", "pending"]
    assert payload["results"] == [True, False]


def test_missing_tool_call_id_out_of_order_result_reuses_overlay_part_in_browser(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    page = browser_page
    _open_harness(page, tmp_path)

    payload = page.evaluate(
        """
        () => window.__streamTimelineHarness.renderMissingToolCallIdOutOfOrderResult()
        """
    )

    assert payload["toolPartCount"] == 1
    assert payload["status"] == "completed"
    assert payload["hasResult"] is True
    assert payload["args"] == {"command": "date"}


def test_ided_tool_result_reuses_pending_missing_id_tool_call_in_browser(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    page = browser_page
    _open_harness(page, tmp_path)

    payload = page.evaluate(
        """
        () => window.__streamTimelineHarness.renderIdedToolResultAfterMissingCallId()
        """
    )

    assert payload["toolPartCount"] == 1
    assert payload["status"] == "completed"
    assert payload["toolCallId"] == "call-shell-1"
    assert payload["hasResult"] is True
    assert payload["args"] == {"command": "date"}


def test_repeated_live_thinking_text_from_older_history_survives_replay_in_browser(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    page = browser_page
    _open_harness(page, tmp_path)

    payload = page.evaluate(
        """
        () => window.__streamTimelineHarness.renderRepeatedLiveThinkingTextFromOlderHistory()
        """
    )

    assert payload["olderPhraseOccurrences"] == 2
    assert payload["latestPhraseOccurrences"] == 1
    assert payload["thinkingCount"] == 3


def test_unfinished_thinking_with_persisted_prefix_survives_replay_in_browser(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    page = browser_page
    _open_harness(page, tmp_path)

    payload = page.evaluate(
        """
        () => window.__streamTimelineHarness.renderUnfinishedThinkingWithPersistedPrefix()
        """
    )

    assert payload["prefixOccurrences"] == 2
    assert payload["suffixOccurrences"] == 1
    assert payload["thinkingCount"] == 2


def test_run_stream_cleanup_clears_overlay_and_event_dedupe_in_browser(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    page = browser_page
    _open_harness(page, tmp_path)

    payload = page.evaluate(
        """
        () => window.__streamTimelineHarness.renderRunStreamCleanupReleasesOverlayAndDedupe()
        """
    )

    assert payload["beforeClearToolCount"] == 1
    assert payload["overlayAfterClear"] is None
    assert payload["afterReplayToolCount"] == 1


def test_output_delta_overlay_keeps_text_streaming_state_in_browser(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    page = browser_page
    _open_harness(page, tmp_path)

    payload = page.evaluate(
        """
        () => window.__streamTimelineHarness.renderOutputDeltaOverlayStreamingState()
        """
    )

    assert payload["textStreaming"] is True
    assert payload["cursorCount"] == 1


def test_persisted_media_ref_filters_finalized_stream_overlay_in_browser(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    page = browser_page
    _open_harness(page, tmp_path)

    payload = page.evaluate(
        """
        () => window.__streamTimelineHarness.renderPersistedMediaRefOverlayDedupe()
        """
    )

    assert payload["imageCount"] == 1
    assert payload["imageNames"] == ["image.png"]


def test_reused_media_ref_from_older_history_survives_overlay_in_browser(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    page = browser_page
    _open_harness(page, tmp_path)

    payload = page.evaluate(
        """
        () => window.__streamTimelineHarness.renderOlderMediaRefReuseOverlay()
        """
    )

    assert payload["imageCount"] == 2
    assert payload["imageNames"] == ["image.png", "image.png"]


def test_terminal_overlay_event_clears_event_dedupe_in_browser(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    page = browser_page
    _open_harness(page, tmp_path)

    payload = page.evaluate(
        """
        () => window.__streamTimelineHarness.renderTerminalOverlayEventClearsDedupe()
        """
    )

    assert payload["firstOccurrences"] == 1
    assert payload["secondOccurrences"] == 1
    assert payload["textStreaming"] is True


def test_replayed_stopped_session_events_do_not_duplicate_history_overlay_in_browser(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    page = browser_page
    _open_harness(page, tmp_path)

    payload = page.evaluate(
        """
        () => window.__streamTimelineHarness.renderStoppedReplayDedup()
        """
    )

    assert payload["firstThinkingCount"] == 1
    assert payload["secondThinkingCount"] == 1
    assert payload["firstToolCount"] == 1
    assert payload["secondToolCount"] == 1
    assert payload["firstCursorCount"] == 0
    assert payload["secondCursorCount"] == 0
    assert payload["secondGroupCount"] == 0
    assert payload["overlayAfterFirst"] is None
    assert payload["overlayAfterSecond"] is None


def test_unpersisted_thinking_overlay_renders_after_history_message_in_browser(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    page = browser_page
    _open_harness(page, tmp_path)

    payload = page.evaluate(
        """
        () => window.__streamTimelineHarness.renderThinkingOverlayPlacement()
        """
    )

    assert payload["messageCount"] == 2
    assert "live thought in progress" not in payload["firstMessageText"]
    assert "live thought in progress" in payload["secondMessageText"]


def test_late_tool_call_rebinds_after_stream_container_rerender_in_browser(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    page = browser_page
    _open_harness(page, tmp_path)

    payload = page.evaluate(
        """
        () => window.__streamTimelineHarness.renderDetachedStreamRebind()
        """
    )

    assert payload["beforeClearToolCount"] == 1
    assert payload["afterClearToolCount"] == 1
    assert payload["toolCallIds"] == ["call-2"]


def test_terminal_completed_overlay_does_not_block_processed_group_in_browser(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    page = browser_page
    _open_harness(page, tmp_path)

    payload = page.evaluate(
        """
        () => window.__streamTimelineHarness.renderTerminalCollapseWithCompletedOverlay()
        """
    )

    assert payload["groupCount"] == 1
    assert payload["groupToolCount"] == 1


def test_terminal_rounds_collapse_only_when_final_output_is_projected_in_browser(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    page = browser_page
    _open_harness(page, tmp_path)

    payload = page.evaluate(
        """
        () => window.__streamTimelineHarness.renderFinalMessageCollapseMatrix()
        """
    )

    assert payload["stoppedNoFinalGroupCount"] == 0
    assert payload["failedFinalGroupCount"] == 1
    assert payload["cancelledFinalGroupCount"] == 1
    assert payload["completedNoFinalGroupCount"] == 0
    assert "failed final answer" in payload["failedFinalText"]
    assert "loop middle output" in payload["completedNoFinalText"]
    assert "cancelled final answer" in payload["cancelledFinalText"]


def test_subagent_session_width_stays_stable_in_browser(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    page = browser_page
    _open_harness(page, tmp_path)

    payload = page.evaluate(
        """
        () => window.__streamTimelineHarness.measureSubagentSessionWidth()
        """
    )

    assert payload["beforeWidth"] == payload["afterWidth"]
    assert payload["beforeWithinScroll"] is True
    assert payload["afterWithinScroll"] is True


def _open_harness(page: Page, tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    html_path = tmp_path / "stream_timeline_harness.html"
    with _serve_harness_directory(repo_root, tmp_path) as base_url:
        stream_module = (
            f"{base_url}/frontend/dist/js/components/messageRenderer/stream.js"
        )
        history_module = (
            f"{base_url}/frontend/dist/js/components/messageRenderer/history.js"
        )
        html_path.write_text(
            f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>stream timeline harness</title>
  <link rel="stylesheet" href="{base_url}/frontend/dist/css/layout.css">
  <link rel="stylesheet" href="{base_url}/frontend/dist/css/components/subagent.css">
</head>
<body>
  <script type="module">
    import {{
      appendStreamChunk,
      appendThinkingChunk,
      appendToolCallBlock,
      applyStreamOverlayEvent,
      clearAllStreamState,
      clearRunStreamState,
      finalizeStream,
      finalizeThinking,
      getCoordinatorStreamOverlay,
      getOrCreateStreamBlock,
      startThinkingBlock,
    }} from {json.dumps(stream_module)};
    import {{
      renderHistoricalMessageList,
    }} from {json.dumps(history_module)};

    function makeContainer(id) {{
      const container = document.createElement('section');
      container.id = id;
      document.body.appendChild(container);
      return container;
    }}

    function renderHistory(container, messages, options) {{
      container.replaceChildren();
      renderHistoricalMessageList(container, messages, {{
        pendingToolApprovals: [],
        isLatestRound: true,
        ...options,
      }});
    }}

    function countSubstring(source, needle) {{
      const haystack = String(source || '');
      const target = String(needle || '');
      if (!target) return 0;
      return haystack.split(target).length - 1;
    }}

    function maxDuplicateToolCount(container) {{
      const counts = new Map();
      Array.from(container.querySelectorAll('.tool-block')).forEach(block => {{
        const key = block.dataset.toolCallId || block.textContent || '';
        counts.set(key, (counts.get(key) || 0) + 1);
      }});
      return Math.max(0, ...Array.from(counts.values()));
    }}

    function waitForAnimationFrame() {{
      return new Promise(resolve => {{
        window.requestAnimationFrame(() => window.requestAnimationFrame(resolve));
      }});
    }}

    window.__streamTimelineHarness = {{
      renderToolArgsParity() {{
        clearAllStreamState();
        const container = makeContainer('tool-args');
        applyStreamOverlayEvent(
          'tool_call',
          {{
            tool_name: 'websearch',
            tool_call_id: 'call-search',
            args: '{{"query":"Anthropic funding 2026"}}',
          }},
          {{
            runId: 'run-tool-args',
            instanceId: 'primary',
            roleId: 'main-role',
            label: 'Main Agent',
          }},
        );
        renderHistory(container, [], {{
          runId: 'run-tool-args',
          streamOverlayEntry: getCoordinatorStreamOverlay('run-tool-args'),
        }});
        const livePreview = container.querySelector('.tool-summary-preview')?.textContent?.trim() || '';
        renderHistory(container, [{{
          role: 'assistant',
          role_id: 'main-role',
          instance_id: 'primary',
          message: {{
            parts: [{{
              part_kind: 'tool-call',
              tool_name: 'websearch',
              tool_call_id: 'call-search',
              args: '{{"query":"Anthropic funding 2026"}}',
            }}],
          }},
        }}], {{
          runId: 'run-tool-args',
          runStatus: 'completed',
          streamOverlayEntry: getCoordinatorStreamOverlay('run-tool-args'),
        }});
        return {{
          livePreview,
          persistedPreview: container.querySelector('.tool-summary-preview')?.textContent?.trim() || '',
          persistedToolCount: container.querySelectorAll('.tool-block').length,
          overlayAfterPersist: getCoordinatorStreamOverlay('run-tool-args'),
        }};
      }},

      renderSessionSwitchIsolation() {{
        clearAllStreamState();
        const sessionOne = makeContainer('session-one');
        const sessionTwo = makeContainer('session-two');
        applyStreamOverlayEvent(
          'thinking_started',
          {{ part_index: 0 }},
          {{ runId: 'run-s1', instanceId: 'primary', roleId: 'main-role', label: 'Main Agent' }},
        );
        applyStreamOverlayEvent(
          'thinking_delta',
          {{ part_index: 0, text: 'S1 private thought' }},
          {{ runId: 'run-s1', instanceId: 'primary', roleId: 'main-role', label: 'Main Agent' }},
        );
        applyStreamOverlayEvent(
          'thinking_finished',
          {{ part_index: 0 }},
          {{ runId: 'run-s1', instanceId: 'primary', roleId: 'main-role', label: 'Main Agent' }},
        );
        applyStreamOverlayEvent(
          'run_completed',
          {{}},
          {{ runId: 'run-s1', instanceId: 'primary', roleId: 'main-role', label: 'Main Agent' }},
        );
        applyStreamOverlayEvent(
          'thinking_started',
          {{ part_index: 0 }},
          {{ runId: 'run-s2', instanceId: 'primary', roleId: 'main-role', label: 'Main Agent' }},
        );
        applyStreamOverlayEvent(
          'thinking_delta',
          {{ part_index: 0, text: 'S2 visible thought' }},
          {{ runId: 'run-s2', instanceId: 'primary', roleId: 'main-role', label: 'Main Agent' }},
        );
        renderHistory(sessionTwo, [], {{
          runId: 'run-s2',
          streamOverlayEntry: getCoordinatorStreamOverlay('run-s2'),
        }});
        renderHistory(sessionOne, [], {{
          runId: 'run-s1',
          streamOverlayEntry: getCoordinatorStreamOverlay('run-s1'),
        }});
        const sessionOneThinkingCount = sessionOne.querySelectorAll('.thinking-block').length;
        renderHistory(sessionOne, [{{
          role: 'assistant',
          role_id: 'main-role',
          instance_id: 'primary',
          message: {{
            parts: [{{
              part_kind: 'thinking',
              part_index: 0,
              content: 'S1 private thought',
            }}],
          }},
        }}], {{
          runId: 'run-s1',
          runStatus: 'completed',
          streamOverlayEntry: getCoordinatorStreamOverlay('run-s1'),
        }});
        return {{
          sessionTwoText: sessionTwo.textContent || '',
          sessionOneThinkingCount,
          hydratedSessionOneThinkingCount: sessionOne.querySelectorAll('.thinking-block').length,
          hydratedSessionOneText: sessionOne.textContent || '',
        }};
      }},

      renderMainPrimaryAliasDedup() {{
        clearAllStreamState();
        const container = makeContainer('primary-alias-dedup');
        const runId = 'run-primary-alias-dedup';
        applyStreamOverlayEvent(
          'thinking_started',
          {{ part_index: 0 }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'alias-1' }},
        );
        applyStreamOverlayEvent(
          'thinking_delta',
          {{ part_index: 0, text: 'DUP_THINK' }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'alias-2' }},
        );
        applyStreamOverlayEvent(
          'thinking_finished',
          {{ part_index: 0 }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'alias-3' }},
        );
        applyStreamOverlayEvent(
          'tool_call',
          {{
            tool_name: 'load_skill',
            tool_call_id: 'call-alias-load',
            args: {{ name: 'deepresearch' }},
          }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'alias-4' }},
        );
        renderHistory(container, [{{
          role: 'assistant',
          role_id: 'Coordinator',
          instance_id: 'inst-main-after-switch',
          created_at: '2026-04-26T09:46:41Z',
          message: {{
            parts: [
              {{ part_kind: 'thinking', part_index: 0, content: 'DUP_THINK' }},
              {{
                part_kind: 'tool-call',
                tool_name: 'load_skill',
                tool_call_id: 'call-alias-load',
                args: {{ name: 'deepresearch' }},
              }},
            ],
          }},
        }}], {{
          runId,
          runStatus: 'running',
          streamOverlayEntry: getCoordinatorStreamOverlay(runId),
          timelineView: 'main',
          canonicalStreamKey: 'primary',
        }});
        return {{
          thinkingCount: container.querySelectorAll('.thinking-block').length,
          toolCount: container.querySelectorAll('.tool-block').length,
          text: container.textContent || '',
          overlayAfterPersist: getCoordinatorStreamOverlay(runId),
        }};
      }},

      renderRepeatedSessionSwitchStress() {{
        clearAllStreamState();
        const container = makeContainer('session-switch-stress');
        const runA = 'session-17606bc3-run';
        const runB = 'session-70b72c62-run';
        const seedRun = (runId, label) => {{
          applyStreamOverlayEvent(
            'thinking_started',
            {{ part_index: 0 }},
            {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: `${{label}}-think-intro-start` }},
          );
          applyStreamOverlayEvent(
            'thinking_delta',
            {{ part_index: 0, text: `${{label}} intro thinking` }},
            {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: `${{label}}-think-intro-delta` }},
          );
          applyStreamOverlayEvent(
            'thinking_finished',
            {{ part_index: 0 }},
            {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: `${{label}}-think-intro-finish` }},
          );
          applyStreamOverlayEvent(
            'tool_call',
            {{
              tool_name: 'load_skill',
              tool_call_id: `${{label}}-load-deepresearch`,
              args: {{ name: 'deepresearch' }},
            }},
            {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: `${{label}}-tool-1` }},
          );
          applyStreamOverlayEvent(
            'tool_call',
            {{
              tool_name: 'load_skill',
              tool_call_id: `${{label}}-load-pptx`,
              args: {{ name: 'pptx-craft' }},
            }},
            {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: `${{label}}-tool-2` }},
          );
          applyStreamOverlayEvent(
            'thinking_started',
            {{ part_index: 1 }},
            {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: `${{label}}-think-plan-start` }},
          );
          applyStreamOverlayEvent(
            'thinking_delta',
            {{ part_index: 1, text: `${{label}} plan thinking` }},
            {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: `${{label}}-think-plan-delta` }},
          );
          applyStreamOverlayEvent(
            'thinking_finished',
            {{ part_index: 1 }},
            {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: `${{label}}-think-plan-finish` }},
          );
        }};
        const persistedPartial = (runId, label) => [{{
          role: 'assistant',
          role_id: 'Coordinator',
          instance_id: `${{label}}-persisted-instance-after-switch`,
          created_at: '2026-04-26T09:46:41Z',
          message: {{
            parts: [
              {{ part_kind: 'thinking', part_index: 0, content: `${{label}} intro thinking` }},
              {{
                part_kind: 'tool-call',
                tool_name: 'load_skill',
                tool_call_id: `${{label}}-load-deepresearch`,
                args: {{ name: 'deepresearch' }},
              }},
              {{
                part_kind: 'tool-call',
                tool_name: 'load_skill',
                tool_call_id: `${{label}}-load-pptx`,
                args: {{ name: 'pptx-craft' }},
              }},
            ],
          }},
        }}];
        const persistedFull = (runId, label) => [{{
          role: 'assistant',
          role_id: 'Coordinator',
          instance_id: `${{label}}-persisted-instance-after-switch`,
          created_at: '2026-04-26T09:46:41Z',
          message: {{
            parts: [
              {{ part_kind: 'thinking', part_index: 0, content: `${{label}} intro thinking` }},
              {{
                part_kind: 'tool-call',
                tool_name: 'load_skill',
                tool_call_id: `${{label}}-load-deepresearch`,
                args: {{ name: 'deepresearch' }},
              }},
              {{
                part_kind: 'tool-call',
                tool_name: 'load_skill',
                tool_call_id: `${{label}}-load-pptx`,
                args: {{ name: 'pptx-craft' }},
              }},
              {{ part_kind: 'thinking', part_index: 1, content: `${{label}} plan thinking` }},
              {{ part_kind: 'thinking', part_index: 2, content: `${{label}} final planning thought` }},
            ],
          }},
        }}];
        const renderRun = (runId, label, messages, runStatus = 'running') => {{
          renderHistory(container, messages, {{
            runId,
            runStatus,
            streamOverlayEntry: getCoordinatorStreamOverlay(runId),
            timelineView: 'main',
            canonicalStreamKey: 'primary',
          }});
          const text = container.textContent || '';
          return {{
            introOccurrences: countSubstring(text, `${{label}} intro thinking`),
            foreignOccurrences: countSubstring(text, `${{label === 'A' ? 'B' : 'A'}} intro thinking`),
            maxToolDuplicateCount: maxDuplicateToolCount(container),
          }};
        }};
        seedRun(runA, 'A');
        seedRun(runB, 'B');
        let maxIntroOccurrences = 0;
        let maxToolDuplicateCount = 0;
        let foreignLeakCount = 0;
        for (let i = 0; i < 120; i += 1) {{
          Object.defineProperty(document, 'hidden', {{
            configurable: true,
            value: i % 7 === 0,
          }});
          document.dispatchEvent(new Event('visibilitychange'));
          const label = i % 2 === 0 ? 'A' : 'B';
          const result = renderRun(
            label === 'A' ? runA : runB,
            label,
            persistedPartial(label === 'A' ? runA : runB, label),
          );
          maxIntroOccurrences = Math.max(maxIntroOccurrences, result.introOccurrences);
          maxToolDuplicateCount = Math.max(maxToolDuplicateCount, result.maxToolDuplicateCount);
          foreignLeakCount += result.foreignOccurrences;
        }}
        applyStreamOverlayEvent(
          'thinking_started',
          {{ part_index: 2 }},
          {{ runId: runA, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'A-think-final-start' }},
        );
        applyStreamOverlayEvent(
          'thinking_delta',
          {{ part_index: 2, text: 'A final planning thought' }},
          {{ runId: runA, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'A-think-final-delta' }},
        );
        applyStreamOverlayEvent(
          'thinking_finished',
          {{ part_index: 2 }},
          {{ runId: runA, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'A-think-final-finish' }},
        );
        applyStreamOverlayEvent(
          'thinking_started',
          {{ part_index: 2 }},
          {{ runId: runB, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'B-think-final-start' }},
        );
        applyStreamOverlayEvent(
          'thinking_delta',
          {{ part_index: 2, text: 'B final planning thought' }},
          {{ runId: runB, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'B-think-final-delta' }},
        );
        applyStreamOverlayEvent(
          'thinking_finished',
          {{ part_index: 2 }},
          {{ runId: runB, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'B-think-final-finish' }},
        );
        renderRun(runA, 'A', persistedFull(runA, 'A'), 'completed');
        const finalRunAThinkingCount = container.querySelectorAll('.thinking-block').length;
        const overlayAfterFullRunA = getCoordinatorStreamOverlay(runA);
        renderRun(runB, 'B', persistedFull(runB, 'B'), 'completed');
        const finalRunBThinkingCount = container.querySelectorAll('.thinking-block').length;
        const overlayAfterFullRunB = getCoordinatorStreamOverlay(runB);
        return {{
          iterations: 120,
          maxIntroOccurrences,
          maxToolDuplicateCount,
          foreignLeakCount,
          finalRunAThinkingCount,
          finalRunBThinkingCount,
          overlayAfterFullRunA,
          overlayAfterFullRunB,
        }};
      }},

      renderPartialThinkingReplayStress() {{
        clearAllStreamState();
        const container = makeContainer('partial-thinking-replay-stress');
        const runId = 'session-7f051512';
        const introPrefix = 'The user wants me to: use deepresearch and pptx-craft';
        const introFull = `${{introPrefix}} before loading both skills and planning the workflow.`;
        const planFull = 'Now I have both skills loaded. Let me plan the workflow in detail.';
        applyStreamOverlayEvent(
          'thinking_started',
          {{ part_index: 0 }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'partial-1' }},
        );
        applyStreamOverlayEvent(
          'thinking_delta',
          {{ part_index: 0, text: introPrefix }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'partial-2' }},
        );
        applyStreamOverlayEvent(
          'thinking_finished',
          {{ part_index: 0 }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'partial-3' }},
        );
        applyStreamOverlayEvent(
          'tool_call',
          {{
            tool_name: 'load_skill',
            tool_call_id: 'partial-load-deepresearch',
            args: {{ name: 'deepresearch' }},
          }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'partial-4' }},
        );
        applyStreamOverlayEvent(
          'tool_call',
          {{
            tool_name: 'load_skill',
            tool_call_id: 'partial-load-pptx',
            args: {{ name: 'pptx-craft' }},
          }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'partial-5' }},
        );
        applyStreamOverlayEvent(
          'thinking_started',
          {{ part_index: 1 }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'partial-6' }},
        );
        applyStreamOverlayEvent(
          'thinking_delta',
          {{ part_index: 1, text: planFull }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'partial-7' }},
        );
        applyStreamOverlayEvent(
          'thinking_finished',
          {{ part_index: 1 }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'partial-8' }},
        );
        const messages = [
          {{
            role: 'assistant',
            role_id: 'Coordinator',
            instance_id: 'persisted-main-before-switch',
            created_at: '2026-04-26T09:46:41Z',
            message: {{
              parts: [
                {{ part_kind: 'thinking', part_index: 0, content: introFull }},
                {{
                  part_kind: 'tool-call',
                  tool_name: 'load_skill',
                  tool_call_id: 'partial-load-deepresearch',
                  args: {{ name: 'deepresearch' }},
                }},
                {{
                  part_kind: 'tool-call',
                  tool_name: 'load_skill',
                  tool_call_id: 'partial-load-pptx',
                  args: {{ name: 'pptx-craft' }},
                }},
              ],
            }},
          }},
          {{
            role: 'assistant',
            role_id: 'Coordinator',
            instance_id: 'persisted-main-after-switch',
            created_at: '2026-04-26T09:46:49Z',
            message: {{
              parts: [
                {{ part_kind: 'thinking', part_index: 1, content: planFull }},
                {{ part_kind: 'text', content: 'Starting the long research loop.' }},
              ],
            }},
          }},
        ];
        for (let i = 0; i < 180; i += 1) {{
          Object.defineProperty(document, 'hidden', {{
            configurable: true,
            value: i % 5 === 0,
          }});
          document.dispatchEvent(new Event('visibilitychange'));
          renderHistory(container, messages, {{
            runId,
            runStatus: 'running',
            streamOverlayEntry: getCoordinatorStreamOverlay(runId),
            timelineView: 'main',
            canonicalStreamKey: 'primary',
          }});
        }}
        const text = container.textContent || '';
        return {{
          introOccurrences: countSubstring(text, introPrefix),
          planOccurrences: countSubstring(text, planFull),
          thinkingCount: container.querySelectorAll('.thinking-block').length,
          toolCount: container.querySelectorAll('.tool-block').length,
          overlayAfterPersist: getCoordinatorStreamOverlay(runId),
        }};
      }},

      async renderConcurrentPrimaryDirectStreamStress() {{
        clearAllStreamState();
        const runA = 'session-7f051512';
        const runB = 'session-8bcc5caa';
        const roleId = 'Coordinator';
        const containerA = makeContainer('direct-stream-run-a');
        const containerB = makeContainer('direct-stream-run-b');
        getOrCreateStreamBlock(containerA, 'primary', roleId, 'Main Agent', runA);
        appendStreamChunk('primary', 'A first', runA, roleId, 'Main Agent');
        startThinkingBlock('primary', 0, {{
          container: containerA,
          runId: runA,
          roleId,
          label: 'Main Agent',
        }});
        appendThinkingChunk('primary', 0, 'A thought', {{
          container: containerA,
          runId: runA,
          roleId,
          label: 'Main Agent',
        }});
        finalizeThinking('primary', 0, {{
          container: containerA,
          runId: runA,
          roleId,
        }});
        getOrCreateStreamBlock(containerB, 'primary', roleId, 'Main Agent', runB);
        appendStreamChunk('primary', 'B first', runB, roleId, 'Main Agent');
        appendStreamChunk('primary', ' A late', runA, roleId, 'Main Agent');
        finalizeStream('primary', roleId, {{ runId: runA }});
        await waitForAnimationFrame();
        return {{
          runAText: containerA.textContent || '',
          runBText: containerB.textContent || '',
          runACursorCountAfterFinalize: containerA.querySelectorAll('.streaming-cursor').length,
        }};
      }},

      renderEmptyActiveThinkingOverlay() {{
        clearAllStreamState();
        const container = makeContainer('empty-active-thinking-overlay');
        const runId = 'run-empty-thinking';
        applyStreamOverlayEvent(
          'thinking_started',
          {{ part_index: 3 }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'empty-think-1' }},
        );
        renderHistory(container, [], {{
          runId,
          runStatus: 'running',
          streamOverlayEntry: getCoordinatorStreamOverlay(runId),
          timelineView: 'main',
          canonicalStreamKey: 'primary',
        }});
        return {{
          thinkingCount: container.querySelectorAll('.thinking-block').length,
          overlayAfterReplay: getCoordinatorStreamOverlay(runId),
        }};
      }},

      renderMissingToolCallIdReinvocation() {{
        clearAllStreamState();
        const runId = 'run-missing-tool-call-id';
        applyStreamOverlayEvent(
          'tool_call',
          {{
            tool_name: 'shell',
            args: {{ command: 'date' }},
          }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'missing-tool-1' }},
        );
        applyStreamOverlayEvent(
          'tool_result',
          {{
            tool_name: 'shell',
            result: {{ ok: true }},
          }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'missing-tool-2' }},
        );
        applyStreamOverlayEvent(
          'tool_call',
          {{
            tool_name: 'shell',
            args: {{ command: 'pwd' }},
          }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'missing-tool-3' }},
        );
        const parts = getCoordinatorStreamOverlay(runId).parts.filter(part => part.kind === 'tool');
        return {{
          toolPartCount: parts.length,
          statuses: parts.map(part => part.status || ''),
          results: parts.map(part => part.result !== undefined),
        }};
      }},

      renderMissingToolCallIdOutOfOrderResult() {{
        clearAllStreamState();
        const runId = 'run-missing-tool-call-id-out-of-order';
        applyStreamOverlayEvent(
          'tool_result',
          {{
            tool_name: 'shell',
            result: {{ ok: true, output: 'done' }},
          }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'missing-tool-ooo-1' }},
        );
        applyStreamOverlayEvent(
          'tool_call',
          {{
            tool_name: 'shell',
            args: {{ command: 'date' }},
          }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'missing-tool-ooo-2' }},
        );
        const parts = getCoordinatorStreamOverlay(runId).parts.filter(part => part.kind === 'tool');
        const part = parts[0] || {{}};
        return {{
          toolPartCount: parts.length,
          status: part.status || '',
          hasResult: part.result !== undefined,
          args: part.args || {{}},
        }};
      }},

      renderIdedToolResultAfterMissingCallId() {{
        clearAllStreamState();
        const runId = 'run-ided-tool-result-after-missing-call-id';
        applyStreamOverlayEvent(
          'tool_call',
          {{
            tool_name: 'shell',
            args: {{ command: 'date' }},
          }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'missing-tool-later-id-1' }},
        );
        applyStreamOverlayEvent(
          'tool_result',
          {{
            tool_name: 'shell',
            tool_call_id: 'call-shell-1',
            result: {{ ok: true, output: 'done' }},
          }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'missing-tool-later-id-2' }},
        );
        const parts = getCoordinatorStreamOverlay(runId).parts.filter(part => part.kind === 'tool');
        const part = parts[0] || {{}};
        return {{
          toolPartCount: parts.length,
          status: part.status || '',
          toolCallId: part.tool_call_id || '',
          hasResult: part.result !== undefined,
          args: part.args || {{}},
        }};
      }},

      renderRepeatedLiveThinkingTextFromOlderHistory() {{
        clearAllStreamState();
        const container = makeContainer('repeated-live-thinking-text');
        const runId = 'run-repeated-live-thinking';
        const olderPhrase = 'Now let me plan the workflow.';
        const latestPhrase = 'Latest persisted thinking tail.';
        applyStreamOverlayEvent(
          'thinking_started',
          {{ part_index: 2 }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'repeat-live-1' }},
        );
        applyStreamOverlayEvent(
          'thinking_delta',
          {{ part_index: 2, text: olderPhrase }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'repeat-live-2' }},
        );
        renderHistory(container, [
          {{
            role: 'assistant',
            role_id: 'Coordinator',
            instance_id: 'primary',
            created_at: '2026-04-26T09:46:41Z',
            message: {{
              parts: [{{ part_kind: 'thinking', part_index: 0, content: olderPhrase }}],
            }},
          }},
          {{
            role: 'assistant',
            role_id: 'Coordinator',
            instance_id: 'primary',
            created_at: '2026-04-26T09:46:49Z',
            message: {{
              parts: [{{ part_kind: 'thinking', part_index: 1, content: latestPhrase }}],
            }},
          }},
        ], {{
          runId,
          runStatus: 'running',
          streamOverlayEntry: getCoordinatorStreamOverlay(runId),
          timelineView: 'main',
          canonicalStreamKey: 'primary',
        }});
        const text = container.textContent || '';
        return {{
          olderPhraseOccurrences: countSubstring(text, olderPhrase),
          latestPhraseOccurrences: countSubstring(text, latestPhrase),
          thinkingCount: container.querySelectorAll('.thinking-block').length,
        }};
      }},

      renderUnfinishedThinkingWithPersistedPrefix() {{
        clearAllStreamState();
        const container = makeContainer('unfinished-thinking-prefix');
        const runId = 'run-unfinished-thinking-prefix';
        const prefix = 'Now let me analyze the session switching timeline carefully.';
        const suffix = ' This live suffix must remain visible.';
        applyStreamOverlayEvent(
          'thinking_started',
          {{ part_index: 2 }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'thinking-prefix-1' }},
        );
        applyStreamOverlayEvent(
          'thinking_delta',
          {{ part_index: 2, text: `${{prefix}}${{suffix}}` }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'thinking-prefix-2' }},
        );
        renderHistory(container, [{{
          role: 'assistant',
          role_id: 'Coordinator',
          instance_id: 'primary',
          created_at: '2026-04-26T09:46:41Z',
          message: {{
            parts: [{{ part_kind: 'thinking', part_index: 2, content: prefix }}],
          }},
        }}], {{
          runId,
          runStatus: 'running',
          streamOverlayEntry: getCoordinatorStreamOverlay(runId),
          timelineView: 'main',
          canonicalStreamKey: 'primary',
        }});
        const text = container.textContent || '';
        return {{
          prefixOccurrences: countSubstring(text, prefix),
          suffixOccurrences: countSubstring(text, suffix.trim()),
          thinkingCount: container.querySelectorAll('.thinking-block').length,
        }};
      }},

      renderRunStreamCleanupReleasesOverlayAndDedupe() {{
        clearAllStreamState();
        const runId = 'run-cleanup-dedupe';
        const emitToolCall = () => applyStreamOverlayEvent(
          'tool_call',
          {{
            tool_name: 'shell',
            tool_call_id: 'call-cleanup',
            args: {{ command: 'date' }},
          }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'cleanup-evt-1' }},
        );
        emitToolCall();
        const beforeClearToolCount = getCoordinatorStreamOverlay(runId)?.parts?.length || 0;
        clearRunStreamState(runId);
        const overlayAfterClear = getCoordinatorStreamOverlay(runId);
        emitToolCall();
        const afterReplayToolCount = getCoordinatorStreamOverlay(runId)?.parts?.length || 0;
        return {{
          beforeClearToolCount,
          overlayAfterClear,
          afterReplayToolCount,
        }};
      }},

      renderOutputDeltaOverlayStreamingState() {{
        clearAllStreamState();
        const container = makeContainer('output-delta-overlay-streaming');
        const runId = 'run-output-delta-overlay';
        applyStreamOverlayEvent(
          'output_delta',
          {{
            output: [{{ kind: 'text', text: 'streamed output delta text' }}],
          }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'output-delta-1' }},
        );
        const overlay = getCoordinatorStreamOverlay(runId);
        renderHistory(container, [], {{
          runId,
          runStatus: 'running',
          streamOverlayEntry: overlay,
          timelineView: 'main',
          canonicalStreamKey: 'primary',
        }});
        return {{
          textStreaming: overlay?.textStreaming === true,
          cursorCount: container.querySelectorAll('.streaming-cursor').length,
        }};
      }},

      renderPersistedMediaRefOverlayDedupe() {{
        clearAllStreamState();
        const container = makeContainer('persisted-media-ref-overlay-dedupe');
        const runId = 'run-persisted-media-ref-overlay-dedupe';
        const mediaPart = {{
          kind: 'media_ref',
          modality: 'image',
          mime_type: 'image/png',
          url: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=',
          name: 'image.png',
        }};
        applyStreamOverlayEvent(
          'output_delta',
          {{ output: [mediaPart] }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'media-dedupe-1' }},
        );
        renderHistory(container, [{{
          role: 'assistant',
          role_id: 'Coordinator',
          instance_id: 'primary',
          message: {{
            parts: [mediaPart],
          }},
        }}], {{
          runId,
          runStatus: 'completed',
          streamOverlayEntry: getCoordinatorStreamOverlay(runId),
          timelineView: 'main',
          canonicalStreamKey: 'primary',
        }});
        return {{
          imageCount: container.querySelectorAll('.msg-image-preview').length,
          imageNames: Array.from(container.querySelectorAll('.msg-image-preview'))
            .map(image => image.getAttribute('data-image-preview-name') || ''),
        }};
      }},

      renderOlderMediaRefReuseOverlay() {{
        clearAllStreamState();
        const container = makeContainer('older-media-ref-reuse-overlay');
        const runId = 'run-older-media-ref-reuse-overlay';
        const mediaPart = {{
          kind: 'media_ref',
          modality: 'image',
          mime_type: 'image/png',
          url: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=',
          name: 'image.png',
        }};
        applyStreamOverlayEvent(
          'output_delta',
          {{ output: [mediaPart] }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'media-reuse-1' }},
        );
        renderHistory(container, [
          {{
            role: 'assistant',
            role_id: 'Coordinator',
            instance_id: 'primary',
            message: {{
              parts: [mediaPart],
            }},
          }},
          {{
            role: 'assistant',
            role_id: 'Coordinator',
            instance_id: 'primary',
            message: {{
              parts: [{{ part_kind: 'text', content: 'newer persisted text' }}],
            }},
          }},
        ], {{
          runId,
          runStatus: 'running',
          streamOverlayEntry: getCoordinatorStreamOverlay(runId),
          timelineView: 'main',
          canonicalStreamKey: 'primary',
        }});
        return {{
          imageCount: container.querySelectorAll('.msg-image-preview').length,
          imageNames: Array.from(container.querySelectorAll('.msg-image-preview'))
            .map(image => image.getAttribute('data-image-preview-name') || ''),
        }};
      }},

      renderTerminalOverlayEventClearsDedupe() {{
        clearAllStreamState();
        const runId = 'run-terminal-clears-overlay-dedupe';
        applyStreamOverlayEvent(
          'text_delta',
          {{ text: 'first lifecycle text' }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'repeat-event-id' }},
        );
        applyStreamOverlayEvent(
          'run_completed',
          {{}},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'terminal-event-id' }},
        );
        applyStreamOverlayEvent(
          'text_delta',
          {{ text: 'second lifecycle text' }},
          {{ runId, instanceId: 'primary', roleId: 'Coordinator', label: 'Main Agent', eventId: 'repeat-event-id' }},
        );
        const overlay = getCoordinatorStreamOverlay(runId);
        const text = overlay?.parts
          ?.filter(part => part.kind === 'text')
          ?.map(part => part.content || '')
          ?.join('\\n') || '';
        return {{
          firstOccurrences: countSubstring(text, 'first lifecycle text'),
          secondOccurrences: countSubstring(text, 'second lifecycle text'),
          textStreaming: overlay?.textStreaming === true,
        }};
      }},

      renderStoppedReplayDedup() {{
        clearAllStreamState();
        const container = makeContainer('stopped-replay');
        const runId = 'run-stopped-replay';
        const messages = [{{
          role: 'assistant',
          role_id: 'main-role',
          instance_id: 'primary',
          created_at: '2026-04-25T12:00:02Z',
          message: {{
            parts: [
              {{
                part_kind: 'thinking',
                part_index: 0,
                content: 'persisted thought',
              }},
              {{
                part_kind: 'tool-call',
                tool_name: 'shell',
                tool_call_id: 'call-1',
                args: {{ command: 'date' }},
              }},
              {{
                part_kind: 'text',
                content: 'final answer',
              }},
            ],
          }},
        }}];
        const events = [
          ['thinking_started', {{ part_index: 0 }}, 'evt-1'],
          ['thinking_delta', {{ part_index: 0, text: 'persisted thought' }}, 'evt-2'],
          ['thinking_finished', {{ part_index: 0 }}, 'evt-3'],
          [
            'tool_call',
            {{
              tool_name: 'shell',
              tool_call_id: 'call-1',
              args: {{ command: 'date' }},
            }},
            'evt-4',
          ],
          [
            'tool_result',
            {{
              tool_name: 'shell',
              tool_call_id: 'call-1',
              result: {{ ok: true, output: 'done' }},
            }},
            'evt-5',
          ],
          ['run_stopped', {{}}, 'evt-6'],
        ];
        const replayEvents = () => {{
          events.forEach(([type, payload, eventId]) => {{
            applyStreamOverlayEvent(type, payload, {{
              runId,
              instanceId: 'primary',
              roleId: 'main-role',
              label: 'Main Agent',
              eventId,
            }});
          }});
        }};
        replayEvents();
        renderHistory(container, messages, {{
          runId,
          runStatus: 'stopped',
          streamOverlayEntry: getCoordinatorStreamOverlay(runId),
        }});
        const firstThinkingCount = container.querySelectorAll('.thinking-block').length;
        const firstToolCount = container.querySelectorAll('.tool-block').length;
        const firstCursorCount = container.querySelectorAll('.streaming-cursor').length;
        const overlayAfterFirst = getCoordinatorStreamOverlay(runId);
        replayEvents();
        renderHistory(container, messages, {{
          runId,
          runStatus: 'stopped',
          streamOverlayEntry: getCoordinatorStreamOverlay(runId),
        }});
        return {{
          firstThinkingCount,
          firstToolCount,
          firstCursorCount,
          overlayAfterFirst,
          secondThinkingCount: container.querySelectorAll('.thinking-block').length,
          secondToolCount: container.querySelectorAll('.tool-block').length,
          secondCursorCount: container.querySelectorAll('.streaming-cursor').length,
          secondGroupCount: container.querySelectorAll('.tool-group').length,
          overlayAfterSecond: getCoordinatorStreamOverlay(runId),
        }};
      }},

      renderThinkingOverlayPlacement() {{
        clearAllStreamState();
        const container = makeContainer('thinking-placement');
        applyStreamOverlayEvent(
          'thinking_started',
          {{ part_index: 0 }},
          {{ runId: 'run-placement', instanceId: 'primary', roleId: 'main-role', label: 'Main Agent' }},
        );
        applyStreamOverlayEvent(
          'thinking_delta',
          {{ part_index: 0, text: 'live thought in progress' }},
          {{ runId: 'run-placement', instanceId: 'primary', roleId: 'main-role', label: 'Main Agent' }},
        );
        renderHistory(container, [{{
          role: 'assistant',
          role_id: 'main-role',
          instance_id: 'primary',
          message: {{
            parts: [{{ part_kind: 'text', content: 'persisted final answer' }}],
          }},
        }}], {{
          runId: 'run-placement',
          streamOverlayEntry: getCoordinatorStreamOverlay('run-placement'),
        }});
        const messages = Array.from(container.querySelectorAll(':scope > .message'));
        return {{
          messageCount: messages.length,
          firstMessageText: messages[0]?.textContent || '',
          secondMessageText: messages[1]?.textContent || '',
        }};
      }},

      renderDetachedStreamRebind() {{
        clearAllStreamState();
        const container = makeContainer('detached-rebind');
        getOrCreateStreamBlock(container, 'inst-live', 'Writer', 'Writer', 'subagent_run_live');
        appendToolCallBlock(
          container,
          'inst-live',
          'shell',
          {{ command: 'echo before' }},
          'call-1',
          {{ runId: 'subagent_run_live', roleId: 'Writer', label: 'Writer' }},
        );
        const beforeClearToolCount = container.querySelectorAll('.tool-block').length;
        container.replaceChildren();
        appendToolCallBlock(
          container,
          'inst-live',
          'write_file',
          {{ path: 'page.svg' }},
          'call-2',
          {{ runId: 'subagent_run_live', roleId: 'Writer', label: 'Writer' }},
        );
        return {{
          beforeClearToolCount,
          afterClearToolCount: container.querySelectorAll('.tool-block').length,
          toolCallIds: Array.from(container.querySelectorAll('.tool-block'))
            .map(item => item.dataset.toolCallId || ''),
        }};
      }},

      renderTerminalCollapseWithCompletedOverlay() {{
        clearAllStreamState();
        const container = makeContainer('terminal-collapse');
        container.dataset.roundCreatedAt = '2026-04-25T12:00:00Z';
        renderHistory(container, [{{
          role: 'assistant',
          role_id: 'main-role',
          instance_id: 'primary',
          created_at: '2026-04-25T12:00:02Z',
          message: {{
            parts: [{{ part_kind: 'text', content: 'planning complete' }}],
          }},
        }}], {{
          runId: 'run-terminal-collapse',
          runStatus: 'completed',
          hasFinalOutput: true,
          streamOverlayEntry: {{
            roleId: 'main-role',
            instanceId: 'primary',
            streamKey: 'primary',
            label: 'Main Agent',
            parts: [{{
              kind: 'tool',
              tool_name: 'write_file',
              tool_call_id: 'call-final',
              args: {{ path: 'page.svg' }},
              status: 'completed',
              result: {{ ok: true }},
            }}],
            textStreaming: false,
            idleCursor: false,
          }},
        }});
        return {{
          groupCount: container.querySelectorAll('.tool-group').length,
          groupToolCount: container.querySelectorAll('.tool-group .tool-block').length,
        }};
      }},

      renderFinalMessageCollapseMatrix() {{
        clearAllStreamState();
        const renderCase = (id, runStatus, hasFinalOutput, parts) => {{
          const container = makeContainer(id);
          container.dataset.roundCreatedAt = '2026-04-25T12:00:00Z';
          renderHistory(container, [{{
            role: 'assistant',
            role_id: 'main-role',
            instance_id: 'primary',
            created_at: '2026-04-25T12:00:02Z',
            message: {{ parts }},
          }}], {{
            runId: `run-${{id}}`,
            runStatus,
            hasFinalOutput,
            streamOverlayEntry: null,
          }});
          return {{
            groupCount: container.querySelectorAll('.tool-group').length,
            text: container.textContent || '',
          }};
        }};
        const stoppedNoFinal = renderCase('stopped-no-final', 'stopped', false, [
          {{ part_kind: 'thinking', part_index: 0, content: 'stopped thought' }},
          {{
            part_kind: 'tool-call',
            tool_name: 'shell',
            tool_call_id: 'call-stopped',
            args: {{ command: 'date' }},
          }},
        ]);
        const failedFinal = renderCase('failed-final', 'failed', true, [
          {{ part_kind: 'thinking', part_index: 0, content: 'failed thought' }},
          {{
            part_kind: 'tool-call',
            tool_name: 'shell',
            tool_call_id: 'call-failed',
            args: {{ command: 'date' }},
          }},
          {{ part_kind: 'text', content: 'failed final answer' }},
        ]);
        const cancelledFinal = renderCase('cancelled-final', 'cancelled', true, [
          {{ part_kind: 'thinking', part_index: 0, content: 'cancelled thought' }},
          {{ part_kind: 'text', content: 'cancelled final answer' }},
        ]);
        const completedNoFinal = renderCase('completed-no-final', 'completed', false, [
          {{ part_kind: 'thinking', part_index: 0, content: 'completed thought' }},
          {{
            part_kind: 'tool-call',
            tool_name: 'shell',
            tool_call_id: 'call-completed',
            args: {{ command: 'date' }},
          }},
          {{ part_kind: 'text', content: 'loop middle output' }},
        ]);
        return {{
          stoppedNoFinalGroupCount: stoppedNoFinal.groupCount,
          failedFinalGroupCount: failedFinal.groupCount,
          cancelledFinalGroupCount: cancelledFinal.groupCount,
          completedNoFinalGroupCount: completedNoFinal.groupCount,
          failedFinalText: failedFinal.text,
          cancelledFinalText: cancelledFinal.text,
          completedNoFinalText: completedNoFinal.text,
        }};
      }},

      measureSubagentSessionWidth() {{
        const shell = document.createElement('main');
        shell.id = 'chat-container';
        shell.style.width = '960px';
        shell.style.height = '400px';
        shell.style.display = 'block';
        const scroll = document.createElement('div');
        scroll.className = 'chat-scroll';
        scroll.style.width = '960px';
        scroll.style.height = '400px';
        const wrapper = document.createElement('section');
        wrapper.className = 'subagent-session-view';
        const body = document.createElement('div');
        body.className = 'subagent-session-body';
        wrapper.appendChild(body);
        scroll.appendChild(wrapper);
        shell.appendChild(scroll);
        document.body.appendChild(shell);
        const beforeWidth = Math.round(wrapper.getBoundingClientRect().width);
        body.appendChild(document.createElement('div'));
        const afterWidth = Math.round(wrapper.getBoundingClientRect().width);
        const scrollWidth = Math.round(scroll.getBoundingClientRect().width);
        return {{
          beforeWidth,
          afterWidth,
          beforeWithinScroll: beforeWidth <= scrollWidth,
          afterWithinScroll: afterWidth <= scrollWidth,
        }};
      }},
    }};
    window.__streamTimelineHarnessReady = true;
  </script>
</body>
</html>
""".strip(),
            encoding="utf-8",
        )
        page.goto(f"{base_url}/{html_path.name}")
        page.wait_for_function(
            "() => window.__streamTimelineHarnessReady === true",
            timeout=_WAIT_TIMEOUT_MS,
        )


@contextmanager
def _serve_harness_directory(repo_root: Path, harness_root: Path) -> Iterator[str]:
    class Handler(SimpleHTTPRequestHandler):
        def translate_path(self, path: str) -> str:
            request_path = unquote(urlsplit(path).path).lstrip("/")
            if request_path.startswith("frontend/"):
                return str(repo_root / request_path)
            return str(harness_root / request_path)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = cast(tuple[str, int], server.server_address)
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _resolve_playwright_browser_root() -> Path:
    env_value = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    candidates: list[Path] = []
    if env_value:
        candidates.append(Path(env_value).expanduser())
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data).expanduser() / "ms-playwright")
        user_profile = os.environ.get("USERPROFILE")
        if user_profile:
            candidates.append(
                Path(user_profile).expanduser() / "AppData" / "Local" / "ms-playwright"
            )
    candidates.append(Path.home() / ".cache" / "ms-playwright")
    for candidate in candidates:
        if _has_playwright_chromium(candidate):
            return candidate
    return candidates[0] if candidates else Path.home() / ".cache" / "ms-playwright"


def _has_playwright_chromium(path: Path) -> bool:
    if not path.exists():
        return False
    executable_names = {
        "chrome",
        "chrome.exe",
        "chrome-headless-shell",
        "chrome-headless-shell.exe",
        "Chromium",
    }
    for child in path.glob("chromium*"):
        if not child.is_dir():
            continue
        for executable in child.rglob("*"):
            if executable.name in executable_names and executable.is_file():
                return True
    return False
