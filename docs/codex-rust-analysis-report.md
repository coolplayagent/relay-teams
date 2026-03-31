# Codex Rust 实现分析报告

## 1. 报告范围

本报告基于本地分析环境中的 npm `@openai/codex 0.117.0` 及其对应 Rust 源码 `rust-v0.117.0`。

- npm 包入口位于 `codex-cli/bin/codex.js`。
- 主要实现位于 `codex-rs/`。
- 本报告分析的源码根目录为 `/tmp/openai-codex`。

本报告重点覆盖以下主题：

1. 上下文压缩的技术原理、触发条件和实现流程。
2. LLM API 失败后的重试策略与回退机制。
3. 长期记忆系统的压缩、检索、排序与遗忘流程。
4. `tool`、`skill`、`app` 的检索、选择、排序与注入机制。
5. BM25 在 Codex Rust 中的实际使用位置与方式。
6. background terminal 的体系结构与关键启发式实现。

## 2. 总体结论

从整体设计看，Codex Rust 并不是一个“到处都用复杂检索算法”的系统。它的大部分“智能”来自以下几类机制：

- LLM 总结与转写：用于上下文压缩、memory phase-1/phase-2。
- SQL 排序与规则过滤：用于长期记忆筛选、遗忘、候选集合维护。
- 路径规则与精确匹配：用于 skill 的显式/隐式调用。
- 轻量 fuzzy match：用于 TUI 的 mention popup。
- 进程管理启发式：用于 unified exec/background terminal。

真正显式使用 BM25 的地方只有一处，即 `tool_search`。它用于当 app tools 数量较大时，对 app/connectors 工具做检索召回。memory、skill、background terminal、上下文压缩都没有使用 BM25，也没有使用 embedding、向量索引或 ANN 检索。

关键源码位置：

- BM25 使用：`/tmp/openai-codex/codex-rs/core/src/tools/handlers/tool_search.rs`
- skill fuzzy match：`/tmp/openai-codex/codex-rs/utils/fuzzy-match/src/lib.rs`
- memory 排序：`/tmp/openai-codex/codex-rs/state/src/runtime/memories.rs`

## 3. 模块视角下的系统结构

可以把本次分析涉及的能力分成五条主线：

1. 短期上下文管理
   - 当前线程历史如何估算 token。
   - 何时触发 compact。
   - compact 后如何重建 replacement history。

2. 长期记忆管理
   - 如何把 rollout 提炼成 stage-1 raw memory。
   - 如何把多个 raw memory 合并成 `MEMORY.md`、`memory_summary.md`、`skills/`。
   - 运行时如何“读记忆”而不是“把全部记忆塞进 prompt”。

3. 工具检索与暴露
   - app tools 少时直接暴露。
   - app tools 多时通过 `tool_search` 间接检索。

4. 技能选择与注入
   - 技能目录如何扫描。
   - 技能如何按元数据压缩进 instructions。
   - 显式与隐式 skill invocation 如何判定。

5. 后台终端与执行会话
   - `exec_command` / `write_stdin` 如何组成可持续的终端会话。
   - 如何做输出缓冲、长轮询、剪枝与审批复用。

## 4. 特殊算法清单

### 4.1 明确使用的算法/策略

- BM25
  - 只用于 `tool_search`。
- 大小写不敏感子序列模糊匹配
  - 用于 TUI 中 skill/plugin/app mention popup。
- LRU 风格回收策略
  - 用于 unified exec 进程会话剪枝。
- Head/Tail 缓冲
  - 用于 background terminal 长输出保留。
- 基于 usage 和 recency 的 SQL 排序
  - 用于 memory phase-2 选择。

### 4.2 未发现的算法

以下能力在本次分析范围内未发现实际使用：

- embedding 检索
- 向量数据库
- FAISS / HNSW / ANN
- cosine similarity rerank
- PageRank / graph ranking
- 学习到的 reranker

## 5. 上下文压缩

这里的“上下文压缩”是线程历史压缩，不是长期记忆系统。

### 5.1 触发方式

Codex 会持续估算当前线程历史占用的 token。当累计上下文过大并超过模型的自动压缩阈值时，会触发 compact。

压缩分两条路径：

- OpenAI provider：优先走远端 compact。
- 非 OpenAI provider：走本地 inline compact。

判断入口：

