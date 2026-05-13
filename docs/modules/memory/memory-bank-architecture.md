# Memory Bank Architecture

> **Feature ID**: FE-1
> **Name**: Cross-Run Memory Bank
> **Status**: Implemented
> **Updated**: 2026-05-10
> **Strictness**: high

## Overview

Memory Bank is the durable memory architecture for Relay Teams. It captures
task results, manual notes, and condensed session context as typed, tagged,
versioned entries in `memory_entries`, then makes those entries available
through search, prompt injection, and governed capability evolution.

The system is organized as three memory tiers:

```text
capture sources
-> working memory
-> medium-term memory
-> persistent memory
-> search, prompt injection, and reviewed skill drafts
```

This tiered shape lets short-lived execution observations decay or expire
quickly while useful session, role, and workspace knowledge can be consolidated
into longer-lived project memory.

The runtime no longer reads or writes legacy role-memory services. During
`MemoryBankRepository` initialization, supported legacy `role_memories` rows are
migrated into `memory_entries` and the old table is dropped. `role_daily_memories`
is also dropped if present.

## Architecture Map

Memory Bank uses tier, scope, status, source, confidence, and expiry fields to
keep memory lifecycle explicit.

| Tier | Scope | Default TTL | Purpose |
| --- | --- | --- | --- |
| `working` | run/task | 4 hours | Immediate observations from active execution. |
| `medium_term` | session/role | 7 days | Useful context that should survive a single run. |
| `persistent` | workspace | none | Long-lived facts, decisions, preferences, and role-performance insights. |

Entries move through the architecture by consolidation. Runtime task completion
creates working memory, consolidation can promote useful information into
medium-term or persistent memory, retrieval selects active entries for
Memory page search and `## Project Memory` prompt injection, and selected
entries can be promoted into reviewable skill or SOP-skill drafts.

## Goals

- Persist structured memory across runs, sessions, roles, and workspaces.
- Support Working, Medium-term, and Persistent memory tiers.
- Provide query, search, create, update, delete, and consolidation APIs.
- Inject relevant Memory Bank entries into role prompts as `## Project Memory`.
- Promote selected memory into draft skills or SOP skills before applying them
  to the runtime skill registry.
- Surface a global Memory page in the main frontend feature navigation.
- Keep session compaction separate from durable memory.

## Non-Goals

- Cross-workspace memory sharing.
- Vector embedding storage.
- Protocol-specific memory behavior in ACP, A2A, or CLI adapters.
- Reintroducing manual role-summary refresh/update/delete flows.

## Data Model

Primary table: `memory_entries`.

Tiers:

- `working`: short-lived run/task observations.
- `medium_term`: session/role knowledge that can survive a run.
- `persistent`: workspace knowledge that survives across sessions.

Scopes:

- `workspace`
- `session`
- `role`

Kinds:

- `insight`
- `constraint`
- `decision`
- `failure_mode`
- `preference`
- `fact`
- `summary`

Statuses:

- `active`
- `superseded`
- `expired`

Sources:

- `consolidation`
- `manual`
- `condensation`
- `task_result`

## Runtime Flow

Task completion writes a Working-tier `task_result` entry through
`MemoryEventHandler`.

Prompt assembly queries Memory Bank through `MemoryBankService`:

```text
resolve role
-> query Memory Bank for relevant role/workspace entries
-> inject ## Project Memory
-> load and compact session history
-> dispatch to runtime adapter
```

Memory Bank is used before local, ACP, A2A, and CLI runtime dispatch. Adapters
consume the already-prepared prompt and do not implement their own memory reads.

Capability evolution is explicit and review-first:

```text
select active Memory Bank entries
-> create memory_evolution_drafts row
-> inspect generated skill/SOP draft
-> apply through ClawHubSkillService.save_skill(...)
-> reload runtime skill registry
```

