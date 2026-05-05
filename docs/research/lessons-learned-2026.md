# 2026 AI Agent 研究借鉴分析 — relay-teams 改进路线图

> **文档类型**: Feature Analysis  
> **创建日期**: 2026-04-25  
> **feature_ids**: lessons-learned-2026  
> **topics**: architecture, orchestration, spec-driven, security, engineering, enhancements  
> **doc_kind**: analysis  

---

## 概述

本文档基于 **2026 年 AI Agent 领域的 38 篇前沿研究**，系统性地提取了 **25 个结构化借鉴点**，覆盖架构优化、角色与编排、Spec-Driven 流程、安全与治理、工程实践、功能增强六大维度。每个借鉴点均经过源码验证，与 relay-teams 当前实现进行详细对比，并给出可操作的实施建议和优先级评估。

2026-04-28 增补分析继续复盘 hello 项目中的 AI 相关 Markdown 语料，并将本文件引用的 hello 来源材料按主题整理到本目录下的研究分类中，归档说明见 [`lessons-learned/hello-archive.md`](lessons-learned/hello-archive.md)：排除 `node_modules` 后共扫描 1272 个 Markdown 文件，其中英文工程关键词粗筛命中 1123 个 AI/Agent/LLM/SDD/MAS/Harness 相关文件；补充中文关键词后命中 1226 个文件，覆盖人工智能、智能体、大模型、多智能体等中文综述材料。新增复盘重点放在 Coding Agent 协作产品源码级横评、2026 Harness 工程归档、SDD/SPDD/形式化验证归档、MAS/A2A/Google 企业 Agent 归档、中文 AI Agent 研究综述与市场材料。该增补未推翻原 25 个借鉴点，而是修正部分已落地状态，并补充 13 个更偏运营化和产品化的缺口。

2026-04-29 进一步复盘 [`coding-agent-orchestration/openai-symphony-research.md`](coding-agent-orchestration/openai-symphony-research.md) 及 OpenAI Codex/Harness 归档后，补充了一个更直接面向产品形态的结论：任务看板不应只是展示层，而可以作为定义任务、承载状态、触发 Agent 执行、回收证据和审查 PR 的控制平面。OpenAI Symphony 将 Linear 工单状态作为状态机输入，并用私有调度状态机管理 claim、running、retry、reconciliation 和 stall timeout，这一点可直接补强 relay-teams 的任务看板、运行时调度和外部任务系统集成路线。

2026-04-29 追加复盘 hello 项目的 [`spdd/`](spdd/) 后，补充一个 SDD 之外的更细颗粒度结论：对复杂、长期维护、强约束任务，规格不应只作为一次性 prompt section 注入，而应升级为可版本化、可审查、可同步的 Structured Prompt Artifact。SPDD 的 REASONS Canvas 将 Requirements、Entities、Approach、Structure、Operations、Norms、Safeguards 拆成七维合约，并强调 prompt 与代码双向同步，这能直接补强 relay-teams 已有 `TaskSpec`、`VerificationPlan` 和 Evidence Bundle 路线。

2026-04-29 同步复盘 hello 项目的 [`formal-verification/`](formal-verification/) 后，补充一个面向高严格度任务的结论：relay-teams 的验证层不应只依赖命令输出和自然语言 Gater 判断，而应为关键状态机、协议、调度、预算和安全策略提供可选的轻量形式化验证通道。TLA+/Alloy 适合先验证状态转移和不变式，Lean/Coq/Isabelle 适合承载数学、算法或高价值规约证明；这些机器可检查证据可以成为 `VerificationPlan` 与 Evidence Bundle 的高可信输入。

## 研究背景

### 研究方法

本研究采用以下方法论：

1. **文献收集**：系统整理 2026 年 AI Agent 领域 38 个 markdown 格式研究文件，涵盖学术论文、行业报告、框架文档
2. **研究点提取**：从中提炼出 **35 条核心研究点**，涵盖 Harness Engineering、Self-Evolving Agents、Spec-Driven Development、Agent 安全、Context Engineering 等关键主题
3. **交叉引用分析**：将 35 条研究点与 relay-teams 源码现状逐一对照，识别出 **25 个可落地的改进借鉴点**
4. **源码验证**：关键数据点均通过源码实际验证（如文件行数、字段存在性、方法签名等），验收准确率达 **92.5%**
5. **增补复盘**：2026-04-28 对 hello 项目的 AI Markdown 语料做二次分组分析，并把本文件引用的来源副本按主题整理到 `coding-agent-orchestration/`、`harness/`、`sdd/`、`mas/`、`google-cloud-next/` 等研究目录；分析优先采用 `coding-agent-orchestration/coding-agent-collaboration-*.md`、`harness/2026/`, `sdd/2026/`, `mas/2026/`, `google-cloud-next/` 等汇总材料，再回查 relay-teams 当前源码确认已落地与缺口
6. **OpenAI Symphony 复盘**：2026-04-29 单独复盘 OpenAI Symphony/Linear 状态机材料，抽取“任务看板即控制面”的调度模式，并对照 relay-teams 的 `TaskStatus`、`TaskRepository`、`TaskOrchestrationService` 和运行事件实现确认缺口
7. **SPDD 复盘**：2026-04-29 追加复盘 hello `spdd/`，将 Thoughtworks SPDD、OpenSPDD CLI、SDD 生态对照映射到 relay-teams 的 TaskSpec、prompt 持久化、证据包和审查闭环缺口
8. **形式化验证复盘**：2026-04-29 追加复盘 hello `formal-verification/`，将 TLA+、Alloy、Lean、Coq、Isabelle、seL4、CompCert 等形式化方法经验映射到 relay-teams 的高严格度验证、状态机不变式和 Evidence Bundle 缺口

### 关键数据来源

| 来源 | 内容 | 数量 |
|------|------|------|
| `cross-reference-analysis.md` | 综合借鉴分析报告 | 25 个借鉴点 |
| `markdown-research-points.md` | 研究点提取报告 | 35 条研究点 |
| `validation-report.md` | 验收报告 | 覆盖率 74.3%，准确率 92.5% |
| [`coding-agent-orchestration/coding-agent-collaboration-research.md`](coding-agent-orchestration/coding-agent-collaboration-research.md), [`coding-agent-orchestration/coding-agent-collaboration-overview.md`](coding-agent-orchestration/coding-agent-collaboration-overview.md) | Paperclip / Multica / Routa / SpectrAI 源码级横评 | 7 个对比维度、6 个趋势 |
| [`coding-agent-orchestration/openai-symphony-research.md`](coding-agent-orchestration/openai-symphony-research.md) | OpenAI Symphony / Linear 状态机研究 | 看板控制面、事务状态机、claim/retry/reconciliation |
| [`harness/2026/openai_*.md`](harness/2026/), [`mas/2026/openai/*.md`](mas/2026/openai/) | OpenAI Codex / Harness 归档 | Codex harness、sandbox agents、WebSocket、agent eval |
| [`harness/2026/MANIFEST.md`](harness/2026/MANIFEST.md) | 2026 AI Harness 工程归档 | 90 个归档条目 |
| [`sdd/2026/README.md`](sdd/2026/README.md), [`sdd/2026/MANIFEST.md`](sdd/2026/MANIFEST.md) | 2026 Spec-Driven Development 归档 | 37 篇论文、12 篇实践博客、14 个工具/公司指南 |
| [`spdd/SUMMARY.md`](spdd/SUMMARY.md), [`spdd/spdd/01-spdd-main-article.md`](spdd/spdd/01-spdd-main-article.md), [`spdd/tools/10-spdd-ecosystem.md`](spdd/tools/10-spdd-ecosystem.md) | Structured Prompt-Driven Development 归档 | REASONS Canvas、三大技能、OpenSPDD、SDD 生态分层 |
| [`formal-verification/research.md`](formal-verification/research.md), [`formal-verification/pdfs/`](formal-verification/pdfs/) | 形式化验证与形式化规格研究 | TLA+、Alloy、Lean、Coq、Isabelle/HOL、seL4、CompCert、AI 辅助证明 |
| [`mas/2026/00-INDEX.md`](mas/2026/00-INDEX.md) | 多 Agent 工程与协议归档 | MAS、MCP、A2A、协议生态 |
| [`ai-market/research-report.md`](ai-market/research-report.md), [`ai-market/deep-research/2026_AI_Agent_Market_Analysis_Deep_Research.md`](ai-market/deep-research/2026_AI_Agent_Market_Analysis_Deep_Research.md) | 中文 AI Agent 研究综述与市场材料 | Agent Memory、Agent Evolution、Agent Teams、Harness 治理 |
| [`lessons-learned/hello-archive.md`](lessons-learned/hello-archive.md) | hello 来源归档说明 | 原始路径映射、选择范围、样式规范化 |

### 验收概况

| 指标 | 数值 | 说明 |
|------|------|------|
| 研究点覆盖率 | 74.3%（26/35） | 9 个研究点未被借鉴分析覆盖 |
| 准确率 | 92.5%（加权） | 6/8 抽查完全准确，1/8 部分不准确，0/8 事实错误 |
| 编号归因错误 | 4 处 | 已在本文件中全部修正 |
| 综合评级 | **B+（84.9%）** | 加分：分析深度和可操作性；扣分：覆盖率和统计严谨性 |

---

## 2026-04-28 增补：hello AI Markdown 全量复盘

### 语料分组

本次增补将 hello 项目中的 AI Markdown 归为八组，引用材料已按主题整理到本目录下的研究分类中，避免把 `tmp/` 下镜像仓库和 `reports/2026/` 主归档重复计数为独立结论：

| 分组 | 代表路径 | 核心主题 | 对 relay-teams 的价值 |
|------|----------|----------|-----------------------|
| 协作平台横评 | [`coding-agent-orchestration/coding-agent-collaboration-research.md`](coding-agent-orchestration/coding-agent-collaboration-research.md), [`coding-agent-orchestration/ai-agent-orchestration-platforms-research.md`](coding-agent-orchestration/ai-agent-orchestration-platforms-research.md) | Paperclip / Multica / Routa / SpectrAI 的生命周期、任务编排、记忆、质量治理、安全 | 给出现成产品形态和工程模式 |
| OpenAI Symphony | [`coding-agent-orchestration/openai-symphony-research.md`](coding-agent-orchestration/openai-symphony-research.md) | Linear 任务看板控制面、事务状态机、claim/retry/reconciliation、Codex 守护进程 | 校准任务看板从展示层升级为调度输入和状态权威 |
| Harness 工程 | [`harness/2026/`](harness/2026/) | Harness/compute 分离、Agent Behavioral Contracts、Runtime Guardrails、Context Engineering | 校准 TaskExecutionService 拆分后的下一层控制面 |
| SDD | [`sdd/2026/`](sdd/2026/) | Spec Kit、契约式开发、规格质量门、长任务 faithfulness loss、实践博客 | 校准 TaskSpec、VerificationPlan、Gater 的闭环强度 |
| SPDD | [`spdd/`](spdd/) | REASONS Canvas、prompt 一等交付工件、prompt/code 双向同步、OpenSPDD | 校准 TaskSpec 从执行附件升级为版本化 Prompt Artifact 的路线 |
| 形式化验证 | [`formal-verification/`](formal-verification/) | TLA+、Alloy、Lean、Coq、Isabelle/HOL、证明助手、工业验证案例 | 校准高严格度任务的机器可检查验证和状态机不变式路线 |
| MAS / 协议 | [`mas/2026/`](mas/2026/) | Supervisor、Adaptive Network、Swarming、MCP+A2A+ACP | 校准 relay-teams 的 MCP/ACP/A2A 边界 |
| 企业 Agent 平台 | [`google-cloud-next/google/`](google-cloud-next/google/), [`google-cloud-next/google-cloud-next/`](google-cloud-next/google-cloud-next/) | Agentic Enterprise、平台化 Agent、上下文和基础设施 | 校准企业部署和多 Provider 管理方向 |
| 中文综述与市场材料 | [`ai-market/research-report.md`](ai-market/research-report.md), [`ai-market/deep-research/`](ai-market/deep-research/), [`presentations/ai-*`](presentations/) | Agent Memory、Agent Evolution、Agent Teams、AI Agent 市场、企业落地 | 补强 FE-1、AO-2、FE-3、SG-1 等原借鉴点的证据 |
| 治理与 AI-native 组织 | [`hello/The_Playbook_For_Building_An_AI_Native_Company.md`](ai-native-company/The_Playbook_For_Building_An_AI_Native_Company.md) | 闭环组织、可查询工件、跨 Provider 治理包、证据纪律 | 校准产品层治理和组织级知识工程 |

### OpenAI Symphony 补充洞察

OpenAI Symphony 的亮点不是“支持 Linear 集成”，而是把任务看板提升为 Agent 工作系统的控制面：

1. **看板是单事实来源**：工单状态不只是 UI 标签，而是调度输入。活跃状态代表可处理任务，终态代表停止、清理和释放资源。
2. **双层状态机**：外层使用 Linear 的 Todo/In Progress/Done 等业务状态；内层使用编排器私有状态管理 Unclaimed、Claimed、Running、RetryQueued、Released 和每次 Run Attempt 的成功、失败、超时、取消。
3. **事件驱动调度**：Poll Tick、Worker Outcome、Retry Timer、Reconciliation Refresh、Stall Timeout 都会触发明确状态转换，避免 Agent 正常退出就被误判为任务完成。
4. **读写分离**：编排器读取看板并持有调度权威；Agent 通过受控工具写回评论、PR、CI 和状态，避免把外部系统 token 暴露给执行沙箱。
5. **产品形态变化**：用户不再“管理 Agent 会话”，而是在熟悉的任务看板里移动任务、审查证据和合并结果；Agent 变成总是在线的后台执行者。

### SPDD 补充洞察

SPDD 与通用 SDD 的区别在于：它不是只把“规格文档”放在代码生成之前，而是把 structured prompt 本身作为可维护资产，并把业务意图、领域抽象、实现计划和治理规则拆成可审查结构。

1. **Prompt 是一等交付工件**：REASONS Canvas 将 Requirements、Entities、Approach、Structure、Operations、Norms、Safeguards 固化为七维合约。对 relay-teams 来说，这意味着 `TaskSpec` 不应只是 `TaskEnvelope` 上的一段执行上下文，而应有独立 artifact id、版本、来源、作者、审查状态和后续同步记录。
2. **先对齐再生成**：SPDD 的 Alignment 要求先锁定业务价值、非目标、领域语言、DoD、边界和依赖。relay-teams 当前 Designer/Crafter/Gater 可以承载这些阶段，但还没有把“对齐通过”作为状态门或持久工件。
3. **抽象优先**：Abstraction First 强调先建领域实体、接口责任、组件边界和任务粒度，再让 Agent 写代码。relay-teams 的 `TaskSpec` 已有 requirements/constraints/acceptance，但缺少 entities/approach/structure/operations/norms/safeguards 这类更细的设计槽位。
4. **prompt/code 双向同步**：SPDD 的关键不是一次生成，而是在代码变化、测试反馈、人工 review 后同步更新 structured prompt。relay-teams 目前有 prompt history 和 run events，但没有对照代码 diff 反向更新规格资产的 `/spdd-sync` 类流程。
5. **适用场景分层**：SPDD 适合规模化标准交付、高合规硬约束、团队协作审计和跨切面一致性工作；对紧急 hotfix、探索 spike、一次性脚本和纯审美工作不应强制套用。这可以转化为 relay-teams 的 `TaskSpecStrictness` 和 orchestration preset 选择逻辑。

### 形式化验证补充洞察

形式化验证材料的直接启发是：当任务对象本身是状态机、协议、调度器、权限边界、预算策略或安全关键算法时，普通测试和人工审查只能覆盖样例路径，不能证明关键不变式长期成立。relay-teams 不需要把所有任务都形式化，但需要一条高严格度任务可选择的机器可检查验证通道。

1. **先轻量建模再证明**：TLA+ 和 Alloy 可以用于任务看板状态机、claim/retry/reconciliation、权限策略、预算阈值、hook 触发条件等有限状态或关系模型。它们更适合作为架构设计前的快速反例发现工具，而不是等实现完成后补证明。
2. **证明助手承载高价值规约**：Lean/Coq/Isabelle 适合算法正确性、协议安全性质、规格精化和数学约束较强的模块。对 relay-teams 来说，它们应作为 `strictness=high` 的可选验证后端，而不是普通任务的默认要求。
3. **证据必须机器可读**：形式化验证的价值不在“文档说验证过”，而在 model checker/proof assistant 的可重跑输出、证明文件、版本、命令、输入规格和失败反例。Evidence Bundle 应能区分 unit test、integration test、model checking、machine-checked proof 等证据类型。
4. **规格错误仍是主要风险**：seL4、CompCert 等案例提醒：形式化证明只能保证实现满足形式化规格，不能保证规格等同真实需求。因此 Designer/Gater 仍要审查需求与规约之间的语义映射。
5. **AI 降低但不消除成本**：LLM/AlphaProof/Lean 生态表明 AI 可以辅助生成证明和 tactics，但高风险任务仍需要把 proof artifact、review 记录和运行命令纳入可追溯工件，而不是把 LLM 的自然语言解释当作证明。

### 当前实现快照

与原始版本相比，relay-teams 在 2026-04-28 已有若干能力不应再被描述为完全缺失：

