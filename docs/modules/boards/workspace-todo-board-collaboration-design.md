# Workspace TODO Board Collaboration, Attempts and Diagnostics Design

## 背景

对比 `hermes-agent` Kanban 后，Workspace TODO Board 可以吸收它在 durable audit、run attempts、worker context、diagnostics 和 idempotency 上的经验，但不能照搬它的任务依赖图和状态体系。

Agent Teams 的 TODO Board 是线性任务列表：

- TODO 之间没有 `parent`、`child`、`blocker` 或 `dependent` 调度关系。
- AI 或人工可以创建新的 TODO，但这些 TODO 是独立工作项，不继承调度依赖。
- Board 主状态仍是 `todo`、`in_progress`、`review`、`done`、`archived`。
- UI 展示不新增 `triage`、`ready`、`blocked` 等公共状态。需要表达等待、运行、暂停、review 等信息时，只展示底层事实：queue ticket、run runtime status、review attempt、diagnostics 等。

本设计补充的是 TODO 生命周期周边的协作和审计模型，而不是新的调度模型。

Fork workspace 是 view/execution context，不是独立 TODO board context。由 fork workspace 发起的 comment、attempt、event 或 AI-created TODO 都归属 root `board_workspace_id`；审计字段记录 `initiated_from_workspace_id`，用于说明动作来自哪个 workspace。

## Attempt History

`BoardTodoItem` 是逻辑任务，`BoardTodoAttempt` 是一次执行或 review 尝试。一个 TODO 可以有多次 attempt：

- 第一次 Start。
- Request changes 后的返工 run。
- AI review run。
- AI auto start 创建的执行 run。

### Attempt 类型

| attempt_type | 说明 |
| --- | --- |
| `start` | 从 `todo` 发放到 AGENTS 执行 |
| `request_changes` | 从 `review` 返工 |
| `ai_review` | executor run 完成后的 AI review |

### Attempt 字段

目标模型：

| 字段 | 说明 |
| --- | --- |
| `attempt_id` | attempt id |
| `todo_id` | 关联 TODO |
| `board_workspace_id` | TODO board 归属 workspace；fork view 解析到 root workspace |
| `initiated_from_workspace_id` | 发起该 attempt 的 view workspace，用于审计 |
| `attempt_type` | `start`、`request_changes`、`ai_review` |
| `status` | attempt 自身状态，例如 `pending`、`active`、`succeeded`、`failed`、`cancelled` |
| `source_workspace_id` | TODO 所属 workspace |
| `execution_workspace_id` | 实际执行 workspace |
| `execution_policy` | `fork_git_worktree` 或 `current_workspace` |
| `runtime_target_id` | executor 或 reviewer runtime target |
| `runtime_target_kind` | `local_role`、`external_role`、`orchestration_preset` |
| `session_id` | 绑定 session |
| `run_id` / `executor_run_id` | 绑定 executor run；迁移期可继续使用 `run_id` 字段名 |
| `review_session_id` | AI review run 所属 session；用于 session deletion callback 定位 reviewer attempt |
| `review_run_id` | AI review run，不能驱动 executor run 状态映射 |
| `queue_ticket_id` | 等待并发 slot 时的 ticket |
| `prompt_ref` | final prompt snapshot 引用 |
| `summary` | AGENTS 或 AI review 产出的摘要 |
| `metadata` | structured completion metadata |
| `error` | 失败原因 |
| `created_at` / `started_at` / `finished_at` | 时间戳 |

`BoardTodoItem` 只保存当前引用：

- `current_attempt_id`：最近一次 attempt。
- `active_attempt_id`：当前 active attempt，若没有执行或 review 中的 attempt 则为空。
- `run_id`/`executor_run_id`：当前 executor run；AI review run 使用 `review_run_id`。
- `diagnostic_count`：未清理 diagnostics 数量。
- `idempotency_key`：manual/AI-created TODO 的去重 key。

完整历史通过 attempt 查询展示，不把全部 run history 堆在 item 行上。

### Prior Attempts Context

Start、Request Changes 和 AI Review 的 preview 应能读取 prior attempts，并向模板暴露摘要变量：

| 变量 | 说明 |
| --- | --- |
| `attempt.current_id` | 当前 attempt id |
| `attempt.previous_summaries` | 最近若干次 attempt 摘要 |
| `attempt.previous_errors` | 最近失败原因 |
| `attempt.last_verification` | 最近一次验证结果 |
| `attempt.last_changed_files` | 最近一次变更文件摘要 |

