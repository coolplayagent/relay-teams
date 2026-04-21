# Database Schema

## 1. Storage

- Engine: SQLite
- Database file: default `~/.relay-teams/relay_teams.db`, overrideable with `RELAY_TEAMS_CONFIG_DIR`
- Foreign keys: enabled on each connection (`PRAGMA foreign_keys = ON`)
- Runtime logs are file-based and stored under the resolved config dir, by default `~/.relay-teams/log/backend.log`, `~/.relay-teams/log/debug.log`, and `~/.relay-teams/log/frontend.log`

## 1.1 Application-Layer Constraints

- SQLite tables do not currently enforce identifier-text `CHECK` constraints. The application layer rejects identifier and reference inputs that are blank, whitespace-only, or the explicit strings `"None"` and `"null"`.
- Optional identifier fields still allow real `NULL` at the API and model layer.
- Repository read paths tolerate previously persisted dirty rows for identifier-heavy tables such as `sessions`, `workspaces`, `external_session_bindings`, `session_history_markers`, `run_runtime`, `background_tasks`, `run_todos`, `monitor_subscriptions`, `monitor_triggers`, `approval_tickets`, `gateway_sessions`, `feishu_gateway_accounts`, and `wechat_accounts`.
- When those readers encounter invalid persisted identifiers or timestamps, they log a warning and skip the bad row or treat the row as missing instead of failing the whole `/api/*` request.

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
- For Feishu rows, `trigger_id` now carries the gateway `account_id`.
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
- `agent_id` references one configured entry in the resolved app config dir `agents.json`, by default `~/.relay-teams/agents.json`.
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

Task-scoped tool runtime state is also stored here under `state_key` values such as `tool_call_state:<tool_call_id>`.
Current tool-call state payloads include run/session linkage, `run_yolo`, and `approval_mode` metadata so SQLite analysis can distinguish YOLO approval bypass from policy-exempt tools.
Sanitized internal tool data may include provider or upstream host identifiers, but must not persist API-key-bearing URLs.

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

### 2.8 `feishu_gateway_accounts`

```sql
CREATE TABLE IF NOT EXISTS feishu_gateway_accounts (
    account_id          TEXT PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,
    display_name        TEXT NOT NULL,
    status              TEXT NOT NULL,
    source_config_json  TEXT NOT NULL,
    target_config_json  TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feishu_gateway_accounts_status
    ON feishu_gateway_accounts(status, updated_at DESC);
```

Purpose: persisted Feishu gateway account definitions and runtime/session targeting config.

Notes:
- Feishu `app_secret`, `verification_token`, and `encrypt_key` are stored in the unified secret store and resolved by `account_id`.
- On first boot after migration, legacy Feishu trigger rows are copied into this table and keep the old `trigger_id` value as `account_id` so existing chat bindings and queue rows continue to resolve.

`status` values:
- `enabled`
- `disabled`

---

### 2.9 `automation_execution_events`

```sql
CREATE TABLE IF NOT EXISTS automation_execution_events (
    event_id TEXT PRIMARY KEY,
    automation_project_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_automation_execution_events_project
    ON automation_execution_events(automation_project_id, created_at DESC);
```

Purpose: append-only execution ledger for automation runs after schedule trigger removal.

Notes:
- Each row is created immediately before materializing a scheduled or manual automation run.
- `reason` is `manual` or `schedule`.

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
    sender_name           TEXT,
    intent_text           TEXT NOT NULL,
    payload_json          TEXT NOT NULL,
    metadata_json         TEXT NOT NULL,
    processing_status     TEXT NOT NULL,
    reaction_status       TEXT NOT NULL DEFAULT 'pending',
    reaction_type         TEXT,
    reaction_attempts     INTEGER NOT NULL DEFAULT 0,
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

Notes:
- `trigger_id` now carries the Feishu gateway `account_id` so existing binding and queue keys remain stable during and after migration.

