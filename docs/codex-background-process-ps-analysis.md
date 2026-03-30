# Codex 后台进程、`/ps` 与 npm 源码分析

## 1. 结论

这次分析覆盖了三层：

1. `@openai/codex` npm 包的已发布内容与启动链路
2. 上游 `openai/codex` 仓库里后台 terminal、`/ps`、`/stop` 的 Rust 实现
3. 本 worktree 中 Agent Teams 对 `codex --serve` 的 stdio/ACP 接入方式

先给结论：

- `@openai/codex` 现在本质上不是 TypeScript 业务实现，而是一个很薄的 Node 启动器；真正的 CLI 主体是按平台分发的原生二进制。启动入口在 `codex-cli/bin/codex.js:1`。
- Codex 的“后台进程”在当前上游实现里，准确说是 background terminals / unified exec processes，不是单独的 shell job 管理器文档名。用户侧最直接的可见入口就是 `/ps` 和 `/stop`。
- `/ps` 的作用是列出当前仍在运行的 background terminals，展示每个后台 terminal 的命令摘要和最近输出片段。命令定义在 `codex-rs/tui/src/slash_command.rs:52`、`codex-rs/tui/src/slash_command.rs:93`，UI 处理在 `codex-rs/tui/src/chatwidget.rs:5360`、`codex-rs/tui/src/chatwidget.rs:7493`。
- `/stop` 的作用是停止所有 background terminals。命令定义在 `codex-rs/tui/src/slash_command.rs:53`、`codex-rs/tui/src/slash_command.rs:94`，执行在 `codex-rs/tui/src/chatwidget.rs:5363`、`codex-rs/tui/src/chatwidget.rs:7505`。
- 底部状态栏会在有后台 terminal 时显示 `N background terminal(s) running · /ps to view · /stop to close`，实现见 `codex-rs/tui/src/bottom_pane/unified_exec_footer.rs:45`。
- 本 worktree 没有自己实现 `/ps` 或 background shell；它做的是把外部 ACP agent 作为 role backend 接进来。对于 Codex，本分支当前采用 `stdio` 方式，配置形态是 `command="codex"`、`args=("--serve",)`，可见于测试 `tests/unit_tests/external_agents/test_acp_client.py:42` 与 `tests/unit_tests/external_agents/test_config_service.py:71`。
- 公开可检索的上游仓库里，我没有检索到 `--serve` 字面量定义；所以“`codex --serve` 是当前公开仓库明文可见的官方入口”这一点，现有证据不足。能确认的是：本分支明确按这个调用约定来对接 Codex，并且其 stdio transport 设计与 ACP JSON-RPC 消息流是自洽的，见 `src/agent_teams/external_agents/acp_client.py:146`。

## 2. `@openai/codex` npm 包到底是什么

npm registry 元数据显示：

- 包名：`@openai/codex`
- 已发布版本：`0.117.0`
- 仓库：`https://github.com/openai/codex.git`
- bin：`codex -> bin/codex.js`

从已发布 tarball 解包结果看，npm 包内容非常薄，只有：

- `bin/codex.js`
- `bin/rg`
- `package.json`
- `README.md`

这说明 npm 包不是把完整业务源码打进去，而是一个分发入口。

### 2.1 启动链路

`codex-cli/bin/codex.js:1` 是统一入口。它做的事情是：

1. 根据 `process.platform` 和 `process.arch` 推导 target triple，如 Linux x64 对应 `x86_64-unknown-linux-musl`，见 `codex-cli/bin/codex.js:16`。
2. 按 target triple 选择平台包，例如 `@openai/codex-linux-x64`，见 `codex-cli/bin/codex.js:15`。
3. 从平台包的 `vendor` 目录里定位真正的 `codex` 原生二进制，见 `codex-cli/bin/codex.js:78`、`codex-cli/bin/codex.js:107`。
4. 用 Node 的 `spawn()` 把这个原生二进制拉起来，并透传当前 CLI 参数，见 `codex-cli/bin/codex.js:167`。
5. 转发 `SIGINT`、`SIGTERM`、`SIGHUP` 给子进程，保证 Ctrl-C 等行为一致，见 `codex-cli/bin/codex.js:187`。

因此，逆向 `npm codex` 时最关键的判断是：

- npm 包不是主体
- Rust/native binary 才是主体
- Node 层只负责平台判断、路径拼装、PATH 注入、信号转发

### 2.2 为什么 npm README 里会说 legacy TypeScript

`codex-cli/README.md:1` 顶部明确写了：这份 README 对应的是 legacy TypeScript implementation，已经被 Rust implementation 取代。

这和上面的结论一致：

- 旧时代：TypeScript 版 CLI
- 当前：Rust 主实现 + npm 仅做跨平台分发包装

## 3. Codex 的 background process / background terminal 是什么

