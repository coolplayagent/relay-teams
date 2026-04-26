# 2026 AI Agent 研究借鉴分析 — relay-teams 改进路线图

> **文档类型**: Feature Analysis  
> **创建日期**: 2026-04-25  
> **feature_ids**: lessons-learned-2026  
> **topics**: architecture, orchestration, spec-driven, security, engineering, enhancements  
> **doc_kind**: analysis  

---

## 概述

本文档基于 **2026 年 AI Agent 领域的 38 篇前沿研究**，系统性地提取了 **25 个结构化借鉴点**，覆盖架构优化、角色与编排、Spec-Driven 流程、安全与治理、工程实践、功能增强六大维度。每个借鉴点均经过源码验证，与 relay-teams 当前实现进行详细对比，并给出可操作的实施建议和优先级评估。

## 研究背景

### 研究方法

本研究采用以下方法论：

1. **文献收集**：系统整理 2026 年 AI Agent 领域 38 个 markdown 格式研究文件，涵盖学术论文、行业报告、框架文档
2. **研究点提取**：从中提炼出 **35 条核心研究点**，涵盖 Harness Engineering、Self-Evolving Agents、Spec-Driven Development、Agent 安全、Context Engineering 等关键主题
3. **交叉引用分析**：将 35 条研究点与 relay-teams 源码现状逐一对照，识别出 **25 个可落地的改进借鉴点**
4. **源码验证**：关键数据点均通过源码实际验证（如文件行数、字段存在性、方法签名等），验收准确率达 **92.5%**

### 关键数据来源

| 来源 | 内容 | 数量 |
|------|------|------|
| `cross-reference-analysis.md` | 综合借鉴分析报告 | 25 个借鉴点 |
| `markdown-research-points.md` | 研究点提取报告 | 35 条研究点 |
| `validation-report.md` | 验收报告 | 覆盖率 74.3%，准确率 92.5% |

### 验收概况

| 指标 | 数值 | 说明 |
|------|------|------|
| 研究点覆盖率 | 74.3%（26/35） | 9 个研究点未被借鉴分析覆盖 |
| 准确率 | 92.5%（加权） | 6/8 抽查完全准确，1/8 部分不准确，0/8 事实错误 |
| 编号归因错误 | 4 处 | 已在本文件中全部修正 |
| 综合评级 | **B+（84.9%）** | 加分：分析深度和可操作性；扣分：覆盖率和统计严谨性 |

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

**已完成（2026-04-26）**。`TaskExecutionService` 已按 Harness 模式拆分，`task_execution_service.py` 从 **1870 行**降至 **1174 行**，主类保留执行入口、运行时状态流转和兼容性委托；原先混杂在单文件中的职责迁移到 `src/relay_teams/agents/orchestration/harnesses/`：

| Harness | 落地模块 | 职责 |
|---------|----------|------|
| `TaskPromptHarness` | `prompt_harness.py` | runtime prompt section、用户 prompt、技能候选、共享状态快照、对话 prompt 持久化 |
| `TaskToolHarness` | `tool_harness.py` | 本地工具、技能工具、MCP 工具的 runtime snapshot 构建 |
| `TaskLlmHarness` | `llm_harness.py` | 单轮 LLM 调用、thinking 配置、todo 完成 guard 与重试 |
| `TaskPersistenceHarness` | `persistence_harness.py` | assistant error 持久化、任务完成 hook、角色记忆、runtime lane 提升 |

现有私有方法名保留为兼容层，避免测试和内部调用一次性迁移；后续 AO-2、SG-1、SP-1、EP-1 可以直接在对应 Harness 边界注入新策略。

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

已完成第一阶段结构拆分。后续建议是在兼容层稳定后逐步把外部调用从 `TaskExecutionService` 私有方法迁移到 Harness 公共方法，并为各 Harness 增加更聚焦的单元测试；AutoHarness 的"代码即策略"连续频谱模型可作为 `TaskToolHarness` 后续扩展方向。

---

### AO-2：Graph-based 编排替代线性 Pipeline

| 字段 | 内容 |
|------|------|
| **编号** | AO-2 |
| **名称** | Graph-based 编排替代线性 Pipeline |
| **所属维度** | 架构优化 |
| **优先级** | **高** |

