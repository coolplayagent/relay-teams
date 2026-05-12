# Workspace TODO Board Module Layout Design

## 背景

当前 `src/relay_teams/boards/` 同时包含两套思路：

1. 真实运行中的 Workspace TODO Board：
   - `todo_models.py`
   - `todo_repository.py`
   - `todo_service.py`
   - `/api/boards/todos*`

2. 早期通用 task board/tracker adapter 原型：
   - `adapter.py`
   - `dispatcher.py`
   - `github_adapter.py`
   - `linear_adapter.py`
   - `internal_adapter.py`
   - `/api/boards/{board_id}/tasks` 等空实现或占位实现

这导致目录表达不清：

- `todo_service.py` 聚合了 source sync、GitHub remote 探测、GitHub API 调用、state reconciliation、session/run handoff、prompt 拼接等多个职责。
- 旧 `TaskBoardAdapter` 具备 move/assign/comment 等双向 tracker 语义，但 Workspace TODO Board 的目标是 Agent Teams 拥有状态、外部 tracker 提供 evidence。
- router 中同时暴露真实 TODO API 和旧 board task API，读者难以判断哪个是主线。

目标是迁移旧 adapter 中可复用的读取和归一化思路，但不保留旧双向 tracker board 语义。旧 `TaskBoardAdapter`、`/api/boards/{board_id}/tasks`、`/api/boards/state-map` 和 move/assign/state-map API 规划删除，不继续让两套抽象平行扩展。

## 目标目录结构

```text
src/relay_teams/boards/
  __init__.py
  api_models.py
  todo/
    __init__.py
    board_scope_service.py
    models.py
    repository.py
    service.py
    lifecycle.py
    attempt_models.py
    attempt_repository.py
    attempt_service.py
    comment_models.py
    comment_repository.py
    event_models.py
    event_repository.py
    diagnostic_models.py
    diagnostic_service.py
    handoff_models.py
    handoff_service.py
    execution_models.py
    execution_queue_repository.py
    execution_queue_service.py
    runtime_target_models.py
    runtime_target_service.py
    review_models.py
    review_service.py
    worker_context_service.py
    source_models.py
    source_repository.py
    source_service.py
  sources/
    __init__.py
    base.py
    github.py
    manual.py
    linear.py
  state/
    __init__.py
    transitions.py
    session_bridge.py
    run_bridge.py
```

## 模块职责

### `boards/api_models.py`

放置 router-facing request/response models，避免 router 内定义大量 Pydantic model。

职责：

- API request/response schema。
- 将 domain model 转换为 API response 的薄适配。
- 不访问 repository。
- 不包含业务决策。

### `boards/todo/models.py`

TODO Board domain model。

职责：

- `BoardTodoItem`
- `BoardTodoScope`
- `BoardTodoStatus`
- source provider/type enum。
- 目标 source identity：`source_id` + `source_key`，以 `(board_workspace_id/source_workspace_id, source_id, source_key)` 去重。
- legacy source provider/type/key 字段的兼容和展示边界。
- status counts。
- board/delta response 的 domain shape。
- 当前 attempt 引用、idempotency key 和 diagnostic count。

### `boards/todo/board_scope_service.py`

TODO board scope resolution 服务。

职责：

- 接收 router 传入的当前页面 `view_workspace_id`。
- 从 workspace profile 读取 `file_scope.backend` 和 `file_scope.forked_from_workspace_id`。
- 普通 project workspace 返回自身作为 `board_workspace_id`。
- `git_worktree` workspace 沿 `forked_from_workspace_id` 追溯最终 root workspace，返回 `BoardTodoScope`。
- 处理 cycle guard、root workspace missing、权限不足和脏 fork metadata diagnostics。
- 为 source、handoff、queue、lifecycle、repository 查询提供统一 board scope。

不负责：

- 创建或删除 workspace fork。
- 修改 board item 状态。
- 读取 GitHub remote 或 source config。

不负责：

- GitHub API shape。
- router request parsing。
- template rendering。
- attempt history、comments、events 或 diagnostics 的完整持久化。

### `boards/todo/repository.py`

TODO item persistence。

职责：

- `board_todo_items` CRUD。
- revision/delta 查询。
- archive/restore 所需基础读写。
- 按 source/session/run/link PR 查询。
- item 上 current/active attempt 和 diagnostic count 的轻量引用维护。

不负责：