- `/tmp/openai-codex/codex-rs/core/src/compact.rs`
- `/tmp/openai-codex/codex-rs/core/src/compact_remote.rs`

### 5.2 本地 inline compact 的原理

本地 compact 的核心做法不是“纯规则裁剪”，而是让模型先生成一段 summary，再把旧历史替换成一个更短的 replacement history。

实现过程：

1. 构造 compact prompt。
2. 把当前历史和合成 prompt 一起发给模型。
3. 如果 compact 请求本身塞不进上下文窗口，就从最老的历史开始删。
4. 拿到模型输出的 summary。
5. 用 `recent user messages + summary` 重建历史。

其中 replacement history 的构造逻辑很关键：

- 最近用户消息按从近到远回溯累计。
- 总预算上限是 `20_000` tokens。
- 如果最后一条还能部分保留，就截断后保留。
- summary 会作为一条新的 user message 插入历史。

关键实现：

- `build_compacted_history()`：`/tmp/openai-codex/codex-rs/core/src/compact.rs#L324`
- 最近用户消息预算：`/tmp/openai-codex/codex-rs/core/src/compact.rs#L337`

这意味着本地 compact 的真实语义是：

“保留少量最近 user turns，并用一条 handoff summary 代替大部分旧历史”。

### 5.3 远端 compact 的原理

OpenAI provider 的 compact 不是本地拼 summary，而是调用模型客户端的 `compact_conversation_history()`。

实现过程：

1. 克隆当前历史。
2. 若 compact 请求过大，先尝试删除尾部 Codex 自己生成的 item。
3. 组装 compact prompt，包含：
   - input history
   - base instructions
   - 当前可见 tools
   - `parallel_tool_calls`
   - reasoning 相关设置
4. 调用远端 compact 接口。
5. 对返回的 compacted transcript 做清洗。
6. 重新注入当前 session 的 canonical initial context。

关键点在第 2 步和第 5 步：

- 第 2 步不是盲删旧 user 消息，而是优先删 Codex 自己生成的尾部 item。
- 第 5 步会过滤掉 stale developer messages 和非真实用户消息，避免压缩结果把过期上下文重新带回来。

关键实现：

- 远端 compact 主流程：`/tmp/openai-codex/codex-rs/core/src/compact_remote.rs#L68`
- compact 前裁剪 codex-generated items：`/tmp/openai-codex/codex-rs/core/src/compact_remote.rs#L277`

### 5.4 上下文压缩的设计取舍

这套设计的核心取舍有三点：

1. 不追求严格保留全部历史，而是保留“最近用户意图 + handoff summary”。
2. 对 OpenAI provider，优先让服务端生成更合适的压缩转写。
3. 压缩完成后重新注入 canonical context，防止 developer/system 指令漂移。

因此，Codex 的 compact 更像“线程级 checkpoint”而不是“通用摘要器”。

## 6. LLM API 失败重试

Codex 的重试分成两层：传输层 retry 和 turn/stream 层 retry。

### 6.1 传输层 retry

底层通用 retry 在 `codex-client/src/retry.rs` 中实现。

关键行为：

- `max_attempts`
- 指数退避
- 0.9 到 1.1 的随机抖动
- 可配置是否重试 429、5xx、transport error

实现：

- retry policy 定义：`/tmp/openai-codex/codex-rs/codex-client/src/retry.rs#L8`
- 指数退避与 jitter：`/tmp/openai-codex/codex-rs/codex-client/src/retry.rs#L38`
- retry 主循环：`/tmp/openai-codex/codex-rs/codex-client/src/retry.rs#L49`

默认 provider 配置：

- `request_max_retries = 4`
- `stream_max_retries = 5`
- `base_delay = 200ms`
- `retry_429 = false`
- `retry_5xx = true`
- `retry_transport = true`

关键实现：

- 默认重试常量：`/tmp/openai-codex/codex-rs/core/src/model_provider_info.rs#L22`
- API provider retry config：`/tmp/openai-codex/codex-rs/core/src/model_provider_info.rs#L175`

一个重要细节是：`run_with_retry()` 使用 `for attempt in 0..=max_attempts`，因此 `max_attempts = 4` 实际表示“1 次初始请求 + 最多 4 次重试”。[ `/tmp/openai-codex/codex-rs/codex-client/src/retry.rs#L58` ]

### 6.2 turn/stream 层 retry