#### 当前状态

编排预设仅有三条线性通道：咨询（直接答复）/快速（Crafter→Gater）/标准（Designer→Crafter→Gater）。CoordinatorGraph 的 `_run_ai_mode()` 是固定循环结构。复杂任务无法表达条件分支、并行汇聚、动态拓扑等非线性的协作模式。

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

引入有向无环图（DAG）编排引擎。任务分解产出的是 Node+Edge 的图结构而非线性队列：每个 Node 绑定 role_id + objective，Edge 定义数据流和依赖关系。保留现有三通道作为预设模板（Template Graph），同时支持 Coordinator 动态构建自定义编排图。Fan-Out+Join 模式特别适用于多文件并行修改场景。

---

### AO-3：编排参数可配置化

| 字段 | 内容 |
|------|------|
| **编号** | AO-3 |
| **名称** | 编排参数可配置化 |
| **所属维度** | 架构优化 |
| **优先级** | **中** |

#### 当前状态

`MAX_ORCHESTRATION_CYCLES = 8` 和 `MAX_PARALLEL_DELEGATED_TASKS = 4` 为源码硬编码常量（经验证精确匹配 `coordinator.py` 第 58-59 行），无法按任务类型或工作空间动态调整。

#### 对比价值

"一个常量适用所有场景"的僵化设计导致：简单咨询任务浪费资源（无需 8 轮循环），复杂重构任务能力不足（可能需要 16 轮/8 并行）。引入配置层后，可按任务复杂度弹性调配资源，无需改代码即可调优。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `PwC_Agentic_SDLC_2026.md` | #5 | Agentic SDLC 的全流程参数化思路 |
| `google/README.md` | #25 | Google ADK 的配置驱动架构 |

#### 实施建议

将编排约束参数移入配置层（如 `orchestration.json` 或角色定义的 frontmatter），支持按 session/run 级别覆盖。引入"编排策略"概念，允许不同任务复杂度匹配不同的循环上限和并行度。简单咨询任务可设为 1 轮/0 并行，大规模重构可设为 16 轮/8 并行。

---

### AO-4：同步/异步路径统一

| 字段 | 内容 |
|------|------|
| **编号** | AO-4 |
| **名称** | 同步/异步路径统一 |
| **所属维度** | 架构优化 |
| **优先级** | **中** |

#### 当前状态

`TaskOrchestrationService` 和各 Repository 大量存在 sync/async 方法对（如 `get()` / `get_async()`），维护成本翻倍且容易引入一致性 bug。

#### 对比价值

约 30-40% 的方法是冗余的 sync/async 双路径。统一为异步优先后，可消除一致性 bug 风险，简化新功能开发的心智负担，并为 Agentic SDLC 中"AI Agent 在最少人工干预下完成全流程"的设计哲学提供基础设施支撑。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `PwC_Agentic_SDLC_2026.md` | #5 | Agentic SDLC — 异步优先原则 |
| `google/README.md`, `google-cloud-next/index.md` | #24 | Google 全栈异步基础设施 |

#### 实施建议

确立"异步优先"架构原则：所有核心路径统一为 async，同步入口仅在 CLI 等必要边界通过 `asyncio.run()` 桥接。内部不再维护双路径。

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

项目明确采用"工具-only 协作"模式——Agent 之间不直接通信，仅通过任务委派工具（如 `orch_create_tasks`、`orch_dispatch_task`）间接交互。Coordinator 是唯一枢纽，所有信息必须经 Coordinator 中转。

#### 对比价值

当前架构中 Coordinator 是信息瓶颈——所有子 Agent 输出需经 Coordinator 汇总再转达。引入 A2A 协议层后，同级 Agent 可传递局部信息（如 Explorer 向 Designer 发送"文件结构发现"补充），降低 Coordinator 的上下文压力，提升局部协作效率。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `mas/00-INDEX.md` | #8 | Agent 协议栈 — MCP（Agent→工具）+ A2A（Agent→Agent）互补标准 |
| `mas/00-INDEX.md` | #8 | MCP SDK 月下载 97M+，150+ A2A 组织 |

#### 实施建议

