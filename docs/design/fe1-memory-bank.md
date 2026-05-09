# FE-1: Memory Bank -- Technical Specification

> **Feature ID**: FE-1
> **Name**: Cross-Run Memory Bank
> **Status**: Implemented foundation -- runtime wiring in progress
> **Created**: 2026-05-04
> **Updated**: 2026-05-09
> **Strictness**: high

---

## 1. Overview

### 1.1 Problem Statement

relay-teams roles currently store memory as a single flat markdown blob per `(role_id, workspace_id)` in the `role_memories` table. This has three critical limitations:

1. **No cross-Run knowledge transfer**: Run-level context is discarded when a Run completes. The reflection memory appended via `record_task_result()` is an unstructured bullet list with no metadata, confidence scores, or categorization.
2. **No retrieval**: `RetrievalScopeKind.MEMORY` exists in the retrieval module but is never wired to any consumer. Memory cannot be queried by relevance, tag, or time range.
3. **No lifecycle management**: Entries accumulate without pruning, deduplication, confidence decay, or structured consolidation. The only lifecycle mechanism is LLM-based reflection rewriting in `subagent_reflection.py`, which targets conversation compaction rather than cross-run project knowledge.

### 1.2 Goal

Build a three-tier, six-operation Memory Bank that:

- Persists structured memory entries across Runs, Sessions, and workspaces
- Integrates with the existing FTS5 retrieval infrastructure via `RetrievalScopeKind.MEMORY`
- Automatically consolidates working memory at Run/Task completion via existing hook events
- Makes typed, tagged, versioned entries the primary long-term memory path while maintaining backward compatibility with the existing reflection memory injection layer
- Provides REST API endpoints and CLI commands for memory management

The legacy `role_memories` table remains a migration bridge for reflection
summaries, session projections, and older subagent refresh flows. New runtime
memory behavior should use Memory Bank entries in `memory_entries`; new features
should not add capabilities to the legacy markdown blob.

### 1.3 Academic Foundation

| Source | Key Contribution |
|--------|------------------|
| Google ADK Memory Bank | Memory Bank concept and tiered storage |
| Self-Evolving Agents (Princeton/Tsinghua/CMU) | Experience accumulation framework |
| Survey of 25 academic papers (hello/docs/memory/research.md) | Six memory operations + three-tier classification |
| Mem0 engineering practice | Graph-structured memory: +26% LOCOMO benchmark, -91% p95 latency |

### 1.4 Out of Scope

- Vector embedding or semantic search beyond FTS5 BM25
- Graph-structured memory relationships (future consideration based on Mem0 data)
- Memory sharing across workspace boundaries
- A2A protocol integration for memory exchange
- Modifications to the existing `subagent_reflection.py` compaction mechanism

---

## 2. Architecture Overview

### 2.1 Three-Tier Memory Hierarchy

```
                    +----------------------------+
                    |   TIER 3: PERSISTENT       |
                    |   (Project/Workspace-scoped)|
                    |   Survives across sessions  |
                    |   Long-lived knowledge      |
                    +----------------------------+
                              ^  Consolidation
                              |  Condensation
                    +----------------------------+
                    |   TIER 2: MEDIUM-TERM      |
                    |   (Session/Role-scoped)     |
                    |   Survives across runs      |
                    |   Within a session          |
                    +----------------------------+
                              ^  Consolidation
                              |
                    +----------------------------+
                    |   TIER 1: WORKING          |
                    |   (Run-scoped)              |
                    |   Short-lived, high-fidelity|
                    |   Current run context       |
                    +----------------------------+
```

**Tier 1 -- Working Memory (Run-scoped)**:
- Lifecycle: created during a Run, expires when the Run completes
- Storage: written to `memory_entries` with `tier=WORKING`, `run_id` populated
- Content: task results, tool outputs, intermediate discoveries, errors encountered
- Consolidation trigger: Run completion or Task completion events

**Tier 2 -- Medium-term Memory (Session/Role-scoped)**:
- Lifecycle: survives across Runs within a session, or persists for a specific role
- Storage: `tier=MEDIUM_TERM`, `session_id` and/or `role_id` populated
- Content: accumulated insights, patterns observed, corrections applied
- Scope: `SESSION` (scoped to `session_id`) or `ROLE` (scoped to `role_id + workspace_id`)

**Tier 3 -- Persistent Memory (Project/Workspace-scoped)**:
- Lifecycle: survives across sessions, tied to workspace
- Storage: `tier=PERSISTENT`, `workspace_id` populated
- Content: project constraints, decision records, failure modes, architectural knowledge
- Scope: `WORKSPACE`

### 2.2 Six Core Operations

| Operation | Description | Trigger |
|-----------|-------------|---------|
| **Consolidation** | Promote Working -> Medium-term -> Persistent | Run/Task completion hook |
| **Updating** | Modify existing entries with versioning | API/CLI mutation |
| **Indexing** | Full-text search via existing FTS5 | Write/update operations |
| **Forgetting** | TTL expiry + confidence decay + capacity limits | Periodic sweep + read-time filter |
| **Retrieval** | Query by scope, tags, time range, full-text | API/CLI query + prompt injection |
| **Condensation** | LLM summarization of verbose entries | Explicit API call or scheduled task |

### 2.3 System Context Diagram

```
  +----------+     +-----------------+     +------------------+
  | REST API |---->| MemoryBank      |---->| memory_entries   |
  | /api/..  |<----| Service         |<----| (SQLite table)   |
  +----------+     +-------+---------+     +------------------+
                            |
           +----------------+----------------+
           |                |                 |
  +--------v------+  +-----v--------+  +----v-----------+
  | MemoryBank    |  | Retrieval    |  | Consolidation  |
  | Repository    |  | Service      |  | Engine         |
  | (SQLite CRUD) |  | (FTS5 MEMORY |  | (LLM-based    |
  +---------------+  |  scope)      |  |  extraction)  |
                     +--------------+  +---------------+
```

---

## 3. Data Models (Pydantic v2)