在真正的 sampling request 流程中，如果错误被标记为 retryable，Codex 还会做 turn 级别的重试。

关键规则：

- `CodexErr::Stream` 视为可重试。
- `ContextWindowExceeded` 不可重试。
- `QuotaExceeded`、`InvalidRequest`、`UsageNotIncluded` 等不可重试。

关键实现：

- `CodexErr::Stream` 定义：`/tmp/openai-codex/codex-rs/core/src/error.rs#L70`
- `is_retryable()`：`/tmp/openai-codex/codex-rs/core/src/error.rs#L196`
- sampling request retry 主循环：`/tmp/openai-codex/codex-rs/core/src/codex.rs#L6410`

### 6.3 rate limit 特殊处理

SSE 流中如果收到 `response.failed`，Codex 会按错误类型分类：

- context window overflow
- quota exceeded
- invalid prompt
- server overloaded
- 普通 retryable error

如果错误码是 `rate_limit_exceeded`，还会尝试从错误消息文本中解析 retry-after delay，例如：

- `11.054s`
- `300ms`

关键实现：

- `response.failed` 分类：`/tmp/openai-codex/codex-rs/codex-api/src/sse/responses.rs#L274`
- `try_parse_retry_after()`：`/tmp/openai-codex/codex-rs/codex-api/src/sse/responses.rs#L436`

随后 turn 层 retry 会优先尊重服务端给出的 delay；如果没有，就使用本地 backoff。

关键实现：

- turn retry delay 计算：`/tmp/openai-codex/codex-rs/core/src/codex.rs#L6462`

### 6.4 WebSocket 到 HTTPS 的回退

`ModelClientSession` 是 turn-scoped，会优先使用 Responses WebSocket。如果流式重试预算耗尽且 provider 支持 fallback，就切回 HTTPS Responses API。

关键实现：

- turn-scoped session 及 sticky routing：`/tmp/openai-codex/codex-rs/core/src/client.rs#L184`
- stream 优先 WS，失败时 fallback：`/tmp/openai-codex/codex-rs/core/src/client.rs#L1282`
- sampling request 中的 fallback：`/tmp/openai-codex/codex-rs/core/src/codex.rs#L6444`

这使得 Codex 的重试不只是“重发请求”，而是“同 turn 内带状态的 transport 降级重试”。

## 7. 长期记忆系统

长期记忆和上下文压缩是两套不同机制。

- 上下文压缩：解决当前线程太长。
- 长期记忆：解决跨线程、跨时间的知识留存。

### 7.1 memory 的总体结构

memory 目录位于 `<codex_home>/memories`，主要包含：

- `memory_summary.md`
- `MEMORY.md`
- `raw_memories.md`
- `rollout_summaries/`
- `skills/`

memory 子系统分两阶段：

1. Phase 1：从单个 rollout 提取结构化记忆。
2. Phase 2：把多个 stage-1 输出整合成文件化的长期记忆工作区。

关键实现：

- memory 模块总入口：`/tmp/openai-codex/codex-rs/core/src/memories/mod.rs`
- pipeline 说明：`/tmp/openai-codex/codex-rs/core/src/memories/README.md`

### 7.2 Phase 1：rollout extraction

Phase 1 会从 state DB 中挑选符合条件的 rollout：

- thread 不是当前线程
- `memory_mode = enabled`
- rollout 已经足够“冷”
- 在允许的年龄窗口内
- 没有被其他 worker 持有 lease

核心流程：

1. claim 一批 rollout jobs。
2. 读取 rollout jsonl。
3. 过滤不适合进入记忆的内容。
4. 把 rollout 内容截断到模型有效窗口的 70%。
5. 让模型流式输出结构化 JSON：
   - `raw_memory`
   - `rollout_summary`
   - `rollout_slug`
6. 对输出做 secret redact。
7. 落回 `stage1_outputs`。

关键实现：

- phase1 入口：`/tmp/openai-codex/codex-rs/core/src/memories/phase1.rs#L81`
- stage1 prompt 构造与流式调用：`/tmp/openai-codex/codex-rs/core/src/memories/phase1.rs#L313`
- rollout 内容过滤：`/tmp/openai-codex/codex-rs/core/src/memories/phase1.rs#L466`
- 70% 截断：`/tmp/openai-codex/codex-rs/core/src/memories/prompts.rs#L129`
- 默认截断参数：`/tmp/openai-codex/codex-rs/core/src/memories/mod.rs#L32`

