# Agent Teams API Design

## Overview

- Base path: `/api`
- Content type: `application/json`
- Streaming endpoint: `text/event-stream`
- Time fields: ISO 8601 UTC strings
- Orchestration model: task-only. There is no workflow graph API, workflow template registry, or persisted dependency DAG.

Common status codes:
- `200`: success
- `400`: invalid task/run request
- `404`: resource not found
- `409`: runtime conflict
- `422`: request validation error

## Core Concepts

- A run starts from one root task.
- Sessions have a run mode:
  - `normal`: one fixed root role (`MainAgent`) handles the run directly.
  - `orchestration`: the root role is `Coordinator`, and delegation is limited by the selected orchestration preset.
- Session mode and orchestration preset can be changed only before the session starts its first run.
- Every delegated task is a persisted task record under that root task.
- A delegated task binds to exactly one delegated role and one subagent instance on first dispatch.
- Re-dispatching the same task reuses its bound instance.
- In one session, delegated tasks with the same bound `role_id` reuse the same session-level subagent instance.
- Same-role task dispatch is serial only. If a role instance is already busy or paused on another task, dispatch returns a runtime conflict.

Task status values:
- `created`
- `assigned`
- `running`
- `stopped`
- `completed`
- `failed`
- `timeout`

## System APIs

### `GET /system/health`

Returns service health.

### `GET /system/configs`

Returns runtime config load status for model, MCP, skills, and effective proxy settings.

### `GET /system/configs/model`

Returns raw `model.json`.

### `GET /system/configs/model/profiles`

Returns normalized model profiles.
Each profile includes `has_api_key`, the currently stored `api_key` value so the web UI can mask it by default and reveal it on demand, `is_default` to mark the runtime fallback profile, and optional `context_window` for next-send context preview UI.
When no profile is explicitly marked default, the backend resolves the default in this order: a profile named `default`, the only configured profile, then the first profile by name.

### `PUT /system/configs/model/profiles/{name}`

Upserts a model profile.
Request body may include optional `source_name` to rename an existing profile while preserving its stored API key when `api_key` is omitted.
Profiles may also include optional `ssl_verify` to override the global outbound TLS verification default for that model only.
Profiles may include `is_default` to promote that profile to the runtime default; saving one default clears the flag from all others.
Profiles may include optional `context_window` to declare the total model context limit separately from `max_tokens`, which remains the output-token cap.

### `DELETE /system/configs/model/profiles/{name}`

Deletes a model profile.
If the deleted profile was the current default and other profiles remain, the backend promotes the first remaining profile by name to stay default.

### `PUT /system/configs/model`

Replaces the full model config object.

### `POST /system/configs/model:probe`

Tests model connectivity for a saved profile and/or draft override.
Draft overrides may include optional `ssl_verify`; effective TLS verification resolves as `override.ssl_verify` -> global `SSL_VERIFY` -> default `true`.
If `timeout_ms` is omitted, the backend uses the resolved profile `connect_timeout_seconds` value, or `15s` when no saved profile is involved.

### `POST /system/configs/model:discover`

Fetches the available model catalog for a saved profile and/or draft override.
Draft overrides may omit `model`, but must provide `base_url` and `api_key` when `profile_name` is omitted.
When `profile_name` is provided, the request may override `base_url`, `api_key`, and `ssl_verify` while reusing the saved credentials for any omitted fields.
If `timeout_ms` is omitted, the backend uses the resolved profile `connect_timeout_seconds` value, or `15s` when no saved profile is involved.
OpenAI-compatible providers map this call to `GET {base_url}/models` and return the normalized `models` list sorted and deduplicated.

### `POST /system/configs/model:reload`

Reloads model config into runtime.

### `GET /system/configs/proxy`

Returns the proxy values currently saved in app `~/.config/agent-teams/.env`.
Fields: `http_proxy`, `https_proxy`, `all_proxy`, `no_proxy`, `proxy_username`, `proxy_password`, `ssl_verify`.
Saved proxy URLs are returned without embedded credentials when the configured proxy URLs share the same username/password pair.
If the password was persisted through the system keyring, the API rehydrates it into `proxy_password` for editing.
If a user manually forces `user:password@host` into `.env`, runtime loading still supports it and the API can read it back, but the save flow will not write that password back to `.env`.

### `PUT /system/configs/proxy`

