# Database Schema

## 1. Storage

- Engine: SQLite
- Database file: default `~/.relay-teams/relay_teams.db`, overrideable with `RELAY_TEAMS_CONFIG_DIR`
- Foreign keys: enabled on each connection (`PRAGMA foreign_keys = ON`)
- Runtime logs are file-based and stored under the resolved config dir, by default `~/.relay-teams/log/backend.log`, `~/.relay-teams/log/debug.log`, and `~/.relay-teams/log/frontend.log`

## 1.1 Application-Layer Constraints

- SQLite tables do not currently enforce identifier-text `CHECK` constraints. The application layer rejects identifier and reference inputs that are blank, whitespace-only, or the explicit strings `"None"` and `"null"`.
- Optional identifier fields still allow real `NULL` at the API and model layer.
- Repository read paths tolerate previously persisted dirty rows for identifier-heavy tables such as `sessions`, `workspaces`, `external_session_bindings`, `session_history_markers`, `run_runtime`, `background_tasks`, `run_todos`, `monitor_subscriptions`, `monitor_triggers`, `approval_tickets`, `gateway_sessions`, `feishu_gateway_accounts`, `wechat_accounts`, `discord_accounts`, and `task_spec_artifacts`.
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
    last_viewed_terminal_run_id TEXT,
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
- `last_viewed_terminal_run_id` stores the latest terminal top-level run the user has opened, so the sidebar can distinguish newly finished runs from already-viewed sessions.

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

Purpose: persistent mapping between one internal `session_id + role_id` pair and the reused remote ACP session created for the bound external agent runtime.

Notes:
- `agent_id` references one configured entry in the resolved app config dir `agents.json`, by default `~/.relay-teams/agents.json`.
- `transport` stores the outbound ACP transport type used by that saved agent config.
- `external_session_id` is the remote ACP session identifier returned by the external agent and reused for later turns in the same internal session.
- `status` stores the last-known remote session health, currently `ready` or `failed`.
- A2A and CLI agent runtimes are configured in `agents.json` but do not write rows here because they do not expose reusable ACP session identifiers.

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
    lifecycle             TEXT NOT NULL DEFAULT 'reusable',
    parent_instance_id    TEXT,
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
- Runtime semantics distinguish reusable session role instances from ephemeral clones.
- `run_id` / `trace_id` are last-observed execution metadata, not uniqueness keys.
- New non-concurrent dispatches for the same `session_id + role_id` reuse the existing `reusable` row.
- Same-role concurrent dispatches may create `ephemeral` clone rows whose `parent_instance_id` points at the reusable instance.
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
CREATE INDEX IF NOT EXISTS idx_tasks_session_trace
    ON tasks(session_id, trace_id, created_at);
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

Optional fields:
- `spec: TaskSpec | null`
- `spec_artifact_id: string | null`
- `spec_source_task_id: string | null`
- `evidence_bundle: VerificationEvidenceBundle | null`
- `lifecycle: TaskLifecyclePolicy`
- `handoff: TaskHandoff | null`
- `depends_on_task_ids: string[]`
- `orchestration_node_id: string | null`

Notes:
- `role_id` is the execution target for the task.
- `title` is a persisted task summary used by session projections and task APIs.
- `lifecycle.spec_checkpoint` is stored inside `envelope_json` and controls automatic Spec Checkpoint refresh thresholds for long non-coordinator executions.
- `spec_artifact_id` links the task envelope to the current versioned row in `task_spec_artifacts`.
- `spec_source_task_id` records the upstream task whose specification this task derives from.
- `evidence_bundle` stores normalized verification evidence generated from checklist, file, command, spec, and formal-verification checks.
- The system does not store a separate workflow graph table. `tasks` is the
  durable orchestration DAG source of truth: `orchestration_node_id` identifies
  graph nodes, and `depends_on_task_ids` stores the resolved dependency edges.
- Fixed orchestration presets materialize their `graph` template as task rows.
  Dynamic Coordinator-created DAGs and automatic planner lanes use the same task
  row shape and recovery semantics.
- Automatic DelegationPlanner planning uses `orchestration_node_id` values such as
  `auto_plan` and `auto_lane_*` to make generated planner and lane tasks
  recoverable and to prevent duplicate planning on resume.
- Role behavioral contracts are not copied into `tasks.envelope_json`. The task
  stores only the selected `role_id`; dispatch and verification resolve the
  current role definition and apply its `contract` at runtime.

---

### 2.3.1 `task_spec_artifacts`

```sql
CREATE TABLE IF NOT EXISTS task_spec_artifacts (
    artifact_id    TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL,
    trace_id       TEXT NOT NULL,
    session_id     TEXT NOT NULL,
    source_task_id TEXT,
    spec_json      TEXT NOT NULL,
    version        INTEGER NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_spec_artifacts_task
    ON task_spec_artifacts(task_id, version);
CREATE INDEX IF NOT EXISTS idx_task_spec_artifacts_session
    ON task_spec_artifacts(session_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_task_spec_artifacts_trace
    ON task_spec_artifacts(trace_id, updated_at);
```

Purpose: durable, versioned task specifications. A task envelope carries the currently bound `spec_artifact_id`; the artifact table keeps the persisted spec payload stable for audit, prompt reconstruction, and downstream source links.