| 能力 | 当前实现 | 仍缺的部分 |
|------|----------|------------|
| Task Spec | `TaskEnvelope.spec: TaskSpec | None`，包含 requirements/constraints/acceptance/evidence/strictness；`TaskPromptHarness.task_contract_prompt()` 会注入 `## Task Spec` | Designer 产物还不是一等持久规格工件，也没有跨阶段强制追踪规格来源 |
| Structured Prompt Artifact | 有 `TaskSpec`、prompt history、任务事件和验证计划，可表达部分执行合约 | 没有 REASONS Canvas 式七维结构、prompt artifact 版本、审查状态、双向同步、prompt/code diff 关联 |
| 验证 | `VerificationPlan` 已支持 checklist、required_files、command_checks、acceptance_criteria、evidence_expectations；`VerificationReport` 已结构化落库到验证事件 | 规格语义合规仍主要靠字符串引用和命令结果，缺少 LLM/规则混合的语义判定和 Evidence Bundle |
| 形式化验证通道 | `VerificationPlan.command_checks` 理论上可以调用任意 CLI，已有 strictness 和验证报告可承接部分结果 | 缺少一等形式化规格/证明工件、TLA+/Alloy/Lean 等后端 profile、机器可读反例、proof artifact 版本和 Gater 对形式化证据类型的识别 |
| 生命周期 | `TaskLifecyclePolicy` 已支持 timeout、heartbeat、on_timeout；`TaskExecutionService` 会记录 heartbeat、超时 handoff、TIMEOUT/STOPPED 状态 | 缺少 DB-backed wake queue/coalescing、任务依赖自动唤醒、孤儿 delegated task 的通用清理策略 |
| 指令加载 | `PromptInstructionResolver` 支持 AGENTS.md/CLAUDE.md/GEMINI.md、全局 AGENTS.md、配置化远程/本地指令，并触发 `InstructionsLoaded` hook | 缺少自动生成跨 Provider 治理包并注入外部 Coding Agent 原生目录的能力 |
| 事件/流 | `EventLog`、`RunEventHub`、`RunInjectionManager` 已支持持久事件、SSE 订阅、运行中注入队列 | 事件还未表达 blocker/dependency、wake reason、claim/checkout lease 等编排语义 |
| Monitor 事件驱动 | `MonitorService` + `MonitorSourceKind`（BACKGROUND_TASK / GITHUB 两种事件来源）+ `MonitorActionType`（WAKE_INSTANCE / WAKE_COORDINATOR / START_FOLLOWUP_RUN / EMIT_NOTIFICATION 四种动作）+ `MonitorRule`（event_names / text_patterns_any / attribute_equals / attribute_in / cooldown / max_triggers / auto_stop / case_sensitive 完整匹配规则）+ `MonitorSubscriptionRecord`（active/stopped 生命周期管理 + trigger_count + last_triggered_at）+ `MonitorEventEnvelope`（source_kind + event_name + body_text + attributes + dedupe_key 完整事件信封） | 缺少 Agent/Role 去重的持久唤醒队列、基于 blocker/dependency 的自动唤醒 |
| 工作区 | `WorkspaceService.fork_workspace()` 已基于 Git worktree 创建隔离工作区 | 还未做到每个 delegated task 自动独占工作区，也缺少任务级 merge/review gate |
| 任务看板/状态机 | `TaskStatus` 已有 created/assigned/running/stopped/completed/failed/timeout；任务仓库和控制面会写入任务事件、心跳、超时和运行状态 | 任务状态还不是可配置业务看板状态；缺少 active/terminal 状态映射、claimed/retry queued/run attempt、reconciliation refresh、外部看板 adapter 和“看板状态驱动调度”的统一语义 |
| 成本 | `TokenUsageRepository` 按 session/run/instance/role/model_profile 记录 token 和 cache usage | 缺少费用换算、预算策略、阈值预警、硬停、按任务价值的模型路由 |
| 外部 Agent | `external_agents/` 已实现 ACP over stdio/HTTP/custom transport + 外部 A2A 客户端（Google A2A 协议）；角色可绑定 `bound_agent_id`；`native_config.py` 为外部 Agent 生成 provider-native 配置 | 内部 skill bridge（将 relay-teams 自有技能映射到 Agent 原生 skill 索引）仍待实现 |

### 补充借鉴点 (OP)

| 编号 | 名称 | 优先级 | 来源 | 当前状态 | 实施建议 |
|------|------|--------|------|----------|----------|
| **OP-1** | DB-backed Wake Queue 与唤醒合并 | 高 | Paperclip Heartbeat、Multica WebSocket Wakeup | Monitor 事件驱动基础设施已部分覆盖：`MonitorActionType.WAKE_INSTANCE` / `WAKE_COORDINATOR` 支持运行实例/协调器唤醒，`START_FOLLOWUP_RUN` 支持后续运行触发。但缺少按 Agent/Role 去重的持久唤醒队列 | 增加 `agent_wakeups` 表，字段包含 wake_reason、target_role/instance、run_id、task_id、coalesce_key；任务完成、审批通过、用户追加输入、依赖解除时写入；Worker 只消费合并后的最新 wake |
| **OP-2** | 原子 Claim/Checkout 与 blocker 自动推进 | 高 | Paperclip issue checkout、Multica ClaimTask、Routa lane gate | `TaskOrchestrationService` 有 role assignment lock 和 busy check，但没有任务级 lease、claim token、blocker/dependency 字段 | 在 `TaskEnvelope` 或 TaskRecord 增加 dependencies/blockers/lease_owner/lease_expires_at；dispatch 前必须原子 claim，冲突返回明确错误；所有 blocker 完成后自动发布 wake |
| **OP-11** | Task Board as State Machine | 高 | OpenAI Symphony、Linear 控制面 | 有内部 `TaskStatus` 和事件流，但任务看板仍偏展示/查询；状态更新没有外部 tracker 映射，也没有 claimed/retry queued/reconciliation 的调度状态机 | 增加 `TaskBoardAdapter` 和 `TaskBoardStateMap`，支持内部看板、GitHub Issue、Linear 等来源；配置 active/terminal/paused/review 状态；Coordinator 轮询或订阅看板事件并进行 claim、dispatch、retry、stall timeout、terminal cleanup；Agent 只通过受控工具写回评论、PR、CI、evidence |
| **OP-3** | 递增式 Task Artifact 与 Evidence Bundle | 高 | Routa Kanban Card、Evidence Bundle、Review Guard | 已有 `TaskSpec`、`VerificationPlan`、`TaskHandoff`、`VerificationReport`；2026-05-04 确认 artifact auto-append 已在 task_execution_service.py 的 SPEC/EXECUTION/VERIFICATION/DELIVERY 阶段全链路贯通 | 将每个任务维护为结构化 artifact：spec -> implementation evidence -> verification findings -> completion summary；Gater 读取 normalized evidence snapshot，而不是依赖上游自述 |
| **OP-12** | Structured Prompt Artifact 与 REASONS Canvas | 高 | SPDD、OpenSPDD、Thoughtworks SDD 生态 | 有 `TaskSpec` 和 prompt history，但没有 prompt 作为一等交付工件，也没有 REASONS 七维结构、版本、审查状态、prompt/code 双向同步 | 增加 `PromptArtifact`/`StructuredPromptSpec`，字段覆盖 requirements、entities、approach、structure、operations、norms、safeguards、source_task_id、version、review_state、sync_status；Designer 产出 artifact，Crafter/Gater 绑定 artifact id，代码 diff 或 review 反馈触发 prompt sync；按 `TaskSpecStrictness` 决定是否强制执行 |
| **OP-13** | Lightweight Formal Verification Lane | 中 | Formal verification research、TLA+、Alloy、Lean/Coq/Isabelle、seL4、CompCert | 有 `VerificationPlan`、command checks 和 strictness，但没有形式化规格/证明作为一等证据类型，也没有状态机不变式或 proof assistant 输出的标准化承接方式 | 扩展 `VerificationPlan` 或新增 `FormalVerificationPlan`，记录 spec_language、tool_profile、invariants/properties、proof_artifacts、counterexample_path、replay_command；高严格度任务可要求 TLA+/Alloy model check 或 Lean/Coq/Isabelle proof check 通过，Gater 读取机器可检查结果而不是自然语言声明 |
| **OP-4** | Provider-native runtime config 与 Skill Bridge | 中 | Multica `CLAUDE.md`/`AGENTS.md`/`GEMINI.md` 动态注入、Agent 原生 Skills | **部分已落地**：`external_agents/native_config.py` 实现了 `NativeConfigGenerator` 和 `resolve_native_config_filename()`，外部 Agent session 生成 provider-native 配置；`PromptInstructionResolver` 支持 AGENTS.md/CLAUDE.md/GEMINI.md 三类指令文件。内部 skill bridge（将 relay-teams 自有技能注册映射到 Agent 原生 skill 索引）仍待实现 | 为 external agent session 生成临时原生配置目录，按 provider 写入 AGENTS/CLAUDE/GEMINI 和对应 skill 索引，复用已有 `PromptInstructionResolver` 与 `SkillRuntimeService` 的内容 |
| **OP-5** | 预算硬停与 Token 经济学 | 高 | Paperclip 七维成本追踪/预算硬停、YC token-maxing、Routa 强弱模型分工 | 已记录 token usage，但没有 cost ledger、budget policy 或自动暂停 | 增加 `BudgetPolicy`：按 workspace/session/run/role/model_profile 聚合费用；阈值触发 warning、human gate、hard stop；Coordinator 分配任务时把“规划用强模型、执行用性价比模型”变成策略 |
| **OP-6** | Multi-Provider 互评与漂移检测 | 中 | SpectrAI 多 Provider 雷达图、Drift Detection | 有模型 fallback 和 capability metadata，但没有同题并行执行/评分 | 在高风险或高价值任务上支持 evaluator fan-out：多个 Provider/角色并行给出方案，按完整性、正确性、代码质量、规范遵循、范围漂移评分，汇总为 Gater 输入 |
| **OP-7** | Bounded Agent 与 Tool Diet 约束 | 中 | Atlan/Stripe one agent one bounded task、4-5 atomic tools | 已完成闭环（2026-05-04）：角色保存时 reject 超限角色、coordinator dispatch 时 warn/reject oversized roles、单元测试全覆盖 | 增加角色/临时角色 validation：超过建议工具数、objective 太宽、verification 为空时 warning 或拒绝；Coordinator 创建任务时自动建议拆分 |
| **OP-8** | 跨 Provider 治理包与 A2A 五元组 handoff | 中 | [`hello/The_Playbook_For_Building_An_AI_Native_Company.md`](ai-native-company/The_Playbook_For_Building_An_AI_Native_Company.md)、provider-native instruction conventions、Cat Cafe Governance Pack (v1.3.0) | relay-teams 能读取 AGENTS.md/CLAUDE.md/GEMINI.md，但没有统一生成和版本化治理包 | 将硬约束、协作标准、质量纪律、知识工程拆成版本化 governance pack；A2A/role handoff 统一 What/Why/Tradeoff/Open Questions/Next Action。参考模板见下方 Cat Cafe 治理包四层结构 |

#### OP-8 参考：Cat Cafe 治理包四层结构

Cat Cafe 项目已实现跨 Provider 自动治理包（Pack version 1.3.0），同一份治理规则通过 Provider 差异变量自动生成 codex / claude / gemini 三份原生配置。其四层治理结构可作为 relay-teams 治理包生成的参考模板：

| 治理层 | 核心内容 | 对 relay-teams 的映射 |
|--------|----------|----------------------|
| **Hard Constraints** | 端口隔离、Redis 分区、禁止自审（跨组 review）、身份不可冒充 | `RoleContract.invariants`（SG-2）+ `RuntimeGuardrailPolicy`（SG-1）|
| **Collaboration Standards** | A2A handoff 五元组（What / Why / Tradeoff / Open Questions / Next Action）、Vision Guardian 原则、review flow 四阶段（quality-gate → request-review → receive-review → merge-gate） | OP-8 的 A2A 五元组格式 + relay-teams 的 SP-1/FE-5 验证流程 |
| **Quality Discipline** | Bug 根因分析流程（reproduce → logs → call chain → confirm root cause → fix）、不确定时停止流程（stop → search → ask → confirm → then act）、Done needs evidence | EP-4 生命周期策略 + FE-5 Evidence Bundle + `VerificationPlan` |
| **Knowledge Engineering** | YAML frontmatter 标准、三层信息架构（CLAUDE.md ≤100行 → Skills → refs/）、Backlog 三层热度分层、Feature lifecycle 五阶段 | OP-4 Provider-native config + `TaskSpec` 结构化规格 + Memory Bank（FE-1）|

治理包生成模式：模板 + Provider 差异变量 → 多份 Provider 原生配置。relay-teams 可复用已有 `PromptInstructionResolver` 的 Provider 感知能力，将治理包作为可版本化 artifact 生成到外部 Agent 的工作目录。
| **OP-9** | Harness 控制面与 Sandbox 计算面分离 | 高 | OpenAI sandbox agents、Harness Engineering | 已完成闭环（2026-05-04）：TaskControlHarness 已 wire 到 task_execution_service.py，_control_harness() 工厂方法提供控制面 delegate 入口 | 明确定义 control plane 只负责 orchestration/tool routing/approval/tracing/recovery/run state；sandbox/worktree/remote workspace 只负责执行，工具调用跨边界必须携带策略决策和审计 ID |
| **OP-10** | Failure-mode driven MVH eval loop | 中 | SDD practitioner blogs、HumanLayer harness practices | 已实现 ← 已启动：`src/relay_teams/agents/evaluation/` 包含 FailureModeClassifier、RunSamplingService、DistributionAnalyzer、MVHRecommendationReport；五类失败模式（context rot/tool sprawl/spec drift/permission friction/verification miss）分类框架 + 抽样/分类/分布分析/投资优先级报告闭环 | 每个 release 周期抽样 50 个真实 run，分类 context rot、tool sprawl、spec drift、permission friction、verification miss；按失败分布决定下一轮 harness 投资 |

### 与原 25 点的关系

新增 OP 点并非替代原路线图，而是把原路线图中较抽象的方向压到更具体的产品机制：

| 原借鉴点 | 2026-04-28/29 增补影响 |
|----------|------------------------|
| AO-2 DAG 编排 | OP-11/OP-2/OP-3 提供 DAG 落地前需要的一等 board state、dependency、claim、artifact 基础 |
| RP-1 / FE-3 A2A | OP-8 定义内部 handoff 格式，OP-4 定义跨 Provider 原生配置桥；RP-1 内部 A2A 消息总线和 FE-3 外部 A2A 客户端均已完成闭环（2026-05-05 确认） |
| SP-1 / FE-5 规格与验证 | OP-12 将 spec/prompt 升级为 REASONS Canvas 式一等工件，OP-13 为高严格度任务补机器可检查验证通道，OP-3 把 spec、evidence、verification 串成持续增长的任务工件 |
| SG-1 / SG-2 护栏 | OP-9 明确控制面/计算面边界，避免把策略放进可被 Agent 修改的执行环境 |
| EP-1 Context Engineering | OP-4/OP-10 把上下文策略连接到 Provider 原生文件、skills 和失败模式采样 |
| FE-4 资源感知 | OP-1/OP-5/OP-11 把资源感知扩展到唤醒队列、预算硬停、模型路由和看板状态驱动的调度槽位 |

---

## 维度一：架构优化 (AO)

---

### AO-1：Harness 模式解构 TaskExecutionService

| 字段 | 内容 |
|------|------|
| **编号** | AO-1 |
| **名称** | Harness 模式解构 TaskExecutionService |
| **所属维度** | 架构优化 |
| **优先级** | **高** |

#### 当前状态

**已完成闭环（2026-04-29）**。`TaskExecutionService` 已按 Harness 模式拆分，主执行路径已从服务私有兼容方法迁移到 Harness 公共方法；`task_execution_service.py` 当前为 **1833 行**，其中仍包含 timeout/cancel/runtime 状态流转和旧测试兼容委托。原先混杂在单文件中的职责已迁移到 `src/relay_teams/agents/orchestration/harnesses/`，并补齐 AutoHarness 生成工具闭环：

| Harness | 落地模块 | 职责 |
|---------|----------|------|
| `TaskPromptHarness` | `prompt_harness.py` | runtime prompt section、用户 prompt、技能候选、共享状态快照、对话 prompt 持久化 |
| `TaskToolHarness` | `tool_harness.py` | 本地工具、技能工具、MCP 工具的 runtime snapshot 构建 |
| `TaskLlmHarness` | `llm_harness.py` | 单轮 LLM 调用、thinking 配置、todo 完成 guard 与重试 |
| `TaskPersistenceHarness` | `persistence_harness.py` | assistant error 持久化、任务完成 hook、角色记忆、runtime lane 提升 |
| `AutoHarness` 工具通道 | `tools/generated_tools/`、`tools/auto_harness_tools/` | 小型 JSON utility tool 合成、测试、审批启用、角色资产持久化、运行中工具目录刷新 |

闭环范围包括：

