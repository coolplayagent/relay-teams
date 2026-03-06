# Agent Teams API Design

## 1. General

- Base path: `/api`
- Content type: `application/json`
- Streaming endpoint uses `text/event-stream`
- Time fields use ISO 8601 UTC strings

Common status codes:
- `200`: success
- `404`: resource not found / run-task mismatch
- `409`: runtime conflict (for example session blocked by paused subagent)
- `422`: request validation error
- `500`: internal error

---

## 2. Core Enums

### execution_mode
- `ai`
- `manual`

### workflow_type
- `spec_flow`
- `custom`

### workflow dispatch action
- `next`
- `revise`

### injection source
- `system`
- `user`
- `subagent`

### run event_type
- `run_started`
- `model_step_started`
- `model_step_finished`
- `text_delta`
- `tool_call`
- `tool_input_validation_failed`
- `tool_result`
- `injection_enqueued`
- `injection_applied`
- `tool_approval_requested`
- `tool_approval_resolved`
- `subagent_stopped`
- `subagent_resumed`
- `run_stopped`
- `run_completed`
- `run_failed`
- `notification_requested`
- `awaiting_manual_action`
- `token_usage`

---

## 3. System APIs

### `GET /system/health`
Returns service status.

Response:
```json
{"status":"ok","version":"0.1.0"}
```

### `GET /system/configs`
Returns runtime config load status for model/mcp/skills.

### `GET /system/configs/model`
Returns raw model config (`model.json`).

### `GET /system/configs/model/profiles`
Returns normalized model profile list.

### `PUT /system/configs/model/profiles/{name}`
Upsert a model profile.

Request:
```json
{
  "provider": "openai_compatible",
  "model": "gpt-4o-mini",
  "base_url": "https://api.openai.com/v1",
  "api_key": "***",
  "temperature": 0.7,
  "top_p": 1.0,
  "max_tokens": 4096
}
```

### `GET /system/configs/model/providers/models`
Returns queryable provider model metadata generated from runtime profiles.

Optional query parameter:
- `provider`: provider type filter (`openai_compatible`, `echo`)

Response:
```json
[
  {
    "profile": "default",
    "provider": "openai_compatible",
    "model": "gpt-4o-mini",
    "base_url": "https://api.openai.com/v1"
  }
]
```

### `DELETE /system/configs/model/profiles/{name}`
Deletes a model profile.

### `PUT /system/configs/model`
Replaces full model config object.

Request:
```json
{"config": {"default": {"model": "..."}}}
```

### `POST /system/configs/model:reload`
Reloads model config into runtime.

### `POST /system/configs/mcp:reload`
Reloads MCP config into runtime.

### `POST /system/configs/skills:reload`
Reloads skills config into runtime.

### `GET /system/configs/notifications`
Returns notification rules by event type.

Response:
```json
{
  "tool_approval_requested": {"enabled": true, "channels": ["browser", "toast"]},
  "run_completed": {"enabled": false, "channels": ["toast"]},
  "run_failed": {"enabled": true, "channels": ["browser", "toast"]},
  "run_stopped": {"enabled": false, "channels": ["toast"]}
}
```

### `PUT /system/configs/notifications`
Replaces notification rules.

Request:
```json
{
  "config": {
    "tool_approval_requested": {"enabled": true, "channels": ["browser", "toast"]},
    "run_completed": {"enabled": true, "channels": ["toast"]},
    "run_failed": {"enabled": true, "channels": ["browser", "toast"]},
    "run_stopped": {"enabled": false, "channels": ["toast"]}
  }
}
```

---

## 4. Session APIs

### `POST /sessions`
Creates session.

Request:
```json
{"session_id": null, "metadata": {"project":"demo"}}
```

Response: `SessionRecord`

### `GET /sessions`
Lists sessions.

### `GET /sessions/{session_id}`
Gets one session.

### `PATCH /sessions/{session_id}`
Updates metadata.

Request:
```json
{"metadata": {"key":"value"}}
```

### `DELETE /sessions/{session_id}`
Deletes session and related runtime data.

### `GET /sessions/{session_id}/rounds`
Returns paged rounds projection.

Query:
- `limit` (default 8, max controlled by backend)
- `cursor_run_id` (optional)

Response shape:
```json
{
  "items": [
    {
      "run_id": "...",
      "created_at": "...",
      "intent": "...",
      "coordinator_messages": [],
      "pending_tool_approvals": [],
      "pending_streams": {
        "coordinator_text": "",
        "coordinator_instance_id": "",
        "by_instance": {}
      },
      "workflows": [],
      "instance_role_map": {},
      "role_instance_map": {},
      "task_instance_map": {},
      "task_status_map": {}
    }
  ],
  "has_more": false,
  "next_cursor": null
}
```

Field notes:
- `instance_role_map`: `instance_id -> role_id`
- `role_instance_map`: `role_id -> latest instance_id` in this run
- `task_instance_map`: `task_id -> assigned instance_id` (use this when a workflow has multiple tasks with the same `role_id`)
- `task_status_map`: `task_id -> task status` (`created|assigned|running|completed|failed|timeout|stopped`)

