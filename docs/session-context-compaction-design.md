# Session Context Compaction Design

## 1. 背景

`agent-teams-main` 的单 session 上下文管理原先只有两层：

1. 读取会话历史后按安全边界裁剪，避免未闭合的 tool call 链进入模型。
2. 当历史 token 估算超过阈值时，直接触发 full compaction，把旧消息隐藏，并把滚动摘要注入 system prompt。

这套实现能工作，但在长线程下有三个明显问题：

- 大量旧工具输出会直接挤占上下文窗口，导致 full compaction 触发过早。
- compaction 预算只看 history，不看 system prompt、tools/MCP/skills 描述、当前用户输入预留和输出预留。
- compact marker 的可观测性较弱，时间线里只看到通用的 `History compacted`。

本设计文档先对三套参考实现做对比，再给出本仓库的最终取舍和本次已落地的实现。

## 2. 范围与非目标

本文只讨论单 session 的短期上下文压缩，不覆盖：

- 跨 session 长期记忆
- 角色级 reflection memory
- RAG / 检索式记忆
- 新的用户命令或新的 `/api/*` 入口

## 3. 三套实现对比

### 3.1 `agent-teams-main` 原实现

主流程位于 `src/relay_teams/agents/execution/llm_session.py`：

```text
load history
-> truncate to safe boundary
-> maybe_compact_history
-> inject compaction summary into system prompt
-> build model settings
-> call model
```

full compaction 位于 `src/relay_teams/agents/execution/conversation_compaction.py`：

- 按固定比例估算阈值
- 重写滚动 markdown summary
- 创建 `COMPACTION` marker
- 把旧消息标记为 `hidden_from_context`
- 后续请求通过 system prompt 注入摘要

优点：

- 架构简单
- 不需要重建 transcript
- 与现有 message repository 集成成本低

缺点：

- 没有前置轻量压缩层
- 预算不看完整 prompt
- 工具输出和高价值对话历史被同等对待
- marker 观测信息不足

### 3.2 Codex

Codex 的 `ContextManager` 位于 `codex-rs/core/src/context_manager/history.rs`。

常态下它并不维护一个独立 `microcompact` 层，而是：

- 写入 history 时就截断大工具输出
- prompt 前做 history normalize
- 超过 `auto_compact_limit` 后执行一次 checkpoint compact

compact 主流程位于：

- `codex-rs/core/src/compact.rs`
- `codex-rs/core/src/compact_remote.rs`

它的核心语义是：

- 让模型生成 handoff summary
- 重建 replacement history
- 直接替换 session 的工作历史

优点：

- compact 边界很清楚
- transcript 语义干净
- resume / rollback 的上下文边界更自然

缺点：

- 没有 Claude Code 那种更细的在线压缩层
- 轻量治理主要体现在工具输出截断，不是独立热路径

### 3.3 Claude Code

Claude Code 的 query 热路径位于 `src/query.ts`，是典型分层管线：

```text
safe history
-> tool-result replacement
-> snip/collapse
-> microcompact
-> context collapse
-> auto compact
-> model call
```

关键实现：

- `src/services/compact/microCompact.ts`
- `src/services/compact/compact.ts`
- `src/services/SessionMemory/sessionMemory.ts`

优点：

- 在线压缩层次最完整
- 更 cache-aware
- 旧工具输出有专门治理路径
- full compaction 触发频率更低

缺点：

- 体系更复杂
- session memory、prompt cache 和 compact 之间有更多状态联动

## 4. 最终取舍

本仓库最终采用的方向是：

- 借 Claude Code 的热路径思路，把 `microcompact` 放到 full compaction 之前。
- 保留当前仓库已有的 rolling summary + hidden history 机制，作为本阶段的持久压缩语义。
- 参考 Codex 的 checkpoint 思路增强 marker 元数据和时间线语义，但本阶段不直接切换到“replacement history 替换 session transcript”。

换句话说，本次实现落地的是一个阶段化方案：

### Phase 1：本次已实现

- 统一 prompt-view 准备流程，并复用到初次尝试、injection restart、validation restart
- 新增 deterministic `microcompact`
- 用完整 prompt 预算驱动 full compaction
- 扩展 compaction metadata 与时间线标签

### Phase 2：后续建议

- 引入显式 checkpoint replacement history
- 将 compact 的主表示从 “summary 注入 system prompt” 进一步演进为“显式 compacted transcript”

### Phase 3：后续建议

- 增加异步 session notes / sidecar memory
- 增强 prompt cache / token cache telemetry

## 5. 本次实现后的主流程

本次提交后，主线程 session 的 prompt 准备流程变为：

