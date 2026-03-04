# Database Schema

本项目使用本地 SQLite 作为持久化存储，数据库文件路径由运行时配置决定（默认 `tmp.db`）。当文件不可写时，自动退化为内存数据库（`file:agent_teams_shared?mode=memory&cache=shared`）。

每次建立连接时均执行 `PRAGMA foreign_keys = ON`，启用外键约束。

共有 **7 张表**，各司其职：

| 表名 | 所在模块 | 职责 |
|---|---|---|
| `sessions` | `state/session_repo.py` | 用户的会话记录及元数据存储 |
| `agent_instances` | `state/agent_repo.py` | agent 实例的运行状态快照 |
| `tasks` | `state/task_repo.py` | 任务记录（当前状态快照） |
| `shared_state` | `state/shared_store.py` | 跨 agent 的共享 KV 状态 |
| `events` | `state/event_log.py` | 业务事件流水（append-only） |
| `messages` | `state/message_repo.py` | LLM 对话消息历史（append-only） |
| `system_logs` | `state/log_repo.py` | 结构化系统日志持久化（append-only） |

---

## `sessions`

存储用户的**会话记录 (Session)**，用于持久化多次交互间共享的生命周期与元数据。

```sql
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    metadata   TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `session_id` | TEXT (PK) | 会话唯一 ID，格式如 `session-<uuid>`。 |
| `metadata` | TEXT | 会话级别的元数据，JSON 格式，可用于存储一些动态扩展配置或用户偏好。 |
| `created_at` | TEXT | 创建时间，ISO 8601（UTC）。 |
| `updated_at` | TEXT | 最后更新时间，ISO 8601（UTC）。 |

---

## `agent_instances`

存储每个 subagent 实例的**当前运行状态**，在进程重启后可由 `InstancePool.from_repo()` 恢复。

```sql
CREATE TABLE IF NOT EXISTS agent_instances (
    run_id       TEXT NOT NULL,
    trace_id     TEXT NOT NULL,
    session_id   TEXT NOT NULL,
    instance_id  TEXT PRIMARY KEY,
    role_id      TEXT NOT NULL,
    status       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
)
CREATE INDEX idx_agent_instances_run_status ON agent_instances(run_id, status)
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `instance_id` | TEXT (PK) | 实例唯一 ID，格式 `inst-<uuid>`。一个角色可以有多个实例同时运行。 |
| `run_id` | TEXT | 本次单轮 LLM run 的 ID。 |
| `trace_id` | TEXT | 从根任务开始的完整调用链 ID，贯穿整个任务树。 |
| `session_id` | TEXT | 用户会话 ID，代表一次完整的用户交互。 |
| `role_id` | TEXT | 该实例所扮演的角色，对应 `.agent_teams/roles/` 下的角色定义文件名。 |
| `status` | TEXT | 枚举：`idle` / `running` / `stopped` / `completed` / `failed` / `timeout`。进程崩溃后残留的 `running` 状态由 `InstancePool.from_repo()` 在启动时自动修正为 `failed`。 |
| `created_at` | TEXT | 实例创建时间，ISO 8601（UTC）。 |
| `updated_at` | TEXT | 最后状态变更时间，ISO 8601（UTC）。 |

---

## `tasks`

存储每条任务的**当前快照**，字段可 UPDATE。高频查询字段（`trace_id`、`session_id`、`parent_task_id`）已从 `envelope_json` 提升为独立列并建有索引。

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
)
CREATE INDEX idx_tasks_trace   ON tasks(trace_id)
CREATE INDEX idx_tasks_session ON tasks(session_id)
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `task_id` | TEXT (PK) | 任务唯一 ID，格式 `task-<uuid>`。 |
| `trace_id` | TEXT | 调用链 ID，用于查询一次完整任务树中的所有任务。已建索引。 |
| `session_id` | TEXT | 所属用户会话 ID。已建索引。 |
| `parent_task_id` | TEXT (nullable) | 父任务 ID，根任务为 NULL。 |
| `envelope_json` | TEXT | 任务完整描述的 JSON（`TaskEnvelope`），包含 `objective`、`dod`、`verification` 等不需要直接查询的字段。 |
| `status` | TEXT | 枚举：`created` / `assigned` / `running` / `stopped` / `completed` / `failed` / `timeout`。 |
| `assigned_instance_id` | TEXT (nullable) | 负责执行该任务的 agent 实例 ID，未分配时为 NULL。 |
| `result` | TEXT (nullable) | 任务完成后的输出内容，仅 `status=completed` 时有值。 |
| `error_message` | TEXT (nullable) | 失败或超时时的错误描述。 |
| `created_at` | TEXT | 任务创建时间，ISO 8601（UTC）。 |
| `updated_at` | TEXT | 最后更新时间，ISO 8601（UTC）。 |