All models reside in `src/relay_teams/memory/`. Every model uses `ConfigDict(extra="forbid")`.

### 3.1 Enums

**`MemoryTier`** -- The three-tier classification.

| Value | Description |
|-------|-------------|
| `WORKING` | Run-scoped, short-lived, high-fidelity |
| `MEDIUM_TERM` | Session/Role-scoped, medium-lived |
| `PERSISTENT` | Workspace-scoped, long-lived |

**`MemoryScope`** -- The scope of visibility for a memory entry.

| Value | Description |
|-------|-------------|
| `WORKSPACE` | Visible to all roles in the workspace |
| `SESSION` | Visible within a specific session |
| `ROLE` | Visible only to a specific role in a workspace |

**`MemoryEntryKind`** -- The structural type of the memory entry content.

| Value | Description |
|-------|-------------|
| `INSIGHT` | A learned insight or observation |
| `CONSTRAINT` | A project constraint (e.g., "uses Pydantic v2") |
| `DECISION` | A recorded decision (e.g., "chose SQLite over PostgreSQL") |
| `FAILURE_MODE` | A known failure pattern |
| `PREFERENCE` | A user/role preference |
| `FACT` | A factual observation about the project |
| `SUMMARY` | A condensed summary from condensation |

**`MemoryEntryStatus`** -- Lifecycle status of a memory entry.

| Value | Description |
|-------|-------------|
| `ACTIVE` | Currently valid and retrievable |
| `SUPERSEDED` | Replaced by a newer entry (linked via `superseded_by_id`) |
| `EXPIRED` | TTL-based expiry or confidence below threshold |

**`MemorySourceKind`** -- What created the memory entry.

| Value | Description |
|-------|-------------|
| `CONSOLIDATION` | Automatic consolidation from working memory |
| `MANUAL` | Created via API/CLI by user |
| `REFLECTION` | Created by reflection rewriting |
| `CONDENSATION` | Created by LLM condensation |
| `TASK_RESULT` | Extracted from a task completion event |

### 3.2 Core Models

**`MemoryContent`** -- Structured content for a memory entry (not flat markdown).

Fields:
- `title`: `str` (min_length=1, max_length=500) -- Human-readable summary title
- `body`: `str` (min_length=1) -- Detailed content body
- `context`: `str` = "" -- Additional context about when/why this was recorded
- `outcome`: `str` = "" -- What was the result or action taken (for DECISION and FAILURE_MODE kinds)

**`MemoryEntry`** -- The primary memory entry model.

Fields:
- `id`: `str` (min_length=1) -- Generated as `mem-{uuid}`
- `tier`: `MemoryTier`
- `scope`: `MemoryScope`
- `workspace_id`: `str` (min_length=1)
- `session_id`: `str | None` = None -- Required when scope=SESSION
- `run_id`: `str | None` = None -- Required when tier=WORKING
- `role_id`: `str | None` = None -- Required when scope=ROLE
- `kind`: `MemoryEntryKind`
- `status`: `MemoryEntryStatus` = `ACTIVE`
- `content`: `MemoryContent`
- `tags`: `tuple[str, ...]` = () -- Each tag: non-empty, lowercase, alphanumeric+dash+underscore
- `confidence_score`: `float` = 1.0 (ge=0.0, le=1.0) -- Current confidence; decays over time
- `source`: `MemorySourceKind`
- `source_ref`: `str` = "" -- Reference to source (e.g., `task:{task_id}`, `run:{run_id}`)
- `superseded_by_id`: `str | None` = None -- Links to the entry that superseded this one
- `parent_entry_id`: `str | None` = None -- Links to the parent entry (for consolidation lineage)
- `version`: `int` = 1 (ge=1) -- Updated on each modification
- `created_at`: `datetime`
- `updated_at`: `datetime`
- `expires_at`: `datetime | None` = None -- TTL-based expiry
- `last_accessed_at`: `datetime | None` = None -- Updated on retrieval
- `access_count`: `int` = 0 (ge=0) -- Incremented on retrieval
- `metadata`: `dict[str, str]` = {} (max 20 keys, each key/value max 500 chars) -- Extensible key-value metadata

Validation rules:
- When `scope=SESSION`, `session_id` must be non-null
- When `scope=ROLE`, `role_id` must be non-null
- When `tier=WORKING`, `run_id` must be non-null
- `tags` must contain no duplicates (case-insensitive)
- `confidence_score` starts at 1.0 for new entries; decays via forgetting logic

**`MemoryEntrySummary`** -- Lightweight projection for list/query responses.

Fields:
- `id`: `str`
- `tier`: `MemoryTier`
- `scope`: `MemoryScope`
- `workspace_id`: `str`
- `session_id`: `str | None`
- `role_id`: `str | None`
- `kind`: `MemoryEntryKind`
- `status`: `MemoryEntryStatus`
- `content_title`: `str`
- `content_body_preview`: `str` (first 200 chars of body)
- `tags`: `tuple[str, ...]`
- `confidence_score`: `float`
- `source`: `MemorySourceKind`
- `version`: `int`
- `created_at`: `datetime`
- `updated_at`: `datetime`
- `expires_at`: `datetime | None`

### 3.3 Request/Response Models

**`CreateMemoryEntryRequest`**:

Fields:
- `tier`: `MemoryTier`
- `scope`: `MemoryScope`
- `workspace_id`: `str` (min_length=1)
- `session_id`: `str | None` = None
- `run_id`: `str | None` = None
- `role_id`: `str | None` = None
- `kind`: `MemoryEntryKind`
- `content`: `MemoryContent`
- `tags`: `tuple[str, ...]` = ()
- `confidence_score`: `float` = 1.0
- `source`: `MemorySourceKind` = `MANUAL`
- `source_ref`: `str` = ""
- `expires_at`: `datetime | None` = None (defaults: WORKING=4h, MEDIUM_TERM=7d, PERSISTENT=null)
- `metadata`: `dict[str, str]` = {}

Validation: same scope/tier rules as `MemoryEntry`.

