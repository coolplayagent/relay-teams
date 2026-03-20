# 大模型可联通性检测与断网重试方案设计

## 1. 目标与范围

本方案解决四类问题：

1. **模型配置可用性探测**：在“设置 > 大模型设置”中提供一键探测，帮助用户在保存配置后快速验证可连通。
2. **运行期自动重试**：在主流程（Coordinator/主 Agent）和 Subagent 的模型调用中，对网络抖动与暂时性故障自动重试。
3. **前端可感知重试过程**：重试开始、每次退避、最终成功/失败都通过 SSE 事件实时透出。
4. **超限暂停+人工恢复**：超过最大重试次数后，流程进入“暂停等待恢复”状态，前端提供“重试继续”按钮，恢复后从当前节点继续。

> 注：本方案是**向前演进设计**，不要求对旧行为做兼容层。

---

## 2. 核心设计原则

- **最小 token 消耗探测**：探测请求应走“轻量模型调用”路径，严格限制输出 token。
- **只重试可重试错误**：仅对网络断开、超时、429、5xx 等暂时错误重试；参数错误/鉴权错误直接失败。
- **主流程与 Subagent 一致语义**：统一重试策略、统一事件字段，便于前端复用。
- **可观测**：每次重试有 request_id/trace_id、错误分类、退避时长。
- **可恢复**：超过阈值后不中断上下文，进入 paused 状态，允许用户人工恢复。

---

## 3. API 设计

## 3.1 模型可联通性探测接口

### 3.1.1 `POST /api/system/configs/model:probe`

用途：探测当前生效配置或候选配置是否能成功完成最小模型调用。

请求体（建议）：

```json
{
  "profile_name": "openai-default",
  "override": {
    "base_url": "https://api.openai.com/v1",
    "api_key": "***",
    "model": "gpt-4o-mini"
  },
  "timeout_ms": 5000
}
```

- `profile_name`：可选，指定已保存档案。
- `override`：可选，允许“未保存先探测”。
- `timeout_ms`：可选，探测超时（默认 5s，最大 10s）。

响应：

```json
{
  "ok": true,
  "provider": "openai",
  "model": "gpt-4o-mini",
  "latency_ms": 842,
  "token_usage": {
    "prompt_tokens": 8,
    "completion_tokens": 1,
    "total_tokens": 9
  },
  "checked_at": "2026-03-04T08:00:00Z",
  "diagnostics": {
    "endpoint_reachable": true,
    "auth_valid": true,
    "rate_limited": false
  }
}
```

失败时：

```json
{
  "ok": false,
  "error_code": "network_timeout",
  "error_message": "Connection timed out",
  "retryable": true,
  "checked_at": "2026-03-04T08:00:00Z"
}
```

实现建议（避免 token 浪费）：

- 固定探测 prompt：`"reply with: pong"`。
- `max_output_tokens=1~4`（推荐 1）。
- 温度固定 0，禁用工具调用、禁用长上下文。
- 优先复用 provider SDK 的最短路径（chat/completions 最小化参数）。

---

## 3.2 运行控制接口扩展

### 3.2.1 `POST /api/runs/{run_id}:resume`

用途：当 run 因重试超限进入 paused 后，用户点击“重试继续”。

请求体（可选）：

```json
{
  "reason": "user_retry",
  "force_probe": true
}
```

- `force_probe=true` 时，恢复前先做一次快速 probe，失败则保持 paused 并返回原因。

响应：

```json
{
  "run_id": "...",
  "status": "running"
}
```

### 3.2.2 `POST /api/runs/{run_id}/subagents/{instance_id}:resume`

用途：仅恢复某个 subagent（可选能力；若当前架构不易支持，可先只支持 run 级恢复）。

---

## 3.3 SSE 事件扩展

新增 run 事件类型（挂到 `RunEventType`）：

- `model_retry_scheduled`
- `model_retry_attempt`
- `model_retry_exhausted`
- `run_paused`
- `run_resumed`
- `subagent_paused`
- `subagent_resumed`（已有可复用，补充 payload）

事件 payload 示例：

```json
{
  "scope": "run",
  "scope_id": "<run_id>",
  "provider": "openai",
  "model": "gpt-4o-mini",
  "attempt": 2,
  "max_attempts": 5,
  "backoff_ms": 1600,
  "error_code": "network_reset",
  "retryable": true,
  "request_id": "...",
  "trace_id": "..."
}
```

`model_retry_exhausted` + `run_paused` 组合语义：

- exhausted 描述“技术上已到阈值”
- paused 描述“业务状态已暂停，等待人工恢复”

---

## 4. 后端实现方案

## 4.1 错误分类器（Retry Classifier）

在 provider 适配层统一错误映射：

- **可重试**：网络断连、DNS 失败、连接超时、读取超时、429、5xx。
- **不可重试**：401/403（鉴权）、404 model not found、400 参数错误、上下文长度超限（通常需改输入）。

输出统一结构：

```python
class RetryDecision(BaseModel):
    retryable: bool
    error_code: str
    http_status: int | None
    message: str
```

## 4.2 重试执行器（Retry Executor）

在 `providers/llm` 调用入口包一层 `call_with_retry(...)`：

- 参数：`max_attempts`, `base_delay_ms`, `max_delay_ms`, `jitter`, `timeout_ms`。
- 策略：指数退避 + 抖动（如 Full Jitter）。
- 每次失败发 `model_retry_attempt`；计划下一次发 `model_retry_scheduled`。
- 成功后在 `model_step_finished` 中附 `retry_count`。