Notes:
- `artifact_id` is generated as `spec-{uuid}`.
- `version` increments per task when a task spec changes.
- `source_task_id` links derived tasks back to the upstream spec-bearing task.
- `spec_json` stores `TaskSpec`, including REASONS Canvas fields, prompt/code sync status, strictness, evidence expectations, and optional formal verification plan metadata.
- Deleting a task or session removes its associated spec artifact rows.

---

### 2.3.2 `spec_checkpoint_evaluations`

```sql
CREATE TABLE IF NOT EXISTS spec_checkpoint_evaluations (
    evaluation_id   TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL,
    artifact_id     TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    trace_id        TEXT NOT NULL,
    checkpoint_seq  INTEGER NOT NULL,
    evaluator       TEXT NOT NULL DEFAULT 'llm',
    fallback        INTEGER NOT NULL DEFAULT 0,
    overall_score   REAL NOT NULL,
    scores_json     TEXT NOT NULL,
    summary         TEXT NOT NULL DEFAULT '',
    drift_detected  INTEGER NOT NULL DEFAULT 0,
    drift_detail    TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_spec_checkpoint_evaluations_task
    ON spec_checkpoint_evaluations(task_id, checkpoint_seq);

CREATE INDEX IF NOT EXISTS idx_spec_checkpoint_evaluations_artifact
    ON spec_checkpoint_evaluations(artifact_id);
```

Purpose: stores LLM drift-detection evaluation results produced when spec checkpoints are rendered with `auto_evaluate_drift` enabled in `SpecCheckpointPolicy`. Each row represents one evaluation of a spec checkpoint against its task spec.

Column descriptions:
- `evaluation_id`: primary key, generated as `speval-{uuid}`.
- `task_id`: reference to the evaluated task. Indexed for efficient per-task queries.
- `artifact_id`: the `TaskSpecArtifact.artifact_id` that was current when the checkpoint was rendered.
- `session_id`: session in which the evaluation occurred.
- `trace_id`: trace identifier for observability correlation.
- `checkpoint_seq`: corresponds to the `sequence` field in `SpecCheckpointDecision`. Indexed alongside `task_id` for sequence-ordered retrieval.
- `evaluator`: evaluator type identifier (default `'llm'`).
- `fallback`: boolean as integer (0/1). Set to 1 when the evaluation used rule-based fallback due to LLM failure or timeout.
- `overall_score`: composite score across all evaluation dimensions (range 0.0-5.0).
- `scores_json`: serialized `list[LLMEvaluationScore]` from the evaluator result, containing per-dimension scores with reasoning.
- `summary`: human-readable evaluation summary text.
- `drift_detected`: boolean as integer (0/1). Set to 1 when `overall_score` falls below the configured `drift_score_threshold` or when the evaluator summary contains drift indicators.
- `drift_detail`: structured JSON string describing which dimensions flagged drift, for frontend consumption.
- `created_at`: ISO 8601 timestamp of evaluation creation.

Relationships:
- Each row references a `task_spec_artifacts` row via `artifact_id`.
- Each row references a `tasks` row via `task_id`.
- Deleting a task or session removes its associated evaluation rows.

Notes:
- No schema migration is required for existing tables. The `task_spec_artifacts` table already stores all data needed for diff computation via its `version` column.
- Evaluation records are non-blocking: failures set `fallback=1` and persist a degraded result rather than raising errors.

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

Task-scoped tool runtime state is also stored here under `state_key` values such as `tool_call_state:<tool_call_id>` and `tool_call_batch:<batch_id>`.
Current tool-call state payloads include run/session linkage, batch linkage, result event ids, `run_yolo`, and `approval_mode` metadata so SQLite analysis can distinguish YOLO approval bypass from policy-exempt tools.
Tool-call batch payloads group all tool calls emitted by one assistant response, including parallel tool calls, so crash recovery can replay only complete sealed batches.
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
Tool call and tool result events are the durable source used to rebuild missing `tool_call_state:*` and `tool_call_batch:*` shared-state entries after a forced backend stop.
`spec_checkpoint_applied` events are durable observability markers emitted after an internal spec-refresh system prompt is persisted for the next model request.

---

### 2.5.1 `security_audit_events`

```sql
CREATE TABLE IF NOT EXISTS security_audit_events (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_event_id     TEXT NOT NULL UNIQUE,
    event_type         TEXT NOT NULL,
    trace_id           TEXT NOT NULL,
    run_id             TEXT NOT NULL,
    session_id         TEXT NOT NULL,
    task_id            TEXT,
    instance_id        TEXT,
    role_id            TEXT,
    tool_call_id       TEXT,
    span_id            TEXT,
    parent_span_id     TEXT,
    action             TEXT NOT NULL,
    target             TEXT NOT NULL,
    content_digest     TEXT,
    content_size_bytes INTEGER,
    command            TEXT,
    decision_reason    TEXT,
    outcome            TEXT NOT NULL,
    metadata_json      TEXT NOT NULL,
    occurred_at        TEXT NOT NULL,
    created_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_security_audit_events_type_id
    ON security_audit_events(event_type, id);
CREATE INDEX IF NOT EXISTS idx_security_audit_events_trace_id
    ON security_audit_events(trace_id, id);
CREATE INDEX IF NOT EXISTS idx_security_audit_events_run_id
    ON security_audit_events(run_id, id);
CREATE INDEX IF NOT EXISTS idx_security_audit_events_session_id
    ON security_audit_events(session_id, id);
CREATE INDEX IF NOT EXISTS idx_security_audit_events_task_id
    ON security_audit_events(task_id, id);
CREATE INDEX IF NOT EXISTS idx_security_audit_events_role_id
    ON security_audit_events(role_id, id);
CREATE INDEX IF NOT EXISTS idx_security_audit_events_time_id
    ON security_audit_events(occurred_at, id);
```