- 主执行路径直接调用 `TaskPromptHarness`、`TaskLlmHarness`、`TaskPersistenceHarness` 公共方法，兼容方法只服务旧调用面。
- `auto_harness_synthesize_tool` 与 `auto_harness_enable_tool` 已加入 `Crafter` 和 `MainAgent`，生成工具以 `generated_*` 命名并作为持久角色资产落盘。
- 启用生成工具必须走工具审批；启用后更新角色工具列表、注册到 `ToolRegistry`，并在同一 run 的当前角色或目标角色下一轮模型请求前刷新可用工具 schema。
- pending 资产不可被同名合成覆盖；持久化 manifest 必须保持 `generated_*` 命名空间，启动时遇到漂移资产只记录 warning 并跳过注册。
- 新增聚焦单元测试覆盖生成工具安全校验、启用/角色资产更新、动态工具注册、默认工具分组、角色默认工具和强制审批路径。

#### 对比价值

relay-teams 的 TaskExecutionService 是一个"上帝类"，所有核心执行逻辑汇聚于一处。借鉴 Harness Engineering 的分层理念，将其拆解为独立可组合的线束模块，可大幅降低单文件认知负荷、支持独立替换和升级各层，为后续所有架构演进（安全拦截层、上下文策略、A2A 通信等）扫清障碍。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `Agent_Harness_Engineering_Survey.md` | #1 | Harness Engineering 范式 — Agent = LLM + Harness |
| `harness/README.md` | #1 | Harness 核心论文（SemaClaw/NLAHs/AutoHarness） |
| `papers/analysis/a-survey-of-self-evolving-agents-....md` | #10 | Self-Evolving Agents 的 What/When/How/Where 框架 |
| `GoogleDeepMind_AutoHarness_2026.md` | #2 | AutoHarness 自动合成（"代码即策略"连续频谱模型） |

#### 实施建议

已完成全部闭环。`TaskExecutionService` 的主执行路径已切到 Harness 公共接口；AutoHarness 的"代码即策略"模型已作为 `TaskToolHarness` 的生成工具扩展落地，采用 pending → approved enable → role asset → runtime refresh 的闭环。剩余兼容委托只作为旧测试和内部调用缓冲，不再是主路径依赖。

**后续收敛方向**：`task_execution_service.py` 仍有 1833 行，其中 timeout / cancel / runtime 状态流转逻辑仍在主服务中而非独立 Harness 中。下一步应将 timeout/cancel/run state 逻辑提取到独立的 `TaskLifecycleHarness` 或 `TaskControlHarness`，使 `task_execution_service.py` 成为纯编排协调者。此收敛与 OP-9（Harness 控制面与 Sandbox 计算面分离）直接关联——控制面逻辑应从执行服务中进一步剥离。

---

### AO-2：Graph-based 编排替代线性 Pipeline

| 字段 | 内容 |
|------|------|
| **编号** | AO-2 |
| **名称** | Graph-based 编排替代线性 Pipeline |
| **所属维度** | 架构优化 |
| **优先级** | **高** |

#### 当前状态

**已完成闭环（2026-04-30）**。编排配置已从“角色列表 + prompt 约定”扩展为显式 DAG contract，`OrchestrationPreset.graph` 可声明 `nodes`、`edges`、`max_parallel_tasks` 和 `final_response_node_id`；`RunTopologySnapshot` 会携带 `orchestration_graph`，Coordinator 在选中 graph preset 时自动按依赖创建 delegated task、执行 fan-out、等待 join，并把上游结果注入下游 node objective。普通 preset 仍保留现有 Coordinator 动态委派循环，同时 `orch_create_tasks` 已支持由 Coordinator 在运行中创建自定义 DAG，避免只能依赖预设模板。

本阶段落地范围：

- 新增 `OrchestrationGraph` / `OrchestrationGraphNode` / `OrchestrationGraphEdge`，校验 node id 唯一、edge 引用合法、无自环、无环、`final_response_node_id` 有效。
- `TaskEnvelope` 新增 `orchestration_node_id` 与 `depends_on_task_ids`，`CoordinatorGraph._run_pending_delegated_tasks()` 已依赖感知：依赖未完成不调度，依赖缺失或失败会将下游任务标记失败并记录事件。
- `CoordinatorGraph._run_graph_mode()` 支持模板 DAG：entry nodes 可并行执行，join node 在所有上游完成后创建，并读取上游 result 作为上下文；所有 graph nodes 完成后再由 Coordinator 汇总最终响应。
- `TaskDraft` 新增 `role_id`、`orchestration_node_id`、`depends_on_task_ids` 和 `depends_on_node_ids`：Coordinator 可在一次 `orch_create_tasks` 调用中声明 Node+Edge 图；`TaskOrchestrationService.create_tasks()` 会解析 node dependency 为实际 task dependency，拒绝未知依赖、重复 node id、root 依赖和环。
- 带 `role_id` 的动态 graph node 会在创建时完成角色校验和 instance 绑定，并以 `ASSIGNED` 状态进入现有 pending runner；ready nodes 会在 Coordinator 当前轮结束后按依赖自动并行执行，未满足依赖的下游节点保持等待。
- 内置配置新增 `fast_graph`（Crafter→Gater）和 `standard_graph`（Designer→Crafter→Gater）两个显式 DAG preset；默认 `default` preset 继续走动态委派，作为兼容路径。
- 设置页新增可选 Graph JSON 编辑区，并在保存 orchestration config 时保留 graph 字段，避免图 preset 被前端序列化丢失。
- 聚焦测试已覆盖 graph contract 校验、run topology 持久化、Coordinator fan-out+join 调度、动态 DAG task 创建与依赖校验、系统配置 API 和设置页 graph 保真。

#### 对比价值

当前线性 Pipeline 限制了系统处理复杂任务拓扑的能力。引入 DAG 编排后，可支持条件分支、并行汇聚、Fan-Out+Join 模式（特别适用于多文件并行修改场景），真正实现动态编排。多文件场景下并行效率将显著提升（当前受限于固定 4-lane 信号量下的线性分发）。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `papers_metadata.md` | #11 | Graph-of-Agents（图基多 Agent 协作） |
| `mas/00-INDEX.md` | #7 | 编排模式 — Adaptive Network / Fan-Out+Join |
| `papers_metadata.md` | #11 | SYMPHONY 异构模型协同规划 |

> **⚠ 验收修正**: 原报告将 SYMPHONY 归因到 #31（错误）。SYMPHONY 正确归属为研究点 #11（Graph-based Agent Teams）。

#### 实施建议

已完成。DAG 编排已覆盖预设模板和运行中动态构图两条路径：任务分解产出 Node+Edge 的图结构而非线性队列，每个 Node 可绑定 `role_id + objective`，Edge 通过 `depends_on_node_ids` / `depends_on_task_ids` 定义数据流和依赖关系。现有三通道保留为兼容 preset，`fast_graph`、`standard_graph` 和动态 `orch_create_tasks` 共同承接 Fan-Out+Join、多文件并行修改和审查汇聚场景。

---

### AO-3：编排参数可配置化

| 字段 | 内容 |
|------|------|
| **编号** | AO-3 |
| **名称** | 编排参数可配置化 |
| **所属维度** | 架构优化 |
| **优先级** | **中** |

#### 当前状态

此前 `MAX_ORCHESTRATION_CYCLES = 8` 和 `MAX_PARALLEL_DELEGATED_TASKS = 4` 为源码硬编码常量（经验证精确匹配 `coordinator.py` 第 58-59 行），无法按任务类型或工作空间动态调整。

**已完成闭环（2026-04-30）**。编排约束已从 `CoordinatorGraph` 的源码常量迁移为显式 `OrchestrationPolicy`：每个 `OrchestrationPreset` 可在 `orchestration.json` 中配置 `policy.max_orchestration_cycles` 与 `policy.max_parallel_delegated_tasks`，`RunTopologySnapshot` 会保存本次 run 的有效策略，`POST /runs` 还支持一次性 `orchestration_policy` 覆盖。Coordinator 动态循环改为读取 run topology 中的策略；`max_orchestration_cycles=0` 可用于简单直答 preset，`max_parallel_delegated_tasks=0` 会跳过自动 delegated task 执行。设置页已增加对应数字字段，保存时会保留 policy、graph 与角色配置。

#### 对比价值

"一个常量适用所有场景"的僵化设计导致：简单咨询任务浪费资源（无需 8 轮循环），复杂重构任务能力不足（可能需要 16 轮/8 并行）。引入配置层后，可按任务复杂度弹性调配资源，无需改代码即可调优。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `PwC_Agentic_SDLC_2026.md` | #5 | Agentic SDLC 的全流程参数化思路 |
| `google/README.md` | #25 | Google ADK 的配置驱动架构 |

#### 实施建议

已完成。编排约束已从 `CoordinatorGraph` 的硬编码常量迁移为 `OrchestrationPolicy`：`OrchestrationPreset` 通过 `orchestration.json` 配置 `policy.max_orchestration_cycles` 与 `policy.max_parallel_delegated_tasks`，`POST /runs` 支持一次性 `orchestration_policy` 覆盖，`RunTopologySnapshot` 持久化每次 run 的有效策略供 Coordinator 运行时读取。简单咨询任务可设为 1 轮/0 并行，大规模重构可设为 16 轮/8 并行，无需改代码即可调优。

---

### AO-4：同步/异步路径统一

| 字段 | 内容 |
|------|------|
| **编号** | AO-4 |
| **名称** | 同步/异步路径统一 |
| **所属维度** | 架构优化 |
| **优先级** | **中** |

#### 当前状态

**已完全闭环（2026-05-04）**。经过五阶段渐进迁移，async 路径已全面统一。

**Phase 1-4（2026-04-27 ~ 2026-05-01）**：仓储层 async 原生化。`_call_sync_async` 同步桥已从所有业务模块消除，仅保留在 `SharedSqliteRepository` 兼容实现中。

**Phase 5（2026-05-04，PR #675）**：Router 层 `call_maybe_async` 消除与服务层 async 方法补全。至此 router 层零 `call_maybe_async`、零 `run_in_threadpool` 残留，service 层全面提供 `*_async` 方法，router 全部使用 `await service.*_async(...)` 原生调用。具体变更：

- **32 文件变更**，分三类：
  - **Service 层（11 文件）**：为 10 个 Service 类补全 `*_async` 方法（使用 `asyncio.to_thread` 包装同步方法），包括 SessionService（27）、TriggerService（21）、RoleSettingsService（7）、WorkspaceService（14+）、FeishuGatewayService（7）、FeishuSubscriptionService（4）、WeChatGatewayService（7）、XiaolubanGatewayService（7）、SpeechConfigService（2）、AssetService（5）。
  - **Router 层（10 文件）**：sessions、session_media、roles、workspaces、observability、speech、feishu_gateway、gateway、triggers、system — 全部改为 `await service.*_async(...)`，消除了 `call_maybe_async` 和 `run_in_threadpool`。
  - **测试层（11 文件）**：更新 fake/mock 服务类的 `*_async` 方法，简化线程池验证测试。
- **`call_maybe_async` 消除数量**：router 层全部清除（10 个文件约 60+ 调用点）。
- **Service async 方法补全数量**：10 个 Service 类共约 94 个 `*_async` 方法。
- **CI 全绿**：ruff check + format 通过，basedpyright 0 errors，5410/5411 单元测试通过（1 个已知 flaky），50/50 非浏览器集成测试通过，qodana 通过。

**剩余收敛方向**：(a) CLI 入口仅保留 `asyncio.run()` 顶层边界，内部全部走 async 路径；(b) SDK 同步 API 可按需提供 `AsyncSDK` / `SyncSDK` 双入口；(c) `call_maybe_async` 工具函数作为历史遗留可标记为 deprecated，但不影响 runtime 行为。

#### 对比价值

约 30-40% 的方法是冗余的 sync/async 双路径。统一为异步优先后，可消除一致性 bug 风险，简化新功能开发的心智负担，并为 Agentic SDLC 中"AI Agent 在最少人工干预下完成全流程"的设计哲学提供基础设施支撑。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `PwC_Agentic_SDLC_2026.md` | #5 | Agentic SDLC — 异步优先原则 |
| `google/README.md`, `google-cloud-next/index.md` | #24 | Google 全栈异步基础设施 |

#### 实施建议

已确立"异步优先"架构原则，并完成全部五个阶段迁移：(1) 编排核心路径；(2) automation 管理面；(3) automation delivery/queue；(4) 剩余业务/运行时仓储收尾；(5) router 层 `call_maybe_async` 消除与服务层 async 方法补全（PR #675）。后续应保持 `tests/unit_tests/test_async_wrapper_coverage.py` 与 `tests/unit_tests/automation/test_automation_repository.py` 的仓库级断言，禁止 `src/relay_teams` 业务模块重新调用 `_call_sync_async`；同步入口仅应保留在 CLI 等必要边界，通过 `asyncio.run()` 调用 async API。

---

## 维度二：角色与编排 (RP)

---

### RP-1：A2A 协议实现 Agent 间直接通信

| 字段 | 内容 |
|------|------|
| **编号** | RP-1 |
| **名称** | A2A 协议实现 Agent 间直接通信 |
| **所属维度** | 角色与编排 |
| **优先级** | **高** |

#### 当前状态

**已完成闭环（2026-05-05 确认）**。内部 A2A 消息总线和外部 A2A 客户端均已实现，超出原始"Agent 间直接通信"的预期范围：

| 能力 | 落地实现 | 关键代码位置 |
|------|----------|-------------|
| **内部 A2A 消息总线** | `A2ABus` 类支持 `publish()`、`subscribe()`、`receive()`、`snapshot()`；`A2aTopic` 枚举定义主题；`A2aBusMessage`/`A2aSubscription`/`A2aBusState` 结构化消息模型 | `agents/orchestration/a2a_bus.py` L27，`agents/orchestration/a2a_bus_models.py` L10-48 |
| **Agent 通信工具** | `send_a2a_message()` 和 `subscribe_a2a_topic()` 工具函数，Agent 可直接在工具调用层面发送和订阅 A2A 消息 | `agents/orchestration/a2a_tool.py` |
| **角色通信桥** | `RoleCommunicationExchange` + `validate_role_communication()` 桥接 A2A 消息到角色间协作 | `agents/orchestration/role_communication.py` |
| **内部 API 端点** | `/api/runs/{run_id}/a2a/*` 端点暴露 A2A 操作 | `interfaces/server/routers/a2a_internal.py` |
| **外部 A2A 客户端** | 支持 Google A2A 协议的 `A2aHttpClient`，含 `probe_a2a_agent()` 和 `send_a2a_prompt()` | `external_agents/a2a_client.py` |
| **事件集成** | `A2A_MESSAGE_PUBLISHED` / `A2A_MESSAGE_DELIVERED` 事件类型已接入事件流 | `agents/tasks/events.py` |

测试覆盖：`test_a2a_bus.py`、`test_a2a_bus_models.py`、`test_a2a_internal.py`（集成）、`test_a2a_client.py`（外部客户端）。

#### 对比价值

当前架构中 Coordinator 是信息瓶颈——所有子 Agent 输出需经 Coordinator 汇总再转达。引入 A2A 协议层后，同级 Agent 可传递局部信息（如 Explorer 向 Designer 发送"文件结构发现"补充），降低 Coordinator 的上下文压力，提升局部协作效率。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `mas/00-INDEX.md` | #8 | Agent 协议栈 — MCP（Agent→工具）+ A2A（Agent→Agent）互补标准 |
| `mas/00-INDEX.md` | #8 | MCP SDK 月下载 97M+，150+ A2A 组织 |

#### 实施建议

已完成。内部 A2A 消息总线已实现为 Run 级别的事件总线，支持基于主题的发布/订阅；外部 A2A 客户端实现了 Google A2A 协议。后续可增强的方向：(a) 跨 Run 的 A2A 消息持久化（当前仅限单 Run 生命周期）；(b) 更丰富的 A2A 服务发现和质量协商；(c) 与 FE-1 Memory Bank 集成，允许跨 Run 知识传递通过 A2A 通道。

---

### RP-2：Self-Evolving Agent 角色优化

| 字段 | 内容 |
|------|------|
| **编号** | RP-2 |
| **名称** | Self-Evolving Agent 角色优化 |
| **所属维度** | 角色与编排 |
| **优先级** | **中** |

#### 当前状态

角色定义为静态 YAML+Markdown 文件，支持内置/自定义/临时角色三类。角色能力在创建后固定不变，没有基于任务执行反馈自动优化的机制。临时角色生命周期与 Run 绑定，Run 结束即消亡，学习成果不沉淀。

#### 对比价值

当前"静态配置"角色定义无法随使用积累经验。构建"角色演化闭环"后，角色定义将从"静态配置"进化为"动态资产"，长期运行中角色持续优化，为"角色市场"提供质量评估基础。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `papers/analysis/a-survey-of-self-evolving-agents-....md` | #10 | Self-Evolving Agents（Princeton/Tsinghua/CMU 联合研究） |
| `papers_metadata.md` | #10 → 论文 [8] | Autogenesis 自演化协议 |
| `ai-research-2025/research.md` | #33 | Agent L1-L5 分级（华为终端标准） |

#### 实施建议

已实现 ← 已启动：`src/relay_teams/roles/` 包含 RolePerformanceMetrics、RoleSelfAssessmentService、SystemPromptAdjustmentEngine、MaturityScoringEngine、TemporaryRoleKnowledgeCaptureService、RoleEvolutionHistoryService；生命周期已 wire 到 MemoryEventHandler（task 完成时记录验证结果）、RunHookPipeline（session 结束时捕获临时角色知识）、memory_injection（role evolution section 注入 prompt）。

