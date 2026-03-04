# Database Schema

## 1. Storage

- Engine: SQLite
- Database file: `.agent_teams/agent_teams.db`
- Foreign keys: enabled on each connection (`PRAGMA foreign_keys = ON`)

---

## 2. Tables

### 2.1 `sessions`

```sql
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    metadata   TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Purpose: session metadata and lifecycle.

---

### 2.2 `agent_instances`

```sql
CREATE TABLE IF NOT EXISTS agent_instances (
    run_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    instance_id TEXT PRIMARY KEY,
    role_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_instances_run_status
    ON agent_instances(run_id, status);
```

Purpose: runtime snapshot of agent instances.

`status` values:
- `idle`
- `running`
- `stopped`
- `completed`
- `failed`
- `timeout`

---

### 2.3 `tasks`

```sql
CREATE TABLE IF NOT EXISTS tasks (
    task_id              TEXT PRIMARY KEY,
    trace_id             TEXT NOT NULL,
    session_id           TEXT NOT NULL,
    parent_task_id       TEXT,
    envelope_json        TEXT NOT NULL,
    status               TEXT NOT NULL,
    assigned_instance_id TEXT,
    result               TEXT,
    error_message        TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_trace ON tasks(trace_id);
CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id);
```

Purpose: task runtime snapshot.

`status` values:
- `created`
- `assigned`
- `running`
- `stopped`
- `completed`
- `failed`
- `timeout`

#### `tasks.envelope_json` (`TaskEnvelope`) contract

Required fields:
- `task_id: string`
- `session_id: string`
- `parent_task_id: string | null`
- `trace_id: string`
- `objective: string`
- `verification: { checklist: string[] }`

Notes:
- `confirmation_gate` is not part of the current schema.

---

### 2.4 `shared_state`

```sql
CREATE TABLE IF NOT EXISTS shared_state (
    scope_type  TEXT NOT NULL,
    scope_id    TEXT NOT NULL,
    state_key   TEXT NOT NULL,
    value_json  TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at  TEXT,
    PRIMARY KEY (scope_type, scope_id, state_key)
);
```

Purpose: cross-agent key-value state.

`scope_type` values:
- `global`
- `session`
- `task`
- `instance`

`expires_at` controls TTL.

---

### 2.5 `events`

```sql
CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type   TEXT NOT NULL,
    trace_id     TEXT NOT NULL,
    session_id   TEXT NOT NULL,
    task_id      TEXT,
    instance_id  TEXT,
    payload_json TEXT NOT NULL,
    occurred_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_trace ON events(trace_id);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
```

Purpose: append-only business/run event log.

---

### 2.6 `messages`

```sql
CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL DEFAULT '',
    instance_id  TEXT NOT NULL,
    task_id      TEXT NOT NULL,
    trace_id     TEXT NOT NULL,
    role         TEXT NOT NULL,
    message_json TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_instance ON messages(instance_id);
CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id);
```

Purpose: append-only LLM message history.

`role` values used by repository:
- `user`
- `assistant`
- `unknown`

---

### 2.7 `system_logs`

```sql
CREATE TABLE IF NOT EXISTS system_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    level         TEXT NOT NULL,
    event         TEXT,
    trace_id      TEXT,
    request_id    TEXT,
    session_id    TEXT,
    run_id        TEXT,
    task_id       TEXT,
    instance_id   TEXT,
    logger        TEXT,
    message       TEXT NOT NULL,
    payload_json  TEXT,
    error_json    TEXT,
    raw_json      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_system_logs_ts ON system_logs(ts);
CREATE INDEX IF NOT EXISTS idx_system_logs_level ON system_logs(level);
CREATE INDEX IF NOT EXISTS idx_system_logs_event ON system_logs(event);
CREATE INDEX IF NOT EXISTS idx_system_logs_trace ON system_logs(trace_id);
CREATE INDEX IF NOT EXISTS idx_system_logs_run ON system_logs(run_id);
CREATE INDEX IF NOT EXISTS idx_system_logs_request ON system_logs(request_id);
```

Purpose: append-only structured runtime logs.

---

### 2.8 `token_usage`

```sql
CREATE TABLE IF NOT EXISTS token_usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL,
    run_id        TEXT NOT NULL,
    instance_id   TEXT NOT NULL,
    role_id       TEXT NOT NULL,
    input_tokens  INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    requests      INTEGER DEFAULT 0,
    tool_calls    INTEGER DEFAULT 0,
    recorded_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_token_usage_run ON token_usage(run_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_session ON token_usage(session_id);
```

Purpose: one row per `agent.iter()` completion cycle (coordinator or subagent). Multiple rows may exist for the same `instance_id` within a run if injection-restarts occurred. Rows are deleted when the owning session is deleted.

---

## 3. Relationship Keys

Primary query keys used by repositories:
- `session_id`: session-level retrieval across `sessions`, `tasks`, `agent_instances`, `events`, `messages`, `token_usage`.
- `trace_id` (`run_id`): run-level retrieval across `tasks`, `events`, `messages`, `token_usage`.
- `task_id`: task-level retrieval and task assignment tracking.
- `instance_id`: agent-level retrieval and message history.