- 调 GitHub。
- 创建 run。
- 渲染 prompt。
- 判断 run status 映射。

### `boards/todo/attempt_models.py`

TODO attempt domain models。

职责：

- `BoardTodoAttempt`
- attempt type：`start`、`request_changes`、`ai_review`。
- attempt status：`pending`、`active`、`succeeded`、`failed`、`cancelled`。
- 内部 attempt phase，例如等待 slot、准备 workspace、创建 run。
- structured completion metadata model。
- `prompt_ref` 引用完整 final prompt snapshot，而不是只保存摘要。

不负责：

- board status transition。
- 直接创建 run。

### `boards/todo/attempt_repository.py`

Attempt history persistence。

职责：

- 保存 start、request changes、AI review attempt。
- 按 TODO 查询 attempt history。
- 绑定 session/run/runtime/execution workspace。
- 保存 summary、metadata 和 error。

### `boards/todo/attempt_service.py`

Attempt application service。

职责：

- 创建 attempt。
- 更新 attempt status 和 metadata。
- 为 handoff preview 和 worker context 汇总 prior attempts。
- 将 run terminal/review decision 映射到 attempt result。

不负责：

- 决定 board status。
- 渲染 prompt。

### `boards/todo/comment_models.py`

Board-level comment domain models。

职责：

- `BoardTodoComment`
- comment source：`human`、`agent`、`ai_review`、`source_sync`、`system`。
- comment 与 attempt/run/session 的可选引用。

### `boards/todo/comment_repository.py`

Comment persistence。

职责：

- 按 TODO 写入和查询 comments。
- 提供 card detail 和 worker context 所需摘要。

### `boards/todo/event_models.py`

Append-only event domain models。

职责：

- `BoardTodoEvent`
- event type enum，例如 `created`、`imported`、`prompt_previewed`、`queued_for_start`、`workspace_forked`、`run_started`、`run_completed`、`review_completed`、`done`、`archived`。
- actor 和 payload model。

### `boards/todo/event_repository.py`

Event persistence。

职责：

- 追加事件。
- 按 TODO 查询 timeline。
- 支持 board delta 或 card detail 的最近 events 摘要。

### `boards/todo/diagnostic_models.py`

Diagnostic domain models。

职责：

- `BoardTodoDiagnostic`
- severity：`info`、`warning`、`error`、`critical`。
- diagnostic kind，例如 source sync failed、template render failed、runtime unavailable、workspace fork failed、queue stale、run missing、AI review failed。
- suggested action model。

### `boards/todo/diagnostic_service.py`

Diagnostic application service。

职责：

- 创建、清理和查询 diagnostics。
- 根据 source sync、handoff、queue、run/session lifecycle 和 AI review 结果写入诊断。
- 维护 item 上 `diagnostic_count` projection。

不负责：

- 用 diagnostics 替代 board status。

### `boards/todo/service.py`

TODO Board application service。

职责：

- 编排 repository、source service、handoff service、lifecycle service。
- 提供 router 使用的高层方法：list board、sync board、start、request changes、archive、restore、link PR。
- 在触碰任何 board persistence 前调用 `board_scope_service`，把 `workspace_id` 参数解析为 `view_workspace_id` 和 `board_workspace_id`。
- 保持事务/失败恢复边界。

目标是让此文件变薄，不再直接包含所有 GitHub sync 和 prompt 拼接细节。

### `boards/todo/lifecycle.py`

Board 状态机和生命周期策略。

职责：

- 执行 `todo -> in_progress`、`in_progress -> review` 等 transition。
- 校验 transition guard。
- 生成 `last_status_reason`。
- 处理 run completed/failed/stopped/missing。
- 处理 session deleted。
- 处理 linked PR merged evidence；只自动执行 `review -> done`，`todo`/`in_progress` 仅记录 evidence。
- 区分 executor `run_id` 与 AI `review_run_id`，避免 review run terminal 误触发 executor 状态映射。

可以与 `state/transitions.py` 配合：`state/transitions.py` 定义纯 transition table，`todo/lifecycle.py` 执行 repository 更新。

### `boards/todo/handoff_models.py`

Handoff domain models。

职责：

- template scope。
- template kind。
- preview request/response。
- final handoff request。
- template variables model。
- prompt snapshot/ref model，保存排队 handoff 所需的完整 final prompt。
- runtime target、execution policy、review policy 的 request/response 引用。

### `boards/todo/handoff_service.py`

