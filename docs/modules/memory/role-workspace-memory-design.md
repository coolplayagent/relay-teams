# Workspace and Role Memory Design

## Current Model

The runtime keeps workspace execution boundaries separate from durable memory.

- `workspace`: execution directory, mount configuration, and tool path boundary.
- `session`: runtime container bound to one workspace.
- `memory_entries`: the structured Memory Bank used for role, session, and
  workspace memory.

Legacy `role_memories` and `role_daily_memories` tables are no longer runtime
stores. Repository initialization migrates supported `role_memories` rows into
Memory Bank and drops both legacy tables.

## Workspace

`workspace` means execution scope only.

Responsibilities:

- store a stable `workspace_id`
- own one or more execution mounts
- expose a stable runtime boundary to sessions, automation, gateways, and tools
- resolve provider-routed execution roots, readable roots, and writable roots
- enforce path boundaries for tools and shell execution inside the selected
  workspace mount

Non-responsibilities:

- durable memory storage
- memory lifecycle policy
- artifact abstraction

One workspace can be shared by multiple sessions.

## Memory Bank

`memory_entries` is the only durable long-term memory store. It supports:

- tiers: `working`, `medium_term`, `persistent`
- scopes: `workspace`, `session`, `role`
- kinds: `insight`, `constraint`, `decision`, `failure_mode`, `preference`,
  `fact`, `summary`
- statuses: `active`, `superseded`, `expired`
- sources: `consolidation`, `manual`, `condensation`, `task_result`

Scope rules:

- role memory is filtered by `workspace_id + role_id`
- session memory is filtered by `workspace_id + session_id`
- workspace memory is shared by all sessions in the workspace
- session deletion does not delete Memory Bank entries

Task completion writes structured Working-tier task-result entries. Later
consolidation promotes useful entries into Medium-term or Persistent memory.

## Runtime Injection

Prompt assembly pulls Memory Bank entries through `MemoryBankService`; it does
not read workspace state or legacy role-memory services.

Prompt memory sections:

- `## Project Memory`: Medium-term and Persistent Memory Bank entries relevant
  to the role/workspace.
- Compaction summary: short-term session history summary from context
  compaction, not durable project memory.

All agent runtime protocols consume the same prepared prompt. Local, ACP, A2A,
and CLI runtimes receive already-injected memory and already-compacted session
history rather than implementing their own memory path.

## Frontend Surface

The global Memory page under the main feature navigation is the Memory Bank
inspection surface. It supports workspace, tier, scope, status, and text
filters, plus a detail pane for the selected entry.

The subagent drawer memory tab reads role-scoped Memory Bank entries. The old
manual role summary editor is removed.

## Removed Pieces

The following older concepts are removed:

- `workspace.memory`
- `workspace.artifacts`
- file-based daily memory
- database-backed daily memory
- role settings for daily memory
- runtime reads from legacy role-memory services
- manual role summary refresh/update/delete APIs

## Migration Notes

If you are reading older code or earlier design notes, note these incompatible
changes:

- role configs still use `memory_profile.enabled`
- primary long-term memory lives in `memory_entries`
- `role_memories` is migration input only and is dropped at startup
- `role_daily_memories` is dropped at startup
