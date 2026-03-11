# Reflection API Design

## CLI

### `agent-teams reflection jobs list`

- default output: table
- optional: `--format json`
- backend endpoint: `GET /api/reflection/jobs?limit={limit}`

### `agent-teams reflection jobs retry`

- input: `--job-id`
- default output: table
- optional: `--format json`
- backend endpoint: `POST /api/reflection/jobs/{job_id}/retry`

### `agent-teams reflection memory show`

- input: `--session-id`, `--role-id`
- default output: path + exists + content
- optional: `--format json`
- backend endpoint: `GET /api/reflection/memory/session-roles/{session_id}/{role_id}`

### `agent-teams reflection daily show`

- input: `--instance-id`, `--date`, `--kind raw|digest`
- default output: path + exists + content
- optional: `--format json`
- backend endpoint: `GET /api/reflection/memory/instances/{instance_id}/daily/{date}?kind={kind}`

## HTTP API

### `GET /api/reflection/jobs`

Query:
- `limit` default `50`

Response:
```json
[
  {
    "job_id": "rjob-123",
    "job_type": "daily_reflection",
    "session_id": "session-1",
    "run_id": "run-1",
    "task_id": "task-1",
    "instance_id": "inst-1",
    "role_id": "writer_agent",
    "workspace_id": "workspace-1",
    "conversation_id": "conversation-1",
    "memory_owner_scope": "session_role",
    "memory_owner_id": "session-1:writer_agent",
    "trigger_date": "2026-03-11",
    "status": "queued",
    "attempt_count": 0,
    "last_error": null,
    "created_at": "2026-03-11T00:00:00+00:00",
    "updated_at": "2026-03-11T00:00:00+00:00"
  }
]
```

### `POST /api/reflection/jobs/{job_id}/retry`

Response: the updated `ReflectionJobRecord`.

Error behavior:
- `404` when the job id does not exist.

### `GET /api/reflection/memory/session-roles/{session_id}/{role_id}`

Response:
```json
{
  "path": ".agent_teams/memory/session_roles/session-1/writer_agent/MEMORY.md",
  "exists": true,
  "content": "# MEMORY\n..."
}
```

### `GET /api/reflection/memory/instances/{instance_id}/daily/{date}`

Query:
- `kind`: `raw|digest`, default `digest`

Response:
```json
{
  "path": ".agent_teams/workspaces/workspace-1/memory/daily/digest/2026-03-11.md",
  "exists": true,
  "content": "# Daily Digest - 2026-03-11\n..."
}
```

Error behavior:
- `404` when the instance id does not exist.