构建"角色演化闭环"：(1) 每次任务完成后，验证结果（Gater 的验收报告）作为角色表现数据写入角色记忆；(2) 定期（如每 N 个 Run）触发角色自评估，基于历史表现调整 system_prompt 中的策略描述；(3) 参照 L1-L5 分级模型，为每个角色定义能力基线，输出可量化的"角色成熟度"。临时角色消亡前，将其有效的 prompt 调整沉淀回模板角色。

---

### RP-3：Swarming 模式探索

| 字段 | 内容 |
|------|------|
| **编号** | RP-3 |
| **名称** | Swarming 模式探索 |
| **所属维度** | 角色与编排 |
| **优先级** | **低** |

#### 当前状态

所有编排都走 Coordinator 集中式调度。即使简单任务（如两段代码并行修复）也需经 Coordinator 创建→分发→汇总的完整流程，存在不必要的编排开销。

#### 对比价值

为低复杂度任务提供去中心化变体，可直接降低编排延迟（省去 Coordinator 汇总轮次），提升系统吞吐量，作为对现有 Supervisor 模式的有效补充。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `mas/00-INDEX.md` | #7 | 五大编排模式 — Swarming（无中心协调器的去中心化协作） |
| `ai-research-2025/research.md` | #32 | MoE 架构成为主流（动态路由思想） |

> **⚠ 验收修正**: 原报告将 MoE 归因到 #31（错误）。MoE 架构是独立研究点 #32。

#### 实施建议

为低复杂度任务引入"Swarm 模式"：当 Coordinator 评估任务为低复杂度（仅涉及 2-3 个子任务、无复杂依赖）时，可将任务直接发布到"任务池"，已就绪的 Agent 从池中自行认领并执行。本质上是现有 `_run_pending_delegated_tasks()` 的去中心化变体——保持信号量控制，但取消 Coordinator 的逐轮汇总环节。建议先完成 AO-2 DAG 编排后再评估。

---

### RP-4：Agent 能力分级标注

| 字段 | 内容 |
|------|------|
| **编号** | RP-4 |
| **名称** | Agent 能力分级标注 |
| **所属维度** | 角色与编排 |
| **优先级** | **低** |

#### 当前状态

角色有 `mode`（primary/subagent）但无能力分级。所有 subagent 角色被等同对待，Coordinator 分发任务时不根据"这个角色擅长什么"进行差异化调度。

#### 对比价值

角色调度更精准；用户对系统能力的预期管理更明确；为后续角色自演化（RP-2）提供量化基线。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `ai-research-2025/research.md` | #33 | Agent L1-L5 分级（华为终端标准） |
| `sdd/README.md` | #3 | SDD 三级规格严格度 |

#### 实施建议

在 `RoleDefinition` 中增加 `capability_level` 字段（L1 反应式 → L5 自主战略），标注每个角色的自主决策深度。Coordinator 在分发任务时同时考虑 role_id 和 capability_level，避免将 L2 级角色分配到需要 L4 级自主决策的任务。同时将 capability_level 暴露到前端 UI，帮助用户理解各角色的实际能力边界。

---

## 维度三：Spec-Driven 流程 (SP)

---

### SP-1：形式化规格嵌入任务生命周期

| 字段 | 内容 |
|------|------|
| **编号** | SP-1 |
| **名称** | 形式化规格嵌入任务生命周期 |
| **所属维度** | Spec-Driven 流程 |
| **优先级** | **高** |

#### 当前状态

**已完成闭环（2026-05-02）**。形式化规格已从 prompt 附件升级为可追溯的任务工件，完整覆盖"规格→持久化→注入→执行→验证→证据包"全链路。六大原始缺口（spec 独立持久工件、spec lineage tracking、Gater 消费 normalized Evidence Bundle、REASONS Canvas 槽位、prompt/code 双向同步记录、形式化验证证据绑定）均已落地。

已落地的核心能力：

| 能力 | 落地实现 | 关键代码位置 |
|------|----------|-------------|
| **一等持久 spec artifact** | `TaskSpecArtifact` 模型携带 `artifact_id`、`task_id`、`session_id`、`trace_id`、`source_task_id`、`spec`、`version`、`created_at`、`updated_at`，存储在 `task_spec_artifacts` 表 | `agents/tasks/models.py` L379-390 |
| **spec lineage tracking** | `TaskEnvelope.spec_artifact_id` 与 `TaskEnvelope.spec_source_task_id` 记录规格来源；`TaskOrchestrationService._resolve_draft_spec_binding()` 自动沿依赖链继承上游规格，支持显式引用和跨任务复用 | `agents/tasks/models.py` L406-407，`agents/orchestration/task_orchestration_service.py` L718-791 |
| **normalized Evidence Bundle** | `VerificationEvidenceBundle` 携带 `spec_artifact_id`、`spec_source_task_id`、`items`、`acceptance_links`、`expectation_links`、`formal_verification_required`/`formal_verification_passed`；`VerificationReport` 携带 `evidence_bundle`，Gater 消费结构化证据包而非纯文本结果 | `agents/tasks/models.py` L327-337，L365-376 |
| **REASONS Canvas 槽位** | `TaskSpec` 包含 `entities`、`approach`、`structure`、`operations`、`norms`、`safeguards` 六个 SPDD 设计槽位（`tuple[str, ...]`），`TaskPromptHarness.task_contract_prompt()` 将全部六个槽位注入执行 prompt | `agents/tasks/models.py` L173-178，`agents/orchestration/harnesses/prompt_harness.py` L534-539 |
| **prompt/code 双向同步** | `TaskSpec.prompt_artifact_version: int` 与 `TaskSpec.prompt_code_sync_status: TaskSpecSyncStatus`（枚举值：UNKNOWN / IN_SYNC / SPEC_AHEAD / CODE_AHEAD / NEEDS_REVIEW），prompt 注入时同步展示 | `agents/tasks/models.py` L179-180，`agents/tasks/enums.py` L22-27 |
| **形式化验证证据绑定** | `FormalVerificationPlan` 支持 TLA+（TLC）、Alloy（Alloy Analyzer）、Lean、Coq、Isabelle、Custom 六种 spec language 及对应 tool profile；`TaskSpec.formal_verification: FormalVerificationPlan | None` 可绑定 proof_artifacts、counterexample_path、replay_command；`_run_strictness_checks()` 对 `strictness=HIGH` 强制要求 structured evidence | `agents/tasks/models.py` L115-124，L181；`agents/tasks/enums.py` L66-81；`agents/orchestration/verification.py` L888-911 |

闭环范围包括：

- `TaskRepository._prepare_envelope_for_storage()`（`task_repository.py` L782）在存储时自动为含 spec 的 envelope 创建或复用 `TaskSpecArtifact`，记录版本号；spec 内容不变时复用已有 artifact_id，spec 变更时创建新 artifact 并递增版本。
- `TaskDraft` 与 `TaskUpdate` 均支持 `spec`、`spec_artifact_id`、`spec_source_task_id` 三字段，`TaskOrchestrationService` 在创建和更新时解析 spec 绑定：支持显式引用已有 artifact、显式指定 source task、自动从依赖任务继承规格。
- `TaskPromptHarness.task_contract_prompt()` 注入完整的 `## Task Spec` section，包含 Spec Artifact ID、Spec Source Task ID、baseline 字段（summary/requirements/constraints/acceptance_criteria/out_of_scope/verification_commands/evidence_expectations）、REASONS Canvas 六槽位、strictness、Prompt Artifact Version、Prompt/Code Sync Status、Formal Verification 详情（spec_language / tool_profile / properties / proof_artifacts / counterexample_path / replay_command），以及 "Completion Evidence: cite each acceptance criterion and evidence expectation in the final handoff" 的强制提示。
- `SpecCheckpointPolicy`（`models.py` L210）控制长周期任务的 spec 自动刷新（基于 tool_calls / messages / history_tokens 三阈值），`build_spec_checkpoint_decision()`（`spec_checkpoint.py` L42）在执行过程中按策略重新注入 spec checkpoint，防止上下文压缩丢失规格。
- 八层验证管线（Structure / Behavior / Evidence / Semantic / Spec / Contract / Security / Formal）全链路守护（详见 FE-5）：`_run_strictness_checks()` 对 HIGH strictness 任务强制要求 verification commands、required files、evidence expectations 或 formal verification evidence；`_run_formal_checks()` 执行 proof artifact 存在性检查、replay command 执行、counterexample 不存在性检查。
- `verify_task()` 产出 `VerificationReport` 携带 `evidence_bundle`，并自动将 `spec_artifact_id` 和 `spec_source_task_id` 写回 evidence_bundle，最终持久化到 `TaskEnvelope.evidence_bundle`，形成"规格→执行→验证→证据包"的闭环。

后续可继续增强的方向：

- `render_spec_checkpoint()`（`spec_checkpoint.py` L126）目前不包含 REASONS Canvas 字段和 formal verification 详情，长周期任务的 spec refresh 仅刷新 baseline 字段——可扩展为覆盖完整结构化 spec。
- `TaskSpecArtifact.version` 字段已存在但尚未建立版本链和 diff 查询能力——可增加版本历史回溯和 spec diff API。
- 前端 UI 和设置页尚未暴露 spec 来源 lineage 可视化和验证证据结构化展示——已有后端数据支撑，前端展示为独立迭代项。

#### 缺口分析

截至 2026-05-02 源码验证，六大原始缺口状态如下：

1. **一等持久 spec artifact** — **已闭合**。`TaskSpecArtifact`（`agents/tasks/models.py` L379-390）作为独立持久模型存储于 `task_spec_artifacts` 表，携带完整元数据（artifact_id、task_id、session_id、trace_id、source_task_id、spec、version、created_at、updated_at）。`TaskRepository._prepare_envelope_for_storage()` 自动创建和复用 spec artifact。
2. **spec lineage tracking** — **已闭合**。`TaskEnvelope.spec_artifact_id` 与 `spec_source_task_id`（L406-407）记录规格来源。`TaskOrchestrationService._resolve_draft_spec_binding()`（L718-791）支持三种解析路径：显式 artifact 引用、显式 source task 引用、依赖链自动继承。`_SourceSpecBinding` 辅助类跨任务聚合 spec 绑定信息。
3. **Gater 消费 normalized Evidence Bundle** — **已闭合**。`VerificationEvidenceBundle`（L327-337）携带结构化的 evidence items、acceptance_links、expectation_links，以及 `formal_verification_required`/`formal_verification_passed` 标记。`VerificationReport`（L365-376）携带 `evidence_bundle`，`verify_task()` 在验证完成后将 evidence_bundle 持久化回 `TaskEnvelope`。
4. **REASONS Canvas 槽位** — **已闭合**。`TaskSpec` L173-178 包含 `entities`、`approach`、`structure`、`operations`、`norms`、`safeguards` 六个 REASONS Canvas 槽位。`TaskPromptHarness.task_contract_prompt()` L534-539 将完整 REASONS Canvas 注入执行 prompt。
5. **prompt/code 双向同步记录** — **已闭合**。`TaskSpec.prompt_artifact_version`（L179，`int >= 1`）与 `prompt_code_sync_status`（L180，`TaskSpecSyncStatus` 枚举：UNKNOWN / IN_SYNC / SPEC_AHEAD / CODE_AHEAD / NEEDS_REVIEW）记录同步状态。枚举定义于 `agents/tasks/enums.py` L22-27。
6. **形式化验证证据绑定** — **已闭合**。`FormalVerificationPlan`（L115-124）支持 TLA+（TLC）、Alloy（Alloy Analyzer）、Lean、Coq、Isabelle、Custom 六种 spec language。`TaskSpec.formal_verification`（L181）可绑定 proof_artifacts、counterexample_path、replay_command。验证引擎的 FORMAL 层（`verification.py` `_run_formal_checks()` / `_run_formal_plan_checks()` / `_run_formal_artifact_check()` / `_run_formal_replay_check()` / `_run_formal_counterexample_check()`）执行完整的形式化验证检查。

> **⚠ 验收修正（2026-05-02）**: 原报告（2026-04-28）标注 SP-1 为"部分已落地"并列出六项缺口。经源码验证，六项缺口在 2026-04-28 至 2026-05-02 期间已全部闭合。表结构和数据模型均与前向兼容。

#### 对比价值

形式化规格嵌入任务生命周期是解决"规格存在但证据链不够硬"的核心机制。relay-teams 已从"spec 只是 prompt 附件"演进为完整的 spec-driven 验证闭环：

- **规格持久化 + lineage**：`TaskSpecArtifact` 使规格从临时数据升级为可版本化、可追溯、可复用的一等工件，满足 Piskala Spec-Driven Development 框架中 spec-as-source 层的要求。
- **结构化证据包**：Gater 消费 `VerificationEvidenceBundle` 而非上游自然语言结果，显著降低审查的认知负荷和误判率。
- **REASONS Canvas**：规格不只是"需求列表"，而是包含领域抽象（entities）、实现路径（approach）、架构边界（structure）、操作语义（operations）、团队规范（norms）、安全护栏（safeguards）的结构化 prompt，与 SPDD 社区的 prompt-as-artifact 理念对齐。
- **形式化验证集成**：对状态机、协议和安全关键策略，`strictness=HIGH` 强制绑定机器可检查证据（model check / proof check），将验证从"LLM 自述完成"提升为可重跑的形式化证据。
- **三级严格度**：LOW（快速任务，仅非空响应检查）→ MEDIUM（默认，含 spec compliance 检查）→ HIGH（安全关键，含 formal verification + structured evidence 要求），与 Piskala 的三级规格严格度框架一致。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `sdd/README.md` | #3 | Spec-Driven Development（Piskala 三级规格严格度框架） |
| `sdd/README.md` | #17 | AI 编码 Agent 退化（SlopCodeBench）—— 长周期任务质量退化 |
| `sdd/README.md` | #18 | SWE-AGI 规格+代码双评估基准 |
| `spdd-report/SUMMARY.md`, `spdd/01-spdd-main-article.md` | 增补 | SPDD REASONS Canvas、prompt 一等交付工件、prompt/code 双向同步 |
| `spdd-report/tools/10-spdd-ecosystem.md` | 增补 | SDD 分层：spec-first / spec-anchored / spec-as-source 与 OpenSPDD 工具链 |
| `formal-verification-research/research.md` | 增补 | TLA+、Alloy、Lean、Coq、Isabelle/HOL 与工业级机器可检查验证 |

> **⚠ 验收修正**: 原报告将 SWE-AGI 归因到 #30（错误）。SWE-AGI 正确归属为研究点 #18（Benchmark 演进）。

#### 实施建议

已完成全部闭环。核心实施路径如下（供参考）：

1. `TaskSpecArtifact` + `TaskEnvelope.spec_artifact_id` / `spec_source_task_id` 建立规格→执行→验证→证据包的 artifact 闭环——`TaskRepository._prepare_envelope_for_storage()` 自动管理。
2. Crafter prompt 通过 `TaskPromptHarness.task_contract_prompt()` 注入完整规格（含 REASONS Canvas + Formal Verification + Completion Evidence 提示）。
3. Gater 通过 `VerificationReport.evidence_bundle` 消费结构化证据包，而非上游自然语言结果。
4. `strictness=HIGH` 通过 `_run_strictness_checks()` 强制要求 verification commands / required files / evidence expectations / formal verification evidence 中至少一项。
5. REASONS Canvas 六槽位已作为 `TaskSpec` 一等字段落地；`prompt_artifact_version` 与 `prompt_code_sync_status` 记录同步状态。
6. `FormalVerificationPlan` 支持六种形式化语言和完整 artifact / replay / counterexample 检查。

后续增强方向：扩展 `render_spec_checkpoint()` 覆盖 REASONS Canvas 字段和 formal verification 详情；建立 `TaskSpecArtifact` 版本历史链和 diff API；前端 UI 暴露 spec lineage 可视化。

#### 可行性确认

已完成。源码验证全部核心模型、字段、方法存在且通过测试覆盖：`TaskSpecArtifact`（`models.py` L379）、`TaskEnvelope.spec_artifact_id`（L406）、`VerificationEvidenceBundle`（L327）、REASONS Canvas 字段（L173-178）、`FormalVerificationPlan`（L115）、`TaskSpecSyncStatus` 枚举（`enums.py` L22）、`TaskPromptHarness.task_contract_prompt()`（`prompt_harness.py` L522）、`_run_strictness_checks()`（`verification.py` L888）。聚焦测试覆盖 spec artifact 创建/复用/回滚、spec binding 解析（显式引用/继承/拒绝无效引用）、strictness 检查、formal verification artifact/replay/counterexample 检查。

---

### SP-2：规格即合约（Code-as-Contract）

| 字段 | 内容 |
|------|------|
| **编号** | SP-2 |
| **名称** | 规格即合约（Code-as-Contract） |
| **所属维度** | Spec-Driven 流程 |
| **优先级** | **高** |

#### 当前状态

**已完成闭环（2026-05-01）**。角色协作规则已从 system prompt 软约束升级为 `RoleContract` 结构化行为合约：`RoleDefinition.contract` 可声明 `preconditions`、`postconditions` 和 `invariants`，角色 Markdown YAML、角色配置 API、设置页编辑器和运行时 prompt 均支持该合约。`TaskOrchestrationService.dispatch_task()` 会在执行前检查前置条件和角色能力不变量；Coordinator 的自动 DAG 调度对 ready delegated task 应用同一检查；`verify_task()` 会将后置保证写入 `VerificationLayer.CONTRACT` 检查结果。

