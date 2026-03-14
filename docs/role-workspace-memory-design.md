# Workspace and Role Memory Design

## 1. Current Model

The runtime now uses three separate concepts:

- `workspace`: execution directory and path boundary only
- `session`: runtime container bound to one workspace
- `role memory`: durable and daily memory stored in the database and keyed by `role_id + workspace_id`

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
- daily memory
- reflection jobs
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

It lives under `src/agent_teams/roles/` and uses two database tables:

- `role_memories`
- `role_daily_memories`

Scope rules:

- durable memory is keyed by `role_id + workspace_id`
- daily memory is keyed by `role_id + workspace_id + memory_date + kind`
- the same role shares memory across sessions inside the same workspace only
- session deletion does not delete role memory

`memory_profile` is now the role-level configuration surface. It controls whether durable and daily memory are enabled for that role.

## 5. Stage Documents

There is no standalone `artifacts` module.

Stage files are managed directly by `src/agent_teams/tools/stage_tools` and stored under the execution workspace:

`{workspace_root}/.agent_teams/sessions/{session_id}/roles/{role_id}/stage/{stage_name}/{timestamp}.md`

Rules:

- each write creates a new timestamped file
- previous versions are preserved
- stage reads choose the latest matching file
- session deletion removes that session subtree from the bound workspace

## 6. Runtime Injection

Runtime dependencies are now split cleanly:

- `workspace`: execution boundary and filesystem access
- `role_memory`: durable and daily memory access

Prompt assembly pulls role memory from `roles.memory_service` and shared runtime state from `shared_state`. It no longer loads durable memory through `workspace`.

## 7. Removed Pieces

The following older concepts are removed:

- `workspace.memory`
- `workspace.artifacts`
- `reflection` module
- reflection APIs and CLI commands
- file-based daily memory

Reflection-like post-task updates are now handled directly in task execution by writing role daily memory and durable memory through the role memory service.

## 8. Migration Notes

If you are reading older code or older discussions, note these incompatible changes:

- role configs must use `memory_profile`; `workspace_profile` is no longer accepted
- role durable memory now lives in `role_memories`
- daily memory now lives in `role_daily_memories`
- both tables are workspace-scoped; older global role-memory rows are not preserved
- stage files now use the direct `stage_tools` layout under workspace/session/role
