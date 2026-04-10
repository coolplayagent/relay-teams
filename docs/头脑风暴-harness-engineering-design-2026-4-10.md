# Harness Engineering 研讨文档

## 1. 背景

Issue `#313` 提出，Agent Teams 需要从“工具 + skill 的集合”升级为真正可落地的 harness engineering 平台。

输入来源：

- Martin Fowler《Harness engineering for coding agent users》
- OpenAI《工程技术：在智能体优先的世界中利用 Codex》
- 仓内议题 `https://github.com/coolplayagent/relay-teams/issues/313`

Issue 中的核心判断可以归纳为：

1. Agent 的启动应当是事务驱动，而不是人工临时召唤。
2. Agent 需要明确责任边界、交付清单与承载它们的 IT 工具。
3. Todo 应具备首问负责制，但允许主动拉起其他角色讨论。
4. 每个角色都应是完整的 harness engineering agent，而不是一段 prompt。
5. 当前平台距离该目标仍有明显差距，现状更接近“skill 编排平台”。
6. 平台自身也是 harness 的一部分，而不仅仅是模型调用容器。
7. 代码检视这类角色，其任务来源、回复闭环、审计链路都应产品化。
8. 中间过程必须可审计、可观测，才能降低组织信任成本。
9. Harness 需要固化角色所需的 tools、prompt、skills，而不是运行时临时拼装。

这份文档的目标，是把上述观点扩展成一份面向 Agent Teams 的完整设计讨论稿，并结合当前仓库能力形成一份可执行的演进蓝图。

## 2. 什么是 Harness Engineering

在本文语境里，Harness Engineering 不是“给模型再加一点上下文”，而是围绕智能体构建一整套可约束、可反馈、可审计、可复用的工程控制系统。

可用一个简式定义：

`Agent = Model + Harness`

其中 harness 不只是 system prompt，还包括：

- 角色定义
- 任务契约
- 工具边界
- 技能加载机制
- 外部系统接入
- 审批与人机闸门
- 日志、事件、指标与回放
- 持续反馈与自我修正回路
- 代码库中的文档、规范、脚本、测试、结构约束

对于 Agent Teams，这意味着平台不能只做“把一个 prompt 发给模型”，而要做“把角色、任务、环境、约束、审计、反馈全部封装成可运行系统”。

## 3. 外部观点提炼

## 3.1 Fowler 的关键观点

Fowler 将 harness 分为两类控制：

- feedforward：事前引导
- feedback：事后反馈

同时又分为两类执行方式：

- computational：确定性、低成本、可频繁运行
- inferential：语义型、模型驱动、成本较高

落到编码智能体场景，核心不是“让 agent 更聪明”，而是：

- 提高首次做对的概率
- 让 agent 在错误暴露后可自我修正
- 让人的精力集中到真正高价值判断上

对 Agent Teams 的直接启发：

1. skill、prompt、docs 只是 feedforward 的一部分。
2. lint、typecheck、tests、结构校验、代码 review agent、运行时观测都属于 feedback。
3. 平台要同时支持 deterministic controls 和 inferential controls。
4. “角色”应该是一个被 harness 包裹的工作单元，而不是裸模型。

## 3.2 OpenAI 的关键观点

OpenAI 在 Codex 实践里更强调：

- 人类掌舵，智能体执行
- 工程师的主要工作转向环境、反馈回路、控制系统设计
- 仓库要成为系统 of record
- AGENTS.md 应该是目录，不应变成大杂烩手册
- 结构化 docs、exec plan、质量规则、可观测性接口都必须被 agent 直接读取和利用
- 规模上去以后，审查、清理漂移、垃圾回收都要 agent 化

对 Agent Teams 的启发是：

1. 平台需要承载 docs-first、repo-as-record 的工作方式。
2. 长期要支持“持续运行的治理 agent”，而不只是对话型 agent。
3. 审查、修复、回复、归档这些动作都应成为系统化任务流。
4. 高吞吐下，平台必须把信任来源从“人盯人”迁移到“机制 + 审计 + 回路”。

