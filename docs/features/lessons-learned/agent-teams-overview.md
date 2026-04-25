# relay-teams 项目全景报告

> 生成时间: 2026-04-25 | 基于 `/opt/workspace/agent-teams-main` 代码库探索

---

## 1. 项目基本信息

| 字段 | 描述 |
|------|------|
| **项目名称** | relay-teams |
| **包名** | `relay-teams` (PyPI) |
| **核心定位** | 角色驱动的多智能体编排框架 (Role-driven multi-agent orchestration framework) |
| **技术栈** | Python 3.12+ / Pydantic v2 / pydantic-ai / FastAPI / Typer / SQLite (aiosqlite) / OpenAI-compatible endpoints |
| **LLM 运行时** | `pydantic_ai` + OpenAI-compatible 端点，支持流式传输 |
| **前端** | `frontend/dist/` — 纯静态资产，由后端 FastAPI 服务提供 |
| **版本管理** | `src/relay_teams/_version.py`，通过 setuptools 动态版本 |
| **代码约束** | 强类型 (Pydantic v2, basedpyright)，不使用 `typing.Any` / `dataclass` / `os.path` |

### 依赖关系（核心）
- **pydantic** >= 2.7.0 — 所有领域模型基座
- **pydantic-ai** + **pydantic-ai-slim[fastmcp]** — LLM Agent 运行时
- **fastapi** + **uvicorn** — HTTP 服务器与 SSE 实时推送
- **typer** — CLI 框架
- **httpx** — HTTP 客户端
- **aiosqlite** — SQLite 异步持久化
- **lark-oapi** — 飞书集成
- **markitdown** — Office/PDF 文档解析

---

## 2. 架构概览

### 2.1 顶层目录结构

```
agent-teams-main/
├── src/
│   ├── relay_teams/          # 核心业务包 (34+ 子模块)
│   └── relay_teams_evals/    # SWE-bench 评估工具
├── frontend/                 # 前端静态资产 (dist/)
├── tests/
│   ├── unit_tests/           # 镜像 src/relay_teams/ 结构
│   └── integration_tests/    # api/ browser/ cli/ support/
├── docs/                     # 设计文档、评估报告、参考资料
├── docker/                   # Dockerfile + 评估入口
├── openspec/                 # OpenSpec 规范与变更管理
├── pyproject.toml            # 项目配置
├── CLAUDE.md / AGENTS.md     # AI Agent 开发规范
└── README.md
```

### 2.2 核心模块拓扑