`processing_status` values:
- `queued`
- `claimed`
- `waiting_result`
- `retryable_failed`
- `cancelled`
- `completed`
- `ignored`
- `dead_letter`

`reaction_status`, `ack_status`, and `final_reply_status` values:
- `pending`
- `sending`
- `sent`
- `skipped`
- `failed`

Notes:
- same-chat Feishu messages are processed in sequence order
- `delivery_count` tracks repeated delivery attempts for the same dedupe key
- `run_id` links the inbound chat message to the created internal run
- `sender_name` stores the resolved Feishu display name used for group-chat intent wrapping
- `reaction_*` tracks the emoji acknowledgement lifecycle separately from queue-text replies
- `ack_text` is reserved for queue backlog replies only

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
- `cwd` stores the resolved absolute workspace root last provided by the gateway channel. For ACP stdio, `session/new.cwd` creates or reuses that workspace, and `session/load.cwd` may rebind the internal session to a different workspace when no active or recoverable run is attached.
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
- bot tokens are stored in the unified secret store, not in this table
- `sync_cursor` stores the last upstream long-poll cursor returned by WeChat
- `workspace_id`, `session_mode`, `normal_root_role_id`, and `orchestration_preset_id` define the runtime preset applied to new or resolved gateway sessions for that account
- runtime status fields such as `running` and `last_error` are computed in memory and returned by the API, not persisted in this table

### 2.10.3 `wechat_inbound_queue`

```sql
CREATE TABLE IF NOT EXISTS wechat_inbound_queue (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    inbound_queue_id   TEXT NOT NULL UNIQUE,
    account_id         TEXT NOT NULL,
    message_key        TEXT NOT NULL,
    gateway_session_id TEXT NOT NULL,
    session_id         TEXT NOT NULL,
    peer_user_id       TEXT NOT NULL,
    context_token      TEXT,
    text               TEXT NOT NULL,
    status             TEXT NOT NULL,
    run_id             TEXT,
    last_error         TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    completed_at       TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_wechat_inbound_queue_message
    ON wechat_inbound_queue(account_id, peer_user_id, message_key);

CREATE INDEX IF NOT EXISTS idx_wechat_inbound_queue_session
    ON wechat_inbound_queue(session_id, id ASC);

CREATE INDEX IF NOT EXISTS idx_wechat_inbound_queue_status
    ON wechat_inbound_queue(status, id ASC);
```

Purpose: persists inbound WeChat direct messages before they enter the shared gateway
session ingress path so same-session traffic queues deterministically and survives
process restarts.

Notes:
- `message_key` is the durable deduplication key derived from upstream message ids,
  sequence numbers, or fallback metadata
- `gateway_session_id` points back to the transport-facing WeChat gateway session row
- `status` flows through `queued`, `waiting_result`, then a terminal state
- `run_id` is populated only after the shared gateway ingress path successfully starts
  the detached run for that message
- queued WeChat messages never auto-attach to an already active session run
- `last_error` captures terminal start/reply failures for that inbound item

---

## 3. Relationship Keys

Primary query keys used by repositories:
- `session_id`: session-level retrieval across `sessions`, `external_agent_sessions`, `tasks`, `agent_instances`, `events`, `messages`, `session_history_markers`, `token_usage`, `background_tasks`, `run_todos`.
- `trace_id` (`run_id`): run-level retrieval across `tasks`, `events`, `messages`, `token_usage`, `background_tasks`, `run_todos`.
- `task_id`: task-level retrieval and task assignment tracking.
- `instance_id`: agent-level retrieval and message history.
- `trigger_id`: Feishu-account scoped retrieval across `external_session_bindings`, `feishu_message_pool`.
- `event_id`: message/event level retrieval for audit and replay preparation.
- `platform + trigger_id + tenant_key + external_chat_id`: external-chat lookup for inbound IM accounts.
- `gateway_session_id`: external channel session retrieval across `gateway_sessions`.
- `external_session_id`: channel-scoped lookup key for reconnect and session resume flows.
- `account_id`: Feishu gateway account retrieval across `feishu_gateway_accounts`.
- `account_id`: WeChat gateway account retrieval across `wechat_accounts`,
  `wechat_inbound_queue`.