Handoff prompt rendering 和模板选择。

职责：

- 根据 todo item/source/workspace 选择模板。
- 渲染 preview prompt。
- 处理 source/workspace/global/fallback 优先级。
- 输出 diagnostics。
- 输出 attempt context、prior attempts 摘要和 unresolved diagnostics 摘要。
- 在 Start、request changes 和 AI auto start 确认后保存完整 final prompt snapshot/ref，供 queue ticket 和 attempt 引用。

不负责：

- 创建 run。
- 修改 board status。
- 分配并发 slot。
- 直接调用 external agent runtime。

### `boards/todo/execution_models.py`

执行编排 domain models。

职责：

- `ExecutionPolicy`：`fork_git_worktree`、`current_workspace`。
- queue kind：`start`、`request_changes`、`ai_review`。
- concurrency snapshot 和 queue preview models。
- queue ticket claim/lease models。
- 内部 attempt phase 引用；不作为 board 公共状态。

### `boards/todo/execution_queue_repository.py`

Execution queue persistence。

职责：

- 保存 queue ticket。
- 保存或引用完整 final prompt snapshot/ref；queue worker 创建 run 时必须能读取完整 prompt。
- 根据 `board_workspace_id/source_workspace_id`、runtime target 和 queue kind 查询 pending ticket。
- 原子更新 ticket status。
- 保存 `claim_token`、`claim_expires_at`、`claimed_by` 和 `failure_count`。
- 保存 diagnostics。

不负责：

- fork workspace。
- 创建 session/run。
- 选择 runtime target。

### `boards/todo/execution_queue_service.py`

并发 slot 和 queue 编排。

职责：

- 按 Workspace+Runtime 双口径检查 active slot。
- v1 默认 `max_active_per_source_workspace = 2`，统计口径是 `board_workspace_id/source_workspace_id`，不是 fork view workspace。
- v1 默认 `max_active_per_runtime_target = 1`。
- `queue_kind=start` 和 `queue_kind=request_changes` 同时检查 workspace/source active slot 与 executor runtime slot。
- `queue_kind=ai_review` 只检查 reviewer runtime slot，不占用 source workspace execution slot，也不阻塞实现类 TODO 的 workspace 并发额度。
- 并发满且允许 queue 时创建 queue ticket。
- slot 可用时抢占 ticket，驱动 workspace preparation 和 run creation。
- run terminal、start failure、session deleted 时释放 slot。
- claim lease 过期时恢复或失败 ticket，并写 diagnostic/event。
- 支持 `max_runtime_seconds` 和连续 start/runtime failure diagnostics。

不负责：

- 渲染 prompt。
- 直接执行 runtime。
- 调外部 tracker。

### `boards/todo/runtime_target_models.py`

Runtime target domain models。

职责：

- `RuntimeTargetKind`：`local_role`、`external_role`、`orchestration_preset`。
- runtime target option、capability、diagnostic models。
- allowed runtime target policy。

### `boards/todo/runtime_target_service.py`

Runtime target 解析服务。

职责：

- 从 role registry 生成 local role 和 external role target。
- 从 orchestration settings 生成 orchestration preset target。
- 根据 workspace/source policy 过滤 allowed targets。
- 生成 preview 默认 runtime target 和 diagnostics。

不负责：

- 读取 external runtime secrets。
- 启动 ACP/A2A/CLI runtime。
- 创建 run。

### `boards/todo/review_models.py`

Review domain models。

职责：

- `ReviewPolicy`：`human_required`、`ai_pre_review`、`ai_auto_done`。
- AI review 状态和 decision model。
- AI review decision 和 summary models。

### `boards/todo/review_service.py`

AI review 编排。

职责：

- 根据 review policy 决定是否启动 AI review。
- 渲染 AI review prompt/template。
- 选择 reviewer runtime target。
- 创建 review run 或进入 review queue。
- 将 AI review 输出归一化为 decision。
- 在 `ai_auto_done` 且 approved 时请求 lifecycle 执行 `review -> done`。
- 创建和更新 `ai_review` attempt。

不负责：

- 自动触发 request changes。
- 绕过 lifecycle 直接写 done。

### `boards/todo/worker_context_service.py`

TODO-bound worker context 服务。

职责：

- 为 TODO-bound session/run 生成 worker context。
- 汇总 TODO、root board source context、execution workspace、prior attempts、comments、events 和 diagnostics。
- 定义 TODO-bound tool 可见性边界。
- 校验工具调用只能操作当前 session/run 绑定的 TODO。