```
relay_teams/
│
├── agents/                    # 智能体领域层
│   ├── execution/             #   LLM 会话执行引擎 (33 个子模块)
│   │   ├── session_runtime.py       # 会话运行时
│   │   ├── prompt_instructions.py   # Prompt 指令构建
│   │   ├── system_prompts.py        # 系统提示词组装
│   │   ├── subagent_runner.py       # 子Agent调用器
│   │   ├── conversation_compaction.py  # 上下文压缩
│   │   └── ...
│   ├── orchestration/         #   编排引擎 (14 个子模块)
│   │   ├── coordinator.py           # 核心：CoordinatorGraph
│   │   ├── meta_agent.py            # 意图调度层 MetaAgent
│   │   ├── task_execution_service.py # 任务执行服务 (1869行)
│   │   ├── task_orchestration_service.py # 任务编排服务
│   │   ├── verification.py          # 验证引擎
│   │   ├── human_gate.py            # 人机审批门
│   │   └── ...
│   ├── tasks/                 #   任务模型与仓库
│   │   ├── models.py          # TaskEnvelope / TaskRecord / VerificationPlan
│   │   ├── enums.py           # TaskStatus 生命周期枚举
│   │   ├── events.py          # 领域事件
│   │   └── task_repository.py
│   └── instances/             #   Agent 运行实例管理
│       ├── models.py          # AgentRuntimeRecord
│       └── instance_repository.py
│
├── roles/                     # 角色系统
│   ├── role_models.py         # RoleDefinition / RoleDocumentRecord
│   ├── role_registry.py       # 角色注册表
│   ├── runtime_role_resolver.py  # 运行时角色解析
│   ├── memory_*.py            # 角色记忆系统 (BM25/注入)
│   ├── temporary_role_*.py    # 动态临时角色
│   └── role_cli.py            # 角色 CLI
│
├── sessions/                  # 会话管理
│   ├── session_models.py      # Session / SessionMode
│   ├── session_service.py
│   └── runs/                  #   运行管理 (36 个子模块)
│       ├── run_service.py          # Run 生命周期
│       ├── run_models.py           # IntentInput / RunResult / RunEvent
│       ├── run_scheduler.py        # 运行调度
│       ├── event_stream.py         # SSE 事件流
│       ├── injection_queue.py      # 消息注入队列
│       ├── todo_service.py         # TODO 跟踪
│       └── ...
│
├── tools/                     # 工具系统
│   ├── registry/              #   工具注册与分组
│   ├── runtime/               #   运行时策略/审批/上下文
│   ├── orchestration_tools/   #   编排工具 (Task CRUD + Dispatch)
│   ├── task_tools/            #   任务操作工具
│   ├── workspace_tools/       #   工作空间文件操作
│   ├── computer_tools/        #   计算机使用工具
│   ├── web_tools/             #   网页抓取/搜索
│   ├── im_tools/              #   IM 集成工具
│   ├── office_tools/          #   Office 文档工具
│   └── ...
│
├── interfaces/                # 外部接口层
│   ├── server/                #   FastAPI 后端
│   │   ├── app.py
│   │   ├── container.py       # DI 容器
│   │   └── routers/           #   /api/* 路由 (17个端点)
│   ├── cli/                   #   Typer CLI
│   └── sdk/                   #   HTTP SDK 客户端
│
├── providers/                 # LLM Provider 适配层
│   ├── provider_contracts.py  #   LLMProvider 协议
│   ├── openai_compatible.py   #   OpenAI 兼容适配
│   ├── model_config.py        #   模型配置与能力
│   ├── model_fallback.py      #   模型回退链
│   └── ...
│
├── builtin/                   # 内置资源
│   ├── roles/                 #   内置角色定义 (.md文件)
│   ├── skills/                #   内置技能包
│   │   ├── deepresearch/      #     深度研究
│   │   ├── pptx-craft/        #     PPT 制作
│   │   ├── skill-installer/   #     技能安装器
│   │   └── time/              #     时间工具
│   └── config/                #   默认配置 (model/orchestration/notifications/prompts)
│
├── skills/                    # 技能管理框架
│   ├── skill_registry.py      #   技能注册
│   ├── discovery.py           #   技能发现
│   ├── clawhub_skill_service.py  # ClawHub 技能市场
│   ├── skill_routing_service.py  # 技能路由
│   └── skill_team_roles.py    # 技能角色生成
│
├── workspace/                 # 工作空间管理
│   ├── workspace_manager.py
│   ├── workspace_models.py
│   ├── git_worktree.py        # Git Worktree 支持
│   └── ssh_profile_*.py       # SSH 远程工作空间
│
├── mcp/                       # MCP (Model Context Protocol) 集成
├── hooks/                     # 运行时钩子系统
├── triggers/                  # GitHub Webhook 触发器
├── automation/                # 自动化调度 (飞书/小鲁班集成)
├── gateway/                   # 多渠道入口 (飞书/微信/小鲁班)
├── external_agents/           # 外部 ACP Agent 集成
├── persistence/               # 数据库层 (SQLite)
├── notifications/             # 通知系统
├── trace/                     # Trace/Span 追踪
├── metrics/                   # 指标平台
├── media/                     # 多媒体 (图像/音频/视频)
├── reminders/                 # 系统提醒引擎
├── retrieval/                 # 信息检索
├── secrets/                   # 密钥管理
├── computer/                  # 计算机使用执行面
├── monitors/                  # 运行时监控
├── validation/                # 验证工具
├── net/                       # 网络层
├── release/                   # 发布管理
├── commands/                  # 命令系统
├── frontend/                  # 前端资产服务
├── env/                       # 环境配置
├── paths/                     # 路径管理
└── logger/                    # 日志系统
```

### 2.3 组件关系图