---

## 3.1 Code Ownership

- `relay_teams.persistence`: shared SQLite connection setup, scope models, and `shared_state`.
- `relay_teams.sessions`: `sessions`, `external_session_bindings`, `session_history_markers`.
- `relay_teams.external_agents`: `external_agent_sessions`.
- `relay_teams.workspace`: `workspaces`.
- `relay_teams.sessions.runs`: `events`, `run_intents`, `run_runtime`, `run_states`, `run_snapshots`, `background_tasks`, `run_todos`.
- `relay_teams.monitors`: `monitor_subscriptions`, `monitor_triggers`.
- `relay_teams.agents`: `agent_instances`.
- `relay_teams.agents.tasks`: `tasks`.
- `relay_teams.agents.execution`: `messages`.
- `relay_teams.tools.runtime`: `approval_tickets`.
- `relay_teams.tools.workspace_tools`: `shell_approval_grants`.
- `relay_teams.providers`: `token_usage`.
- `relay_teams.gateway.feishu`: `feishu_gateway_accounts`, `feishu_message_pool`.
- `relay_teams.automation`: `automation_execution_events`.
- `relay_teams.gateway`: `gateway_sessions`.
- `relay_teams.gateway.wechat`: `wechat_accounts`, `wechat_inbound_queue`.
- `relay_teams.roles`: `role_memories`.

---

### 2.9 `run_intents`

```sql
CREATE TABLE IF NOT EXISTS run_intents (
    run_id         TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL,
    intent         TEXT NOT NULL,
    input_json     TEXT,
    run_kind       TEXT NOT NULL DEFAULT 'conversation',
    generation_config_json TEXT,
    execution_mode TEXT NOT NULL,
    session_mode   TEXT NOT NULL DEFAULT 'normal',
    yolo           TEXT NOT NULL DEFAULT 'false',
    thinking_enabled TEXT NOT NULL DEFAULT 'false',
    thinking_effort TEXT,
    target_role_id TEXT,
    topology_json  TEXT,
    conversation_context_json TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_intents_session ON run_intents(session_id);
```

Purpose: stores the run input and per-run execution settings needed for queued runs and recoverable resume paths.

Notes:
- `intent` remains a text summary used for previews and logs.
- `input_json` stores the canonical typed run input array, including text and media references.
- `run_kind` distinguishes `conversation`, `generate_image`, `generate_audio`, and `generate_video`.
- `generation_config_json` stores the typed native media-generation config for provider-native image/audio/video runs.
- `yolo` controls whether tool approvals are skipped entirely for that run.
- `thinking_enabled` and `thinking_effort` capture per-run thinking configuration for providers that support reasoning streams.
- `target_role_id` stores an optional one-run direct-chat override, such as a leading `@Role` mention from the web composer.
- `session_mode` and `topology_json` snapshot the resolved root-agent topology, including the selected normal-mode root role, used when the run was created, so recoverable resumes do not drift when global orchestration settings change later.
- `conversation_context_json` stores optional source-channel context, including Feishu group-chat markers used by runtime prompt assembly and the automation direct-send override used by IM-bound scheduled runs.

---

### 2.9.0 `approval_tickets` and `shell_approval_grants`

`approval_tickets` persists pending and reusable tool-approval records. Shell tickets also store `metadata_json`, which carries normalized command data used when the operator resolves a pending shell approval as `approve_exact` or `approve_prefix`.

`shell_approval_grants` stores project-scoped reusable shell approvals keyed by:
- `workspace_key`
- `runtime_family`
- `scope` (`exact` or `prefix`)
- `value`

These grants are local-only runtime permissions. They are separate from per-run approval tickets and are used only by the shell tool in non-`yolo` runs.

