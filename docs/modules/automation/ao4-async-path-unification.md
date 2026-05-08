# AO-4 Async Path Unification Spec

## Status

Implemented through the final 2026-05-01 AO-4 repository slice, with the final
2026-05-08 cleanup removing the legacy sync-wrapper helper from
`SharedSqliteRepository`. The async runtime and server persistence paths now use
native async repository operations instead of bridging through synchronous SQLite
methods.

## Context

AO-4 moves runtime and persistence paths away from async wrappers around
synchronous SQLite methods. Earlier slices migrated the orchestration hot path,
automation management plane, automation delivery queue, bound-session queue, and
session terminal-view marker flow. The final slice completes the remaining
runtime-facing repository families:

- message history and prompt persistence
- task, run, event, background task, approval, and user-question batch lookups
- external agent sessions and external session bindings
- gateway account, inbound queue, session, and message-pool stores
- workspace, SSH profile, media asset, trigger, monitor, role memory, token
  usage, and retrieval FTS stores

The issue addressed is not only blocking I/O. A wrapper-based async method can
inherit synchronous locking behavior, hide queue pressure, and make route timeout
semantics depend on whether work happened to be routed through a thread bridge.
The contract after AO-4 is explicit: async runtime paths use native async
repository operations unless the boundary is intentionally synchronous.

## Goals

- Remove the legacy sync-wrapper helper from `src/relay_teams` completely.
- Keep async repository methods on `aiosqlite`, `async_fetchone()`,
  `async_fetchall()`, `_run_async_read()`, and `_run_async_write()`.
- Preserve existing public API payloads, database schemas, state-machine
  semantics, timestamp ordering, and cache invalidation behavior.
- Preserve atomic claim behavior for automation delivery, bound-session queue,
  gateway inbound queue, Feishu message pool, approvals, questions, and runtime
  work queues.
- Add regression coverage that fails when a runtime source file reintroduces a
  sync bridge caller.

## Non-Goals

- Removing every synchronous public API method. CLI, scripts, and legacy sync
  callers may continue to call synchronous methods at deliberate boundaries.
- Changing `/api/*` contracts or database schema.
- Introducing a new database transaction abstraction.
- Replacing the session-read route-work queue or other request admission
  controls.

## Design Contract

Async repository methods must execute database work directly on async
connections. Read methods should use `async_fetchone()` and `async_fetchall()`
so cursors are closed consistently. Write methods should close cursors before
returning and reload records when callers expect stored database state.

`SharedSqliteRepository` no longer exposes a sync-wrapper helper. If a sync API
still exists for a deliberate boundary, its async equivalent must contain its own
async SQL implementation rather than wrapping the sync method.

Claim and queue operations must remain atomic. Each claim updates a row only
from its eligible pending state, or from a stale in-progress state, and then
reloads the same row inside the write operation. A zero-row update means another
worker already owns the item and the method returns `None`.

The terminal-view route still uses `_call_session_read("sessions.terminal_view",
...)` so it remains governed by `RouteWorkClass.SESSION_READ` admission and
load-shedding. The queued callable is async, and the route may return
`{"status": "deferred"}` when the request times out, but the marker task must
continue in the background and its result must be observed.

## Implementation Spec

### Automation Delivery Repository

`AutomationDeliveryRepository` implements these async methods with native
`aiosqlite` access:

- `create_async`
- `update_async`
- `get_by_run_id_async`
- `list_pending_started_async`
- `list_pending_terminal_async`
- `claim_started_async`
- `claim_terminal_async`
- `list_pending_started_cleanup_async`
- `claim_started_cleanup_async`
- `has_project_records_async`
- `delete_by_project_async`

Create and update write the same fields as the synchronous methods and reload by
`run_id`. Missing `run_id` lookups still raise `KeyError`.

### Bound Session Queue Repository

`AutomationBoundSessionQueueRepository` implements these async methods with
native `aiosqlite` access:

- `create_async`
- `update_async`
- `get_async`
- `has_non_terminal_item_for_run_async`
- `count_non_terminal_by_session_async`
- `count_non_terminal_ahead_async`
- `list_ready_to_start_async`
- `list_waiting_for_result_async`
- `claim_starting_async`
- `list_pending_queue_cleanup_async`
- `claim_queue_cleanup_async`
- `has_project_records_async`
- `delete_by_project_async`

