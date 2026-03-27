# 内容大纲与页面描述生成技能

本技能指导 AI 完成内容规划中的两个核心环节：**大纲结构生成** 和 **单页内容描述生成**。最终产物为一个 `ppt_plan.md` 文件，包含完整大纲和每一页的详细内容描述。

## 最终产物：ppt_plan.md

所有大纲和页面描述生成完毕后，**必须**将结果整合写入工作目录下的 `ppt_plan.md` 文件。

### 文件格式模板（必须严格遵循）

以下格式为**强制模板**，章节标题和标题层级不可变更：

```markdown
# {主题}

> 生成时间：{YYYY-MM-DD HH:MM}
> 总页数：{N} 页
> 风格：{style_id}（如 huawei、default，必须从 [styles.json](styles/styles.json) 中读取对应的配色和字体信息）

---

## 大纲总览

{如果是章节格式，按章节列出；如果是简单格式，直接列出}

### 第一部分：{章节名}（章节格式时使用）

| 页码 | 标题   | 核心要点           |
| ---- | ------ | ------------------ |
| P1   | {标题} | {要点 1}；{要点 2} |
| P2   | {标题} | {要点 1}；{要点 2} |

### 第二部分：{章节名}

| 页码 | 标题   | 核心要点           |
| ---- | ------ | ------------------ |
| P3   | {标题} | {要点 1}；{要点 2} |

---

## 页面详细描述

### P1: {页面标题}

{该页的完整 Markdown 描述，包含标题、内容}

---

### P2: {页面标题}

{该页的完整 Markdown 描述}

---

（依此类推，每页用 --- 分隔）
```

### 写入规则

1. **文件路径**：由调用方通过 `output_path` 参数指定，必须提供相对于 skill 根目录的完整路径
2. **覆盖策略**：如果文件已存在，直接覆盖（每次生成都是完整内容）
3. **写入时机**：
   - 大纲生成完成 + 所有页面描述生成完成后写入
   - 大纲修改或页面描述修改后，重新写入完整文件
4. **编码**：UTF-8
5. **大纲总览**中的要点用分号 `;` 连接，保持表格紧凑
6. **页面详细描述**中保留完整 Markdown 格式（标题层级、列表等）

### 格式强制要求（不可变更）

ppt_plan.md 的结构必须严格遵循模板，**不得变更以下关键元素**：

| 元素             | 正确格式             | 错误示例                         |
| ---------------- | -------------------- | -------------------------------- |
| 页面描述章节标题 | `## 页面详细描述`    | `## 页面结构说明`、`## 页面内容` |
| 每页标题格式     | `### P1: {页面标题}` | `**P1: xxx**`、`P1: xxx`         |
| 页码格式         | `P1`、`P2`、`P3`...  | `第1页`、`Page 1`、`1.`          |

**违反格式的文件将被下游工具拒绝**，必须重新生成。

## 大纲输出格式

所有大纲相关操作共享两种 JSON 输出格式，根据内容长度自动选择：

### 格式 1：简单格式（短内容，无主要章节）

```json
[
  { "title": "标题 1", "points": ["要点 1", "要点 2"] },
  { "title": "标题 2", "points": ["要点 1", "要点 2"] }
]
```

### 格式 2：章节格式（长内容，有明确分部）

```json
[
  {
    "part": "第一部分：引言",
    "pages": [
      { "title": "欢迎", "points": ["要点 1", "要点 2"] },
      { "title": "概述", "points": ["要点 1", "要点 2"] }
    ]
  },
  {
    "part": "第二部分：主要内容",
    "pages": [
      { "title": "主题 1", "points": ["要点 1", "要点 2"] },
      { "title": "主题 2", "points": ["要点 1", "要点 2"] }
    ]
  }
]
```

**选择规则**：内容有清晰的 2+ 个主要章节时用格式 2，否则用格式 1。

---

## 需求确认（前置步骤）

在开始生成大纲之前，**必须**确认以下三项关键信息：主题、页数、风格

| 项目 | 说明 |
|------|------|
| **主题** | 演示文稿的核心内容 |
| **页数** | 目标页数（默认 3-6 页，最多 30 页） |
| **风格** | 视觉风格选择 |



---

## 工作流一览

