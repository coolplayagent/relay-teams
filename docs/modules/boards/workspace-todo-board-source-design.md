# Workspace TODO Board Source Design

## 设计目标

TODO Board source 设计解决三个问题：

1. 当前 GitHub issue source 只能从 workspace git remote 自动探测，用户不能配置。
2. 当前 cursor 和 repository state 偏 GitHub-only，不适合未来多 source。
3. `TaskBoardAdapter`、`GitHubAdapter`、`LinearAdapter`、`InternalBoardAdapter` 与真实 TODO Board 实现并存，但语义没有统一，且旧双向 tracker board API 不应继续作为 TODO Board 主线。

目标模型是：root board workspace 拥有一组可配置 source，source adapter 只负责同步和 evidence normalization，Agent Teams board service 负责 item persistence、状态机和 handoff。由 root workspace fork 出来的 `git_worktree` workspace 不拥有独立 TODO source，它的 TODO 页面共享 root board 的 source 配置和 cursor。旧 adapter 中可复用的读取和归一化逻辑可以迁移，旧 move/assign/state-map 语义规划删除。

## Source 类型

v1 支持一种用户可配置 source：

| Source kind | Provider | 说明 |
| --- | --- | --- |
| `github_issues` | `github` | 从一个 GitHub repository 的 issues 导入 TODO，PR 只作为 evidence |

预留扩展：

| Source kind | Provider | 说明 |
| --- | --- | --- |
| `linear_issues` | `linear` | 从 Linear team/project/view 导入 issue |
| `jira_issues` | `jira` | 从 Jira JQL/filter 导入 issue |
| `internal_tasks` | `internal` | 从 Agent Teams task repository 投影候选工作项 |
| `custom_http` | `custom` | 用户配置 HTTP source，由 adapter 归一化为 source record |

## Source 配置入口

看板 toolbar 最右侧提供 icon-only 齿轮设置按钮，打开 TODO settings 面板。面板同时承载来源列表、Grouped/Mixed 视图偏好和 Handoff template 设置；TODO 列 header 不再单独放来源设置入口。

设置面板属于解析后的 `board_workspace_id`：

- 请求参数中的 `workspace_id` 表示当前页面 `view_workspace_id`。
- 后端先执行 `resolve_board_workspace_id(view_workspace_id)`。
- 普通 workspace 只展示自身 board sources。
- `git_worktree` fork workspace 展示 root board sources；如果允许编辑，实际修改 root board source config。
- 新建 source 默认绑定 `board_workspace_id`，而不是 fork workspace。
- 切换 workspace 时 settings 面板按解析后的 `board_workspace_id` 加载对应配置。
- Connector 健康状态可以显示为辅助信息，但不是配置入口。

Fork workspace UI 必须清楚提示 source settings shared with root workspace，避免用户以为自己正在修改 fork-local board。

### Settings 面板内容

Source 列表显示：

- source display name。
- source kind/provider。
- enabled/disabled 状态。
- source scope，例如 `owner/repo`。
- 最近同步时间。
- 最近同步结果和 diagnostics。
- source 级 handoff template 是否覆盖。
- 刷新按钮。

Handoff Templates 区域显示：

- workspace 默认 `start` / `request_changes` 模板。
- 每个 source 的 `start` / `request_changes` override。
- source override 删除后回落 workspace default，再回落 built-in template。

GitHub source 编辑表单包含：

- `display_name`：用户可读名称。
- `enabled`：是否参与自动/手动 sync。
- `repository_full_name`：显式 `owner/repo`。
- `auto_detect_from_workspace`：允许用 workspace git remote 生成默认建议。
- `credential_ref`：凭据来源，初期可复用已有 GitHub trigger account 或 shared GitHub token。
- `sync_open_issues`：v1 固定为 true。
- `sync_pr_evidence`：v1 固定为 true。

## GitHub 自动探测

自动探测不再是唯一行为，也不是每次同步的真值来源。第一阶段实现为一次性 bootstrap：如果 root board workspace 的 git remote 能解析为 `owner/repo`，后端自动创建一个 enabled `github_issues` source；如果不能解析，则只返回 diagnostics，用户之后通过来源设置手动添加。

流程：

1. 用户打开 source settings 或触发 board sync。
2. 后端检查 `board_todo_workspace_state.todo_sources_bootstrapped`。
3. 如果尚未 bootstrap，后端读取 `board_workspace_id` 对应 root/source workspace 的 git remotes。
4. 如果找到 GitHub remote，创建一个 persisted `github_issues` source，例如 `owner/repo`。
5. 无论成功或失败，都标记 bootstrap 已尝试；后续以用户维护的 persisted source list 为准。