---

### 2.9.1 `background_tasks`

```sql
CREATE TABLE IF NOT EXISTS background_tasks (
    background_task_id  TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL,
    session_id          TEXT NOT NULL,
    kind                TEXT NOT NULL DEFAULT 'command',
    instance_id         TEXT,
    role_id             TEXT,
    tool_call_id        TEXT,
    title               TEXT NOT NULL DEFAULT '',
    command             TEXT NOT NULL,
    cwd                 TEXT NOT NULL,
    execution_mode      TEXT NOT NULL,
    status              TEXT NOT NULL,
    tty                 INTEGER NOT NULL,
    pid                 INTEGER,
    timeout_ms          INTEGER,
    exit_code           INTEGER,
    recent_output_json  TEXT NOT NULL,
    output_excerpt      TEXT NOT NULL,
    log_path            TEXT NOT NULL,
    subagent_role_id    TEXT,
    subagent_run_id     TEXT,
    subagent_task_id    TEXT,
    subagent_instance_id TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    completed_at        TEXT,
    completion_notified_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_background_tasks_run
    ON background_tasks(run_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_background_tasks_status
    ON background_tasks(status, updated_at DESC);
```

Purpose: durable metadata for managed background tasks bound to one run.

Notes:
- `execution_mode` is currently fixed to `background`.
- `kind` is `command` for managed shell work and `subagent` for one-shot background subagent runs created by the normal-mode `spawn_subagent` tool.
- `title` stores the operator-facing label used in recovery/UI lists. Command tasks may leave it empty; subagent tasks should set it.
- `status` is one of `running`, `blocked`, `stopped`, `failed`, or `completed`.
- `pid` is populated only for OS-backed command tasks.
- `recent_output_json` stores recent non-empty output lines for recovery/UI. `output_excerpt` stores the bounded head/tail excerpt used by tool results and session detail views. The full stream is persisted to the workspace-scoped file at `log_path`.
- Subagent rows keep `command` as a synthetic value like `subagent:<role_id>` for compatibility with existing projections, and also persist the concrete synthetic run/task/instance linkage in `subagent_role_id`, `subagent_run_id`, `subagent_task_id`, and `subagent_instance_id`.
- `completion_notified_at` records when the runtime successfully emitted the background-task completion follow-up into the parent run or recovery flow.
- Startup recovery marks non-terminal rows as interrupted/stopped rather than attempting to reattach to old OS processes.

---

### 2.9.1.1 `run_todos`

```sql
CREATE TABLE IF NOT EXISTS run_todos (
    run_id                 TEXT PRIMARY KEY,
    session_id             TEXT NOT NULL,
    items_json             TEXT NOT NULL,
    version                INTEGER NOT NULL,
    updated_at             TEXT NOT NULL,
    updated_by_role_id     TEXT,
    updated_by_instance_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_run_todos_session
    ON run_todos(session_id, updated_at DESC);
```

Purpose: latest persisted run-scoped todo snapshot for one run.

Notes:
- `items_json` stores the full ordered todo table, not incremental patches.
- Each item includes `content` plus `status = pending | in_progress | completed`.
- The application layer enforces at most one `in_progress` row and rejects oversized payloads.
- `version` increments on every successful full-table write, including clears.
- The runtime returns a synthetic empty snapshot when no `run_todos` row exists yet, but persistence only occurs after the first successful `todo_write`.

---

### 2.9.1.2 `monitor_subscriptions`

```sql
CREATE TABLE IF NOT EXISTS monitor_subscriptions (
    monitor_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_key TEXT NOT NULL,
    created_by_instance_id TEXT,
    created_by_role_id TEXT,
    tool_call_id TEXT,
    status TEXT NOT NULL,
    rule_json TEXT NOT NULL,
    action_json TEXT NOT NULL,
    trigger_count INTEGER NOT NULL DEFAULT 0,
    last_triggered_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    stopped_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_monitor_subscriptions_run
    ON monitor_subscriptions(run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_monitor_subscriptions_source
    ON monitor_subscriptions(source_kind, source_key, status, created_at DESC);
```

