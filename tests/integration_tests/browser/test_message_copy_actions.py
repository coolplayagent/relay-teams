from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import threading
from typing import cast
from urllib.parse import unquote
from urllib.parse import urlsplit

from playwright.sync_api import Page
from playwright.sync_api import sync_playwright
import pytest


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
                viewport={"width": 1280, "height": 900},
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


def test_last_answer_copy_button_copies_only_latest_answer(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    _open_message_copy_harness(browser_page, tmp_path)

    payload = cast(
        dict[str, object],
        browser_page.evaluate(
            """
        async () => {
          window.__runMessageCopySync();
          const buttons = Array.from(document.querySelectorAll('.message-copy-btn'));
          buttons[0]?.click();
          await new Promise(resolve => setTimeout(resolve, 0));
          return {
            buttonCount: buttons.length,
            buttonMessageId: buttons[0]?.closest('.message')?.id || '',
            oldAnswerButtonCount: document.querySelectorAll('#old-answer .message-copy-btn').length,
            userButtonCount: document.querySelectorAll('#user-message .message-copy-btn').length,
            copiedText: window.__copiedText[0] || '',
          };
        }
        """
        ),
    )

    assert payload == {
        "buttonCount": 1,
        "buttonMessageId": "latest-answer",
        "oldAnswerButtonCount": 0,
        "userButtonCount": 0,
        "copiedText": 'Latest answer\n\nif ok:\n    print("yes")',
    }


def test_copy_button_waits_until_latest_answer_is_stable(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    _open_message_copy_harness(browser_page, tmp_path)

    payload = cast(
        dict[str, object],
        browser_page.evaluate(
            """
        () => {
          window.__runMessageCopySync();
          const stableCount = document.querySelectorAll('.message-copy-btn').length;
          const liveMessage = document.createElement('article');
          liveMessage.className = 'message';
          liveMessage.dataset.role = 'model';
          liveMessage.id = 'live-answer';
          liveMessage.innerHTML = `
            <div class="msg-header"><span class="msg-role role-agent">AGENT</span></div>
            <div class="msg-content">
              <div class="msg-text">Streaming answer<span class="streaming-cursor"></span></div>
            </div>
          `;
          document.getElementById('chat-messages').appendChild(liveMessage);
          window.__runMessageCopySync();
          const liveCount = document.querySelectorAll('.message-copy-btn').length;
          const liveOwner = document.querySelector('.message-copy-btn')?.closest('.message')?.id || '';
          liveMessage.querySelector('.streaming-cursor').remove();
          window.__runMessageCopySync();
          const finalButton = document.querySelector('.message-copy-btn');
          return {
            stableCount,
            liveCount,
            liveOwner,
            finalCount: document.querySelectorAll('.message-copy-btn').length,
            finalOwner: finalButton?.closest('.message')?.id || '',
          };
        }
        """
        ),
    )

    assert payload == {
        "stableCount": 1,
        "liveCount": 0,
        "liveOwner": "",
        "finalCount": 1,
        "finalOwner": "live-answer",
    }


def test_copy_button_syncs_after_detached_history_mount(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    _open_message_copy_harness(browser_page, tmp_path)

    payload = cast(
        dict[str, object],
        browser_page.evaluate(
            """
        () => {
          const root = document.getElementById('chat-messages');
          root.replaceChildren();
          const section = document.createElement('section');
          section.innerHTML = `
            <article class="message" data-role="model" id="detached-answer">
              <div class="msg-header"><span class="msg-role role-agent">AGENT</span></div>
              <div class="msg-content"><div class="msg-text">Mounted final answer</div></div>
            </article>
          `;
          window.__syncMessageCopyTarget(section);
          const beforeMount = section.querySelectorAll('.message-copy-btn').length;
          root.appendChild(section);
          window.__runMessageCopySync();
          return {
            beforeMount,
            afterMount: root.querySelectorAll('.message-copy-btn').length,
            owner: root.querySelector('.message-copy-btn')?.closest('.message')?.id || '',
          };
        }
        """
        ),
    )

    assert payload == {
        "beforeMount": 0,
        "afterMount": 1,
        "owner": "detached-answer",
    }


def test_bound_intent_copy_button_copies_prompt_without_toggling_summary(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    _open_message_copy_harness(browser_page, tmp_path)

    payload = cast(
        dict[str, object],
        browser_page.evaluate(
            """
        async () => {
          const detail = document.createElement('details');
          detail.open = true;
          const summary = document.createElement('summary');
          const summaryButton = document.createElement('button');
          summaryButton.className = 'round-detail-intent-copy';
          summary.appendChild(summaryButton);
          const body = document.createElement('div');
          const bodyButton = document.createElement('button');
          bodyButton.className = 'round-detail-intent-copy';
          body.appendChild(bodyButton);
          detail.append(summary, body);
          document.body.appendChild(detail);

          window.__bindCopyButton(summaryButton, 'first user intent');
          window.__bindCopyButton(bodyButton, 'second user intent');
          const keyEvent = new KeyboardEvent('keydown', {
            bubbles: true,
            cancelable: true,
            key: 'Enter',
          });
          const keyDispatchResult = summaryButton.dispatchEvent(keyEvent);
          const openAfterSummaryButtonKeydown = detail.open;
          summaryButton.click();
          await new Promise(resolve => setTimeout(resolve, 0));
          const openAfterSummaryCopy = detail.open;
          bodyButton.click();
          await new Promise(resolve => setTimeout(resolve, 0));

          return {
            openAfterSummaryCopy,
            openAfterSummaryButtonKeydown,
            summaryButtonKeydownCanceled: keyDispatchResult === false || keyEvent.defaultPrevented,
            copiedText: window.__copiedText.slice(-2),
            summaryButtonClass: summaryButton.className,
            bodyButtonLabel: bodyButton.getAttribute('aria-label') || '',
          };
        }
        """
        ),
    )

    assert payload["bodyButtonLabel"] in {"Copy", "复制"}
    assert payload == {
        "openAfterSummaryCopy": True,
        "openAfterSummaryButtonKeydown": True,
        "summaryButtonKeydownCanceled": False,
        "copiedText": ["first user intent", "second user intent"],
        "summaryButtonClass": "round-detail-intent-copy message-copy-btn is-copied",
        "bodyButtonLabel": payload["bodyButtonLabel"],
    }


def test_round_intent_toggle_survives_streaming_patch_and_overlap(
    browser_page: Page,
    tmp_path: Path,
) -> None:
    _open_round_intent_harness(browser_page, tmp_path)

    payload = cast(
        dict[str, object],
        browser_page.evaluate(
            """
        async () => window.__runRoundIntentScenario()
        """
        ),
    )

    assert payload == {
        "overflow": "true",
        "openedBeforePatch": True,
        "openAfterPatch": True,
        "closedBeforePatch": True,
        "closedAfterPatch": True,
        "toggleHit": True,
    }


def _open_message_copy_harness(page: Page, tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    html_path = tmp_path / "message_copy_actions.html"
    with _serve_harness_directory(repo_root, tmp_path) as base_url:
        html_path.write_text(
            _message_copy_harness_html(base_url),
            encoding="utf-8",
        )
        page.goto(f"{base_url}/{html_path.name}")
        page.wait_for_function(
            "() => window.__messageCopyReady === true",
            timeout=_WAIT_TIMEOUT_MS,
        )


def _open_round_intent_harness(page: Page, tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    html_path = tmp_path / "round_intent_controls.html"
    with _serve_harness_directory(repo_root, tmp_path) as base_url:
        html_path.write_text(
            _round_intent_harness_html(base_url),
            encoding="utf-8",
        )
        page.goto(f"{base_url}/{html_path.name}")
        page.wait_for_function(
            "() => window.__roundIntentReady === true",
            timeout=_WAIT_TIMEOUT_MS,
        )


def _message_copy_harness_html(base_url: str) -> str:
    return f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>message copy actions harness</title>
</head>
<body>
  <main id="chat-messages">
    <article class="message" data-role="model" id="old-answer">
      <div class="msg-header"><span class="msg-role role-agent">AGENT</span></div>
      <div class="msg-content"><div class="msg-text"><p>Old answer</p></div></div>
    </article>
    <article class="message" data-role="user" id="user-message">
      <div class="msg-header"><span class="msg-role role-user">USER</span></div>
      <div class="msg-content"><div class="msg-text"><p>User prompt</p></div></div>
    </article>
    <article class="message" data-role="model" id="latest-answer">
      <div class="msg-header"><span class="msg-role role-agent">AGENT</span></div>
      <div class="msg-content">
        <div class="msg-text">
          <p>Latest <strong>answer</strong></p>
          <div class="markdown-code-block">
            <div class="markdown-code-header">
              <span class="markdown-code-language">Bash</span>
              <button class="markdown-code-copy" type="button">Copy</button>
            </div>
            <pre><code>if ok:
    print("yes")
</code></pre>
          </div>
          <details class="thinking-block"><summary>Thinking</summary><div>secret thought</div></details>
          <details class="tool-block"><summary>Tool</summary><div>tool output</div></details>
        </div>
      </div>
    </article>
  </main>
  <script>
    window.__copiedText = [];
    Object.defineProperty(navigator, 'clipboard', {{
      configurable: true,
      value: {{
        writeText: async value => {{
          window.__copiedText.push(String(value));
        }},
      }},
    }});
  </script>
  <script type="module">
    import {{ bindCopyButton, syncLastAnswerCopyButton }} from "{base_url}/frontend/dist/js/components/messageRenderer/messageActions.js";
    window.__bindCopyButton = bindCopyButton;
    window.__runMessageCopySync = () => syncLastAnswerCopyButton(document.getElementById('chat-messages'));
    window.__syncMessageCopyTarget = target => syncLastAnswerCopyButton(target);
    window.__messageCopyReady = true;
  </script>
</body>
</html>
""".strip()


def _round_intent_harness_html(base_url: str) -> str:
    return f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>round intent controls harness</title>
  <link rel="stylesheet" href="{base_url}/frontend/dist/style.css">
  <style>
    body {{
      margin: 0;
      width: 1280px;
      height: 900px;
    }}
    .chat-container {{
      height: 720px;
    }}
    #chat-messages {{
      height: 640px;
      overflow-y: auto;
      padding: 24px 120px;
    }}
    .intent-overlap-probe {{
      height: 56px;
      margin-top: -42px;
      background: rgba(239, 68, 68, 0.18);
    }}
  </style>
</head>
<body class="light-theme">
  <div class="chat-container">
    <main id="chat-messages" class="chat-scroll"></main>
    <div id="input-container"></div>
  </div>
  <div id="round-nav-float"></div>
  <script>
    Object.defineProperty(navigator, 'clipboard', {{
      configurable: true,
      value: {{ writeText: async () => undefined }},
    }});
  </script>
  <script type="module">
    import {{
      createLiveRound,
      overlayRoundRecoveryState,
    }} from "{base_url}/frontend/dist/js/components/rounds/timeline.js";

    const waitForLayout = () => new Promise(resolve => {{
      requestAnimationFrame(() => requestAnimationFrame(resolve));
    }});

    window.__runRoundIntentScenario = async () => {{
      const longIntent = [
        '请检查这个项目里 Skill 机制是怎么实现的，并总结入口、注册流程和运行时调用链。',
        '重点看 src/relay_teams/skills 和 interfaces/server 相关路由。',
        '最后给出一个简洁但完整的实现说明。'
      ].join('\\n');
      createLiveRound('run-intent-controls', longIntent);
      await waitForLayout();

      const detail = document.querySelector('.round-detail-intent');
      const summary = detail?.querySelector('.round-detail-intent-summary');
      summary?.click();
      await waitForLayout();
      const openedBeforePatch = detail?.open === true;

      overlayRoundRecoveryState('run-intent-controls', {{
        run_phase: 'running',
        pending_tool_approval_count: 1,
      }});
      await waitForLayout();
      const openAfterPatch = detail?.open === true;

      detail?.querySelector('.round-detail-intent-collapse')?.click();
      await waitForLayout();
      const closedBeforePatch = detail?.open === false;

      overlayRoundRecoveryState('run-intent-controls', {{
        run_phase: 'running',
        pending_tool_approval_count: 0,
      }});
      await waitForLayout();
      const closedAfterPatch = detail?.open === false;

      const header = document.querySelector('.round-detail-header');
      const overlap = document.createElement('div');
      overlap.className = 'message intent-overlap-probe';
      overlap.textContent = 'streaming overlap probe';
      header?.after(overlap);
      await waitForLayout();

      const toggle = detail?.querySelector('.round-detail-intent-toggle');
      const rect = toggle?.getBoundingClientRect();
      const hit = rect
        ? document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2)
        : null;

      return {{
        overflow: detail?.dataset.overflow || '',
        openedBeforePatch,
        openAfterPatch,
        closedBeforePatch,
        closedAfterPatch,
        toggleHit: hit === toggle || toggle?.contains(hit) === true,
      }};
    }};
    window.__roundIntentReady = true;
  </script>
</body>
</html>
""".strip()


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