## 4. Agent Teams 当前能力盘点

从现有代码看，Agent Teams 已经具备一部分 harness 基础设施，但还没有形成统一产品心智。

## 4.1 已有基础能力

### 4.1.1 角色、任务、协调执行

系统已经有角色、任务、协调器、任务编排与执行基础设施：

- 任务模型与仓储：`src/relay_teams/agents/tasks/models.py:17`、`src/relay_teams/agents/tasks/task_repository.py:14`
- 协调器：`src/relay_teams/agents/orchestration/coordinator.py:64`
- 任务编排服务：`src/relay_teams/agents/orchestration/task_orchestration_service.py:46`
- 任务执行服务：`src/relay_teams/agents/orchestration/task_execution_service.py:95`

这说明平台已经具备“角色执行任务”的主骨架，不是纯单轮聊天系统。

### 4.1.2 角色能力边界

角色配置已包含：

- tools
n- mcp_servers
- skills
- model_profile
- system_prompt
- memory_profile
- bound_agent_id

相关定义可见：

- `src/relay_teams/roles/role_models.py:28`
- `src/relay_teams/roles/settings_service.py:205`
- `src/relay_teams/providers/provider_factory.py:98`

这意味着“角色能力边界”在数据结构层面已经存在。

### 4.1.3 Prompt / Skill / Tool 组合能力

当前平台已支持：

- 运行时 prompt 构建：`src/relay_teams/agents/execution/system_prompts.py:247`
- prompt 预览：`src/relay_teams/interfaces/server/routers/prompts.py:72`
- skill routing：`src/relay_teams/skills/skill_routing_service.py:222`
- load_skill 工具化使用：`src/relay_teams/agents/execution/system_prompts.py:54`

这说明平台已能动态把 role、context、skill 暴露给模型，但主要仍偏“提示工程 + 技能调度”。

### 4.1.4 审批、暂停、恢复

平台已经具备比较扎实的工具审批设施：

- 审批状态机：`src/relay_teams/tools/runtime/approval_state.py:32`
- 审批票据持久化：`src/relay_teams/tools/runtime/approval_ticket_repo.py:78`
- 工具执行审批封装：`src/relay_teams/tools/runtime/execution.py:58`
- run 级审批解析：`src/relay_teams/sessions/runs/run_manager.py:1751`

这是一种很典型的 harness control：危险动作不会直接执行，而是进入受控状态。

### 4.1.5 可审计事件与运行态

系统已经有 append-only event log、run state、session timeline：

- 业务事件日志：`src/relay_teams/sessions/runs/event_log.py:20`
- run state：`src/relay_teams/sessions/runs/run_state_repo.py:83`
- session rounds projection：`src/relay_teams/sessions/session_rounds_projection.py:210`
- message repository：`src/relay_teams/agents/execution/message_repository.py:335`

这为“全过程审计”提供了很好的底座。

### 4.1.6 可观测性

平台已经有 observability API 与指标查询：

- 路由：`src/relay_teams/interfaces/server/routers/observability.py:8`
- 数据模型：`src/relay_teams/metrics/models.py:169`
- 查询服务：`src/relay_teams/metrics/query_service.py:48`
- CLI：`src/relay_teams/interfaces/cli/metrics_cli.py:34`

这意味着 Agent Teams 不只是“记日志”，已经开始具备 KPI、趋势与 breakdown 的产品形态。

### 4.1.7 自动化 / 事务驱动入口

系统已经有 automation project，可以通过计划任务或绑定会话触发执行：

- 自动化项目模型：`src/relay_teams/automation/automation_models.py:93`
- 自动化服务：`src/relay_teams/automation/automation_service.py:74`
- 调度器：`src/relay_teams/automation/scheduler_service.py:45`
- 会话绑定队列：`src/relay_teams/automation/automation_bound_session_queue_service.py:117`

这已经非常接近 issue 中“事务驱动启动 agent”的方向。

## 4.2 当前缺口