Purpose: immutable security/compliance audit log separate from the business run event stream.

Notes:
- `event_type` is `file_write`, `shell_command`, or `coordinator_decision`.
- File write events record the logical path, final content SHA-256 digest, byte size, role, task, run, and tool call context. Raw file content is not stored.
- Shell command events record the command string plus execution context and result metadata such as `exit_code` when available.
- Coordinator decision events record `orch_dispatch_task` selections as the task-to-role channel decision plus the dispatch prompt/reason, capped by the application layer.
- `span_id` and `parent_span_id` bind each row to a `security.audit` trace span.
- `occurred_at` and `created_at` are stored as UTC ISO 8601 text so SQLite range filters are stable across client-provided offsets.
- Repositories expose append and list operations only; there is no source path for Agent tools to update or delete audit rows.

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
CREATE INDEX IF NOT EXISTS idx_messages_session_role_id
    ON messages(session_id, role, id);
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
- Runtime-internal guidance, including automatic Spec Checkpoint refreshes, may be stored as `ModelRequest` rows whose message part is `system-prompt`; the `role` column remains `user` because it describes the persisted Pydantic message envelope, not the semantic prompt authority.

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
- `marker_type` currently includes `clear` and `compaction`.
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
- `channel_type` identifies the transport-facing gateway implementation and currently includes `acp_stdio`, `discord`, `wechat`, and `xiaoluban`.
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
- queued WeChat messages never auto-attach to an already active session run
- `last_error` captures terminal start/reply failures for that inbound item

---

### 2.10.4 `discord_accounts`

```sql
CREATE TABLE IF NOT EXISTS discord_accounts (
    account_id              TEXT PRIMARY KEY,
    display_name            TEXT NOT NULL,
    status                  TEXT NOT NULL,
    bot_user_id             TEXT,
    application_id          TEXT,
    allowed_channel_ids_json TEXT NOT NULL,
    allow_channel_messages  INTEGER NOT NULL,
    workspace_id            TEXT NOT NULL,
    session_mode            TEXT NOT NULL,
    normal_root_role_id     TEXT,
    orchestration_preset_id TEXT,
    yolo                    INTEGER NOT NULL,
    shell_safety_policy_enabled INTEGER NOT NULL DEFAULT 1,
    thinking_json           TEXT NOT NULL,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);
```

Purpose: persisted Discord bot account configuration and runtime/session targeting config.

Notes:
- Discord bot tokens are stored in the unified secret store and resolved by `account_id`; they are not stored in this table.
- `account_id` is the Discord bot user id returned by Discord's current-user API.
- `allowed_channel_ids_json` stores guild channel ids that may send non-mention messages when `allow_channel_messages = 1`.
- Direct messages and guild mentions are accepted independently of `allowed_channel_ids_json`.
- `workspace_id`, `session_mode`, `normal_root_role_id`, and `orchestration_preset_id` define the runtime preset applied to new or resolved gateway sessions for that account.
- `shell_safety_policy_enabled` controls whether queued Discord runs keep the local shell safety deny layer enabled.
- runtime status fields such as `running`, `last_error`, and timestamps for last inbound/outbound activity are computed in memory and returned by the API, not persisted in this table.

`status` values:
- `enabled`
- `disabled`

### 2.10.5 `discord_inbound_queue`

```sql
CREATE TABLE IF NOT EXISTS discord_inbound_queue (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    inbound_queue_id    TEXT NOT NULL UNIQUE,
    account_id          TEXT NOT NULL,
    message_key         TEXT NOT NULL,
    gateway_session_id  TEXT NOT NULL,
    session_id          TEXT NOT NULL,
    peer_user_id        TEXT NOT NULL,
    channel_id          TEXT NOT NULL,
    guild_id            TEXT,
    thread_id           TEXT,
    reply_to_message_id TEXT,
    text                TEXT NOT NULL,
    status              TEXT NOT NULL,
    run_id              TEXT,
    last_error          TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    completed_at        TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_discord_inbound_queue_message
    ON discord_inbound_queue(account_id, channel_id, message_key);

CREATE INDEX IF NOT EXISTS idx_discord_inbound_queue_session
    ON discord_inbound_queue(session_id, id ASC);

CREATE INDEX IF NOT EXISTS idx_discord_inbound_queue_status
    ON discord_inbound_queue(status, id ASC);
```

Purpose: persists accepted Discord inbound messages before they enter the shared
gateway session ingress path so same-session traffic queues deterministically and
survives process restarts.

Notes:
- `message_key` is normally `mid:{discord_message_id}`.
- `channel_id` stores the reply channel. For thread messages this is the thread id; `thread_id` also stores the source thread id.
- `gateway_session_id` points back to the Discord gateway session row.
- `status` flows through `queued`, `starting`, `waiting_result`, then `completed` or `failed`.
- `run_id` is populated only after the shared gateway ingress path successfully starts.
- queued Discord messages never auto-attach to an already active session run.
- `last_error` captures terminal start/reply failures for that inbound item.