不负责：

- 暴露全局 board 管理工具。
- 让 worker 直接将 TODO 标记为 `done`。

### `boards/todo/source_models.py`

Source config 和 normalized record/evidence model。

职责：

- `BoardTodoSource`
- `BoardTodoSourceKind`
- `BoardTodoSourceState`
- `SourceRecord`
- `SourceEvidence`
- provider-specific config Pydantic models。

### `boards/todo/source_repository.py`

Source config/state persistence。

职责：

- source 配置 CRUD。
- per-source cursor。
- sync diagnostics。
- enabled source 查询。

### `boards/todo/source_service.py`

Source sync orchestration。

职责：

- 根据 `board_workspace_id/source` 调用 adapter。
- 将 adapter 输出交给 board item repository/lifecycle。
- 支持 full/incremental/preview sync。
- 多 source 错误隔离。
- 管理 per-source cursor。

不负责：

- 外部 provider 细节。
- run/session lifecycle。

### `boards/sources/base.py`

Source adapter protocol/base class。

职责：

- 定义 adapter 接口。
- 定义 sync result。
- 定义 capability flags，例如是否支持 comments/artifacts。

### `boards/sources/github.py`

GitHub Issues source adapter。

职责：

- GitHub repo config validation。
- workspace remote auto-detect suggestion。
- GitHub issue/PR/timeline API 调用。
- 输出 `SourceRecord` 和 `SourceEvidence`。
- 使用现有 proxy-aware `net.clients` 和 GitHub client 能力。

不负责：

- 直接写 `BoardTodoItem`。
- 直接设置 board status。

### `boards/sources/linear.py`

Linear source adapter，迁移旧 `linear_adapter.py` 思路。

职责：

- Linear issue list normalization。
- 输出 source records。
- 可选 comment/artifact capability。

v1 可以只保留设计和空 adapter，不必立即暴露 UI。

### `boards/state/transitions.py`

纯状态机定义。

职责：

- transition table。
- event enum。
- guard 结果。
- 纯函数：给定当前状态和事件，返回目标状态或 conflict。

### `boards/state/session_bridge.py`

Session lifecycle bridge。

职责：

- sessions 模块删除 session 时调用的窄接口。
- 将 session deleted 转为 board lifecycle event。

### `boards/state/run_bridge.py`

Run lifecycle bridge。

职责：

- runs 模块 run terminal 时调用的窄接口。
- 将 run completed/failed/stopped/missing 转为 board lifecycle event。

## 依赖方向

```text
interfaces/server/routers
  -> boards/api_models
  -> boards/todo/service
  -> boards/todo/{board_scope_service,repository,lifecycle,attempt_service,comment_repository,event_repository,diagnostic_service,handoff_service,source_service,execution_queue_service,runtime_target_service,review_service,worker_context_service}
  -> boards/sources/*
  -> sessions/runs service protocols
```

规则：

- routers 不访问 repository。
- routers 传入的 `workspace_id` 只表示 `view_workspace_id`；domain service 统一通过 board scope service 解析 `board_workspace_id`。
- source adapters 不访问 board item repository。
- lifecycle 不调用外部 provider。
- handoff service 不创建 run。
- execution queue 不渲染 prompt，不直接执行 runtime。
- attempt service 不决定 board status。
- diagnostics 不替代 board status。
- runtime target service 不读取外部 runtime secrets。
- review service 通过 sessions/runs 创建 review run，不直接调用 provider。
- source service 不写 message history。
- sessions/runs 通过 bridge/protocol 通知 boards，避免反向深耦合。

## 旧文件迁移策略