尽管基础设施不少，但离 harness engineering agent 仍有几个明显缺口。

### 4.2.1 角色还是“能力包”，不是“责任单元”

现有 role 更像：

- 一组 prompt
- 一组 tools / skills / mcp
- 一个模型配置

但还不完全像：

- 有明确职责声明
- 有固定输入源
- 有标准交付件
- 有完成定义
- 有治理规则
- 有专属反馈回路

也就是说，现在 role 更偏执行能力定义，而不是工作产品定义。

### 4.2.2 任务契约还不够产品化

当前 task 已有 title、description、verification 等字段，但还没有形成统一的“可审计工作单”规范，例如：

- 任务来源系统
- 业务主键
- SLA
- 必须回复对象
- 交付物类型
- 回写目标
- 失败升级路径
- 责任角色

这会导致 agent 任务在系统内可跑，但和真实业务事务的绑定还不够强。

### 4.2.3 缺少“角色级 harness 模板”

当前 skills 是通用能力组件，roles 是配置组合，但还缺一层：

- 针对某类角色的标准 harness 模板

例如：

- 代码检视 agent 模板
- 缺陷分诊 agent 模板
- 发布值班 agent 模板
- 文档治理 agent 模板

模板里应该固化：

- 入口事件
- 必备工具
- 必备 skills
- 审批策略
- 交付规范
- 审计字段
- 反馈回路
- 指标口径

### 4.2.4 反馈回路仍偏“调用后检查”，而非“闭环治理”

当前已有 lint、审批、重试、事件流，但还缺以下闭环：

- 失败归因归档
- 重复问题自动转规则
- review comment 自动回复与追踪
- 漂移巡检 agent 常驻运行
- role 质量评分回灌

也就是平台有局部控制点，但还缺“持续 harness 运营层”。

### 4.2.5 外部事务源接入不够面向角色职责

Issue 里提到一个很具体的例子：

- 代码检视 agent 的 Todo 来源于 CodeHub 上的 MR
- 交付件是 review comment 与回复

而当前 Agent Teams 虽然有 gateway、automation、IM 接入，但在“角色 -> 外部事务源 -> 交付件回写”这一链路上还没有统一抽象。

## 5. 面向 Agent Teams 的 Harness 定义

针对本项目，建议将 harness 定义为以下五层。

## 5.1 L0 模型层

只负责推理，不承担业务身份。

内容包括：

- model profile
- provider
- transport
- retry / timeout

现有实现主要分布在：

- `src/relay_teams/providers/`
- `src/relay_teams/external_agents/provider.py:105`

## 5.2 L1 角色层

定义“谁来做这类事”。

应包含：

- 角色目标
- 责任边界
- 禁止事项
- 可用能力
- 交付物定义
- 审批策略
- 记忆策略

当前已有部分能力，但需要增强为“责任型角色”。

## 5.3 L2 任务层

定义“这次要做什么”。

应包含：

- 来源事件
- 目标对象
- 任务契约
- 验收标准
- 回写位置
- 期限与优先级
- 人工升级规则

当前 task 模型能承载一部分，但还需要增加业务事务语义。

## 5.4 L3 控制层

定义“如何确保过程可控”。

应包含：

- prompt guidance
- docs / plans / refs
- tool approval
- lint / typecheck / test / structure check
- review agent
- fallback / retry / timeout
- human gate

这是 harness engineering 的核心增益区。

## 5.5 L4 观测与治理层

定义“如何让组织信任这个系统”。

应包含：

- event log
- timeline
- prompt preview
- 审批轨迹
- KPI 与 breakdown
- 失败归因
- 漂移治理
- 模板版本与效果评估

## 6. 目标状态：每个角色都是一个完整 Harness Agent

建议把“角色”升级为以下产品对象：

### 6.1 角色定义

每个角色除了 prompt，还应明确：

- `responsibility_statement`：负责什么
- `owned_event_sources`：从哪些系统接任务
- `deliverable_contracts`：交付什么
- `feedback_controls`：必须经过哪些检查
- `handoff_policy`：何时拉起其他角色
- `escalation_policy`：何时升级给人
- `audit_requirements`：必须记录哪些过程字段

