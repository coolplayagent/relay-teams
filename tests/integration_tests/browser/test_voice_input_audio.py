from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re
import threading

from playwright.sync_api import Page
from playwright.sync_api import sync_playwright


_VIEWPORT_WIDTH = 1280
_VIEWPORT_HEIGHT = 900
_WAIT_TIMEOUT_MS = 10_000
_FRONTEND_TEST_PORT_START = 49152
_FRONTEND_TEST_PORT_ATTEMPTS = 80


class _QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


def test_voice_input_sends_pcm_bytes_and_stops_after_silence() -> None:
    with _serve_frontend_dist() as base_url:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": _VIEWPORT_WIDTH, "height": _VIEWPORT_HEIGHT},
                permissions=["microphone"],
            )
            context.add_init_script(_voice_audio_probe_script())
            page = context.new_page()
            _route_speech_config(page)

            page.goto(base_url, wait_until="load")
            page.wait_for_selector("#voice-input-btn", state="attached")
            page.wait_for_function(
                "() => document.querySelector('#voice-input-btn')?.disabled === false",
                timeout=_WAIT_TIMEOUT_MS,
            )

            page.locator("#voice-input-btn").click()

            page.wait_for_function(
                "() => document.querySelector('#voice-input-btn')?.dataset.voiceState === 'listening'",
                timeout=_WAIT_TIMEOUT_MS,
            )
            page.wait_for_function(
                "() => window.__voiceProbe.sent.some(item => String(item).startsWith('bytes:'))",
                timeout=_WAIT_TIMEOUT_MS,
            )
            page.wait_for_function(
                "() => document.querySelector('#voice-input-btn')?.dataset.voiceState === 'idle'",
                timeout=_WAIT_TIMEOUT_MS,
            )

            result = page.evaluate(
                "() => ({"
                "state: document.querySelector('#voice-input-btn')?.dataset.voiceState,"
                "active: globalThis.__relayTeamsVoiceInputActive,"
                "sent: window.__voiceProbe.sent"
                "})"
            )

            context.close()
            browser.close()

    assert result["state"] == "idle"
    assert result["active"] is False
    assert '{"type":"start"}' in result["sent"]
    assert '{"type":"stop"}' in result["sent"]
    assert any(str(item).startswith("bytes:") for item in result["sent"])


def test_voice_space_hold_focuses_prompt_and_suppresses_silence_stop() -> None:
    with _serve_frontend_dist() as base_url:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": _VIEWPORT_WIDTH, "height": _VIEWPORT_HEIGHT},
                permissions=["microphone"],
            )
            context.add_init_script(_voice_audio_probe_script())
            page = context.new_page()
            _route_speech_config(page)

            page.goto(base_url, wait_until="load")
            page.wait_for_selector("#voice-input-btn", state="attached")
            page.wait_for_function(
                "() => document.querySelector('#voice-input-btn')?.disabled === false",
                timeout=_WAIT_TIMEOUT_MS,
            )

            page.mouse.click(500, 260)
            page.keyboard.down("Space")
            page.wait_for_function(
                "() => document.querySelector('#voice-input-btn')?.dataset.voiceState === 'listening'",
                timeout=_WAIT_TIMEOUT_MS,
            )
            page.wait_for_timeout(2500)
            held = page.evaluate(
                "() => ({"
                "state: document.querySelector('#voice-input-btn')?.dataset.voiceState,"
                "active: globalThis.__relayTeamsVoiceInputActive,"
                "focused: document.activeElement === document.querySelector('#prompt-input'),"
                "sent: window.__voiceProbe.sent"
                "})"
            )

            page.keyboard.up("Space")
            page.wait_for_function(
                "() => document.querySelector('#voice-input-btn')?.dataset.voiceState === 'idle'",
                timeout=_WAIT_TIMEOUT_MS,
            )
            released = page.evaluate(
                "() => ({"
                "state: document.querySelector('#voice-input-btn')?.dataset.voiceState,"
                "active: globalThis.__relayTeamsVoiceInputActive,"
                "sent: window.__voiceProbe.sent"
                "})"
            )

            context.close()
            browser.close()

    assert held["state"] == "listening"
    assert held["active"] is True
    assert held["focused"] is True
    assert '{"type":"stop"}' not in held["sent"]
    assert released["state"] == "idle"
    assert released["active"] is False
    assert '{"type":"stop"}' in released["sent"]


