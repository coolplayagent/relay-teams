# 日志全流程 Trace 能力建设方案

## 1. 背景与问题现状

当前项目已有部分日志能力，但整体可观测性不足，主要体现在：

1. **日志入口分散且语义不统一**：运行链路里同时存在 `print` 风格输出（如 `runtime/console.py`）和少量 `logging`（如 `interfaces/server/routers/runs.py`），格式、字段、级别不一致。
2. **缺少“全链路关联 ID”**：虽然业务里有 `run_id/session_id/task_id/instance_id` 等对象，但没有作为统一日志字段进行端到端透传，定位问题需要人工拼接上下文。
3. **异常与中断场景覆盖不足**：意外中断（进程退出、SSE 断流、子任务卡住、工具调用失败）缺乏统一“故障事件”与“最后状态快照”日志。
4. **前端诊断信息不可回收**：目前仓库内仅有前端构建产物（`frontend/dist`），缺少可维护的前端日志 SDK 与统一上报通道，用户侧错误很难回放。

> 结论：需要把日志从“零散打印”升级为“结构化事件流 + 统一关联 ID + 前后端一体化采集”。

---

## 2. 建设目标（可验收）

建议按以下目标验收：

- **G1：统一日志规范**：后端所有关键路径输出结构化 JSON 日志，具备统一字段。
- **G2：全链路可追踪**：从 HTTP 请求 -> run 创建 -> coordinator 调度 -> agent/tool 执行 -> SSE 推送，均能以 `trace_id` 一键检索。
- **G3：异常可复盘**：任何 5xx、tool error、stream 中断、人工停止都能在日志中看到“触发点 + 传播链路 + 最终状态”。
- **G4：前后端可关联**：前端产生的错误日志与后端 run/stream 日志可通过 `trace_id` / `run_id` 对齐。
- **G5：可运营**：支持日志分级、采样、脱敏、保留期策略，避免日志噪音和泄密。

---

## 3. 日志数据模型（建议标准）

### 3.1 必备字段

统一定义一个 `LogEvent`（不要求先引入新存储，先落标准）：

- `ts`：ISO8601 时间戳（UTC）
- `level`：`DEBUG/INFO/WARN/ERROR`
- `service`：固定 `agent_teams`
- `env`：`dev/test/prod`
- `event`：事件名（如 `run.created`, `tool.call.failed`）
- `message`：人类可读摘要
- `trace_id`：一次请求/会话链路的全局追踪 ID
- `request_id`：单次 HTTP 请求 ID
- `session_id` / `run_id` / `task_id` / `instance_id` / `tool_call_id`：按场景可选
- `role_id`：执行角色（coordinator/subagent 等）
- `duration_ms`：耗时类事件统一字段
- `error.type` / `error.message` / `error.stack`：异常字段
- `payload`：必要上下文（必须过脱敏与截断）

### 3.2 事件命名建议

采用 `domain.action[.result]`，例如：

- `http.request.received`, `http.request.completed`
- `run.created`, `run.stopped`, `run.failed`
- `coord.task.dispatched`, `coord.task.completed`
- `tool.call.started`, `tool.call.succeeded`, `tool.call.failed`
- `stream.opened`, `stream.chunk.sent`, `stream.closed`, `stream.failed`
- `gate.opened`, `gate.resolved`

---

## 4. 后端落地方案

### 4.1 统一日志基础设施

1. 新增运行时日志模块（示例：`src/agent_teams/runtime/logging.py`），负责：
   - 初始化标准 `logging`；
   - 输出 JSON formatter；
   - 注入公共字段（service/env/version/hostname）；
   - 提供 `get_logger(__name__)` 与 `bind_context(...)` 能力。
2. 将 `runtime/console.py` 里生产路径的 `print` 逐步迁移到结构化 logger（可保留 CLI 友好输出作为开发模式）。
3. 日志级别由配置驱动（`.env` / 配置对象），至少支持 `LOG_LEVEL`, `LOG_FORMAT=json|console`。

### 4.2 Trace 上下文透传

建议使用 `contextvars` 保存 `trace_id/request_id/run_id/session_id`，在以下入口写入：

- FastAPI 中间件：生成或接收 `X-Request-Id`，若无则创建；
- `create_run` 时创建 `trace_id`（可直接复用 run_id，或单独生成）；
- coordinator/worker/tool 执行开始时绑定 `task_id/instance_id/role_id`。

> 要求：任何 logger 调用都无需手动拼所有 ID，context 自动附加。

### 4.3 关键埋点清单（最小可用）

按优先级建议先补这些点：

1. **HTTP 层**：请求开始/结束、状态码、耗时、异常。
2. **Run 生命周期**：创建、状态迁移、停止、完成、失败。
3. **Coordinator 调度**：任务出队、分发、重试、超时、等待人工。
4. **Tool 调用**：入参摘要、审批结果、执行耗时、失败原因（含可重试标记）。
5. **SSE 流**：建立连接、事件发送计数、客户端断开、服务端异常关闭。
6. **持久化事件日志**（`sessions/runs/event_log` 相关）：写入成功/失败与重试。