```text
publish MODEL_STEP_STARTED
load conversation history
-> truncate to safe boundary
-> snapshot original history for later summary source
-> inject existing compaction summary into provisional system prompt
-> build full prompt budget
-> microcompact old tool results in prompt view
-> if still above threshold then full compaction
   - plan on live prompt-view history
   - rewrite rolling summary from original history
-> coerce the visible suffix to a provider-replayable history
-> rebuild final system prompt with latest summary
-> build model settings / agent iteration context
-> call model
```

对应入口位于 `src/relay_teams/agents/execution/llm_session.py` 中的 `_prepare_prompt_context(...)`。
真正的每轮迭代重建则统一走 `_build_agent_iteration_context(...)`。

## 6. 关键设计

### 6.1 `microcompact`

新模块：`src/relay_teams/agents/execution/conversation_microcompact.py`

职责：

- 只作用于发送给模型的 prompt view
- 不写回数据库
- 只处理旧的 `ToolReturnPart`
- 不处理最近保护尾部
- 不跨越未闭合的 tool call / tool return 链

压缩方式：

- 仅替换大体积旧工具结果
- 占位文本固定、可预测、可测试
- 保留工具名、原始 token 估算、前后预览片段

这样做的目的不是“生成摘要”，而是先把低价值的大输出从热路径里移走，尽量减少 full compaction 的触发频率。

这里还有一个实现细节需要明确：

- `microcompact` 只改变 live prompt view
- full compaction 的 rolling summary 不直接消费这个 preview 版本
- session 会在 `microcompact` 之前保留一份原始 `source_history`
- 当真正触发 rolling-summary compaction 时，预算规划和 kept suffix 仍基于 live history，
  但 summary rewrite 改为读取原始 `source_history`

这样可以避免 preview-only 的前后片段在 full compaction 之前就污染摘要来源，
导致工具输出中间的关键信息被永久丢失。

### 6.2 完整 prompt 预算

新预算不再只看 history，而是显式纳入：

- `system_prompt`
- 当前用户输入预留
- builtin tools / MCP / skills 的上下文开销
- 输出预留

实现上新增了 `ConversationCompactionBudget`，其阈值由完整 prompt 预算推导，而不是由 history 单独推导。

### 6.3 full compaction 仍采用 rolling summary

本阶段没有直接替换为 Codex 风格的 replacement history。

原因：

- 当前仓库已有 `hidden_from_context` + marker + summary 注入路径
- 直接切换 transcript 语义会牵涉更大范围的恢复、时间线和历史投影逻辑

因此本次落地做的是：

- 在 full compaction 之前新增 `microcompact`
- 把 full compaction 的触发判断改成基于完整 prompt 预算
- 给 marker 补足后续迁移到 checkpoint 语义所需的基础元数据

同时，full compaction 现在采用一个明确的双视图策略：

- `history`：发送给 provider 的 live prompt view，也是 compaction planner 看到的视图
- `source_history`：`microcompact` 之前保存的原始消息视图，只用于 rolling summary rewrite

这样可以同时满足两件事：

- token 预算、保留尾部和最终 provider 输入保持一致
- rolling summary 仍然基于完整工具输出，而不是 preview 版替身

### 6.4 统一迭代上下文重建

这次实现里，首次尝试、injection restart 和 committed tool validation restart
都统一走 `_build_agent_iteration_context(...)`。

这意味着每次真正进入下一轮 `agent.iter(...)` 之前，都会重新：

- 从 repository 读取安全边界历史
- 重建 provisional / final system prompt
- 重新执行 `microcompact`、full compaction、replay repair
- 按压缩后的最新预算重建 model settings

这样做的目的，是避免只有首次尝试用了压缩后的 prompt view，
而后续 restart 又因为重新加载 repository 原始历史而退回大体积未压缩 history。

同时，`MODEL_STEP_STARTED` 会在 prompt preparation 之前发布。
这保证了如果新 attempt 在 prompt prep 阶段失败，rounds projection 里的 retry card
也能被及时清理，不会残留一个已经不存在的 active retry 状态。

### 6.5 `tool-safe boundary` 不等于 `provider-replayable boundary`

这次真实故障暴露了一个关键问题：旧实现里的 “safe boundary” 只保证了 `tool_call -> tool_return` 链闭合，
但没有保证 compact 之后剩下的可见 suffix 仍然能作为合法 chat history 重放给 provider。

典型坏形态是：

- 历史前半段被 compact 掉
- 后半段只剩 `assistant/tool/...`
- 触发这些工具调用的原始 user 锚点已经不在 live history 里

这类 suffix 在部分 provider 上会直接触发 `messages 参数非法`，
而且即使 provider 容忍，也会削弱长时任务继续执行时的语义稳定性。

