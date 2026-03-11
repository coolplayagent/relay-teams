# Reflection Database and Storage Schema

## `reflection_jobs`

```sql
CREATE TABLE IF NOT EXISTS reflection_jobs (
    job_id              TEXT PRIMARY KEY,
    job_type            TEXT NOT NULL,
    session_id          TEXT NOT NULL,
    run_id              TEXT NOT NULL,
    task_id             TEXT NOT NULL,
    instance_id         TEXT NOT NULL,
    role_id             TEXT NOT NULL,
    workspace_id        TEXT NOT NULL,
    conversation_id     TEXT NOT NULL,
    memory_owner_scope  TEXT NOT NULL,
    memory_owner_id     TEXT NOT NULL,
    trigger_date        TEXT NOT NULL,
    status              TEXT NOT NULL,
    attempt_count       INTEGER NOT NULL,
    last_error          TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
```

Indexes:
- `idx_reflection_jobs_status_created(status, created_at)`
- `idx_reflection_jobs_session(session_id, created_at)`
- `idx_reflection_jobs_owner_type_date(memory_owner_id, job_type, trigger_date)`

Enum values:
- `job_type`: `daily_reflection`, `long_term_consolidation`
- `status`: `queued`, `running`, `completed`, `failed`
- `memory_owner_scope`: `session_role`

## File Layout

### Daily raw

- `.agent_teams/workspaces/{workspace_id}/memory/daily/raw/YYYY-MM-DD.md`

### Daily digest

- `.agent_teams/workspaces/{workspace_id}/memory/daily/digest/YYYY-MM-DD.md`

### Long-term memory

- `.agent_teams/memory/session_roles/{session_id}/{role_id}/MEMORY.md`

## Lifecycle Rules

- `daily_reflection` is created after successful non-coordinator subagent completion.
- `long_term_consolidation` is created at most once per day for the same `session + role` owner in the current implementation.
- server startup resets leftover `running` jobs back to `queued`.
- retry only updates the job back to `queued`; `attempt_count` increases again when the worker reclaims it.
- session deletion removes all reflection jobs and all role-level memory files for that session.
- daily files older than `daily_retention_days` are deleted during daily reflection processing.