---

### 2.10.6 `xiaoluban_accounts`

```sql
CREATE TABLE IF NOT EXISTS xiaoluban_accounts (
    account_id                          TEXT PRIMARY KEY,
    display_name                        TEXT NOT NULL,
    base_url                            TEXT NOT NULL,
    status                              TEXT NOT NULL,
    derived_uid                         TEXT NOT NULL,
    notification_workspace_ids_json     TEXT NOT NULL DEFAULT '[]',
    notification_receiver               TEXT,
    notification_receivers_json         TEXT NOT NULL DEFAULT '[]',
    notify_self                         INTEGER NOT NULL DEFAULT 1,
    im_config_json                      TEXT NOT NULL DEFAULT '{}',
    created_at                          TEXT NOT NULL,
    updated_at                          TEXT NOT NULL
);
```

Purpose: persisted Xiaoluban outbound notification and inbound IM account configuration.

Notes:
- Personal tokens are stored in the unified secret store and resolved by `account_id`; they are not stored in this table.
- `notification_receivers_json` stores the normalized list of Xiaoluban group ids used for completion notifications.
- `notify_self` is retained as a compatibility column but Xiaoluban completion notifications always include the token owner's `derived_uid`.
- `notification_receiver` is a legacy compatibility column. Rows with only this old column populated are loaded as one group receiver and still notify the token owner.
- `notification_workspace_ids_json` scopes normal workspace completion notifications; an empty list disables those notifications for the account.
- `im_config_json` currently stores the Xiaoluban IM workspace id used for inbound forwarded messages.

`status` values:
- `enabled`
- `disabled`

---

## 3. Relationship Keys

Primary query keys used by repositories:
- `session_id`: session-level retrieval across `sessions`, `external_agent_sessions`, `tasks`, `agent_instances`, `events`, `security_audit_events`, `messages`, `session_history_markers`, `token_usage`, `background_tasks`, `run_todos`.
- `trace_id` (`run_id`): run-level retrieval across `tasks`, `task_spec_artifacts`, `events`, `security_audit_events`, `messages`, `token_usage`, `background_tasks`, `run_todos`.
- `task_id`: task-level retrieval and task assignment/spec tracking.
- `artifact_id`: task spec artifact lookup.

### board_configs (Phase 3 OP-11: Task Board as State Machine)

Board configurations are currently held in-memory via `TaskBoardConfig` models. Future persistence may add a `board_configs` table with columns:

- `board_id` (TEXT PK) -- board identifier
- `adapter` (TEXT NOT NULL) -- adapter type ("internal", "github", "linear")
- `config_json` (TEXT NOT NULL) -- serialized `TaskBoardConfig`
- `created_at` (TEXT NOT NULL)
- `updated_at` (TEXT NOT NULL)
- `instance_id`: agent-level retrieval and message history.
- `trigger_id`: Feishu-account scoped retrieval across `external_session_bindings`, `feishu_message_pool`.
- `event_id`: message/event/audit level retrieval for audit and replay preparation.
- `platform + trigger_id + tenant_key + external_chat_id`: external-chat lookup for inbound IM accounts.
- `gateway_session_id`: external channel session retrieval across `gateway_sessions`.
- `external_session_id`: channel-scoped lookup key for reconnect and session resume flows.
- `account_id`: Feishu gateway account retrieval across `feishu_gateway_accounts`.
- `account_id`: WeChat gateway account retrieval across `wechat_accounts`,
  `wechat_inbound_queue`.
- `account_id`: Discord gateway account retrieval across `discord_accounts`,
  `discord_inbound_queue`.
- `account_id`: Xiaoluban gateway account retrieval across `xiaoluban_accounts`.

---

## 3.1 Code Ownership

- `relay_teams.persistence`: shared SQLite connection setup, scope models, and `shared_state`.
- `relay_teams.sessions`: `sessions`, `external_session_bindings`, `session_history_markers`.
- `relay_teams.agent_runtimes`: `external_agent_sessions`, `agent_instances`.
- `relay_teams.workspace`: `workspaces`.
- `relay_teams.sessions.runs`: `events`, `run_intents`, `run_runtime`, `run_states`, `run_snapshots`, `background_tasks`, `run_todos`.
- `relay_teams.boards`: `board_todo_items`.
- `relay_teams.audit`: `security_audit_events`.
- `relay_teams.monitors`: `monitor_subscriptions`, `monitor_triggers`.
- `relay_teams.agents.tasks`: `tasks`, `task_spec_artifacts`.
- `relay_teams.agents.execution`: `messages`.
- `relay_teams.tools.runtime`: `approval_tickets`.
- `relay_teams.tools.workspace_tools`: `shell_approval_grants`.
- `relay_teams.providers`: `token_usage`.
- `relay_teams.gateway.feishu`: `feishu_gateway_accounts`, `feishu_message_pool`.
- `relay_teams.automation`: `automation_execution_events`.
- `relay_teams.gateway`: `gateway_sessions`.
- `relay_teams.gateway.wechat`: `wechat_accounts`, `wechat_inbound_queue`.
- `relay_teams.gateway.discord`: `discord_accounts`, `discord_inbound_queue`.
- `relay_teams.gateway.xiaoluban`: `xiaoluban_accounts`.
- `relay_teams.connector`: no SQLite tables. Connector status is derived from
  `triggers` GitHub rows, the existing gateway account tables, and W3 connector
  metadata stored under the app config directory. W3 writes non-sensitive JSON
  configuration to `connectors/w3.json`, stores its password in the unified
  secret store, and never persists the raw MaaS `cloudDragonTokens.authToken`.
  That token is resolved on demand as the W3 `WEB_TOKEN` / request
  `X-Auth-Token`. MaaS and CodeAgent password model profiles may store
  `auth_source = "w3"` in `model.json`; that is only a reference to the W3
  connector credentials, so W3 password updates do not rewrite model profile
  secrets.
