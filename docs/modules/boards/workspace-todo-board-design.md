# Workspace TODO Board Design

## Purpose

Workspace TODO Board is the Agent Teams-owned kanban for workspace work items. It
uses external systems only as sources of truth for evidence, not for board column
state. In v1, GitHub issues are imported as TODO items. Pull requests are linked
to review items and are used as evidence for completion.

The feature is owned by the `boards` domain. It is intentionally separate from
the connector overview: connectors report availability and health, while boards
own TODO persistence, session binding, status transitions, and archive semantics.

## User Experience

The frontend exposes a dedicated `Boards` feature page alongside Skills,
Automation, and Connectors. The main board shows four workflow columns:

- `todo`
- `in_progress`
- `review`
- `done`

`archived` is a separate view. Archived items can be restored to `todo`; restore
does not recover the pre-archive status because that would bypass the
session/PR-driven state machine.

Each card shows source, title, repository, session, linked PR, source update
time, and actions. GitHub issue cards use GitHub issue `updated_at`; manual TODOs
fall back to the local board row update time. Details open in a modal with the
full body, issue URL, linked PR URL, session/run ids, source update time, board
update time, and status reason. The card also has a direct source-link action for
the GitHub issue. Source links are icon-only external links. The details modal
paginates long bodies by paragraph so large issue descriptions remain readable
without losing the metadata context.

Each column has its own client-side search and sort controls. Search matches the
title, body, repository, issue/PR numbers, and session id. Sort modes are newest,
oldest, title A-Z, and title Z-A. Time sort uses `source_updated_at || updated_at`
so GitHub issue ordering follows issue activity rather than the time Agent Teams
persisted the row. These controls are view state only; they do not change board
persistence or revision.

Small screens keep the kanban interaction instead of collapsing the workflow
into a single list. The board uses horizontal snap columns, compact spacing, a
stacked toolbar, fixed column headers, independently scrolling card lists, and a
near-fullscreen details modal.

## State Machine

The board status is owned by Agent Teams:

- `todo`: new manual item or imported GitHub issue.
- `in_progress`: a user starts processing; the backend creates a dedicated
  session/run and stores `session_id/run_id`.
- `review`: the bound run completes.
- `done`: the item has a linked PR and the PR is merged.
- `archived`: soft delete; hidden from the main board.

GitHub PRs are not TODO cards. During sync/reconcile, review GitHub issue items
may query issue timeline events to discover linked PRs. A merged linked PR moves
the issue TODO to `done`.

If a bound session is deleted, active board items cannot remain in
`in_progress` or `review` with a dead session reference. Non-archived,
non-`done` items bound to the deleted session return to `todo`, clear
`session_id/run_id`, and record `Bound session deleted`. `done` items keep their
status but clear the stale session reference.

## API

Full board APIs remain available for cold start and compatibility:

- `GET /api/boards/todos`
- `POST /api/boards/todos:sync`

Incremental APIs support cached frontend views:

- `GET /api/boards/todos:changes`
- `POST /api/boards/todos:sync-changes`

Delta requests include `workspace_id`, `include_archived`, and
`after_revision`. Sync delta also accepts `force_full`. Delta responses include
changed items, removed active-view ids, status counts, diagnostics, synced time,
and the latest workspace revision.

Mutation APIs return the changed item so the frontend can update local cache
without reloading the whole board:

- `POST /api/boards/todos`
- `POST /api/boards/todos/{todo_id}:start`
- `POST /api/boards/todos/{todo_id}:request-changes`
- `POST /api/boards/todos/{todo_id}:archive`
- `POST /api/boards/todos/{todo_id}:restore`
- `POST /api/boards/todos/{todo_id}:link-pr`

## Persistence

`board_todo_items` stores the item state and source references. Each row has an
`item_revision`. `updated_at` is the Agent Teams row update time, while
`source_updated_at` is the external source update time used for board sorting.
`board_todo_workspace_state` stores the current workspace revision and the
GitHub issue sync cursor.

Every write that changes an item increments the workspace revision and writes
that value into the item row. Delta queries return rows with
`item_revision > after_revision`. In active view, archived rows are returned as
`removed_todo_ids` so the frontend can remove them from the main board.

The GitHub issue cursor is per workspace/repository. A repository change resets
incremental behavior by ignoring the old cursor.

## Sync Behavior

Sync resolves the workspace Git remote to `owner/repo`, then obtains a GitHub
token from enabled trigger accounts or the shared GitHub token.

Full sync fetches all open issues and treats that open issue number set as the
authoritative active TODO set. Incremental sync fetches all issues changed since
the stored cursor so recent close/reopen events can be reconciled. Pull request
refresh uses the same cursor during incremental sync and only full sync scans
all PRs. After a successful sync, the cursor is advanced to the sync time.

GitHub `/issues` responses that contain a `pull_request` object are ignored as
TODO sources. Closed issues are not imported as new TODO items. If an existing
active issue item is later observed as closed, sync moves it to `done` only when
its linked PR is merged; otherwise it is soft-archived with status reason
`GitHub issue closed`. Pull requests are still listed so linked PR merge state
can be reconciled for review items.

The frontend uses a versioned localStorage cache. A cache-version bump forces a
new cold start. The first board load for each workspace in a browser session
shows cache immediately and may perform a sync in the background only when the
workspace has not auto-synced in the last hour. Manual `Sync GitHub` is always a
deliberate full reconciliation so stale closed issues are removed from the active
board. If a full reconciliation does not see a previously active GitHub issue in
the open issue set, that item is archived as
`GitHub issue no longer open`.

If GitHub later reports the same issue as open again, sync restores only items
that were archived by GitHub closed/non-open reconciliation back to `todo`.
Items manually archived by a user remain archived until the user explicitly
restores them.

## Frontend Cache And Progressive Loading

The frontend cache is keyed by `workspace_id + include_archived`. Each cached
board stores the latest revision. On mount or workspace switch:

- Cached data renders immediately.
- A delta request refreshes in the background.
- Without cache, the page shows column skeletons.
- When full data arrives, cards are rendered in batches to create visible
  progress instead of a single long blocking update.

Column headers, counts, search, and sort controls do not scroll with the card
list. Only the cards inside each column have vertical scrolling.

Mutation results are merged into active and archived caches immediately. A
background delta refresh follows to reconcile any lifecycle updates. Archived
items are never reactivated by sync unless they were auto-archived because a
GitHub issue was closed or no longer open and GitHub later reports that issue as
open again.

Progressive loading animates only cards that first enter the DOM. Cached boards
render without entrance animation, and non-data interactions such as opening
details or source links do not re-render the columns. This avoids the impression
that the whole board is refreshing for every click.

## Failure Modes

If workspace Git remote resolution fails, the board still loads local/manual
items and returns diagnostics.

If GitHub token resolution or sync fails, cached/local data remains usable and
diagnostics are displayed. GitHub sync diagnostics must always include a
non-empty message; API failures include the status code when available. Archived
items are never reactivated by sync.

If a delta is requested without a usable cache, the frontend falls back to the
full board endpoint.

## Test Strategy

Backend unit tests cover workspace independence, revision/delta behavior,
archive/remove semantics, restore, incremental GitHub cursor use, issue-only
sync, closed issue filtering, review PR linking, merged PR completion, session
delete recovery, and historical PR TODO archival.

Frontend checks cover cache-first rendering, delta merge, progressive loading,
workspace/view switch cancellation, per-column search/sort, detail pagination,
restore from archive, and responsive layout.