Request Changes 必须优先包含 prior attempt 摘要和用户反馈，帮助 AGENTS 避免重复失败路径。

## Structured Completion Metadata

默认 Start、Request Changes 和 AI Review 模板应要求 AGENTS 输出结构化完成信息。该信息进入 attempt metadata，供 review UI、AI review 和后续 retry 使用。

标准字段：

| 字段 | 说明 |
| --- | --- |
| `changed_files` | 文件变更摘要，允许为空 |
| `verification` | 执行过的测试、检查或无法验证的原因 |
| `created_todos` | 新创建的线性 TODO 列表，不表示父子依赖 |
| `blocked_reason` | 本次 attempt 无法继续的原因，仅作为诊断信息，不引入 `blocked` board 状态 |
| `retry_notes` | 下次重试应注意的内容 |
| `residual_risk` | 剩余风险或人工 review 重点 |

`created_todos` 中的每个条目必须支持 `idempotency_key`，避免 AI 重复创建相同 TODO。

## Handoff Prompt Snapshot

Attempt 和 queue ticket 需要引用完整 final prompt snapshot。目标可以新增 `board_todo_handoff_prompts` 表或等价存储，但不能只保存摘要后再依赖尚未创建的 run/message history。

最小字段：

| 字段 | 说明 |
| --- | --- |
| `prompt_ref` | prompt snapshot id |
| `todo_id` | 关联 TODO |
| `attempt_id` | 关联 attempt |
| `template_source` | source/workspace/global/fallback 或 AI suggested source |
| `final_prompt_snapshot` | 用户或 AI policy 确认的完整 prompt |
| `created_at` | 创建时间 |

run 创建成功后，prompt 进入 session history。Board 侧 snapshot 继续用于 queue 恢复、attempt audit 和失败诊断。

## Comments

Board-level comment thread 用于保存 TODO 卡片自己的协作上下文，不替代 session message history。

Comment 来源：

| source | 用途 |
| --- | --- |
| `human` | 用户补充说明、review feedback、手工记录 |
| `agent` | TODO-bound AGENTS 主动报告进度或补充上下文 |
| `ai_review` | AI review 结论和摘要 |
| `source_sync` | 外部 source 同步提示 |
| `system` | 状态机、queue 或 diagnostics 产生的系统说明 |

目标字段：

| 字段 | 说明 |
| --- | --- |
| `comment_id` | comment id |
| `todo_id` | 关联 TODO |
| `board_workspace_id` | TODO board 归属 workspace |
| `initiated_from_workspace_id` | 可选，comment 从哪个 view workspace 写入 |
| `source` | comment 来源 |
| `author_id` | 用户、role、runtime target 或 system |
| `body` | comment 内容 |
| `attempt_id` | 可选 attempt 引用 |
| `run_id` | 可选 run 引用 |
| `created_at` | 创建时间 |

Request Changes feedback 应同时进入 handoff metadata 和 comment thread，便于 card detail 不打开 session 也能看到返工原因。

## Events

`BoardTodoEvent` 是 append-only audit log。它记录状态变化和重要事实，供 UI timeline、debug 和后续 reconciliation 使用。

事件不替代当前 item 状态；item 状态是可查询 projection，events 是历史。

事件类型：

| event_type | 说明 |
| --- | --- |
| `created` | manual 或 AI-created TODO 创建 |
| `imported` | source sync 导入 |
| `source_updated` | source record 更新 |
| `prompt_previewed` | handoff preview 生成 |
| `queued_for_start` | 并发满，创建 start queue ticket |
| `queued_for_request_changes` | 并发满，创建 request changes queue ticket |
| `queue_ticket_claimed` | queue ticket 获得 slot |
| `workspace_forked` | execution workspace fork 成功 |
| `run_started` | executor run 创建 |
| `run_completed` | executor run completed |
| `run_failed` | executor run failed/stopped/missing |
| `review_started` | AI review attempt 创建 |
| `review_completed` | AI review decision 写入 |
| `done` | TODO 进入 done |
| `archived` | TODO archived |
| `restored` | TODO restored |
| `diagnostic_created` | diagnostic 写入 |
| `diagnostic_cleared` | diagnostic 清理 |