这里的“压缩”本质是 LLM 语义压缩，而不是算法式摘要提取。

### 7.3 Phase 2：global consolidation

Phase 2 负责从多个 stage-1 outputs 中选出值得长期保留的一批，再让 consolidation agent 生成高层记忆文件。

Phase 2 的关键不是“再做一次语义检索”，而是“基于 usage 和 recency 做候选排序，再让 agent 做语义整合”。

流程如下：

1. claim 全局 phase-2 job。
2. 从 DB 取当前选择集 `selected`。
3. 对比上一次成功 phase-2 的 `previous_selected`。
4. 构造 `added / retained / removed` diff。
5. 把 union 集合落盘为：
   - `raw_memories.md`
   - `rollout_summaries/`
6. 生成 consolidation prompt。
7. 启动受限 subagent，在 memory workspace 内写：
   - `MEMORY.md`
   - `memory_summary.md`
   - `skills/`
8. 成功后重写 `selected_for_phase2` baseline。

关键实现：

- phase2 主流程：`/tmp/openai-codex/codex-rs/core/src/memories/phase2.rs#L41`
- selection -> artifacts：`/tmp/openai-codex/codex-rs/core/src/memories/phase2.rs#L79`
- 保留当前选择和 previous baseline 的 union：`/tmp/openai-codex/codex-rs/core/src/memories/phase2.rs#L163`

### 7.4 memory 的排序算法

Phase 2 候选选择完全依赖 SQL 排序，不依赖 BM25 或向量相似度。

当前选择的 SQL 逻辑：

- 保留非空 `stage1_outputs`
- 只保留仍然在可用时间窗口内的 memory
- 对从未使用过的 memory，用 `source_updated_at` 作为 recency 回退
- 排序规则：
  1. `usage_count DESC`
  2. `COALESCE(last_usage, source_updated_at) DESC`
  3. `source_updated_at DESC`
  4. `thread_id DESC`

关键实现：

- `get_phase2_input_selection()`：`/tmp/openai-codex/codex-rs/state/src/runtime/memories.rs#L343`

这说明 memory 排序本质是：

- 先看“曾经有没有真正帮到当前模型”。
- 再看“最近有没有被使用”。
- 再看“原始线程是否较新”。

### 7.5 memory 的检索流程

运行时并不存在一个专门的 `memory search engine`。Codex 采用的是“memory summary 预注入 + prompt 约束 + 普通文件搜索”的轻量 recall 流程。

具体机制：

1. 每个 turn 构造 developer instructions 时，若开启 memory tool，则读取 `memory_summary.md`。
2. `memory_summary.md` 最多注入 5000 tokens。
3. prompt 模板要求模型先读 summary，再按关键词去搜 `MEMORY.md`。
4. 只有当 `MEMORY.md` 明确指向 `rollout_summaries/` 或 `skills/` 时，才继续打开这些更细的文件。

关键实现：

- memory prompt 注入入口：`/tmp/openai-codex/codex-rs/core/src/codex.rs#L3510`
- summary 截断到 5000 tokens：`/tmp/openai-codex/codex-rs/core/src/memories/prompts.rs#L163`
- quick memory pass 指令模板：`/tmp/openai-codex/codex-rs/core/templates/memories/read_path.md#L21`

因此，memory 的 recall 机制可以概括为：

“由 LLM 根据 summary 提取关键词，再通过文件系统做受约束的逐层下钻，而不是由系统做独立语义检索。”

### 7.6 memory 的强化与遗忘

memory 的 `usage_count` 不是看文件有没有被打开，而是看模型最终是否在回答中引用了 memory citation。

流程如下：

1. memory prompt 要求模型在使用 memory 时输出 `<oai-mem-citation>`。
2. 后处理解析 citation 中的 `rollout_ids`。
3. 对对应 `stage1_outputs` 做：
   - `usage_count += 1`
   - `last_usage = now`
4. 下一轮 phase-2 选择会更偏向这些被证明有价值的 memory。

关键实现：

- citation 解析：`/tmp/openai-codex/codex-rs/core/src/memories/citations.rs#L6`
- response item 完成后记录 usage：`/tmp/openai-codex/codex-rs/core/src/stream_events_utils.rs#L124`
- DB 更新：`/tmp/openai-codex/codex-rs/state/src/runtime/memories.rs#L85`