Purpose: durable run-scoped monitor definitions that subscribe one run to one event source.

Notes:
- `source_kind` is currently `background_task` or `github`.
- `source_key` is source-specific, currently a managed `background_task_id` or a GitHub repository full name such as `owner/repo`.
- `rule_json` stores the deterministic match contract (`event_names`, `text_patterns_any`, attribute filters, cooldown, and trigger caps).
- `action_json` stores the follow-up action contract (`wake_instance`, `wake_coordinator`, `start_followup_run`, or `emit_notification`).
- `created_by_instance_id`, `created_by_role_id`, and `tool_call_id` preserve provenance for routing and audit.

---

### 2.9.1.2 `monitor_triggers`

```sql
CREATE TABLE IF NOT EXISTS monitor_triggers (
    monitor_trigger_id TEXT PRIMARY KEY,
    monitor_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_key TEXT NOT NULL,
    event_name TEXT NOT NULL,
    dedupe_key TEXT,
    body_text TEXT NOT NULL,
    attributes_json TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL,
    action_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_monitor_triggers_monitor
    ON monitor_triggers(monitor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_monitor_triggers_dedupe
    ON monitor_triggers(monitor_id, dedupe_key);
```

Purpose: immutable audit log of matched monitor events.

Notes:
- `dedupe_key` is optional but enables at-most-once behavior per monitor when event sources provide a stable delivery or line key.
- `attributes_json` stores the normalized event envelope used for deterministic matching and later inspection.
- `raw_payload_json` preserves the source-native normalized payload used to wake the run or emit a notification.

---

### 2.9.1.3 GitHub Trigger Management Tables

```sql
CREATE TABLE IF NOT EXISTS github_trigger_accounts (
    account_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL,
    token_configured INTEGER NOT NULL DEFAULT 0,
    webhook_secret_configured INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS github_repo_subscriptions (
    repo_subscription_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    repo_name TEXT NOT NULL,
    full_name TEXT NOT NULL,
    external_repo_id TEXT,
    default_branch TEXT,
    callback_url TEXT,
    provider_webhook_id TEXT,
    subscribed_events_json TEXT NOT NULL DEFAULT '[]',
    webhook_status TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_webhook_sync_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(account_id, full_name)
);

CREATE TABLE IF NOT EXISTS trigger_rules (
    trigger_rule_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    account_id TEXT NOT NULL,
    repo_subscription_id TEXT NOT NULL,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    match_config_json TEXT NOT NULL,
    dispatch_config_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    last_error TEXT,
    last_fired_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(repo_subscription_id, name)
);
```

Purpose: durable GitHub webhook account, repository, and rule configuration for PR/issue-driven automation.

Notes:
- `github_trigger_accounts.token_configured` and `webhook_secret_configured` track only per-account stored secrets; runtime token lookup may still fall back to the system GitHub config.
- `github_repo_subscriptions.callback_url` is the persisted webhook callback endpoint used during automatic registration and re-registration.
- `github_repo_subscriptions.subscribed_events_json` is derived from the union of enabled rule `match_config.event_name` values for that repository, not a client-managed source of truth.
- `github_repo_subscriptions.webhook_status` is `unregistered`, `registered`, or `error` based on the latest reconciliation attempt.
- Disabling an account, disabling a repository, or leaving a repository with no enabled rules drives the repository back toward the `unregistered` state.

---

### 2.9.2 `media_assets`