上游源码里与 `/ps` 直接对应的术语主要是：

- `background terminal`
- `unified exec process`
- `background terminals running`

也就是说，用户看到的是“后台 terminal 会话”，底层实现映射到 unified exec process manager。

### 3.1 `/ps` 和 `/stop` 的命令定义

命令枚举定义在 `codex-rs/tui/src/slash_command.rs:12`。

其中：

- `SlashCommand::Ps` 在 `codex-rs/tui/src/slash_command.rs:52`
- 描述文本 `list background terminals` 在 `codex-rs/tui/src/slash_command.rs:93`
- `SlashCommand::Stop` 在 `codex-rs/tui/src/slash_command.rs:53`
- 描述文本 `stop all background terminals` 在 `codex-rs/tui/src/slash_command.rs:94`
- 这两个命令允许在任务运行期间使用，见 `codex-rs/tui/src/slash_command.rs:139`

这点很重要：

- `/ps` 不是离线管理命令，而是故意设计成任务运行中也能查看
- `/stop` 也同样可以在任务中触发

### 3.2 TUI 里 `/ps` 实际做什么

`/ps` 在 `codex-rs/tui/src/chatwidget.rs:5360` 被分发到 `add_ps_output()`。

`add_ps_output()` 定义在 `codex-rs/tui/src/chatwidget.rs:7493`，逻辑很简单：

- 遍历 `self.unified_exec_processes`
- 把每个 process 转成 `UnifiedExecProcessDetails`
- 插入一条历史输出 cell

真正的展示结构在 `codex-rs/tui/src/history_cell.rs:656`：

- 标题固定为 `Background terminals`，见 `codex-rs/tui/src/history_cell.rs:681`
- 如果没有后台 terminal，显示 `No background terminals running.`，见 `codex-rs/tui/src/history_cell.rs:684`
- 每个后台 terminal 展示：
  - `command_display`
  - 最近输出 `recent_chunks`
- 最多显示 16 个后台 terminal，见 `codex-rs/tui/src/history_cell.rs:679`
- 命令摘要会截断
- 最近输出也会按宽度截断
- 最外层命令 cell 会把这次输出标记成 `/ps`，见 `codex-rs/tui/src/history_cell.rs:782`

所以 `/ps` 的用户侧效果不是操作系统级 `ps`，而是：

- 只看 Codex 自己维护的后台 terminal 列表
- 带业务上下文
- 能看到每个后台 terminal 最近几行输出

### 3.3 `/stop` 实际做什么

`/stop` 在 `codex-rs/tui/src/chatwidget.rs:5363` 分发到 `clean_background_terminals()`。

实现见 `codex-rs/tui/src/chatwidget.rs:7505`：

- 提交 `AppCommand::clean_background_terminals()`
- UI 上追加一条信息：`Stopping all background terminals.`

从这个实现可见，`/stop` 是“全停”语义，不是按单个 process_id 定向停止。

## 4. 后台 terminal 是怎么被追踪的

TUI 侧维护了 `self.unified_exec_processes`。

### 4.1 进程开始

当 unified exec 启动时，TUI 会往列表里放一条 process summary，相关逻辑在 `codex-rs/tui/src/chatwidget.rs:3676`：

- 若已存在同 key 进程，则更新 `call_id` 与 `command_display`
- 否则 push 一个 `UnifiedExecProcessSummary`
- 然后刷新 footer，见 `codex-rs/tui/src/chatwidget.rs:3702`

### 4.2 进程输出

最近输出追踪在 `codex-rs/tui/src/chatwidget.rs:3711`：

- 按 `call_id` 找到对应后台 process
- 把 stdout/stderr chunk 转成文本行
- 丢弃空行
- 最多保留最近 3 条输出，见 `codex-rs/tui/src/chatwidget.rs:3730`

这也是 `/ps` 能展示“最近输出”的来源。

### 4.3 进程结束

结束时在 `codex-rs/tui/src/chatwidget.rs:3692`：

- 根据 `process_id` 或 `call_id` 从 `self.unified_exec_processes` 删除
- 若列表长度变化，刷新 footer

所以 `/ps` 看到的是“当前存活中的 unified exec process 列表”，不是历史列表。

## 5. 底部提示文案说明了官方推荐交互

`codex-rs/tui/src/bottom_pane/unified_exec_footer.rs:45` 定义了统一摘要：

- `N background terminal(s) running · /ps to view · /stop to close`

这几乎就是官方交互模型的最短总结：

- 有后台 terminal 时，先看 `/ps`
- 不想要了就 `/stop`

这也是“background process 使用方法”的最可靠用户层结论。

## 6. 底层 unified exec 是怎么保活后台进程的

核心实现位于 `codex-rs/core/src/unified_exec/process_manager.rs:160`。

### 6.1 `exec_command()` 的行为