### `GET /sessions/{session_id}/rounds/{run_id}`
Gets one round projection.

### `GET /sessions/{session_id}/agents`
Lists agent instances in session.

### `GET /sessions/{session_id}/events`
Lists persisted business events in session.

### `GET /sessions/{session_id}/messages`
Lists persisted messages in session.

### `GET /sessions/{session_id}/agents/{instance_id}/messages`
Lists messages for one agent instance.

### `GET /sessions/{session_id}/workflows`
Lists persisted workflow graphs discovered from session tasks.

### `GET /sessions/{session_id}/token-usage`
Returns aggregated token consumption for the entire session, grouped by `role_id`.

Response:
```json
{
  "session_id": "...",
  "total_input_tokens": 12345,
  "total_output_tokens": 3456,
  "total_tokens": 15801,
  "total_requests": 10,
  "total_tool_calls": 7,
  "by_role": {
    "coordinator_agent": {
      "role_id": "coordinator_agent",
      "input_tokens": 8000,
      "output_tokens": 2000,
      "total_tokens": 10000,
      "requests": 5,
      "tool_calls": 3
    }
  }
}
```

### `GET /sessions/{session_id}/runs/{run_id}/token-usage`
Returns token consumption for a single run, broken down per agent instance.

Response:
```json
{
  "run_id": "...",
  "total_input_tokens": 5000,
  "total_output_tokens": 1500,
  "total_tokens": 6500,
  "total_requests": 4,
  "total_tool_calls": 2,
  "by_agent": [
    {
      "instance_id": "...",
      "role_id": "coordinator_agent",
      "input_tokens": 3000,
      "output_tokens": 800,
      "total_tokens": 3800,
      "requests": 2,
      "tool_calls": 1
    }
  ]
}
```

---

## 5. Run APIs

### `POST /runs`
Creates run.

Request:
```json
{
  "intent": "Implement endpoint X",
  "session_id": "session-xxxx",
  "execution_mode": "ai"
}
```

Response:
```json
{"run_id":"...","session_id":"..."}
```

### `GET /runs/{run_id}/events`
SSE stream for run events.

SSE payload line:
```text
data: {"event_type":"text_delta", ...}
```

### `POST /runs/{run_id}/inject`
Broadcasts user/system/subagent message to running agents in run.

Request:
```json
{"source":"user","content":"Please include pagination"}
```

### `GET /runs/{run_id}/tool-approvals`
Lists pending tool approval requests.

### `POST /runs/{run_id}/tool-approvals/{tool_call_id}/resolve`
Resolves tool approval.

Request:
```json
{"action":"approve","feedback":""}
```

### `POST /runs/{run_id}/stop`
Stops run or one subagent.

Request (main):
```json
{"scope":"main"}
```

Request (subagent):
```json
{"scope":"subagent","instance_id":"inst-xxx"}
```

### `POST /runs/{run_id}/subagents/{instance_id}/inject`
Injects follow-up content to a subagent.

Request:
```json
{"content":"Revise with this extra constraint"}
```

---

## 6. Workflow APIs

### `POST /workflows/runs/{run_id}`
Creates workflow for a run.

Request:
```json
{
  "objective": "Build API service",
  "workflow_type": "custom",
  "tasks": [
    {
      "task_name": "spec",
      "objective": "Define API contract",
      "role_id": "spec_spec",
      "depends_on": []
    },
    {
      "task_name": "code",
      "objective": "Implement API",
      "role_id": "spec_coder",
      "depends_on": ["spec"]
    }
  ]
}
```

`workflow_type=spec_flow` ignores custom `tasks` and uses built-in stage template.

### `GET /workflows/runs/{run_id}/{workflow_id}`
Gets workflow status.

### `POST /workflows/runs/{run_id}/{workflow_id}/dispatch`
Dispatches workflow action.

Request:
```json
{
  "action": "next",
  "feedback": "optional note",
  "max_dispatch": 1
}
```

Action behavior:
- `next`: approve current stage and dispatch next ready tasks.
- `revise`: revise latest completed stage with feedback.

---

## 7. Task APIs

### `GET /tasks`
Lists all tasks.

### `GET /tasks/{task_id}`
Gets one task.

---

## 8. Role APIs

### `GET /roles`
Lists loaded role definitions.

### `POST /roles:validate`
Validates role files against registered tools.

---

## 9. Prompt APIs

### `POST /prompts:preview`
Builds prompt preview payload for a specific role.

Request:
```json
{
  "role_id": "coordinator_agent",
  "objective": "Draft release note",
  "shared_state": {"lang": "zh-CN", "priority": 1},
  "tools": ["dispatch_tasks"],
  "skills": ["time"]
}
```