```
用户意图 → [Gateway] → [MetaAgent]
                           │
                    ┌──────┴──────┐
                    │             │
              SessionMode:     SessionMode:
               NORMAL        ORCHESTRATION
                    │             │
              [MainAgent]   [CoordinatorGraph]
                    │             │
                    │    ┌────────┴──────────┐
                    │    │  _run_ai_mode()    │
                    │    │  编排循环 (max 8)   │
                    │    └────────┬──────────┘
                    │             │
                    │    ┌────────┴──────────────────┐
                    │    │  _run_pending_delegated_tasks()
                    │    │  并行调度 (max 4 lanes)     │
                    │    └──┬────────┬────────┬───────┘
                    │       │        │        │
                    │   [Explorer] [Designer] [Crafter]
                    │       │        │        │
                    │   ┌───┴────────┴────────┴───┐
                    │   │   verification.py        │
                    │   │   → Gater (验收)          │
                    │   └─────────────────────────┘
                    │
              [TaskExecutionService] ← LLM Provider
                    │
              [Tool Registry] → [所有工具]
```

---

## 3. 角色系统设计

### 3.1 角色定义模型

角色由 `RoleDefinition` (Pydantic v2 模型) 定义，核心字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `role_id` | RequiredIdentifierStr | 唯一标识符 |
| `name` | str | 显示名称 |
| `description` | str | 角色描述 |
| `version` | str | 版本号 |
| `tools` | tuple[str, ...] | 可用工具列表 (支持通配符 `*`) |
| `mcp_servers` | tuple[str, ...] | MCP 服务器列表 |
| `skills` | tuple[str, ...] | 技能引用列表 |
| `model_profile` | str | 模型配置档案名 |
| `execution_surface` | ExecutionSurface | 执行面类型 (API/Desktop/Browser) |
| `mode` | RoleMode | `primary` (主Agent) 或 `subagent` (子Agent) |
| `memory_profile` | MemoryProfile | 记忆配置 (BM25 检索) |
| `hooks` | HooksConfig | 钩子配置 |
| `system_prompt` | str | 系统提示词 |

角色定义文件格式为 **Markdown + YAML frontmatter**（如 `coordinator.md`），支持两种来源：
- `RoleConfigSource.BUILTIN` — 内置角色（随包发布）
- `RoleConfigSource.APP` — 用户自定义角色（运行时创建）

### 3.2 内置角色体系

| 角色 ID | 名称 | 模式 | 定位 | 工具权限 |
|---------|------|------|------|----------|
| **Coordinator** | Coordinator | primary | 元编排器，驱动任务生命周期，评估复杂度，选择执行路径 | 编排工具 (create_tasks, dispatch, list...) + 技能管理 |
| **MainAgent** | Main Agent | primary | 普通模式下直接执行用户请求的全栈 Agent | 文件编辑 + Shell + 子Agent + Web + 监控 + 所有 MCP |
| **Explorer** | Explorer | subagent | 代码空间探测员，高效导航搜索，只读不写 | grep, glob, read, office_read, write_tmp |
| **Designer** | Designer | subagent | 规格架构师，将模糊意图转化为技术规格 | grep, glob, read, office_read, write_tmp |
| **Crafter** | Crafter | subagent | 执行实现者，通过编程自动化完成任务 | 文件编辑 + Shell + Web + 监控 + 所有 MCP + 技能 |
| **Gater** | Gater | subagent | 质量审计员，零信任、证据驱动的验收 | grep, glob, read, office_read, write_tmp, shell, 监控 |
| **Daily-AI-Report** | Daily AI Report | (专用) | AI 日报生成 | (特定技能) |

#### 角色职责边界设计（防角色坍塌）

每个角色都有严格的"禁区"约束，防止越权行为：
- **Coordinator**: 不亲自编写代码/规格/审计，不预先宣布完成
- **Explorer**: 只读操作，不执行/编辑文件，不做推测性结论
- **Designer**: 只产出技术规格，不编写生产代码，不提供代码片段示例
- **Crafter**: 不可修改任务规格，交付前必须运行自动化工具
- **Gater**: 不编辑任何文件/不制定新计划，证据不全不给出 ACCEPTED

### 3.3 动态临时角色

系统支持运行时创建临时角色（`temporary_role_models.py` / `temporary_role_repository.py`）：
- 通过 `orch_create_temporary_role` 工具动态创建
- 优先使用 `template_role_id` 继承最接近的现有角色能力
- 临时角色服务于单一明确子任务
- 生命周期与 Run 绑定

### 3.4 角色协作机制

1. **Coordinator 评估意图**：根据复杂度选择执行通道（咨询/快速/标准）
2. **独立记忆隔离**：每个角色拥有独立的执行记录记忆，Crafter 不拥有 Explorer 的记忆
3. **信息链路传递**：大内容先存为文件，通过文件路径传递，避免上下文膨胀
4. **明确分发描述**：Coordinator 分发任务时禁止使用代词（"这个/那个"），必须写清上下文