当前内置 Explorer/Designer/Gater/Crafter 已带基础行为合约：Explorer/Designer/Gater 通过 `must_not_have_tools` 固化只读或不改生产文件边界；Crafter/Gater 通过 `result_mentions_acceptance_criteria` 与 `result_mentions_evidence_expectations` 将验收项和证据要求变成可验证输出义务。保存/校验角色配置时，未知 contract capability 引用和违反自身不变量的角色会被拒绝；读取既有配置仍保持容错，避免脏的持久化 capability 引用阻断启动或设置页加载。

#### 对比价值

将角色间协作的"软约束"变为"可验证的硬合约"，减少 Coordinator 需在分发描述中重复申明的约束量，为自动化编排（无需 LLM 决策的场景）提供规则引擎基础。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `sdd/README.md` | #3 | SDD 的"代码即合约"理念 |
| `Agent_Harness_Engineering_Survey.md`, `harness/README.md` | #1 | Harness Engineering 中的 Agent Behavioral Contracts |

#### 实施建议

引入 `RoleContract` 模型，作为 `RoleDefinition` 的补充：定义每个角色的前置条件（preconditions，如 Designer 必须收到 Explorer 的发现报告）、后置保证（postconditions，如 Crafter 必须运行自动化测试）、不变量约束（invariants，如 Gater 不修改任何文件）。RoleContract 以结构化 YAML 定义，在任务分发时由 `TaskOrchestrationService` 自动校验前置条件是否满足，在验证阶段自动校验后置保证是否达成。

---

### SP-3：Spec-Checkpoint 抗退化机制

| 字段 | 内容 |
|------|------|
| **编号** | SP-3 |
| **名称** | Spec-Checkpoint 抗退化机制 |
| **所属维度** | Spec-Driven 流程 |
| **优先级** | **低**（核心机制已在 SP-1 中落地，增强项 E1-E4 已于 2026-05-03 全部完成。当前为维护态） |

#### 当前状态

**已全部落地（2026-05-03）**。SP-3 提出的两条核心策略——"规格认知刷新"与"规格优先压缩"——均已有对应实现，且四项增强方向（E1-E4）已全部完成：

1. **规格认知刷新（已落地）**：`SpecCheckpointPolicy`（`agents/tasks/models.py` L210）通过 `tool_calls` / `messages` / `history_tokens` 三阈值控制长周期任务的 spec 自动刷新。`build_spec_checkpoint_decision()`（`agents/execution/spec_checkpoint.py` L42）在执行过程中按策略判断是否需要注入 spec checkpoint；`render_spec_checkpoint()`（`spec_checkpoint.py` L126）提取 baseline 字段（summary / requirements / constraints / acceptance_criteria / out_of_scope / verification_commands / evidence_expectations）渲染为带序列号标记的 checkpoint 内容，以 assistant 消息方式注入当前会话。`session_runtime.py` L727-940 在 LLM 循环的两个决策点（tool call 结束后、assistant 消息生成后）调用 `apply_spec_checkpoint_if_due()`，实现自动认知刷新。该能力已在 SP-1 中完整闭环。

2. **规格优先压缩（已落地）**：`conversation_compaction.py`（经验证达 1146 行）的摘要重写指令（L609-611）明确要求："If the transcript contains a Task Spec or Spec Checkpoint, preserve its requirements, constraints, acceptance criteria, out-of-scope items, verification commands, and evidence expectations in a dedicated spec section. Spec constraints are higher priority than ordinary conversation details and must not be weakened or generalized." 压缩不是"无感知规格"的，而是 spec-aware 的：spec checkpoint 内容被保留在独立 spec section 中，约束优先级高于普通对话。

已落地的核心能力：

| 能力 | 落地实现 | 关键代码位置 |
|------|----------|-------------|
| **Spec Checkpoint Policy** | `SpecCheckpointPolicy` 配置 `refresh_interval_tool_calls` / `refresh_interval_messages` / `refresh_interval_history_tokens` 三阈值，控制自动刷新触发条件 | `agents/tasks/models.py` L210 |
| **checkpoint 决策** | `build_spec_checkpoint_decision()` 检查阈值、去重（基于 sequence marker）、返回注入决策 | `agents/execution/spec_checkpoint.py` L42 |
| **checkpoint 渲染** | `render_spec_checkpoint()` 提取 spec baseline 字段，渲染为带 `<!-- relay-spec-checkpoint -->` 标记的结构化内容 | `agents/execution/spec_checkpoint.py` L126 |
| **运行时注入** | `session_runtime.py` 在两个 LLM 循环决策点调用 `apply_spec_checkpoint_if_due()`，自动注入 checkpoint 并记录 `SPEC_CHECKPOINT_APPLIED` 事件 | `agents/execution/session_runtime.py` L727-940 |
| **spec-aware 压缩** | compaction 摘要重写指令要求保留 spec checkpoint 内容，spec constraints 优先级高于普通对话 | `agents/execution/conversation_compaction.py` L609-611 |

#### 缺口分析

截至 2026-05-02 源码验证，SP-3 原始两项需求状态如下：

1. **执行过程中"刷新规格认知"** — **已闭合**。`SpecCheckpointPolicy` + `build_spec_checkpoint_decision()` + `render_spec_checkpoint()` + `session_runtime.py` 注入逻辑形成完整的 spec checkpoint 闭环。该缺口在 SP-1 闭环中一并解决。
2. **压缩时"规格优先保留"** — **已闭合**。`conversation_compaction.py` L609-611 的摘要重写指令明确要求 spec 内容保留在独立 section 中，spec constraints 优先级高于普通对话。

原始增强方向与实施状态：

- `render_spec_checkpoint()`（`spec_checkpoint.py` L126）已包含 REASONS Canvas 字段（entities / approach / structure / operations / norms / safeguards，L175-180）和 formal verification 详情（L189-206）。早期文档标注"不包含 REASONS Canvas 字段"系过时描述，已在源码验证中更正。

#### 对比价值

缓解长周期任务中 Agent 对初始规格的遗忘问题，提升复杂任务的首次完成率，为 SWE-bench 评估中的长尾失败案例提供改善路径。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `sdd/README.md` | #17 | AI 编码 Agent 长周期任务质量退化（SlopCodeBench 证据） |
| `Anthropic_Context_Engineering_Guide.md`, `harness/README.md` | #4 | Context Engineering — 上下文压缩与编辑策略 |

#### 实施建议

SP-3 的核心机制已在 SP-1 中落地。后续增强方向及实施状态（截至 2026-05-03）：

1. ~~扩展 `render_spec_checkpoint()` 覆盖 REASONS Canvas 字段和 `FormalVerificationPlan` 详情~~ — **已实现**。源码验证确认 `render_spec_checkpoint()` L175-180 已渲染全部六个 REASONS Canvas 字段，L189-206 已渲染 formal verification 详情。
2. ~~`SpecCheckpointPolicy` 增加基于 spec 变更号的增量刷新机制~~ — **已实现**。`SpecCheckpointPolicy` 新增 `refresh_on_version_change`、`auto_evaluate_drift`、`drift_score_threshold` 字段。当 `refresh_on_version_change=True` 且 `TaskSpecArtifact.version` 递增时，`build_spec_checkpoint_decision()` 触发立即刷新并附带版本变更 diff 摘要。
3. ~~Spec artifact version chain / diff API~~ — **已实现**。新增 `GET /tasks/{task_id}/spec-artifacts`（版本列表）和 `GET /tasks/{task_id}/spec-artifacts/{version}/diff`（字段级 diff），通过 `SpecArtifactDiffService` 计算两个 `TaskSpec` 版本间的逐字段变更。
4. ~~Frontend spec lineage 可视化~~ — **已实现**。新增 `specLineage.js` 组件（含 timeline.js、diffViewer.js、evaluationPanel.js 子模块），从 agent panel 的 "Spec Lineage" 操作入口访问版本时间线、字段 diff 和 drift 评估结果。
5. ~~LLM evaluator 集成 drift 检测~~ — **已实现**。`evaluate_checkpoint_drift_async()` 在 checkpoint 注入后异步调用 `LLMEvaluator.evaluate_spec_quality()`，持久化 `spec_checkpoint_evaluations` 记录，通过 `GET /tasks/{task_id}/spec-checkpoint-evaluations` API 暴露结果。评估失败时回退为规则引擎并标记 `fallback=True`。

#### 可行性确认

已完成。源码验证全部核心组件存在且通过测试覆盖：`SpecCheckpointPolicy`（`models.py` L210）、`build_spec_checkpoint_decision()`（`spec_checkpoint.py` L42）、`render_spec_checkpoint()`（`spec_checkpoint.py` L126）、`apply_spec_checkpoint_if_due()`（`session_runtime.py` L727）、compaction spec-aware 指令（`conversation_compaction.py` L609-611）。

增强组件（E1-E4）实施确认：
- E1: `build_spec_checkpoint_decision()` 新增 `current_artifact_version` 参数，`render_spec_checkpoint()` 新增 `version_change` 参数和版本变更 diff 渲染区块。
- E2: `SpecArtifactDiffService`（`spec_artifact_diff_service.py`）实现 `_diff_task_specs()` 和 `_build_diff_summary()`；三组 API endpoint 已注册到 task router。
- E3: `specLineage.js` 组件（含 timeline、diffViewer、evaluationPanel 子模块）已集成到 agent panel。
- E4: `evaluate_checkpoint_drift_async()` 实现异步 drift 评估，`spec_checkpoint_evaluations` 表和 `SPEC_CHECKPOINT_EVALUATED` 事件类型已落盘。

---

## 维度四：安全与治理 (SG)

---

### SG-1：运行时护栏（Runtime Guardrails）层

| 字段 | 内容 |
|------|------|
| **编号** | SG-1 |
| **名称** | 运行时护栏（Runtime Guardrails）层 |
| **所属维度** | 安全与治理 |
| **优先级** | **高** |

#### 当前状态

**基础框架已落地（2026-05-03）**。`RuntimeGuardrailPolicy`（`tools/runtime/guardrails.py`，873 行）已实现完整的三层运行时护栏架构，与原实施建议高度一致：

| 护栏层 | 枚举值 | 职责 | 已有默认规则 |
|--------|--------|------|-------------|
| **预执行层** | `RuntimeGuardrailLayer.PRE_EXECUTION` | 在 LLM 工具调用前确定性拦截 | `role_tool_allowlist`（角色工具白名单边界）、`runtime_denied_tools`（运行时拒绝工具）、`destructive_shell_pattern`（破坏性 shell 命令拦截） |
| **执行中监控层** | `RuntimeGuardrailLayer.IN_EXECUTION` | 对工具调用参数/输出实时校验 | `input_size` / `output_size` / `call_frequency` 限制 |
| **后验证层** | `RuntimeGuardrailLayer.POST_VALIDATION` | 任务完成后合规报告观察 | 观察记录机制，输出到 `runtime_guardrail_report` 共享状态 |

核心数据模型：

- **`RuntimeGuardrailRuleType` 枚举**（六种规则类型）：`TOOL_ALLOWLIST` / `TOOL_DENYLIST` / `INPUT_SIZE` / `OUTPUT_SIZE` / `CALL_FREQUENCY` / `SHELL_DESTRUCTIVE_PATTERN` — 覆盖工具边界、I/O 大小、调用频率和破坏性命令模式
- **`RuntimeGuardrailAction` 枚举**（三种动作类型）：`ALLOW` / `WARN` / `DENY` — 从放行到警告到硬拒绝的分级响应
- **`RuntimeGuardrailStatus` 枚举**（三种状态）：`PASSED` / `WARNING` / `BLOCKED` — 规则评估后的结果分类
- **`RuntimeGuardrailRule`** 模型：支持 `tool_names`、`role_ids`、`session_modes`、`run_kinds` 等作用域限定，以及 `max_bytes`、`max_calls_per_task`、`blocked_patterns` 等约束参数

规则通过 `default_runtime_guardrail_rules()` 预置默认集，运行时评估结果持久化到共享状态，观察记录上限 200 条。设计与实现规格见 [`docs/modules/security/runtime-guardrails-sg1.md`](../modules/security/runtime-guardrails-sg1.md)。

仍需后续增强的方向：

- (a) 热点规则自动生成——基于角色合约（`RoleContract`）自动派生护栏规则，而非手动编写
- (b) 护栏报告的安全审计集成——将护栏观察记录写入 `security_audit_events` 表，支持合规查询
- (c) 不同角色/任务类型的差异化规则集加载——根据 `TaskSpecStrictness` 和角色合约动态切换规则集

#### 对比价值

从"依赖 LLM 自律"升级为"确定性安全门 + LLM 自律"的双重防护，为企业用户提供可审计的安全日志，降低越权操作风险。这是企业级部署的前提条件。三层护栏模型直接对应 ILION（预执行确定性门）、AgentDoG（执行中诊断）和 Proof-of-Guardrail（后验证报告）三种研究范式。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `harness/README.md` | #15 | Runtime Guardrails — AgentDoG 诊断框架、ILION 确定性预执行安全门、Proof-of-Guardrail |
| `Bengio_International_AI_Safety_Report_2026.md` | #12 | 国际 AI 安全报告的多维度安全框架 |
| [`docs/modules/security/runtime-guardrails-sg1.md`](../modules/security/runtime-guardrails-sg1.md) | 内部规格 | SG-1 运行时护栏设计与实现规格 |

#### 实施建议

基础架构已落地，三层护栏模型、六种规则类型、三种动作和观察记录机制均已实现。后续应聚焦于三个方面：(a) 从 `RoleContract.invariants` 自动派生护栏规则（`must_not_have_tools` → `TOOL_DENYLIST`，`must_have_tools` → `TOOL_ALLOWLIST`）；(b) 将 `runtime_guardrail_report` 持久化到 `security_audit_events`，支持 `/api/audit` 查询；(c) 为 `strictness=HIGH` 任务自动增强规则集（降低 `call_frequency` 上限、收紧 `output_size` 限制）。

---

### SG-2：角色行为边界强制执行

| 字段 | 内容 |
|------|------|
| **编号** | SG-2 |
| **名称** | 角色行为边界强制执行 |
| **所属维度** | 安全与治理 |
| **优先级** | **高** |

#### 当前状态

角色的"禁区"约束存在于两层：(1) **工具注册层**（技术强制）——RoleDefinition.tools 字段控制可用工具集（如 Gater 没有 `edit`/`write` 工具）；(2) **system_prompt 层**（LLM 自律）——补充覆盖 shell/write_tmp 等通道。但 shell 和 write_tmp 构成**规避通道**——LLM 可通过 shell 执行任意命令或通过 write_tmp 写入临时文件绕过约束。

#### 对比价值

"角色坍塌"从偶发风险变为不可能事件；提升系统可信度；减少 Gater 审计中发现"设计阶段已违反约束"的回溯成本。改动量相对可控但安全收益极高，是 SG-1 中最易快速见效的部分。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `Agent_Harness_Engineering_Survey.md`, `harness/README.md` | #1 | Harness Engineering 中的 Agent Behavioral Contracts |
| `Dario_Amodei_Adolescence_of_Technology_2026.md` | #14 | AI 风险路径（Amodei 技术青春期） |

> **⚠ 验收修正**: 原报告将现状描述为"完全在 system_prompt 中"。实际验证发现约束在工具注册层 + system_prompt 双层实现，但 shell/write_tmp 存在规避通道。建议方向正确但描述需修正。

#### 实施建议

将角色的"禁区"约束从 prompt 层提升到工具注册层——为每个角色定义"工具调用的运行时权限策略"（existing `runtime/policy` 模块的增强版）。例如，Gater 角色的策略在运行时拦截所有 write 类工具调用，Designer 角色拦截所有 `shell` 工具调用。这实际上是现有 `tools/runtime/` 中策略机制的深化——从审批模式扩展到强制拒绝模式。

#### 落地状态

2026-05-01 已落地 SG-2 基础强制执行：`must_not_have_tools` 不变量会在运行时工具注册和共享工具执行策略中同时生效，脏的既有角色配置不会阻断启动，但被合约禁止的工具会被过滤并记录警告；旧的 runtime tool snapshot 也不能重新授予被合约禁止的工具。内置 Gater 已关闭 `shell` 与 `write_tmp`，只保留读取、后台任务观察和监控控制能力。设计与实现规格见 [`docs/modules/agents/sg2-role-behavior-boundaries-spec.md`](../modules/agents/sg2-role-behavior-boundaries-spec.md)。

---

### SG-3：审计追踪增强

| 字段 | 内容 |
|------|------|
| **编号** | SG-3 |
| **名称** | 审计追踪增强 |
| **所属维度** | 安全与治理 |
| **优先级** | **中** |

#### 当前状态

**部分已落地（2026-05-05 确认）**。`audit/` 模块已实现安全审计事件的结构化记录，超出原始文档"缺少"的描述：

