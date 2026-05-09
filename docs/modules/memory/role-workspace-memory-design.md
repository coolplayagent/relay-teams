# Workspace and Role Memory Design

## 1. Current Model

The runtime uses three separate concepts:

- `workspace`: execution directory and path boundary only
- `session`: runtime container bound to one workspace
- `role memory`: workspace-scoped durable memory stored in the database and keyed by `role_id + workspace_id`

This replaces the older mixed design where workspace, memory, reflection, and file artifacts were modeled together.

## 2. Workspace

`workspace` means execution scope only.

Responsibilities:

- store a stable `workspace_id`
- own one or more execution mounts
- expose a stable runtime boundary to sessions, automation, and tools
- resolve provider-routed execution roots, readable roots, and writable roots
- enforce path boundaries for tools and shell execution inside the selected workspace mount

Non-responsibilities:

- role durable memory
- reflection storage policy
- artifact abstraction

One workspace can be shared by multiple sessions.

Current and future workspace storage may include multiple mounts backed by
different providers, but that does not change the meaning of `workspace_id`
for other domains.

## 3. Session Binding

Each session must bind to an existing workspace.

Rules:

- session creation requires `workspace_id`
- deleting a session does not delete the workspace
- subagents inherit the session workspace scope
- `workspace_id` on runtime records means execution workspace ID, not a single root directory

The default server bootstrap may create a `default` workspace with one local
mount rooted at the current project directory for convenience, but that is an
application bootstrap choice, not the workspace model itself.

## 4. Role Memory

Memory is no longer part of the workspace module.

The current runtime has two durable memory stores during migration:

- `role_memories`
- `memory_entries`

`memory_entries` is the primary long-term Memory Bank. It stores typed entries
across three tiers, Working, Medium-term, and Persistent, and supports the six
memory operations: consolidation, updating, indexing, forgetting, retrieval, and
condensation.

`role_memories` is the legacy reflection-memory compatibility layer. It stores a
single bounded markdown summary for older prompt injection, session projection,
and subagent reflection refresh flows. New long-term memory capabilities should
target Memory Bank instead of expanding `role_memories`.

Scope rules:

- durable memory is keyed by `role_id + workspace_id`
- the same role shares memory across sessions inside the same workspace only
- session deletion does not delete role memory

`memory_profile` is the role-level configuration surface. It now controls whether role memory is enabled at all.

## 5. Subagent Reflection Memory

For subagents, `role_memories.content_markdown` stores a single bounded reflection summary.

Rules:

- reflection memory is strategy memory, not a transcript replacement
- automatic updates happen during subagent context compaction
- manual refresh can call the same strategy through the session API
- each rewrite combines the old summary with newer transcript evidence
- repeated updates must deduplicate, keep stable guidance, and drop stale or one-off details
- future same-role sessions inject only this compact summary

The compaction entry point must depend on a replaceable strategy interface so future compression algorithms can be swapped without changing the execution flow.

## 6. Runtime Injection

Runtime dependencies are split cleanly:

- `workspace`: execution boundary and filesystem access
- `memory_bank`: structured long-term memory access
- `role_memory`: legacy durable reflection memory access

Prompt assembly pulls Memory Bank entries through `MemoryBankService`, legacy
reflection memory through `roles.memory_service`, and shared runtime state from
`shared_state`. It no longer loads durable memory through `workspace`.

All agent runtime protocols consume the same prepared session prompt. Local,
ACP, A2A, and CLI runtimes should receive already-injected memory sections and
already-compacted session history rather than reimplementing memory or context
compression in protocol adapters.

Prompt memory sections have separate meanings:

- `## Project Memory`: Memory Bank Medium-term and Persistent entries.
- `## Reflection Memory`: legacy `role_memories` markdown summary.
- Compaction summary: short-term session history summary from full context
  compaction, not durable project memory.

## 7. Removed Pieces

The following older concepts are removed:

- `workspace.memory`
- `workspace.artifacts`
- file-based daily memory
- database-backed daily memory
- role settings for `daily memory`

Task completion no longer appends per-task daily memory. Subagent long-term memory is maintained through compact-driven reflection summary rewrites.

## 8. Migration Notes

If you are reading older code or earlier design notes, note these incompatible changes:

- role configs must use `memory_profile`; `workspace_profile` is no longer accepted
- `memory_profile` only contains `enabled`
- primary long-term memory lives in `memory_entries`
- legacy reflection memory still lives in `role_memories` until migration is complete
- `role_daily_memories` has been removed