自动探测失败时：

- 不阻止用户手动输入 `owner/repo`。
- settings 面板显示诊断，例如 `Workspace has no GitHub remote`。
- board 仍可加载其他 enabled source；如果没有 source，board 为空。

如果用户在 fork workspace 页面打开设置或点击检测，自动探测仍以 root/source workspace 为准。fork workspace 的 git remote 不能作为 source identity；最多只能作为 UI 诊断中的辅助信息，不能驱动 fork-local source 创建。

## Source Identity

每个 source 必须有稳定 id，例如 `bsrc_{uuid}`。不要再把 source identity 隐含在 `(workspace_id, source_provider, source_key)` 中。

建议字段：

| 字段 | 说明 |
| --- | --- |
| `source_id` | 稳定 source id |
| `workspace_id` | 绑定 board workspace；目标语义可命名为 `board_workspace_id` |
| `kind` | `github_issues` 等用户可配置来源 |
| `provider` | `local`、`github`、`linear` 等 |
| `display_name` | 用户可读名称 |
| `enabled` | 是否启用 |
| `config` | provider-specific Pydantic model 序列化结果 |
| `created_at` / `updated_at` | 本地配置时间 |

`BoardTodoItem` 目标 identity 应保留：

- `source_id`
- `source_key`

并可在迁移期保留 `source_provider`、`source_type` 等兼容展示字段。目标实现不能再把这些字段作为跨 source identity。

其中 `source_key` 是 source 内部稳定 key，例如：

- GitHub issue record：`issue:{issue_number}`
- Linear issue：`issue:{linear_issue_id}`

跨 source 唯一性应由 `(board_workspace_id/source_workspace_id, source_id, source_key)` 保证，而不是把所有 GitHub repo 都压到同一个 workspace cursor。`workspace_id` 在迁移期可继续表示 source workspace；目标文档应优先使用 `board_workspace_id` 表达 board 归属、使用 `source_workspace_id` 表达 TODO 所属原 workspace。

命名层级固定为：

- `source.kind = github_issues`：workspace source 配置类型，表示一个 GitHub repository 的 issue source。
- `SourceRecord.source_type = github_issue`：adapter 输出的 normalized record 类型。
- `github_pull_request`：只允许作为兼容清理或 evidence 语境出现；PR 在新设计中不是 TODO source/card。

## Source State 和 Cursor

当前 `board_todo_workspace_state.github_issue_sync_cursor` 需要演进为 per-source cursor。

建议 source state：

| 字段 | 说明 |
| --- | --- |
| `source_id` | source id |
| `workspace_id` | board workspace id；目标语义可命名为 `board_workspace_id` |
| `revision` | 可选，source config/state revision |
| `sync_cursor` | adapter 私有 cursor，JSON 或 typed string |
| `last_sync_started_at` | 最近 sync 开始时间 |
| `last_sync_finished_at` | 最近 sync 完成时间 |
| `last_sync_status` | `idle`、`running`、`succeeded`、`failed` |
| `last_diagnostics` | 最近诊断信息 |

GitHub cursor v1：

- full sync：先为当前 source/repo 下所有带 linked GitHub PR refs 的 GitHub issue-backed board item 刷新 linked PR / merged evidence；再拉取 open issues 作为 active open issue set，最后对 missing/non-open records 做 reconcile。
- incremental sync：按 `updated_since` 拉取 changed issues 和 changed PRs。
- cursor 成功后推进到 sync start time 减 1 秒，保留当前实现的容错思想。

多 source 时，每个 source 独立推进 cursor。一个 source 失败不能阻止其他 source 同步。

## Grouped / Mixed Board Views

第一阶段 API 返回非持久化 `source_groups` 供前端展示：

- 每个 configured external source 生成一个 group。
- 如果旧 external item 引用的 source 已不存在，后端可从 item source 字段派生 missing-source group，前端使用普通来源缺失文案，不展示内部兼容术语。
- 旧 `source_provider=local` 或 `source_type=manual` 的 item 不再属于 supported board contract；board list/delta/sync 响应忽略这些 rows。

前端支持两种展示模式：

- `Grouped`：所有状态列都按 `source_groups` 折叠分组；空 group 默认不占用列空间。
- `Mixed`：所有状态列平铺卡片，不按来源嵌套；卡片仍显示来源标签。

### Fork Workspace Source Inheritance

Fork workspace 是 execution workspace 或 view workspace，不是独立 TODO source workspace。

规则：

