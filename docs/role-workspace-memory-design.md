# Role Workspace and Memory Design

## 1. Problem Statement

Current runtime continuity is primarily bound to `instance_id`, not to a stable role-level working context.

This causes two inconsistent behaviors:
- Re-entering the same instance usually preserves continuity because message history is loaded by `instance_id`.
- Creating a new instance for the same role usually loses continuity because the new instance has no prior history.

At the same time, the current implementation splits "workspace" into multiple unrelated concepts:
- File operations use a plain `workspace_root: Path`.
- Shared state uses central SQLite storage with `GLOBAL/SESSION/TASK/INSTANCE` scopes.
- Message history uses central SQLite storage keyed by `session_id/instance_id/task_id`.
- Stage documents are stored as files under `.agent_teams/stage_docs/<run_id>`.

The result is that file workspace, memory, history, and artifacts are not modeled as one coherent runtime object.

---

## 2. Design Goals

The workspace design should satisfy the following goals:
- Role is an abstraction and declares workspace requirements rather than concrete paths.
- Agent instance is an execution carrier and binds to an already-resolved workspace handle.
- New instance creation must inherit role memory and recent conversation continuity.
- Instance re-entry must preserve exact execution continuity whenever possible.
- Workspace backend must support filesystem, SQLite, or hybrid implementations without changing upper-layer orchestration logic.
- File artifacts, structured memory, and message history should be accessed through one unified runtime abstraction.

---

## 3. Core Design Decision

Continuity must not be owned by `instance`.

Normative ownership model:
- Memory does not belong to an `Agent` instance; it belongs to a role scope.
- `Role` defines the state space, while an `Agent` instance is only an execution process running inside that state space.
- To guarantee continuity, recoverability, and auditability, role memory is split into three stability layers:
  - `workspace + role`: long-term role memory for durable knowledge, responsibility boundaries, and cross-thread continuity.
  - `workspace + role + conversation`: thread memory for phase context inside one collaboration thread.
  - `workspace + role + conversation + task`: task-temporary memory for in-task working memory and transient state.
- Instance lifecycle (creation, teardown, migration, re-entry) must restore continuity by reloading role-scoped state.
- Role scope includes both cognitive-memory boundary and execution boundary. The execution boundary must at least define working directory, readable/writable paths, and branch binding; Git worktree is the preferred engineering carrier for a role-scoped workspace.

Continuity should instead be owned by:
- `workspace`
- `role thread`
- `conversation thread`

Role not only defines responsibility, it also defines the state space an agent may read, modify, and persist.

Role also defines the execution boundary.

This means:
- role defines not only cognitive boundaries, but also execution boundaries
- worktree is the ideal carrier for the role execution boundary
- the role state space must include filesystem scope definition
- filesystem scope must at least define working directory, readable paths, writable paths, and branch binding
- an implementation may further realize that scope as an independent Git worktree so execution isolation, recovery, auditability, and integration stay stable

This means:
- role defines the recoverable state contract
- agent instance is only the runtime carrier of that contract
- startup, migration, and re-entry restore continuity by loading role-scoped state
- system continuity must rely on recoverable role-scoped state, not on one transient dialogue context

An instance may be replaced, but the thread identity must remain stable.

This means:
- `instance` is for execution lifecycle.
- `conversation thread` is for short- to medium-term working context.
- `role workspace memory` is for longer-lived reusable knowledge inside one workspace.

---

## 4. Conceptual Model

### 4.1 `WorkspaceProfile`

Role declares what kind of workspace it needs.

Suggested fields:
- `binding`: `session | role | instance | task`
- `backend`: `filesystem | sqlite | hybrid`
- `capabilities`: `files | shell | history | memory | artifacts`
- `readable_scopes`: `workspace | session | role | conversation | task | instance`
- `writable_scopes`: `workspace | session | role | conversation | task | instance`
- `persistent_scopes`: `workspace | session | role | conversation | task | instance`
- `file_scope.backend`: `project | git_worktree`
- `file_scope.working_directory`
- `file_scope.readable_paths`
- `file_scope.writable_paths`
- `file_scope.branch_binding`: `shared | session | role | instance`
- `file_scope.branch_name | null`

Notes:
- Role should not directly declare a concrete filesystem path.
- Different roles may require different capabilities.
- Role state space is the contract that determines what can be recovered on startup, migration, or re-entry.
- Role file scope is the contract that determines where execution may happen and what filesystem surface is visible or mutable.
- `git_worktree` should be treated as the preferred role-scoped execution backend when engineering isolation matters.

Examples:
- `spec_coder`: `hybrid`, with `files`, `shell`, `history`, `memory`, `artifacts`
- `spec_spec`: `hybrid`, with `history`, `memory`, `artifacts`
- `time`: `sqlite` or lightweight non-filesystem workspace

### 4.2 `WorkspaceRef`

Runtime-resolved identity of one workspace binding.

Suggested fields:
- `workspace_id`
- `profile_name`
- `session_id`
- `role_id`
- `instance_id | null`