---

## 4. 任务编排机制

### 4.1 双模式执行架构

系统支持两种会话模式（`SessionMode`）：

| 模式 | 说明 |
|------|------|
| **NORMAL** | 单Agent直接执行，主Agent (MainAgent) 直接处理用户意图 |
| **ORCHESTRATION** | 多Agent编排模式，Coordinator 驱动任务分解与委派 |

### 4.2 任务生命周期

任务状态枚举 (`TaskStatus`)：

```
CREATED → ASSIGNED → RUNNING → COMPLETED
                  ├── STOPPED (暂停)
                  ├── FAILED (失败)
                  └── TIMEOUT (超时)
```

任务核心模型：
- **TaskEnvelope** — 任务信封（不可变元数据）：task_id, session_id, parent_task_id, trace_id, role_id, objective, verification
- **TaskRecord** — 任务记录（含可变状态）：envelope + status + result + timestamps
- **VerificationPlan** — 验证计划：checklist 元组（如 `"non_empty_response"`）
- **VerificationResult** — 验证结果：passed + details

### 4.3 编排流程详解

#### 4.3.1 MetaAgent 意图调度

```python
class MetaAgent:
    async def handle_intent(intent, trace_id) -> RunResult
    async def resume_run(trace_id) -> RunResult
```
- 作为最外层入口，将用户意图转发至 CoordinatorGraph
- 支持 Run 恢复（断点续跑）

#### 4.3.2 CoordinatorGraph 核心编排

```python
class CoordinatorGraph:
    async def run(intent, trace_id)         # 启动新编排
    async def resume(trace_id)              # 恢复已暂停编排
```

**编排循环（`_run_ai_mode`）**：
1. 首先执行 Coordinator 第一轮 → LLM 调用编排工具创建子任务
2. 进入编排循环（最多 **8 轮**，`MAX_ORCHESTRATION_CYCLES`）：
   - `_run_pending_delegated_tasks()` — 并行执行待处理委派任务
   - 使用 `asyncio.Semaphore(4)` 限制并行度为 **4 条 lane**
   - 每轮执行后，Coordinator 汇总结果，可能创建新任务或宣告完成
3. 无待处理任务时自动退出循环

**编排预设**（`orchestration.json`）定义了动态路由策略：
- **咨询通道**：纯问答 → Coordinator 直接答复或调度 Crafter 获取信息
- **快速通道**：简单任务 → Crafter(执行) → Gater(验收)
- **标准通道**：复杂任务 → Designer(规格) → Crafter(执行) → Gater(验收)

#### 4.3.3 TaskOrchestrationService 细粒度控制

```python
class TaskOrchestrationService:
    async def create_tasks(run_id, tasks)       # 批量创建子任务
    async def update_task(run_id, task_id, update)  # 更新任务信息
    async def dispatch_task(run_id, task_id, role_id, prompt)  # 分发执行
    async def list_delegated_tasks(run_id)       # 列出委派任务
```

关键设计点：
- **实例复用**：优先复用已有 session-role 实例（REUSABLE lifecycle）
- **临时克隆**：当实例被阻塞任务占用时，创建 Ephemeral 克隆（EPHEMERAL lifecycle）
- **角色分配锁**：使用 async Lock 防止同一 session+role 的并发分配竞争
- **并行信号量**：每个 run 独立的 Semaphore(4) 控制并行度
- **Hook 集成**：任务创建时触发 `TASK_CREATED` 钩子

#### 4.3.4 验证机制

`verify_task()` 在任务完成后执行：
- 检查任务状态是否为 COMPLETED
- 逐一验证 `VerificationPlan.checklist` 中的验证项
- 默认检查 `non_empty_response`（结果非空）
- 发布 `VERIFICATION_PASSED` 或 `VERIFICATION_FAILED` 事件

#### 4.3.5 人机审批门 (Human Gate)

`GateManager` 提供人工介入机制：
- `open_gate()` — 开启审批门，阻塞等待
- `resolve_gate()` — 外部调用审批（approve/revise + feedback）
- `wait_for_gate()` — 阻塞直到审批完成

### 4.4 Agent 实例管理

**实例生命周期**：
```
InstanceLifecycle: REUSABLE | EPHEMERAL
InstanceStatus: IDLE | RUNNING | PAUSED | STOPPED | COMPLETED
```