Saves proxy values into app `~/.config/agent-teams/.env` and reloads runtime proxy state immediately.
Blank values remove the corresponding proxy key.
`proxy_username` and `proxy_password` are optional shared credentials.
`ssl_verify` controls the default TLS certificate verification policy for Agent Teams outbound HTTP clients.
When omitted or `null`, the backend removes `SSL_VERIFY` from `.env` and falls back to strict verification by default.
On save, proxy passwords are persisted only through a usable system keyring backend. The `.env` file stores proxy URLs without the password portion.
If no usable keyring backend is available, saving a proxy password fails with a user-facing error instead of falling back to plaintext file storage.
Runtime loading still supports manual `.env` proxy URLs that already contain embedded passwords.
`no_proxy` accepts both comma-separated and semicolon-separated entries. Wildcard host patterns such as `127.*`, `192.168.*`, and the special token `<local>` are supported.

### `POST /system/configs/proxy:reload`

Reloads effective proxy env into runtime.
This updates process-level proxy variables for future HTTP requests and shell/MCP subprocesses, clears removed proxy keys, and refreshes MCP runtime state.

### `POST /system/configs/mcp:reload`

Reloads MCP config into runtime.

### `POST /system/configs/skills:reload`

Reloads skills config into runtime.

### `GET /system/configs/notifications`

Returns notification rules by event type.

### `PUT /system/configs/notifications`

Replaces notification rules.

### `GET /system/configs/orchestration`

Returns global orchestration settings.

Response fields:
- `default_orchestration_preset_id`
- `presets[]`
  - `preset_id`
  - `name`
  - `description`
  - `role_ids`
  - `orchestration_prompt`

### `PUT /system/configs/orchestration`

Replaces global orchestration settings.

Rules:
- `presets[].role_ids` may contain only normal roles; reserved system roles are rejected.
- The default preset id must match one existing preset.
- `MainAgent` and `Coordinator` base role prompts are edited through `/roles/configs/*`, not this config.
- `orchestration_prompt` is appended only for `Coordinator` in `orchestration` session mode.

### `GET /system/configs/environment-variables`

Returns environment variables grouped by `system` and `app` scope.
`system` is read-only and reflects the effective runtime environment currently visible to the Agent Teams server and newly spawned child processes.
`app` is editable and stored in `~/.config/agent-teams/.env`.
Each record includes `key`, `value`, `scope`, and `value_kind` (`string` or `expandable`).

### `PUT /system/configs/environment-variables/{scope}/{key}`

Upserts one environment variable in the target `scope`.
Request body fields:
- `value`: raw variable value
- optional `source_key`: rename from an existing key before saving the new key

`app` writes preserve unrelated `.env` lines and comments where possible.
The backend preserves the existing value kind on edit or rename when possible, otherwise it infers `expandable` when the value contains `%NAME%` placeholders.
`system` scope is read-only and returns a user-facing validation error on mutation.

### `DELETE /system/configs/environment-variables/{scope}/{key}`

Deletes one app environment variable from the target scope.
Deleting a missing key returns a user-facing validation error.

### `POST /system/configs/web:probe`

Tests whether a target `http` or `https` URL is reachable under the current proxy and global SSL settings.
The request may also include `proxy_override` with `http_proxy`, `https_proxy`, `all_proxy`, `no_proxy`, `proxy_username`, `proxy_password`, and `ssl_verify` to run a one-shot probe against unsaved form values.
The backend uses `HEAD` first and falls back to `GET` when the target does not support `HEAD`.
Any HTTP response (`2xx` through `5xx`) counts as reachable.
Only transport-level failures such as timeout, DNS, TLS, or proxy handshake errors return `ok=false`.

## Session APIs

### `POST /sessions`

Creates a session.

Request:

```json
{
  "session_id": null,
  "workspace_id": "default",
  "metadata": {"project": "demo"}
}
```

Notes:
- New sessions default to `session_mode = "normal"`.
- New sessions also store the current default orchestration preset id so they can be switched to orchestration before the first run.

### `GET /sessions`

Lists sessions.

### `GET /sessions/{session_id}`

Gets one session.

Response fields also include:
- `session_mode`
- `orchestration_preset_id`
- `started_at`
- `can_switch_mode`

### `PATCH /sessions/{session_id}`

Updates session metadata.