No background path silently writes skills. Draft application is a user or API
mutation, and source memory metadata records the applied draft and skill ref.
Draft creation derives `workspace_id` from the API path and validates the target
skill identifiers before persisting the draft, so invalid drafts do not fail
later during skill application. Draft apply and reject mutations atomically
claim a draft before persisting the transition or writing the skill, preventing
concurrent requests from creating multiple skill outputs or reporting
conflicting final states for one draft. Apply releases the claim on skill-write
failure or cancellation, retries final applied-state persistence, and treats
source-memory metadata tagging as a best-effort follow-up. Tagging patches only
metadata keys, so concurrent content, tag, status, and scoring edits are not
overwritten by draft application. Applied timestamps are recorded after the
skill write completes, and source-memory tag patches use their own current
patch time for recency ordering.

## API Surface

Global read/search:

- `GET /api/memories`
- `POST /api/memories/search`

Workspace-scoped operations:

- `GET /api/workspaces/{workspace_id}/memories`
- `POST /api/workspaces/{workspace_id}/memories`
- `GET /api/workspaces/{workspace_id}/memories/{memory_id}`
- `PUT /api/workspaces/{workspace_id}/memories/{memory_id}`
- `DELETE /api/workspaces/{workspace_id}/memories/{memory_id}`
- `POST /api/workspaces/{workspace_id}/memories/search`
- `POST /api/workspaces/{workspace_id}/memories/consolidate`
- `POST /api/workspaces/{workspace_id}/memories/evolutions`
- `GET /api/workspaces/{workspace_id}/memories/evolutions`
- `GET /api/workspaces/{workspace_id}/memories/evolutions/{draft_id}`
- `POST /api/workspaces/{workspace_id}/memories/evolutions/{draft_id}:apply`
- `POST /api/workspaces/{workspace_id}/memories/evolutions/{draft_id}:reject`

The frontend Memory page uses the global endpoints for browsing and text search.
The subagent memory tab uses the workspace list endpoint filtered by
`scope=role`, `role_id`, and `status=active`.

Task completion writes both a working task-summary entry and a persistent
`role-performance` insight when verification data is available. Role
self-assessment reads those Memory Bank insights directly.

## Frontend Surface

The main sidebar feature list includes Memory directly below IM Gateway.

The Memory page provides:

- graphical architecture map for capture, tier consolidation, and reuse
- workspace filter
- tier filter
- scope filter
- status filter
- text search
- result list with tags and timestamps
- detail pane for selected entries, including lifecycle fields such as status,
  source, confidence, update time, and expiry
- controls to draft a skill or SOP skill from a selected active memory entry,
  then apply the reviewed draft into the app-scoped skill directory

The old subagent UI page for manual role summaries is removed. The subagent
drawer now shows Memory Bank entries only.

## Migration

Startup migration is automatic:

1. Create `memory_entries` and indexes.
2. Normalize any already-persisted `source=reflection` rows to
   `source=consolidation`.
3. If a supported `role_memories` table exists, import:
   - `content_markdown` as a `summary`
   - `performance_json` as an `insight` tagged `role-performance`
   - `assessment_state_json` as an `insight` tagged `role-assessment`
4. Mark imported rows as `tier=persistent`, `scope=role`,
   `source=consolidation`, `confidence_score=0.8`.
5. Drop `role_memories`.
6. Drop `role_daily_memories`.
7. On server start, backfill active Memory Bank entries into the retrieval
   index so migrated rows are searchable through FTS-backed search.

Unsupported legacy table shapes are dropped with a warning because the current
runtime has no legacy reader.

## Verification

Required coverage:

- repository creates `memory_entries` and indexes
- legacy `role_memories` migration imports rows and drops the old table
- task completion writes Memory Bank entries
- role prompt injection reads Memory Bank entries
- global memory list/search endpoints work
- workspace memory CRUD/search/consolidation endpoints work
- memory evolution draft APIs create, list, apply, and reject drafts
- applying a memory evolution draft writes a valid skill and reloads skills
- subagent UI no longer renders manual summary controls
- sidebar renders Memory below IM Gateway