引入 A2A（Agent-to-Agent）协议层作为现有 MCP 层的补充。维持 Coordinator 作为编排中心不变，但允许同级 Agent 之间传递局部信息。设计"轻量级 A2A 消息"机制——Agent 可发布结构化消息到 Run 级别的事件总线，其他 Agent 按需订阅。这不改变编排权威性，但避免了所有信息都必须经 Coordinator 中转的瓶颈。

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

Designer 角色产出技术规格（存为 tmp 文件），但规格在任务流转中没有形式化地位。`TaskEnvelope` 的 `verification` 字段仅是 `VerificationPlan`（checklist 字符串列表），缺乏结构化规格文档（经验证 `agents/tasks/models.py` 中确认无 `spec_document` 字段）。Crafter 执行时不一定参照 Designer 的规格输出。验证阶段（Gater）也不以 Designer 规格为验收依据。

#### 对比价值

这是解决当前"Designer 产出规格但后续环节无强制约束"这一根本断裂的关键改进。将验证从字符串匹配提升为规格合规校验，从根本上缓解 AI 编码 Agent 长任务退化问题。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `sdd/README.md` | #3 | Spec-Driven Development（Piskala 三级规格严格度框架） |
| `sdd/README.md` | #17 | AI 编码 Agent 退化（SlopCodeBench）—— 长周期任务质量退化 |
| `sdd/README.md` | #18 | SWE-AGI 规格+代码双评估基准 |

> **⚠ 验收修正**: 原报告将 SWE-AGI 归因到 #30（错误）。SWE-AGI 正确归属为研究点 #18（Benchmark 演进）。

#### 实施建议

在 `TaskEnvelope` 中增加 `spec_document` 字段（关联 Designer 输出的规格文件路径或内联内容），建立"规格→执行→验证"的闭环：(1) Designer 阶段产出的规格自动绑入后续 TaskEnvelope；(2) Crafter 的 system_prompt 中强制注入规格全文；(3) Gater 验收时以规格中的"验收标准（Definition of Done）"作为核心检查清单。参照 Piskala 的三级严格度：默认中等严格度（结构化规格 + 关键断言），简单任务可降为低严格度（自然语言描述），安全关键任务升为高严格度（形式化断言 + 自动化验证）。

#### 可行性评估

源码验证 `TaskEnvelope` 确实无 `spec_document` 字段，现状描述精确。**可行性：高**。Pydantic v2 模型扩展+流程串联，2-3 周可交付。风险点：旧 TaskRecord 需 Optional 兼容。

---

### SP-2：规格即合约（Code-as-Contract）

| 字段 | 内容 |
|------|------|
| **编号** | SP-2 |
| **名称** | 规格即合约（Code-as-Contract） |
| **所属维度** | Spec-Driven 流程 |
| **优先级** | **高** |

#### 当前状态

角色之间的协作规则分散在三个地方——角色的 system_prompt 禁区约束、RoleDefinition 的 tools/mcp 权限、以及 Coordinator 的分发描述。没有统一的行为合约（Behavioral Contract）将"角色能做什么、必须做什么、禁止做什么"形式化为可直接校验的契约。

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
| **优先级** | **中** |

#### 当前状态

Crafter 在执行复杂任务时依赖单一 LLM 会话上下文。虽然存在 `conversation_compaction.py`（经验证达 1138 行，压缩机制较完善），但压缩是被动触发、无感知规格的——可能压缩掉关键的规格约束信息。没有在执行过程中"刷新规格认知"的机制。

#### 对比价值

缓解长周期任务中 Agent 对初始规格的遗忘问题，提升复杂任务的首次完成率，为 SWE-bench 评估中的长尾失败案例提供改善路径。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `sdd/README.md` | #17 | AI 编码 Agent 长周期任务质量退化（SlopCodeBench 证据） |
| `Anthropic_Context_Engineering_Guide.md`, `harness/README.md` | #4 | Context Engineering — 上下文压缩与编辑策略 |

#### 实施建议

建立"Spec Checkpoint"机制——当 Crafter 的单一 Run 执行超过一定 Token 数或轮次时，系统自动注入规格摘要作为"认知刷新"。具体策略：(1) 每隔 N 轮工具调用，从绑定的 spec_document 提取关键约束项，以 system 消息方式重新注入；(2) 在上下文压缩时采用"规格优先保留"策略，确保 spec 相关的上下文片段最后被压缩。

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