**实例仓库** (`AgentInstanceRepository`)：
- 按 session+role 查找实例（复用策略）
- 按 run 列出所有实例
- 支持实例状态标记

### 4.5 任务领域事件

事件类型 (`EventType`)：
- `TASK_CREATED`, `TASK_ASSIGNED`, `TASK_COMPLETED`, `TASK_FAILED`
- `INSTANCE_CREATED`
- `VERIFICATION_PASSED`, `VERIFICATION_FAILED`

事件通过 `EventLog` 发布，被事件总线 (`RunEventHub`) 消费并转为 `RunEvent` 推送到 SSE。

---

## 5. 现有功能清单与特点

### 5.1 核心功能

| 功能 | 说明 |
|------|------|
| **多Agent编排** | Coordinator → Sub-Agent 委派执行，支持最多 8 轮循环、4 路并行 |
| **双会话模式** | NORMAL (单Agent) / ORCHESTRATION (多Agent编排)，按需切换 |
| **角色定义与管理** | YAML+Markdown 角色定义，支持内置/自定义/临时角色 |
| **任务全生命周期** | CR→AS→RU→CO/FA/TI 状态机，含验证、恢复、重试 |
| **工具注册体系** | 按组注册，支持工具审批、运行时策略 |
| **技能系统** | 技能发现、注册、路由、市场安装 (ClawHub) |
| **MCP 集成** | Model Context Protocol 支持，FastMCP 集成 |
| **多 Provider 支持** | OpenAI-compatible 适配、模型回退链、连接重试 |
| **Web UI** | FastAPI 服务 + 前端，支持中英文切换 |
| **CLI** | 完整的 Typer 命令行界面 |
| **HTTP SDK** | 独立的 SDK 包用于 API 调用 |
| **SSE 实时推送** | Run 事件流，前端/SDK 实时接收 |
| **运行时钩子** | Hook 系统：匹配/执行/事件集成 |
| **GitHub Webhook** | 触发器系统，支持 localhost.run 临时公共 URL |
| **自动化调度** | 定时触发执行，队列管理 |
| **多渠道入口** | 飞书/微信/小鲁班 IM 集成 |
| **外部 Agent** | ACP 协议集成外部智能体 |
| **工作空间管理** | 本地/SSH/Git Worktree 多工作空间 |
| **角色记忆** | BM25 检索的角色长期记忆 |
| **多媒体支持** | 图像/音频/视频生成与处理 |
| **上下文压缩** | 对话上下文微压缩 (microcompact) |
| **模型回退** | 多模型回退链 + 自动切换 |
| **Token 用量追踪** | 每次运行的 Token 统计 |
| **Trace/Span** | 运行追踪链路 |
| **人机审批** | Gate 机制支持人工介入关键决策 |
| **消息注入** | 运行中注入消息（系统/用户/SubAgent） |

### 5.2 内置技能包

| 技能 | 说明 |
|------|------|
| **deepresearch** | 深度研究技能 |
| **pptx-craft** | PPT 制作技能 (含 SVG-to-PPTX 转换) |
| **skill-installer** | 技能安装管理器 |
| **time** | 时间工具 |

### 5.3 技术特点

1. **严格类型安全**：全部使用 Pydantic v2 模型，禁止 `typing.Any` / `hasattr` / `# type: ignore`，使用 basedpyright 检查
2. **事件驱动架构**：EventLog + EventHub + SSE 的事件流管道
3. **工具-only 协作**：Agent 之间不直接通信，仅通过任务委派工具交互
4. **一致性验证**：每个任务都有 VerificationPlan，Gater 做最终零信任验收
5. **防角色坍塌**：每个角色定义了严格的职责边界禁区
6. **可恢复执行**：Run 暂停/恢复机制，断点续跑
7. **模型灵活性**：支持任意 OpenAI-compatible 端点，可配置多个模型档案
8. **持久化安全降级**：运行时对脏数据容忍（unknown tools/mcp/skills 降级为警告而非崩溃）

### 5.4 SWE-bench 评估结果

| 模式 | 基准 | 通过率 | 通过/失败 | 平均耗时 | Token 输入 |
|------|------|--------|-----------|----------|-----------|
| Normal | SWE-bench Verified 100 | 72.0% | 72/28 | 369.2s | 60M |
| Orchestration | SWE-bench Verified 100 | 73.0% | 73/27 | 704.2s | 103M |

