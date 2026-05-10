# FE-1: Memory Bank Technical Specification

> **Feature ID**: FE-1
> **Name**: Cross-Run Memory Bank
> **Status**: Implemented
> **Updated**: 2026-05-10
> **Strictness**: high

## Overview

Memory Bank is the durable long-term memory system for Relay Teams. It replaces
legacy role-memory storage with typed, tagged, versioned entries in
`memory_entries`.

The runtime no longer reads or writes legacy role-memory services. During
`MemoryBankRepository` initialization, supported legacy `role_memories` rows are
migrated into `memory_entries` and the old table is dropped. `role_daily_memories`
is also dropped if present.

## Goals

- Persist structured memory across runs, sessions, roles, and workspaces.
- Support Working, Medium-term, and Persistent memory tiers.
- Provide query, search, create, update, delete, and consolidation APIs.
- Inject relevant Memory Bank entries into role prompts as `## Project Memory`.
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

The frontend Memory page uses the global endpoints for browsing and text search.
The subagent memory tab uses the workspace list endpoint filtered by
`scope=role`, `role_id`, and `status=active`.

## Frontend Surface

The main sidebar feature list includes Memory directly below IM Gateway.

The Memory page provides:

- workspace filter
- tier filter
- scope filter
- status filter
- text search
- result list with tags and timestamps
- detail pane for selected entries

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
- subagent UI no longer renders manual summary controls
- sidebar renders Memory below IM Gateway
