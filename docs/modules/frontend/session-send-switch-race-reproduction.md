# Session Send/Switch Race Data Capture

Date: 2026-05-09

This note records how to capture reliable frontend evidence for the send/switch
race. It is intentionally not the UI contract. The contract lives in unit and
browser tests: timeline ordering, target isolation, stream replay, sidebar state,
and subagent navigation must remain correct across the full runtime matrix.

## Environment

- Frontend URL: `http://127.0.0.1:8000/?real-api-devtools=1`
- Backend/model path: real local backend with the real LLM provider, not the fake
  benchmark LLM.
- Browser action source: browser input events against the real DOM. The critical
  send and target-session click are started in the same browser action batch.

## Capture Procedure

Use two existing sessions that both already contain `你好` messages.

1. Open session A, place `你好` in the composer.
2. Start the send button click.
3. Start the target session B click within the same action batch, without
   waiting for the send click to settle. For automated mock-browser tests, this
   is represented by dispatching the send click and target-session click in the
   same page task. For real API smoke, use actual browser input events.
4. Start a 50 ms sampler before the click batch. Capture active target, loading
   state, main timeline text, visible placeholders, sidebar indicators, and
   stream watermarks.

The original failing reproduction timing from the browser run:

```json
{
  "target_start_after_send_start_ms": 32,
  "clicks_done_ms": 1415
}
```

The original screenshots from the first failing reproduction were saved
under:

- `.tmp/send-switch-race-overlap-80ms.png`
- `.tmp/send-switch-race-overlap-1000ms.png`

## Required Sample Fields

Each sample JSON should include:

- `send_started_ms`
- `target_click_ms`
- `target_click_after_send_ms`
- `target_active_after_target_click_ms`
- `target_stable_after_target_click_ms`
- active session/subagent identity
- visible loading state
- visible timeline text summary
- source pending/live placeholder presence
- EventSource URL or stream watermark when available

## Expected Capture Outcome

- Session B becomes the active session.
- The main timeline renders only session B history and live stream state.
- Session A may continue running in the background, but its pending user message,
  live run placeholder, stream events, loading overlay, and running composer state
  must not render in session B.
- Session B should not show a long loading state caused by session A starting a
  run.
- The measured `target_stable_after_target_click_ms` should be below 1000 ms for
  the simple existing-session switch path.

## Historical Failure Signature

The 80 ms sample after the original overlapping click batch showed:

```text
2026/5/9 23:32:31
LIVE
运行中
你好
real-visible-a-2e5759
停止
...
```

The screenshot also showed the target session row selected while the main timeline
contained a new live `你好` block and the composer showed an in-flight insertion
state. That was the failure signature: send/run state created for the source
interaction rendered into the newly active target session during fast navigation.

## Current Smoke Result

The 2026-05-10 real-backend smoke used `http://127.0.0.1:8000/?real-api-devtools=1`
with the real configured model path and two existing non-empty `你好` sessions.
Measured result:

```json
{
  "target_click_after_send_ms": 8,
  "target_active_after_target_click_ms": 39,
  "target_stable_after_target_click_ms": 39
}
```

The screenshot was saved under:

- `.tmp/real-api-send-switch-smoke.png`

## Why Earlier Captures Missed It

The earlier smoke and mock tests separated `fill`, `send click`, and `target
session click` into different harness operations. That let the frontend settle
between operations, so the race window was mostly avoided. The failing user path
requires the send click and target click to overlap in one UI interaction window.

Future captures for this race must start the target session click before the send
click has fully settled, then record enough samples to determine whether:

- the active target identity is session B,
- no source-session pending or live run block is rendered in session B,
- no source-session loading overlay remains visible in session B,
- unread/running/completed sidebar state updates still converge within one
  second.