- Fork workspace 不创建独立 `board_todo_sources`。
- Fork workspace 不创建独立 `board_todo_source_state` 或 cursor。
- Fork workspace 不因为打开 TODO 页面或点击 Sync 而创建独立 `board_todo_items`。
- Source settings 在 fork workspace 中展示 root board sources；编辑 source、启用/禁用 source、修改 GitHub `owner/repo`、修改 source template 都写入 root board workspace。
- 从 fork 页面触发 sync 时，sync root board sources 和 root cursors。
- GitHub repo/source config 只属于 root board workspace；自动探测只作为 root source 配置建议。

如果 root workspace 已删除、缺失或解析出现 cycle，source settings 和 sync 返回 diagnostics，例如 `board_scope_missing_root`，不得悄悄创建 fork-local source。

## Source Adapter 抽象

旧 `TaskBoardAdapter` 同时包含 `list_tasks`、`move_task`、`assign_task`、`add_comment`、`add_artifact`，更像外部 tracker 的双向 board 操作接口。新的 TODO Board 不应该让 adapter 拥有 board column state，因此只能迁移其读取和归一化思路；旧 `TaskBoardAdapter` 类型、`/api/boards/{board_id}/tasks` 和 `state-map` API 规划删除，不再改造成新的公开扩展点。

目标接口语义：

```text
BoardTodoSourceAdapter
  describe_config()
  validate_config(config)
  resolve_default_config(workspace)
  sync(config, state, mode) -> SourceSyncResult
  fetch_completion_evidence(item_refs) -> EvidenceResult
```

Source adapter 输出 normalized records，而不是直接写 board item：

```text
SourceRecord
  source_key
  source_type
  title
  body
  source_url
  source_updated_at
  external_status
  references
  raw_summary
```

```text
SourceEvidence
  source_key
  evidence_type
  linked_url
  linked_number
  completed
  completed_at
  reason
```

Board service 消费 adapter 输出：

- 根据 source record upsert `BoardTodoItem`。
- 根据 evidence 触发状态机事件。
- 根据 sync mode reconcile missing/closed external records。
- 写 revision/delta。

## GitHub Source 行为

### 导入规则

- GitHub `/issues` 中带 `pull_request` 对象的记录不是 TODO item。
- open issue 导入或更新为 TODO item。
- closed issue 不作为新 TODO 导入。
- 已存在 active item 被观察为 closed：
  - 如果 linked PR merged 且 item 已在 `review`，进入 `done`。
  - 如果 linked PR merged 但 item 仍在 `todo` 或 `in_progress`，只记录 evidence，等待 run lifecycle 或用户动作决定状态。
  - 如果 item 已在 `done`，保持 `done`，只补充 closed evidence，不自动 archive。
  - 否则自动 archive，并记录 source reason；archive 前必须取消 pending/claimed queue ticket、释放 queue slot，并 supersede active AI review attempt。若已有 active executor run，Boards 应请求 stop/cancel，且在 run 进入 terminal 前继续计入 workspace/runtime active slot；item 标为 archived 后忽略该 run 后续对 board status 的自动推进，只保留 event/diagnostic。

### PR Evidence

PR 不单独成为 TODO card。PR 的作用：

- 通过 issue timeline 或用户手动 link 关联到 imported issue TODO。
- merged PR 可以把 linked `review` item 推进到 `done`。
- 对 linked `todo` 或 `in_progress` item，merged PR 只记录 completion evidence，不抢占 board/run lifecycle。

### Reopen 行为

GitHub issue reopened 时：

- 只恢复由 source reconciliation 自动 archive 的 item。
- 用户手动 archive 的 item 不自动恢复。
- 恢复目标状态为 `todo`，不恢复 archive 前状态，避免绕过 session/run 状态机。

### Explicit Repo 优先级

GitHub source repository 解析优先级：

1. 已持久化 source config 中显式 `repository_full_name`。
2. 首次初始化配置时，从 root workspace git remote 自动识别出的 `owner/repo`。
3. 无配置时不执行 GitHub sync；只显示 diagnostics，并允许用户手动创建 source。

实现阶段的自动识别只负责创建可编辑的 persisted source。source 创建后，
`repository_full_name` 以用户可修改的 persisted config 为准；后续 sync 不再把
git remote 当作真值。

## Unsupported Local/Manual Rows

Manual TODO 不再是 supported source 或 supported create flow：

- 不再创建 system-managed `manual` source。
- 不再公开手动创建 TODO 的 API 或 UI。
- 旧 `source_provider=local` 或 `source_type=manual` 的 rows 视为过期/错误数据，不加载到 board response，不进入 `source_groups`，也不参与 sync/reconcile 展示。
- 旧 manual source rows 不出现在 source settings list 中。

## Linear/Internal Adapter 整合