**`UpdateMemoryEntryRequest`**:

Fields:
- `content`: `MemoryContent | None` = None
- `tags`: `tuple[str, ...] | None` = None
- `confidence_score`: `float | None` = None
- `status`: `MemoryEntryStatus | None` = None
- `expires_at`: `datetime | None | ...` = ... (sentinel to distinguish null from absent)
- `metadata`: `dict[str, str] | None` = None

At least one field must be provided. On update, `version` increments and `updated_at` refreshes.

**`MemoryQuery`** -- For retrieval/search requests.

Fields:
- `workspace_id`: `str` (min_length=1) -- Required scope anchor
- `tier`: `MemoryTier | None` = None -- Filter by tier
- `scope`: `MemoryScope | None` = None -- Filter by scope
- `session_id`: `str | None` = None -- When scope=SESSION
- `role_id`: `str | None` = None -- When scope=ROLE
- `kind`: `MemoryEntryKind | None` = None -- Filter by kind
- `status`: `MemoryEntryStatus | None` = None -- Default: ACTIVE only
- `tags`: `tuple[str, ...]` = () -- Entries must contain ALL specified tags
- `text_query`: `str` = "" -- Full-text search query (FTS5 MATCH)
- `created_after`: `datetime | None` = None
- `created_before`: `datetime | None` = None
- `min_confidence`: `float` = 0.0 -- Filter entries below threshold
- `limit`: `int` = 20 (ge=1, le=100)
- `offset`: `int` = 0 (ge=0)

**`MemoryQueryResult`**:

Fields:
- `items`: `tuple[MemoryEntrySummary, ...]`
- `total_count`: `int` (ge=0) -- Total matching entries (not just this page)
- `offset`: `int`
- `limit`: `int`

**`MemoryConsolidationRequest`**:

Fields:
- `workspace_id`: `str` (min_length=1)
- `session_id`: `str | None` = None -- Consolidate entries within a session
- `role_id`: `str | None` = None -- Consolidate entries for a specific role
- `source_run_id`: `str | None` = None -- Consolidate working memory from a specific run
- `target_tier`: `MemoryTier` -- Target tier for consolidation (MEDIUM_TERM or PERSISTENT)
- `target_scope`: `MemoryScope` -- Target scope for consolidated entries
- `filter_tags`: `tuple[str, ...]` = () -- Only consolidate entries with these tags
- `filter_kind`: `MemoryEntryKind | None` = None -- Only consolidate entries of this kind

**`MemoryConsolidationResult`**:

Fields:
- `source_entry_count`: `int` -- Number of entries examined
- `consolidated_entry_count`: `int` -- Number of entries produced
- `superseded_entry_ids`: `tuple[str, ...]` -- IDs of source entries marked SUPERSEDED
- `new_entry_ids`: `tuple[str, ...]` -- IDs of newly created consolidated entries

**`MemorySearchRequest`**:

Fields:
- `workspace_id`: `str` (min_length=1)
- `text_query`: `str` (min_length=1)
- `tier`: `MemoryTier | None` = None
- `scope`: `MemoryScope | None` = None
- `session_id`: `str | None` = None
- `role_id`: `str | None` = None
- `kind`: `MemoryEntryKind | None` = None
- `tags`: `tuple[str, ...]` = () -- Entries must contain ALL specified tags
- `min_confidence`: `float` = 0.3
- `limit`: `int` = 10 (ge=1, le=100)

**`MemorySearchResult`**:

Fields:
- `items`: `tuple[MemorySearchHit, ...]`
- `total_count`: `int`

**`MemorySearchHit`**:

Fields:
- `entry`: `MemoryEntrySummary`
- `score`: `float` -- FTS5 BM25 relevance score
- `rank`: `int` (ge=1)
- `snippet`: `str` -- FTS5-generated snippet with match highlights

---

## 4. Database Schema (SQLite)

### 4.1 New Table: `memory_entries`

```sql
CREATE TABLE IF NOT EXISTS memory_entries (
    memory_id         TEXT PRIMARY KEY,
    tier              TEXT NOT NULL,
    scope             TEXT NOT NULL,
    workspace_id      TEXT NOT NULL,
    session_id        TEXT,
    run_id            TEXT,
    role_id           TEXT,
    kind              TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active',
    content_title     TEXT NOT NULL,
    content_body      TEXT NOT NULL,
    content_context   TEXT NOT NULL DEFAULT '',
    content_outcome   TEXT NOT NULL DEFAULT '',
    tags              TEXT NOT NULL DEFAULT '',
    confidence_score  REAL NOT NULL DEFAULT 1.0,
    source            TEXT NOT NULL,
    source_ref        TEXT NOT NULL DEFAULT '',
    superseded_by_id  TEXT,
    parent_entry_id   TEXT,
    version           INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    expires_at        TEXT,
    last_accessed_at  TEXT,
    access_count      INTEGER NOT NULL DEFAULT 0,
    metadata_json     TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (superseded_by_id) REFERENCES memory_entries(memory_id),
    FOREIGN KEY (parent_entry_id)  REFERENCES memory_entries(memory_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_entries_workspace_tier
    ON memory_entries(workspace_id, tier, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_entries_workspace_scope
    ON memory_entries(workspace_id, scope, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_entries_session
    ON memory_entries(session_id, tier, status, updated_at DESC)
    WHERE session_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_memory_entries_role
    ON memory_entries(workspace_id, role_id, tier, status, updated_at DESC)
    WHERE role_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_memory_entries_run
    ON memory_entries(run_id, status)
    WHERE run_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_memory_entries_expires
    ON memory_entries(expires_at)
    WHERE expires_at IS NOT NULL AND status = 'active';

CREATE INDEX IF NOT EXISTS idx_memory_entries_source_ref
    ON memory_entries(source_ref);
```