### 6.2 角色运行包

一个角色的 harness package 应由以下内容组成：

- role document
- required tools
- required skills
- allowed MCP servers
- task contract templates
- checklists
- review / reply templates
- observability metric spec
- failure playbook

当前 RoleDefinition 只承载了其中一部分，后续应继续结构化。

## 7. 事务驱动模型

Issue 的第一条很关键：Agent 启动时机应当是事务驱动。

## 7.1 统一 Trigger -> WorkItem -> Task 模型

建议新增统一抽象：

- Trigger：外部事件
- WorkItem：可追踪业务事务
- Task：平台内可执行任务

其中：

- Trigger 是原始输入，如 MR opened、comment added、定时扫描、IM 消息
- WorkItem 是业务对象，如 `code_review:mr_12345`
- Task 是本次执行单元

这样一个 MR 可以在系统里长期存在为 WorkItem，期间会派生多个 Task：

- 初次 review
- 对 reviewer reply
- 二次复检
- 合并前最终检查

## 7.2 为什么需要 WorkItem 层

因为单纯使用 run / task 难以表达：

- 同一事务的多次触发
- 多角色围绕同一事务协作
- 外部系统对象的持续状态追踪
- 长生命周期的责任归属

所以建议把“任务”之上再补一层“事务工作单”。

## 8. 首问负责制与协作机制

Issue 提到 Todo 需要首问负责制，同时允许主动拉起角色讨论。这一点非常适合 Agent Teams 当前 coordinator + task orchestration 的方向。

## 8.1 建议的责任规则

当一个 WorkItem 被路由到角色 A：

- 角色 A 成为 owner role
- owner role 对最终交付负责
- owner role 可以派生子任务给其他角色
- 其他角色返回结果，但不改变 owner 责任归属
- 对外回写应默认以 owner role 的上下文发出

这会让系统具备清晰的责任链，而不是“谁最后说话谁负责”。

## 8.2 主动拉起角色讨论的机制

这里的“讨论”不应等同于把任务转交出去，而应被建模为 owner role 发起的一次受控协作。

建议规则如下：

- 只有 owner role 可以发起围绕当前 WorkItem 的正式讨论
- 讨论必须带着明确议题，而不是笼统求助
- 被拉起的角色只对自己的分析结论负责，不接管 WorkItem owner
- 讨论输出必须沉淀为可引用结论，不能只停留在消息往返
- owner role 必须显式采纳、驳回或继续追问讨论结论
- 最终对外交付仍由 owner role 汇总并回写

建议把这类协作抽象成 `Discussion Thread` 或 `Consultation Task`，而不是普通自由对话。其最小字段应包括：

- `work_item_id`
- `owner_role_id`
- `consulted_role_id`
- `question`
- `expected_output`
- `returned_findings`
- `owner_decision`
- `decision_rationale`

这样可以把“主动拉人讨论”从隐式 prompt 行为提升为可审计的产品行为。

## 8.3 何时应该发起讨论

并非所有任务都需要多角色讨论。建议只有在以下场景才触发：

- owner role 缺少关键领域知识
- 任务需要跨边界判断，例如架构、性能、安全、合规
- 外部事务已进入争议状态，需要第二视角
- 当前反馈信号互相冲突，owner role 无法直接收敛
- 预计对外交付具有高风险，需要会签式判断

相反，下列场景不应滥用讨论：

- owner role 本可直接完成，只是想规避责任
- 为了“显得谨慎”而机械拉齐多个角色
- 没有形成具体问题，仅泛化地让别人“帮忙看看”

## 8.4 讨论闭环与审计

如果平台要真正体现“首问责任制 + 主动拉起角色讨论”，则必须把讨论过程纳入审计链：

- 谁发起了讨论
- 为什么发起
- 拉起了哪些角色
- 每个角色返回了什么判断
- owner role 最终采纳了什么
- 未采纳部分的原因是什么
- 最终交付与讨论结论的关系是什么