def test_voice_input_stops_when_websocket_backpressure_persists() -> None:
    with _serve_frontend_dist() as base_url:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": _VIEWPORT_WIDTH, "height": _VIEWPORT_HEIGHT},
                permissions=["microphone"],
            )
            context.add_init_script(_voice_audio_probe_script(backpressured=True))
            page = context.new_page()
            _route_speech_config(page)

            page.goto(base_url, wait_until="load")
            page.wait_for_selector("#voice-input-btn", state="attached")
            page.wait_for_function(
                "() => document.querySelector('#voice-input-btn')?.disabled === false",
                timeout=_WAIT_TIMEOUT_MS,
            )

            page.locator("#voice-input-btn").click()

            page.wait_for_function(
                "() => document.querySelector('#voice-input-btn')?.dataset.voiceState === 'listening'",
                timeout=_WAIT_TIMEOUT_MS,
            )
            page.wait_for_function(
                "() => document.querySelector('#voice-input-btn')?.dataset.voiceState === 'idle'",
                timeout=_WAIT_TIMEOUT_MS,
            )
            result = page.evaluate(
                "() => ({"
                "state: document.querySelector('#voice-input-btn')?.dataset.voiceState,"
                "active: globalThis.__relayTeamsVoiceInputActive,"
                "sent: window.__voiceProbe.sent"
                "})"
            )

            context.close()
            browser.close()

    assert result["state"] == "idle"
    assert result["active"] is False
    assert result["sent"][0] == '{"type":"start"}'
    assert result["sent"][-1] == '{"type":"stop"}'
    assert any(str(item).startswith("bytes:") for item in result["sent"])


def test_voice_input_closes_websocket_when_finalize_times_out() -> None:
    with _serve_frontend_dist() as base_url:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": _VIEWPORT_WIDTH, "height": _VIEWPORT_HEIGHT},
                permissions=["microphone"],
            )
            context.add_init_script(_voice_audio_probe_script(close_on_stop=False))
            page = context.new_page()
            _route_speech_config(page)

            page.goto(base_url, wait_until="load")
            page.wait_for_selector("#voice-input-btn", state="attached")
            page.wait_for_function(
                "() => document.querySelector('#voice-input-btn')?.disabled === false",
                timeout=_WAIT_TIMEOUT_MS,
            )

            page.locator("#voice-input-btn").click()

            page.wait_for_function(
                "() => document.querySelector('#voice-input-btn')?.dataset.voiceState === 'listening'",
                timeout=_WAIT_TIMEOUT_MS,
            )
            page.wait_for_function(
                "() => document.querySelector('#voice-input-btn')?.dataset.voiceState === 'idle'",
                timeout=12_000,
            )
            result = page.evaluate(
                "() => ({"
                "state: document.querySelector('#voice-input-btn')?.dataset.voiceState,"
                "active: globalThis.__relayTeamsVoiceInputActive,"
                "sent: window.__voiceProbe.sent,"
                "closed: window.__voiceProbe.closed"
                "})"
            )

            context.close()
            browser.close()

    assert result["state"] == "idle"
    assert result["active"] is False
    assert '{"type":"start"}' in result["sent"]
    assert '{"type":"stop"}' in result["sent"]
    assert result["closed"] == 1


def test_voice_input_drops_pre_ready_audio_after_sample_rate_negotiation() -> None:
    with _serve_frontend_dist() as base_url:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": _VIEWPORT_WIDTH, "height": _VIEWPORT_HEIGHT},
                permissions=["microphone"],
            )
            context.add_init_script(
                _voice_audio_probe_script(sample_rate=24000, ready_delay_ms=360)
            )
            page = context.new_page()
            _route_speech_config(page)

            page.goto(base_url, wait_until="load")
            page.wait_for_selector("#voice-input-btn", state="attached")
            page.wait_for_function(
                "() => document.querySelector('#voice-input-btn')?.disabled === false",
                timeout=_WAIT_TIMEOUT_MS,
            )

            page.locator("#voice-input-btn").click()

            page.wait_for_function(
                "() => window.__voiceProbe.sent.some(item => String(item).startsWith('bytes:'))",
                timeout=_WAIT_TIMEOUT_MS,
            )
            result = page.evaluate(
                "() => window.__voiceProbe.sent"
                ".filter(item => String(item).startsWith('bytes:'))"
                ".map(item => Number(String(item).slice(6)))"
            )

            context.close()
            browser.close()

    assert result
    assert all(byte_count % 4096 == 0 for byte_count in result)