| 能力 | 落地实现 | 关键代码位置 |
|------|----------|-------------|
| **审计事件模型** | `SecurityAuditEvent` 结构化模型 | `audit/models.py` |
| **审计事件仓储** | `security_audit_events` 表，含 event_type、trace_id、run_id、session_id、task_id、role_id、occurred_at 索引 | `audit/repository.py` |
| **审计服务** | 同步和异步双路径写入，支持文件写、shell 命令、Coordinator 决策等安全事件记录 | `audit/service.py` |
| **运行时集成** | `_record_security_audit_event_async()` 在工具执行时自动记录审计事件 | `tools/runtime/execution.py` L923, L973 |
| **API 端点** | `/api/audit` 端点供外部合规系统查询 | `interfaces/server/routers/` |

测试覆盖：`test_audit_repository.py`（7+ 测试）、`test_security_audit.py`（6+ 测试）、`test_audit_router.py`（路由过滤和限制验证）、`test_control_harness.py`（审计上下文）。

仍需后续增强的方向：跨 Session 的审计趋势分析、审计事件与 VerificationReport 的关联查询、护栏报告的安全审计集成完整性。

#### 对比价值

满足企业级部署的合规审计要求；支持事后安全事件溯源；为"Done needs evidence"的质量纪律提供系统级支持。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `Bengio_International_AI_Safety_Report_2026.md` | #12 | 国际 AI 安全报告的透明性与问责机制 |
| `Stanford_HAI_AI_Index_2026.md` | #22 | 企业 Agent 部署的生产基础设施要求 |

#### 实施建议

基础审计追踪已落地。`audit/` 模块已实现安全审计事件模型、持久化仓储、双路径写入服务和 API 端点。后续应聚焦于：(a) 将审计事件与 `VerificationReport` 关联，支持"哪个验证结果触发了什么操作"的完整溯源查询；(b) 扩展事件类型覆盖 Coordinator 的通道选择理由和任务分发决策；(c) 审计日志独立存储不可被 Agent 修改——已部分落地，需验证外部写入防护的完整性。

---

### SG-4：AI 风险评估框架嵌入

| 字段 | 内容 |
|------|------|
| **编号** | SG-4 |
| **名称** | AI 风险评估框架嵌入 |
| **所属维度** | 安全与治理 |
| **优先级** | **中** |

#### 当前状态

系统对任务的风险没有任何内置评估。所有任务不论风险等级走相同的编排流程。高影响操作（如删除数据库、发布到生产环境）与低影响操作（如查询文件内容）在编排层面无差异。

#### 对比价值

防止低级编排错误导致高影响操作（如错误的发布）；为 Human Gate 提供智能触发条件而非全手动；建立"信任但验证"的递进安全梯度。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `Dario_Amodei_Adolescence_of_Technology_2026.md` | #14 | AI 风险路径（Amodei 四类风险） |
| `Hinton_Nobel_Speech_2024_AI_Existential_Threat.md` | #13 | AI 存在性威胁（Hinton） |
| `Bengio_International_AI_Safety_Report_2026.md` | #12 | Bengio 安全报告 |

#### 实施建议

在 Coordinator 的意图评估阶段增加"风险评估"维度：定义任务风险等级（Low/Medium/High/Critical），基于操作影响范围（读 vs 写 vs 删除 vs 发布）和目标环境（本地工作空间 vs 远程仓库 vs 生产环境）自动判定。高风险任务强制启用 Human Gate，Critical 级任务要求双重确认。风险等级作为 TaskEnvelope 的元数据传递给下游角色，指导其行为策略。

---

## 维度五：工程实践 (EP)

---

### EP-1：全面 Context Engineering 策略

| 字段 | 内容 |
|------|------|
| **编号** | EP-1 |
| **名称** | 全面 Context Engineering 策略 |
| **所属维度** | 工程实践 |
| **优先级** | **高** |

#### 当前状态

已完成闭环（2026-05-04）。`conversation_compaction.py` 提供 context 压缩；`providers/prompt_caching.py` 提供 `apply_anthropic_cache_markers()` 和 `apply_openai_cache_markers()` 两个函数，已分别 wire 到 `session_prompt.py` 的 Anthropic 和 OpenAI 请求构建路径（EP-1a）；`context_editing.py` 的 `build_diff_injection()` 和 `build_injection_message()` 已 wire 到 `session_runtime.py` 的 LLM 循环，当 spec version 变化时自动注入差量消息（EP-1b）。三层 context engineering 策略（缓存/编辑/压缩）全链路贯通。

#### 对比价值

减少 20-40% 的重复 Token 处理开销（尤其对标准通道的多角色编排）；上下文压缩不再丢失关键规格信息；为超长任务提供可持续的上下文管理能力。投资回报直观可量化。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `Anthropic_Context_Engineering_Guide.md` | #4 | Context Engineering — Context Windows、Compaction、Context Editing、Prompt Caching |
| `harness/README.md` | #4 | Context Engineering 类论文（6 篇） |

#### 实施建议

参照 Anthropic 指南构建三层上下文管理策略：(1) **缓存层**——将角色的 system_prompt、工具列表、技能描述等"稳态上下文"标记为可缓存，利用 LLM Provider 的 Prompt Caching 能力避免重复处理；(2) **编辑层**——当任务规格更新时，不重建完整上下文而是通过 Context Editing 只差量注入变更部分；(3) **压缩层**（existing 增强）——增强现有 compaction 为"规格感知压缩"，默认保留任务规格和验证标准。三层策略可按 `context_strategy` 配置项选择。

**低成本起点：Provider-native prompt caching**。当前 Anthropic 和 OpenAI 等主流 Provider 已在 API 层原生支持 prompt caching——标记静态前缀为可缓存段即可在后续调用中复用，无需 relay-teams 自身构建缓存中间层。建议将 caching 分解为两个子项：
- (a) **Provider-native prompt caching**（低成本快速落地）：在 `providers/openai_compatible.py` 和 `providers/anthropic_support.py` 的请求构建路径中，标记 system prompt 前缀和工具 schema 为可缓存段，直接利用 Provider 的缓存 API 降低重复 Token 处理开销
- (b) **relay-teams 层 Context Editing**（中长期投资）：当 `TaskSpec` 局部字段更新时，不重建完整上下文而是差量注入变更部分，需要更复杂的上下文管理架构

#### 可行性确认

源码验证：`conversation_compaction.py` 存在且 1146 行，`tools/runtime/` 无缓存或编辑模块。现状描述精确。

---

### EP-2：自演化 Benchmarks 对齐质量度量

| 字段 | 内容 |
|------|------|
| **编号** | EP-2 |
| **名称** | 自演化 Benchmarks 对齐质量度量 |
| **所属维度** | 工程实践 |
| **优先级** | **中** |

#### 当前状态

已有 SWE-bench 评估（Verified 100，Normal 72% / Orchestration 73%），但评估仅在发布前手动执行，没有持续集成到开发流程中。Orchestration 模式耗时 704.2s（vs Normal 369.2s）但通过率仅提升 1%，成本效益不明确。缺少内部质量指标的持续追踪。

#### 对比价值

质量变化实时可见而非发布前才发现；为架构改进提供量化依据（如 Orchestration 模式的价值评估）；"规格合规率"直接验证 SP-1 的改进效果。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `sdd/README.md` | #18 | Benchmark 演进 — SWE-AGI 规格+代码双评估基准、OmniCode、Vibe-Code-Bench |
| `nvidia-state-of-ai-2026.md` | #20 | AI 产业 ROI 中的资源优化 |

#### 实施建议

建立三层基准体系：(1) **Micro-Benchmarks**——针对单个能力（如规格生成质量、工具调用准确率）的快速自动化测试，集成到 CI；(2) **SWE-bench 持续追踪**——每次主分支合并自动运行 SWE-bench Verified 子集，监控通过率回归；(3) **Spec-Compliance Benchmark**——参照 SWE-AGI 的规格+代码双评估思路，新增"规格合规率"指标——度量 Crafter 输出与 Designer 规格的一致性。

---

### EP-3：Agentic SDLC 全流程覆盖

| 字段 | 内容 |
|------|------|
| **编号** | EP-3 |
| **名称** | Agentic SDLC 全流程覆盖 |
| **所属维度** | 工程实践 |
| **优先级** | **低** |

#### 当前状态

relay-teams 覆盖了规划（Coordinator）、编码（Crafter）、测试/验证（Gater）三阶段，但没有延伸到部署和运维阶段。`release/` 模块存在，但其自动化程度和与新编排流程的集成度不明确。

#### 对比价值

从"AI 辅助编码"扩展到"AI 辅助交付"；减少人工在部署环节的介入；为 PwC 预测的"Agentic SDLC 全面到来"提供实践经验。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `PwC_Agentic_SDLC_2026.md` | #5 | Agentic SDLC — AI Agent 在最少人工干预下完成规划→编码→测试→部署→运维全流程 |

#### 实施建议

向下游扩展编排能力：(1) 在标准通道后增加可选的 "Deploy" 阶段（Agent 自动执行部署前置检查、环境变量验证、滚动更新等）；(2) 引入 "OpsAgent" 角色（或扩展现有 Crafter 的运维技能），负责部署后的健康检查和自动回滚；(3) 将整个 Agentic SDLC 作为可编排的"超图"——从代码修改到上线验证的全链路可视化。建议先完成核心编排能力优化后再扩展。

---

### EP-4：任务超时自动处理完善

| 字段 | 内容 |
|------|------|
| **编号** | EP-4 |
| **名称** | 任务超时自动处理完善 |
| **所属维度** | 工程实践 |
| **优先级** | **中** |

#### 当前状态

已部分落地（2026-04-28，auto-retry wired 2026-05-04）。`TaskLifecyclePolicy` 已支持 `timeout_seconds`、`heartbeat_interval_seconds`、`on_timeout`；`TaskExecutionService.execute()` 会在超时后取消 worker、写入 timeout handoff、按策略把任务标为 `TIMEOUT` 或 `STOPPED`，并将 runtime phase 转为 idle、awaiting recovery 或 awaiting manual action。心跳路径通过 `heartbeat_running_async()` 更新 running task 的 `updated_at`。2026-05-04 完成 auto-retry wiring：coordinator 在 DAG 依赖图处理和 delegated task 依赖检查时，对 `TaskStatus.TIMEOUT` 的上游记录调用 `_handle_timeout_policy_async()`，实现超时策略的自动重试/human gate；同时构造 `TIMEOUT_HANDOFF` evidence item 写入 artifact 链。

剩余缺口不再是"是否有超时"，而是"超时和唤醒能否覆盖所有长生命周期形态"：缺少 DB-backed wake queue、wake coalescing、跨进程 worker 重启后的 delegated task orphan recovery，以及基于 blocker/dependency 的自动唤醒。

#### 对比价值

已消除单个任务 worker 永久运行的主要风险。下一步价值在于把超时、心跳、唤醒、依赖解除纳入同一套生命周期控制，避免"任务已经停了但上游不知道"或"依赖已完成但下游没有被唤醒"。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `feature_codex_shell_background_process.md` | 文件级 | Codex Shell 后台进程的生命周期管理 |
| `Agent_Harness_Engineering_Survey.md`, `harness/README.md` | #1 | Harness Engineering 的运行时安全 |

> **⚠ 验收修正**: 原报告引用 "#29 Codex Shell 后台进程"。但 #29 研究点是"AI+机器人融合"——原报告将文件编号 29 与研究点编号 29 混淆。实际参考来源应为文件 `feature_codex_shell_background_process.md`（研究点报告中文件序号 29），内容为 Codex Shell 后台进程。

#### 实施建议

在已落地的 timeout/heartbeat 基础上补齐生命周期外围：(1) 增加持久 `agent_wakeups` 队列和 coalesce_key，避免同一 Agent 被重复唤醒；(2) 为 delegated task 增加 lease/claim 到期检测，服务重启后可恢复或标记 orphan；(3) Coordinator 收到 `TASK_TIMEOUT` 后按 `on_timeout` 执行自动重试、拆分任务或 human gate；(4) 将 timeout/handoff 结果纳入 Evidence Bundle，供 Gater 判断是否可接受部分成果。

---

## 维度六：功能增强 (FE)

---

### FE-1：跨 Run 的 Memory Bank

| 字段 | 内容 |
|------|------|
| **编号** | FE-1 |
| **名称** | 跨 Run 的 Memory Bank |
| **所属维度** | 功能增强 |
| **优先级** | **高** |

#### 当前状态

角色有 BM25 检索的长期记忆（`memory_bm25.py`），但 Run 之间没有显式的知识传递机制。"角色记忆"存储的是执行记录的检索索引，而非结构化的"经验教训"或"项目知识图谱"。全景报告明确指出"运行级上下文不跨 Run"。

#### 对比价值

解决"每次 Run 都从零开始"的低效问题；项目上下文通过记忆自然积累；减少重复性错误（Crafter 不会在同一项目上犯已经犯过的错误）。为角色自演化奠定基础。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `google/README.md` | #25 | Google ADK 的 Memory Bank |
| `papers/analysis/a-survey-of-self-evolving-agents-....md` | #10 | Self-Evolving Agents 的经验沉淀 |
| `hello/docs/memory/research.md`（25 篇学术论文综述） | 增补 | 六种记忆操作（Consolidation / Updating / Indexing / Forgetting / Retrieval / Condensation）、三层记忆分类、Mem0 工程实践 |

#### 学术基础：三种记忆分类与六种核心操作

来自 `hello/docs/memory/research.md`（基于 25 篇学术论文的系统性研究）提供的记忆理论框架，可直接指导 Memory Bank 的接口设计：

**三层记忆分类**：
1. **隐式记忆**（模型参数）—— LLM 的预训练权重，不可由 relay-teams 直接修改
2. **工作记忆**（上下文 KV 对）—— 当前 Run 的对话上下文，Run 结束后丢失
3. **显式记忆**（外部存储）—— 跨 Run 的结构化/向量/图结构存储，是 Memory Bank 的核心作用域

**六种核心记忆操作**：
1. **Consolidation（巩固）**—— 从短期工作记忆提取关键信息转化为长期持久记忆，类似人类海马体→新皮层机制
2. **Updating（更新）**—— 修正已有记忆条目中的过时信息，保持知识时效性
3. **Indexing（索引）**—— 为记忆建立检索结构（BM25、向量索引、图关系），支持高效查询
4. **Forgetting（遗忘）**—— 移除过期或低价值记忆，控制记忆库体积和检索噪声
5. **Retrieval（检索）**—— 按语义相关性从记忆库中召回与当前任务相关的历史知识
6. **Condensation（凝缩）**—— 将多条相关记忆压缩为更高层次的抽象总结

**Mem0 工程实践参考**：图结构记忆表示在 LOCOMO 基准上实现 26% 性能提升，p95 延迟降低 91%——为 Memory Bank 的 ROI 提供了具体数据支撑。

#### 实施建议

构建"三层 + 六操作"Memory Bank 架构：

**(1) 工作记忆层**（Run-scoped）—— 当前 Run 的上下文，Run 结束后通过 Consolidation 提取关键摘要
**(2) 中期记忆层**（Session/Role-scoped）—— 跨 Run 但仍在会话/角色范围内的知识，支持 Updating 和 Condensation
**(3) 持久记忆层**（Project-scoped）—— 跨 Session 的结构化知识，包括"项目约束"（如"本项目使用 Pydantic v2，禁止 typing.Any"）、"决策记录"（如"选择 SQLite 而非 PostgreSQL 是因为单机部署需求"）、"失败模式"（如"Crafter 在处理 X 类型文件时经常失败"）

六种操作接口应覆盖完整的记忆生命周期：Crafter 在执行前自动 Retrieval 相关持久记忆；Run 结束时触发 Consolidation + Indexing；定期执行 Forgetting（移除低价值条目）和 Condensation（合并重复/相关条目）。参考 Mem0 图结构记忆设计，优先考虑关系型存储而非纯向量检索。

Memory Bank 作为"使组织可查询"的基础设施（参考 Diana Hu "AI Native Company" 演讲中的 Queryable 工厂概念），不仅服务 Crafter 的执行效率，也为跨项目的知识复用和组织级学习奠定基础。

---

### FE-2：AutoHarness 自动工具合成

| 字段 | 内容 |
|------|------|
| **编号** | FE-2 |
| **名称** | AutoHarness 自动工具合成 |
| **所属维度** | 功能增强 |
| **优先级** | **中** |

#### 当前状态

**已落地首版（2026-04-29）**。工具系统仍支持手动注册，但已新增 AutoHarness 生成工具通道：`Crafter` 与 `MainAgent` 可以调用 `auto_harness_synthesize_tool` 生成 pending JSON utility tool，再通过 `auto_harness_enable_tool` 强制审批启用。生成工具持久化为角色资产并注册到 `ToolRegistry`，同一 run 内启用后会刷新下一轮模型请求的工具 schema。

#### 对比价值

扩展 Crafter 的能力边界而不增加手动工具维护成本；将 shell 退化调用替换为结构化工具调用；参考 DeepMind 证明的"小模型合成 Harness > 大模型直接执行"范式。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `GoogleDeepMind_AutoHarness_2026.md` | #2 | AutoHarness（DeepMind）——使用小模型自动合成代码 Harness 以超越大模型表现 |

#### 实施建议