```sql
CREATE TABLE IF NOT EXISTS media_assets (
    asset_id            TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL,
    workspace_id        TEXT NOT NULL,
    storage_kind        TEXT NOT NULL,
    modality            TEXT NOT NULL,
    mime_type           TEXT NOT NULL,
    name                TEXT NOT NULL DEFAULT '',
    relative_path       TEXT,
    external_url        TEXT,
    size_bytes          INTEGER,
    width               INTEGER,
    height              INTEGER,
    duration_ms         INTEGER,
    thumbnail_asset_id  TEXT,
    source              TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_media_assets_session
    ON media_assets(session_id, created_at ASC);
```

Purpose: session-scoped media asset registry used by typed run input/output, ACP transport, and frontend rendering.

Notes:
- `storage_kind` is `local` or `remote`.
- Local assets are stored under the session artifact directory inside the owning workspace.
- Remote assets keep their external URL and are still projected as normalized `media_ref` content parts.
- Session deletion removes both the DB rows and the session artifact subtree.

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

### 2.8 `retrieval_scopes`

```sql
CREATE TABLE IF NOT EXISTS retrieval_scopes (
    scope_kind     TEXT NOT NULL,
    scope_id       TEXT NOT NULL,
    backend        TEXT NOT NULL,
    tokenizer      TEXT NOT NULL,
    title_weight   REAL NOT NULL,
    body_weight    REAL NOT NULL,
    keyword_weight REAL NOT NULL,
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (scope_kind, scope_id)
);
```

Purpose: stores typed retrieval-scope configuration for local full-text indexes. Each scope maps one logical corpus such as `skill`, `memory`, `mcp`, or `file` to one retrieval backend and tokenizer strategy.

Notes:
- current backend is `sqlite_fts5`
- tokenizer is currently `unicode61` or `trigram`
- weights are query-time BM25 field weights for `title`, `body`, and `keywords`

### 2.9 `retrieval_documents`

```sql
CREATE TABLE IF NOT EXISTS retrieval_documents (
    rowid       INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_kind  TEXT NOT NULL,
    scope_id    TEXT NOT NULL,
    document_id TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    keywords    TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (scope_kind, scope_id, document_id),
    FOREIGN KEY (scope_kind, scope_id)
        REFERENCES retrieval_scopes(scope_kind, scope_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_retrieval_documents_scope
    ON retrieval_documents(scope_kind, scope_id, updated_at);
```

Purpose: durable content rows that back scope-local retrieval indexes.

Notes:
- `document_id` is the stable caller-owned identifier inside one retrieval scope
- `keywords` stores the normalized keyword text used as the third BM25 field
- FTS virtual tables read from this table through `content='retrieval_documents'`

### 2.10 `retrieval_fts_unicode61` / `retrieval_fts_trigram`

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS retrieval_fts_unicode61
USING fts5(
    scope_kind UNINDEXED,
    scope_id UNINDEXED,
    document_id UNINDEXED,
    title,
    body,
    keywords,
    content='retrieval_documents',
    content_rowid='rowid',
    tokenize='unicode61',
    detail='column'
);