def test_voice_button_hides_without_stt_config_and_space_remains_text_input() -> None:
    with _serve_frontend_dist() as base_url:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            _route_speech_config(page, configured=False)

            page.goto(base_url, wait_until="load")
            page.wait_for_selector("#voice-input-btn", state="attached")
            page.wait_for_function(
                "() => {"
                "const button = document.querySelector('#voice-input-btn');"
                "return button?.hidden === true && button?.disabled === true;"
                "}",
                timeout=_WAIT_TIMEOUT_MS,
            )
            page.locator("#prompt-input").fill("hello")
            page.keyboard.press("Space")
            result = page.evaluate(
                "() => ({"
                "hidden: document.querySelector('#voice-input-btn')?.hidden,"
                "disabled: document.querySelector('#voice-input-btn')?.disabled,"
                "value: document.querySelector('#prompt-input')?.value,"
                "active: globalThis.__relayTeamsVoiceInputActive"
                "})"
            )

            browser.close()

    assert result["hidden"] is True
    assert result["disabled"] is True
    assert result["value"] == "hello "
    assert result["active"] is False


def test_voice_input_worklet_emits_chunked_audio_without_level_message_storm() -> None:
    with _serve_frontend_dist() as base_url:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(base_url, wait_until="load")
            result = page.evaluate(
                """async () => {
                    if (!window.OfflineAudioContext || !window.AudioWorkletNode) {
                        return { supported: false };
                    }
                    const renderWorklet = async (configuredSampleRate = null) => {
                        const context = new OfflineAudioContext(1, 48000, 48000);
                        await context.audioWorklet.addModule('/js/components/voiceInputWorklet.js');
                        const node = new AudioWorkletNode(
                            context,
                            'relay-teams-voice-input',
                            { processorOptions: { targetSampleRate: 16000 } },
                        );
                        if (configuredSampleRate) {
                            node.port.postMessage({
                                type: 'configure',
                                targetSampleRate: configuredSampleRate,
                            });
                        }
                        const oscillator = new OscillatorNode(context, { frequency: 440 });
                        const gain = new GainNode(context, { gain: 0.2 });
                        const messages = [];
                        node.port.onmessage = event => {
                            messages.push({
                                type: event.data?.type || '',
                                bytes: event.data?.audio?.byteLength || 0,
                                level: event.data?.level || 0,
                            });
                        };
                        oscillator.connect(gain).connect(node).connect(context.destination);
                        oscillator.start(0);
                        oscillator.stop(1);
                        await context.startRendering();
                        return messages;
                    };
                    const messages = await renderWorklet(24000);
                    return {
                        supported: true,
                        total: messages.length,
                        audio: messages.filter(item => item.type === 'audio').length,
                        levelOnly: messages.filter(item => item.type === 'level').length,
                        hasBytes: messages.some(item => item.bytes > 0),
                        totalBytes: messages.reduce((sum, item) => sum + item.bytes, 0),
                        maxLevel: Math.max(...messages.map(item => item.level)),
                    };
                }"""
            )
            browser.close()

    assert result["supported"] is True
    assert result["audio"] > 0
    assert result["levelOnly"] == 0
    assert result["hasBytes"] is True
    assert result["totalBytes"] > 0
    assert result["maxLevel"] > 0
    assert result["total"] <= 14


def test_composer_action_buttons_do_not_overlap_in_runtime_states() -> None:
    with _serve_frontend_dist() as base_url:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(
                viewport={"width": _VIEWPORT_WIDTH, "height": _VIEWPORT_HEIGHT}
            )
            _route_speech_config(page)

            page.goto(base_url, wait_until="load")
            page.wait_for_selector(".composer-actions", state="attached")

            result = page.evaluate(_composer_action_layout_probe_script(False))

            browser.close()

    _assert_composer_action_layout(result)


def test_new_session_composer_action_buttons_do_not_overlap() -> None:
    with _serve_frontend_dist() as base_url:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            wide_page = browser.new_page(
                viewport={"width": _VIEWPORT_WIDTH, "height": _VIEWPORT_HEIGHT}
            )
            _route_speech_config(wide_page)
            wide_page.goto(base_url, wait_until="load")
            wide_page.wait_for_selector(".composer-actions", state="attached")
            wide_result = wide_page.evaluate(_composer_action_layout_probe_script(True))

            narrow_page = browser.new_page(viewport={"width": 520, "height": 780})
            _route_speech_config(narrow_page)
            narrow_page.goto(base_url, wait_until="load")
            narrow_page.wait_for_selector(".composer-actions", state="attached")
            narrow_result = narrow_page.evaluate(
                _composer_action_layout_probe_script(True)
            )

            browser.close()

    _assert_composer_action_layout(wide_result)
    _assert_composer_action_layout(narrow_result)