`exec_command()` 在 `codex-rs/core/src/unified_exec/process_manager.rs:160`：

1. 打开带 sandbox 的执行会话
2. 创建 process
3. 发出 `ExecCommandSource::UnifiedExecStartup` begin event，见 `codex-rs/core/src/unified_exec/process_manager.rs:190`
4. 启动输出流式采集，见 `codex-rs/core/src/unified_exec/process_manager.rs:198`
5. 如果进程启动后仍存活，则立刻把它存入进程表，见 `codex-rs/core/src/unified_exec/process_manager.rs:200`
6. 返回本轮已收集输出；若进程仍活着，则响应中带 `process_id`，见 `codex-rs/core/src/unified_exec/process_manager.rs:321`

这里最关键的一句注释在 `codex-rs/core/src/unified_exec/process_manager.rs:200`：

> Persist live sessions before the initial yield wait so interrupting the turn cannot drop the last Arc and terminate the background process.

也就是说，上游明确在做“turn 结束后仍保活后台 terminal”的设计。

### 6.2 `write_stdin()` 的行为

对后台 terminal 的继续交互在 `codex-rs/core/src/unified_exec/process_manager.rs:336`：

- 通过 `process_id` 找到存活进程
- 可向 tty 写 stdin，见 `codex-rs/core/src/unified_exec/process_manager.rs:357`
- 空输入时，本质上是 poll 背景输出
- 非空输入时，是继续与这个后台 terminal 交互

配置项 `background_terminal_max_timeout` 在 `codex-rs/core/src/config/mod.rs:536`，说明官方把这条路径明确定义为 background terminal 输出轮询超时窗口。

### 6.3 进程表与自动裁剪

进程保存在 `store_process()`，见 `codex-rs/core/src/unified_exec/process_manager.rs:523`。

该函数会：

- 把 process 放进 `process_store`
- 若进程数过多，触发告警与裁剪，见 `codex-rs/core/src/unified_exec/process_manager.rs:546`
- 为进程挂一个 exit watcher，见 `codex-rs/core/src/unified_exec/process_manager.rs:569`

这说明 background terminal 不是无界的；它们是一个受控资源池。

## 7. `background process` 的用户使用方法

基于上游文档页与源码，用户侧可以总结成下面这套心智模型。

### 7.1 什么时候会出现后台 terminal

当 Codex 以 unified exec 方式执行某些长时间运行命令，而该进程在当前 turn 结束后仍然存活时，它会被保留为后台 terminal。

源码证据：

- TUI 文案里统一称为 `background terminal`，见 `codex-rs/tui/src/bottom_pane/unified_exec_footer.rs:53`
- core 层在进程仍活着时会 `store_process()`，见 `codex-rs/core/src/unified_exec/process_manager.rs:202`

### 7.2 怎么查看

在 Codex CLI 交互界面输入：

```text
/ps
```

效果：

- 列出还在跑的后台 terminal
- 每个 terminal 给出命令摘要
- 每个 terminal 给出最近输出片段

对应实现：

- `codex-rs/tui/src/chatwidget.rs:7493`
- `codex-rs/tui/src/history_cell.rs:672`

### 7.3 怎么关闭

在 Codex CLI 交互界面输入：

```text
/stop
```

效果：

- 停掉所有后台 terminal

对应实现：

- `codex-rs/tui/src/chatwidget.rs:7505`

### 7.4 怎么发现当前有后台 terminal

看底部 footer：

- `1 background terminal running · /ps to view · /stop to close`
- `N background terminals running · /ps to view · /stop to close`

对应实现：`codex-rs/tui/src/bottom_pane/unified_exec_footer.rs:50`

## 8. 本 worktree 如何使用 Codex

本分支不是在改 Codex 本体，而是在做 Agent Teams 的 external ACP agent 能力。

### 8.1 配置模型

外部 agent 的 `stdio` transport 定义在 `src/agent_teams/external_agents/models.py:26`：

- `command`
- `args`
- `env`

整体配置是 `ExternalAgentConfig`，见 `src/agent_teams/external_agents/models.py:63`。

### 8.2 运行方式

本分支的 stdio client 在 `src/agent_teams/external_agents/acp_client.py:146`。

它会：

- `asyncio.create_subprocess_exec(command, *args, ...)`，见 `src/agent_teams/external_agents/acp_client.py:171`
- 通过 stdin/stdout 跑 JSON-RPC
- 逐行读取 stdout 消息，见 `src/agent_teams/external_agents/acp_client.py:249`
- 把 stderr 作为调试日志吸收，见 `src/agent_teams/external_agents/acp_client.py:266`

而且它明确支持把外部 agent 启动在当前 session workspace 内，见：

- `tests/unit_tests/external_agents/test_acp_client.py:14`
- `src/agent_teams/external_agents/acp_client.py:152`
- `src/agent_teams/external_agents/acp_client.py:177`