- `relay_teams.memory`: `memory_entries`.
- Role document files: role Markdown front matter stores `RoleDefinition`
  metadata, including the optional `contract` object for behavioral
  preconditions, postconditions, and capability invariants. These files are
  configuration resources, not SQLite tables.

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
    shell_safety_policy_enabled TEXT NOT NULL DEFAULT 'true',
    thinking_enabled TEXT NOT NULL DEFAULT 'false',
    thinking_effort TEXT,
    target_role_id TEXT,
    topology_json  TEXT,
    conversation_context_json TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_intents_session ON run_intents(session_id);
CREATE INDEX IF NOT EXISTS idx_run_intents_session_created
    ON run_intents(session_id, created_at ASC);
```

Purpose: stores the run input and per-run execution settings needed for queued runs and recoverable resume paths.

Notes:
- `intent` remains a text summary used for previews and logs.
- `input_json` stores the canonical typed run input array, including text and media references.
- `run_kind` distinguishes `conversation`, `generate_image`, `generate_audio`, and `generate_video`.
- `generation_config_json` stores the typed native media-generation config for provider-native image/audio/video runs.
- `yolo` controls whether tool approvals are skipped entirely for that run.
- `shell_safety_policy_enabled` controls whether shell execution keeps the local shell safety deny layer enabled for that run.
- `thinking_enabled` and `thinking_effort` capture per-run thinking configuration for providers that support reasoning streams.
- `target_role_id` stores an optional one-run direct-chat override, such as a leading `@Role` mention from the web composer.
- `session_mode` and `topology_json` snapshot the resolved root-agent topology, including the selected normal-mode root role, selected fixed orchestration graph when present, and effective orchestration policy, used when the run was created, so recoverable resumes do not drift when global orchestration settings change later. The policy snapshot includes DelegationPlanner auto-planning fields such as `auto_plan_long_tasks`, `planner_role_id`, and `max_temporary_roles_per_run`.
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
    input_text          TEXT NOT NULL DEFAULT '',
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
    subagent_suppress_hooks INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    completed_at        TEXT,
    completion_notified_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_background_tasks_run
    ON background_tasks(run_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_background_tasks_session
    ON background_tasks(session_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_background_tasks_status
    ON background_tasks(status, updated_at DESC);
```

Purpose: durable metadata for managed background tasks bound to one run. Foreground
subagent rows retain enough launch metadata to reattach or resume a synchronous
`spawn_subagent` call after a forced backend stop.

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

### 2.9.1.2 `board_todo_items`

```sql
CREATE TABLE IF NOT EXISTS board_todo_items (
    todo_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    source_id TEXT,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    source_provider TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_key TEXT NOT NULL,
    repository_full_name TEXT,
    issue_number INTEGER,
    pull_request_number INTEGER,
    html_url TEXT,
    session_id TEXT,
    run_id TEXT,
    linked_pr_number INTEGER,
    linked_pr_url TEXT,
    archived_at TEXT,
    last_synced_at TEXT,
    source_updated_at TEXT,
    last_status_reason TEXT,
    item_revision INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(workspace_id, source_provider, source_key)
);

CREATE INDEX IF NOT EXISTS idx_board_todo_items_workspace_status
    ON board_todo_items(workspace_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_board_todo_items_workspace_revision
    ON board_todo_items(workspace_id, item_revision);
CREATE INDEX IF NOT EXISTS idx_board_todo_items_run
    ON board_todo_items(run_id);
CREATE INDEX IF NOT EXISTS idx_board_todo_items_linked_pr
    ON board_todo_items(repository_full_name, linked_pr_number);
CREATE INDEX IF NOT EXISTS idx_board_todo_items_source_id
    ON board_todo_items(workspace_id, source_id, source_key);

CREATE TABLE IF NOT EXISTS board_todo_workspace_state (
    workspace_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL DEFAULT 0,
    github_issue_sync_cursor TEXT,
    repository_full_name TEXT,
    todo_sources_bootstrapped INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS board_todo_sources (
    source_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    provider TEXT NOT NULL,
    display_name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    repository_full_name TEXT,
    system_managed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS board_todo_source_state (
    source_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    sync_cursor TEXT,
    last_sync_started_at TEXT,
    last_sync_finished_at TEXT,
    last_sync_status TEXT NOT NULL DEFAULT 'idle',
    last_diagnostics_json TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_board_todo_sources_workspace
    ON board_todo_sources(workspace_id, kind, enabled);
```