@contextmanager
def _serve_frontend_dist() -> Iterator[str]:
    repo_root = Path(__file__).resolve().parents[3]
    frontend_dist = repo_root / "frontend" / "dist"
    handler = functools.partial(
        _QuietStaticHandler,
        directory=os.fspath(frontend_dist),
    )
    server = _create_frontend_server(handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()


def _create_frontend_server(
    handler: Callable[..., SimpleHTTPRequestHandler],
) -> ThreadingHTTPServer:
    for offset in range(_FRONTEND_TEST_PORT_ATTEMPTS):
        port = _FRONTEND_TEST_PORT_START + offset
        try:
            return ThreadingHTTPServer(("127.0.0.1", port), handler)
        except OSError:
            continue
    raise RuntimeError("Could not allocate a frontend test port.")


def _route_speech_config(page: Page, *, configured: bool = True) -> None:
    body = (
        '{"stt_profile_name":"test-stt","language":"zh-CN",'
        '"prompt":null,"configured":true}'
    )
    if not configured:
        body = (
            '{"stt_profile_name":null,"language":"zh-CN",'
            '"prompt":null,"configured":false}'
        )
    page.route(
        re.compile(r".*/api/speech/config$"),
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=body,
        ),
    )


def _voice_audio_probe_script(
    *,
    backpressured: bool = False,
    close_on_stop: bool = True,
    sample_rate: int = 16000,
    ready_delay_ms: int = 20,
) -> str:
    buffered_amount = "3000000" if backpressured else "0"
    close_on_stop_value = "true" if close_on_stop else "false"
    sample_rate_value = str(sample_rate)
    ready_delay_value = str(ready_delay_ms)
    return (
        r"""
(() => {
  window.AudioWorkletNode = undefined;
  window.__voiceProbe = { sent: [], closed: 0 };
  navigator.mediaDevices = navigator.mediaDevices || {};
  navigator.mediaDevices.getUserMedia = async () => ({
    getTracks: () => [{ stop() {}, readyState: "live", kind: "audio" }],
    getAudioTracks: () => [{ stop() {}, readyState: "live", kind: "audio" }],
  });

  class FakeAudioContext {
    constructor() {
      this.sampleRate = 48000;
      this.state = "running";
      this.destination = {};
      this.audioWorklet = null;
    }

    addEventListener() {}
    createMediaStreamSource() {
      return {
        connect(target) {
          target.__sourceConnected = true;
          return target;
        },
        disconnect() {},
      };
    }
    createScriptProcessor() {
      const processor = {
        onaudioprocess: null,
        __interval: null,
        __frameIndex: 0,
        connect() {
          if (this.__interval) return;
          this.__interval = window.setInterval(() => {
            const frame = this.__frameIndex;
            this.__frameIndex += 1;
            const samples = new Float32Array(4096);
            const amplitude = frame < 10 ? 0.18 : 0;
            for (let index = 0; index < samples.length; index += 1) {
              samples[index] = amplitude * Math.sin(index / 8);
            }
            this.onaudioprocess?.({
              inputBuffer: {
                getChannelData() {
                  return samples;
                },
              },
            });
          }, 80);
        },
        disconnect() {
          if (this.__interval) {
            window.clearInterval(this.__interval);
            this.__interval = null;
          }
        },
      };
      return processor;
    }
    resume() {
      this.state = "running";
      return Promise.resolve();
    }
    close() {
      this.state = "closed";
      return Promise.resolve();
    }
  }

  window.AudioContext = FakeAudioContext;
  window.webkitAudioContext = FakeAudioContext;

  class FakeSocket extends EventTarget {
    constructor() {
      super();
      this.readyState = 0;
      this.bufferedAmount = """
        + buffered_amount
        + r""";
      window.setTimeout(() => {
        this.readyState = 1;
        this._emit("open", new Event("open"));
        window.setTimeout(() => {
          this._message({ type: "status", status: "ready", sample_rate: """
        + sample_rate_value
        + r""" });
        }, """
        + ready_delay_value
        + r""");
      }, 20);
    }

    send(data) {
      const value = typeof data === "string"
        ? data
        : `bytes:${data.byteLength || data.size || 0}`;
      window.__voiceProbe.sent.push(value);
      if (typeof data === "string" && data.includes("stop") && """
        + close_on_stop_value
        + r""") {
        window.setTimeout(() => this.close(), 20);
      }
    }
    close() {
      if (this.readyState === 3) return;
      this.readyState = 3;
      window.__voiceProbe.closed += 1;
      this._emit("close", new CloseEvent("close"));
    }
    _message(payload) {
      if (this.readyState !== 1) return;
      this._emit("message", new MessageEvent("message", {
        data: JSON.stringify(payload),
      }));
    }
    _emit(type, event) {
      this.dispatchEvent(event);
      const handler = this[`on${type}`];
      if (typeof handler === "function") {
        handler.call(this, event);
      }
    }
  }

  window.WebSocket = function WebSocket(url) {
    if (String(url).includes("/api/speech/stt/stream")) {
      return new FakeSocket();
    }
    throw new Error(`Unexpected WebSocket URL: ${url}`);
  };
  window.WebSocket.CONNECTING = 0;
  window.WebSocket.OPEN = 1;
  window.WebSocket.CLOSING = 2;
  window.WebSocket.CLOSED = 3;
})();
"""
    )


