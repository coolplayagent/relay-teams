# AI_SE3_Huawei_20slides_Final — 来源与关键论断映射

- 生成时间：2026-03-30 21:22:28 CST +0800
- 当前时间基准：2026-03-30 21:22:28 CST +0800
- 主文件：`hello-root/AI_SE3_Huawei_20slides_Final.pptx`
- 主题：AI大模型时代下的软件开发思考及调研
- 风格：华为风格执行汇报版（浅底、克制红色强调、模块化、数据优先）
- 页数：20 页（精确）

## 一、文件说明
本文件用于满足以下交付要求：
1. 列出本次 PPT 使用的关键来源；
2. 映射每一页的重要论断到可追溯来源；
3. 记录补充网络研究与采用口径；
4. 标记限制、解释性判断与非直接引语。

## 二、最终 PPT 结构（20页）
1. 封面｜AI大模型时代下的软件开发思考及调研
2. 执行摘要：本报告的五个判断
3. 为什么现在必须重看软件开发：AI 已从工具议题变成经营议题
4. 本报告的立场：真正要升级的不是开发工具，而是软件工程操作系统
5. 章节一｜范式迁移：从软件工程 2.0 走向 3.0
6. Software Engineering 3.0：从 code-centric 到 intent-centric
7. SE 3.0 对企业研发栈的含义：Teammate.next 到 Runtime.next
8. Thoughtworks 的提醒：不要把 GenAI 缩减为“写代码更快”
9. 章节二｜企业分水岭：从 Prompt Engineering 走向 Harness Engineering
10. 什么是 Harness Engineering：企业不是在“使用 AI”，而是在“驯化 AI”
11. Harness 的三大核心：Context Engineering、Architectural Constraints、Garbage Collection
12. Harness 不是文档堆砌，而是可运行的工程系统
13. 章节三｜证据审视：北美数据证明了什么，又没有证明什么
14. 效率证据：北美一线研究表明，AI 辅助对开发者个体产出有真实增益
15. 质量与信任证据：收益存在，但“几乎正确”仍是主流痛点
16. 关键反直觉：局部提效不自动转化为系统级交付提升
17. 章节四｜组织重构：Human-Agent Team 将成为新的研发基本单元
18. 研发组织的新模型：从 Org Chart 到 Work Chart / Human-Agent Team
19. 企业落地路线：从工具试点到平台化 AI-native engineering
20. 结论与建议：未来三年的胜负手，不是模型选择，而是工程系统能力

## 三、关键来源清单
### Primary sources
[1] Hassan, Ahmed E. et al. *Towards AI-Native Software Engineering (SE 3.0): A Vision and a Challenge Roadmap*. arXiv.
URL: https://arxiv.org/abs/2410.06107
使用方式：SE 3.0、intent-centric、conversation-oriented、Teammate.next / IDE.next / Compiler.next / Runtime.next。

[2] Birgitta Böckeler. *Harness Engineering*. Martin Fowler / Thoughtworks.
URL: https://martinfowler.com/articles/exploring-gen-ai/harness-engineering.html
使用方式：Harness 的三大核心；“AI success depends on rigor and constraint”这一类解读的直接依据。

[3] engineering.fyi. *Harness engineering: leveraging Codex in an agent-first world*.
URL: https://www.engineering.fyi/article/harness-engineering-leveraging-codex-in-an-agent-first-world
使用方式：OpenAI Harness 团队实践摘要（0 行手写代码、约 5 个月、约 100 万行代码、约 1500 PR、AGENTS.md ~100 行等）。

[4] Thoughtworks. *Generative AI and the software development lifecycle: Much more than coding assistance*.
URL: https://www.thoughtworks.com/en-us/insights/articles/generative-ai-software-development-lifecycle-more-than-coding-assistance
使用方式：GenAI 不应只被理解为代码生成；10%–30% 生产率提升与影响因素；SDLC 全生命周期价值。

[5] Thoughtworks. *Looking Glass 2025*.
URL: https://www.thoughtworks.com/content/dam/thoughtworks/documents/looking-glass-2025/looking_glass_2025_final.pdf
使用方式：将 AI 嵌入整个软件开发生命周期；secure software delivery；平台、RAG、评测、可观测性等企业落地视角。