目标字段：

| 字段 | 说明 |
| --- | --- |
| `event_id` | event id |
| `todo_id` | 关联 TODO |
| `board_workspace_id` | TODO board 归属 workspace |
| `initiated_from_workspace_id` | 动作发起 workspace；fork 页面操作时记录 fork workspace id |
| `event_type` | 事件类型 |
| `actor_type` | `human`、`ai`、`system`、`source`、`runtime` |
| `actor_id` | actor 引用 |
| `attempt_id` | 可选 attempt 引用 |
| `run_id` | 可选 run 引用 |
| `session_id` | 可选 session 引用 |
| `source_record_id` | 可选 source record 引用 |
| `payload` | 小型结构化 payload |
| `created_at` | 创建时间 |

events 必须按 `created_at` 和单调 event id 稳定排序。list/delta API 可以返回最近 events 摘要；完整 timeline 由 card detail 懒加载。

## Diagnostics

`BoardTodoDiagnostic` 是可恢复的事实提示，不是 board 状态。它用于解释为什么某个 TODO 暂时不能启动、review 或同步，也用于提示用户可采取的动作。

目标字段：

| 字段 | 说明 |
| --- | --- |
| `diagnostic_id` | diagnostic id |
| `board_workspace_id` | diagnostic 所属 root board workspace；source/global diagnostics 必填；board scope resolution 失败时可为空 |
| `initiated_from_workspace_id` | 触发该 diagnostic 的 view workspace，可为空 |
| `todo_id` | 可为空；source/global diagnostics 可不绑定具体 TODO |
| `source_id` | 可选 source 引用 |
| `attempt_id` | 可选 attempt 引用 |
| `kind` | 诊断类型 |
| `severity` | `info`、`warning`、`error`、`critical` |
| `title` | 简短标题 |
| `detail` | 详细说明 |
| `suggested_actions` | 可展示动作，例如 re-preview、retry、open settings、choose runtime |
| `created_at` | 创建时间 |
| `cleared_at` | 清理时间 |

所有 diagnostics 都必须可查询和清理。正常 source settings、sync 或 board diagnostic 按 `board_workspace_id` 查询；fork view 中产生的诊断仍写入 root `board_workspace_id`，并用 `initiated_from_workspace_id` 记录触发页面。若 board scope resolution 失败导致没有 root `board_workspace_id`，diagnostic 走 `initiated_from_workspace_id` + `kind` 的 view-scoped lookup/clear 路径，不创建 fork-local board。

诊断类型：

| kind | 说明 |
| --- | --- |
| `source_sync_failed` | source sync 失败 |
| `template_render_failed` | 模板渲染失败 |
| `runtime_target_unavailable` | runtime target 不存在或不可用 |
| `workspace_fork_failed` | fork git worktree 失败 |
| `queue_ticket_stale` | queue ticket lease 过期或无法恢复 |
| `run_missing` | bound run 丢失 |
| `session_deleted` | bound session 被删除 |
| `ai_review_failed` | AI review run 失败或输出不可解析 |
| `duplicate_ai_created_todo` | AI 创建 TODO 被 idempotency 去重 |
| `continuous_start_failure` | 连续 start/runtime failure 达到阈值 |
| `board_scope_missing_root` | fork workspace 无法解析到 root board workspace |
| `board_scope_cycle` | fork ancestry 出现 cycle，无法安全解析 board scope |

Diagnostics 显示在 card detail、source settings 和 start/review modal 中。清理规则由产生方决定，例如 source sync 成功后清理 `source_sync_failed`，用户重新选择 runtime 后清理 `runtime_target_unavailable`。

## Idempotency

去重分三层：

| 场景 | 去重键 |
| --- | --- |
| source item | `board_workspace_id/source_workspace_id` + `source_id` + `source_key` |
| manual/AI-created TODO | `board_workspace_id` + `idempotency_key` |
| start/request changes/AI review attempt | `todo_id` + `attempt_type` + `idempotency_key` 或 queue ticket id |

规则：