旧 `LinearAdapter`、`InternalBoardAdapter` 可迁移为 `sources/linear.py` 和 `sources/internal.py` 的基础，但必须改变语义：

- `list_tasks` 转为 `sync` 输出 `SourceRecord`。
- `move_task` 不作为 board status 更新主路径。
- `add_comment`/`add_artifact` 可保留为 optional evidence delivery capability，但不属于 v1 必需能力。
- `TaskBoardStateMap` 不再映射到 TODO Board column；旧兼容 API 删除前只能视为兼容层，不作为新设计依赖。

旧 dispatcher 的 polling/worker outcome loop 不作为新 TODO Board 的调度核心。新设计由 source sync service 和 lifecycle bridge 分别负责外部同步与 run/session 状态消费；旧 dispatcher 相关 API 后续删除。

## Source Sync 模式

| 模式 | 用途 | 行为 |
| --- | --- | --- |
| `full` | 用户手动全量同步、首次同步 | 先刷新所有 linked PR / merged evidence，再拉取 source active set，最后 reconcile missing/non-open records |
| `incremental` | 背景刷新、页面 delta | 使用 per-source cursor 拉取 changed records |
| `preview` | 配置验证 | 不写 item，只返回可同步数量、诊断和示例记录 |

同步失败：

- source state 记录失败 diagnostics。
- board 仍返回已有本地数据。
- 失败 source 不推进 cursor。
- 多 source sync 时继续执行其他 source。

## API 方向

目标 API 仅作为设计方向，具体 schema 在实现阶段写入 core API 文档。

```text
GET    /api/boards/todo-sources?workspace_id=...
POST   /api/boards/todo-sources
PATCH  /api/boards/todo-sources/{source_id}
DELETE /api/boards/todo-sources/{source_id}
POST   /api/boards/todo-sources:detect
POST   /api/boards/todo-sources/{source_id}:sync
POST   /api/boards/todo-sources/{source_id}:preview-sync
```

API request 中的 `workspace_id` 是当前页面 `view_workspace_id`。Response 应返回 `board_workspace_id`、`view_workspace_id`、`is_fork_view` 和 `forked_from_workspace_id`，让前端缓存和设置面板以 root board 为主 key。

Source 删除语义：

- `DELETE /api/boards/todo-sources/{source_id}` 只允许删除没有 item、template、cursor、diagnostic 引用的 source。
- 如果 source 已经导入过 TODO 或仍有 active/done/archived item，首版 UI 应提供 disable/archive source 语义，而不是物理删除；`PATCH enabled=false` 保留 `source_id`、cursor、diagnostics 和 item/source context。
- 用户需要停止同步时使用 disable；已有 TODO 保持线性 board item，不因 source disabled 自动删除。
- 后续若支持强制删除，必须先写入 tombstone source record 或其他 explicit missing-source context，避免 preview/sync/diagnostic 出现 dangling `source_id`。

现有：

```text
POST /api/boards/todos:sync
POST /api/boards/todos:sync-changes
```

可以作为 workspace-level sync，内部遍历 enabled sources。手动 sync 默认 `full`，背景 sync 默认 `incremental`。

## 测试矩阵

| 场景 | 期望 |
| --- | --- |
| 显式 GitHub repo | sync 使用配置 repo，不读取 git remote 作为最终值 |
| 自动识别成功 | 若 board 还没有 GitHub source，则创建 enabled `github_issues` source；用户之后可修改 |
| 自动探测失败 | 用户仍可手动配置 GitHub repo |
| 多 GitHub source | 每个 source cursor 独立推进 |
| source disabled | 不参与自动和 workspace-level sync |
| delete unused source | 删除 source config 和空 cursor/diagnostic 引用 |
| delete source with imported items | 拒绝物理删除，引导使用 `enabled=false` |
| GitHub token 缺失 | 返回 source diagnostics，不影响其他已配置 source |
| issue open | upsert TODO item |
| issue closed without merged PR and item in `todo`/`in_progress`/`review` | 自动 archive |
| issue closed without merged PR and item in `done` | 保持 `done`，只记录 closed evidence |
| issue closed with merged PR and item in `review` | 进入 `done` |
| issue closed with merged PR and item in `todo`/`in_progress` | 只记录 PR merged evidence，不抢占 run lifecycle |
| issue reopened | 只恢复 source 自动 archive 的 item |
| fork workspace 打开 source settings | 展示 root board sources，并标明 shared with root workspace |
| fork workspace 编辑 GitHub repo | 修改 root board source config，不创建 fork-local source |
| fork workspace 触发 sync | 使用 root board sources/cursors，不读取 fork remote 作为 source identity |
| root workspace 缺失 | 返回 diagnostics，不创建 fork-local board/source |