这部分能力可以落在现有 task / event log / message timeline 之上，但需要新增面向讨论的领域事件，例如：

- `work_item_consultation_requested`
- `work_item_consultation_answered`
- `work_item_consultation_adopted`
- `work_item_consultation_rejected`

## 8.5 与现有 Coordinator 的关系

当前 coordinator 更像执行时调度器。未来可以区分两种角色：

- coordination runtime：平台内部调度逻辑
- owner role：业务责任角色

也就是说，协调器不等于业务 owner。它负责分发和收敛，但责任可属于某个普通业务角色。

## 9. 代码检视 Agent 示例

Issue 给出的代码检视 agent 是一个很好的锚点。

## 9.1 目标定义

代码检视 agent 不是“帮我看下代码”，而是：

- 面向 MR 事务运行
- 读取 diff、上下文、评论线程、规则库
- 产出结构化 review comments
- 跟踪作者回复
- 对自己评论进行确认、澄清、关闭或升级

## 9.2 输入源

事务输入可包括：

- MR opened
- 新 commit pushed
- 人类 reviewer comment
- 作者 reply / resolve
- CI failed / recovered
- merge blocked

## 9.3 交付件

至少应支持：

- review comments
- line comments
- general summary
- comment reply
- review verdict
- unresolved issues summary

## 9.4 反馈控制

代码检视 agent 的 harness 应包含：

- diff retrieval
- repo docs / architecture docs
- lint / typecheck / tests 结果读取
- 历史 review thread 检索
- 评论去重
- 误报回收
- reply SLA 跟踪

## 9.5 平台需要补什么

为了支持该角色，平台还缺：

- 外部 code review provider 统一接口
- review comment / reply 的 Tool 抽象
- WorkItem 持久化
- “评论已发送 / 已回复 / 已关闭”状态机
- review 效果指标

## 10. 平台自身是 Harness 的一部分

Issue 中“平台本身是 harness 的一部分”这一点需要明确写入设计原则。

Agent Teams 不应把自己定位成：

- 模型转发器
- prompt playground
- skill launcher

而应定位成：

- 可治理的 agent runtime
- 可审计的事务执行系统
- 可演进的 harness operating system

这意味着平台层至少要负责：

- 身份与角色
- 任务与事务
- 权限与审批
- 运行与恢复
- 日志与指标
- 资料与技能
- 版本化与模板化

## 11. Agent Teams 的 Harness 参考架构

建议形成以下架构：

### 11.1 Event Ingress

来源：

- IM
- code review system
- scheduler
- webhook
- MCP / ACP bridge

输出：

- Trigger
- WorkItem create / update

### 11.2 WorkItem Orchestrator

负责：

- 选择 owner role
- 生成 / 更新任务契约
- 决定是否复用已有会话
- 管理事务状态机

### 11.3 Role Runtime

负责：

- role prompt
- tool / skill / mcp capability boundary
- memory
- external agent binding
- execution policy

### 11.4 Control Plane

负责：

- approval
- policy
- lint / typecheck / tests
- quality gates
- human gate
- retry / timeout / recoverability

### 11.5 Governance Plane

负责：

- event log
- timeline
- observability
- evaluation
- drift detection
- template versioning

## 12. 设计原则

建议把以下原则作为 Agent Teams 的 harness 原则。

### 12.1 角色优先，模型次之

对用户和组织暴露的主概念应该是 role，而不是 provider / model。

### 12.2 事务优先，而非对话优先

对业务场景，session 只是载体，WorkItem 才是主对象。

### 12.3 交付物优先，而非文本优先

每个角色都应该定义自己交付的对象，而不是只输出一段自然语言。

### 12.4 控制内建，而非事后补丁

审批、验证、回写、审计、观测都应默认内建到运行流中。

### 12.5 审计默认开启

任何外部写操作、任何关键决策、任何自动恢复，都应可回溯。

### 12.6 可观测性是产品功能

不是给开发者看的后台指标，而是组织建立信任的核心界面。