推荐默认：

- `max_attempts=5`（首次 + 4 次重试）
- `base_delay=500ms`
- `max_delay=8000ms`

## 4.3 暂停/恢复状态机

为 run 增加状态：`running | paused | completed | failed | stopped`。

触发规则：

1. 调用失败且可重试 -> 自动重试。
2. 达到 `max_attempts` 仍失败 -> emit `model_retry_exhausted`，切 `paused`，emit `run_paused`。
3. 用户调用 `:resume` -> 切 `running`，emit `run_resumed`，从失败节点继续。

恢复点要求：

- 保留“当前未完成 step”的输入快照（prompt、工具结果、上下文游标）。
- **长流输出中断策略（定案）**：当流式输出中途网络中断时，不尝试恢复原流的 token 级续传；直接丢弃中断流，从最近 checkpoint 重新执行当前 step。
- `resume` 后由后端重新发起该 step 的流式输出，前端将其视为同一任务的继续执行。

## 4.4 幂等性设计（必须项）

- 对所有可能产生外部副作用的工具调用（消息发送、工单创建、外部写入）强制携带 `idempotency_key`。
- `idempotency_key` 生成建议：`run_id + task_id + instance_id + tool_call_id` 的稳定哈希。
- 工具执行记录需持久化 `idempotency_key -> result` 映射；恢复后若命中已成功记录，直接返回历史结果，不重复执行。
- 仅在“确认未落库”的失败场景允许重新执行，避免二次副作用。

## 4.5 鉴权热更新设计（必须项）

- `POST /api/runs/{run_id}:resume` 执行前，必须从最新配置源重载模型 profile/API Key，不得复用进程内旧凭据缓存。
- 若重载后鉴权仍失败，run 保持 `paused`，并通过 `run_paused` payload 返回错误码（如 `auth_invalid`）。
- 建议在 resume 响应中透出 `config_version`，便于排查“恢复时使用了哪版凭据”。

## 4.6 主流程 + Subagent 一致接入

- 主流程：Coordinator 使用统一 `call_with_retry`。
- Subagent：每个 instance 的模型调用同样走 `call_with_retry`。
- 事件中增加 `scope` 字段区分：`run | subagent`，并附 `instance_id`（当 scope=subagent）。

---

## 5. 前端交互设计

## 5.1 设置页“探测连通性”按钮

位置：模型配置编辑区（保存按钮旁）。

交互：

1. 用户填写配置 -> 点“探测连通性”。
2. 前端调用 `POST /api/system/configs/model:probe`。
3. 展示结果：
   - 成功：延迟、模型名、token 使用（很小）。
   - 失败：错误类型 + 可操作建议（如“检查 API Key / 网络代理 / base_url”）。

按钮状态：`idle | probing | success | failed`。

## 5.2 运行页重试可视化（必须体现暂停态 UX）

监听新增 SSE 事件：

- `model_retry_attempt`：显示“第 N 次重试失败”。
- `model_retry_scheduled`：显示“将在 X 秒后重试”。
- `run_paused` / `subagent_paused`：显示暂停提示与“重试继续”按钮。
- 暂停提示必须包含：`已自动重试 N 次`、`当前等待人工恢复`、最近错误原因。

点击“重试继续”后：

- 调用 `POST /api/runs/{run_id}:resume`。
- 成功则恢复执行并重新推送当前 step 输出；失败则保留暂停并提示原因。

---

## 6. 配置项设计

新增系统配置（可进 `/api/system/configs`）：

```json
{
  "llm_retry": {
    "enabled": true,
    "max_attempts": 5,
    "base_delay_ms": 2000,
    "jitter": false,
    "per_request_timeout_ms": 30000,
    "pause_on_exhausted": true,
    "resume_requires_probe": false
  }
}
```

支持 profile 级覆盖（某些 provider 更严格限流时可单独调大）。

---

## 7. 可观测性与告警

- 日志字段：`run_id`, `instance_id`, `attempt`, `max_attempts`, `error_code`, `retryable`, `backoff_ms`。
- 指标：
  - `llm_retry_attempt_total{provider,model,error_code}`
  - `llm_retry_exhausted_total{provider,model}`
  - `llm_pause_total{scope}`
  - `llm_probe_latency_ms`
  - `llm_probe_success_ratio`
- 告警建议：
  - 5 分钟内 exhausted 激增。
  - 某 provider probe success ratio 低于阈值。

---

## 8. 测试方案

1. **单元测试**
   - 错误分类：429/5xx/timeout 可重试，401/400 不可重试。
   - 退避计算：指数增长与 jitter 边界。
2. **集成测试**
   - 模拟网络闪断后第 2 次成功，验证事件序列。
   - 连续失败到阈值，验证 run 进入 paused。
   - 调用 `:resume` 后继续并完成。
3. **前端测试**
   - 探测按钮状态流转。
   - SSE 重试事件可视化。
   - paused 时重试按钮显示与恢复。

---

## 9. 分阶段落地建议

### Phase 1（最小可用）

- 后端 `model:probe` 接口
- 统一重试执行器（仅 run 级）
- `run_paused/run_resumed` + 前端按钮

### Phase 2（完善）

- subagent 粒度暂停/恢复
- 更细粒度错误码映射
- 指标与告警接入

### Phase 3（增强）

- 自动网络恢复探测（后台定时 probe 后提示“一键恢复”）
- 不同 provider 的策略模板（OpenAI/Anthropic/本地模型）

---
