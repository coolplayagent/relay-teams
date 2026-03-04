# Agent Teams API 设计文档

版本：MVP（直接演进版，无 `/api/v1` 兼容层）

## 1. 设计目标

- 前端与 CLI 仅通过 HTTP/SSE 与后端交互。
- 后端统一暴露 `/api` 前缀接口。
- 运行流采用“两阶段”模式：
1. 先创建 run（`POST /api/runs`）
2. 再订阅 run 事件（`GET /api/runs/{run_id}/events`，SSE）

## 2. 通用约定

- Base URL：`http://<host>:<port>/api`
- 编码：`application/json; charset=utf-8`
- 时间字段：ISO 8601（UTC）
- 枚举大小写：全小写字符串（如 `ai`、`human`、`auto`）
- 错误格式：
```json
{"detail": "error message"}
```
- 追踪头：
  - 请求可选携带 `X-Request-Id` 与 `X-Trace-Id`；
  - 服务端会在响应头回传 `X-Request-Id` / `X-Trace-Id` 便于端到端排障。

常见状态码：

- `200` 成功
- `404` 资源不存在或上下文不匹配
- `422` 请求参数校验失败
- `500` 服务内部错误

## 3. 核心数据对象

### SessionRecord

```json
{
  "session_id": "session-xxxx",
  "metadata": {},
  "created_at": "2026-03-03T12:00:00Z",
  "updated_at": "2026-03-03T12:00:00Z"
}
```

### Run（创建响应）

```json
{
  "run_id": "uuid",
  "session_id": "session-xxxx"
}
```

### RunEvent（SSE `data:` 行负载）

```json
{
  "session_id": "session-xxxx",
  "run_id": "uuid",
  "trace_id": "uuid",
  "task_id": null,
  "instance_id": null,
  "role_id": null,
  "event_type": "text_delta",
  "payload_json": "{\"text\":\"...\"}",
  "occurred_at": "2026-03-03T12:00:01Z"
}
```

## 4. 接口清单

### 4.1 System

- `GET /system/health`
- `GET /system/configs`
- `GET /system/configs/model`
- `GET /system/configs/model/profiles`
- `PUT /system/configs/model/profiles/{name}`
- `DELETE /system/configs/model/profiles/{name}`
- `PUT /system/configs/model`
- `POST /system/configs/model:reload`
- `POST /system/configs/mcp:reload`
- `POST /system/configs/skills:reload`

### 4.2 Sessions

- `POST /sessions`
- `GET /sessions`
- `GET /sessions/{session_id}`
- `PATCH /sessions/{session_id}`
- `DELETE /sessions/{session_id}`
- `GET /sessions/{session_id}/rounds`
- `GET /sessions/{session_id}/rounds/{run_id}`
- `GET /sessions/{session_id}/agents`
- `GET /sessions/{session_id}/events`
- `GET /sessions/{session_id}/messages`
- `GET /sessions/{session_id}/agents/{instance_id}/messages`
- `GET /sessions/{session_id}/workflows`

### 4.3 Runs

- `POST /runs`
- `GET /runs/{run_id}/events`（SSE）
- `POST /runs/{run_id}/inject`
- `POST /runs/{run_id}/subagents/{instance_id}/inject`
- `POST /runs/{run_id}/stop`
- `GET /runs/{run_id}/gates`
- `POST /runs/{run_id}/gates/{task_id}/resolve`
- `GET /runs/{run_id}/tool-approvals`
- `POST /runs/{run_id}/tool-approvals/{tool_call_id}/resolve`
- `POST /runs/{run_id}/dispatch`

### 4.4 Tasks

- `GET /tasks`
- `GET /tasks/{task_id}`

### 4.5 Roles

- `GET /roles`
- `POST /roles:validate`

### 4.6 Logs

- `POST /logs/frontend`（前端日志批量上报）

## 5. 关键流程

### 5.1 创建会话

请求：
```http
POST /api/sessions
Content-Type: application/json

{}
```

响应：
```json
{
  "session_id": "session-ab12cd34",
  "metadata": {},
  "created_at": "...",
  "updated_at": "..."
}
```

### 5.2 发起一次运行

请求：
```http
POST /api/runs
Content-Type: application/json

{
  "intent": "Draft a release note",
  "session_id": "session-ab12cd34",
  "execution_mode": "ai",
  "confirmation_gate": false
}
```