Create and update reload by `automation_queue_id`. Count and readiness queries
reuse the same non-terminal status set as the synchronous path.

### Final Repository Slice

The final slice converts the remaining async methods that previously delegated
to sync methods:

- `MessageRepository` now performs append, projection, history replay, prompt
  persistence, pruning, compaction, system prompt hiding, and delete operations
  with async SQL and async timestamp sequencing.
- Runtime projection repositories perform batch reads natively:
  `TaskRepository`, `RunRuntimeRepository`, `BackgroundTaskRepository`,
  `RunIntentRepository`, `EventLog`, `UserQuestionRepository`, and
  `ApprovalTicketRepository`.
- External and gateway repositories use native async CRUD and queue operations:
  external sessions, external session bindings, Feishu and WeChat accounts,
  XiaoLuBan accounts, gateway sessions, WeChat inbound queue, and Feishu message
  pool.
- Operational stores use native async reads and writes: workspace records, SSH
  profiles, media assets, monitors, triggers, role memory, shell approvals, and
  token usage.
- Retrieval FTS paths use async scope configuration, document indexing,
  deletion, rebuild, search, and stats operations while preserving existing FTS5
  schema and ranking behavior.

### Session Terminal View

`SessionService.mark_latest_terminal_run_viewed_async()` performs the same
logical workflow as the synchronous service path:

1. Load the session and fail with `KeyError` when the session is unknown.
2. Load run runtimes and optional background task records asynchronously.
3. Resolve the latest user-visible terminal run from preloaded records.
4. Persist `last_viewed_terminal_run_id` with
   `SessionRepository.mark_terminal_run_viewed_async()`.
5. Invalidate list-session cache after a successful marker write.

`mark_session_terminal_viewed()` creates a task for the session-read queued async
operation, waits on `asyncio.shield()` with the existing timeout, and observes
deferred task completion on timeout or request cancellation. Deferred logging
covers successful completion, missing sessions, explicit task cancellation, and
unexpected failures.

## Validation Matrix

| Area | Coverage |
| --- | --- |
| Repository-wide bridge guard | `tests/unit_tests/test_async_wrapper_coverage.py` scans `src/relay_teams` and fails if any source file references the removed sync-wrapper helper. |
| Wrapper-free method guard | `tests/unit_tests/test_async_wrapper_coverage.py` checks migrated async methods do not call the removed sync-wrapper helper. |
| Automation delivery repository | `tests/unit_tests/automation/test_automation_repository.py` exercises CRUD, listing, claims, cleanup, project lookup, and deletion through async repository methods. |
| Bound-session queue repository | `tests/unit_tests/automation/test_automation_repository.py` covers create, update, get, non-terminal counts, readiness, waiting-result listing, claim, cleanup, project lookup, and deletion without sync wrappers. |
| Session terminal view | `tests/unit_tests/interfaces/server/test_sessions_router.py` covers timeout deferral, request cancellation, deferred result logging, load-shedding via the session-read helper, and success/missing-session behavior. |
| Service latest-terminal selection | `tests/unit_tests/sessions/test_session_auto_title.py` covers async terminal marker behavior through the service layer. |
| Type contract | `uv run --extra dev basedpyright` validates async helper and row conversion signatures. |

## Operational Invariants

- Runtime async paths must not call sync repository methods through wrapper
  helpers.
- Route timeout must not cancel terminal-view persistence.
- Request cancellation must not leave a background marker failure unobserved.
- Session-read queue admission must still apply to terminal-view marker work.
- Claim methods must be race-safe under concurrent workers.
- Retrieval FTS updates must preserve existing scope isolation and ranking
  behavior.
- The branch does not require schema or `/api/*` contract changes.

## Follow-Up Scope

AO-4's sync-bridge migration is complete for runtime source paths. Future work
should focus on reducing redundant synchronous public APIs where they no longer
serve a CLI or script boundary, and on replacing remaining management-plane
`call_maybe_async` compatibility seams with direct async service calls.
