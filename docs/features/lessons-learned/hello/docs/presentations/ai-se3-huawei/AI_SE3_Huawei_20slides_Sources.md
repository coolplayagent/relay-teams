# AI大模型时代下软件开发思考及调研（20页PPT）资料说明

- 生成时间：2026-03-30 20:53:57 CST +0800
- 主题：AI大模型时代下的软件开发范式迁移、工程实践与组织重构
- 风格：华为风格（红色强调、白底高信息密度、模块化布局）
- 页数：20页

## 核心结论摘要

1. 软件工程正在从“人写代码、工具辅助”走向“人定义意图、AI参与实现”的 **SE 3.0 / AI-native engineering** 阶段。[1]
2. 真正决定企业落地效果的，不再只是模型能力，而是 **上下文工程、约束机制、测试体系、可观测性与安全交付** 的系统工程能力。[2][3][4][5][8]
3. AI 对个体开发效率和局部代码质量已有多项正向证据，但 **局部效率提升并不自动等于系统级交付提升**；如果工程纪律不足，吞吐与稳定性甚至可能下降。[8][9][11][12][13]
4. 组织层面正在从传统 Org Chart 走向 **Human-Agent Team / Work Chart**，软件团队的管理对象将从“人力容量”扩展到“数字劳动力 + 人机配比”。[14]
5. 对大型企业而言，最优解不是“全员随意用 AI”，而是构建 **企业级 Harness（护栏/底座）**：规范化仓库、共享指令、RAG、评测、质量门禁、平台化 Agent Runtime、审计与安全控制。[2][3][4][5][6][7]

## 可直接用于PPT的数据点

### A. 软件工程 3.0 / AI-native
- Ahmed E. Hassan 等在《Towards AI-Native Software Engineering (SE 3.0)》中提出：SE 3.0 是一种 **intent-centric、conversation-oriented** 的 AI-native 软件工程模式，并给出 `Teammate.next / IDE.next / Compiler.next / Runtime.next` 等技术栈构想。[1]

### B. Thoughtworks 视角
- Thoughtworks 认为，GenAI 的价值不应仅被窄化为代码生成；其在 **测试、文档、需求澄清、知识检索、场景推演** 等 SDLC 多环节都能创造价值。[4]
- Thoughtworks 自身实验结果显示：GenAI 在软件开发中可带来 **10%–30% 的生产率提升**，但效果取决于开发者经验、使用 AI 的经验、问题定义清晰度三项因素。[4]
- Thoughtworks《Looking Glass 2025》明确建议：**“将 AI 嵌入整个软件开发生命周期”**，而不只是用于编码；同时强调 RAG、评测与可观测性的重要性。[5]
- Thoughtworks 2025 年 Radar 新闻稿指出：2025 年 AI 辅助软件工程正在从 prompt engineering 快速走向 **context engineering、MCP、agentic systems 与 AI coding workflows**，且伴随 **shadow IT、对 AI 生成代码的 complacency** 等反模式上升。[6]
- Thoughtworks 2025 年博客指出：AI 辅助正在推动软件工程实践演化，关键字包括 **context engineering、shared instructions、spec-driven development**。[7]

### C. Harness engineering / Agent-first engineering
- Martin Fowler / Thoughtworks 对 OpenAI Harness engineering 的解读，将其核心拆为三类：**Context engineering、Architectural constraints、Garbage collection**。[2]
- engineering.fyi 对 OpenAI 原文的结构化摘要显示：OpenAI Harness 团队用 **0 行人工手写代码**、约 **5 个月**、约 **100 万行代码**、约 **1500 个 PR**、约 **1/10 开发时间** 构建出内部 beta 产品；同时强调 `AGENTS.md` 应保持约 **100 行**、充当目录而不是百科全书。[3]

### D. 北美公司 / 研究机构关于效率与质量的量化证据
- MIT Open Publishing 预览研究：基于 Microsoft 与 Accenture 两项现场实验、共 **1974 名开发者**，初步结果显示：
  - Microsoft 开发者使用 Copilot 后，每周 PR 完成量提升 **12.92%–21.83%**；
  - Accenture 开发者提升 **7.51%–8.69%**。[13]
- Microsoft Research 2025：基于 Microsoft、Accenture、某《财富》100 强企业三项随机现场实验、共 **4867 名开发者**，使用 AI 编码助手后，已完成任务数平均提升 **26.08%**。[12]
- GitHub 与 Accenture 的企业研究显示：
  - Pull Request 数量提升 **8.69%**；
  - PR merge rate 提升 **15%**；
  - successful builds 提升 **84%**；
  - **90%** 的开发者认为工作更有满足感；
  - **95%** 的开发者表示更享受编码。[9]
- GitHub 2024 代码质量随机对照试验显示：
  - 使用 Copilot 的开发者通过全部 10 个单元测试的概率高出 **53.2%**；
  - 可读性提升 **3.62%**；
  - 可靠性提升 **2.94%**；
  - 可维护性提升 **2.47%**；
  - 简洁性提升 **4.16%**；
  - 代码被批准的概率提升 **5%**。[11]

### E. DORA / 系统交付视角
- 2024 DORA 报告显示：**超过 75%** 的受访者在至少一项日常工作职责上依赖 AI。[8]
- 超过 **1/3** 的受访者表示 AI 带来了“中度到极高”的生产率提升。[8]
- 但 DORA 同时发现：当 AI 采用度提升 **25%** 时，文档质量提升 **7.5%**、代码质量提升 **3.4%**、代码评审速度提升 **3.1%**；与此同时，交付吞吐下降 **1.5%**、交付稳定性下降 **7.2%**。[8]
- DORA AI Preview 显示，只有 **24%** 的开发者“高度信任”AI 生成代码；这说明组织级 AI 落地仍受信任与治理约束。[8]