安全机制分散且有限：工具审批（`runtime/approval`）仅作用于单个工具调用级别；角色禁区约束依赖 LLM 对 system_prompt 的遵从（无强制力）；人机审批门（Human Gate）需要显式开启且是全手动操作。没有统一的、贯穿任务全生命周期的运行时安全层。

#### 对比价值

从"依赖 LLM 自律"升级为"确定性安全门 + LLM 自律"的双重防护，为企业用户提供可审计的安全日志，降低越权操作风险。这是企业级部署的前提条件。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `harness/README.md` | #15 | Runtime Guardrails — AgentDoG 诊断框架、ILION 确定性预执行安全门、Proof-of-Guardrail |
| `Bengio_International_AI_Safety_Report_2026.md` | #12 | 国际 AI 安全报告的多维度安全框架 |

#### 实施建议

构建"三层运行时护栏"架构：(1) **预执行层**（ILION 风格）——在 LLM 调用工具前，以确定性规则检查意图是否在角色权限范围内，不依赖 LLM 判断；(2) **执行中监控层**（AgentDoG 风格）——对工具调用的参数和输出进行实时校验，标记异常模式（如超出预期频率的文件删除、异常大的写入等）；(3) **后验证层**（Proof-of-Guardrail 风格）——任务完成后自动生成"安全合规报告"作为 Gater 验收的必要输入。三层护栏的热点规则可配置，不同角色/任务类型加载不同规则集。

#### 可行性评估

**可行性：中高**。需先完成 AO-1 解构（拆解 TaskExecutionService），否则在 1869 行巨型文件中增加安全拦截层难度极高。现有 `tools/runtime/policy.py` 提供了 ToolApprovalPolicy 作基础，但需扩展为确定性拒绝模式。3-4 周需团队精通现有架构。

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

---

### SG-3：审计追踪增强

| 字段 | 内容 |
|------|------|
| **编号** | SG-3 |
| **名称** | 审计追踪增强 |
| **所属维度** | 安全与治理 |
| **优先级** | **中** |

#### 当前状态

存在 `trace/` 模块（Trace/Span 追踪）和 `metrics/`（指标平台），但聚焦于性能监控。缺少面向安全和合规的审计日志——如"哪个 Agent 在何时对哪些文件做了什么操作"的结构化记录。

#### 对比价值

满足企业级部署的合规审计要求；支持事后安全事件溯源；为"Done needs evidence"的质量纪律提供系统级支持。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `Bengio_International_AI_Safety_Report_2026.md` | #12 | 国际 AI 安全报告的透明性与问责机制 |
| `Stanford_HAI_AI_Index_2026.md` | #22 | 企业 Agent 部署的生产基础设施要求 |

#### 实施建议

在现有 trace 链路中增加"安全审计 Span"类型：自动记录所有文件写操作（路径+内容摘要+角色+任务 ID）、所有 shell 命令执行（命令+角色+上下文）、所有关键决策点（Coordinator 的通道选择理由）。审计日志独立存储，不可被 Agent 修改。提供 `/api/audit` 端点供外部合规系统查询。

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

存在 `conversation_compaction.py`（经验证达 1138 行，压缩机制较完善），但**没有 Prompt Caching（缓存）、Context Editing（编辑）策略**（`tools/runtime/` 目录下无缓存或编辑模块）。每次 LLM 调用都重新构建完整 system_prompt，即使角色定义和技能描述等静态内容在多次调用间不变。上下文管理缺少战略层级的设计。

#### 对比价值

减少 20-40% 的重复 Token 处理开销（尤其对标准通道的多角色编排）；上下文压缩不再丢失关键规格信息；为超长任务提供可持续的上下文管理能力。投资回报直观可量化。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `Anthropic_Context_Engineering_Guide.md` | #4 | Context Engineering — Context Windows、Compaction、Context Editing、Prompt Caching |
| `harness/README.md` | #4 | Context Engineering 类论文（6 篇） |

#### 实施建议

