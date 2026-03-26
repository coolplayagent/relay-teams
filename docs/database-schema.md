# Database Schema

## 1. Storage

- Engine: SQLite
- Database file: `~/.agent-teams/agent_teams.db`
- Foreign keys: enabled on each connection (`PRAGMA foreign_keys = ON`)
- Runtime logs are file-based and stored under `~/.agent-teams/log/backend.log`, `~/.agent-teams/log/debug.log`, and `~/.agent-teams/log/frontend.log`

---

## 2. Tables

### 2.1 `sessions`

```sql
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    metadata     TEXT NOT NULL,
    session_mode TEXT NOT NULL DEFAULT 'normal',
    normal_root_role_id TEXT,
    orchestration_preset_id TEXT,
    started_at   TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
```

Purpose: session metadata, lifecycle, and bound execution workspace identity.

Notes:
- `session_mode` is `normal` or `orchestration`.
- `normal_root_role_id` stores the session-selected root role for normal mode. When `NULL`, runtime falls back to the current `MainAgent`.
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

### 2.1.2 `external_session_bindings`

```sql
CREATE TABLE IF NOT EXISTS external_session_bindings (
    platform          TEXT NOT NULL,
    trigger_id        TEXT NOT NULL,
    tenant_key        TEXT NOT NULL,
    external_chat_id  TEXT NOT NULL,
    session_id        TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    PRIMARY KEY (platform, trigger_id, tenant_key, external_chat_id)
);

CREATE INDEX IF NOT EXISTS idx_external_session_bindings_session
    ON external_session_bindings(session_id);
CREATE INDEX IF NOT EXISTS idx_external_session_bindings_trigger
    ON external_session_bindings(trigger_id, updated_at DESC);
```

Purpose: persistent mapping between an external chat identity and the internal Agent Teams session.

Notes:
- `platform` starts with `feishu`.
- `trigger_id + tenant_key + external_chat_id` is the durable lookup key used by inbound Feishu callbacks.
- The same external chat under different Feishu bots resolves to different internal sessions.
- The owning session remains the source of truth for runtime state; this table only resolves the external conversation back to that session.

---

### 2.1.3 `external_agent_sessions`

```sql
CREATE TABLE IF NOT EXISTS external_agent_sessions (
    session_id          TEXT NOT NULL,
    role_id             TEXT NOT NULL,
    agent_id            TEXT NOT NULL,
    transport           TEXT NOT NULL,
    external_session_id TEXT NOT NULL,
    status              TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    PRIMARY KEY(session_id, role_id, agent_id)
);
```

Purpose: persistent mapping between one internal `session_id + role_id` pair and the reused remote ACP session created for the bound external agent.

Notes:
- `agent_id` references one configured entry in `~/.agent-teams/agents.json`.
- `transport` stores the outbound ACP transport type used by that saved agent config.
- `external_session_id` is the remote ACP session identifier returned by the external agent and reused for later turns in the same internal session.
- `status` stores the last-known remote session health, currently `ready` or `failed`.

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

Notes:
- Session-level `clear` operations no longer delete rows from `messages`.
- The active conversation context is derived by looking up the latest `session_history_markers.marker_type = 'clear'` entry for the same `session_id` and filtering rows with `created_at` after that marker.
- Historical session round rendering may still read the full `messages` history for the same session.

---

### 2.6.1 `session_history_markers`

```sql
CREATE TABLE IF NOT EXISTS session_history_markers (
    marker_id      TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL,
    marker_type    TEXT NOT NULL,
    metadata_json  TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_history_markers_session
    ON session_history_markers(session_id, created_at DESC);
```

Purpose: append-only logical history boundaries for a session.

Notes:
- `marker_type` currently starts with `clear`.
- A `clear` marker divides active context from earlier persisted history without deleting earlier `messages`, `events`, or `token_usage`.
- Multiple markers may exist for the same session. Runtime context uses the latest matching marker.

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

Notes:
- Session-level `clear` operations do not delete `token_usage` rows.
- Active session totals are filtered to rows whose `recorded_at` is after the latest `session_history_markers.marker_type = 'clear'` row for the same `session_id`.
- Run-level totals are unchanged and always aggregate the full `run_id` history.

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

Notes:
- Feishu bot secrets are not stored in this table.
- Feishu `app_secret`, `verification_token`, and `encrypt_key` are stored in keyring and resolved by `trigger_id`.

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

### 2.10 `feishu_message_pool`