Design decisions:
- `tags` stored as space-separated string for FTS5 keyword column matching; validated as tuple in Pydantic model
- `metadata_json` stored as JSON string; not indexed (filtering by metadata is not a primary access pattern)
- `content_title`, `content_body`, `content_context`, `content_outcome` flattened from `MemoryContent` for direct FTS5 indexing
- Composite indexes target the primary query patterns: by workspace+tier, workspace+scope, session, role, run, and expiry
- `superseded_by_id` and `parent_entry_id` are soft foreign keys for lineage tracking

### 4.2 FTS5 Integration via Existing Retrieval Infrastructure

The Memory Bank does **not** create its own separate FTS5 virtual table. Instead, it uses the existing `RetrievalService` + `SqliteFts5RetrievalStore` infrastructure with `RetrievalScopeKind.MEMORY`.

**Scope ID convention**: `memory:{workspace_id}` for workspace-scoped, `memory:{workspace_id}:session:{session_id}` for session-scoped, `memory:{workspace_id}:role:{role_id}` for role-scoped.

Each `MemoryEntry` maps to a `RetrievalDocument`:

| RetrievalDocument field | Source |
|------------------------|--------|
| `scope_kind` | `RetrievalScopeKind.MEMORY` |
| `scope_id` | Derived from workspace_id + scope + session_id/role_id |
| `document_id` | `memory_id` |
| `title` | `content_title` |
| `body` | `content_body + " " + content_context + " " + content_outcome` |
| `keywords` | `tags` |

The `MemoryBankRepository` calls `RetrievalService.upsert_documents()` on every create/update, and `RetrievalService.delete_documents()` on every delete.

### 4.3 Existing Table: `role_memories` (Unchanged)

The existing `role_memories` table continues to serve the current reflection memory injection layer. During the migration period (Section 9), both tables coexist. After migration, `role_memories` continues to exist for backward compatibility but the Memory Bank becomes the primary memory storage.

---

## 5. Six Core Operations

### 5.1 Consolidation

**Purpose**: Extract key insights from working memory and promote them to medium-term or persistent memory.

**Trigger Points**:
1. `HookEventName.TASK_COMPLETED` -- After task completion hooks execute (via `PersistenceHarness.execute_task_completed_hooks()`)
2. `RunEventType.RUN_COMPLETED` -- When a Run reaches terminal state
3. Manual trigger via `POST /api/workspaces/{workspace_id}/memories/consolidate`

**Consolidation Flow**:

1. **Source selection**: Query all `WORKING` tier entries matching the consolidation request filters (workspace, session, role, run)
2. **Filtering**: Skip entries with `confidence_score < 0.3` or `status != ACTIVE`
3. **LLM extraction**: Send filtered entries to an LLM agent with a consolidation prompt that asks it to:
   - Identify key insights, decisions, failure modes, constraints, and facts
   - Classify each extracted item by `MemoryEntryKind`
   - Assign confidence scores based on source evidence strength
   - Remove duplicates and merge related items
4. **Entry creation**: Create new entries at the target tier with `source=CONSOLIDATION`
5. **Source supersession**: Mark source entries as `SUPERSEDED` with `superseded_by_id` pointing to new entries
6. **Indexing**: Upsert new entries into the retrieval FTS5 index

**LLM Requirements**:
- Must use streaming API (consistent with project transport rules)
- Uses the reflection model config resolved by `ServerContainer.resolve_reflection_model_config()`
- Prompt must include the structured entry data, not raw markdown
- Output must be parseable into `MemoryEntry` objects

**Automatic TTL defaults**:
- WORKING entries: `expires_at` = `created_at + 4 hours`
- MEDIUM_TERM entries: `expires_at` = `created_at + 7 days`
- PERSISTENT entries: `expires_at` = null (no expiry)

### 5.2 Updating

**Purpose**: Modify existing memory entries while preserving version history.

**Behavior**:
- Each update increments `version` by 1
- `updated_at` refreshes to current UTC time
- If `content` changes, the FTS5 index entry is updated via `RetrievalService.upsert_documents()`
- If `status` changes to `SUPERSEDED`, the FTS5 index entry is removed
- Updates that change `confidence_score` to below the configured threshold (default 0.2) automatically set `status=EXPIRED`

### 5.3 Indexing

**Purpose**: Enable full-text search over memory entries via the existing FTS5 retrieval infrastructure.

**Integration**:
- Uses `RetrievalScopeKind.MEMORY` with `RetrievalTokenizer.UNICODE61` (primary) and `RetrievalTokenizer.TRIGRAM` (for code-heavy entries)
- Scope config defaults: `title_weight=8.0`, `body_weight=2.0`, `keyword_weight=5.0` (memory titles are high-signal)
- The `MemoryBankRepository` calls `RetrievalService.upsert_documents()` asynchronously after every create/update operation
- On delete, calls `RetrievalService.delete_documents()` to remove the entry from the index

**Scope ID resolution**:
- `memory:{workspace_id}` -- All workspace-scoped entries
- `memory:{workspace_id}:session:{session_id}` -- Session-scoped entries
- `memory:{workspace_id}:role:{role_id}` -- Role-scoped entries

### 5.4 Forgetting

**Purpose**: Prevent memory bank bloat and retrieval noise by removing or deprioritizing low-value entries.

**Three forgetting mechanisms**:

1. **TTL-based expiry**: When `expires_at` is not null and `expires_at < now`:
   - Entry `status` set to `EXPIRED`
   - Entry removed from FTS5 index
   - Triggered by a periodic sweep (on any read or explicit `POST /consolidate` call)

2. **Confidence decay**: Each entry's `confidence_score` decays over time:
   - Decay formula: `confidence_score *= decay_factor` where `decay_factor` is configurable per tier
   - WORKING: no decay (short-lived)
   - MEDIUM_TERM: `decay_factor = 0.98` per day (halves in ~34 days)
   - PERSISTENT: `decay_factor = 0.995` per day (halves in ~138 days)
   - Decay is applied lazily on read or during consolidation sweep
   - Entries below `min_confidence` threshold (default 0.2) are marked `EXPIRED`