| 场景           | 输入                | 输出                | 参考                              |
| -------------- | ------------------- | ------------------- | --------------------------------- |
| 从想法生成大纲 | 用户的 idea 文本    | 大纲 JSON           | [从想法生成](#从想法生成大纲)     |
| 解析已有大纲   | 用户提供的大纲文本  | 大纲 JSON（结构化） | [解析大纲](#解析用户提供的大纲)   |
| 从描述提取大纲 | 用户提供的完整描述  | 大纲 JSON           | [从描述提取](#从描述文本提取大纲) |
| 修改/细化大纲  | 当前大纲 + 用户要求 | 修改后的大纲 JSON   | [大纲修改](#大纲修改 refinement)  |
| 生成单页描述   | 大纲 + 页面信息     | 页面 Markdown 描述  | [页面描述生成](#单页页面描述生成) |
| 修改页面描述   | 当前描述 + 用户要求 | 修改后的描述数组    | [描述修改](#页面描述修改)         |
| 切分描述到各页 | 完整描述 + 大纲     | 每页描述字符串数组  | [描述切分](#描述文本切分)         |

---

## 从想法生成大纲

**输入**：用户的想法/主题描述文本

**Prompt 模板**：

```
You are a helpful assistant that generates an outline for a content plan.

You can organize the content in two ways:
1. Simple format: [{"title": "...", "points": ["...", "..."]}, ...]
2. Part-based format: [{"part": "...", "pages": [{"title": "...", "points": ["...", "..."]}, ...]}, ...]

Choose the format that best fits the content. Use parts when the content has clear major sections.

The user's request: {用户想法文本}. Now generate the outline, don't include any other text.
请使用全中文输出。
```

**要点**：

- 只输出 JSON，不包含任何其他文字
- 自动判断内容适合简单格式还是章节格式
- points 应具体、有信息量，不要写空泛的占位文字

---

## 解析用户提供的大纲

**输入**：用户已写好的非结构化大纲文本

**核心规则**（极其重要）：

- **禁止修改**用户原文的任何内容
- **禁止添加**原文中没有的新内容
- **禁止删除**原文中的任何内容
- 只做结构化重组，保留所有标题和要点原文

**Prompt 模板**：

```
You are a helpful assistant that parses a user-provided outline text into a structured format.

The user has provided the following outline text:
{用户大纲文本}

Your task is to analyze this text and convert it into a structured JSON format
WITHOUT modifying any of the original text content.

Important rules:
- DO NOT modify, rewrite, or change any text from the original outline
- DO NOT add new content that wasn't in the original text
- DO NOT remove any content from the original text
- Only reorganize the existing content into the structured format
- Preserve all titles, bullet points, and text exactly as they appear

Now parse the outline text above into the structured format. Return only the JSON.
请使用全中文输出。
```

---

## 从描述文本提取大纲

**输入**：用户提供的完整内容描述文本（含每页内容）

**任务**：从描述中识别出页面结构，提取每页标题和关键要点。

**Prompt 模板**：

```
You are a helpful assistant that analyzes a user-provided content description text
and extracts the outline structure from it.

The user has provided the following description text:
{描述文本}

Your task is to analyze this text and extract the outline structure:
1. How many pages are described
2. The title for each page
3. The key points or content structure for each page

Important rules:
- Extract the outline structure from the description text
- Identify page titles and key points
- If the text has clear sections/parts, use the part-based format
- Preserve the logical structure and organization from the original text
- The points should be concise summaries of the main content for each page

Return only the JSON.
请使用全中文输出。
```

---

## 大纲修改（Refinement）

**输入**：当前大纲 + 用户的修改要求 + 原始输入信息 + 历史修改记录（可选）

**Prompt 模板**：

```
You are a helpful assistant that modifies content outlines based on user requirements.

原始输入信息：
- 内容构想：{原始想法}

当前的内容大纲结构如下：
{当前大纲 JSON}

之前用户提出的修改要求：
- {历史要求 1}
- {历史要求 2}

**用户现在提出新的要求：{新要求}**

请根据用户的要求修改和调整大纲。你可以：
- 添加、删除或重新排列页面
- 修改页面标题和要点
- 调整页面的组织结构
- 添加或删除章节（part）
- 合并或拆分页面
- 如果当前没有内容，请根据用户要求和原始输入信息创建新的大纲

只输出 JSON 格式的大纲，不要包含其他文字。
请使用全中文输出。
```

**要点**：

- 保留历史修改记录，帮助 AI 理解上下文演变
- 原始输入信息根据项目类型不同：idea（构想）、outline（大纲文本）、descriptions（描述文本）

---

## 单页页面描述生成

这是整个流程中最关键的环节，将大纲要点展开为具有专业深度的页面内容。

**角色设定**：麦肯锡/贝恩资深内容策划专家

**核心要求**：不要简单罗列，必须深挖内容之间的**逻辑关系**。

**输入**：

- `page_index`：当前页码（从 1 开始）
- `original_input`：总需求（用户原始想法/大纲/描述）
- `outline`：完整大纲 JSON
- `page_outline`：当前页的大纲 `{title, points}`
- `part_info`：所属章节信息（可选）
- `research_path`：研究报告路径（如调用方提供）

**降级处理**：

如果调用方未提供 `research_path`（即跳过研究阶段直接给想法），页面描述应：
- 使用更通用的描述性语言（"本页面聚焦于XXX的核心要点"）
- 避免依赖具体研究数据的陈述（如具体数字、排名、统计结论）
- 明确标注"[待补充]"区域，供用户后续填充
- 侧重于逻辑框架和叙述结构的搭建，而非数据细节

**Prompt 模板**：

```
# Role
你是一位就职于麦肯锡/贝恩的资深内容策划专家。你的核心能力是将大纲转化为
**具有强逻辑关联**的单页内容。

# Context
我们正在编写第 {page_index} 页内容。
- **总需求**: "{original_input}"
- **全案大纲**: {outline}
- **本页核心任务**: {page_outline}
- **补充信息**: {part_info}

# Task
请撰写本页的具体内容。不要只做简单的罗列，必须深挖内容之间的**逻辑关系**。

# Output Rules (Crucial)
1. **内容结构说明**: 在内容开头，用引用块 `>` 说明本页内容的逻辑结构。
2. **标题法则**: 主标题必须是**结论性**的完整句子（Action + Result）。
3. **格式要求**: 使用标准的 Markdown。
请使用全中文输出。
```

**输出规则详解**：

1. **内容结构说明**：开头用 `>` 引用块说明本页内容的逻辑结构类型，例如：
   - `> 本页采用**并列递进**结构：三个驱动因素看似并列，实则存在时间递进...`
   - `> 本页采用**论据支撑**结构：左侧展示核心观点，右侧列出支撑数据...`
2. **标题法则**：标题必须是结论性完整句，如"数字化转型使运营效率提升 40%"而非"数字化转型"
3. **Markdown 格式**：使用标准 Markdown，便于后续阅读和处理

---

## 页面描述修改

**输入**：所有页面当前描述 + 用户修改要求 + 大纲 + 原始输入

**输出格式**：JSON 字符串数组，每个元素对应一页的描述

```json
[
  "页面标题：人工智能的诞生\n页面文字：\n- 1950 年，图灵提出\"图灵测试\"...",
  "页面标题：AI 的发展历程\n页面文字：\n- 1950 年代：符号主义..."
]
```

**Prompt 要点**：

- 可修改标题、内容、详细程度、结构和表达
- 如果参考文件中含 `/files/` 开头的图片 URL，以 markdown 图片格式输出：`![描述](/files/mineru/xxx/image.png)`
- 其他页面素材（如有）以 markdown 图片链接附在描述末尾

---

## 描述文本切分

**输入**：用户提供的完整描述文本 + 已提取的大纲结构

**任务**：将完整描述按大纲结构切分为每页独立描述。

**输出格式**：JSON 字符串数组，每个元素格式：

```
页面标题：[标题]

页面文字：
- [要点 1]
- [要点 2]
```

**规则**：

- 按大纲结构顺序对应每页
- 保留原文所有重要内容
- 若某页在原文中无明确描述，根据大纲合理生成

---

## 参考文件处理

当用户上传了参考文件时，在 prompt 前插入 XML 格式的文件内容：

```xml
<uploaded_files>
  <file name="文件名.pdf">
    <content>
    文件解析后的文本内容...
    </content>
  </file>
</uploaded_files>
```

参考文件内容将为大纲生成和页面描述提供素材支撑。

---

## 完整生成流程

### 标准流程

1. **生成大纲**：根据用户输入（想法/大纲文本/描述）生成结构化大纲 JSON
2. **逐页生成描述**：遍历大纲中的每一页，调用页面描述生成 Prompt
3. **整合写入**：将大纲和所有页面描述合并写入 `ppt_plan.md`
4. **迭代修改**（如需要）：根据用户反馈修改大纲或描述，重新写入 `ppt_plan.md`

**格式检查（提交前必做）**：

1. 确认存在 `## 页面详细描述` 章节（不是其他标题）
2. 确认每页使用 `### PN: 标题` 格式（三级标题，非行内加粗）
3. 确认每页都有具体的 Markdown 内容描述

### 修改流程

用户对已有 `ppt_plan.md` 提出修改要求时：

1. 读取当前 `ppt_plan.md` 中的大纲和描述
2. 根据修改类型调用对应的 Refinement Prompt
3. **重新生成完整的 `ppt_plan.md`**（不做增量修改，始终写入完整文件）

---

## 示例与参考

详细的输入输出示例见 [examples.md](examples.md)。

---

## 使用场景

### 独立使用

本技能可以独立使用，用于将用户的想法、大纲文本或完整描述转化为结构化的 `ppt_plan.md` 文件：

```
调用 planner 模块，根据以下信息生成完整大纲和页面描述：
- 用户需求：{用户原始需求}
- 页数要求：{N} 页
```

### 与 pptx 技能配合使用

本技能也是 `pptx` 技能的子技能，负责大纲构建阶段：

当用户在 pptx 技能中请求生成大纲时，pptx 技能会调用本技能来：

1. 根据用户输入（想法/大纲文本/描述）生成结构化大纲 JSON
2. 逐页生成详细的页面描述
3. 将结果整合写入 `ppt_plan.md` 文件

`ppt_plan.md` 将作为 pptx 技能后续生成 HTML 幻灯片的唯一依据。

---

## 风格配置

本技能在生成视觉方案时，需要了解可用的 PPT 风格。

### 配置文件位置

风格配置位于 [styles.json](styles/styles.json)。

### 读取风格配置

在视觉方案设计开始时，先读取风格配置：

```
file_read: styles/styles.json
```

### 风格匹配逻辑

根据用户输入的关键词，匹配 styles.json 中的 `keywords` 字段：

- 用户说"华为风格" → 匹配 id: "huawei"
- 用户说"企业汇报风格" → 匹配 keywords 包含"企业汇报"的风格

---

## 输入输出路径约定

### 概述

本技能通过 prompt 中的路径参数确定输入输出位置。调用方必须显式指定输出路径。

### 路径格式要求

- 统一使用**相对于 skill 根目录的相对路径**
- 所有输入、输出和风格配置路径都以 skill 根目录为基准

### 必需参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `output_path` | string | ppt_plan.md 的输出路径（相对于 skill 根目录） |

### 可选参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `research_path` | string | 研究报告路径，用于参考素材 |

### 输入文件

- `{research_path}` - 研究报告（如存在则读取）

### 输出产物

- `{output_path}` - 大纲文件

### 目录处理

如输出目录不存在，本技能将**自动创建目录**。

### 错误处理

如调用时未提供 `output_path`，本技能将报错并终止：

```
错误：未指定输出路径 (output_path)。

本技能需要以下必需参数：
- output_path: ppt_plan.md 的输出路径（相对于 skill 根目录）

正确调用示例：
"请帮我制作大纲，主题是「AI 报告」，输出到 output/ppt_plan.md"
```

### 调用示例

**通过 pptx-craft 调用**：

```
请帮我制作演示文稿大纲。
主题：{topic}
页数：{page_count}
风格：{style_id}
输出路径：{output_path}
研究报告路径：{research_path}
```

**独立使用**：

```
请帮我制作一份演示文稿大纲。
要求：
- 主题：数字化转型
- 页数：10 页
- 风格：huawei
- 输出路径：output/ppt_plan.md
```