### 4.3 `WorkspaceHandle`

The only workspace object visible to tools and runtime.

Suggested responsibilities:
- `files`
- `memory`
- `history`
- `artifacts`
- `scratchpad`
- `execution_root`
- `readable_roots`
- `writable_roots`
- `branch_binding`
- `worktree_root | null`

Upper layers should depend on `WorkspaceHandle`, not on raw `Path`, `SharedStore`, or `MessageRepository`.

---

## 5. Memory Layers

To support both new-instance continuity and re-entry continuity, memory should be split into three layers.

### 5.1 `RoleWorkspaceMemory`

Scope:
- stable knowledge for one role inside one workspace

Typical contents:
- reusable constraints
- terminology
- known project conventions
- preferred paths
- prior accepted design decisions

Usage:
- loaded when a new instance of the same role is created in the same workspace
- updated when important facts become stable enough to persist
- treated as primary continuity source when a new agent instance replaces an older one

### 5.2 `ConversationThread`

Scope:
- one continuous working thread for a role in a workspace

Typical contents:
- message history
- safe-boundary transcript
- summarized recent context
- tool interaction continuity

Usage:
- primary continuity mechanism for re-entry
- reusable by a replacement instance if the original instance is gone

### 5.3 `TaskScratchpad`

Scope:
- temporary state for the current task or current execution segment

Typical contents:
- local plan
- uncommitted intermediate results
- transient tool state

Usage:
- may be discarded after task completion
- should not be treated as durable role memory

---

## 6. Recommended Workspace Topology

The default topology should not be "one physical directory per role instance".

The recommended default is:

### 6.1 `SessionWorkspace`

Shared project-level execution space for one session.

Responsibilities:
- project file access
- shared artifacts
- common session-level context
- default shared execution root when no isolated worktree is requested

### 6.2 `RoleThread`

Stable logical context keyed by:
- `session_id`
- `role_id`

Responsibilities:
- role-level reusable memory
- continuity across multiple instances of the same role in one session
- stable execution boundary definition for the role
- branch or worktree binding when execution isolation is enabled

### 6.3 `ConversationThread`

Sub-context under one `RoleThread`, typically keyed by task lineage or active work stream.

Responsibilities:
- exact working continuity
- resumable history
- re-entry and handoff support

Benefits:
- roles in the same session can share project context without mixing private memory.
- multiple instances of the same role can continue the same thread.
- different sessions remain isolated.
- file operations can be isolated without relying on transient prompt instructions alone.

---

## 7. Handling the Two Critical Scenarios

### 7.1 Creating a New Agent Instance

Goal:
- preserve continuity even when a brand new instance is created

Required behavior:
- do not start from a blank context
- attach the new instance to an existing `RoleThread`
- load the latest relevant `ConversationThread`
- load durable `RoleWorkspaceMemory`
- load current task artifacts and stage outputs
- restore all recoverable role-scoped state allowed by the role's declared state space
- restore the declared filesystem execution boundary before running tools
- if the role uses isolated execution, attach or recreate the bound worktree before execution resumes

Interpretation:
- the new instance is new only as an execution carrier
- it is not a new logical role context
- continuity is recovered from role state, not reconstructed only from recent dialogue

### 7.2 Re-entering an Agent Instance

Goal:
- preserve precise continuity for the current work

Required behavior:
- continue the existing `ConversationThread`
- restore safe-boundary message history
- restore resumable scratchpad or checkpoint state when available
- if the original instance is unavailable, allow a replacement instance to continue the same thread
- restore writable and persistent role-scoped state before resuming execution
- restore the same file scope contract, including workdir, readable roots, writable roots, and branch binding

Interpretation:
- re-entry is thread continuity first, instance continuity second

---

## 8. Current Code Issues That Drive This Change

### 8.1 Workspace is not a first-class runtime object

Current state:
- tools receive only `workspace_root: Path`
- provider factory hardcodes `Path.cwd()`

Impact:
- workspace is treated as a working directory, not as a runtime domain object
- execution boundary is implicit and cannot be audited or restored precisely

### 8.2 Message continuity is keyed by `instance_id`

Current state:
- provider loads history using `message_repo.get_history(request.instance_id)`

Impact:
- continuity is preserved only when the same instance is reused
- new instances lose history by default

### 8.3 Shared state lacks workspace and role dimensions

Current state:
- scopes are only `global`, `session`, `task`, `instance`

Impact:
- no direct place for role-level durable memory inside a workspace

### 8.4 Stage artifacts are file conventions, not workspace capabilities

Current state:
- stage docs are stored in a fixed filesystem convention

Impact:
- artifacts are not abstracted consistently with memory and history

### 8.5 File execution scope is not modeled explicitly

Current state:
- file tools operate on a generic workspace root
- shell execution has no explicit role-scoped workdir contract
- no branch binding or worktree binding is attached to role runtime state