```sql
CREATE TABLE IF NOT EXISTS feishu_message_pool (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    message_pool_id       TEXT NOT NULL UNIQUE,
    trigger_id            TEXT NOT NULL,
    trigger_name          TEXT NOT NULL,
    tenant_key            TEXT NOT NULL,
    chat_id               TEXT NOT NULL,
    chat_type             TEXT NOT NULL,
    event_id              TEXT NOT NULL,
    message_key           TEXT NOT NULL,
    message_id            TEXT,
    command_name          TEXT,
    intent_text           TEXT NOT NULL,
    payload_json          TEXT NOT NULL,
    metadata_json         TEXT NOT NULL,
    processing_status     TEXT NOT NULL,
    ack_status            TEXT NOT NULL,
    ack_text              TEXT,
    final_reply_status    TEXT NOT NULL,
    final_reply_text      TEXT,
    delivery_count        INTEGER NOT NULL,
    process_attempts      INTEGER NOT NULL,
    ack_attempts          INTEGER NOT NULL,
    final_reply_attempts  INTEGER NOT NULL,
    session_id            TEXT,
    run_id                TEXT,
    next_attempt_at       TEXT NOT NULL,
    last_claimed_at       TEXT,
    last_error            TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    completed_at          TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_feishu_message_pool_key
    ON feishu_message_pool(trigger_id, tenant_key, message_key);
CREATE INDEX IF NOT EXISTS idx_feishu_message_pool_status
    ON feishu_message_pool(processing_status, next_attempt_at, id ASC);
CREATE INDEX IF NOT EXISTS idx_feishu_message_pool_chat
    ON feishu_message_pool(trigger_id, tenant_key, chat_id, id ASC);
CREATE INDEX IF NOT EXISTS idx_feishu_message_pool_run
    ON feishu_message_pool(run_id, updated_at DESC);
```

Purpose: durable inbound Feishu message queue and lifecycle ledger.

`processing_status` values:
- `queued`
- `claimed`
- `waiting_result`
- `retryable_failed`
- `cancelled`
- `completed`
- `ignored`
- `dead_letter`

`ack_status` and `final_reply_status` values:
- `pending`
- `sending`
- `sent`
- `skipped`
- `failed`
- `failed`

Notes:
- same-chat Feishu messages are processed in sequence order
- `delivery_count` tracks repeated delivery attempts for the same dedupe key
- `run_id` links the inbound chat message to the created internal run

---

### 2.10.1 `gateway_sessions`

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
- `channel_type` identifies the transport-facing gateway implementation and currently includes `acp_stdio` and `wechat`.
- `external_session_id` is the channel-visible session key; `internal_session_id` remains the core runtime session source of truth.
- `capabilities_json` stores channel-scoped capability negotiation data.
- `session_mcp_servers_json` stores session-scoped MCP server declarations supplied through the gateway transport.
- `mcp_connections_json` stores MCP connection state for gateway-managed transports such as MCP over ACP.

---

### 2.10.2 `wechat_accounts`

```sql
CREATE TABLE IF NOT EXISTS wechat_accounts (
    account_id               TEXT PRIMARY KEY,
    display_name             TEXT NOT NULL,
    base_url                 TEXT NOT NULL,
    cdn_base_url             TEXT NOT NULL,
    route_tag                TEXT,
    status                   TEXT NOT NULL,
    remote_user_id           TEXT,
    sync_cursor              TEXT NOT NULL,
    workspace_id             TEXT NOT NULL,
    session_mode             TEXT NOT NULL,
    normal_root_role_id      TEXT,
    orchestration_preset_id  TEXT,
    yolo                     INTEGER NOT NULL,
    thinking_json            TEXT NOT NULL,
    last_login_at            TEXT,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL
);
```

Purpose: persisted account-level configuration and sync cursor state for the WeChat gateway worker.

Notes:
- bot tokens are stored in keyring, not in this table
- `sync_cursor` stores the last upstream long-poll cursor returned by WeChat
- `workspace_id`, `session_mode`, `normal_root_role_id`, and `orchestration_preset_id` define the runtime preset applied to new or resolved gateway sessions for that account
- runtime status fields such as `running` and `last_error` are computed in memory and returned by the API, not persisted in this table

---

## 3. Relationship Keys

Primary query keys used by repositories:
- `session_id`: session-level retrieval across `sessions`, `external_agent_sessions`, `tasks`, `agent_instances`, `events`, `messages`, `session_history_markers`, `token_usage`.
- `trace_id` (`run_id`): run-level retrieval across `tasks`, `events`, `messages`, `token_usage`.
- `task_id`: task-level retrieval and task assignment tracking.
- `instance_id`: agent-level retrieval and message history.
- `trigger_id`: trigger-level retrieval across `triggers`, `trigger_events`.
- `event_id`: trigger-event level retrieval for audit and replay preparation.
- `platform + trigger_id + tenant_key + external_chat_id`: external-chat lookup for inbound IM triggers.
- `gateway_session_id`: external channel session retrieval across `gateway_sessions`.
- `external_session_id`: channel-scoped lookup key for reconnect and session resume flows.
- `account_id`: WeChat gateway account retrieval across `wechat_accounts`.

---

## 3.1 Code Ownership