Purpose: workspace-scoped TODO board state owned by Agent Teams. External
systems such as GitHub provide source records only; they do not own board
columns.

Notes:
- `status` is one of `todo`, `in_progress`, `review`, `done`, or `archived`.
- `source_id` points at the configured TODO source for imported items. Old local/manual rows are invalid for the current board contract and are ignored by board list, delta, and sync responses.
- GitHub sync upserts by source identity and source key while keeping `(workspace_id, source_provider, source_key)` readable for existing data.
- `updated_at` is the local board row update time; `source_updated_at` stores
  the external source update time, such as GitHub issue `updated_at`, for
  business-time sorting.
- `session_id/run_id` bind an item to the dedicated session/run created when processing starts.
- Session deletion clears stale board references; active non-`done` items bound to that session return to `todo`.
- Users can explicitly move `review` items to `done`; this updates `status`, `last_status_reason`, and revision metadata without adding new columns.
- `linked_pr_number/linked_pr_url` move imported issue items to `done` when the linked PR merges.
- `archived_at` implements soft delete. Sync does not reactivate manually archived rows. Full GitHub sync treats the open issue set as active truth; closed or otherwise non-open GitHub issues without merged linked PR evidence are archived instead of staying in TODO. If GitHub later reports an issue as open again, rows archived by GitHub closed/non-open reconciliation are restored to `todo`.
- `item_revision` and `board_todo_workspace_state.revision` power frontend delta updates.
- `board_todo_sources` stores user-managed `github_issues` sources. Older manual source rows may exist but are ignored by the settings API and are not returned as display groups.
- `todo_sources_bootstrapped` records that GitHub source auto-initialization has already been attempted for the board workspace. When true, sync and settings reads no longer recreate sources from git remote detection; users manage the list explicitly.
- GitHub sources persist `display_name`, `enabled`, and `repository_full_name`; sync uses this persisted configuration instead of re-reading git remotes after initialization. Multiple GitHub sources are supported and each owns independent state.
- `board_todo_source_state` stores per-source cursors and diagnostics. Disabled sources do not participate in board sync and retain their existing state.
- `board_todo_workspace_state.github_issue_sync_cursor` and `repository_full_name` are retained for existing data; per-source state is authoritative for new sync.
- TODO board API responses may include `run_status`, `run_phase`, `run_recoverable`, and `run_last_error` on `BoardTodoItem`, but those are derived from `run_runtime` at read time and are not `board_todo_items` columns.
- TODO board API responses may include non-persisted `source_groups` for grouped/mixed frontend rendering. `source_groups` is derived from `board_todo_sources` and current item source fields and is not a database table.
- Bound `in_progress` TODO rows keep `session_id` and `run_id` when the runtime row is present but stopped, failed, paused, stopping, queued, or running. Only a missing bound run is treated as stale and clears the session/run references back to `todo`; completed runs move the row to `review`.
- Repository: `src/relay_teams/boards/todo_repository.py`

---

### 2.9.1.3 `monitor_subscriptions`

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

### 2.10 Legacy Role Memory Tables

`role_memories` and `role_daily_memories` are no longer owned runtime tables.
`MemoryBankRepository` treats them as migration input only:

- a supported legacy `role_memories` table is imported into `memory_entries`
  during repository initialization and then dropped
- unsupported legacy `role_memories` shapes are dropped with a warning because
  the current runtime has no legacy reader
- `role_daily_memories` is always dropped if it exists

All durable role/workspace memory now lives in `memory_entries`.

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
    interval_every INTEGER,
    interval_unit TEXT,
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
- `schedule_mode` is `interval`, `cron`, or `one_shot`.
- `interval_every` and `interval_unit` are set only for interval schedules, where unit is `minutes`, `hours`, or `days`.
- `cron_expression` stores five-field cron schedules, including advanced cron expressions entered directly in the UI.
- `run_config_json` stores session mode, normal-mode root role id, orchestration preset, execution mode, YOLO, and thinking configuration.
- `delivery_binding_json` stores the selected Feishu chat target plus the exact bound `session_id` chosen from the current Feishu binding candidates.
- `delivery_events_json` stores which Feishu notifications are enabled for that automation project.
- `trigger_id` is a legacy compatibility field and now stores `schedule-{automation_project_id}`.
- `last_session_id` points at the most recent session used by that automation project, including a reused bound IM session.
- `next_run_at` is the scheduler cursor used to find due projects. Interval schedules advance from the scheduler fire time by one interval and do not backfill missed periods.

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

---

### 2.N `guardrail_audit`

```sql
CREATE TABLE IF NOT EXISTS guardrail_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '',
    task_id TEXT NOT NULL DEFAULT '',
    instance_id TEXT NOT NULL DEFAULT '',
    role_id TEXT NOT NULL DEFAULT '',
    tool_name TEXT NOT NULL DEFAULT '',
    layer TEXT NOT NULL,
    rule_type TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    action TEXT NOT NULL,
    triggered INTEGER NOT NULL DEFAULT 0,
    original_text_excerpt TEXT NOT NULL DEFAULT '',
    modified_text_excerpt TEXT NOT NULL DEFAULT '',
    triggered_rule_names TEXT NOT NULL DEFAULT '[]',
    strictness TEXT NOT NULL DEFAULT 'medium',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    evaluated_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_guardrail_audit_run_id
    ON guardrail_audit(run_id);
CREATE INDEX IF NOT EXISTS idx_guardrail_audit_task_id
    ON guardrail_audit(task_id);
CREATE INDEX IF NOT EXISTS idx_guardrail_audit_role_id
    ON guardrail_audit(role_id);
CREATE INDEX IF NOT EXISTS idx_guardrail_audit_evaluated_at
    ON guardrail_audit(evaluated_at);
```