遗忘机制有两层：

1. retention prune
   - 对 `selected_for_phase2 = 0` 且太久没被使用的 stage1 outputs 删除。
2. polluted forgetting
   - 若线程因为 web search / MCP 被标记为 polluted，且它曾参与过 phase-2 baseline，则触发新一轮 consolidation，把依赖它的 memory 删除或改写。

## 8. Tool 检索与排序

### 8.1 app tools 的两种暴露方式

Codex 对 app tools 的暴露分两种模式：

1. 数量较少
   - 直接暴露给模型。
2. 数量较多
   - 不直接暴露，而是通过 `tool_search` 检索召回。

阈值是 100 个。

关键实现：

- 阈值定义：`/tmp/openai-codex/codex-rs/core/src/codex.rs#L427`
- 判断逻辑：`/tmp/openai-codex/codex-rs/core/src/codex.rs#L6594`

### 8.2 BM25 的使用位置

BM25 只在 `tool_search` handler 中使用。

实现依赖：

- `bm25::Document`
- `bm25::Language`
- `bm25::SearchEngineBuilder`

关键实现：

- 依赖引入：`/tmp/openai-codex/codex-rs/core/src/tools/handlers/tool_search.rs#L13`
- 建索引与搜索：`/tmp/openai-codex/codex-rs/core/src/tools/handlers/tool_search.rs#L79`

### 8.3 BM25 的文档构造方式

每个工具会被转成一份搜索文档，字段包括：

- handler key/name
- `tool_name`
- `server_name`
- `title`
- `description`
- `connector_name`
- `connector_description`
- input schema 中 `properties` 的字段名

关键实现：

- `build_search_text()`：`/tmp/openai-codex/codex-rs/core/src/tools/handlers/tool_search.rs#L147`

这说明 `tool_search` 的 BM25 不是在搜“工具源码”，而是在搜“工具元数据 + 参数字段名”。

### 8.4 BM25 检索流程

完整流程：

1. 校验 `query` 非空。
2. 取全部 app tools。
3. 为保证稳定性，先按 key 排序。
4. 把每个工具构造成一份 BM25 文档。
5. 用 `Language::English` 建立 search engine。
6. 调用 `search(query, limit)`。
7. 把命中的工具按 `tool_namespace` 分组。
8. 返回 namespace 级别的结构，而不是单个工具平铺列表。

关键实现：

- handler 主流程：`/tmp/openai-codex/codex-rs/core/src/tools/handlers/tool_search.rs#L43`
- namespace 分组：`/tmp/openai-codex/codex-rs/core/src/tools/handlers/tool_search.rs#L100`

### 8.5 BM25 结果的真实排序语义

这里有一个实现细节非常重要：

- BM25 首先决定“哪些工具 entry 被召回”。
- 但最终返回给模型时，这些结果会进入 `BTreeMap`，按 namespace 有序分组。

因此，最终语义并不是严格的“按 BM25 分数从高到低输出整个结果集”，而是：

“先用 BM25 做召回，再用 namespace 结构组织返回结果。”

这对模型的影响是：

- 更容易看到同一 connector 下的工具集合。
- 更像“命中一个 connector namespace”，而不是“命中一个具体工具”。

## 9. Skill 的压缩、检索、排序与路由

skill 系统分为四件事：

1. skill 目录发现与排序
2. skill 元数据压缩注入
3. 显式 skill 选择
4. 隐式 skill 触发

### 9.1 skill discovery 与排序

skills loader 会从多个 root 扫描技能目录：

- repo scope
- user scope
- system scope
- admin scope
- plugin skill roots
- `.agents/skills`

扫描特征：

- BFS 队列
- 单 root 最大深度 6
- 单 root 最多 2000 个目录
- 路径去重后再排序
- 排序规则：
  1. `Repo`
  2. `User`
  3. `System`
  4. `Admin`
  5. 同 scope 下按 `name`
  6. 再按 `path`

关键实现：

- 扫描与排序：`/tmp/openai-codex/codex-rs/core-skills/src/loader.rs#L181`
- BFS 扫描限制：`/tmp/openai-codex/codex-rs/core-skills/src/loader.rs#L385`

这套排序很重要，因为显式 skill mention 的最终保留顺序会继承 `skills` 原顺序。

### 9.2 skill 的“压缩”