3. **Capacity limits**: Per-scope entry count limits:
   - WORKING per run: max 200 entries
   - MEDIUM_TERM per session/role: max 500 entries
   - PERSISTENT per workspace: max 2000 entries
   - When limit is reached, lowest-confidence entries are consolidated or expired
   - Applied during consolidation sweep

**Sweep trigger**: The forgetting sweep runs as part of consolidation operations and can also be triggered explicitly.

### 5.5 Retrieval

**Purpose**: Query memory entries by scope, tags, time range, and full-text search.

**Query modes**:

1. **Structured query** (`MemoryQuery`): Filter by tier, scope, kind, status, tags, time range, confidence. Returns paginated `MemoryEntrySummary` results. Uses direct SQL against `memory_entries` table.

2. **Full-text search** (`MemorySearchRequest`): FTS5 BM25 search via `RetrievalService`. Returns `MemorySearchHit` with relevance scores and snippets. Can be combined with structured filters (tier, scope, kind, tags, confidence) applied as post-filter on FTS5 results.

3. **Prompt injection query**: Used by the injection layer to retrieve relevant memories for role system_prompt augmentation. Queries `PERSISTENT` entries for the workspace plus `ROLE`-scoped entries for the target role. Returns top-N by relevance.

**Access tracking**: Every retrieval updates `last_accessed_at` and increments `access_count` on matching entries.

### 5.6 Condensation

**Purpose**: Compress multiple related entries into higher-level abstract summaries using LLM.

**Trigger**: Explicit API call via `POST /consolidate` with consolidation logic that produces summaries, or a scheduled condensation step.

**Condensation flow**:
1. Select a group of related entries (same tags, similar content via FTS5 clustering, or explicit set)
2. Send entries to LLM with a condensation prompt
3. LLM produces a single `SUMMARY`-kind entry covering the group
4. New entry created with `source=CONDENSATION`, `parent_entry_id` linking to the first source entry
5. Source entries marked `SUPERSEDED` with `superseded_by_id` pointing to the new summary entry
6. FTS5 index updated: source entries removed, new entry added

**LLM Requirements**:
- Must use streaming API
- Uses reflection model config
- Output must produce a valid `MemoryContent` with `kind=SUMMARY`

---

## 6. REST API Endpoints

All endpoints are under `/api/workspaces/{workspace_id}/memories`. They follow the existing convention from `api-design.md`: JSON request/response, ISO 8601 UTC timestamps, common status codes.

### 6.1 `GET /api/workspaces/{workspace_id}/memories`

List/query memory entries with structured filters.

Query parameters (map to `MemoryQuery` fields):
- `tier` (optional): `working`, `medium_term`, `persistent`
- `scope` (optional): `workspace`, `session`, `role`
- `session_id` (optional): filter to session
- `role_id` (optional): filter to role
- `kind` (optional): `insight`, `constraint`, `decision`, `failure_mode`, `preference`, `fact`, `summary`
- `status` (optional): default `active`
- `tags` (optional): comma-separated, all must match
- `created_after` (optional): ISO 8601
- `created_before` (optional): ISO 8601
- `min_confidence` (optional): float, default 0.0
- `limit` (optional): int, default 20, max 100
- `offset` (optional): int, default 0

Response: `MemoryQueryResult` (200)

### 6.2 `POST /api/workspaces/{workspace_id}/memories`

Create a new memory entry.

Request body: `CreateMemoryEntryRequest`

Response: `MemoryEntry` (201)

Validation errors (422):
- Scope/tier field requirements violations
- Invalid tags
- Confidence score out of range

### 6.3 `GET /api/workspaces/{workspace_id}/memories/{memory_id}`

Get a specific memory entry by ID.

Response: `MemoryEntry` (200) or 404

### 6.4 `PUT /api/workspaces/{workspace_id}/memories/{memory_id}`

Update an existing memory entry.

Request body: `UpdateMemoryEntryRequest`

Response: `MemoryEntry` (200) or 404

Side effects:
- Version incremented
- FTS5 index updated if content changed
- Entry expired if confidence falls below threshold

### 6.5 `DELETE /api/workspaces/{workspace_id}/memories/{memory_id}`

Delete a memory entry permanently.

Response: 204 (no content) or 404

Side effects:
- FTS5 index entry removed
- Any entries with `superseded_by_id` pointing to this entry have that field set to null

### 6.6 `POST /api/workspaces/{workspace_id}/memories/consolidate`

Trigger consolidation of working/medium-term memory into higher tiers.

Request body: `MemoryConsolidationRequest`

Response: `MemoryConsolidationResult` (200)

This is the programmatic entry point for both consolidation and condensation operations.

### 6.7 `POST /api/workspaces/{workspace_id}/memories/search`

Full-text search over memory entries.

Request body: `MemorySearchRequest`

Response: `MemorySearchResult` (200)

---

## 7. Integration Points

### 7.1 Hook Event Integration (Consolidation Trigger)

**Integration point**: `TaskPersistenceHarness.execute_task_completed_hooks()` in `src/relay_teams/agents/orchestration/harnesses/persistence_harness.py`

After existing task completion hooks execute, the persistence harness calls into `MemoryBankService.consolidate_from_task_completion()` with the following data from `TaskCompletedInput`:
- `workspace_id` (from instance context)
- `session_id`
- `run_id`
- `completed_task_id`
- `title`, `objective`, `output_text`, `completion_reason`

**Behavior**:
1. Create a `WORKING` entry from the task result (if the task produced meaningful output)
2. If the task has a parent task that also completed in the same run, trigger a run-level consolidation check
3. When all tasks in a run are complete, promote `WORKING` entries to `MEDIUM_TERM` scoped to `SESSION`

**Implementation pattern**: The harness calls through a new dependency injected into the harness model, similar to how `hook_service` is injected:

`MemoryBankService` is optionally available on the harness. When present, the harness calls consolidation. When absent (e.g., during testing or when memory bank is disabled), the call is skipped.

### 7.2 Existing `roles/memory_service.py` Integration

**Current behavior**: `RoleMemoryService.record_task_result()` appends a bullet list to the flat markdown blob.