[6] Thoughtworks. *Technology Radar 33 Highlights The Rapid Evolution of AI Assistance in 2025*.
URL: https://www.thoughtworks.com/en-us/about-us/news/2025/thoughtworks-tech-radar-33-rapid-ai
使用方式：context engineering、MCP、agentic systems、AI coding workflows、shadow IT、complacency with AI-generated code 等。

[7] Thoughtworks. *AI assistance is a misunderstood revolution in software engineering — here’s why*.
URL: https://www.thoughtworks.com/insights/blog/generative-ai/ai-assistance-misunderstood-revolution-software-engineering
使用方式：context engineering、shared instructions、spec-driven development、AI 是团队技术而非个人技巧。

[8] Google Cloud / DORA. *Highlights from the 10th DORA report*；*DORA Report Preview - AI in the workplace: Adoption and impact*.
URLs:
- https://cloud.google.com/blog/products/devops-sre/announcing-the-2024-dora-report
- https://dora.dev/research/2024/ai-preview/
使用方式：AI 采用率 >75%；高度信任 AI 代码仅 24%；文档/代码质量/评审速度上升而吞吐/稳定性下滑。

[9] GitHub. *Research: Quantifying GitHub Copilot’s impact in the enterprise with Accenture*.
URL: https://github.blog/news-insights/research/research-quantifying-github-copilots-impact-in-the-enterprise-with-accenture/
使用方式：PR +8.69%、merge rate +15%、successful builds +84%、90% job fulfillment、95% enjoy coding more。

[10] GitHub. *Survey: The AI wave continues to grow on software development teams*.
URL: https://github.blog/news-insights/research/survey-ai-wave-grows/
使用方式：97%+ 使用 AI 编码工具；59%–88% 组织允许或鼓励使用；98%+ 试验过 AI 生成测试用例。

[11] GitHub. *Does GitHub Copilot improve code quality? Here’s what the data says*.
URL: https://github.blog/news-insights/research/does-github-copilot-improve-code-quality-heres-what-the-data-says/
使用方式：通过全部单测概率 +53.2%；可读性 +3.62%；可靠性 +2.94%；可维护性 +2.47%；简洁性 +4.16%；批准率 +5%。

[12] Microsoft Research. *The Effects of Generative AI on High-Skilled Work: Evidence from Three Field Experiments with Software Developers*.
URL: https://www.microsoft.com/en-us/research/publication/the-effects-of-generative-ai-on-high-skilled-work-evidence-from-three-field-experiments-with-software-developers/
使用方式：4867 名开发者；完成任务平均提升 26.08%。

[13] MIT Open Publishing. *The Productivity Effects of Generative AI: Evidence from a Field Experiment with GitHub Copilot*.
URL: https://mit-genai.pubpub.org/pub/v5iixksv/release/2
使用方式：Microsoft 与 Accenture 开发者 PR 完成量提升区间。

[14] Microsoft. *The 2025 Annual Work Trend Index: The Frontier Firm is born*.
URL: https://blogs.microsoft.com/blog/2025/04/23/the-2025-annual-work-trend-index-the-frontier-firm-is-born/
使用方式：82% 领导者；80% 时间精力不足；46% 使用 agents 自动化流程；41% 训练 agent；36% 管理 agent；78% 招聘新 AI 岗位等。

[15] Stack Overflow. *2025 Developer Survey*.
URL: https://survey.stackoverflow.co/2025/
使用方式：84% 使用或计划使用 AI；69% 认同 agent 提升生产率；17% 认同改善协作；66% 认为“几乎正确但不完全正确”是最大挫折；45.2% 认为调试 AI 生成代码更耗时。

## 四、逐页重要论断映射
### Slide 1
- 论断：AI-native engineering 是独立于单点工具增强的范式跃迁。
  来源：[1][4][5]

### Slide 2
- 论断：SE 3.0、Harness、局部效率 vs 系统交付、Human-Agent Team 是全篇五个主判断。
  来源：[1][2][4][5][8][12][14]

### Slide 3
- 论断：AI 软件开发已经从个人试验走向企业经营议题。
  证据：Microsoft WTI 的高层紧迫度；DORA 的采用率；GitHub/Stack Overflow 的大规模使用信号。
  来源：[8][10][14][15]

### Slide 4
- 论断：企业真正要建设的是“软件工程操作系统”。
  这是基于 [1][2][4][5] 的综合解释性判断，不是任何单一来源原句。

### Slide 5
- 章节过渡页，归纳性陈述来自 [1][4][7]。

