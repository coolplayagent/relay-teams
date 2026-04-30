# LLM Concurrency Incident Record

Date: 2026-04-28
Issue: #588

## Summary

Two sessions overlapped and pushed several `glm-5.1` requests to BigModel at the same time:

- `session-65525b35`: normal run started at 2026-04-28 08:31:01 UTC.
- `session-45c27856`: orchestration run started at 2026-04-28 08:31:29 UTC.
- At about 2026-04-28 08:33:46 UTC, the orchestration run dispatched four `Crafter` subagents while the other session was still active.
- BigModel returned 429 responses with `code=1305` and message `the model is currently overloaded`.
- The backend process later became unresponsive from the UI perspective and was restarted at about 2026-04-28 08:45:42 UTC. The restart marked both runs as `interrupted_by_process_restart`.

## Root Cause

The orchestration layer had a per-run delegated task limit, but the LLM HTTP path had no shared provider-level concurrency budget. Multiple sessions could therefore exceed the upstream model capacity even when each individual orchestration run stayed within its local limit.

Async execution kept network waits non-blocking, but it did not provide admission control. Each incoming run still allocated a worker task, event stream state, persistence work, retry state, and logging work. When many runs are allowed to start and then wait or retry deep inside the LLM transport path, the service can remain alive but become slow or unresponsive to health and UI requests.

## Fix In This Change

The LLM HTTP client now shares a concurrency limiter per event loop and URL origin. The default limit is four concurrent requests per LLM origin and can be configured with:

```text
RELAY_TEAMS_LLM_HTTP_MAX_CONCURRENCY
```

Setting the value to `0` disables the limiter.

The limiter is held for the full response stream lifetime, so streaming responses still count against the provider budget until the stream is closed.

## Validation

Fake LLM regression coverage:

- One slow session plus one orchestration session with five delegated tasks.
- The fake LLM observed at most four concurrent chat completions.
- Both runs completed and backend health remained OK.

Real BigModel pressure test:

- 60 concurrent real BigModel runs: 60/60 completed, 140/140 health probes returned 200.
- 120 concurrent real BigModel runs: 120/120 completed, 404/404 health probes returned 200.
- BigModel still produced upstream 429 responses during the high-load run, but the backend stayed responsive and the retry path completed successfully.

## Follow-Up

This change protects the upstream model and prevents unbounded provider concurrency. It does not yet provide full server admission control. A separate run-level capacity guard should cap started and queued runs and return 429 or 503 before too many worker tasks, SSE queues, persistence writes, and retry states accumulate.