响应：
```json
{
  "run_id": "90166c2b-360b-42a9-9fcd-3d7cbd7f5f74",
  "session_id": "session-ab12cd34"
}
```

### 5.3 订阅运行事件（SSE）

请求：
```http
GET /api/runs/{run_id}/events
Accept: text/event-stream
```

事件示例：
```text
data: {"event_type":"run_started", ...}

data: {"event_type":"text_delta","payload_json":"{\"text\":\"Hello\"}", ...}

data: {"event_type":"run_completed", ...}

data: {"event_type":"run_stopped","payload_json":"{\"reason\":\"stopped_by_user\"}", ...}
```

说明：

- 第一个订阅者会触发该 run 执行。
- `run_completed` 或 `run_failed` 后流结束。

### 5.4 运行中注入消息

请求：
```http
POST /api/runs/{run_id}/inject
Content-Type: application/json

{
  "source": "user",
  "content": "Additional constraint"
}
```

子 agent 定向补充消息：
```http
POST /api/runs/{run_id}/subagents/{instance_id}/inject
Content-Type: application/json

{"content":"Please revise with this extra requirement"}
```

停止运行（主 agent 或 subagent）：
```http
POST /api/runs/{run_id}/stop
Content-Type: application/json

{"scope":"main"}
```

```http
POST /api/runs/{run_id}/stop
Content-Type: application/json

{"scope":"subagent","instance_id":"inst-xxx"}
```

### 5.5 人工调度与确认门

- 获取待确认 gate：`GET /api/runs/{run_id}/gates`
- 处理 gate：
```http
POST /api/runs/{run_id}/gates/{task_id}/resolve
Content-Type: application/json

{"action":"approve","feedback":""}
```
- 人工派发任务：
```http
POST /api/runs/{run_id}/dispatch
Content-Type: application/json

{"session_id":"session-ab12cd34","task_id":"task-xxx"}
```

### 5.6 工具调用审批

- 获取待审批的工具调用：`GET /api/runs/{run_id}/tool-approvals`
- 审批/拒绝工具调用：
```http
POST /api/runs/{run_id}/tool-approvals/{tool_call_id}/resolve
Content-Type: application/json

{"action":"approve","feedback":""}
```

```http
POST /api/runs/{run_id}/tool-approvals/{tool_call_id}/resolve
Content-Type: application/json

{"action":"deny","feedback":"不安全操作"}
```

## 6. 枚举约束

### execution_mode

- `ai`
- `human`
- `auto`

### injection source

- `system`
- `user`
- `subagent`

### 主要 run event_type

- `run_started`
- `model_step_started`
- `model_step_finished`
- `text_delta`
- `tool_call`
- `tool_result`
- `tool_approval_requested`
- `tool_approval_resolved`
- `injection_enqueued`
- `injection_applied`
- `subagent_stopped`
- `subagent_resumed`
- `awaiting_human_dispatch`
- `human_task_dispatched`
- `subagent_gate`
- `gate_resolved`
- `run_stopped`
- `run_completed`
- `run_failed`

## 7. 兼容性说明

- 旧 `/api/v1/*` 接口已不作为标准契约。
- 新客户端应统一接入 `/api/*`。
- Web 与 CLI 均应按“创建 run + 订阅 SSE”流程实现。

## 8. Session Rounds Payload Notes (2026-03-04)

`GET /sessions/{session_id}/rounds` and `GET /sessions/{session_id}/rounds/{run_id}` now include `pending_streams` for refresh-safe stream recovery.

Example:

```json
{
  "run_id": "run-123",
  "pending_tool_approvals": [],
  "pending_streams": {
    "coordinator_text": "",
    "coordinator_instance_id": "",
    "by_instance": {
      "inst-abc": "still streaming text..."
    }
  }
}
```

Semantics:
- `coordinator_text`: unpersisted coordinator stream delta for this round.
- `coordinator_instance_id`: coordinator instance id if available.
- `by_instance`: unpersisted stream deltas keyed by subagent instance id.
- Empty strings/maps mean no pending stream delta should be rendered.


### 5.7 前端日志上报

请求：
```http
POST /api/logs/frontend
Content-Type: application/json

{
  "events": [
    {
      "level": "error",
      "event": "sse.disconnect",
      "message": "event stream disconnected unexpectedly",
      "trace_id": "trace_xxx",
      "run_id": "run_xxx",
      "payload": {"ready_state": 2}
    }
  ]
}
```

响应：
```json
{"accepted": 1}
```