### F. Microsoft Work Trend / Human-Agent Team
- Microsoft 2025 Work Trend Index 显示：
  - **82%** 的领导者认为现在是重构战略与运营的关键年份；
  - **53%** 的领导者要求提高生产率；
  - **80%** 的全球员工表示缺乏足够时间或精力完成工作；
  - 员工平均每 **2 分钟** 就会被会议、邮件或消息打断一次；
  - **82%** 的领导者预计未来 12–18 个月将使用数字劳动力扩充员工队伍；
  - **46%** 的领导者表示其组织正在使用 agent 全自动化工作流或业务流程；
  - **41%** 的领导者预计 5 年内团队会训练 agent，**36%** 预计会管理 agent；
  - **78%** 的领导者正在考虑招聘新的 AI 岗位；
  - **83%** 的领导者认为 AI 会让员工更早承担复杂和战略性工作。[14]

### G. GitHub / Stack Overflow 社区与企业信号
- GitHub 2024 企业开发者调查（2000 名受访者，覆盖美国、巴西、德国、印度）显示：
  - 超过 **97%** 的受访者曾在工作中使用过 AI 编码工具；
  - 各地区有 **59%–88%** 的企业至少“允许”或“鼓励”使用 AI 工具；
  - 在美国，**90%** 的受访者认为 AI 工具提升了代码质量；在印度为 **81%**；
  - 几乎所有组织（**98%+**）都试验过使用 AI 生成测试用例。[10]
- Stack Overflow 2025 调查显示：
  - **84%** 的开发者正在使用或计划使用 AI 工具；
  - AI agent 用户中，**69%** 认同 agent 提高了生产率，约 **70%** 认同其减少了特定开发任务时间；
  - 但只有 **17%** 认同 agent 改善了团队协作；
  - **66%** 的开发者最大挫折是“AI 方案几乎正确但并不完全正确”；
  - **45.2%** 认为调试 AI 生成代码更耗时；
  - 对 AI 工具准确性，信任者约 **33%**，不信任者约 **46%**。[15]

## 参考来源（建议在PPT中按 [1] [2] 形式引用）

[1] Hassan, Ahmed E. et al. *Towards AI-Native Software Engineering (SE 3.0): A Vision and a Challenge Roadmap*. arXiv:2410.06107. https://arxiv.org/abs/2410.06107

[2] Birgitta Böckeler. *Harness Engineering*. Martin Fowler / Thoughtworks, 2026-02-17. https://martinfowler.com/articles/exploring-gen-ai/harness-engineering.html

[3] engineering.fyi. *Harness engineering: leveraging Codex in an agent-first world*（对 OpenAI 原文的结构化摘要）. https://www.engineering.fyi/article/harness-engineering-leveraging-codex-in-an-agent-first-world

[4] Thoughtworks. *Generative AI and the software development lifecycle: Much more than coding assistance*. https://www.thoughtworks.com/en-us/insights/articles/generative-ai-software-development-lifecycle-more-than-coding-assistance

[5] Thoughtworks. *Looking Glass 2025*（PDF）. https://www.thoughtworks.com/content/dam/thoughtworks/documents/looking-glass-2025/looking_glass_2025_final.pdf

[6] Thoughtworks. *Technology Radar 33 Highlights The Rapid Evolution of AI Assistance in 2025*. https://www.thoughtworks.com/en-us/about-us/news/2025/thoughtworks-tech-radar-33-rapid-ai

[7] Thoughtworks. *AI assistance is a misunderstood revolution in software engineering — here’s why*. https://www.thoughtworks.com/insights/blog/generative-ai/ai-assistance-misunderstood-revolution-software-engineering

[8] Google Cloud / DORA. *Highlights from the 10th DORA report*；以及 *DORA Report Preview - AI in the workplace: Adoption and impact*. https://cloud.google.com/blog/products/devops-sre/announcing-the-2024-dora-report ; https://dora.dev/research/2024/ai-preview/

[9] GitHub. *Research: Quantifying GitHub Copilot’s impact in the enterprise with Accenture*. https://github.blog/news-insights/research/research-quantifying-github-copilots-impact-in-the-enterprise-with-accenture/

[10] GitHub. *Survey: The AI wave continues to grow on software development teams*. https://github.blog/news-insights/research/survey-ai-wave-grows/

[11] GitHub. *Does GitHub Copilot improve code quality? Here’s what the data says*. https://github.blog/news-insights/research/does-github-copilot-improve-code-quality-heres-what-the-data-says/

[12] Microsoft Research. *The Effects of Generative AI on High-Skilled Work: Evidence from Three Field Experiments with Software Developers*. https://www.microsoft.com/en-us/research/publication/the-effects-of-generative-ai-on-high-skilled-work-evidence-from-three-field-experiments-with-software-developers/

[13] MIT Open Publishing. *The Productivity Effects of Generative AI: Evidence from a Field Experiment with GitHub Copilot*. https://mit-genai.pubpub.org/pub/v5iixksv/release/2

[14] Microsoft. *The 2025 Annual Work Trend Index: The Frontier Firm is born*. https://blogs.microsoft.com/blog/2025/04/23/the-2025-annual-work-trend-index-the-frontier-firm-is-born/

[15] Stack Overflow. *2025 Developer Survey*. https://survey.stackoverflow.co/2025/

## 说明
- 本次 PPT 中的量化数据优先采用可直接访问、可追溯的一手/准一手来源。
- 对 OpenAI 原始 Harness 文章，由于自动抓取受限，本次以 Martin Fowler/Thoughtworks 的评述与 engineering.fyi 的结构化摘要交叉参考。
- 对 McKinsey 等需登录/反爬较强站点，本次作为思路参考，不在量化结论中使用其不可直接核验的数据。