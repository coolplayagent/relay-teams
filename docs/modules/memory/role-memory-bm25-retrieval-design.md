# Memory Bank BM25 Retrieval Design

## Purpose

Memory retrieval ranks structured Memory Bank entries for prompt injection and
for user-facing search. It uses the existing retrieval module and
`RetrievalScopeKind.MEMORY` when a retrieval service is configured, with a
SQLite fallback when it is not.

Legacy role-memory tables are not retrieval sources. Supported legacy rows are
first migrated into `memory_entries`, then retrieval operates on Memory Bank
entries only.

## Source Entries

Retrieval documents are derived from active `memory_entries` rows.

Document fields:

- `document_id`: `memory_id`
- `scope_kind`: `memory`
- `scope_id`: `workspace_id`
- `title`: `content.title`
- `body`: `content.body`, `content.context`, and `content.outcome`
- `keywords`: tags plus tier/scope/kind/source tokens

Only `status=active` entries are indexed. Expired or superseded entries remain
available for direct audit reads but should not influence prompt retrieval.

## Query Semantics

Workspace-scoped search uses:

- `POST /api/workspaces/{workspace_id}/memories/search`
- `MemorySearchRequest`
- retrieval scope id equal to the workspace id

Global search uses:

- `POST /api/memories/search`
- `GlobalMemorySearchRequest`
- optional `workspace_id`

When `workspace_id` is supplied, global search delegates to workspace search.
When it is omitted, the service queries Memory Bank summaries across workspaces
and applies text filtering in the service fallback path. Search defaults to
`status=active`; non-active status filters bypass FTS because only active rows
are indexed.

Filters:

- `tier`
- `scope`
- `session_id`
- `role_id`
- `kind`
- `status`
- `tags`
- `min_confidence`
- `limit`

## Prompt Injection

Prompt injection reads Memory Bank through `MemoryBankService` and formats a
bounded `## Project Memory` section. The query favors active Medium-term and
Persistent entries scoped to the current workspace and role.

The injection path should remain deterministic:

- no interface layer repository access
- no provider-specific memory query
- no protocol adapter memory implementation
- no fallback to legacy role-memory tables

## Index Lifecycle

Indexing occurs after create/update when a retrieval service is configured.
Server startup also reindexes active Memory Bank entries so rows imported from
legacy `role_memories` become searchable even though migration writes them
before the retrieval service is available. Delete removes the Memory Bank row;
retrieval stores may rebuild from `memory_entries` if needed.

For fallback deployments without retrieval service configuration, search scans
Memory Bank summaries with the same filters and returns ranked matches with
snippets.

## Testing

Coverage should verify:

- Memory Bank entries can be searched by workspace and globally
- retrieval-service failures fall back cleanly
- non-active entries are ignored by search
- role prompt injection uses Memory Bank entries
- legacy migration creates searchable Memory Bank rows