参照 Anthropic 指南构建三层上下文管理策略：(1) **缓存层**——将角色的 system_prompt、工具列表、技能描述等"稳态上下文"标记为可缓存，利用 LLM Provider 的 Prompt Caching 能力避免重复处理；(2) **编辑层**——当任务规格更新时，不重建完整上下文而是通过 Context Editing 只差量注入变更部分；(3) **压缩层**（existing 增强）——增强现有 compaction 为"规格感知压缩"，默认保留任务规格和验证标准。三层策略可按 `context_strategy` 配置项选择。

#### 可行性确认

源码验证：`conversation_compaction.py` 存在且 1138 行，`tools/runtime/` 无缓存或编辑模块。现状描述精确。

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

`TaskStatus.TIMEOUT` 状态已定义，但全景报告指出"未看到自动超时检测和处理的完整机制"。长时间运行的任务（如深度研究技能）可能无限阻塞编排循环。

#### 对比价值

消除编排循环中"任务永久阻塞"的隐患；提升系统整体鲁棒性；为 SWE-bench 中的长耗时任务（704.2s）提供合理的超时策略。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `feature_codex_shell_background_process.md` | 文件级 | Codex Shell 后台进程的生命周期管理 |
| `Agent_Harness_Engineering_Survey.md`, `harness/README.md` | #1 | Harness Engineering 的运行时安全 |

> **⚠ 验收修正**: 原报告引用 "#29 Codex Shell 后台进程"。但 #29 研究点是"AI+机器人融合"——原报告将文件编号 29 与研究点编号 29 混淆。实际参考来源应为文件 `feature_codex_shell_background_process.md`（研究点报告中文件序号 29），内容为 Codex Shell 后台进程。

#### 实施建议

完善超时自动处理：(1) 每个任务创建时绑定可配置的超时时长（默认值可按角色/任务类型设定）；(2) 使用独立的异步计时器监控任务状态，超时自动标记为 TIMEOUT 并通知 Coordinator；(3) Coordinator 收到 TIMEOUT 事件后决定重试（降级模型/简化任务）还是直接终止并报告用户。参考 Codex Shell 后台进程的设计——进程超时后需要优雅清理资源。

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

#### 实施建议

构建"Memory Bank"双层架构：(1) **工作记忆层**（Run-scoped）——当前 Run 的上下文，Run 结束后提取关键摘要；(2) **持久记忆层**（Project-scoped）——跨 Run 的结构化知识，包括"项目约束"（如"本项目使用 Pydantic v2，禁止 typing.Any"）、"决策记录"（如"选择 SQLite 而非 PostgreSQL 是因为单机部署需求"）、"失败模式"（如"Crafter 在处理 X 类型文件时经常失败"）。Memory Bank 通过 API 可查询，Crafter 在执行前自动检索相关的持久记忆。

---

### FE-2：AutoHarness 自动工具合成

| 字段 | 内容 |
|------|------|
| **编号** | FE-2 |
| **名称** | AutoHarness 自动工具合成 |
| **所属维度** | 功能增强 |
| **优先级** | **中** |

#### 当前状态

工具系统为手动注册制（`tools/registry/`），新增工具需要编写 Python 实现并注册到工具注册表。当 Crafter 遇到"现有工具无法完成"的需求时，只能退而求其次使用 shell 工具，丧失了结构化的输入/输出保障。

#### 对比价值

扩展 Crafter 的能力边界而不增加手动工具维护成本；将 shell 退化调用替换为结构化工具调用；参考 DeepMind 证明的"小模型合成 Harness > 大模型直接执行"范式。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `GoogleDeepMind_AutoHarness_2026.md` | #2 | AutoHarness（DeepMind）——使用小模型自动合成代码 Harness 以超越大模型表现 |

#### 实施建议

引入"运行时工具合成"能力——当 Crafter 判断现有工具集不足以完成任务时，可触发"工具合成请求"：系统使用较小的快速模型（参照 AutoHarness 思路）自动生成一个临时 Python 工具函数，自动包装为 MCP 工具并注册到当次 Run 的临时工具注册表。合成后的工具需通过自动化测试验证（输入/输出类型校验 + 沙箱执行安全检查）后才可供调用。

---

### FE-3：MCP + A2A 双协议栈完善

| 字段 | 内容 |
|------|------|
| **编号** | FE-3 |
| **名称** | MCP + A2A 双协议栈完善 |
| **所属维度** | 功能增强 |
| **优先级** | **高** |