---

## 6. 项目代码规模

基于目录探索的估算：

| 模块 | 文件数 (估计) | 说明 |
|------|--------------|------|
| `agents/` | ~55 | 执行引擎 + 编排 + 任务 + 实例 |
| `sessions/` | ~40 | 会话管理 + Run 管理 |
| `roles/` | ~14 | 角色定义与管理 |
| `tools/` | ~50+ | 工具注册 + 各类工具实现 |
| `interfaces/server/` | ~20 | FastAPI 路由 + DI |
| `providers/` | ~22 | LLM Provider 适配 |
| `builtin/` | ~20 | 角色 + 技能 + 配置 |
| `skills/` | ~15 | 技能管理框架 |
| `workspace/` | ~15 | 工作空间 |
| 其他模块 | ~60+ | hooks/mcp/automation/gateway/... |
| **总计** | **~300+ Python 源文件** | 不含测试 |

---

## 7. 已有的可改进空间

基于代码探索发现的客观事实：

### 7.1 架构层面

1. **TaskExecutionService 体量过大**：`task_execution_service.py` 达 1869 行，承担了 Prompt 构建、消息持久化、工具执行、LLM 调用、Hook 集成、子Agent运行等过多职责，虽然内部分了多个方法但单一文件维护成本高

2. **同步/异步双路径并存**：`TaskOrchestrationService` 和各 Repository 大量存在 sync/async 方法对（如 `get()` / `get_async()`, `create()` / `create_async()`），增加了维护开销

3. **编排循环硬编码**：`MAX_ORCHESTRATION_CYCLES = 8` 和 `MAX_PARALLEL_DELEGATED_TASKS = 4` 为固定常量，不可通过配置调整

### 7.2 编排能力

4. **验证机制简单**：`verify_task()` 仅做字符串匹配（检查 checklist 关键词是否在 result 中），而非真正的语义验证或结构校验

5. **缺乏跨 Run 记忆**：Run 之间没有显式的知识传递机制（虽然角色有 BM25 记忆，但运行级上下文不跨 Run）

6. **缺少优先级调度**：任务分发无优先级排序，仅按创建顺序处理

### 7.3 工程质量

7. **测试覆盖不透明**：无法从当前探索判断测试覆盖率，`tests/unit_tests` 镜像了 `src/` 结构，但需要实际运行才能确认

8. **前端耦合**：前端资产直接放在 `frontend/dist/`，无独立构建流程描述，与后端单仓耦合

9. **文档分散**：`docs/` 下有 44 个条目（设计文档 + 评估报告 + 参考资料），文档结构较散乱，无统一的文档站点

### 7.4 功能缺口

10. **缺少任务超时自动处理**：`TaskStatus.TIMEOUT` 状态存在但未看到自动超时检测和处理的完整机制

11. **Hook 系统文档不足**：Hook 的事件模型、匹配规则、执行器类型缺少面向使用者的文档

12. **外部 Agent 集成有限**：`external_agents/` 模块存在但 ACP 协议的实现深度需要进一步评估

---

## 8. API 路由清单

基于 `interfaces/server/routers/` 目录：

| 路由模块 | 推测路径 | 功能 |
|---------|---------|------|
| `sessions.py` | `/api/sessions` | 会话 CRUD |
| `runs.py` | `/api/runs` | 运行管理 + SSE |
| `prompts.py` | `/api/prompts` | Prompt 发送 |
| `tasks.py` | `/api/tasks` | 任务操作 |
| `roles.py` | `/api/roles` | 角色管理 |
| `workspaces.py` | `/api/workspaces` | 工作空间管理 |
| `commands.py` | `/api/commands` | 命令系统 |
| `mcp.py` | `/api/mcp` | MCP 管理 |
| `triggers.py` | `/api/triggers` | 触发器管理 |
| `automation.py` | `/api/automation` | 自动化配置 |
| `gateway.py` | `/api/gateway` | 网关入口 |
| `logs.py` | `/api/logs` | 日志查看 |
| `observability.py` | `/api/observability` | 可观测性 |
| `system.py` | `/api/system` | 系统信息 |
| `session_media.py` | `/api/sessions/.../media` | 会话多媒体 |
| `feishu_gateway.py` | (webhook) | 飞书事件回调 |

---

*报告完成。以上内容均基于代码库实际探索的事实证据整理。*