### `PATCH /sessions/{session_id}/topology`

Updates session run mode and orchestration preset.

Request:

```json
{
  "session_mode": "orchestration",
  "orchestration_preset_id": "default"
}
```

Rules:
- Only sessions that have not started their first run may be changed.
- `orchestration_preset_id` is required when `session_mode = "orchestration"`.
- `orchestration_preset_id` is ignored when `session_mode = "normal"`.

### `DELETE /sessions/{session_id}`

Deletes a session and all persisted runtime data under that session.
The bound workspace record is preserved.

### `GET /sessions/{session_id}/rounds`

Returns paged round projections.

Response shape:

```json
{
  "items": [
    {
      "run_id": "run-1",
      "created_at": "2026-03-11T12:00:00Z",
      "intent": "Implement endpoint X",
      "coordinator_messages": [],
      "tasks": [
        {
          "task_id": "task-2",
          "title": "Write API code",
          "assigned_role_id": "spec_coder",
          "role_id": "spec_coder",
          "status": "completed",
          "assigned_instance_id": "inst-2",
          "instance_id": "inst-2"
        }
      ],
      "instance_role_map": {"inst-2": "spec_coder"},
      "role_instance_map": {"spec_coder": "inst-2"},
      "task_instance_map": {"task-2": "inst-2"},
      "task_status_map": {"task-2": "completed"},
      "retry_events": [
        {
          "occurred_at": "2026-03-11T12:00:04Z",
          "instance_id": "inst-2",
          "role_id": "spec_coder",
          "attempt_number": 2,
          "total_attempts": 6,
          "retry_in_ms": 2000,
          "phase": "scheduled",
          "is_active": true,
          "error_code": "429",
          "error_message": "Rate limited"
        }
      ],
      "pending_tool_approvals": [],
      "pending_tool_approval_count": 0,
      "run_status": "running",
      "run_phase": "idle",
      "is_recoverable": true
    }
  ],
  "has_more": false,
  "next_cursor": null
}
```

Notes:
- `tasks` contains delegated task summaries only. The root coordinator task is omitted.
- `task_instance_map` is the authoritative mapping when multiple tasks use the same `role_id`.
- `retry_events` reflects the current retry card for the run timeline. The array is empty when no retry state should be shown and contains at most one entry.
- Active retry countdowns are anchored to the event `occurred_at` timestamp, not to the browser receive time.
- `retry_events[].phase` is `scheduled` while backoff is pending and `failed` when retries have been exhausted.

### `GET /sessions/{session_id}/rounds/{run_id}`

Gets one round projection.

### `GET /sessions/{session_id}/recovery`

Returns active run recovery state, pending tool approvals, paused subagent state, and round snapshot.

### `GET /sessions/{session_id}/agents`

Lists one session-level agent instance per delegated role in the session. Each entry also includes a compact reflection preview for the subagent role in the current workspace, plus the latest runtime system prompt snapshot and runtime tools JSON captured before the most recent subagent execution step.

Response fields include:
- `instance_id`
- `role_id`
- `status`
- `created_at`
- `updated_at`
- `reflection_summary_preview`
- `reflection_updated_at`
- `runtime_system_prompt`
- `runtime_tools_json`

### `GET /sessions/{session_id}/events`

Lists persisted business events in the session.

### `GET /sessions/{session_id}/messages`

Lists persisted messages in the session.

### `GET /sessions/{session_id}/agents/{instance_id}/messages`

Lists messages for one agent instance.

### `GET /sessions/{session_id}/agents/{instance_id}/reflection`

Returns the full stored reflection summary for one subagent instance.

Response fields:
- `instance_id`
- `role_id`
- `summary`
- `preview`
- `updated_at`
- `source`

### `POST /sessions/{session_id}/agents/{instance_id}/reflection:refresh`

Triggers reflection recomputation for one subagent instance and returns the refreshed summary. This uses the same compaction/reflection strategy as automatic context compaction.

### `PATCH /sessions/{session_id}/agents/{instance_id}/reflection`

Overwrites the stored reflection summary for that subagent role in the current workspace.

Request:

```json
{
  "summary": "- Prefer concise implementation notes"
}
```

### `DELETE /sessions/{session_id}/agents/{instance_id}/reflection`

Deletes the stored reflection summary for that subagent role in the current workspace. The response returns an empty reflection projection with `updated_at=null`.