#### 当前状态

MCP 集成已存在（`mcp/` 模块），但 A2A 协议尚未实现。`external_agents/` 模块实现的是 **ACP**（Agent Communication Protocol）而非 **A2A**（Google 提出的 Agent-to-Agent 开放协议）（经验证 `external_agents/acp_client.py` 包含 `AcpTransportClient` 等类）。MCP 覆盖了 Agent↔工具的连接，但 Agent↔Agent 的标准化通信路径缺失。

#### 对比价值

与行业标准对齐；支持跨框架 Agent 协作（与 LangGraph/CrewAI/AutoGen 生态互通）；为 relay-teams 成为"A2A 原生框架"提供差异化竞争力。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `mas/00-INDEX.md` | #8 | MCP（Agent→工具）+ A2A（Agent→Agent）互补标准成为行业共识 |

#### 实施建议

将 `external_agents/` 的 ACP 实现升级或并存支持 A2A 协议。A2A 层提供标准化的 Agent 发现、能力查询、任务委托接口，使得 relay-teams 的 Agent 可以与任何支持 A2A 的外部 Agent 互操作，同时内部 Agent 间的结构化通信也走 A2A 标准。MCP 保持为 Agent↔工具的协议。

#### 可行性确认

源码验证 `external_agents/acp_client.py` 确认使用 ACP 协议，ACP vs A2A 的区分正确。

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

`verify_task()` 仅做字符串匹配——检查 checklist 关键词是否在 result 中存在（经验证 `verification.py` 第 22-29 行：`for item in checklist: if key not in result`——确实是子串匹配）。"通过"的标准仅是 `non_empty_response`（结果非空），缺乏语义层面的验证能力。Gater 角色弥补了部分验证缺陷（零信任、证据驱动），但 `verify_task()` 本身的自动化验证能力极弱。

#### 对比价值

自动验证从"非空判断"升级为"多维度质量门"；减少 Gater 角色承担本可自动化的检查工作，让 Gater 专注于需要判断力的审查。直接提升任务交付质量，是 SP-1 的自然延伸。依赖 SP-1 完成后 spec_document 字段可用，验证逻辑可直接消费。

#### 参考来源

| 来源文件 | 研究点编号 | 研究点标题 |
|----------|-----------|-----------|
| `sdd/README.md` | #3 | SDD 的自动化验证 |
| `harness/README.md` | #6 | Agent 可靠性科学框架 |
| `sdd/README.md` | #18 | SWE-AGI 的规格+代码双评估 |

#### 实施建议

将验证引擎从字符串匹配升级为三级验证：(1) **结构验证**——检查输出是否满足预期的格式（如 JSON schema、文件存在性、关键字段非空）；(2) **行为验证**——对于代码修改任务，自动运行测试套件或 lint 检查；对于文档任务，检查格式合规性；(3) **规格合规验证**——利用 LLM 判断输出是否满足 spec_document 中定义的验收标准（Definition of Done）。三级验证的结果汇总为结构化 `VerificationReport`。

#### 可行性确认

源码验证 `verification.py` 仅 49 行，升级空间大。`for item in checklist: if key not in result` 确实是子串匹配。"字符串匹配"用词精确。**可行性：高**，2 周可交付。

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

### 1. AO-1：Harness 模式解构 TaskExecutionService（已完成基础拆分）

**完成情况**：2026-04-26 已完成基础 Harness 拆分，TaskExecutionService 不再直接承载 Prompt、工具快照、LLM guard、记忆和 runtime lane 的全部实现细节。

**后续收益**：为 SG-1 的安全拦截层、SP-1 的规格绑定、EP-1 的上下文策略和 AO-2 的 DAG 编排扫清主要结构障碍。

### 2. SP-1 + FE-5：形式化规格 + 智能验证（质量核心）

**为什么紧迫**：当前"Designer 产出规格但不强制约束"的断裂是最大的质量短板。验证引擎仅做字符串匹配，浪费了 Designer 的规格分析成果。

**预期收益**：从根本上缓解长任务退化问题，首次完成率预计提升 15-25%。

### 3. SG-2：角色行为边界强制执行（安全底线）