### 8.3 为什么这里写的是 `codex --serve`

本分支的测试与配置示例都用：

- `command="codex"`
- `args=("--serve",)`

证据：

- `tests/unit_tests/external_agents/test_acp_client.py:42`
- `tests/unit_tests/external_agents/test_config_service.py:77`
- `tests/unit_tests/external_agents/test_config_service.py:157`

这说明本 worktree 预期 Codex 作为一个 stdio ACP server 被拉起。

但是要注意：

- 我在公开的 `openai/codex` 当前仓库源码中，没有检索到 `--serve` 字面量定义
- 所以目前能确定的是“本分支依赖这个调用约定”
- 不能仅凭公开源码进一步断言它在当前所有公开发行版里都保持不变

### 8.4 本分支如何把 Codex 接进 Agent Teams 运行时

外部 agent API 暴露在：

- `GET /api/system/configs/agents`，`src/agent_teams/interfaces/server/routers/system.py:334`
- `GET /api/system/configs/agents/{agent_id}`，`src/agent_teams/interfaces/server/routers/system.py:341`
- `PUT /api/system/configs/agents/{agent_id}`，`src/agent_teams/interfaces/server/routers/system.py:352`
- `DELETE /api/system/configs/agents/{agent_id}`，`src/agent_teams/interfaces/server/routers/system.py:364`
- `POST /api/system/configs/agents/{agent_id}:test`，`src/agent_teams/interfaces/server/routers/system.py:376`

CLI 则在：

- `src/agent_teams/external_agents/agent_cli.py:21`

而真正的 role backend 切换在 `ExternalAcpProvider`，见 `src/agent_teams/external_agents/provider.py:100`。

`ExternalAcpSessionManager` 会：

- 解析当前 role 绑定的外部 agent，见 `src/agent_teams/external_agents/provider.py:206`
- 构造外部 session，见 `src/agent_teams/external_agents/provider.py:646`
- 在 `session/new` / `session/load` 中传 `cwd` 和 `mcpServers`，见 `src/agent_teams/external_agents/provider.py:668`
- 处理来自外部 agent 的 `mcp/connect`、`mcp/message`、`mcp/disconnect`，见 `src/agent_teams/external_agents/provider.py:699`
- 把 `session/update` 中的 chunk/tool_call/tool_result 重新映射回 Agent Teams 的 run events，见 `src/agent_teams/external_agents/provider.py:756`

### 8.5 这和 `/ps` 的关系

本分支目前没有复刻 Codex 的 `/ps` 命令。

关系是：

- 如果外部 agent 本身就是 Codex
- 那么 `/ps` 属于 Codex 自己的交互界面/TUI slash command
- Agent Teams 这边只负责把它当成 ACP backend 拉起与通信

也就是说：

- `/ps` 是 Codex 产品能力
- `codex --serve` 是本分支假定的对接入口
- Agent Teams 不直接管理 Codex 内部 unified exec 列表

## 9. 可信度与边界

这次结论里，下面几项可信度最高：

1. npm 包是 Node wrapper，真实逻辑在原生 binary
2. `/ps` = list background terminals
3. `/stop` = stop all background terminals
4. unified exec process manager 会在 turn 结束后保活后台 terminal
5. 本分支按 `codex --serve` 的 stdio ACP 方式接入 Codex

下面这项要保留边界：

- 公开仓库里没有检索到 `--serve` 的字面定义，因此不能只凭公开源码证明当前所有 Codex 发行物都暴露这个 flag；这里只能说本分支的集成是按这一契约设计和测试的

## 10. 如果要在本分支里实际配置一个 Codex 外部 agent

按当前代码结构，最小配置心智模型应当是：

```json
{
  "agent_id": "codex_local",
  "name": "Codex Local",
  "description": "Runs Codex via stdio",
  "transport": {
    "transport": "stdio",
    "command": "codex",
    "args": ["--serve"],
    "env": [
      {
        "name": "CODEX_API_KEY",
        "secret": true
      }
    ]
  }
}
```

这不是拍脑袋猜的，而是与本分支测试中保存/解析/恢复 secret 的方式一致：

- secret 不写回配置文件，只在 secret store 中保存，见 `tests/unit_tests/external_agents/test_config_service.py:62`
- runtime 解析时重新回填 secret，见 `tests/unit_tests/external_agents/test_config_service.py:108`

## 11. 一句话总结

如果只记一条：

- Codex 现在是“npm 壳 + Rust 原生主体”；后台进程能力在上游实现里表现为 background terminals / unified exec，用户主要通过 `/ps` 查看、通过 `/stop` 停止；本 worktree 则是把 Codex 作为一个 `stdio` 外部 ACP agent，通过 `codex --serve` 约定接入到 Agent Teams。