### `envelope_json` 内部结构（`TaskEnvelope`）

| 字段 | 说明 |
|---|---|
| `task_id` | 同外层 `task_id` |
| `trace_id` | 同外层 `trace_id` |
| `session_id` | 同外层 `session_id` |
| `parent_task_id` | 同外层 `parent_task_id` |
| `objective` | 任务目标描述 |
| `parent_instruction` | 来自父 agent 的额外指令（可选） |
| `scope` | 该任务所关注的上下文范围（字符串列表） |
| `dod` | Definition of Done，任务完成标准清单 |
| `verification` | 验收计划，包含 `checklist` 字段 |

---

## `shared_state`

跨 agent 共享的 KV 存储，支持 global / session / task / instance 四种作用域。值 upsert（有则更新，无则插入）。支持 TTL 过期，过期行由 `cleanup_expired()` 清理。

```sql
CREATE TABLE IF NOT EXISTS shared_state (
    scope_type  TEXT NOT NULL,
    scope_id    TEXT NOT NULL,
    state_key   TEXT NOT NULL,
    value_json  TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at  TEXT,
    PRIMARY KEY (scope_type, scope_id, state_key)
)
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `scope_type` | TEXT (PK part) | 枚举：`global` / `session` / `task` / `instance`。决定该状态的可见范围。 |
| `scope_id` | TEXT (PK part) | 作用域的具体 ID。`global` 时固定为 `"global"`；其他情况分别为对应的 ID。 |
| `state_key` | TEXT (PK part) | 键名。三者联合构成唯一主键。 |
| `value_json` | TEXT | 值，JSON 格式。 |
| `updated_at` | TEXT | 最后写入时间，由 SQLite 自动维护。 |
| `expires_at` | TEXT (nullable) | 过期时间，ISO 8601（UTC）。NULL 表示永不过期。写入时可通过 `ttl_seconds` 参数指定。读取时自动过滤已过期行。 |

**作用域说明：**

| scope_type | 可见范围 | 典型用途 |
|---|---|---|
| `global` | 所有 agent、所有 session | 全局配置、共享知识 |
| `session` | 同一用户会话内所有 agent | 用户偏好、会话级上下文 |
| `task` | 某个任务及其子任务 | 任务内部的中间状态 |
| `instance` | 单个 agent 实例私有 | 实例本地草稿 |

---

## `events`

业务事件的 **append-only 流水日志**，只增不改，用于审计和历史回溯。由 `state/event_log.py` 中的 `EventLog` 类管理。

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
)
CREATE INDEX idx_events_trace   ON events(trace_id)
CREATE INDEX idx_events_session ON events(session_id)
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER (PK) | 自增主键，保证事件的全局插入顺序。 |
| `event_type` | TEXT | 事件类型，见下方枚举。 |
| `trace_id` | TEXT | 调用链 ID，用于按 trace 查询完整事件序列。已建索引。 |
| `session_id` | TEXT | 所属用户会话 ID。已建索引。 |
| `task_id` | TEXT (nullable) | 关联的任务 ID，仅任务相关事件有值。 |
| `instance_id` | TEXT (nullable) | 关联的 agent 实例 ID，仅实例相关事件有值。 |
| `payload_json` | TEXT | 事件附加数据，JSON 格式，内容随 `event_type` 变化。 |
| `occurred_at` | TEXT | 事件发生时间，ISO 8601（UTC）。 |

### `event_type` 枚举

| 值 | 触发时机 |
|---|---|
| `task_created` | 新任务被创建 |
| `task_assigned` | 任务被分配给某个 agent 实例 |
| `task_started` | agent 开始执行任务 |
| `task_stopped` | 任务被用户停止 |
| `task_completed` | 任务执行成功 |
| `task_failed` | 任务执行失败 |
| `task_timeout` | 任务超时 |
| `instance_created` | 新的 subagent 实例被创建 |
| `instance_stopped` | subagent 实例被用户停止 |
| `instance_recycled` | 空闲实例被回收清理 |
| `verification_passed` | 任务验收通过 |
| `verification_failed` | 任务验收未通过 |

---


## `system_logs`

结构化运行日志的 **append-only 持久化表**，用于跨进程排障和按 `trace_id/run_id/request_id` 检索。由 `state/log_repo.py` 管理，日志由 `runtime/log_persistence.py` 异步批量写入。

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
)
CREATE INDEX idx_system_logs_ts      ON system_logs(ts)
CREATE INDEX idx_system_logs_level   ON system_logs(level)
CREATE INDEX idx_system_logs_event   ON system_logs(event)
CREATE INDEX idx_system_logs_trace   ON system_logs(trace_id)
CREATE INDEX idx_system_logs_run     ON system_logs(run_id)
CREATE INDEX idx_system_logs_request ON system_logs(request_id)
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER (PK) | 自增主键，保证写入顺序。 |
| `ts` | TEXT | 日志时间戳（ISO 8601 UTC）。 |
| `level` | TEXT | 日志级别（DEBUG/INFO/WARNING/ERROR）。 |
| `event` | TEXT (nullable) | 结构化事件名（如 `run.created`）。 |
| `trace_id` | TEXT (nullable) | 端到端链路 ID。 |
| `request_id` | TEXT (nullable) | HTTP 请求 ID。 |
| `session_id/run_id/task_id/instance_id` | TEXT (nullable) | 业务上下文字段。 |
| `logger` | TEXT | logger 名称。 |
| `message` | TEXT | 可读消息摘要。 |
| `payload_json` | TEXT (nullable) | 结构化 payload。 |
| `error_json` | TEXT (nullable) | 异常结构（type/message/stack）。 |
| `raw_json` | TEXT | 原始完整 JSON 日志，便于二次解析。 |

---

## `messages`

每个 agent 实例的 **LLM 对话消息历史**，append-only，用于在多次任务之间保持对话上下文。由 `state/message_repo.py` 中的 `MessageRepository` 管理。

每次 `LLMProvider.generate()` 调用时：
1. **读取**：`get_history(instance_id)` 加载历史，传入 `agent.run_sync(message_history=...)`
2. **写入**：`result.new_messages()` 拿到本轮新消息，逐条 INSERT

```sql
CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id  TEXT NOT NULL,
    task_id      TEXT NOT NULL,
    trace_id     TEXT NOT NULL,
    role         TEXT NOT NULL,
    message_json TEXT NOT NULL,
    created_at   TEXT NOT NULL
)
CREATE INDEX idx_messages_instance ON messages(instance_id)
CREATE INDEX idx_messages_task     ON messages(task_id)
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER (PK) | 自增主键，保证消息的绝对顺序（不依赖时间戳精度）。 |
| `instance_id` | TEXT | 产生该消息的 agent 实例 ID。已建索引，用于按实例加载完整历史。 |
| `task_id` | TEXT | 产生该消息时对应的任务 ID。已建索引，可按任务查询本轮消息。 |
| `trace_id` | TEXT | 调用链 ID，便于跨表关联。 |
| `role` | TEXT | 消息角色：`user`（发给 LLM 的输入）/ `assistant`（LLM 回复，含 tool_call）。由 pydantic-ai 的 `ModelRequest`/`ModelResponse` 类型推断。 |
| `message_json` | TEXT | 使用 pydantic-ai `ModelMessagesTypeAdapter` 序列化的 JSON，包含完整的 parts（文本、tool_call、tool_result）。 |
| `created_at` | TEXT | 插入时间，ISO 8601（UTC）。 |

---



```
session_id ──┬── sessions (PK)
             ├── agent_instances
             ├── tasks
             ├── shared_state (scope=session)
             └── events

trace_id   ──┬── agent_instances
             ├── tasks
             └── events

task_id    ──┬── tasks
             ├── agent_instances (assigned_instance_id)
             ├── shared_state (scope=task)
             └── events

instance_id ─┬── agent_instances
             ├── tasks (assigned_instance_id)
             ├── shared_state (scope=instance)
             └── events
```

> **注意**：`agent_instances` 表与内存中的 `InstancePool` 是两套并行状态。`InstancePool` 是进程内缓存，`agent_instances` 是持久化备份。进程启动时通过 `InstancePool.from_repo(agent_repo)` 从数据库重建内存状态，并将残留的 `running` 实例标记为 `failed`。