skill 的压缩不是摘要算法，而是“只把 skill 元数据而不是完整内容预先塞进系统指令”。

Codex 预注入的 `skills section` 只包含：

- `name`
- `description`
- `file path`

并附带一套行为说明：

- 用户明确提到 skill 或任务明显匹配 skill 描述时才使用。
- 先打开 `SKILL.md`，按 progressive disclosure 继续读。
- 只读必要的引用文件，不要全量追踪。

关键实现：

- skills section 渲染：`/tmp/openai-codex/codex-rs/core-skills/src/render.rs#L5`

真正使用某个 skill 时，才会把对应 `SKILL.md` 的完整内容读出来并注入为 `SkillInstructions`。

关键实现：

- 读取并注入 skill：`/tmp/openai-codex/codex-rs/core-skills/src/injection.rs#L24`

所以 skill 的压缩模式可以概括为：

“默认只暴露技能目录摘要，命中后再按需展开完整指令。”

### 9.3 显式 skill 选择算法

显式 skill 选择通过 `collect_explicit_skill_mentions()` 完成。

处理顺序：

1. 先处理结构化 `UserInput::Skill`
   - 按 path 精确匹配
   - disabled path 跳过
   - 已选过 path 跳过
2. 再处理文本里的 `$skill-name` 或 `[$skill-name](path)`
   - 先按 path 命中
   - 再按 plain name 命中
   - plain name 只有在“技能名唯一且没有 connector slug 冲突”时才允许

关键实现：

- 显式 skill 选择入口：`/tmp/openai-codex/codex-rs/core-skills/src/injection.rs#L100`
- 文本 mention 提取：`/tmp/openai-codex/codex-rs/core-skills/src/injection.rs#L230`
- plain name 唯一性和 connector 冲突过滤：`/tmp/openai-codex/codex-rs/core-skills/src/injection.rs#L359`

这个算法的本质不是搜索，而是：

- 路径精确匹配优先
- 普通名字匹配需要无歧义
- 结果顺序保持原 `skills` 顺序

### 9.4 隐式 skill invocation

隐式 skill invocation 不是语义推断，而是命令模式识别 + 路径索引。

启动时，allowed skills 会建立两份索引：

- `scripts/` 目录索引
- `SKILL.md` 文档路径索引

关键实现：

- 索引构造：`/tmp/openai-codex/codex-rs/core-skills/src/invocation_utils.rs#L8`
- 仅允许 enabled 且允许 implicit invocation 的 skill：`/tmp/openai-codex/codex-rs/core-skills/src/model.rs#L110`

隐式匹配流程：

1. 用 `shlex` 或空白分词切命令。
2. 判断是否是常见脚本运行命令：
   - `python`
   - `bash`
   - `node`
   - `pwsh`
   - 等
3. 若是脚本运行，则把脚本路径 canonicalize 后向上找祖先，看是否落到某个 skill 的 `scripts/` 目录内。
4. 若不是脚本运行，再判断是否是常见读文件命令：
   - `cat`
   - `sed`
   - `head`
   - `tail`
   - `less`
   - 等
5. 若命令读取了某个具体 `SKILL.md` 路径，则命中对应 skill。

关键实现：

- 隐式匹配主入口：`/tmp/openai-codex/codex-rs/core-skills/src/invocation_utils.rs#L29`
- script runner 识别：`/tmp/openai-codex/codex-rs/core-skills/src/invocation_utils.rs#L50`
- 脚本路径祖先回溯：`/tmp/openai-codex/codex-rs/core-skills/src/invocation_utils.rs#L82`
- 文档读取匹配：`/tmp/openai-codex/codex-rs/core-skills/src/invocation_utils.rs#L105`

### 9.5 TUI 的 fuzzy match

TUI 中 skill/plugin/app mention popup 使用的是一个轻量模糊匹配算法，不是 BM25。

算法特征：

- 大小写不敏感
- 子序列匹配
- 返回命中的字符索引，用于高亮
- score 越小越好
- prefix 命中额外减 100 分
- contiguous match 优于 spread-out match

关键实现：

- fuzzy match 算法：`/tmp/openai-codex/codex-rs/utils/fuzzy-match/src/lib.rs#L1`
- popup 排序：`/tmp/openai-codex/codex-rs/tui/src/bottom_pane/skill_popup.rs#L130`

注意一个实现细节：popup 的排序是 `sort_rank -> fuzzy score -> display_name`。其中：