Purpose: persistent audit trail for every runtime guardrail evaluation. Each row records the context (run, task, role, tool), the guard rule that was evaluated, whether it triggered, and excerpts of the original/modified text.

Notes:
- `layer`, `rule_type`, and `action` store enum values from the runtime guardrail system.
- `triggered_rule_names` is a JSON array of rule IDs that fired for this evaluation.
- `strictness` mirrors the task spec strictness level at the time of evaluation.
- `metadata_json` stores additional structured context (finding counts, details).
- Records are append-only; no updates or deletes are expected.
- Repository: `src/relay_teams/tools/runtime/guardrail_audit_repository.py`

---

### 2.N+1 `task_artifacts`

```sql
CREATE TABLE IF NOT EXISTS task_artifacts (
    task_id TEXT NOT NULL PRIMARY KEY,
    spec_artifact_id TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    evidence_bundle_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Purpose: unified task artifact container that aggregates all execution, verification, and delivery evidence for a single task.

Notes:
- One row per task, keyed by `task_id`.
- `spec_artifact_id` links back to the originating spec artifact.
- `evidence_bundle_json` stores the full `VerificationEvidenceBundle` as JSON.
- `summary` is a human-readable summary of the artifact contents.
- Repository: `src/relay_teams/agents/tasks/artifact_repository.py`

---

### 2.N+2 `task_artifact_entries`

```sql
CREATE TABLE IF NOT EXISTS task_artifact_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    role_id TEXT NOT NULL DEFAULT '',
    instance_id TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    linked_evidence_ids TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_artifact_entries_task_id
    ON task_artifact_entries(task_id);
CREATE INDEX IF NOT EXISTS idx_artifact_entries_phase
    ON task_artifact_entries(phase);
CREATE INDEX IF NOT EXISTS idx_artifact_entries_event_type
    ON task_artifact_entries(event_type);
