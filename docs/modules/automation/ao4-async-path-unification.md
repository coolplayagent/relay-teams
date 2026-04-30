# AO-4 Async Path Unification Spec

## Status

Implemented for the 2026-04-30 PR scope. This spec records the design and
implementation contract for the third AO-4 migration slice.

## Context

AO-4 moves runtime and persistence paths away from async wrappers around
synchronous SQLite methods. The earlier slices moved core orchestration and the
automation management plane. This slice covers the automation delivery queue,
bound-session queue, and session terminal-view marker path.

The issue this slice addresses is not only blocking I/O. A wrapper-based async
method can inherit synchronous locking behavior, hide queue pressure, and make
route timeout semantics depend on whether work happened to be routed through a
thread bridge. The new contract is explicit: async runtime paths use native
async repository operations unless the boundary is intentionally synchronous.

## Goals

- Keep automation delivery and bound-session queue workers on native
  `aiosqlite` reads and writes.
- Preserve existing state-machine semantics for create, update, claim, list,
  cleanup, and project deletion operations.
- Keep session terminal-view marking on the session-read route-work queue while
  preventing request timeout from cancelling the persisted marker update.
- Add regression coverage that fails if migrated async methods fall back to
  `_call_sync_async`.
- Leave public API payloads and database schemas unchanged.

## Non-Goals

- Migrating every remaining repository that still has `_call_sync_async`
  methods.
- Changing automation delivery, queue, or session API response models.
- Introducing a new database transaction abstraction.
- Changing CLI or other deliberately synchronous entry points.

## Design Contract

Async repository methods in the migrated scope must call
`SharedSqliteRepository._run_async_read()` or `_run_async_write()` directly.
Read methods should use `async_fetchone()` and `async_fetchall()` so cursors are
closed consistently. Write methods should close cursors before returning and
reload records when callers expect stored database state.

Claim operations must remain atomic. Each claim updates a row only from its
eligible pending state, or from a stale in-progress state, and then reloads the
same row in the write operation. A zero-row update means another worker already
owns the item and the method returns `None`.

Bound-session queue list operations clamp worker limits to the existing safe
range. Delivery list operations preserve their existing caller-provided limit
semantics.

The terminal-view route must still use
`_call_session_read("sessions.terminal_view", ...)` so it remains governed by
`RouteWorkClass.SESSION_READ` admission and load-shedding. The route may return
`{"status": "deferred"}` when the request times out, but the marker task must
continue in the background and its result must be observed.

## Implementation Spec

### Automation Delivery Repository

`AutomationDeliveryRepository` now implements these async methods with native
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

Create and update write the same fields as the synchronous methods and reload
by `run_id`. Missing `run_id` lookups still raise `KeyError`.

### Bound Session Queue Repository

`AutomationBoundSessionQueueRepository` now implements these async methods with
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
| Wrapper-free contract | `tests/unit_tests/test_async_wrapper_coverage.py` checks migrated async methods do not call `_call_sync_async`. |
| Automation delivery repository | `tests/unit_tests/automation/test_automation_repository.py` monkeypatches `_call_sync_async` to fail and exercises CRUD, listing, claims, cleanup, project lookup, and deletion. |
| Bound-session queue repository | `tests/unit_tests/automation/test_automation_repository.py` covers create, update, get, non-terminal counts, readiness, waiting-result listing, claim, cleanup, project lookup, and deletion without sync wrappers. |
| Session terminal view | `tests/unit_tests/interfaces/server/test_sessions_router.py` covers timeout deferral, request cancellation, deferred result logging, load-shedding via the session-read helper, and success/missing-session behavior. |
| Service latest-terminal selection | `tests/unit_tests/sessions/test_session_auto_title.py` covers async terminal marker behavior through the service layer. |

## Operational Invariants

- Async runtime paths in this scope must not call sync repository methods through
  `_call_sync_async`.
- Route timeout must not cancel terminal-view persistence.
- Request cancellation must not leave a background marker failure unobserved.
- Session-read queue admission must still apply to terminal-view marker work.
- Claim methods must be race-safe under concurrent workers.
- The branch does not require schema or `/api/*` contract changes.

## Follow-Up Scope

Remaining `_call_sync_async` users are outside this PR scope and should be
migrated by repository ownership area. The next candidates are external gateway,
workspace, trigger, media, role memory, and broader session history persistence
paths.