已引入"运行时工具合成"能力。首版不是临时 MCP 包装，而是更适合 Relay Teams 角色模型的持久角色资产：生成工具必须以 `generated_*` 命名，合成后保存为 pending，启用时重新校验 AST、核对 hash、重跑测试并经过审批，随后写入目标角色工具列表并注册为普通本地工具。同名 pending 资产不会被再次合成覆盖，已持久化 manifest 也会在启动和运行加载时重新校验命名空间与代码 hash。

**扩展方向**：(a) 从当前的小型 JSON utility tool 扩展到更复杂的工具类型（如文件处理工具、API 适配工具、数据转换工具），降低通用 shell 退化调用的频率；(b) 从 Crafter / MainAgent 扩展到其他角色（如 Designer）的工具合成授权，让更多角色可以按需生成专用工具；(c) 与 OP-9（Harness 控制/计算分离）关联——自动合成的工具若涉及高风险操作，是否需要在 sandbox 中执行，应在 `RuntimeGuardrailPolicy` 中增加生成工具的行为约束规则。

---

### FE-3：MCP + A2A 双协议栈完善

| 字段 | 内容 |
|------|------|
| **编号** | FE-3 |
| **名称** | MCP + A2A 双协议栈完善 |
| **所属维度** | 功能增强 |
| **优先级** | **高** |

#### 当前状态

**已完成闭环（2026-05-05 确认）**。MCP 和 A2A 双协议栈均已实现：

- **MCP**：`src/relay_teams/mcp/` 模块完整实现 MCP 配置、注册、服务和 CLI，支持 Agent↔工具的标准化连接。
- **内部 A2A**：`agents/orchestration/a2a_bus.py`（`A2ABus`）、`a2a_bus_models.py`（Topic/Message/Subscription/State）、`a2a_tool.py`（Agent 工具）实现了完整的内部 Agent↔Agent 通信。
- **外部 A2A**：`external_agents/a2a_client.py` 实现了 Google A2A 协议客户端（`A2aHttpClient`、`probe_a2a_agent()`、`send_a2a_prompt()`），支持与外部 A2A Agent 互操作。
- **ACP**：`external_agents/acp_client.py` 的 ACP 传输客户端与 A2A 并存。
- **协议模型**：`external_agents/models.py` 支持 `ExternalAgentProtocol.A2A` 和 `ExternalAgentProtocol.ACP` 两种协议声明。

测试覆盖：`test_a2a_bus.py`、`test_a2a_bus_models.py`、`test_a2a_client.py`、集成 API 测试 `test_a2a_internal.py`。

#### 对比价值

与行业标准对齐；支持跨框架 Agent 协作（与 LangGraph/CrewAI/AutoGen 生态互通）；为 relay-teams 成为"A2A 原生框架"提供差异化竞争力。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `mas/00-INDEX.md` | #8 | MCP（Agent→工具）+ A2A（Agent→Agent）互补标准成为行业共识 |

#### 实施建议

已完成。MCP（Agent↔工具）+ A2A（Agent↔Agent）双协议栈已并行实现：内部 A2A 消息总线支持 Run 级别的结构化通信，外部 A2A 客户端支持与 Google A2A 生态互操作。后续可增强方向：(a) 将 A2A 消息持久化支持跨 Run/跨 Session 的长生命周期通信；(b) 扩展 A2A 服务发现、能力广告和质量协商；(c) 与 OP-8 跨 Provider 治理包集成，统一内部和外部 A2A handoff 格式为五元组结构。

#### 可行性确认

源码验证确认 MCP、A2A（内部+外部）和 ACP 三协议均已实现并通过测试覆盖。

---

### FE-4：优先级调度与资源感知

| 字段 | 内容 |
|------|------|
| **编号** | FE-4 |
| **名称** | 优先级调度与资源感知 |
| **所属维度** | 功能增强 |
| **优先级** | **中** |

#### 当前状态

任务分发无优先级排序，仅按创建顺序处理。`TaskEnvelope` 中确认**无 priority 字段**。当多个任务同时待处理时，同等对待紧急修复和低优先级优化。

#### 对比价值

紧急任务（如安全修复）不被常规任务阻塞；系统在高负载下优雅降级而非硬性拒绝；多用户场景下的公平性和优先级保障。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `mas/00-INDEX.md` | #7 | 五大编排模式中的 Supervisor 模式优先级队列 |
| `nvidia-state-of-ai-2026.md` | #20 | AI 产业 ROI 中的资源优化 |

#### 实施建议

在 `TaskEnvelope` 中增加 `priority` 字段（Critical/High/Normal/Low）。Coordinator 创建子任务时可指定优先级。`TaskOrchestrationService.dispatch_task()` 在选择下一个待处理任务时考虑优先级。同时引入"资源感知"——根据当前系统负载（活跃 Run 数、LLM API 队列深度）自动调整并行度和接受新任务的意愿。

#### 可行性确认

源码验证 TaskEnvelope 确认无 priority 字段，现状描述精确。

---

### FE-5：验证引擎智能化升级

| 字段 | 内容 |
|------|------|
| **编号** | FE-5 |
| **名称** | 验证引擎智能化升级 |
| **所属维度** | 功能增强 |
| **优先级** | **高** |

#### 当前状态

部分已升级（2026-04-28）。`verify_task()` 不再只是非空/字符串 checklist：`VerificationPlan` 支持 `required_files`、`command_checks`、`acceptance_criteria`、`evidence_expectations`，命令验证会受 `ToolApprovalPolicy` 和 role allowed tools 约束，并对 stdout/stderr 做 bounded capture；结果汇总为结构化 `VerificationReport`。

2026-05-01 已继续补齐 Evidence Bundle 与语义判定基础：`VerificationReport` 现在携带 normalized `VerificationEvidenceBundle`，会把任务结果、required file、命令输出、工具调用/结果事件和 Gater/timeout 类 findings 归一化为 evidence item；测试、lint、diff、形式化验证命令输出会解析为结构化 metrics。acceptance criteria 与 evidence expectations 不再只看结果文本引用，而是生成 evidence link，并新增 Evidence 与 Semantic 两层检查。语义层先落地规则 evaluator，并预留外部/LLM evaluator 注入点；外部 evaluator 失败时会记录日志并回退到规则判定。

仍需后续增强的是：将真实 LLM evaluator 接入运行时 provider，并对高严格度任务增加更强的重复性控制、多模型互评和形式化验证 profile。

#### 八层验证管线实现细节

FE-5 的验证引擎已升级为完整的八层管线，2026-05-04 完成 real LLM evaluator wiring：VerificationEvaluatorFactory.build() 返回 _LlmSemanticEvaluator 而非 stub（`verification.py` 中 `_run_strictness_checks()` 和各层专用检查方法），由 `TaskSpecStrictness` 驱动检查深度：

| 验证层 | VerificationLayer 枚举值 | 检查内容 | Strictness 要求 |
|--------|--------------------------|----------|----------------|
| **Structure** | `STRUCTURE` | 非空结果检查、响应结构完整性 | LOW + |
| **Behavior** | `BEHAVIOR` | command_checks 执行、stdout/stderr bounded capture | MEDIUM + |
| **Evidence** | `EVIDENCE` | required_files 存在性、evidence items 归一化、acceptance_links 和 expectation_links 生成 | MEDIUM + |
| **Semantic** | `SEMANTIC` | 规则 evaluator + 外部/LLM evaluator 注入点，评估实现是否满足 spec 语义 | MEDIUM + |
| **Spec** | `SPEC` | spec compliance 检查，验证结果与 TaskSpec 的一致性 | MEDIUM + |
| **Contract** | `CONTRACT` | RoleContract postconditions 检查（result_mentions_acceptance_criteria、result_mentions_evidence_expectations、handoff_present） | MEDIUM + |
| **Security** | `SECURITY` | 安全相关约束检查 | HIGH |
| **Formal** | `FORMAL` | proof artifact 存在性、replay command 执行、counterexample 不存在性检查 | HIGH + FormVerificationPlan |

HIGH strictness 任务强制要求 verification commands / required files / evidence expectations / formal verification evidence 中至少一项。外部 evaluator 失败时回退到规则判定并标记 `fallback=True`。

#### 对比价值

自动验证已经具备结构层和行为层雏形。继续升级的价值在于把 Gater 从"人工重读所有上下文"中解放出来，让它消费结构化证据包和语义判定结果，专注于高判断力审查。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `sdd/README.md` | #3 | SDD 的自动化验证 |
| `harness/README.md` | #6 | Agent 可靠性科学框架 |
| `sdd/README.md` | #18 | SWE-AGI 的规格+代码双评估 |

#### 实施建议

在现有 `VerificationReport` 上继续升级为四层验证：(1) **结构验证**——沿用 required files、格式/schema、关键字段检查；(2) **行为验证**——沿用 command checks，并增加测试、lint、diff 统计的标准化解析；(3) **证据验证**——检查每条 acceptance criterion 是否有对应 evidence item，而不仅是文本引用；(4) **语义合规验证**——由 LLM 或规则+LLM 混合 evaluator 判断实现是否满足 spec，并输出可复核理由。第一阶段已落地 Evidence Bundle、证据链接、规则语义 evaluator 和外部 evaluator 回退机制，后续应把 LLM evaluator 接到受控运行时配置。

#### 可行性确认

源码验证 `verification.py` 已包含结构、行为、SPEC 三类 `VerificationLayer`，且 `VerificationReport` 已存在。剩余工作聚焦 Evidence Bundle 和语义 evaluator，**可行性：中高**，2-3 周可交付。风险点：LLM evaluator 需要可重复性控制和失败时的降级策略。

---

### FE-6：对比实验框架

| 字段 | 内容 |
|------|------|
| **编号** | FE-6 |
| **名称** | 对比实验框架 |
| **所属维度** | 功能增强 |
| **优先级** | **低** |

#### 当前状态

SWE-bench 评估存在但仅为发布前执行，没有 A/B 对比实验的能力。Orchestration vs Normal 的对比数据（73% vs 72%）是已有的对比案例，但无法系统性复现。

#### 对比价值

为每个架构改进提供量化验证手段；帮助用户选择最适合其场景的编排策略；积累"什么情况下什么策略最优"的实践知识。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `GoogleDeepMind_AutoHarness_2026.md` | #2 | AutoHarness 的 145 种 TextArena 游戏对比实验 |
| `Brynjolfsson_Generative_AI_at_Work.md` | #19 | Brynjolfsson 的交错引入准实验设计 |

#### 实施建议

构建内置的"编排策略对比实验框架"——允许针对相同输入意图，以不同编排策略（如 Normal vs Standard Channel vs 自定义 DAG）并行执行并对比结果质量、耗时、Token 消耗。结果自动存储并可视化，支持统计分析。参照交错引入设计，同一批任务分别用新旧策略执行。

---

## Top 3 紧迫行动建议

> 以下基于 2026-05-05 进度重新评估。AO-1、AO-2、AO-4、SP-1、SP-2、SG-2、RP-1、FE-3 均已闭环，推荐重心已转移。

### 1. FE-1：跨 Run 的 Memory Bank（核心差异化）

**完成情况**：学术基础已充分（六种记忆操作 + 三层分类 + Mem0 工程数据），relay-teams 已有 BM25 记忆（`memory_bm25.py`）和角色记忆服务作为基础。RP-2 的 `RolePerformanceMetrics` 和 OP-10 的 `MemoryEntryKind.FAILURE_MODE` 已为 Memory Bank 提供结构化存储模式。

**预期收益**：解决"每次 Run 都从零开始"的低效问题，为闭环组织（Closed-loop Organization）奠定数据基础。

### 2. OP-1：DB-backed Wake Queue 与唤醒合并

**为什么紧迫**：Monitor 事件驱动基础设施已就绪（`MonitorActionType.WAKE_INSTANCE` / `WAKE_COORDINATOR` / `START_FOLLOWUP_RUN`），但缺少持久唤醒队列和去重机制。EP-4 的 timeout/heartbeat/auto-retry 已落地，唤醒合并将把超时、心跳、唤醒、依赖解除纳入同一套生命周期控制。

**预期收益**：避免"任务已经停了但上游不知道"或"依赖已完成但下游没有被唤醒"的编排黑洞。

### 3. OP-11：Task Board as State Machine

**为什么紧迫**：Automation/Triggers/Monitors 基础设施已完整实现，内部 `TaskStatus` 和事件流已有基础。OP-2 的原子 Claim 和 OP-1 的 Wake Queue 是前置依赖。

**预期收益**：将任务看板从展示层升级为调度输入和状态权威，实现 OpenAI Symphony 式的看板驱动调度。

---

## 四阶段实施路线图

> **进度标注更新至 2026-05-05**。已完成条目标注完成日期，部分完成条目标注已落地范围和剩余缺口。

```
Phase 1 — 基础加固（4-6 周）                                              ✅ 已完成├── AO-1: TaskExecutionService Harness 解构  ← ✅ 已完成闭环（2026-04-29）
├── AO-3: 编排参数可配置化                   ← ✅ 已完成闭环（2026-04-30）
├── EP-4: 任务超时自动处理                   ← ✅ timeout/heartbeat/auto-retry 已落地（2026-05-04 wiring complete）
├── SG-2: 角色边界强制执行                   ← ✅ 基础已落地（2026-05-01）
└── OP-7: Bounded Agent / Tool Diet 静态校验  ← ✅ 已完成闭环（2026-05-04：role save rejection + coordinator dispatch check + unit tests）

Phase 2 — 质量与安全核心（6-8 周）                                    ✅ 已完成
├── SP-1: 形式化规格嵌入                     ← ✅ 已完成闭环（2026-05-02），六大缺口全闭合
├── OP-12: Structured Prompt Artifact        ← ✅ 已被 SP-1 的 REASONS Canvas + spec artifact 覆盖
├── OP-13: Lightweight Formal Verification   ← ✅ 已被 SP-1 的 FormalVerificationPlan 覆盖
├── FE-5: 验证引擎升级                       ← ✅ 八层验证管线已闭环（2026-05-02），real LLM evaluator wired through factory（2026-05-04）
├── SG-1: 三层运行时护栏                     ← ✅ 基础框架已落地（2026-05-03），guardrail audit persistence repository completed（2026-05-04）
├── EP-1: Context Engineering                ← ✅ 已完成闭环（2026-05-04：OpenAI cache markers wired + Anthropic markers already present + context editing already wired in session_runtime）
├── SP-2: RoleContract                       ← ✅ 已完成闭环（2026-05-01）
├── OP-3: 递增式 Task Artifact / Evidence    ← ✅ 已完成闭环（2026-05-04：auto-append in EXECUTION/VERIFICATION/DELIVERY phases confirmed wired）
└── OP-9: Harness 控制面与 Sandbox 计算面    ← ✅ 已完成闭环（2026-05-04：TaskControlHarness imported into task_execution_service，_control_harness factory method added）

Phase 3 — 编排与通信进化（6-8 周）                                   ⬡ 大部分已完成
├── RP-1/A2A: Agent间直接通信                 ← ✅ 已完成闭环（2026-05-05 确认：内部 A2A 消息总线 + 外部 A2A 客户端 + Agent 通信工具）
├── AO-2: DAG 编排引擎                       ← ✅ 已完成闭环（2026-04-30）
├── FE-1: Memory Bank                        ← 未启动（学术基础已补充，见 FE-1 章节）
├── FE-3: MCP + A2A 双协议栈                 ← ✅ 已完成闭环（2026-05-05 确认：MCP + 内部 A2A bus + 外部 A2A Google protocol + ACP 并存）
├── EP-2: Benchmark 体系                     ← 未启动
├── OP-11: Task Board as State Machine       ← 未启动（Automation/Triggers 基础设施已就绪）
├── OP-1: DB-backed Wake Queue               ← 未启动（Monitor 事件驱动基础设施已就绪）
├── OP-2: Atomic Claim / blocker 自动推进    ← 未启动
└── OP-4: Provider-native runtime config     ← ⬡ 部分已落地（external_agents/native_config.py 已实现；内部 skill bridge 待实现）

Phase 4 — 差异化特性（按需启动）                                     ⬡ 部分已完成
├── FE-2: AutoHarness 工具合成               ← ✅ 已落地首版（2026-04-29）
├── OP-5: 预算硬停与 Token 经济学             ← 未启动
├── OP-6: Multi-Provider 互评与漂移检测       ← 未启动
├── OP-8: 跨 Provider 治理包与 A2A 五元组     ← 未启动（Cat Cafe 治理包参考已补充，见 OP-8）
├── OP-10: Failure-mode driven MVH eval loop ← ✅ 已启动（2026-05-05：`src/relay_teams/agents/evaluation/` 实现）
├── RP-2: Self-Evolving Agent                ← ✅ 已启动（2026-05-05：`src/relay_teams/roles/` 实现）
├── FE-6: 对比实验框架                       ← 未启动
├── RP-3: Swarming 模式                      ← 未启动
├── RP-4: Agent 能力分级标注                  ← 未启动
├── AO-4: 同步/异步路径统一                   ← ✅ 已完全闭环（2026-05-04，PR #675，router 层 call_maybe_async 全消除）
├── SP-3: Spec-Checkpoint 抗退化              ← ✅ 已全部落地（2026-05-03），增强项 E1-E4 完成
├── SG-3: 审计追踪增强                       ← ⬡ 部分已落地（audit/ 模块：模型/仓储/服务/API 端点；与 VerificationReport 关联待实现）
├── SG-4: AI 风险评估框架                     ← 未启动
├── FE-4: 优先级调度与资源感知                ← 未启动
└── EP-3: Agentic SDLC 全流程                ← 未启动
```