**New behavior (dual-write during migration)**:
1. `role_memories` table continues to be updated as before (no breaking change)
2. Simultaneously, a `WORKING` entry is created in `memory_entries` via `MemoryBankService`
3. The existing `build_injected_memory()` method continues to read from `role_memories` for the `## Reflection Memory` section

**Post-migration behavior**:
1. `build_injected_memory()` reads from `memory_entries` instead of `role_memories`
2. Injects both the `## Reflection Memory` section (from existing ROLE-scoped entries) and a new `## Project Memory` section (from WORKSPACE-scoped PERSISTENT entries)
3. `role_memories` table is kept but no longer actively written

### 7.3 Retrieval Service Integration

**Integration point**: `src/relay_teams/retrieval/retrieval_service.py`

The `MemoryBankRepository` holds a reference to `RetrievalService` (or `SqliteFts5RetrievalStore` directly). On every write operation:

1. Map the `MemoryEntry` to a `RetrievalDocument` with `scope_kind=RetrievalScopeKind.MEMORY`
2. Call `retrieval_service.upsert_documents()` with the mapped document
3. On delete, call `retrieval_service.delete_documents()`

**Scope config initialization**: The first write to a new scope automatically creates the scope config via `upsert_documents()`. Default config:
- `tokenizer`: `RetrievalTokenizer.UNICODE61`
- `title_weight`: 8.0
- `body_weight`: 2.0
- `keyword_weight`: 5.0

### 7.4 Prompt Injection Integration

**Integration point**: `src/relay_teams/roles/memory_injection.py` -- `build_role_with_memory()`

The existing function injects reflection memory as `## Reflection Memory`. It is extended to also inject project-level persistent memory as `## Project Memory`.

**Retrieval query for injection**:
- Scope: `WORKSPACE` (for project-level PERSISTENT entries) + `ROLE` (for role-specific entries)
- Tier: `PERSISTENT` and `MEDIUM_TERM`
- Status: `ACTIVE`
- `min_confidence`: 0.5
- Sorted by: `confidence_score DESC`, `updated_at DESC`
- Limit: top 15 entries

**Injection format**:
```
## Project Memory
{entry.content.title}
{entry.content.body}

## Reflection Memory
{existing_reflection_markdown}
```

### 7.5 CLI Commands

**Module**: `src/relay_teams/interfaces/cli/memory_cli.py`

Commands registered under a new `memory` subcommand group:

| Command | Description |
|---------|-------------|
| `memory list --workspace-id ID [--tier T] [--scope S] [--format json]` | List memories |
| `memory get --workspace-id ID --memory-id ID [--format json]` | Get a specific memory |
| `memory create --workspace-id ID --tier T --scope S --kind K --title T --body B [--tags T] [--format json]` | Create a memory entry |
| `memory update --workspace-id ID --memory-id ID [--title T] [--body B] [--tags T] [--confidence C] [--format json]` | Update a memory entry |
| `memory delete --workspace-id ID --memory-id ID` | Delete a memory entry |
| `memory search --workspace-id ID --query Q [--limit N] [--format json]` | Full-text search |
| `memory consolidate --workspace-id ID [--target-tier T] [--format json]` | Trigger consolidation |
| `memory migrate --workspace-id ID [--dry-run] [--format json]` | Run migration from role_memories |

Output: Default table format. `--format json` outputs structured JSON matching the API response models.

---

## 8. Module Structure

### 8.1 New Package: `src/relay_teams/memory/`

```
src/relay_teams/memory/
    __init__.py                  # Public API exports
    memory_models.py             # All Pydantic models and enums (Section 3)
    memory_repository.py         # MemoryBankRepository (SQLite CRUD + retrieval sync)
    memory_service.py            # MemoryBankService (business logic for 6 operations)
    memory_consolidation.py      # ConsolidationEngine (LLM-based consolidation/condensation)
    memory_injection.py          # build_memory_injection_text() -- for prompt augmentation
    memory_forgetting.py         # ForgettingEngine (TTL sweep, confidence decay, capacity)
    memory_defaults.py           # Default config values, TTL presets, capacity limits
```

### 8.2 Dependency Graph

```
memory/__init__.py
    <- memory_models.py      (no internal deps beyond pydantic)
    <- memory_defaults.py    (no internal deps)
    <- memory_repository.py  -> memory_models
                              -> retrieval (RetrievalService, RetrievalScopeKind)
                              -> persistence (SharedSqliteRepository)
    <- memory_forgetting.py  -> memory_models
                              -> memory_repository
    <- memory_service.py     -> memory_models
                              -> memory_repository
                              -> memory_forgetting
                              -> memory_consolidation
                              -> memory_injection
    <- memory_consolidation.py -> memory_models
                                -> memory_repository
                                -> providers (LLM streaming)
    <- memory_injection.py   -> memory_models
                              -> memory_service (read-only, for retrieval)
```

### 8.3 Modified Existing Files

| File | Change |
|------|--------|
| `src/relay_teams/roles/memory_service.py` | `record_task_result()` dual-writes to `MemoryBankService` when available |
| `src/relay_teams/roles/memory_injection.py` | `build_role_with_memory()` calls `build_memory_injection_text()` for `## Project Memory` section |
| `src/relay_teams/agents/orchestration/harnesses/persistence_harness.py` | `TaskPersistenceHarness` receives optional `memory_bank_service`; calls consolidation after task completion |
| `src/relay_teams/interfaces/server/container.py` | `ServerContainer` builds `MemoryBankService` and `MemoryBankRepository`; exposes property |
| `src/relay_teams/interfaces/server/deps.py` | New `get_memory_bank_service()` dependency provider |
| `src/relay_teams/interfaces/server/app.py` | Registers `memory_router` from `routers/memories.py` |
| `src/relay_teams/interfaces/cli/__init__.py` | Registers `memory` CLI subcommand group |

### 8.4 New Files