- `sort_rank` 先决定插件/skill/app 的大类优先级。
- `fuzzy score` 再决定同类中的近似度顺序。

## 10. Background Terminal / Unified Exec

background terminal 本质上不是一个单独子系统，而是 `unified_exec` 这套交互式进程会话框架。

### 10.1 核心设计

`unified_exec` 负责：

- 创建交互式进程
- 复用会话
- 输出缓冲
- 审批与 sandbox 编排
- sandbox denial 回退

模块说明已经直接写在源码顶部：

- `/tmp/openai-codex/codex-rs/core/src/unified_exec/mod.rs#L1`

默认常量：

- 普通最小 yield：250ms
- 空轮询最小 yield：5000ms
- 非空写入最大等待：30000ms
- 后台终端默认最大轮询：300000ms
- 输出缓冲上限：1 MiB
- 最大后台进程数：64

关键实现：

- 常量定义：`/tmp/openai-codex/codex-rs/core/src/unified_exec/mod.rs#L59`

### 10.2 approval cache 的命令规范化

为了复用审批结果，unified exec 不直接用原始 argv 做缓存 key，而是先 canonicalize。

典型作用：

- `/bin/bash -lc` 和 `bash -lc` 归一化
- shell wrapper 提取实际脚本文本
- PowerShell wrapper 归一化

关键实现：

- canonicalization：`/tmp/openai-codex/codex-rs/core/src/command_canonicalization.rs#L8`
- 作为 approval key 一部分使用：`/tmp/openai-codex/codex-rs/core/src/tools/runtimes/unified_exec.rs#L67`

这是一种典型的工程启发式，不是检索算法，但它直接影响 background terminal 的审批复用效率。

### 10.3 sandbox denial heuristic

unified exec 的一大特点是审批与 sandbox 编排统一交给 orchestrator。若第一次执行看起来像 sandbox denial，则在策略允许时自动改为无 sandbox 重试，并尽量避免重复打扰用户。

这部分在 unified exec 模块文档中有明确说明：

- `/tmp/openai-codex/codex-rs/core/src/unified_exec/mod.rs#L5`

因此 unified exec 的“重试”不是单纯 I/O 重试，而是“策略级 fallback”。

### 10.4 HeadTailBuffer

长输出不会无上限缓存，而是进入 `HeadTailBuffer`。

算法规则：

- 缓冲总上限固定。
- 前 50% 预算保留 head。
- 后 50% 预算保留 tail。
- 中间内容直接丢弃。
- 如果某个 tail chunk 超过整个 tail 预算，只保留它的最后一段。

关键实现：

- buffer 设计：`/tmp/openai-codex/codex-rs/core/src/unified_exec/head_tail_buffer.rs#L4`
- 50/50 预算：`/tmp/openai-codex/codex-rs/core/src/unified_exec/head_tail_buffer.rs#L31`

这就是 background terminal 的“输出压缩”机制。它不是文本摘要，而是 head/tail retention。

### 10.5 UTF-8 安全的流式分块

输出 watcher 会不断读取 PTY 输出，并把它按 UTF-8 边界拆成 delta event。

规则：

- 单个 delta 最多 8192 bytes。
- 不切断 UTF-8 字符。
- 如果当前 buffer 找不到合法 UTF-8 前缀，也会至少吐出一个 byte 以保证进度。

关键实现：

- delta 上限：`/tmp/openai-codex/codex-rs/core/src/unified_exec/async_watcher.rs#L29`
- watcher 主循环：`/tmp/openai-codex/codex-rs/core/src/unified_exec/async_watcher.rs#L37`
- UTF-8 安全切分：`/tmp/openai-codex/codex-rs/core/src/unified_exec/async_watcher.rs#L278`

### 10.6 长轮询与后台保活

后台终端的核心交互模式不是持续推流，而是：

- `exec_command` 启动
- 如进程未结束则返回 `session_id`
- 后续通过 `write_stdin`：
  - 非空输入：真正写 stdin
  - 空输入：长轮询新输出

为保证后台进程在 turn 中断后也不会立刻消失，进程会在初始等待前就先被存进 process store。

关键实现：

- 先存 live session 再等待：`/tmp/openai-codex/codex-rs/core/src/unified_exec/process_manager.rs#L198`
- `write_stdin` 的空轮询 yield 裁剪：`/tmp/openai-codex/codex-rs/core/src/unified_exec/process_manager.rs#L382`