### `GET /sessions/{session_id}/tasks`

Lists delegated tasks in the session.

### `GET /sessions/{session_id}/token-usage`

Returns aggregated token usage for the entire session, grouped by `role_id`. The totals include the coordinator agent and every subagent run recorded under the same `session_id`. Response totals expose `total_cached_input_tokens` and `total_reasoning_output_tokens` alongside the existing input/output/request counters. Legacy local rows with missing or `NULL` counters are normalized to `0` before aggregation.

### `GET /sessions/{session_id}/runs/{run_id}/token-usage`

Returns token usage for a single run, grouped by agent instance. Response totals expose `total_cached_input_tokens` and `total_reasoning_output_tokens` alongside the existing input/output/request counters. Legacy local rows with missing or `NULL` counters are normalized to `0` before aggregation.

## Run APIs

### `POST /runs`

Creates a run.

Request:

```json
{
  "intent": "Implement endpoint X",
  "session_id": "session-1",
  "execution_mode": "ai",
  "yolo": false,
  "thinking": {
    "enabled": false,
    "effort": null
  }
}
```

Notes:

- `yolo` is optional.
- `yolo: false` preserves the existing tool approval flow.
- `yolo: true` skips tool approval for all tools in that run, including resumed recoverable runs.
- `thinking` is optional.
- `thinking.enabled` enables model thinking streams for providers that emit thinking parts.
- `thinking.effort` optionally sets provider reasoning effort (`minimal`, `low`, `medium`, `high`); when set, it is forwarded to OpenAI-compatible providers as `openai_reasoning_effort`.
- The backend resolves the session mode at run creation time and snapshots the chosen root topology into the run intent for queued and recoverable resume flows.

Response:

```json
{"run_id": "run-1", "session_id": "session-1"}
```

### `GET /runs/{run_id}/events`

Streams run events via SSE.

Thinking events:
- `thinking_started`: payload includes `part_index`, `role_id`, `instance_id`.
- `thinking_delta`: payload includes `part_index`, `text`, `role_id`, `instance_id`.
- `thinking_finished`: payload includes `part_index`, `role_id`, `instance_id`.

Retry events:
- `llm_retry_scheduled`: payload includes `instance_id`, `role_id`, `attempt_number`, `total_attempts`, `retry_in_ms`, `error_code`, and `error_message`.
- `llm_retry_exhausted`: payload includes `instance_id`, `role_id`, `attempt_number`, `total_attempts`, `error_code`, and `error_message`.

Frontend behavior:
- The web UI uses `llm_retry_scheduled` to render one active retry card in the round timeline and keep its countdown live while the retry backoff window is active.
- Retry countdowns are computed from the SSE event `occurred_at` timestamp plus `retry_in_ms`, so delayed delivery or page refresh does not restart the timer.
- Later retry events replace the same card instead of stacking multiple historical cards.
- Once a retried model attempt produces successful output, the retry card is removed.
- If the run still fails after retries are exhausted, `llm_retry_exhausted` leaves the retry card visible as the final failed retry state.

### `POST /runs/{run_id}/inject`

Injects follow-up content to active agents in a run.

### `GET /runs/{run_id}/tool-approvals`

Lists pending tool approvals.

### `POST /runs/{run_id}/tool-approvals/{tool_call_id}/resolve`

Approves or denies a pending tool call.

Request:

```json
{"action": "approve", "feedback": ""}
```

### `POST /runs/{run_id}/stop`

Stops the full run or a specific subagent.

### `POST /runs/{run_id}:resume`

Resumes a recoverable run.

### `POST /runs/{run_id}/subagents/{instance_id}/inject`

Injects follow-up content to one paused/running subagent.

## Task APIs

### `POST /tasks/runs/{run_id}`

Creates delegated tasks under the run root task.

Request:

```json
{
  "tasks": [
    {
      "title": "Write API code",
      "objective": "Implement the endpoint and tests"
    }
  ]
}
```

Behavior:
- Creates delegated task contracts only.
- Role binding happens later during dispatch.

Response:

```json
{
  "created_count": 1,
  "tasks": [
    {
      "task_id": "task-2",
      "title": "Write API code",
      "objective": "Implement the endpoint and tests",
      "status": "created",
      "assigned_role_id": null,
      "assigned_instance_id": null,
      "role_id": null,
      "instance_id": null,
      "parent_task_id": "task-root"
    }
  ]
}
```