Request notes:
- `role_id` is required.
- `objective` is optional; server fills a default preview objective when omitted.
- `shared_state` is optional and defaults to `{}`.
- `tools` / `skills` are optional overrides. If omitted, role defaults are used.

Response:
```json
{
  "role_id": "coordinator_agent",
  "objective": "Draft release note",
  "tools": ["dispatch_tasks"],
  "skills": ["time"],
  "runtime_system_prompt": "...",
  "provider_system_prompt": "...",
  "user_prompt": "...",
  "tool_prompt": "...",
  "skill_prompt": "..."
}
```

Error behavior:
- `404`: unknown role id
- `400`: unknown tool/skill override

---

## 10. Frontend Log API

### `POST /logs/frontend`
Ingests frontend structured logs in batch.

Request:
```json
{
  "events": [
    {
      "level": "error",
      "event": "sse.disconnect",
      "message": "event stream disconnected",
      "trace_id": "trace-xxx",
      "request_id": "req-xxx",
      "run_id": "run-xxx",
      "session_id": "session-xxx",
      "task_id": null,
      "instance_id": null,
      "role_id": null,
      "payload": {"ready_state": 2},
      "ts": "2026-03-04T12:00:00Z"
    }
  ]
}
```

---

## 11. Trigger APIs

### trigger enums

#### `source_type`
- `schedule`
- `webhook`
- `im`
- `rss`
- `custom`

#### `status`
- `enabled`
- `disabled`

#### `auth mode`
- `none`
- `url_token`
- `header_token`
- `hmac_sha256`

#### `trigger event status`
- `received`
- `duplicate`
- `rejected_auth`

### `POST /triggers`
Creates one trigger definition.

Request:
```json
{
  "name": "repo_push_trigger",
  "display_name": "Repo Push Trigger",
  "source_type": "webhook",
  "source_config": {"provider": "github"},
  "auth_policies": [
    {"mode": "none"}
  ],
  "target_config": null,
  "enabled": true
}
```

Response: `TriggerDefinition`

### `GET /triggers`
Lists all trigger definitions.

### `GET /triggers/{trigger_id}`
Gets one trigger definition.

### `PATCH /triggers/{trigger_id}`
Updates trigger mutable fields:
- `name`
- `display_name`
- `source_config`
- `auth_policies`
- `target_config`

### `POST /triggers/{trigger_id}:enable`
Enables trigger.

### `POST /triggers/{trigger_id}:disable`
Disables trigger.

### `POST /triggers/{trigger_id}:rotate-token`
Rotates webhook public token.

### `POST /triggers/ingest`
Internal generic trigger ingest endpoint.

Request:
```json
{
  "trigger_id": "trg_123",
  "trigger_name": null,
  "source_type": "webhook",
  "event_key": "evt_001",
  "occurred_at": "2026-03-05T12:00:00Z",
  "payload": {"action": "push"},
  "metadata": {"source": "internal"}
}
```

### `POST /triggers/webhooks/{public_token}`
Webhook dedicated endpoint. Requests are routed by `public_token`.

Supported body patterns:
1. Envelope style:
```json
{
  "event_key": "evt_001",
  "occurred_at": "2026-03-05T12:00:00Z",
  "payload": {"action": "push"},
  "metadata": {"provider": "github"}
}
```
2. Raw JSON object (the whole object is persisted as `payload`).

Response (`/ingest` and `/webhooks/{public_token}`):
```json
{
  "accepted": true,
  "event_id": "tev_abc123",
  "duplicate": false,
  "status": "received",
  "trigger_id": "trg_123",
  "trigger_name": "repo_push_trigger"
}
```

### `GET /triggers/{trigger_id}/events`
Lists persisted trigger events for one trigger.

Query:
- `limit` (default 50, max 100)
- `cursor_event_id` (optional)

Response:
```json
{
  "items": [
    {
      "sequence_id": 1,
      "event_id": "tev_abc123",
      "trigger_id": "trg_123",
      "trigger_name": "repo_push_trigger",
      "source_type": "webhook",
      "event_key": "evt_001",
      "status": "received",
      "received_at": "2026-03-05T12:00:00Z",
      "occurred_at": "2026-03-05T12:00:00Z",
      "payload": {"action": "push"},
      "metadata": {"provider": "github"},
      "headers": {"content-type": "application/json"},
      "remote_addr": "127.0.0.1",
      "auth_mode": "none",
      "auth_result": "accepted",
      "auth_reason": "no_auth_required"
    }
  ],
  "next_cursor": null
}
```

### `GET /triggers/events/{event_id}`
Gets one persisted trigger event.

### trigger ingest status/error behavior
- `403`: authentication rejected (`rejected_auth` event is still persisted for audit)
- `404`: unknown trigger / token / event id
- `409`: disabled trigger
- `422`: payload validation or source mismatch

### idempotency
- `event_key` is optional.
- When provided, uniqueness is enforced by `(trigger_id, event_key)`.
- Duplicate ingest returns `duplicate=true` and existing `event_id`.

Response:
```json
{"accepted": 1}
```