**为什么紧迫**：shell/write_tmp 的规避通道是当前安全架构的盲区。改动量可控但安全收益极高——将"角色坍塌"从偶发风险变为不可能事件。

**预期收益**：为企业级部署扫清安全障碍，是 SG-1 中最易快速见效的部分。

---

## 四阶段实施路线图

```
Phase 1 — 基础加固（4-6 周）
├── AO-1: TaskExecutionService Harness 解构 ← 已完成基础拆分（2026-04-26）
├── AO-3: 编排参数可配置化 ← 前置：无
├── EP-4: 任务超时自动处理 ← 前置：无
└── SG-2: 角色边界强制执行 ← 前置：无

Phase 2 — 质量与安全核心（6-8 周）
├── SP-1: 形式化规格嵌入 ← 依赖 AO-1 完成拆解后更易实现
├── FE-5: 验证引擎升级 ← 依赖 SP-1 的 spec_document 字段
├── SG-1: 三层运行时护栏 ← 依赖 SG-2 的强制执行机制
├── EP-1: Context Engineering ← 前置：无（可并行）
└── SP-2: RoleContract ← 依赖 SP-1 验证闭环建立后

Phase 3 — 编排与通信进化（6-8 周）
├── RP-1/A2A: Agent间直接通信 ← 前置：Phase 1-2 稳定
├── AO-2: DAG 编排引擎 ← 依赖 AO-1 解构 + RP-1 通信机制
├── FE-1: Memory Bank ← 前置：无（可并行）
├── FE-3: MCP + A2A 双协议栈 ← 前置：Phase 1-2 稳定
└── EP-2: Benchmark 体系 ← 依赖 SP-1/FE-5 的验证能力

Phase 4 — 差异化特性（按需启动）
├── FE-2: AutoHarness 工具合成
├── RP-2: Self-Evolving Agent
├── FE-6: 对比实验框架
├── RP-3: Swarming 模式
├── RP-4: Agent 能力分级标注
├── AO-4: 同步/异步路径统一
├── SP-3: Spec-Checkpoint 抗退化
├── SG-3: 审计追踪增强
├── SG-4: AI 风险评估框架
├── FE-4: 优先级调度与资源感知
└── EP-3: Agentic SDLC 全流程
```

---

## 优先级分布汇总

| 优先级 | 数量 | 编号 |
|--------|------|------|
| **高** | 11 | AO-1, AO-2, RP-1, SP-1, SP-2, SG-1, SG-2, EP-1, FE-1, FE-3, FE-5 |
| **中** | 10 | AO-3, AO-4, RP-2, SP-3, SG-3, SG-4, EP-2, EP-4, FE-2, FE-4 |
| **低** | 4 | RP-3, RP-4, EP-3, FE-6 |

---

## 总结

本研究通过对 2026 年 AI Agent 领域 38 篇前沿研究的系统分析，识别出 25 个与 relay-teams 产品高度相关的改进借鉴点。这些借鉴点分布在六大维度，覆盖了从底层架构到顶层功能的完整技术栈。

**核心发现**：

1. **架构层面**：TaskExecutionService 的"上帝类"问题是所有后续改进的最大瓶颈（AO-1），必须优先解决
2. **流程层面**：Spec-Driven Development 的形式化嵌入（SP-1）是提升任务交付质量的关键杠杆
3. **安全层面**：角色行为边界的强制执行（SG-2）和企业级运行时护栏（SG-1）是走向生产的必要条件
4. **工程层面**：全面的 Context Engineering 策略（EP-1）可直接带来 20-40% 的 Token 开销优化
5. **功能层面**：跨 Run 的 Memory Bank（FE-1）和 MCP+A2A 双协议栈（FE-3）构成核心差异化能力

四阶段路线图确保了依赖关系的正确性和实施的渐进性，预计 Phase 1-3 完成后（约 16-22 周），relay-teams 将在架构质量、安全保障、功能完备性三个维度实现显著跃升。

---

*本文件整合自 cross-reference-analysis.md（25 借鉴点）、markdown-research-points.md（35 研究点 + 38 源文件）、validation-report.md（验收修正）三份原始报告。所有 25 个借鉴点已逐一提取、交叉引用来源、标注验收修正。*
