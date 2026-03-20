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
    session_mode TEXT NOT NULL DEFAULT 'normal',
    orchestration_preset_id TEXT,
    started_at   TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
```

Purpose: session metadata, lifecycle, and bound execution workspace identity.

Notes:
- `session_mode` is `normal` or `orchestration`.
- `orchestration_preset_id` stores the session-selected preset for orchestration mode.
- `started_at` is written when the first run is created and locks further mode switching for that session.

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

Purpose: registered execution workspaces. `profile_json` stores the typed workspace profile, including Git worktree metadata such as `source_root_path`, `branch_name`, and `forked_from_workspace_id` when a workspace is created through project forking.

---

### 2.2 `agent_instances`

```sql
CREATE TABLE IF NOT EXISTS agent_instances (
    run_id                TEXT NOT NULL,
    trace_id              TEXT NOT NULL,
    session_id            TEXT NOT NULL,
    instance_id           TEXT PRIMARY KEY,
    role_id               TEXT NOT NULL,
    workspace_id          TEXT NOT NULL DEFAULT '',
    conversation_id       TEXT NOT NULL DEFAULT '',
    status                TEXT NOT NULL,
    runtime_system_prompt TEXT NOT NULL DEFAULT '',
    runtime_tools_json    TEXT NOT NULL DEFAULT '',
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_instances_run_status
    ON agent_instances(run_id, status);
```

Purpose: runtime snapshot of agent instances. Besides lifecycle state, each row now stores the latest runtime system prompt and runtime tools JSON shown in the subagent panel.

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

### 2.9.1 `gateway_sessions`

```sql
CREATE TABLE IF NOT EXISTS gateway_sessions (
    gateway_session_id       TEXT PRIMARY KEY,
    channel_type             TEXT NOT NULL,
    external_session_id      TEXT NOT NULL,
    internal_session_id      TEXT NOT NULL,
    active_run_id            TEXT,
    peer_user_id             TEXT,
    peer_chat_id             TEXT,
    cwd                      TEXT,
    capabilities_json        TEXT NOT NULL,
    channel_state_json       TEXT NOT NULL,
    session_mcp_servers_json TEXT NOT NULL,
    mcp_connections_json     TEXT NOT NULL,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_gateway_sessions_channel_external
    ON gateway_sessions(channel_type, external_session_id);
CREATE INDEX IF NOT EXISTS idx_gateway_sessions_internal_session
    ON gateway_sessions(internal_session_id);
```

Purpose: persistent mapping between an external gateway channel session and the internal Agent Teams session/run state used by the runtime.

Notes:
- `channel_type` identifies the transport-facing gateway implementation, starting with `acp_stdio`.
- `external_session_id` is the channel-visible session key; `internal_session_id` remains the core runtime session source of truth.
- `capabilities_json` stores channel-scoped capability negotiation data.
- `session_mcp_servers_json` stores session-scoped MCP server declarations supplied through the gateway transport.
- `mcp_connections_json` stores MCP connection state for gateway-managed transports such as MCP over ACP.

---

## 3. Relationship Keys

Primary query keys used by repositories:
- `session_id`: session-level retrieval across `sessions`, `tasks`, `agent_instances`, `events`, `messages`, `token_usage`.
- `trace_id` (`run_id`): run-level retrieval across `tasks`, `events`, `messages`, `token_usage`.
- `task_id`: task-level retrieval and task assignment tracking.
- `instance_id`: agent-level retrieval and message history.
- `trigger_id`: trigger-level retrieval across `triggers`, `trigger_events`.
- `event_id`: trigger-event level retrieval for audit and replay preparation.
- `gateway_session_id`: external channel session retrieval across `gateway_sessions`.
- `external_session_id`: channel-scoped lookup key for reconnect and session resume flows.

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
- `agent_teams.gateway`: `gateway_sessions`.
- `agent_teams.roles`: `role_memories`.

---

### 2.9 `run_intents`

```sql
CREATE TABLE IF NOT EXISTS run_intents (
    run_id         TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL,
    intent         TEXT NOT NULL,
    execution_mode TEXT NOT NULL,
    session_mode   TEXT NOT NULL DEFAULT 'normal',
    yolo           TEXT NOT NULL DEFAULT 'false',
    thinking_enabled TEXT NOT NULL DEFAULT 'false',
    thinking_effort TEXT,
    topology_json  TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_intents_session ON run_intents(session_id);
```

Purpose: stores the user intent and per-run execution settings needed for queued runs and recoverable resume paths. `yolo` controls whether tool approvals are skipped entirely for that run. `thinking_enabled` and `thinking_effort` capture per-run thinking configuration for providers that support reasoning streams. `session_mode` and `topology_json` snapshot the resolved root-agent topology used when the run was created, so recoverable resumes do not drift when global orchestration settings change later.

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

Purpose: workspace-scoped durable role memory. For subagents this table stores the current reflection summary that is injected into future same-role sessions in the same workspace.

Notes:
- there is no `role_daily_memories` table anymore
- legacy daily-memory tables may be dropped during repository initialization
- reflection growth is controlled by compaction and summary rewrite, not append-only rows

---

## 4. Notes

- Session deletion removes that session subtree under the bound workspace.
- Daily memory is no longer file-based.
