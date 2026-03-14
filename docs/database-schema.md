# Database Schema

## 1. Storage

- Engine: SQLite
- Database file: `~/.config/agent-teams/agent_teams.db`
- Foreign keys: enabled on each connection (`PRAGMA foreign_keys = ON`)
- Runtime logs are file-based and stored under `~/.config/agent-teams/log/backend.log`, `~/.config/agent-teams/log/debug.log`, and `~/.config/agent-teams/log/frontend.log`

---

## 2. Tables

### 2.1 `sessions`

```sql
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    metadata     TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
```

Purpose: session metadata, lifecycle, and bound execution workspace identity.

---

### 2.1.1 `workspaces`

```sql
CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id TEXT PRIMARY KEY,
    root_path    TEXT NOT NULL,
    backend      TEXT NOT NULL,
    profile_json TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
```

Purpose: registered execution workspaces.

---

### 2.2 `agent_instances`

```sql
CREATE TABLE IF NOT EXISTS agent_instances (
    run_id          TEXT NOT NULL,
    trace_id        TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    instance_id     TEXT PRIMARY KEY,
    role_id         TEXT NOT NULL,
    workspace_id    TEXT NOT NULL DEFAULT '',
    conversation_id TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_instances_run_status
    ON agent_instances(run_id, status);
```

Purpose: runtime snapshot of agent instances.

Notes:
- Runtime semantics are session-level: one delegated role instance is reused across all tasks in the same session.
- `run_id` / `trace_id` are last-observed execution metadata, not uniqueness keys.
- New dispatches for the same `session_id + role_id` reuse the existing row instead of creating a new instance.
- `workspace_id` is the execution workspace bound from the owning session.
- `conversation_id` is the conversation continuity key for the role instance.

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
- `role_id: string`
- `title: string | null`
- `objective: string`
- `verification: { checklist: string[] }`

Notes:
- `role_id` is the execution target for the task.
- `title` is a persisted task summary used by session projections and task APIs.
- The system no longer stores workflow graphs. `tasks` is the only orchestration source of truth.

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
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL DEFAULT '',
    workspace_id    TEXT NOT NULL DEFAULT '',
    conversation_id TEXT NOT NULL DEFAULT '',
    agent_role_id   TEXT NOT NULL DEFAULT '',
    instance_id     TEXT NOT NULL,
    task_id         TEXT NOT NULL,
    trace_id        TEXT NOT NULL,
    role            TEXT NOT NULL,
    message_json    TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_instance ON messages(instance_id);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id);