## 13. 与当前代码能力的映射

## 13.1 可以复用的现有能力

### 13.1.1 事务驱动雏形

- automation projects：`src/relay_teams/automation/automation_service.py:74`
- gateway / IM inbound：`src/relay_teams/gateway/feishu/inbound_runtime.py:204`
- session project binding：`src/relay_teams/sessions/session_models.py:17`

这些已经说明平台不是只能手工发 prompt。

### 13.1.2 角色执行与委派

- coordinator：`src/relay_teams/agents/orchestration/coordinator.py:111`
- dispatch_task：`src/relay_teams/agents/orchestration/task_orchestration_service.py:162`
- temporary role：`src/relay_teams/tools/task_tools/create_temporary_role.py:76`

### 13.1.3 审计底座

- event log：`src/relay_teams/sessions/runs/event_log.py:20`
- message timeline：`src/relay_teams/sessions/session_service.py:568`
- prompt preview：`src/relay_teams/interfaces/server/routers/prompts.py:72`

### 13.1.4 可观测底座

- overview / breakdown：`src/relay_teams/metrics/query_service.py:48`
- observability API：`src/relay_teams/interfaces/server/routers/observability.py:8`

### 13.1.5 安全控制

- tool approval：`src/relay_teams/tools/runtime/execution.py:347`
- shell grant persistence：`src/relay_teams/tools/workspace_tools/shell_approval_repo.py:40`

## 13.2 需要新增的关键模型

建议新增如下领域对象：

- `WorkItem`
- `WorkItemEvent`
- `RoleHarnessTemplate`
- `DeliverableContract`
- `ExternalWorkProvider`
- `RoleOwnershipPolicy`
- `HarnessEvaluationRecord`

其中最优先是 `WorkItem` 与 `RoleHarnessTemplate`。

## 14. Role Harness Template 设计建议

## 14.1 模板目的

把一类角色的最佳实践固化下来，避免每次都靠人工配置 role。

## 14.2 模板内容

建议模板包含：

- metadata
- applicable event sources
- role prompt baseline
- required tools
- required skills
- optional tools / skills
- review checklist
- approval policy
- deliverable schema
- metrics spec
- fallback playbook

## 14.3 模板层级

可分为：

- topology template：面向系统类型
- role template：面向职责类型
- task template：面向事务类型

例如：

- `crud-service-reviewer`
- `frontend-bug-triage`
- `release-note-curator`
- `docs-gardener`

## 15. 文档、技能、工具的重新定位

## 15.1 docs 是记录系统

应明确鼓励：

- docs 承载长期稳定事实
- AGENTS.md 承载入口地图
- 临时计划要落地为 repo 工件
- prompt 不应长期保存业务事实

## 15.2 skills 是“可调用经验包”

skills 不应只是能力碎片，而应逐渐承载：

- how-to
- playbook
- review policy
- repair workflow
- external system usage instructions

## 15.3 tools 是“可执行约束接口”

tool 的价值不只是调用能力，而是：

- 把外部系统访问变成受控 API
- 把结果结构化
- 把写操作纳入审批与审计

## 16. 审计与可观测设计建议

## 16.1 必须回答的组织问题

一个 harness 平台必须让组织能回答：

- 这个 agent 为什么被启动？
- 它依据什么做出这个结论？
- 它调用了哪些工具？
- 哪些动作经过审批？
- 失败发生在哪一步？
- 最终是谁对外产生了什么交付？
- 这类任务最近的成功率如何？

## 16.2 观测维度建议

建议 observability 从“基础性能”扩展到“业务责任”：

- role success rate
- role rework rate
- work item closure time
- approval wait time
- external write count
- review comment acceptance rate
- false positive rate
- auto-recovery rate
- human escalation rate

## 16.3 审计最小字段集

对每个 WorkItem / Task / Deliverable 建议至少记录：

- source_provider
- source_kind
- external_object_id
- owner_role_id
- active_task_id
- tool_calls
- approval_tickets
- deliverable_type
- deliverable_target
- delivered_at
- reviewer_feedback
- final_status

