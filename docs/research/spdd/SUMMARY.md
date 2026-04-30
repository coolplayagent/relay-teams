# SPDD 深度研究资料索引

> 研究日期：2026-04-29 | 主题：Structured Prompt-Driven Development (SPDD)

## 核心概念

**SPDD (Structured Prompt-Driven Development)** 是 Thoughtworks 内部 IT 团队 (Global IT Services) 发展出的一种工程方法，将 Prompt 视为**一等交付工件** (first-class delivery artifacts)。通过 REASONS Canvas 七维结构化框架和完整工作流，使 AI 辅助开发成为可治理、可审查、可复用的组织级能力。

### REASONS Canvas 七维度

| 维度 | 含义 | 类别 |
|------|------|------|
| R - Requirements | 业务目标和 DoD | 抽象层（意图与设计） |
| E - Entities | 领域实体和关系 | 抽象层 |
| A - Approach | 解决方案策略 | 抽象层 |
| S - Structure | 系统架构和依赖 | 抽象层 |
| O - Operations | 具体实现步骤（按序） | 执行层 |
| N - Norms | 编码标准和模式 | 治理层 |
| S - Safeguards | 约束和边界条件 | 治理层 |

### 三大核心技能

1. **Alignment (对齐)** — 锁定意图后再动手
2. **Abstraction First (抽象优先)** — 先设计再生成
3. **Iterative Review (迭代审查)** — 将输出变成受控循环

## 文档目录

###  spdd/ — SPDD 核心文献（Martin Fowler 官站）

| 文件 | 内容 | 来源 |
|------|------|------|
| `01-spdd-main-article.md` | SPDD 主文章全文 | [martinfowler.com](https://martinfowler.com/articles/structured-prompt-driven/) |
| `02-abstraction-first.md` | 核心技能：抽象优先 | [martinfowler.com](https://martinfowler.com/articles/structured-prompt-driven/abstraction-first.html) |
| `03-alignment.md` | 核心技能：对齐 | [martinfowler.com](https://martinfowler.com/articles/structured-prompt-driven/alignment.html) |
| `04-iterative-review.md` | 核心技能：迭代审查 | [martinfowler.com](https://martinfowler.com/articles/structured-prompt-driven/iterative-review.html) |

###  related/ — SDD 生态相关文献

| 文件 | 内容 | 来源 |
|------|------|------|
| `05-birgitta-sdd-tools.md` | Birgitta Böckeler: Understanding SDD — Kiro, spec-kit, Tessl 三级分析 | [martinfowler.com](https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html) |
| `06-tw-sdd-unpacking.md` | Liu Shangqi: Spec-driven development 深度拆解 (2025) | [thoughtworks.com](https://www.thoughtworks.com/en-us/insights/blog/agile-engineering-practices/spec-driven-development-unpacking-2025-new-engineering-practices) |
| `07-tw-beyond-vibe-coding.md` | Sunit Parekh: Beyond Vibe Coding — AI-Native 工程五大构建块 | [thoughtworks.com](https://www.thoughtworks.com/en-us/insights/blog/generative-ai/beyond-vibe-coding-the-five-building-blocks-of-aI-native-engineering) |
| `08-tw-radar-sdd.md` | Thoughtworks Technology Radar: Spec-driven development (Nov 2025, Assess) | [thoughtworks.com](https://www.thoughtworks.com/en-us/radar/techniques/spec-driven-development) |

###  tools/ — 工具与项目

| 文件 | 内容 | 来源 |
|------|------|------|
| `09-openspdd-readme.md` | OpenSPDD CLI 工具 README（REASONS Canvas 工具链） | [github.com/gszhangwei/open-spdd](https://github.com/gszhangwei/open-spdd) |
| `10-spdd-ecosystem.md` | SPDD/SDD 生态系统全景：工具、框架、方法论对照 | 综合整理 |

## SPDD vs SDD 对照

| 维度 | Spec-Driven Development (SDD) | Structured Prompt-Driven Development (SPDD) |
|------|-------------------------------|---------------------------------------------|
| 起源 | 行业通用术语，2025 年兴起 | Thoughtworks 内部实践，2026 年公开发布 |
| 核心单位 | Spec (规格文档) | Structured Prompt (REASONS Canvas) |
| Spec 定义 | 功能规格文档 (PRD + 技术规格) | 七维结构化设计合约 |
| 治理方式 | 因工具而异 (Kiro/spec-kit/Tessl) | 统一工作流 + 双向同步 |
| 同步机制 | 多数为单向 (spec → code) | 双向 (prompt ↔ code, /spdd-sync) |
| 定位 | Birgitta 分类为 spec-anchored | 明确 spec-anchored + 闭环迭代 |
| 工具支持 | Kiro / spec-kit / Tessl / OpenSpec / BMAD | OpenSPDD (CLI) |

## 关键时间线

- **2025-10**: Birgitta Böckeler 发表 SDD 三工具分析 (martinfowler.com)
- **2025-11**: Thoughtworks Technology Radar 将 SDD 列入 "Assess"
- **2025-12**: Liu Shangqi 发表 SDD 深度拆解博客
- **2026-03**: Sunit Parekh 发表 "Beyond Vibe Coding" 五大构建块
- **2026-03**: OpenSPDD 开源 (GitHub, MIT License)
- **2026-04-28**: Wei Zhang & Jessie Xia 发表 SPDD 主文章 (martinfowler.com)

## 推荐阅读顺序

1. `01-spdd-main-article.md` — 建立整体认知
2. `05-birgitta-sdd-tools.md` — 理解 SDD 生态背景
3. `02-abstraction-first.md` + `03-alignment.md` + `04-iterative-review.md` — 深入三大技能
4. `06-tw-sdd-unpacking.md` — SDD 实践细节
5. `07-tw-beyond-vibe-coding.md` — AI-Native 工程全景
6. `09-openspdd-readme.md` — 工具实操
7. `10-spdd-ecosystem.md` — 生态系统全景图