### `GET /tasks/runs/{run_id}`

Lists tasks in a run.

Query:
- `include_root`: `true|false`

### `GET /tasks`

Lists all persisted tasks.

### `GET /tasks/{task_id}`

Gets one task record.

### `PATCH /tasks/{task_id}`

Updates a delegated task definition.

Request:

```json
{
  "title": "Review code",
  "objective": "Review the implementation and report issues"
}
```

Rules:
- Only `created` delegated tasks can be updated.
- `role_id` cannot be updated through task APIs.
- Root coordinator tasks cannot be updated through task APIs.

### `POST /tasks/{task_id}/dispatch`

Dispatches or re-dispatches a delegated task.

Request:

```json
{"role_id": "spec_coder", "prompt": "Address pagination concerns"}
```

Rules:
- `created`: bind the task to the provided `role_id`, create or reuse the session-level subagent instance for that role, then execute.
- `assigned` or `stopped`: reuse the bound instance and continue.
- `completed`: requires non-empty `prompt`, then reuses the same instance.
- `running`: rejected as a conflict.
- `failed` or `timeout`: rejected; create a new task instead.
- After the first dispatch, the delegated role is fixed for that task. To change roles, create a replacement task.
- If another task already holds the same role instance in `assigned`, `running`, or `stopped`, dispatch is rejected as a conflict.

## Role APIs

### `GET /roles`

Lists loaded role definitions.

### `GET /roles:options`

Returns editor options for role settings.

Response fields:
- `coordinator_role_id`
- `main_agent_role_id`
- `tools`
- `mcp_servers`
- `skills`

### `GET /roles/configs`

Lists editable role document summaries for the settings UI.

Response fields:
- `role_id`
- `name`
- `description`
- `version`
- `model_profile`
- `source`

### `GET /roles/configs/{role_id}`

Returns one editable role document.

Response fields:
- `source_role_id`
- `role_id`
- `name`
- `description`
- `version`
- `tools`
- `mcp_servers`
- `skills`
- `model_profile`
- `memory_profile`
- `source`
- `system_prompt`
- `file_name`
- `content`

### `PUT /roles/configs/{role_id}`

Validates and saves one role document, then reloads the runtime role registry.

Request:

```json
{
  "source_role_id": "spec_coder",
  "role_id": "spec_coder",
  "name": "Spec Coder",
  "description": "Implements requested changes.",
  "version": "1.0.0",
  "tools": ["read_file", "write_file"],
  "mcp_servers": [],
  "skills": [],
  "model_profile": "default",
  "memory_profile": {
    "enabled": true
  },
  "system_prompt": "Implement the requested change."
}
```

Rules:
- Path `role_id` must match body `role_id`.
- Unknown tools, MCP servers, or skills are rejected.
- When `source_role_id` is omitted and the file does not exist yet, a new role file is created.
- Renaming a role writes a new file and removes the previous file when validation succeeds.
- Reserved system roles keep fixed identity fields (`role_id`, `name`, `description`, `version`) and fixed `system_prompt` through this API.

### `POST /roles:validate`

Validates role files against registered tools and skills.

Constraint:
- `depends_on` is invalid in role front matter. Ordering is runtime task orchestration state, not role metadata.

### `POST /roles:validate-config`

Validates one in-memory role draft without saving it.

Use cases:
- settings UI inline validation
- pre-save editor checks for tools, MCP servers, skills, and role schema

## Workspace APIs

### `GET /workspaces`

Lists registered execution workspaces.

### `POST /workspaces`

Creates one execution workspace.

Request:

```json
{
  "workspace_id": "default",
  "root_path": "D:/workspace/agent_teams"
}
```

Rules:
- `root_path` must already exist.
- `root_path` must be a directory.
- `workspace_id` must be unique.

### `GET /workspaces/{workspace_id}`

Returns one registered execution workspace.

### `POST /workspaces/{workspace_id}:fork`

Creates a forked execution workspace backed by a Git worktree.

Request:

```json
{
  "name": "alpha-project-fork"
}
```

