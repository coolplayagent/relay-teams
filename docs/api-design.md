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

- A run starts from one root coordinator task.
- Every delegated task is a persisted task record under that root task.
- A delegated task binds to exactly one subagent instance on first dispatch.
- Re-dispatching the same task reuses its bound instance.
- In one session, delegated tasks with the same `role_id` reuse the same session-level subagent instance.
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

Returns runtime config load status for model, MCP, and skills.

### `GET /system/configs/model`

Returns raw `model.json`.

### `GET /system/configs/model/profiles`

Returns normalized model profiles.

### `PUT /system/configs/model/profiles/{name}`

Upserts a model profile.
Request body may include optional `source_name` to rename an existing profile while preserving its stored API key when `api_key` is omitted.

### `DELETE /system/configs/model/profiles/{name}`

Deletes a model profile.

### `PUT /system/configs/model`

Replaces the full model config object.

### `POST /system/configs/model:probe`

Tests model connectivity for a saved profile and/or draft override.
If `timeout_ms` is omitted, the backend uses the resolved profile `connect_timeout_seconds` value, or `15s` when no saved profile is involved.

### `POST /system/configs/model:reload`

Reloads model config into runtime.

### `POST /system/configs/mcp:reload`

Reloads MCP config into runtime.

### `POST /system/configs/skills:reload`

Reloads skills config into runtime.

### `GET /system/configs/notifications`

Returns notification rules by event type.

### `PUT /system/configs/notifications`

Replaces notification rules.

## Session APIs

### `POST /sessions`

Creates a session.

Request:

```json
{"session_id": null, "metadata": {"project": "demo"}}
```

### `GET /sessions`

Lists sessions.

### `GET /sessions/{session_id}`

Gets one session.

### `PATCH /sessions/{session_id}`

Updates session metadata.

### `DELETE /sessions/{session_id}`

Deletes a session and all persisted runtime data.

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
          "role_id": "spec_coder",
          "status": "completed",
          "instance_id": "inst-2"
        }
      ],
      "instance_role_map": {"inst-2": "spec_coder"},
      "role_instance_map": {"spec_coder": "inst-2"},
      "task_instance_map": {"task-2": "inst-2"},
      "task_status_map": {"task-2": "completed"},
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

### `GET /sessions/{session_id}/rounds/{run_id}`

Gets one round projection.

### `GET /sessions/{session_id}/recovery`

Returns active run recovery state, pending tool approvals, paused subagent state, and round snapshot.

### `GET /sessions/{session_id}/agents`

Lists one session-level agent instance per delegated role in the session.

### `GET /sessions/{session_id}/events`

Lists persisted business events in the session.

### `GET /sessions/{session_id}/messages`

Lists persisted messages in the session.

### `GET /sessions/{session_id}/agents/{instance_id}/messages`

Lists messages for one agent instance.

### `GET /sessions/{session_id}/tasks`

Lists delegated tasks in the session.

### `GET /sessions/{session_id}/token-usage`

Returns aggregated token usage for the session, grouped by `role_id`. Legacy local rows with missing or `NULL` counters are normalized to `0` before aggregation.

### `GET /sessions/{session_id}/runs/{run_id}/token-usage`

Returns token usage for a single run, grouped by agent instance. Legacy local rows with missing or `NULL` counters are normalized to `0` before aggregation.

## Run APIs

### `POST /runs`

Creates a run.

Request:

```json
{
  "intent": "Implement endpoint X",
  "session_id": "session-1",
  "execution_mode": "ai"
}
```

Response:

```json
{"run_id": "run-1", "session_id": "session-1"}
```

### `GET /runs/{run_id}/events`