- Source sync 看到同一 source key 时更新现有 TODO，不创建重复 item。
- `source_provider`、`source_type` 和旧 `source_key` 组合只用于迁移兼容和展示，不再作为目标 source identity。
- Manual 和 AI-created TODO 的 `idempotency_key` 只在同一个 `board_workspace_id` 内去重；不同 root board workspace 可以安全使用相同业务 key。
- Manual TODO create 也必须携带稳定 `idempotency_key`；人工创建请求 timeout/retry 时返回已有 TODO 引用，不创建重复 manual item。
- AI-created TODO 必须携带 `idempotency_key`；同一 `board_workspace_id` 内重复创建返回已有 TODO 引用，并写 `duplicate_ai_created_todo` 诊断或 event。
- AI auto start 必须有 idempotency key，重复请求不能创建重复 queue ticket 或 run。
- Automation import 也必须携带 idempotency key。

在目标模型中，source item 去重应按 `board_workspace_id/source_workspace_id + source_id + source_key` 计算。`workspace_id` 旧字段只作为迁移期兼容，不应让 fork view 产生一套新的 source item identity。

## TODO-bound Worker Context

TODO-bound session/run 可以获得一个最小 board 工具集，但工具只在该上下文中暴露，不污染普通 session。

目标工具语义：

| tool | 说明 |
| --- | --- |
| `board_todo_show` | 读取当前 TODO、root board source context、execution workspace、prior attempts、comments 和 diagnostics |
| `board_todo_comment` | 向当前 TODO comment thread 写 comment |
| `board_todo_create` | 创建新的线性 TODO，必须携带 idempotency key |
| `board_todo_report_progress` | 写轻量进度 comment 或 event，不改变 board 主状态 |

边界：

- 工具必须校验当前 session/run 绑定的 `todo_id`。
- TODO-bound worker 默认只能操作当前 TODO。
- 工具读取 root board context；涉及文件修改、验证和路径时仍指向 execution workspace。
- `board_todo_create` 创建的是独立线性 TODO，不创建依赖关系。
- 不提供 worker 直接 `done` 当前 TODO 的默认工具；完成仍由 run terminal、AI review、用户确认或 completion evidence 驱动。

## 前端展示

Card detail drawer 应展示：

- 当前 board status。
- queue ticket 事实，例如是否等待 slot、排队时间、queue kind。
- 当前 run runtime status。
- review policy、review state 和 AI review decision。
- source workspace、execution workspace 和 branch。
- board workspace、当前 view workspace；fork view 中要提示 TODO board shared with root workspace。
- attempt history。
- comments。
- events。
- diagnostics。

主卡片展示应尽量使用具体事实文案，不引入独立状态体系。例如：

- `Waiting for runtime slot` 来自 active queue ticket。
- `Run paused` 来自 `RunRuntimeStatus=paused`。
- `AI review pending` 来自 review attempt 或 review state。
- `Needs attention` 来自未清理 error/critical diagnostic。

这些文案是派生展示，不是 board domain 的新增状态。

## 测试矩阵

| 场景 | 期望 |
| --- | --- |
| Start 成功 | 创建 `start` attempt，绑定 run/session/runtime/execution workspace |
| Request changes 成功 | 创建 `request_changes` attempt，并可读取 prior attempts |
| AI review | 创建 `ai_review` attempt，记录 decision 和 summary |
| run failed | attempt 记录 failed/error，event 写入 `run_failed` |
| 用户反馈 | 同时保存 handoff feedback metadata 和 comment |
| AI-created TODO | 使用 idempotency key 去重 |
| duplicate AI create | 返回已有 TODO，写 event 或 diagnostic |
| source sync 失败 | 写 source diagnostic，不影响 manual TODO |
| fork 失败 | 写 attempt error、event、diagnostic，不创建 run |
| queue ticket stale | 释放 ticket，写 diagnostic |
| TODO-bound tool 操作其他 TODO | 拒绝并记录诊断上下文 |
| card detail | 能展示 attempt、comment、event、diagnostic、source workspace、execution workspace |
| fork 页面 manual/AI create TODO | TODO 归属 root board，event 记录 fork 为 initiator |
| fork 页面 comment | comment 写入 root board TODO thread，metadata 记录 fork workspace |
| TODO-bound worker context in fork execution workspace | 读取 root board context，但 execution path 指向 execution workspace |

## 非目标

- 不设计 TODO dependency graph。
- 不设计 `triage`、`ready`、`blocked` 等 Hermes Kanban 状态。
- 不让 comments/events 成为外部 tracker 的 source of truth。
- 不让 Boards 模块直接执行 ACP/A2A/CLI runtime。
