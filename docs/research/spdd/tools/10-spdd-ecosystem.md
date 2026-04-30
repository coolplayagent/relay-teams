# SPDD/SDD 生态系统全景：工具、框架、方法论对照

> 综合整理 | 2026-04-29

## 一、方法论与框架对照

| 名称 | 类型 | 核心理念 | Spec/Prompt 策略 | 工具 |
|------|------|----------|------------------|------|
| **SPDD** | 方法论 + 工具 | 将 Prompt 作为一等交付工件，REASONS Canvas 七维结构 | spec-anchored + 双向同步 | OpenSPDD CLI |
| **SDD (通用)** | 行业术语 | 编写规格文档再让 AI 生成代码 | spec-first 到 spec-as-source 不等 | 多种 |
| **BMAD Method** | 方法论 | 多角色 Agent 编排：plan-analysis-design-architect-dev-test | 仿真团队角色 | BMAD CLI |
| **PromptOps** | 实践 | 将 Prompt 视为可版本化、可测试的软件组件 | 持续管理 | — |

## 二、SDD 工具生态对照

### Birgitta Böckeler 三级分类

| 级别 | 含义 | 代表工具 |
|------|------|----------|
| **Spec-first** | 先写 spec 再开发，完成后 spec 可能丢弃 | Kiro (Amazon) |
| **Spec-anchored** | spec 与代码共存并持续演化 | SPDD, spec-kit (GitHub) |
| **Spec-as-source** | spec 是唯一编辑源，代码为生成产物 | Tessl Framework |

### 工具详细对照

| 工具 | 开发者 | 定位 | 工作流 | License |
|------|--------|------|--------|---------|
| **OpenSPDD** | Wei Zhang (Thoughtworks) | SPDD 方法论 CLI 实现 | analysis → canvas → generate → sync | MIT |
| **Kiro** | Amazon | 轻量级 SDD IDE | Requirements → Design → Tasks | — |
| **spec-kit** | GitHub | SDD 开源工具包 | Constitution → Specify → Plan → Tasks | 开源 |
| **Tessl Framework** | Tessl | spec-as-source 方案 | Spec ↔ Code (双向, beta) | 私有 Beta |
| **OpenSpec** | Fission-AI | 轻量 SDD 框架 | Proposal → Apply → Archive | 开源 |
| **OpenSDD** | 社区 | 开放标准 SDD | 围绕开放标准 | 开源 |
| **PDD** | PromptDriven.org | Prompt 驱动开发 CLI | sync / generate / test / verify | MIT |
| **SpecPrompt** | — | Spec 取代 Prompt | 版本化、可测试的 spec | — |

## 三、SPDD 工作流命令体系 (OpenSPDD)

| 命令 | 类型 | 用途 |
|------|------|------|
| `/spdd-story` | 可选 | 将大需求拆分为 INVEST 用户故事 |
| `/spdd-analysis` | 核心 | 从需求提取领域关键词，扫描代码，产出战略分析 |
| `/spdd-reasons-canvas` | 核心 | 生成 REASONS Canvas 完整设计文档 |
| `/spdd-generate` | 核心 | 读取 Canvas，按 Operations 逐任务生成代码 |
| `/spdd-prompt-update` | 核心 | 需求变更时增量更新 Canvas |
| `/spdd-sync` | 核心 | 代码侧变更反向同步回 Canvas |
| `/spdd-api-test` | 可选 | 生成 cURL API 测试脚本 |
| `/spdd-code-review` | 可选(beta) | 对照 Canvas 进行代码审查 |

## 四、SPDD 适用场景评估

| 评分 | 场景 | 说明 |
|------|------|------|
| 5/5 | 规模化标准化交付 | 高重复业务逻辑，需长期可维护性 |
| 5/5 | 高合规硬约束环境 | 金融系统、多通道部署等 |
| 4/5 | 团队协作与审讦 | 变更需端到端可追溯 |
| 4/5 | 跨切面一致性工作 | 多微服务/多语言重构同步 |
| 2/5 | 紧急 hotfix | 速度优先于架构纪律 |
| 2/5 | 探索性 spike | 验证想法而非生产软件 |
| 1/5 | 一次性脚本 | 投入产出比不合算 |
| 1/5 | 纯创意/视觉工作 | 审美驱动而非逻辑驱动 |

## 五、SPDD 投资回报分析

### 回报

| 收益 | 影响度 | 见效速度 |
|------|--------|----------|
| 确定性 | 高 | 即时 |
| 可追溯性 | 高 | 即时 |
| 加速审查 | 高 | 短期 |
| 可解释性 | 中高 | 渐进 |
| 安全演化 | 高 | 长期 |

### 投入

| 领域 | 门槛 | 性质 |
|------|------|------|
| 思维转变 | 高 | 持续培训 |
| 前期高阶专业知识 | 中高 | 按功能 |
| 自动化工具 | 中 | 基础设施建设 |

## 六、关键人物与机构

| 人物 | 角色 | 关联 |
|------|------|------|
| Wei Zhang (张伟) | AI 辅助交付专家, Thoughtworks | SPDD 作者, OpenSPDD 开发者 |
| Jessie Jie Xia (夏杰) | Global CIO, Thoughtworks | SPDD 作者 |
| Birgitta Böckeler | Distinguished Engineer, Thoughtworks | "Exploring GenAI" 系列, SDD 三级分类 |
| Liu Shangqi | Technology Director, Thoughtworks APAC | SDD 深度拆解博文 |
| Martin Fowler | Author/Editor | SPDD 文章编辑和发布平台 |
| Sunit Parekh | Thoughtworks | "Beyond Vibe Coding" 作者 |

## 七、示例项目

| 项目 | 说明 | 链接 |
|------|------|------|
| token-billing | SPDD 示例项目 — LLM Token 计费引擎 | [GitHub](https://github.com/gszhangwei/token-billing) |
| open-spdd | SPDD CLI 工具 (Go) | [GitHub](https://github.com/gszhangwei/open-spdd) |