Streams run events via SSE.

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
      "role_id": "spec_coder",
      "title": "Write API code",
      "objective": "Implement the endpoint and tests"
    }
  ],
  "auto_dispatch": false
}
```

Behavior:
- `auto_dispatch=false`: create tasks only.
- `auto_dispatch=true`: only valid when `tasks` contains exactly one item; creates the task and dispatches it immediately.

Response:

```json
{
  "ok": true,
  "created_count": 1,
  "tasks": [
    {
      "task_id": "task-2",
      "title": "Write API code",
      "role_id": "spec_coder",
      "objective": "Implement the endpoint and tests",
      "status": "created",
      "instance_id": "",
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
  "role_id": "reviewer",
  "title": "Review code",
  "objective": "Review the implementation and report issues"
}
```

Rules:
- Only `created` delegated tasks can be updated.
- Root coordinator tasks cannot be updated through task APIs.

### `POST /tasks/{task_id}/dispatch`

Dispatches or re-dispatches a delegated task.

Request:

```json
{"feedback": "Address pagination concerns"}
```

Rules:
- `created`: bind the task to the session-level subagent instance for its `role_id` (creating it if needed), then execute.
- `assigned` or `stopped`: reuse the bound instance and continue.
- `completed`: requires non-empty `feedback`, then reuses the same instance.
- `running`: rejected as a conflict.
- `failed` or `timeout`: rejected; create a new task instead.
- If another task already holds the same role instance in `assigned`, `running`, or `stopped`, dispatch is rejected as a conflict.

## Role APIs

### `GET /roles`

Lists loaded role definitions.

### `GET /roles:options`

Returns editor options for role settings.

Response fields:
- `tools`
- `mcp_servers`
- `skills`
- `workspace_bindings`

### `GET /roles/configs`

Lists editable role document summaries for the settings UI.

Response fields:
- `role_id`
- `name`
- `version`
- `model_profile`

### `GET /roles/configs/{role_id}`

Returns one editable role document.

Response fields:
- `source_role_id`
- `role_id`
- `name`
- `version`
- `tools`
- `mcp_servers`
- `skills`
- `model_profile`
- `workspace_profile`
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
  "version": "1.0.0",
  "tools": ["read_file", "write_file"],
  "mcp_servers": [],
  "skills": [],
  "model_profile": "default",
  "workspace_profile": {"binding": "session"},
  "system_prompt": "Implement the requested change."
}
```

Rules:
- Path `role_id` must match body `role_id`.
- Unknown tools, MCP servers, or skills are rejected.
- When `source_role_id` is omitted and the file does not exist yet, a new role file is created.
- Renaming a role writes a new file and removes the previous file when validation succeeds.

### `POST /roles:validate`

Validates role files against registered tools and skills.

Constraint:
- `depends_on` is invalid in role front matter. Ordering is runtime task orchestration state, not role metadata.

### `POST /roles:validate-config`

Validates one in-memory role draft without saving it.

Use cases:
- settings UI inline validation
- pre-save editor checks for tools, MCP servers, skills, and role schema

## Prompt APIs

### `POST /prompts:preview`

Builds prompt preview payload for a specific role. Coordinator role IDs are resolved from the loaded role files and are not hardcoded to `coordinator_agent`.

Request:

```json
{
  "role_id": "Coordinator",
  "objective": "Draft release note",
  "shared_state": {"lang": "zh-CN", "priority": 1},
  "tools": ["dispatch_task"],
  "skills": ["time"]
}
```

Response:

```json
{
  "role_id": "Coordinator",
  "objective": "Draft release note",
  "tools": ["dispatch_task"],
  "skills": ["time"],
  "runtime_system_prompt": "...",
  "provider_system_prompt": "...",
  "user_prompt": "...",
  "tool_prompt": "...",
  "skill_prompt": "..."
}
```

## MCP APIs

### `GET /mcp/servers`

Lists effective MCP servers after config merge.

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

## Reflection APIs

### `GET /reflection/jobs`

Lists reflection jobs.

### `POST /reflection/jobs/{job_id}/retry`

Retries a failed or queued reflection job.

### `GET /reflection/memory/session-roles/{session_id}/{role_id}`

Reads role-level long-term memory content.

### `GET /reflection/memory/instances/{instance_id}/daily/{date}`

Reads one instance daily memory file.