---

## 优先级分布汇总

| 优先级 | 数量 | 编号 |
|--------|------|------|
| **高** | 11 | AO-1, AO-2, RP-1, SP-1, SP-2, SG-1, SG-2, EP-1, FE-1, FE-3, FE-5 |
| **中** | 10 | AO-3, AO-4, RP-2, SP-3, SG-3, SG-4, EP-2, EP-4, FE-2, FE-4 |
| **低** | 4 | RP-3, RP-4, EP-3, FE-6 |

### 2026-04-28/29 增补优先级

| 优先级 | 数量 | 编号 |
|--------|------|------|
| **高** | 7 | OP-1, OP-2, OP-3, OP-5, OP-9, OP-11, OP-12 |
| **中** | 6 | OP-4, OP-6, OP-7, OP-8, OP-10, OP-13 |

---

## 2026-05-03 增补：产品形态洞察与工程模式记录

### Diana Hu "AI Native Company" 产品形态洞察

Diana Hu（YC Partner）在 Y Combinator Startup School 演讲中提出三个直接影响 relay-teams 产品演进方向的核心洞察：

1. **闭环组织（Closed-loop Organization）**——AI 应使组织从"执行但不系统性度量"的开环模式升级为"持续监控和自我调节"的闭环系统。relay-teams 的 FE-1 Memory Bank（记忆巩固与检索）和 RP-2 Self-Evolving Agent（角色记忆反馈优化）应定位为闭环组织的具体实现：每次 Run 的结果和验证报告不只是一次性产物，而是驱动下一轮执行优化的输入。`VerificationReport.evidence_bundle` 和 `TaskSpecArtifact` 的版本链是闭环的数据基础。

2. **可查询（Queryable）**——每个重要行动产生可被 AI 学习和使用的工件。这超出了当前 FE-1 的"跨 Run 记忆"，上升到组织级信息架构：`TaskSpec` 的结构化字段、`VerificationReport` 的分类发现、`security_audit_events` 的操作记录，都应成为可被未来 Run 系统性查询的知识资产。"使组织可查询"是 Memory Bank 和检索系统（`retrieval/` FTS 模块）的高层动机。

3. **AI 软件工厂（AI Software Factory）**——人类写规格和测试，AI 迭代直到测试通过，是 TDD 的下一步进化。relay-teams 已具备基础：SP-1 的结构化规格 + FE-5 的八层验证管线 + SG-1 的运行时护栏。从 spec-driven 到 software factory 的演进路径：当前是"人类写规格 → Agent 生成代码 → Gater 验证"；下一步是"人类写规格 + 测试 → Agent 迭代直至通过 → 人类只做最终审查"。`TaskSpecStrictness.HIGH` 和 `FormalVerificationPlan` 是这条路径的技术基础。

这三个洞察将 relay-teams 的多项独立改进方向（FE-1 记忆、SP-1 规格、FE-5 验证、SG-1 护栏）串联为一个连贯的产品演进叙事。

### 事件驱动基础设施工程模式

relay-teams 已构建完整的事件驱动基础设施，由三个模块协同组成一条"事件源摄入 → 归一化 → 规则匹配 → 动作调度"链路：

**Triggers（`triggers/`）**——外部事件的受控入站通道：
- `TriggerRuleRecord` / `TriggerRuleMatchConfig`：规则持久化和匹配配置
- `GitHubTriggerAccount` / `GitHubRepoSubscription`：GitHub webhook 入站
- 支持 GitHub webhook → `trigger_rules` 表匹配 → 自动化行为触发

**Monitors（`monitors/`）**——事件归一化与订阅管理：
- `MonitorEventEnvelope`：统一事件信封（source_kind + event_name + body_text + attributes + dedupe_key）
- `MonitorRule`：完整匹配规则（event_names / text_patterns_any / attribute_equals / attribute_in / cooldown / max_triggers / auto_stop / case_sensitive）
- `MonitorActionType`：四种动作（WAKE_INSTANCE / WAKE_COORDINATOR / START_FOLLOWUP_RUN / EMIT_NOTIFICATION）
- `MonitorSubscriptionRecord`：订阅生命周期（active/stopped + trigger_count + last_triggered_at）
- `MonitorService.emit()`：评估规则匹配、记录触发审计、调度动作

**Automation（`automation/`）**——计划驱动的任务触发系统：
- `AutomationProjectRepository`：自动化项目完整 CRUD
- `AutomationService`：含 async 全链路的管理服务
- `AutomationBoundSessionQueueService`：自动化任务队列与会话绑定
- `AutomationProjectStatus` 枚举：项目状态管理
- 支持 cron / webhook / API 触发的定期任务执行

三者关系：Triggers 从外部系统（GitHub 等）摄入事件 → Monitors 归一化并匹配订阅规则 → 执行动作（唤醒实例/后续运行/通知）→ Automation 提供计划驱动的触发模式。Triggers + Monitors 构成了 OP-11 "Task Board as State Machine" 和 OP-1 "Wake Queue" 的基础设施前身。

### 钩子系统事件驱动架构

`hooks/` 模块实现了灵活的运行时事件拦截和处理机制：

- **事件模型**：`HookEventName` 枚举覆盖 18 种运行时事件（SessionStart/End、UserPromptSubmit、PreToolUse、PostToolUse、TaskCreated/Completed、PreCompact/PostCompact 等）
- **匹配层**：`HookMatcherGroup` 支持按事件名、条件、来源作用域进行精细匹配
- **执行器**：四种 `HookHandlerType`——command（子进程）、http（远程调用）、prompt（上下文注入）、agent（Agent 执行）
- **决策类型**：`HookDecisionType` 支持 10 种决策——allow / deny / ask / updated_input / additional_context / continue / retry / set_env / defer / observe
- **来源分层**：`HookSourceScope` 六层——user → project → project_local → plugin → role → skill
- **运行时状态**：`HookRuntimeState` 跟踪 Hook 执行上下文

这一架构为 relay-teams 提供了贯穿系统所有层的事件拦截能力，从用户输入预处理到工具调用审批到任务生命周期事件，均通过统一的 Hook 管线处理。

### 插件系统 Manifest + Component Source 模式

`plugins/` 模块实现了声明式插件架构：

- **`PluginManifest`**：声明插件提供的组件（skills / roles / commands / hooks / mcp_servers / monitors / settings）+ 用户可配置字段（type / title / default / sensitive / required）+ 插件依赖
- **`PluginComponentKind`**：七种组件类型（skills / roles / commands / hooks / mcp_servers / monitors / settings）
- **`PluginScope`**：五种作用域（local / user / project / project_local / managed）
- **`PluginRegistry`**：聚合所有插件的 component sources 并提供统一查询
- **`PluginDiagnostic`**：插件加载失败时记录诊断信息而不崩溃启动
- **组件源解析**：`PluginComponentSource` 统一解析插件内各种组件的路径

### Pydantic v2 严格模型 + 异步优先架构实践

relay-teams 的工程实践中体现了两个一致性的架构约束：

**Pydantic v2 严格模型**：
- 所有领域模型使用 `BaseModel` + `ConfigDict(extra="forbid")`，拒绝未知字段
- 显式枚举替代松散字符串：`TaskStatus`（7 值）、`VerificationLayer`（8 值）、`TaskSpecStrictness`（3 值）、`RuntimeGuardrailLayer`（3 值）等
- 标识符验证：`RequiredIdentifierStr` / `OptionalIdentifierStr` 拒绝 blank / whitespace-only / "None" / "null"
- 容错读取：仓储读路径对无效标识符/时间戳只记录日志警告并跳过，不阻断 API

**异步优先架构**：
- 全量 async aiosqlite 原生路径，`_call_sync_async` 仅在 `SharedSqliteRepository` 保留为兼容桥
- 新业务模块禁止调用 `_call_sync_async`，仓库级静态测试阻断回流
- 同步入口仅保留在 CLI 等必要边界，通过 `asyncio.run()` 调用 async API

### 工作空间制品管理

`workspace/` 模块实现了任务级的隔离制品管理：

- `WorkspaceService.fork_workspace()` 基于 Git worktree 创建隔离工作区
- `workspace/artifacts` 提供制品注册和查询（`media_assets` 表记录媒体资产）
- 工作区与 Session/Run 绑定，支持多任务并行互不干扰
- 当前仍未做到每个 delegated task 自动独占工作区，也缺少任务级 merge/review gate

### 会话运行管理（sessions/runs/）的事件驱动设计

`sessions/runs/`（37 个文件）实现了 Run 级别的完整生命周期管理：

- **Run 生命周期**：created → running → completed / failed / timeout / stopped
- **EventLog + RunEventHub**：持久事件日志 + SSE 事件流，支持 Monitor 订阅
- **InjectionQueue**：运行时注入队列，用户/系统可在 Run 执行中追加消息，在 LLM 循环安全边界（模型请求前 / tool call batch 后）释放
- **崩溃恢复**：从持久化的 `run_intent` / recovery state 恢复中断的 Run
- **后台任务**：独立进程执行 shell / subagent，输出持久化到日志文件，Monitor 可订阅后台任务输出
- **RunScheduler + RunFollowups**：调度和后续运行管理，与 Monitor 的 `START_FOLLOWUP_RUN` 动作联动

### hello 文档中的治理经验借鉴

hello 项目的 `governance/`、`holistic-modeling/`、`openspec/` 三个目录提供了额外的治理经验：

- **governance/**：治理策略文档，验证了 Cat Cafe 四层治理结构的系统性和实践性
- **holistic-modeling/**：整体建模方法论，与 relay-teams 的 `TaskSpec` REASONS Canvas 七维结构形成呼应
- **openspec/**：开放规格标准，与 relay-teams 的 `TaskSpecArtifact` 版本化和 OpenSPDD 工具链理念一致

---

## 总结

本研究通过对 2026 年 AI Agent 领域 38 篇前沿研究的系统分析，识别出 25 个与 relay-teams 产品高度相关的改进借鉴点。这些借鉴点分布在六大维度，覆盖了从底层架构到顶层功能的完整技术栈。2026-04-28/29 增补又对 hello 项目中 1226 个关键词命中的 AI Markdown 文件、OpenAI Symphony 状态机研究、SPDD 归档和形式化验证归档做复盘，并把本文件引用的 hello 来源材料按主题整理到本目录下的研究分类中，补充了 13 个更偏运营化、产品化和跨 Provider 协作的 OP 借鉴点。2026-05-03 进一步基于差距分析更新了路线图进度标注，补充了事件驱动基础设施、治理包模式、记忆系统学术基础和产品形态洞察。

**核心发现**：

1. **架构层面**：TaskExecutionService 的 Harness 解构（AO-1）和 DAG 编排（AO-2）均已完成，架构基础已扫清主要障碍；同步/异步统一（AO-4）已完成全部五阶段迁移，包括 router 层 `call_maybe_async` 全消除和服务层 async 方法补全（PR #675）
2. **流程层面**：Spec-Driven Development 的形式化嵌入（SP-1）六大缺口已全部闭合；八层验证管线（FE-5）已闭环；RoleContract（SP-2）已结构化角色行为合约；SP-3 增强项 E1-E4 全部完成
3. **安全层面**：角色行为边界（SG-2）已落地强制执行；运行时护栏（SG-1）基础框架已实现三层/六规则/三动作模型；审计追踪（SG-3）已部分落地（模型/仓储/服务/API 端点）
4. **工程层面**：全面的 Context Engineering（EP-1）已完成闭环（2026-05-04），Provider-native prompt caching + context editing + spec-aware compaction 三层策略全链路贯通
5. **通信层面**：A2A 协议栈（RP-1 + FE-3）已完成闭环（2026-05-05 确认），内部 A2A 消息总线 + 外部 Google A2A 客户端 + MCP 三协议并存
6. **功能层面**：跨 Run 的 Memory Bank（FE-1）有坚实学术基础支持（六种记忆操作 + 三层分类 + Mem0 数据），是下一阶段核心差异化特性
7. **运营层面**：事件驱动基础设施（Triggers + Monitors + Automation）已完整实现，为 OP-11 Task Board 和 OP-1 Wake Queue 提供了基础设施前身
8. **角色层面**：Self-Evolving Agent（RP-2）和 Failure-mode MVH eval loop（OP-10）已于 2026-05-05 实现并提交（PR #685），角色演化闭环与失败模式分类框架已落地

**产品演进远景**：结合 Diana Hu "AI Native Company" 演讲的三个核心洞察——闭环组织、可查询工厂、AI 软件工厂——relay-tems 的多项改进方向可串联为连贯的产品叙事：Memory Bank 是闭环组织的数据基础，结构化 `TaskSpec`/`VerificationReport` 是可查询的工件，SP-1 + FE-5 + SG-1 的组合是 AI 软件工厂的技术底座。

四阶段路线图确保了依赖关系的正确性和实施的渐进性。Phase 1 和 Phase 2 已全部闭环，Phase 3 的 RP-1/A2A 和 FE-3/MCP+A2A 也已闭环，后续重点应转向：(a) FE-1 Memory Bank 启动实施；(b) OP-1 DB-backed Wake Queue 和 OP-2 Atomic Claim 作为编排进化的基础设施；(c) OP-11 Task Board as State Machine 将调度输入从信息展示升级为状态驱动。

---

*本文件整合自 cross-reference-analysis.md（25 借鉴点）、markdown-research-points.md（35 研究点 + 38 源文件）、validation-report.md（验收修正）三份原始报告，并在 2026-04-28/29 增补 hello AI Markdown、OpenAI Symphony 状态机、SPDD 和形式化验证复盘；2026-05-03 基于差距分析报告更新路线图进度、补充事件驱动基础设施记录、Cat Cafe 治理包工程细节、记忆系统学术基础和 Diana Hu 产品形态洞察；2026-05-05 基于代码库逐一验证修正 RP-1/FE-3/SG-3/OP-4 状态，刷新路线图进度和行动建议。所有原 25 个借鉴点 + 13 个 OP 借鉴点已逐一提取、交叉引用来源、标注验收修正和落地状态。*

## 更新日志

### 2026-05-03 差距分析驱动更新
- **P0**: 修正 SG-1 运行时护栏状态（未实现→基础框架已落地），补充 RuntimeGuardrailLayer 三层/六规则/三动作模型实现细节；刷新四阶段路线图进度标注（Phase 1 已完成，Phase 2 大部分完成，Phase 3 部分启动，Phase 4 部分已完成）；补充 Monitor 事件驱动子系统描述（MonitorRule 完整匹配规则 + MonitorActionType 四种动作 + 订阅生命周期管理）
- **P1**: 吸收 Cat Cafe 治理包四层治理结构（Hard Constraints / Collaboration Standards / Quality Discipline / Knowledge Engineering）到 OP-8 作为参考模板；补充 FE-1 Memory Bank 学术基础（六种记忆操作 Consolidation/Updating/Indexing/Forgetting/Retrieval/Condensation + 三层记忆分类 + Mem0 工程数据）；新增 Diana Hu AI Native Company 演讲的三个产品形态洞察（闭环组织 / 可查询 / AI 软件工厂）
- **P2-P3**: 补充 Automation/Triggers 模块到事件驱动基础设施描述和实现快照表；EP-1 补充 Provider-native prompt caching 作为低成本起点策略；AO-1 补充后续收敛方向（提取 lifecycle Harness）；AO-4 补充同步 API 收敛路线；FE-2 补充扩展方向；SP-3 标注为"增强已完成，维护态"；FE-5 八层验证管线补充具体实现细节表；补充钩子系统事件驱动架构模式、插件系统 Manifest/Component Source 模式、Pydantic v2 严格模型实践、工作空间制品管理、会话运行管理事件驱动设计、hello 治理经验借鉴等工程模式记录

### 2026-05-05 代码库验证驱动更新
- **P0**: 修正 RP-1/A2A 状态（未启动→已完成闭环），源码确认内部 A2A 消息总线 + 外部 Google A2A 客户端 + Agent 通信工具均已实现并通过测试覆盖；修正 FE-3/MCP+A2A 状态（A2A 未实现→已完成闭环），双协议栈全链路贯通；修正 SG-3 审计追踪状态（未启动→部分已落地），`audit/` 模块含模型/仓储/服务/API 端点；修正 OP-4 状态（未启动→部分已落地），`native_config.py` 为外部 Agent 生成 provider-native 配置
- **P1**: 刷新四阶段路线图进度标注（Phase 1 已完成，Phase 2 已完成，Phase 3 大部分已完成，Phase 4 部分已完成）；更新 Top 3 紧迫行动建议（EP-1 和 OP-9 已完成，新重心转向 FE-1 Memory Bank / OP-1 Wake Queue / OP-11 Task Board）；更新"与原 25 点的关系"和"当前实现快照"表反映 A2A 和审计追踪落地；更新核心发现摘要补充通信和角色层面进展
- **P2-P3**: OP-10 和 RP-2 已于 2026-05-05 实现并提交（PR #685），OP-10 含 FailureModeClassifier/RunSamplingService/DistributionAnalyzer/MVHRecommendationReport，RP-2 含 RolePerformanceMetrics/RoleSelfAssessmentService/SystemPromptAdjustmentEngine/MaturityScoringEngine/TemporaryRoleKnowledgeCaptureService/RoleEvolutionHistoryService