本次修复分两层处理：

- full compaction 选边界时，不再只要求 tool 链闭合，还要求保留段本身是 replayable 的：
  - 第一个可见 message 必须是 user anchor
  - 可见段内不能存在 orphan tool result
- 在真正调用 provider 前，增加最后一道 prompt-view 修复：
  - 如果可见 history 没有 user anchor，但内部 tool replay 仍合法，则插入 synthetic resume bridge
  - 如果可见 history 前缀已经损坏，则先裁掉不可重放前缀，再决定是否补 bridge

resume bridge 默认会带上当前 run intent，用来恢复 “当前任务为什么在做这些工具动作” 这个用户锚点。

为了不让这个 replayability 检查在热路径上退化成 O(n²)，
planner 现在会先反向预计算每个 suffix start 是否 replayable，
再在正向扫描 split 边界时复用这张表。
`coerce_replayable_compaction_count(...)` 也沿用同一套预计算逻辑，
避免对 `history[index:]` 反复做全量重扫。

### 6.6 marker 元数据与时间线

本次 compaction marker 额外记录：

- `compaction_strategy=rolling_summary`
- `estimated_tokens_before`
- `estimated_tokens_after_microcompact`
- `estimated_tokens_after_compact`
- `threshold_tokens`
- `target_tokens`
- `compacted_message_count`
- `kept_message_count`
- `protected_tail_messages`

时间线和 rounds projection 现在会把该类 marker 显示为：

```text
History compacted (rolling summary)
```

而不是单一的 `History compacted`。

同时，`microcompact` 不再伪装成 history marker。
它会作为 round 级运行时观测字段暴露在 `/api/sessions/{session_id}/rounds` 和
`/api/sessions/{session_id}/rounds/{run_id}` 中，前端显示为独立 badge：

```text
Microcompact 139.9k -> 9.0k
```

这个 badge 只表示“本轮 prompt view 做过轻量压缩”，不表示历史边界已经被持久化重写。

## 7. Prompt Cache / Token Cache 设计考虑

本次没有完整实现 prompt cache telemetry，但设计上已经向这个方向收敛：

- `microcompact` 只改 prompt view，不改持久 transcript，减少不必要的结构性变化。
- compaction 预算开始显式考虑完整 prompt 组成，而不是只估 history。
- marker 元数据开始保留压缩前后 token 变化，为后续补 `cache_read/create/delete` 做准备。
- 当前 system prompt 实际上仍是“稳定前缀 + 动态尾巴”：
  - 基础 role/system 指令块大体稳定
  - rolling summary 作为动态 section 追加在 system prompt 末尾
  - 这让缓存收益主要集中在 system prompt 前半段
  - 只要 summary 改变，位于它后面的 history 前缀缓存通常就会一起失效

后续建议继续完善：

- 区分 `cache read`、`cache create`、`cache delete`
- 记录 cache-break reason
- 固化 stable prefix 与动态 prompt 区段

## 8. 代码落点

本次改动的主要代码位置：

- `src/relay_teams/agents/execution/llm_session.py`
  - 新增统一 `_prepare_prompt_context(...)`
  - `_build_agent_iteration_context(...)` 统一复用到首次尝试与 restart
  - compaction、token 预算、provider-safe replay 修复统一由该入口编排
  - full compaction 时同时传递 live `history` 与原始 `source_history`
- `src/relay_teams/agents/execution/conversation_microcompact.py`
  - 新增发送前轻量压缩
- `src/relay_teams/agents/execution/conversation_compaction.py`
  - 新增 `ConversationCompactionBudget`
  - full compaction 改为接收完整 prompt 预算
  - rolling summary rewrite 改为读取原始 `source_history`
  - replayable suffix 规划改为反向预计算，避免 O(n²) 扫描
  - marker metadata 扩展
- `src/relay_teams/sessions/session_service.py`
  - marker label 支持区分 rolling summary compaction
- `src/relay_teams/sessions/session_rounds_projection.py`
  - rounds 投影同步显示 rolling-summary label
  - rounds 投影新增 `microcompact` 运行时字段
  - 后续 step 若显式报告未应用 `microcompact`，会清理旧 badge 状态
- `frontend/dist/js/components/rounds/timeline.js`
  - round badge 区分 `microcompact` 与 full compaction marker

## 9. 测试

本次补充和更新的测试覆盖：

- `tests/unit_tests/agents/execution/test_conversation_microcompact.py`
  - 只压旧工具结果
  - 未闭合 tool chain 不被破坏
  - 相同输入得到完全一致的输出