```

Purpose: append-only LLM message history.

`role` values used by repository:
- `user`
- `assistant`
- `unknown`

---

### 2.7 `token_usage`

```sql
CREATE TABLE IF NOT EXISTS token_usage (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id              TEXT NOT NULL,
    run_id                  TEXT NOT NULL,
    instance_id             TEXT NOT NULL,
    role_id                 TEXT NOT NULL,
    input_tokens            INTEGER DEFAULT 0,
    cached_input_tokens     INTEGER DEFAULT 0,
    output_tokens           INTEGER DEFAULT 0,
    reasoning_output_tokens INTEGER DEFAULT 0,
    requests                INTEGER DEFAULT 0,
    tool_calls              INTEGER DEFAULT 0,
    recorded_at             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_token_usage_run ON token_usage(run_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_session ON token_usage(session_id);
```

Purpose: one row per `agent.iter()` completion cycle (coordinator or subagent). Multiple rows may exist for the same `instance_id` within a run if injection-restarts occurred. The table stores both the billed prompt/completion counts and the provider-reported cached-input / reasoning-output sub-counts used by the session usage UI. Rows are deleted when the owning session is deleted.

---

### 2.8 `triggers`

```sql
CREATE TABLE IF NOT EXISTS triggers (
    trigger_id         TEXT PRIMARY KEY,
    name               TEXT NOT NULL UNIQUE,
    display_name       TEXT NOT NULL,
    source_type        TEXT NOT NULL,
    status             TEXT NOT NULL,
    public_token       TEXT UNIQUE,
    source_config_json TEXT NOT NULL,
    auth_policies_json TEXT NOT NULL,
    target_config_json TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_triggers_source_type
    ON triggers(source_type);
CREATE INDEX IF NOT EXISTS idx_triggers_status
    ON triggers(status);
```

Purpose: trigger definitions and webhook routing configuration.

`source_type` values:
- `schedule`
- `webhook`
- `im`
- `rss`
- `custom`

`status` values:
- `enabled`
- `disabled`

---

### 2.9 `trigger_events`

```sql
CREATE TABLE IF NOT EXISTS trigger_events (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id           TEXT NOT NULL UNIQUE,
    trigger_id         TEXT NOT NULL,
    trigger_name       TEXT NOT NULL,
    source_type        TEXT NOT NULL,
    event_key          TEXT,
    status             TEXT NOT NULL,
    received_at        TEXT NOT NULL,
    occurred_at        TEXT,
    payload_json       TEXT NOT NULL,
    metadata_json      TEXT NOT NULL,
    headers_json       TEXT NOT NULL,
    remote_addr        TEXT,
    auth_mode          TEXT,
    auth_result        TEXT NOT NULL,
    auth_reason        TEXT,
    FOREIGN KEY(trigger_id) REFERENCES triggers(trigger_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_trigger_events_key
    ON trigger_events(trigger_id, event_key)
    WHERE event_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_trigger_events_trigger
    ON trigger_events(trigger_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_trigger_events_status
    ON trigger_events(status, id DESC);
```

Purpose: append-only ingest audit log for trigger events.

`status` values:
- `received`
- `duplicate`
- `rejected_auth`

---

## 3. Relationship Keys

Primary query keys used by repositories:
- `session_id`: session-level retrieval across `sessions`, `tasks`, `agent_instances`, `events`, `messages`, `token_usage`.
- `trace_id` (`run_id`): run-level retrieval across `tasks`, `events`, `messages`, `token_usage`.
- `task_id`: task-level retrieval and task assignment tracking.
- `instance_id`: agent-level retrieval and message history.
- `trigger_id`: trigger-level retrieval across `triggers`, `trigger_events`.
- `event_id`: trigger-event level retrieval for audit and replay preparation.

---

## 3.1 Code Ownership

- `agent_teams.persistence`: shared SQLite connection setup, scope models, and `shared_state`.
- `agent_teams.sessions`: `sessions`.
- `agent_teams.workspace`: `workspaces`.
- `agent_teams.sessions.runs`: `events`, `run_intents`, `run_runtime`, `run_states`, `run_snapshots`.
- `agent_teams.agents`: `agent_instances`.
- `agent_teams.agents.tasks`: `tasks`.
- `agent_teams.agents.execution`: `messages`.
- `agent_teams.tools.runtime`: `approval_tickets`.
- `agent_teams.providers`: `token_usage`.
- `agent_teams.triggers`: `triggers`, `trigger_events`.
- `agent_teams.roles`: `role_memories`, `role_daily_memories`.

---

### 2.9 `run_intents`

```sql
CREATE TABLE IF NOT EXISTS run_intents (
    run_id         TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL,
    intent         TEXT NOT NULL,
    execution_mode TEXT NOT NULL,
    approval_mode  TEXT NOT NULL DEFAULT 'standard',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_intents_session ON run_intents(session_id);
```

Purpose: stores the user intent and per-run execution settings needed for queued runs and recoverable resume paths. `approval_mode` controls whether tool approvals run in `standard` mode or are skipped entirely in `yolo` mode.

---

### 2.10 `role_memories`

```sql
CREATE TABLE IF NOT EXISTS role_memories (
    role_id          TEXT NOT NULL,
    workspace_id     TEXT NOT NULL,
    content_markdown TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (role_id, workspace_id)
);
```

Purpose: durable role memory shared by the same `role_id` inside one workspace.

---

### 2.11 `role_daily_memories`

```sql
CREATE TABLE IF NOT EXISTS role_daily_memories (
    role_id           TEXT NOT NULL,
    workspace_id      TEXT NOT NULL,
    memory_date       TEXT NOT NULL,
    kind              TEXT NOT NULL,
    content_markdown  TEXT NOT NULL,
    source_session_id TEXT,
    source_task_id    TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    PRIMARY KEY (role_id, workspace_id, memory_date, kind)
);
```

Purpose: per-day role memory snapshots scoped to one workspace.

`kind` values:
- `raw`
- `digest`

---

## 4. Filesystem Layout

### 4.1 Stage documents

- `{workspace_root}/.agent_teams/sessions/{session_id}/roles/{role_id}/stage/{stage_name}/{timestamp}.md`

Notes:
- Stage files are managed directly by `src/agent_teams/tools/stage_tools`.
- Session deletion removes that session subtree under the bound workspace.
- Daily memory is no longer file-based.
