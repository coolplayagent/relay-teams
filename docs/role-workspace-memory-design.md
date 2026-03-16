# Workspace and Role Memory Design

## 1. Current Model

The runtime uses three separate concepts:

- `workspace`: execution directory and path boundary only
- `session`: runtime container bound to one workspace
- `role memory`: workspace-scoped durable memory stored in the database and keyed by `role_id + workspace_id`

This replaces the older mixed design where workspace, memory, reflection, and file artifacts were modeled together.

## 2. Workspace

`workspace` means execution workspace only.

Responsibilities:

- store a stable `workspace_id`
- point to a concrete `root_path`
- resolve execution root, readable roots, writable roots
- enforce path boundaries for tools and shell execution
- provide the filesystem base used by stage tools

Non-responsibilities:

- role durable memory
- reflection storage policy
- artifact abstraction

One workspace can be shared by multiple sessions.

## 3. Session Binding

Each session must bind to an existing workspace.

Rules:

- session creation requires `workspace_id`
- deleting a session does not delete the workspace
- subagents inherit the session workspace
- `workspace_id` on runtime records means execution workspace ID

The default server bootstrap may create a `default` workspace rooted at the current project directory for convenience, but that is an application bootstrap choice, not the workspace model itself.

## 4. Role Memory

Memory is no longer part of the workspace module.

It lives under `src/agent_teams/roles/` and uses one database table:

- `role_memories`

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

## 6. Stage Documents

There is no standalone `artifacts` module.

Stage files are managed directly by `src/agent_teams/tools/stage_tools` and stored under the execution workspace:

`{workspace_root}/.agent_teams/sessions/{session_id}/roles/{role_id}/stage/{stage_name}/{timestamp}.md`

Rules:

- each write creates a new timestamped file
- previous versions are preserved
- stage reads choose the latest matching file
- session deletion removes that session subtree from the bound workspace

## 7. Runtime Injection

Runtime dependencies are split cleanly:

- `workspace`: execution boundary and filesystem access
- `role_memory`: durable reflection memory access

Prompt assembly pulls role memory from `roles.memory_service` and shared runtime state from `shared_state`. It no longer loads durable memory through `workspace`.

## 8. Removed Pieces

The following older concepts are removed:

- `workspace.memory`
- `workspace.artifacts`
- file-based daily memory
- database-backed daily memory
- role settings for `daily memory`

Task completion no longer appends per-task daily memory. Subagent long-term memory is maintained through compact-driven reflection summary rewrites.

## 9. Migration Notes

If you are reading older code or earlier design notes, note these incompatible changes:

- role configs must use `memory_profile`; `workspace_profile` is no longer accepted
- `memory_profile` only contains `enabled`
- role durable memory lives in `role_memories`
- `role_daily_memories` has been removed
- stage files use the direct `stage_tools` layout under workspace/session/role