| 当前文件 | 目标位置 | 说明 |
| --- | --- | --- |
| `todo_models.py` | `todo/models.py` | 拆出 API request models 到 `api_models.py` |
| `todo_repository.py` | `todo/repository.py` | 保留 item persistence，新增 source id 后迁移 |
| `todo_service.py` | `todo/service.py` + 多个子服务 | 拆出 GitHub sync、handoff、lifecycle |
| start/request changes queue 逻辑 | `todo/execution_queue_service.py` | 新增并发和 queue 编排 |
| start/request changes/review history | `todo/attempt_service.py` | 新增 attempt history |
| card comments | `todo/comment_repository.py` | 新增 board-level comment thread |
| card events | `todo/event_repository.py` | 新增 append-only audit log |
| diagnostics | `todo/diagnostic_service.py` | 新增可恢复诊断 |
| TODO-bound worker context | `todo/worker_context_service.py` | 新增 AGENTS 执行上下文边界 |
| role/external runtime option 解析 | `todo/runtime_target_service.py` | 新增 runtime target 解析 |
| AI review 逻辑 | `todo/review_service.py` | 新增 review policy 和 decision 映射 |
| `adapter.py` | 删除，迁移可复用类型到 `sources/base.py` + `todo/source_models.py` | `TaskBoardAdapter` 不作为新公开接口 |
| `github_adapter.py` | `sources/github.py` | 只迁移 issue/PR 读取和 records/evidence 输出 |
| `linear_adapter.py` | `sources/linear.py` | 只迁移 issue list normalization |
| `internal_adapter.py` | 后续删除或独立 source adapter | internal projection 不作为 v1 主线 |
| `dispatcher.py` | 删除 | 旧 polling/worker outcome loop 不作为新 TODO Board 核心 |
| `controlled_tools.py` | 迁移到 `todo/worker_context_service.py` 暴露的 TODO-bound tools，或随旧 adapter 一起删除 | 旧 `TaskBoardAdapter.move_task/add_artifact` 工具不作为新公开写入路径 |
| router 内 BoardTask models | 删除 | 旧空 API 规划删除，不作为 legacy 长期保留 |

## 旧 API 处理

当前 router 中有两类 API：

真实主线：

```text
/api/boards/todos*
```

旧占位：

```text
/api/boards
/api/boards/{board_id}/tasks
/api/boards/{board_id}/sync
/api/boards/{board_id}/tasks/{task_id}/state
/api/boards/state-map
```

目标：

- 新 TODO Board 能力继续围绕 `/api/boards/todos*` 和新的 todo source/handoff endpoints。
- 旧 `/api/boards/{board_id}/tasks` 不作为 Workspace TODO Board 扩展入口，并规划删除。
- 旧 `/api/boards/state-map` 属于 task-board adapter 兼容，不是 TODO Board 状态机，并规划删除。
- 前端和新 API 不得依赖旧 BoardTask/TaskBoardStateMap 实现新 TODO 看板。

## Migration Phases

### Phase 1: 文档和模型设计

- 拆分文档。
- 定义 source、handoff、state machine、目录结构。
- 不改业务代码。

### Phase 2: Source 配置持久化和 source_id 双轨

- 新增 board scope service，让所有 TODO Board API 先把 `view_workspace_id` 解析为 `board_workspace_id`。
- 新增 source config/state models 和 repository。
- 新增 `source_id` 并把目标去重键定义为 `(board_workspace_id/source_workspace_id, source_id, source_key)`。
- 迁移期保留 `source_provider/source_type/source_key` 旧字段用于展示、读旧数据和兼容现有 rows，但文档和新写入逻辑以 `source_id` 为准。
- GitHub source 显式配置。
- workspace source settings API。
- fork workspace source settings 读写 root board sources，不创建 fork-local source/cursor。
- 保留旧自动 remote 作为迁移 fallback。

### Phase 3: GitHub sync 迁移

- 将 `BoardTodoService._sync_github` 拆到 `sources/github.py` 和 `source_service.py`。
- cursor 改为 per-source。
- workspace-level sync 遍历 enabled sources。

### Phase 4: Handoff 迁移

- 新增 template models/service。
- 新增 preview endpoints。
- 前端 Start/Request changes 改为 prompt editor。
- 后端 Start 使用 final prompt。
- preview/start 增加 runtime target、execution policy、review policy 和 concurrency preview。

### Phase 5: Execution Queue 和 Runtime Target

- 新增 runtime target models/service。
- 新增 handoff prompt snapshot/ref 存储，queue ticket 和 attempt 通过 `prompt_ref` 找回完整 final prompt。
- 新增 execution queue repository/service。
- 实现 Workspace+Runtime 双口径并发限制。
- `in_progress` 通过 queue ticket、attempt phase 和 run runtime 派生展示。
- queue ticket 获得 slot 后再 fork workspace 和创建 session/run。

### Phase 6: Attempts、Comments、Events 和 Diagnostics

- 新增 attempt models/repository/service。
- 新增 comment repository。
- 新增 event repository。
- 新增 diagnostic models/service。
- Handoff、queue、review 和 lifecycle 写入 attempt/event/diagnostic。
- Card detail 展示 attempt history、comments、events 和 diagnostics。