CREATE VIRTUAL TABLE IF NOT EXISTS retrieval_fts_trigram
USING fts5(
    scope_kind UNINDEXED,
    scope_id UNINDEXED,
    document_id UNINDEXED,
    title,
    body,
    keywords,
    content='retrieval_documents',
    content_rowid='rowid',
    tokenize='trigram',
    detail='column'
);
```

Purpose: reusable SQLite FTS5 indexes for retrieval scopes. The runtime chooses one table per scope based on tokenizer configuration and ranks hits with SQLite `bm25(...)`.

Notes:
- `scope_kind`, `scope_id`, and `document_id` are stored as `UNINDEXED` metadata columns for filtering and result projection
- rows are synchronized by the retrieval store layer instead of generic database triggers
- current observability design intentionally avoids storing raw query text in metrics or trace attributes

### 2.11 `metric_points`

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
- `delivery_binding_json` stores the selected Feishu chat target plus the exact bound `session_id` chosen from the current Feishu binding candidates.
- `delivery_events_json` stores which Feishu notifications are enabled for that automation project.
- `trigger_id` is a legacy compatibility field and now stores `schedule-{automation_project_id}`.
- `last_session_id` points at the most recent session used by that automation project, including a reused bound IM session.
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
    reply_to_message_id TEXT,
    started_message_id TEXT,
    terminal_message_id TEXT,
    started_sent_at TEXT,
    terminal_sent_at TEXT,
    started_cleanup_status TEXT NOT NULL DEFAULT 'skipped',
    started_cleanup_attempts INTEGER NOT NULL DEFAULT 0,
    started_cleaned_at TEXT,
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

Notes:
- `reply_to_message_id` stores the persisted receipt that later automation output should reply to when the run did not create its own started receipt.
- `started_message_id` and `terminal_message_id` store the provider `message_id` returned by Feishu for sent automation messages.
- `started_cleanup_status`, `started_cleanup_attempts`, and `started_cleaned_at` remain for compatibility with older rows, but new receipts are not automatically deleted by the current cleanup policy.
- terminal messages are persisted but are not automatically deleted by the current cleanup policy.

### 2.1.4 `automation_bound_session_queue`

```sql
CREATE TABLE IF NOT EXISTS automation_bound_session_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    automation_queue_id TEXT NOT NULL UNIQUE,
    automation_project_id TEXT NOT NULL,
    automation_project_name TEXT NOT NULL,
    session_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    binding_json TEXT NOT NULL,
    delivery_events_json TEXT NOT NULL,
    run_config_json TEXT NOT NULL,
    prompt TEXT NOT NULL,
    queue_message TEXT NOT NULL,
    run_id TEXT UNIQUE,
    status TEXT NOT NULL,
    start_attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    resume_attempts INTEGER NOT NULL DEFAULT 0,
    resume_next_attempt_at TEXT NOT NULL,
    queue_message_id TEXT,
    queue_cleanup_status TEXT NOT NULL DEFAULT 'skipped',
    queue_cleanup_attempts INTEGER NOT NULL DEFAULT 0,
    queue_cleaned_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_automation_bound_session_queue_session
    ON automation_bound_session_queue(session_id, id ASC);
CREATE INDEX IF NOT EXISTS idx_automation_bound_session_queue_status
    ON automation_bound_session_queue(status, next_attempt_at, id ASC);
CREATE INDEX IF NOT EXISTS idx_automation_bound_session_queue_project
    ON automation_bound_session_queue(automation_project_id, created_at DESC);
```

Purpose: persists scheduled/manual automation runs that are bound to an existing IM
session so they can queue behind that session's current run, survive restarts, and
resume result delivery after they finally start.

Notes:
- rows are created only for automation runs that reuse a bound IM session and cannot
  start immediately
- `prompt` stores the queued prompt after the `定时任务触发：{display_name}` prefix is added
- `queue_message` stores the already-rendered receipt sent to the bound chat when the
  run is queued
- `run_id` is populated only after the queued item has successfully started
- `status` flows through `queued`, `starting`, `waiting_result`, then a terminal state
- `resume_attempts` and `resume_next_attempt_at` persist the auto-resume retry state for recoverable `awaiting_recovery` runs bound to that session
- `queue_message_id` stores the Feishu provider `message_id` for the queue receipt
- `queue_cleanup_status`, `queue_cleanup_attempts`, and `queue_cleaned_at` remain for compatibility with older rows, but current queue receipts are retained in chat instead of being auto-deleted

### 2.1.5 `sessions` additions

The `sessions` table now also stores:
- `project_kind TEXT NOT NULL DEFAULT 'workspace'`
- `project_id TEXT NOT NULL DEFAULT ''`

Purpose: lets one session belong to either a regular workspace project or an automation project while preserving the existing `workspace_id` execution binding.

Notes:
- Existing rows are backfilled as `project_kind='workspace'` and `project_id=workspace_id`.
- Automation-generated sessions keep `workspace_id='automation-system'` internally, but project grouping uses `project_kind='automation'` and the automation project id.