| File | Purpose |
|------|---------|
| `src/relay_teams/memory/__init__.py` | Package exports |
| `src/relay_teams/memory/memory_models.py` | All data models from Section 3 |
| `src/relay_teams/memory/memory_repository.py` | SQLite CRUD + FTS5 sync |
| `src/relay_teams/memory/memory_service.py` | Business logic orchestrator |
| `src/relay_teams/memory/memory_consolidation.py` | LLM consolidation engine |
| `src/relay_teams/memory/memory_injection.py` | Prompt injection text builder |
| `src/relay_teams/memory/memory_forgetting.py` | Forgetting/delta sweep engine |
| `src/relay_teams/memory/memory_defaults.py` | Configuration constants |
| `src/relay_teams/interfaces/server/routers/memories.py` | FastAPI router for memory endpoints |
| `src/relay_teams/interfaces/cli/memory_cli.py` | Typer CLI commands |
| `tests/unit_tests/memory/__init__.py` | Test package |
| `tests/unit_tests/memory/test_memory_models.py` | Model validation tests |
| `tests/unit_tests/memory/test_memory_repository.py` | Repository CRUD + index sync tests |
| `tests/unit_tests/memory/test_memory_service.py` | Service logic tests |
| `tests/unit_tests/memory/test_memory_consolidation.py` | Consolidation flow tests |
| `tests/unit_tests/memory/test_memory_forgetting.py` | TTL/confidence/capacity tests |
| `tests/unit_tests/memory/test_memory_injection.py` | Injection text building tests |

---

## 9. Migration Plan

### 9.1 Phase 1: Additive (No Breakage)

1. Create `memory_entries` table and `src/relay_teams/memory/` package
2. Add REST API endpoints and CLI commands
3. Wire `MemoryBankService` into `ServerContainer` but do not inject into existing flows
4. Existing `role_memories` table and `RoleMemoryService` remain untouched
5. Manual and API-created memory entries coexist with reflection memory

**Verification**: All existing tests pass. New memory endpoints are functional. Existing reflection memory injection is unchanged.

### 9.2 Phase 2: Dual Write

1. Inject `MemoryBankService` into `TaskPersistenceHarness` (optional field)
2. When a task completes:
   a. Existing `record_task_result()` still writes to `role_memories`
   b. Additionally, a `WORKING` entry is created in `memory_entries`
3. After run completion, consolidation promotes `WORKING` -> `MEDIUM_TERM`
4. Inject project memory alongside reflection memory in `build_role_with_memory()`

**Verification**: Both `role_memories` and `memory_entries` are populated after task completion. The `## Project Memory` section appears in injected prompts. Existing reflection memory continues to work.

### 9.3 Phase 3: Migration of Existing Data

1. CLI command `memory migrate --workspace-id ID` reads all rows from `role_memories`
2. For each `(role_id, workspace_id, content_markdown)`:
   a. Parse the markdown bullet list into individual entries
   b. Create `MEDIUM_TERM` / `ROLE`-scoped entries for each parsed bullet
   c. Original `content_markdown` is preserved as a single `SUMMARY`-kind entry
3. `--dry-run` flag shows what would be migrated without writing
4. After migration, `build_injected_memory()` reads from `memory_entries` when entries exist, falls back to `role_memories` when not

**Verification**: Migrated entries produce equivalent injection text. Fallback to `role_memories` works for unmigrated workspaces.

### 9.4 Phase 4: Primary Source Switch

1. `build_injected_memory()` reads primarily from `memory_entries`
2. `role_memories` is no longer actively written by `record_task_result()`
3. `role_memories` table is retained for backward compatibility but is read-only
4. The `## Reflection Memory` section header is kept in injection for continuity

**Verification**: New runs use `memory_entries` as primary source. Old workspaces with unmigrated data still work via fallback.

---

## 10. Database Schema Documentation Updates

The following must be updated in `docs/core/database-schema.md` (per AGENTS.md rule: "Database schema and API changes do not need backward compatibility, but matching updates to `docs/core/database-schema.md` and `docs/core/api-design.md` must be included in the same task"):

1. Add new section `2.11 memory_entries` with the full `CREATE TABLE` DDL from Section 4.1
2. Add index documentation for all `idx_memory_entries_*` indexes
3. Update section `2.10 role_memories` notes to indicate it is in maintenance mode
4. Add cross-reference from `retrieval_scopes` that `MEMORY` scope kind is now used by the memory bank

---

## 11. API Design Documentation Updates

The following must be updated in `docs/core/api-design.md`:

1. Add new section **Memory Bank APIs** documenting all seven endpoints from Section 6
2. Update **Memory Notes** section to reflect the new memory bank as primary storage
3. Document the relationship between memory endpoints and existing reflection endpoints (they coexist during migration)

---

## 12. Acceptance Criteria (Definition of Done)

### 12.1 Core Models

| # | Criterion | Verification |
|---|-----------|--------------|
| AC-1 | All enums (`MemoryTier`, `MemoryScope`, `MemoryEntryKind`, `MemoryEntryStatus`, `MemorySourceKind`) exist with correct values | `basedpyright` clean; unit test enumerates all values |
| AC-2 | `MemoryEntry` model enforces scope/tier validation rules (SESSION requires session_id, ROLE requires role_id, WORKING requires run_id) | Unit test with invalid combinations raises `ValidationError` |
| AC-3 | `MemoryContent` model enforces min_length=1 on title and body | Unit test with empty strings raises `ValidationError` |
| AC-4 | No model uses `typing.Any` or loose `{}` structures | `ruff check` + `basedpyright` clean |

### 12.2 Repository

| # | Criterion | Verification |
|---|-----------|--------------|
| AC-5 | `memory_entries` table is created on first use with all columns and indexes | Integration test: fresh DB, verify schema via `PRAGMA table_info` |
| AC-6 | CRUD operations (create, read, update, delete) work correctly | Unit test: create entry, read by ID, update content, delete, verify 404 |
| AC-7 | FTS5 index is kept in sync: upsert on create/update, delete on delete | Integration test: create entry, search finds it; delete entry, search returns empty |
| AC-8 | Repository uses `aiosqlite` for all async methods, no sync bridge helper | Static analysis: grep repository file, no sync bridge calls |

