# Workspace TODO Board Design

本文档是 Workspace TODO Board 设计文档索引。详细设计已拆分到多个文件，避免单一文档继续承载产品目标、数据来源、状态机、AGENTS 交付和模块结构等不同层面的内容。

当前阶段只定义目标设计，不表示所有 API、数据库表或前端交互已经实现。实现阶段如果修改公共 API 或持久化结构，必须同步更新 `docs/core/api-design.md` 和 `docs/core/database-schema.md`。

## 文档入口

- [整体设计](workspace-todo-board-overview.md)
  - 说明 TODO 看板的产品目标、核心概念、系统边界和端到端用户流程。
  - 给出从 source sync 到 AGENTS handoff、session/run、review/done 的完整流程。

- [数据来源设计](workspace-todo-board-source-design.md)
  - 说明 source 配置模型、GitHub 显式配置、自动探测 fallback、多 source 同步和未来扩展方式。
  - 定义 `source_id` 目标 identity、legacy provider/type/key 兼容边界，以及旧 `TaskBoardAdapter` 可复用逻辑迁移后删除的方向。

- [状态机设计](workspace-todo-board-state-machine.md)
  - 定义 board 状态、run runtime 状态、session 删除、PR merged、issue closed/reopened、archive/restore 之间的关系。
  - 包含状态表、事件表和 Mermaid 状态图。

- [AGENTS 交付设计](workspace-todo-board-agent-handoff.md)
  - 说明用户点击 Start 后如何先编辑 prompt，再确认交给 AGENTS 执行。
  - 定义模板优先级、模板变量、preview API、start API 和 request changes 语义。

- [执行 Runtime、AI 发放和并发设计](workspace-todo-board-execution-runtime-design.md)
  - 说明 TODO handoff 如何绑定本地 role、外部 agent runtime 或 orchestration preset。
  - 定义 AI start、AI review、AI auto done、queue ticket、attempt phase 和 Workspace+Runtime 并发限制。

- [协作审计、Attempt 和诊断设计](workspace-todo-board-collaboration-design.md)
  - 说明 TODO 的 attempt/run history、comments/events、structured completion metadata、diagnostics、idempotency 和 TODO-bound worker context。
  - 明确 TODO 是线性列表，不引入 parent/child dependency graph，也不照搬 Hermes Kanban 的 `triage`、`ready`、`blocked` 等状态体系。

- [模块目录结构设计](workspace-todo-board-module-layout.md)
  - 规划 `src/relay_teams/boards/` 的目标目录结构、职责拆分、依赖方向和迁移策略。
  - 明确旧空 API 与旧 dispatcher 语义的迁移边界。

## 设计原则

- Board column state 由 Agent Teams 拥有，外部系统只提供 source record 和 completion evidence。
- TODO source 必须可配置；GitHub 自动探测只能作为默认建议，不能是唯一入口。
- TODO source 目标 identity 使用 `source_id + source_key`；`source_provider/source_type/source_key` 旧三元组只用于迁移兼容和展示。
- TODO board 的持久化归属由 `board_workspace_id` 决定；普通 workspace 的 `view_workspace_id` 与 `board_workspace_id` 相同，`git_worktree` fork workspace 只作为 view/execution workspace，共享最终 root workspace 的同一套 TODO board。
- AGENTS handoff 必须由用户确认最终 prompt 后触发，后端不再隐式拼装不可见提示词。
- AI 发放也必须走同一条 handoff preview/final prompt/start 管线，不能绕过模板、runtime target、execution workspace 或并发限制。
- 状态机必须以 session/run 生命周期为一等输入，避免卡片状态和实际执行状态分裂。
- linked PR merged 只自动完成已经处于 `review` 的 item；`todo` 或 `in_progress` item 只记录 evidence。
- 排队 handoff 必须保存完整 final prompt snapshot/ref，摘要不能作为后续 run 创建输入。
- `in_progress` 表示“已发放或处理中”；卡片展示从 queue ticket、attempt phase、run runtime status、review policy/state 和 diagnostics 等事实派生，不新增一套 Hermes 式公共子标签体系。
- TODO 来源是线性的；AI 或人工可以创建新的 TODO，但新 TODO 与原 TODO 不形成调度依赖。
- Boards 模块只拥有 TODO board 和 tracker/source 集成，不拥有 run-local todo 工具状态，也不绕过 sessions/runs 的生命周期边界。