- `agent_teams.persistence`: shared SQLite connection setup, scope models, and `shared_state`.
- `agent_teams.sessions`: `sessions`, `external_session_bindings`, `session_history_markers`.
- `agent_teams.external_agents`: `external_agent_sessions`.
- `agent_teams.workspace`: `workspaces`.
- `agent_teams.sessions.runs`: `events`, `run_intents`, `run_runtime`, `run_states`, `run_snapshots`.
- `agent_teams.agents`: `agent_instances`.
- `agent_teams.agents.tasks`: `tasks`.
- `agent_teams.agents.execution`: `messages`.
- `agent_teams.tools.runtime`: `approval_tickets`.
- `agent_teams.providers`: `token_usage`.
- `agent_teams.triggers`: `triggers`, `trigger_events`.
- `agent_teams.gateway`: `gateway_sessions`.
- `agent_teams.wechat`: `wechat_accounts`.
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
    target_role_id TEXT,
    topology_json  TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_intents_session ON run_intents(session_id);
```

Purpose: stores the user intent and per-run execution settings needed for queued runs and recoverable resume paths. `yolo` controls whether tool approvals are skipped entirely for that run. `thinking_enabled` and `thinking_effort` capture per-run thinking configuration for providers that support reasoning streams. `target_role_id` stores an optional one-run direct-chat override, such as a leading `@Role` mention from the web composer. `session_mode` and `topology_json` snapshot the resolved root-agent topology, including the selected normal-mode root role, used when the run was created, so recoverable resumes do not drift when global orchestration settings change later.

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

### 2.8 `metric_points`

```sql
CREATE TABLE IF NOT EXISTS metric_points (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    scope        TEXT NOT NULL,
    scope_id     TEXT NOT NULL,
    metric_name  TEXT NOT NULL,
    bucket_start TEXT NOT NULL,
    tags_json    TEXT NOT NULL,
    value        REAL NOT NULL,
    recorded_at  TEXT NOT NULL
);
```

Indexes:
- `idx_metric_points_scope(scope, scope_id, bucket_start)`
- `idx_metric_points_metric(metric_name, bucket_start)`

### 2.1.2 `automation_projects`

```sql
CREATE TABLE IF NOT EXISTS automation_projects (
    automation_project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT 'automation-system',
    prompt TEXT NOT NULL,
    schedule_mode TEXT NOT NULL,
    cron_expression TEXT,
    run_at TEXT,
    timezone TEXT NOT NULL,
    run_config_json TEXT NOT NULL,
    delivery_binding_json TEXT,
    delivery_events_json TEXT NOT NULL DEFAULT '[]',
    trigger_id TEXT NOT NULL UNIQUE,
    last_session_id TEXT,
    last_run_started_at TEXT,
    last_error TEXT,
    next_run_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_automation_projects_schedule
    ON automation_projects(status, next_run_at);
```

Purpose: stores virtual automation projects shown in the sidebar, their schedule definition, run configuration, and the latest execution pointers.

Notes:
- `schedule_mode` is `cron` or `one_shot`.
- `run_config_json` stores session mode, orchestration preset, execution mode, YOLO, and thinking configuration.
- `delivery_binding_json` stores the selected Feishu chat target copied from an existing `external_session_bindings` row.
- `delivery_events_json` stores which Feishu notifications are enabled for that automation project.
- `trigger_id` points at the backing `triggers` row used as the schedule event ledger.
- `last_session_id` points at the most recent generated session instance.
- `next_run_at` is the scheduler cursor used to find due projects.

### 2.1.3 `automation_deliveries`

```sql
CREATE TABLE IF NOT EXISTS automation_deliveries (
    automation_delivery_id TEXT PRIMARY KEY,
    automation_project_id TEXT NOT NULL,
    automation_project_name TEXT NOT NULL,
    run_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    binding_json TEXT NOT NULL,
    delivery_events_json TEXT NOT NULL,
    started_status TEXT NOT NULL,
    terminal_status TEXT NOT NULL,
    terminal_event TEXT,
    started_attempts INTEGER NOT NULL,
    terminal_attempts INTEGER NOT NULL,
    started_message TEXT,
    terminal_message TEXT,
    started_sent_at TEXT,
    terminal_sent_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_automation_deliveries_project
    ON automation_deliveries(automation_project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_automation_deliveries_started
    ON automation_deliveries(started_status, updated_at ASC);
CREATE INDEX IF NOT EXISTS idx_automation_deliveries_terminal
    ON automation_deliveries(terminal_status, updated_at ASC);
```

Purpose: persists Feishu delivery state for automation runs so started/completed/failed messages can be retried and resumed after process restart.

### 2.1.4 `sessions` additions

The `sessions` table now also stores:
- `project_kind TEXT NOT NULL DEFAULT 'workspace'`
- `project_id TEXT NOT NULL DEFAULT ''`

Purpose: lets one session belong to either a regular workspace project or an automation project while preserving the existing `workspace_id` execution binding.

Notes:
- Existing rows are backfilled as `project_kind='workspace'` and `project_id=workspace_id`.
- Automation-generated sessions keep `workspace_id='automation-system'` internally, but project grouping uses `project_kind='automation'` and the automation project id.