Impact:
- concurrent agents can interfere with each other during execution
- isolation depends on convention instead of domain contracts
- restart and audit cannot reconstruct the exact execution boundary

---

## 9. Recommended Data Model Changes

### 9.1 Role definition

Add:
- `workspace_profile`

Do not add:
- raw `workspace_root`

Reason:
- role should declare requirements, not runtime-resolved concrete locations
- role should declare recoverable state scope, not rely on ad hoc context carry-over

### 9.2 Runtime records

Add to agent/session/runtime records as needed:
- `workspace_id`
- `conversation_id`

Reason:
- execution record should point to its logical continuity context

### 9.3 Message storage

Add:
- `conversation_id`
- `agent_role_id`

Primary retrieval should move from:
- `instance_id`

To:
- `conversation_id`

Reason:
- continuity belongs to the thread, not to the instance

### 9.4 Shared state

Either:
- extend scopes with `workspace`, `role`, `conversation`

Or:
- hide raw scope handling behind a dedicated workspace memory API

The second option is cleaner for long-term evolution.

The implementation should still preserve the same principle:
- recoverable role-scoped state must remain explicit
- tools and runtime should not smuggle continuity through implicit in-memory dialogue state only

---

## 10. Recommended Runtime API Shape

### 10.1 Tool dependency injection

Replace:
- raw `workspace_root`

With:
- `workspace: WorkspaceHandle`

### 10.2 Tool access pattern

Tools should read capabilities from the workspace handle:
- file tools use `workspace.files`
- memory tools use `workspace.memory`
- stage tools use `workspace.artifacts`
- conversation-aware operations use `workspace.history`
- shell and file tools resolve paths through `workspace` file-scope rules instead of arbitrary project-root access

This avoids leaking storage backend details into tools.

### 10.3 File-scope runtime behavior

Workspace handle should expose:
- execution root
- readable roots
- writable roots
- branch name
- optional worktree root

Runtime and tools should enforce:
- reads stay inside readable roots
- writes stay inside writable roots
- shell cwd stays inside the declared execution root
- when `git_worktree` is selected, the worktree path remains part of recoverable workspace identity

---

## 11. Recommended Default Backend Strategy

Use a hybrid workspace by default.

### 11.1 Filesystem stores

Best for:
- source code
- stage documents
- exported artifacts
- shell working directory
- role-scoped worktree execution boundary

### 11.2 SQLite stores

Best for:
- structured memory
- message history
- checkpoints
- thread metadata

### 11.3 Why hybrid is preferred

Pure filesystem is weak for structured memory and resumability.

Pure SQLite is weak for:
- code editing workflows
- shell commands
- artifact interoperability

Hybrid keeps upper layers stable while letting each storage concern use the appropriate backend.

It also lets the system separate:
- recoverable structured continuity in SQLite
- executable code and branch-scoped changes in filesystem or Git worktrees

---

## 12. Migration Strategy

Recommended order:

1. Introduce workspace domain models and manager without changing behavior.
2. Add `workspace_profile` to roles.
3. Add `workspace_id` and `conversation_id` to runtime records.
4. Add `conversation_id` to message persistence and start dual-writing.
5. Change provider history lookup from `instance_id` to `conversation_id`.
6. Replace `workspace_root` injection with `WorkspaceHandle`.
7. Move stage docs and memory access behind workspace capabilities.

This sequence minimizes disruption and allows gradual migration.

---

## 13. Current Implementation Status

The current codebase has already implemented these concrete pieces:
- subagent workspace binding is instance-scoped, so each subagent run resolves its own `workspace_id` and `conversation_id`
- shared-state memory isolation for subagents now uses instance-specific session/role scopes
- message history continuity already uses `conversation_id`
- reflection is implemented as an independent module under `src/agent_teams/reflection/`
- successful non-coordinator subagent completion now enqueues asynchronous reflection jobs
- daily memory is stored per instance workspace under `memory/daily/raw` and `memory/daily/digest`
- long-term memory is stored per `session + role` under `.agent_teams/memory/session_roles/{session_id}/{role_id}/MEMORY.md`
- next subagent entry injects long-term memory plus today's digest into the system prompt
- session deletion now also removes reflection jobs and role-level long-term memory files

This means the repository has moved from the earlier conceptual target into a partially realized hybrid design:
- execution isolation is primarily instance-scoped
- short-term file memory is instance-scoped
- stable reusable memory is session-role scoped

The detailed reflection behavior and public contracts are documented in:
- `docs/reflection/subagent-reflection-memory-design.md`
- `docs/reflection/reflection-api-design.md`
- `docs/reflection/reflection-database-schema.md`

---

## 14. Summary

The key architectural rule is:

- `role` declares workspace requirements
- `role` declares readable, writable, and persistent state scope
- `workspace` owns memory and artifacts
- `conversation thread` owns continuity
- `instance` owns execution only

If continuity must survive both new instance creation and instance re-entry, it cannot remain attached to `instance_id`.

It must be attached to a stable logical thread inside a workspace.