### 12.3 Six Operations

| # | Criterion | Verification |
|---|-----------|--------------|
| AC-9 | **Consolidation**: WORKING entries are promoted to MEDIUM_TERM/PERSISTENT with correct source/superseded linkage | Unit test: create WORKING entries, consolidate, verify new entries and superseded status |
| AC-10 | **Updating**: Version increments, updated_at refreshes, FTS5 index is updated | Unit test: update content, verify version=2 and new time |
| AC-11 | **Indexing**: Entries are queryable via `RetrievalScopeKind.MEMORY` FTS5 | Integration test: create entry, search via `RetrievalService.search()` with MEMORY scope kind |
| AC-12 | **Forgetting**: Expired entries (TTL past) are marked EXPIRED and removed from index | Unit test: create entry with past `expires_at`, run sweep, verify status=EXPIRED and not in search |
| AC-13 | **Forgetting**: Confidence decay is applied and low-confidence entries expire | Unit test: create entry with low confidence, run sweep, verify expiry |
| AC-14 | **Retrieval**: Structured query returns paginated results with correct filtering | Unit test: create mixed entries, query by tier/scope/kind/tags, verify filtered results |
| AC-15 | **Condensation**: Multiple entries can be condensed into a single SUMMARY entry | Unit test: create related entries, condense, verify summary and superseded status |

### 12.4 API Endpoints

| # | Criterion | Verification |
|---|-----------|--------------|
| AC-16 | `GET /api/workspaces/{id}/memories` returns filtered list | Integration test: create entries with different tiers, query with tier filter |
| AC-17 | `POST /api/workspaces/{id}/memories` creates entry and returns 201 | Integration test: POST valid request, verify 201 and entry in DB |
| AC-18 | `GET /api/workspaces/{id}/memories/{mid}` returns specific entry | Integration test: create entry, GET by memory_id |
| AC-19 | `PUT /api/workspaces/{id}/memories/{mid}` updates entry | Integration test: update content, verify version bump |
| AC-20 | `DELETE /api/workspaces/{id}/memories/{mid}` returns 204 | Integration test: delete entry, verify 204 and absence from DB |
| AC-21 | `POST /api/workspaces/{id}/memories/consolidate` triggers consolidation | Integration test: create WORKING entries, consolidate, verify result |
| AC-22 | `POST /api/workspaces/{id}/memories/search` returns FTS5 results | Integration test: search by text, verify relevance ranking |

### 12.5 Integration

| # | Criterion | Verification |
|---|-----------|--------------|
| AC-23 | Task completion creates WORKING memory entry automatically | Integration test: complete a task, verify memory entry created |
| AC-24 | Existing reflection memory still injected after task completion | Regression test: existing `build_role_with_memory()` returns `## Reflection Memory` section |
| AC-25 | Project Memory section injected alongside Reflection Memory | Integration test: `build_role_with_memory()` includes `## Project Memory` when PERSISTENT entries exist |
| AC-26 | CLI commands work with `--format json` output | CLI integration test for each command |

### 12.6 Migration

| # | Criterion | Verification |
|---|-----------|--------------|
| AC-27 | `memory migrate --workspace-id ID --dry-run` shows preview without writing | CLI test: verify no DB changes |
| AC-28 | `memory migrate --workspace-id ID` creates entries from role_memories markdown | Integration test: seed role_memories, migrate, verify entries |
| AC-29 | Fallback: unmigrated workspaces still work via role_memories | Integration test: workspace with role_memories but no memory_entries entries |

### 12.7 Documentation

| # | Criterion | Verification |
|---|-----------|--------------|
| AC-30 | `docs/core/database-schema.md` updated with `memory_entries` section | Manual review: section exists with correct DDL |
| AC-31 | `docs/core/api-design.md` updated with Memory Bank APIs section | Manual review: all 7 endpoints documented |
| AC-32 | No existing tests broken by changes | `pytest tests/unit_tests` passes cleanly |

---

## 13. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| LLM consolidation produces low-quality entries | Medium | Medium | Confidence scoring + manual review via CLI; consolidation can be run with `--dry-run` |
| FTS5 index drift from entry mutations | Low | High | Every write operation syncs to FTS5; `rebuild_scope()` available for repair |
| Performance impact of dual-write during migration | Low | Low | Dual-write is fire-and-forget; failure to write to memory_entries does not block existing flow |
| Memory bank grows unbounded | Medium | High | Forgetting engine with TTL, confidence decay, and capacity limits |
| Backward compatibility break with existing reflection memory | Low | Critical | Phase-based migration with fallback; existing `role_memories` table is never dropped |

---

## 14. Prefix Convention

All `memory_id` values use the prefix `mem-{uuid}` to distinguish them from other entity IDs in the system (tasks use `task-`, specs use `spec-`, etc.).

---

## 15. Implementation Sequencing

The recommended implementation order, respecting dependency chains:

1. **`memory_models.py`** -- All enums and Pydantic models (AC-1 through AC-4)
2. **`memory_defaults.py`** -- Configuration constants
3. **`memory_repository.py`** -- SQLite CRUD + FTS5 sync (AC-5 through AC-8)
4. **`memory_forgetting.py`** -- TTL sweep + confidence decay (AC-12, AC-13)
5. **`memory_service.py`** -- Business logic orchestrator (AC-9, AC-10, AC-14)
6. **`memory_consolidation.py`** -- LLM-based consolidation/condensation (AC-9, AC-15)
7. **`memory_injection.py`** -- Prompt injection builder (AC-25)
8. **REST API router** (`routers/memories.py`) + DI wiring (AC-16 through AC-22)
9. **CLI commands** (`memory_cli.py`) (AC-26)
10. **Integration wiring** into persistence harness + memory service dual-write (AC-23, AC-24)
11. **Migration CLI** (`memory migrate`) (AC-27 through AC-29)
12. **Documentation updates** (AC-30, AC-31)