def _composer_action_layout_probe_script(new_session: bool) -> str:
    new_session_value = "true" if new_session else "false"
    return (
        """
() => {
  const container = document.querySelector("#input-container");
  const wrapper = document.querySelector(".input-wrapper");
  const prompt = document.querySelector("#prompt-input");
  const actions = document.querySelector(".composer-actions");
  const controls = {
    resume: document.querySelector("#resume-run-btn"),
    stop: document.querySelector("#stop-btn"),
    voice: document.querySelector("#voice-input-btn"),
    send: document.querySelector("#send-btn"),
  };
  container.classList.toggle("is-new-session-draft-composer", """
        + new_session_value
        + """);
  prompt.value = "voice layout regression probe";
  prompt.dispatchEvent(new Event("input", { bubbles: true }));

  const scenarios = [
    { name: "send", visible: ["send"] },
    { name: "send-voice", visible: ["send", "voice"] },
    { name: "send-voice-stop", visible: ["send", "voice", "stop"] },
    { name: "send-voice-resume", visible: ["send", "voice", "resume"] },
    { name: "send-voice-stop-resume", visible: ["send", "voice", "stop", "resume"] },
  ];

  const setVisible = (element, visible) => {
    element.hidden = false;
    element.disabled = false;
    element.style.visibility = "visible";
    element.style.display = visible ? "inline-flex" : "none";
  };
  const rectFor = (name, element) => {
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    const visible = style.display !== "none"
      && style.visibility !== "hidden"
      && rect.width > 0
      && rect.height > 0;
    return {
      name,
      visible,
      left: rect.left,
      right: rect.right,
      top: rect.top,
      bottom: rect.bottom,
      width: rect.width,
      height: rect.height,
    };
  };
  const overlaps = (first, second) => {
    if (!first.visible || !second.visible) return false;
    return first.left < second.right - 0.5
      && first.right > second.left + 0.5
      && first.top < second.bottom - 0.5
      && first.bottom > second.top + 0.5;
  };

  return scenarios.map((scenario) => {
    Object.entries(controls).forEach(([name, element]) => {
      setVisible(element, scenario.visible.includes(name));
    });
    const actionRect = actions.getBoundingClientRect();
    const wrapperRect = wrapper.getBoundingClientRect();
    const promptStyle = getComputedStyle(prompt);
    const rects = Object.entries(controls).map(([name, element]) => rectFor(name, element));
    const visibleRects = rects.filter((rect) => rect.visible);
    const collisions = [];
    for (let firstIndex = 0; firstIndex < visibleRects.length; firstIndex += 1) {
      for (let secondIndex = firstIndex + 1; secondIndex < visibleRects.length; secondIndex += 1) {
        if (overlaps(visibleRects[firstIndex], visibleRects[secondIndex])) {
          collisions.push(`${visibleRects[firstIndex].name}/${visibleRects[secondIndex].name}`);
        }
      }
    }
    return {
      name: scenario.name,
      rects,
      collisions,
      railWidth: actionRect.width,
      wrapperWidth: wrapperRect.width,
      promptPaddingRight: Number.parseFloat(promptStyle.paddingRight),
      railFitsPromptPadding: Number.parseFloat(promptStyle.paddingRight) >= actionRect.width + 14,
      railInsideWrapper: actionRect.left >= wrapperRect.left
        && actionRect.right <= wrapperRect.right
        && actionRect.top >= wrapperRect.top
        && actionRect.bottom <= wrapperRect.bottom,
    };
  });
}
"""
    )


def _assert_composer_action_layout(result: list[dict[str, object]]) -> None:
    assert result
    for scenario in result:
        assert scenario["collisions"] == [], scenario
        assert scenario["railFitsPromptPadding"] is True, scenario
        assert scenario["railInsideWrapper"] is True, scenario