### 4.4 异常与中断增强

1. **全局异常兜底**：FastAPI exception handler 统一记录结构化错误日志。
2. **进程信号处理**：捕获 `SIGTERM/SIGINT`，输出 shutdown reason、在途 run 数量、未完成任务摘要。
3. **崩溃前冲刷**：确保 handler 中调用日志 flush，减少最后几秒日志丢失。
4. **超时守卫**：对 tool/run/dispatch 增加 watchdog 事件，出现超时时记录 timeout 诊断字段。

---

## 5. 前端日志与后端联动（建议）

当前仓库只有 `frontend/dist`，建议补充前端源码工程后实施以下策略：

1. **前端日志 SDK**：封装 `logInfo/logWarn/logError`，默认带：
   - `trace_id`（页面级）
   - `run_id/session_id`
   - 路由、浏览器信息、网络状态
2. **SSE 观测点**：记录 connect/reconnect/disconnect/message parse error。
3. **错误采集**：
   - `window.onerror`
   - `unhandledrejection`
   - 接口失败（HTTP status + body 摘要）
4. **上报通道**：新增 `/api/logs/frontend`（可批量上报 + 采样），后端写同一日志系统。
5. **隐私与体积控制**：输入内容默认脱敏（如 token/email/phone），前端日志长度截断。

---

## 6. 日志治理规范（必须）

1. **脱敏规则**：对密钥、token、cookie、Authorization、用户敏感输入进行掩码。
2. **大小控制**：字段长度上限（如 2KB），超出截断并标记 `truncated=true`。
3. **采样策略**：
   - 成功流量低比例采样（如 5%）
   - 错误流量全量保留
4. **保留策略**：按环境定义 TTL（dev 7d / prod 30d+，按合规调整）。
5. **日志级别约束**：
   - `DEBUG`：开发诊断
   - `INFO`：关键业务事件
   - `WARN`：可恢复异常
   - `ERROR`：失败与中断

---

## 7. 分阶段实施路线（建议 4 周）

### Phase 1（第 1 周）：标准与骨架

- 建立结构化日志模块与配置开关。
- 接入 FastAPI 中间件，打通 `request_id/trace_id`。
- 在 run 创建、SSE 开关、tool 调用处补第一批埋点。

### Phase 2（第 2 周）：核心链路覆盖

- coordinator/task_execution_service 全链路埋点。
- 异常处理统一化（全局 handler + timeout/watchdog）。
- 输出“日志字段字典文档”。

### Phase 3（第 3 周）：前端可观测性

- 建立前端日志 SDK 与错误捕获。
- 建立前端日志上报 API，并与后端 trace 对齐。

### Phase 4（第 4 周）：运营化

- 加入采样、脱敏、保留策略。
- 增加告警规则（错误率、SSE 断开率、tool fail rate、run timeout）。
- 输出排障手册（按 trace_id 检索流程）。

---

## 8. 验收清单（建议直接做成 DoD）

- [ ] 任意一个 `run_id` 能在 3 分钟内定位完整执行链路。
- [ ] 任意一次 5xx 能查到请求参数摘要、异常栈、关联任务上下文。
- [ ] 发生中断（stop/signal/timeout）时有统一 `run.interrupted` 日志。
- [ ] 前端 SSE 断连可与后端 stream 日志对齐定位。
- [ ] 日志中不出现明文密钥/token。

---

## 9. 结合当前代码的优先改造点（具体到目录）

1. `src/agent_teams/runtime/console.py`
   - 将 `print` 输出迁移到结构化 logger；保留 CLI 展示时做双通道输出。
2. `src/agent_teams/interfaces/server/routers/runs.py`
   - 补 `request started/completed`，并在异常分支写入 `run_id/request_id`。
3. `src/agent_teams/coordination/coordinator.py`
   - 现有 debug 文本日志升级为结构化事件（event + IDs + duration）。
4. `src/agent_teams/coordination/task_execution_service.py`
   - 补工具调用结果、重试、超时等关键字段。
5. `src/agent_teams/providers/llm.py`
   - 增加模型请求/响应统计（token、latency、provider error code）。
6. `src/agent_teams/sessions/runs/event_log.py`
   - 对事件写入失败进行明确告警与重试日志。

---

## 10. 推荐先做的最小版本（MVP）

若希望最快看到收益，可先做以下 5 项：

1. 统一 `logging` JSON 输出（替换核心 `print`）。
2. FastAPI 中间件注入 `request_id/trace_id`。
3. run/coordinator/tool/stream 四条主链路埋点。
4. 全局异常 handler + signal 中断日志。
5. 一份“按 trace_id 排障 SOP”。

完成后即可显著提升“前后端出问题时不知道卡在哪”的现状，再逐步扩展前端上报与告警系统。