## 17. 反馈回路与持续治理

Harness engineering 的重点不是一次性配置，而是持续演进。

## 17.1 问题到规则的闭环

当重复问题出现时，应支持把经验沉淀到：

- docs
- skills
- role template
- lint rule
- review checklist
- approval policy

## 17.2 Garbage Collection Agents

建议未来内建一类持续治理角色：

- docs drift detector
- dead skill detector
- role quality janitor
- prompt bloat detector
- stale work item sweeper

## 17.3 Harness 评估

平台最终需要支持评估 harness 本身，而不只是评估模型。

建议至少支持：

- 角色级成功率基线
- 模板版本对比
- 不同 feedback controls 的贡献度分析
- “无人工介入完成率”
- “误报导致返工率”

## 18. 演进路线建议

## Phase 1：统一心智与补齐事务对象

目标：从 skill 平台升级到事务型 agent 平台。

建议项：

1. 新增 `WorkItem` 抽象。
2. 定义 trigger -> work item -> task 映射。
3. 给 role 增加责任 / 交付 / 审计元数据。
4. 文档化 Role Harness Template 概念。
5. 把 observability 扩展到 role / work item 维度。

## Phase 2：打通外部事务闭环

目标：让典型角色对真实业务对象负责。

建议项：

1. 抽象 code review provider 接口。
2. 支持 review comment / reply / resolve 工具。
3. 将外部对象状态持久化到 WorkItem。
4. 支持 owner role 模型。
5. 支持交付物回写记录。

## Phase 3：模板化与持续治理

目标：让角色成为可复制的 harness 产品。

建议项：

1. 引入 role harness templates。
2. 支持模板版本化与效果对比。
3. 增加 janitor / drift governance agents。
4. 建立 harness evaluation 体系。

## 19. 对 Issue #313 十条观点的逐条落地

### 19.1 Agent 启动时机：事务驱动

结论：正确，应以 WorkItem 为中心推进。

### 19.2 Agent 有明确责任边界和交付清单

结论：正确，RoleDefinition 需要继续升级为责任型角色模型。

### 19.3 Todo 首问责任制，但可主动拉起其他角色

结论：正确，应引入 owner role 语义。

### 19.4 每个角色都是独立完整 harness engineering agent

结论：正确，role 不能只是一段 system prompt。

### 19.5 当前 IT 工具只是一堆 skill

结论：基本成立。当前已有任务、审批、事件、观测底座，但统一产品心智仍不足。

### 19.6 平台本身是 harness 的一部分

结论：完全正确，而且应上升为顶层设计原则。

### 19.7 代码检视 agent Todo 来源是 MR

结论：是最适合作为首个垂直场景的样板。

### 19.8 代码检视 agent 交付件是检视意见与回复

结论：需要平台支持 deliverable contract 与 external write-back。

### 19.9 中间过程全部可审计和可观测

结论：平台已有不错基础，应继续向 WorkItem / Deliverable 维度扩展。

### 19.10 Agent 固化 Harness 需要的 tool/prompt/skill

结论：应通过 Role Harness Template 落地，而非仅靠运行时临时配置。

## 20. 最终结论

Agent Teams 已经具备不少 harness engineering 的底座能力：

- 角色
- 任务
- 协调执行
- skills
- tools
- MCP / ACP
- 审批
- 事件日志
- 可观测性
- 自动化触发

但目前这些能力仍然偏“基础设施拼装态”，尚未上升为统一的 harness operating model。

下一阶段最关键的，不是继续增加零散 tool 或 skill，而是把平台抽象升级为：

- 以事务为中心
- 以责任角色为中心
- 以交付物为中心
- 以控制回路为中心
- 以审计与治理为中心

建议后续围绕以下三个关键词推进：

- `WorkItem`
- `Owner Role`
- `Role Harness Template`

这是 Agent Teams 从“会调用模型的 agent 平台”走向“可在组织里承担真实职责的 harness engineering 平台”的关键一步。