### Slide 6
- 论断：SE 3.0 是 intent-centric、conversation-oriented。
  来源：[1]

### Slide 7
- 论断：研发栈将从 Teammate.next 到 Runtime.next 多层重构。
  来源：[1]；结合 [5] 做企业架构表达。

### Slide 8
- 论断：Thoughtworks 认为 GenAI 的价值不应被缩减为 coding assistance。
  来源：[4]
- 论断：Thoughtworks 观察到 10%–30% 生产率提升。
  来源：[4]

### Slide 9
- 章节过渡页，核心命题来自 [2][3][6][7] 的共同趋势。

### Slide 10
- 论断：Harness 是企业控制层。
  来源：[2][3]

### Slide 11
- 论断：Harness 三大核心为 Context Engineering、Architectural Constraints、Garbage Collection。
  来源：[2]；[3] 为实践补充。

### Slide 12
- 论断：Harness 是可运行工作流而不是文档堆砌。
  来源：[3][5][7]
- 工作流表达 Spec→Context→Generation→Evaluation→Merge/Reject→Feedback 为综合性组织表达，不是直接引语。

### Slide 13
- 章节过渡页，证据框架来自 [8][9][11][12][13][15]。

### Slide 14
- 数据：26.08%、12.92%–21.83%、7.51%–8.69%、8.69% / 15% / 84%。
  来源：[9][12][13]

### Slide 15
- 数据：+53.2%、+3.62%、+2.94%、24%、66%。
  来源：[8][11][15]

### Slide 16
- 数据：+7.5%、+3.4%、+3.1%、-1.5%、-7.2%。
  来源：[8]
- 论断：若没有 Harness 和治理，AI 可能把“个人快”转化为“系统乱”。
  来源：[8] + [4][5] 的解释性推演。

### Slide 17
- 章节过渡页，组织重构依据主要来自 [14]，并辅以 [5][15]。

### Slide 18
- 数据：46%、41%、36%、17%。
  来源：[14][15]
- 矩阵表达为面向企业管理的解释性结构化表达，不是某单一来源原表。

### Slide 19
- 三阶段路线图为基于 [2][4][5][8][14] 的操作化建议。
  这部分属于综合判断，不是来源中的现成框架原文。

### Slide 20
- 最终结论为全篇综合判断，对应来源见该页右侧索引。
  具体支撑：SE 3.0 [1]；Harness [2][3]；SDLC 与企业治理 [4][5][8]；证据 [9][11][12][13]；组织重构 [14][15]。

## 五、补充说明与限制
1. 本次生成遵循 `tmp/ai_se3_huawei_20slide_content_spec.md` 的 20 页结构要求，未增加额外 references-only 第 21 页；因此采用“每页页脚引用 + 最后一页来源索引 + 本 notes 文件”三层追溯方式。
2. 一些管理框架、路线图和组织矩阵属于基于多来源的综合性表达，已在对应处标注为“解释性判断”或“综合表达”，避免误认为单一来源事实。
3. Thoughtworks `Looking Glass 2025` 通过 PDF 下载确认可访问；本次在 PPT 中主要把它作为企业治理、生命周期嵌入和 secure software delivery 的支撑来源。
4. OpenAI 原始 Harness 文章未直接纳入最终引用编号，因本次使用了 Martin Fowler / Thoughtworks 评述与 engineering.fyi 结构化摘要进行交叉验证，优先选择更稳定、可抓取来源。
5. 所有量化数字均来自可访问页面或此前已完成的来源整理文件；如后续用于正式对外发布，建议再做一次人工复核与截图存档。

## 六、工作区中参考的材料文件
- `tmp/ai_se3_huawei_20slide_content_spec.md` — 本次 PPT 的主合同文件
- `AI_SE3_Huawei_20slides_Sources.md` — 已整理好的来源摘要
- `2026_AI_Insight_Sources_and_Outline.md` — 华为风格结构与写法参考
- `2026_AI_Insight_Fix_Log.md` — 排版收紧与文本安全区经验参考

## 七、交付文件路径
- PPTX：`hello-root/AI_SE3_Huawei_20slides_Final.pptx`
- 来源与说明：`hello-root/AI_SE3_Huawei_20slides_Final_Notes.md`
- 生成脚本：`hello-root/generate_ai_se3_huawei_20slides_final.js`