- `tests/unit_tests/agents/execution/test_conversation_compaction.py`
  - marker metadata 正确写入
  - rolling summary 会读取 `microcompact` 之前的原始工具输出
  - replayable suffix 规划不会退化成 O(n²) 后缀重扫
- `tests/unit_tests/agents/execution/test_llm_session.py`
  - `_prepare_prompt_context(...)` 会同时把 live `history` 和原始 `source_history` 传给 full compaction
  - `_safe_max_output_tokens(...)` 开始考虑完整 prompt 预算
  - validation / injection restart 会重建同一套 prompt-view 上下文
- `tests/unit_tests/sessions/test_session_agent_messages.py`
  - timeline label 更新
- `tests/unit_tests/sessions/test_rounds_projection_message_role_fallback.py`
  - rounds projection label 更新
  - rounds projection `microcompact` 字段映射
  - 较新的 step 若未应用 `microcompact`，旧 badge 会被清理
- `tests/unit_tests/frontend/test_round_history_clear_ui.py`
  - 前端 round badge 显示 `microcompact` 独立文案

新增 API 集成测试：

- `tests/integration_tests/api/test_session_context_compaction.py`
  - `test_short_history_microcompact_preserves_exact_recall_without_marker`
  - 验证短历史大工具输出场景下，prompt-view `microcompact` 足以支撑后续 recall，而不会额外写入 full compaction marker
  - `/api/sessions/{session_id}/rounds` 会返回 `microcompact` 运行时字段，允许 UI 明确区分“只发生了轻量压缩”和“发生了 full compaction marker”
- `tests/integration_tests/api/test_session_context_compaction.py`
  - `test_multiple_rolling_summary_rewrites_preserve_rounds_and_exact_recall`
  - 验证多轮 full rolling-summary 重写后：
    - `session_history_markers` 会持续增长
    - 旧消息会被 `hidden_from_context`
    - `/api/sessions/{session_id}/rounds` 能稳定返回 `History compacted (rolling summary)`
    - 最终 recall 仍能精确返回被多轮摘要重写后的关键事实

集成测试运行时配置还额外做了两件事：

- fake LLM 的测试 profile 显式配置 `context_window=22000`，让完整 prompt 预算既不会因为未知模型窗口而失效，也不会因为窗口过小导致 `history_trigger_tokens` 直接归零
- fake LLM 增加 deterministic rolling-summary compaction / recall 场景，使 API 集成测试可以稳定覆盖多轮摘要重写路径，而不是只验证普通聊天流

## 10. 真实 LLM 回归结论

在本次实现基础上，已经补做过两类真实 LLM 回归：

- 短历史超大工具输出：
  - 单个 run 内产生大体积工具结果，但不写 full compaction marker
  - 随后 recall 仍能精确返回全局关键事实
  - 说明 prompt-view `microcompact` 可以覆盖“短历史但工具输出极大”的热路径
- 多轮 rolling-summary 重写：
  - 构造 6 个高负载 phase，连续触发多次 full rolling-summary marker
  - 最终 session 累积出现 5 个 compaction marker，19 条旧消息被隐藏
  - recall 仍能精确命中全局事实和 phase 级 anchor / checksum

这两轮真实回归表明：

- 当前实现已经能把“轻量压缩”和“多轮 full rolling-summary”两条主路径都跑通
- 在已经发生多次 rolling-summary 重写的情况下，还没有观察到关键细节漂移
- 但最后一个 marker 通常只覆盖到较早阶段，最新阶段仍依赖活跃尾部保留，因此“更老细节在更深层重写后是否退化”仍值得继续压测

后续如果继续做实测，最有价值的场景是：

- 再增加更多 phase，把最早两轮 facts 也完全压入更深层 summary
- 专门检查更早阶段的精确字符串是否开始漂移
- 结合 token usage 和 rounds timeline 一起观察 compaction 是否触发得足够早

## 11. 后续演进建议

当前实现已经把仓库从“只有 full compaction”推进到了“多层 prompt-view 压缩”。

下一步建议优先级如下：

1. 引入显式 compaction checkpoint，而不是仅通过 system prompt 注入 summary。
2. 将 resume / clear / rollback 都对齐到 checkpoint 语义。
3. 扩展 token usage 记录，补充 cache create/delete 与 cache-break reason。
4. 如有需要，再引入异步 session notes，而不是让同步主路径继续膨胀。

## 12. 结论

本仓库最适合的路线不是纯抄 Codex 或纯抄 Claude Code，而是：

- 在热路径上采用 Claude Code 风格的在线轻量压缩
- 在持久压缩语义上保留并逐步强化当前仓库已有的 rolling summary 机制
- 未来再向 Codex 风格的 checkpoint transcript 继续演进

本次实现完成的是这条路线里最有收益、风险最低的一步。