### Phase 7: Lifecycle 和 Review 拆分

- run/session bridge 迁移到 `state/` 或 `todo/lifecycle.py`。
- reconcile 逻辑集中。
- 补齐 run status 映射测试。
- 新增 review models/service。
- 支持 `human_required`、`ai_pre_review`、`ai_auto_done`。
- AI review 创建 `ai_review` attempt。

### Phase 8: TODO-bound Worker Context

- 新增 worker context service。
- 定义 TODO-bound tool 暴露边界。
- 支持 `board_todo_show`、`board_todo_comment`、`board_todo_create`、`board_todo_report_progress` 的目标语义。
- `board_todo_create` 创建线性 TODO，不创建依赖关系。

### Phase 9: Legacy adapter 和 API 删除

- 迁移旧 adapters 中可复用的 provider 读取和 normalization 代码。
- 删除旧 `TaskBoardAdapter` 双向 tracker state 语义。
- 删除旧空 API：`/api/boards/{board_id}/tasks`、`/api/boards/{board_id}/sync`、`/api/boards/{board_id}/tasks/{task_id}/state`、`/api/boards/state-map`。
- 更新 `docs/core/project-layout.md` 和模块边界文档。

## 测试布局

目标测试目录：

```text
tests/unit_tests/boards/
  todo/
    test_board_scope_service.py
    test_models.py
    test_repository.py
    test_lifecycle.py
    test_attempt_service.py
    test_comment_repository.py
    test_event_repository.py
    test_diagnostic_service.py
    test_handoff_service.py
    test_execution_queue_service.py
    test_runtime_target_service.py
    test_review_service.py
    test_source_service.py
    test_worker_context_service.py
  sources/
    test_github_source.py
    test_manual_source.py
    test_linear_source.py
  state/
    test_transitions.py
  test_api_models.py
```

集成测试：

```text
tests/integration_tests/api/test_board_todo_sources.py
tests/integration_tests/api/test_board_todo_scope.py
tests/integration_tests/api/test_board_todo_handoff.py
tests/integration_tests/api/test_board_todo_execution_queue.py
tests/integration_tests/api/test_board_todo_review.py
tests/integration_tests/api/test_board_todo_collaboration.py
tests/integration_tests/api/test_board_todo_lifecycle.py
```

前端测试：

```text
tests/unit_tests/frontend/test_board_todo_source_settings_ui.py
tests/unit_tests/frontend/test_board_todo_handoff_ui.py
tests/unit_tests/frontend/test_board_todo_execution_labels_ui.py
tests/unit_tests/frontend/test_board_todo_review_ui.py
tests/unit_tests/frontend/test_board_todo_detail_timeline_ui.py
```

## 验收标准

- 新目录结构能让读者从文件名判断职责。
- `todo_service.py` 不再是 source sync、prompt rendering、state machine、run creation 的混合大文件。
- 旧 `TaskBoardAdapter` 概念被迁移可复用读取逻辑后删除，避免与 TODO Board 状态机冲突。
- Router 保持薄层，只做 request/response 和 HTTP error mapping。
- Runtime target 覆盖本地 role、外部 agent role 和 orchestration preset，但 Boards 不直接执行 external runtime。
- 并发上限由 execution queue 统一控制，AI auto start 不能绕过。
- AI review 通过 lifecycle 进入 done，不直接写 board status。
- linked PR merged 只自动推进 linked `review` item；`todo`/`in_progress` item 只记录 evidence。
- queue ticket 和 attempt 必须引用完整 prompt snapshot/ref。
- Root workspace 和 fork workspace 通过 `board_scope_service` 解析到同一个 `board_workspace_id`，共享 TODO items、sources、cursors、templates、revision 和 delta。
- Fork workspace 不创建独立 source/cursor/item；source settings、sync、manual create、archive/restore 都操作 root board，并在 event/attempt 中记录发起 workspace。
- TODO 是线性列表，不引入 dependency graph 或 dependency service。
- 卡片细节从 queue ticket、attempt、run runtime、review state 和 diagnostics 派生，不引入 Hermes 式公共状态标签体系。
- Attempt/comment/event/diagnostic history 能解释 Start、Request Changes、AI Review 和失败恢复过程。
- 新增 API/DB 时，core API/schema 文档同步更新。