Rules:
- Source workspace must exist and its `root_path` must be inside a Git repository.
- The backend normalizes `name` into the new `workspace_id`.
- The fork creates branch `fork/{workspace_id}` from the source workspace current `HEAD`.
- The worktree directory is created under the managed workspace storage directory and becomes the new workspace `root_path`.
- The returned workspace profile uses `file_scope.backend = "git_worktree"` and includes `source_root_path`, `branch_name`, and `forked_from_workspace_id`.

### `POST /workspaces/pick`

Opens a native directory picker on the local machine, then registers the chosen
directory as a workspace. If the selected directory is already registered, the
existing workspace record is returned.

Request:

```json
{
  "root_path": "D:/workspace/agent_teams"
}
```

If `root_path` is provided, the server skips the native picker and registers the
specified directory directly.

Response:

```json
{
  "workspace": {
    "workspace_id": "agent-teams",
    "root_path": "D:/workspace/agent_teams",
    "profile": {
      "backend": "filesystem",
      "file_scope": {
        "backend": "project",
        "working_directory": ".",
        "readable_paths": ["."],
        "writable_paths": ["."],
        "branch_binding": "shared",
        "branch_name": null,
        "source_root_path": null,
        "forked_from_workspace_id": null
      }
    },
    "created_at": "2026-03-14T12:00:00Z",
    "updated_at": "2026-03-14T12:00:00Z"
  }
}
```

Rules:
- Returns `{ "workspace": null }` when the picker is cancelled.
- `root_path`, when provided, must already exist and be a directory.
- Linux native picking requires an installed desktop picker such as `zenity`,
  `qarma`, `yad`, or `kdialog`.
- Returns `503` when the runtime cannot open a native directory picker.

### `DELETE /workspaces/{workspace_id}`

Deletes one registered execution workspace.

Query:
- `remove_worktree`: `true|false`

Rules:
- `remove_worktree=true` only affects workspaces with `file_scope.backend = "git_worktree"`.
- When `remove_worktree=true`, the backend runs `git worktree remove --force` before deleting the workspace record.
- When `remove_worktree=false`, the backend deletes only the workspace record.

## Prompt APIs

### `POST /prompts:preview`

Builds prompt preview payload for a specific role. Coordinator role IDs are resolved from the loaded role files and are not hardcoded to `coordinator_agent`.

Request:

```json
{
  "role_id": "Coordinator",
  "workspace_id": "default",
  "objective": "Draft release note",
  "shared_state": {"lang": "zh-CN", "priority": 1},
  "tools": ["dispatch_task"],
  "skills": ["time"]
}
```

Notes:
- `objective` is optional.
- `workspace_id` is optional.
- When `workspace_id` is provided, `runtime_system_prompt` resolves `Working Directory` from the workspace execution root using the same workspace path resolution as real agent execution.
- When `workspace_id` does not exist, the endpoint returns `404`.
- When `objective` is omitted or blank, the preview response returns `objective: ""` and `user_prompt: ""`.

Response:

```json
{
  "role_id": "Coordinator",
  "objective": "Draft release note",
  "tools": ["dispatch_task"],
  "skills": ["time"],
  "runtime_system_prompt": "...",
  "provider_system_prompt": "...",
  "user_prompt": "..."
}
```

## MCP APIs

### `GET /mcp/servers`

Lists effective MCP servers from app scope.

### `GET /mcp/servers/{server_name}/tools`

Lists tools exposed by one MCP server.

## Trigger APIs

### `POST /triggers`

Creates a trigger definition.

### `GET /triggers`

Lists trigger definitions.

### `GET /triggers/{trigger_id}`

Gets one trigger definition.

### `PATCH /triggers/{trigger_id}`

Updates trigger mutable fields.

### `POST /triggers/{trigger_id}:enable`

Enables a trigger.

### `POST /triggers/{trigger_id}:disable`

Disables a trigger.

### `POST /triggers/{trigger_id}:rotate-token`

Rotates the public webhook token.

### `POST /triggers/ingest`

Internal generic trigger ingest endpoint.

### `POST /triggers/webhooks/{public_token}`

Public webhook ingest endpoint.

### `GET /triggers/{trigger_id}/events`

Lists persisted trigger events.

### `GET /triggers/events/{event_id}`

Gets one persisted trigger event.

## Memory Notes

- `workspace` now means execution workspace only.
- Durable role memory is stored in the database and keyed by `role_id + workspace_id`.
- Daily role memory is stored in the database and keyed by `role_id + workspace_id + memory_date + kind`.