### 10.7 进程剪枝策略

当 unified exec 会话数达到上限后，会触发剪枝。

算法：

1. 若总数未达 64，不剪。
2. 先按 recency 降序排序。
3. 保护最近 8 个会话。
4. 再按 LRU 升序排序。
5. 优先删除“不在保护集合中且已经退出”的会话。
6. 若没有，则删最老的非保护会话。

关键实现：

- 剪枝入口：`/tmp/openai-codex/codex-rs/core/src/unified_exec/process_manager.rs#L837`

这是一种典型的“保护最近热点 + 优先回收冷退出对象”的 LRU 变体。

## 11. 各子系统中的压缩、检索、排序总表

| 子系统 | 压缩方式 | 检索方式 | 排序方式 | 核心算法 |
| --- | --- | --- | --- | --- |
| 上下文 compact | summary + recent user turns；或远端 transcript rewrite | 无独立检索 | 最近用户优先，必要时删最老历史 | LLM 总结、token 预算裁剪 |
| 长期 memory | stage-1 raw memory + phase-2 consolidation | summary -> MEMORY.md -> rollout/skills 的 prompt 引导式读取 | `usage_count` + `last_usage/source_updated_at` SQL 排序 | LLM 总结、SQL 排序 |
| tool_search | 不压缩历史，只压缩工具元数据为搜索文档 | BM25 检索 app tools | 先 BM25 召回，再按 namespace 分组 | BM25 |
| skill | 只预注入 name/description/path，命中后再展开 SKILL.md | 路径精确匹配、名字唯一性匹配、命令模式匹配 | loader 的 scope/name/path 顺序；TUI 用 fuzzy score | 规则匹配、fuzzy subsequence |
| background terminal | Head/Tail 输出缓冲 | 无检索 | LRU 风格剪枝 | HeadTailBuffer、LRU 变体 |

## 12. 最关键的技术判断

### 12.1 Codex Rust 的“搜索”主要有三种，不是一种

1. `tool_search` 的 BM25
2. skill/app mention popup 的 fuzzy subsequence
3. memory 的 prompt 引导式文件搜索

这三者分别解决：

- 工具召回
- UI 输入过滤
- 长期经验回忆

它们彼此独立，且没有统一抽象成一个通用检索引擎。

### 12.2 memory 不是传统 RAG

memory 没有 embedding，也没有向量召回。它更接近：

- 先做 per-rollout 总结
- 再做全局 handbook consolidation
- 运行时让模型自己按目录结构和关键词去读文件

因此它本质上是“文件化、可增量维护的长期手册”，而不是“语义向量记忆库”。

### 12.3 compact 和 memory 不是同一层功能

compact 解决的是当前线程上下文窗口问题。

memory 解决的是跨线程、跨时间的经验留存问题。

二者都用了 LLM 总结，但目标、输入、输出和调用时机完全不同。

### 12.4 background terminal 的“算法性”主要来自系统工程

background terminal 没有 BM25 或语义检索。它的关键价值来自：

- 命令 canonicalization
- sandbox denial heuristic
- head/tail 输出缓冲
- UTF-8 安全增量输出
- 长轮询 timeout 裁剪
- LRU 风格会话回收

这些都属于工程化启发式与资源约束设计。

## 13. 结论

如果从“是否用了特别算法”这个角度总结 Codex Rust：

- 明确使用 BM25 的地方只有 `tool_search`。
- memory 的核心不是 BM25，而是 LLM 总结 + SQL 排序 + prompt 驱动文件检索。
- skill 的核心不是语义搜索，而是目录元数据压缩、路径精确匹配、名字歧义消解和命令模式匹配。
- background terminal 的核心不是搜索算法，而是执行会话管理与输出缓冲策略。
- 整个系统最重要的设计思想不是“统一检索算法框架”，而是“不同子系统使用最小够用的机制”。

换句话说，Codex Rust 的能力分布非常工程化：

- 该用 BM25 的地方只在工具召回使用 BM25。
- 该用 LLM 总结的地方用 LLM 总结。
- 该用 SQL 排序的地方用 SQL 排序。
- 该用规则匹配的地方用规则匹配。

这种设计带来的好处是链路清晰、可解释性强、失败模式明确，也更容易做逐子系统演进。