```

Purpose: individual entries within a task artifact, recording events across spec, execution, verification, and delivery phases.

Notes:
- `phase` is one of: `spec`, `execution`, `verification`, `delivery`.
- `entry_id` is a unique identifier for each entry within the artifact.
- `linked_evidence_ids` is a JSON array of evidence item IDs from the parent artifact's evidence bundle.
- Entries are append-only; ordered by `id` for chronological replay.
- Repository: `src/relay_teams/agents/tasks/artifact_repository.py`

---

### 2.N+3 runtime_guardrail_audit



Purpose: persistent store for runtime guardrail audit findings, enabling compliance queries and debugging of guardrail decisions across tasks and roles.

Notes:
- One row per guardrail finding event.
-  is one of: , , , .
-  is one of: , , .
-  stores structured rule-specific data.
- Indexed on , , , and  for common query patterns.
- Repository: 

---

### 2.N+4 `memory_entries`

```sql
CREATE TABLE IF NOT EXISTS memory_entries (
    memory_id         TEXT PRIMARY KEY,
    tier              TEXT NOT NULL,
    scope             TEXT NOT NULL,
    workspace_id      TEXT NOT NULL,
    session_id        TEXT,
    run_id            TEXT,
    role_id           TEXT,
    kind              TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active',
    content_title     TEXT NOT NULL,
    content_body      TEXT NOT NULL,
    content_context   TEXT NOT NULL DEFAULT '',
    content_outcome   TEXT NOT NULL DEFAULT '',
    tags              TEXT NOT NULL DEFAULT '',
    confidence_score  REAL NOT NULL DEFAULT 1.0,
    source            TEXT NOT NULL,
    source_ref        TEXT NOT NULL DEFAULT '',
    superseded_by_id  TEXT,
    parent_entry_id   TEXT,
    version           INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    expires_at        TEXT,
    last_accessed_at  TEXT,
    access_count      INTEGER NOT NULL DEFAULT 0,
    metadata_json     TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (superseded_by_id) REFERENCES memory_entries(memory_id),
    FOREIGN KEY (parent_entry_id)  REFERENCES memory_entries(memory_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_entries_workspace_tier
    ON memory_entries(workspace_id, tier, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_entries_workspace_scope
    ON memory_entries(workspace_id, scope, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_entries_session
    ON memory_entries(session_id, tier, status, updated_at DESC)
    WHERE session_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_memory_entries_role
    ON memory_entries(workspace_id, role_id, tier, status, updated_at DESC)
    WHERE role_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_memory_entries_run
    ON memory_entries(run_id, status)
    WHERE run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_memory_entries_expires
    ON memory_entries(expires_at)
    WHERE expires_at IS NOT NULL AND status = 'active';
CREATE INDEX IF NOT EXISTS idx_memory_entries_source_ref
    ON memory_entries(source_ref);
```

Purpose: structured three-tier memory bank entries for the FE-1 Memory Bank feature. Supports Working (run-scoped), Medium-term (session/role-scoped), and Persistent (workspace-scoped) tiers with automatic TTL expiry, confidence decay, and consolidation promotion.

Notes:
- `memory_id` is generated as `mem-{uuid_hex}`.
- `tier` is one of: `working`, `medium_term`, `persistent`.
- `scope` is one of: `workspace`, `session`, `role`.
- `kind` is one of: `insight`, `constraint`, `decision`, `failure_mode`, `preference`, `fact`, `summary`.
- `status` is one of: `active`, `superseded`, `expired`.
- `source` is one of: `consolidation`, `manual`, `condensation`, `task_result`.
  Already-persisted legacy `reflection` values are normalized to
  `consolidation` during repository initialization.
- `tags` stores space-separated tag tokens for LIKE-based filtering.
- `confidence_score` decays over time for medium_term and persistent entries; entries below the minimum threshold are automatically expired.
- `superseded_by_id` references the memory entry that replaced this entry during consolidation.
- `parent_entry_id` references the source entry from which this entry was consolidated.
- `expires_at` is set automatically based on tier TTL defaults (working=4h, medium_term=7d, persistent=null).
- `metadata_json` stores up to 20 key-value string pairs.
- Legacy `role_memories` rows are migrated into this table at startup, then
  the legacy table is removed.
- Server startup reindexes active Memory Bank rows into retrieval so migrated
  records are searchable through FTS-backed memory search.
- Repository: `src/relay_teams/memory/repository.py`

### `memory_evolution_drafts`

```sql
CREATE TABLE IF NOT EXISTS memory_evolution_drafts (
    draft_id               TEXT PRIMARY KEY,
    workspace_id           TEXT NOT NULL,
    target                 TEXT NOT NULL,
    status                 TEXT NOT NULL,
    source_memory_ids_json TEXT NOT NULL,
    skill_id               TEXT NOT NULL,
    runtime_name           TEXT NOT NULL,
    description            TEXT NOT NULL DEFAULT '',
    instructions           TEXT NOT NULL,
    applied_skill_ref      TEXT,
    rejection_reason       TEXT NOT NULL DEFAULT '',
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    applied_at             TEXT,
    rejected_at            TEXT
);
```

Purpose: reviewable Memory Bank evolution drafts. A draft captures selected
active memory entries and renders a proposed `SKILL.md` payload before any
runtime skill directory is mutated.

Notes:
- `draft_id` is generated as `mem-evo-{uuid_hex}`.
- `target` is one of: `skill`, `sop_skill`.
- `status` is one of: `draft`, `applied`, `rejected`, `superseded`.
- `source_memory_ids_json` stores the ordered source memory IDs used to render
  the proposal.
- Applying a draft writes through the app-scoped ClawHub skill service, reloads
  the runtime skill registry, and records the applied draft and skill ref in
  source memory metadata.

### `memory_skill_drafts`

```sql
CREATE TABLE IF NOT EXISTS memory_skill_drafts (
    draft_id                 TEXT PRIMARY KEY,
    status                   TEXT NOT NULL,
    scope_kind               TEXT NOT NULL,
    workspace_id             TEXT,
    workspace_ids_json       TEXT NOT NULL DEFAULT '[]',
    source_memory_ids_json   TEXT NOT NULL DEFAULT '[]',
    draft_kind               TEXT NOT NULL,
    runtime_name             TEXT NOT NULL,
    description              TEXT NOT NULL DEFAULT '',
    instructions             TEXT NOT NULL DEFAULT '',
    files_json               TEXT NOT NULL DEFAULT '[]',
    validation_messages_json TEXT NOT NULL DEFAULT '[]',
    generation_error         TEXT NOT NULL DEFAULT '',
    applied_skill_id         TEXT,
    applied_ref              TEXT,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL,
    validated_at             TEXT,
    applied_at               TEXT
);

CREATE INDEX IF NOT EXISTS idx_memory_skill_drafts_status_updated
    ON memory_skill_drafts(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_skill_drafts_workspace_updated
    ON memory_skill_drafts(workspace_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_skill_drafts_kind_updated
    ON memory_skill_drafts(draft_kind, updated_at DESC);
```

Purpose: stores reviewable skill drafts synthesized from workspace or
cross-workspace Memory Bank entries. Drafts must be queried, edited, and
validated before they can be applied as app-scoped skills.

Notes:
- `draft_id` is generated as `msd-{uuid_hex}`.
- `status` is one of: `draft`, `validated`, `applying`, `applied`, `rejected`.
- `scope_kind` is `workspace` or `cross_workspace`.
- `draft_kind` is `skill` or `sop_skill`.
- `workspace_ids_json` records all workspaces represented by the source
  memories; `workspace_id` is populated for workspace-scoped drafts.
- `source_memory_ids_json` records the Memory Bank entries used to synthesize
  the draft. The generator consolidates related memories and does not create
  one skill per memory entry.
- `runtime_name`, `description`, `instructions`, and `files_json` are editable
  until the draft is applied.
- `validation_messages_json` stores skill-creator-compatible validation errors
  and warnings.
- `applied_skill_id` and `applied_ref` record the ClawHub-managed app skill
  created when a validated draft is applied.
- Repository: `src/relay_teams/memory/skill_draft_repository.py`
