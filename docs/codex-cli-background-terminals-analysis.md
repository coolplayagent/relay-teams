# Codex CLI Background Process / Background Terminals 能力分析

## 结论

Codex CLI 的“background process”能力本质上不是把普通 shell 命令简单做成 `cmd &`，而是建立在一套受管的终端/命令执行抽象之上。对外可见的交互入口是 `/ps`，对内更像是 `unified_exec` + `command/exec` 的会话模型。

从官方文档和本机已安装 npm 包两侧看，Codex 至少支持：
- 启动受管命令执行会话
- 区分同步与异步执行模式
- 持续采集 stdout / stderr
- 查看最近输出 tail
- 列出后台终端
- 向运行中进程写 stdin
- 调整 PTY 尺寸
- 终止运行中的会话
- 维护运行状态与清理残留会话

## 分析范围

本分析基于两类证据：

1. 官方文档
- `https://developers.openai.com/codex/cli/slash-commands/`
- `https://developers.openai.com/codex/llms-full.txt`

2. 本机 npm 安装包逆向
- 包版本：`@openai/codex@0.117.0`
- npm 包元数据：`/home/steven/.nvm/versions/node/v24.14.0/lib/node_modules/@openai/codex/package.json`
- JS 入口：`/home/steven/.nvm/versions/node/v24.14.0/lib/node_modules/@openai/codex/bin/codex.js`
- Linux 原生二进制：`/home/steven/.nvm/versions/node/v24.14.0/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/codex/codex`

## 关键发现

### 1. npm 安装的 `@openai/codex` 本身不是主要实现层

npm 包里的 JS 代码只是一个启动器。它根据平台解析到对应的原生二进制包，然后 `spawn` 真正的 Codex 可执行文件。

证据：
- `package.json` 只暴露了 `bin/codex.js`，并通过 `optionalDependencies` 引入平台专属原生包
- `bin/codex.js` 在运行时解析 `@openai/codex-linux-x64` 等包并执行里面的原生二进制

这意味着：
- 真正的 background terminals / `/ps` / 执行模型主要实现在原生二进制里
- 单看 npm JS wrapper 不能解释该能力的内部机制

### 2. `/ps` 是官方公开能力，不是纯社区猜测

官方单文件文档明确列出 `/ps`：

- `/ps` 的描述：`Show experimental background terminals and their recent output.`
- 使用方式：输入 `/ps`
- 展示内容：每个 background terminal 的 command 和最多三行最近的非空输出
- 生效条件：`Background terminals appear when unified_exec is in use; otherwise, the list may be empty.`

这说明：
- Codex 确实存在“background terminals”这一一等概念
- `/ps` 只是查询/展示入口
- 底层是否能出现后台终端，取决于 `unified_exec` 执行路径是否启用

### 3. 背景能力和 `command/exec` 能力是一套体系

官方 app-server 文档公开了以下 RPC/能力：
- `command/exec`
- `command/exec/write`
- `command/exec/resize`
- `command/exec/terminate`

并明确说明：
- `tty: true` 时可获得 PTY-backed session
- 使用 `processId` 可以在后续继续 `write` / `resize` / `terminate`
- 打开 `streamStdoutStderr: true` 后，会在命令运行期间持续收到 `command/exec/outputDelta`

这说明 Codex 内部并不是一次性 `spawn -> wait -> collect output` 的简单模型，而是：
- 可持续持有进程句柄或逻辑上的 session id
- 可交互
- 可流式回传输出
- 可生命周期管理

### 4. 原生二进制字符串能看到明确的后台终端痕迹

从原生二进制提取字符串后，可以直接看到与 background terminals 强相关的字面量，包括：
- `background terminal`
- `/ps to view`
- `executionMode`
- `stdout`
- `stderr`
- `stdout_tail=`
- `stderr_tail=`
- `write_stdin failed:`
- `failed to clean background terminals:`
- `running`
- `blocked`
- `stopped`
- `failed`
- `completed`
- `core/src/unified_exec/async_watcher.rs`

这些字符串非常关键，因为它们直接说明内部至少存在：
- 后台终端对象
- `/ps` 的提示文案
- 执行模式字段
- 输出 tail 缓存或 tail 展示逻辑
- stdin 写入能力
- 后台终端清理逻辑
- 状态枚举
- 一个叫 `unified_exec` 的执行子系统

## 能力模型推断

基于上述证据，可以把 Codex 的 background terminals 能力抽象成下面的模型。

### A. 启动阶段

模型或用户触发某个命令执行后，Codex 不一定总走传统同步等待路径，而可能进入 `unified_exec` 的后台终端路径。

推断行为：
- 创建一个命令执行会话
- 为会话分配内部 id 或 processId
- 记录 command、executionMode、stdout/stderr 管道
- 如果是 TTY 模式，则创建 PTY 会话

### B. 运行阶段

命令启动后，Codex 持续接收输出，并维护当前状态。

推断状态至少包括：
- `running`
- `blocked`
- `stopped`
- `failed`
- `completed`

这里的 `blocked` 很可能不是传统 OS 进程状态，而是产品级状态，表示该执行因为权限、审批、策略、输入等待或其他原因处于阻塞态。

### C. 观测阶段

Codex 允许在主对话之外回看后台终端。

从 `/ps` 文档看，它至少会显示：
- 命令内容
- 当前状态
- 最多三行近期非空输出

从二进制字符串看，它内部还有：
- `stdout_tail`
- `stderr_tail`

因此更合理的推断是：
- Codex 为每个后台终端维护一个 tail buffer
- `/ps` 默认展示压缩版摘要，而不是全部日志
- 更完整输出很可能仍保留在对应的执行会话流中

### D. 控制阶段

官方公开能力表明，Codex 支持：
- 向运行中的命令写 stdin
- 调整 PTY 窗口大小
- 终止命令

这意味着它的后台能力不是“只读监控”，而是“可控制会话”。

### E. 清理阶段

二进制里出现了 `failed to clean background terminals:`，说明 Codex 在会话结束、CLI 退出或状态收敛时，会尝试清理后台终端。

这进一步说明：
- 它考虑了残留后台任务问题
- 其后台终端有独立于当前可视 transcript 的生命周期
- 清理失败是一个被显式处理的错误场景

## `/ps` 支持的能力

从官方文档与逆向证据综合看，`/ps` 至少支持以下能力：

### 1. 列出后台终端
最基本能力，查看当前有哪些 background terminals。

### 2. 查看状态
状态至少覆盖：
- running
- blocked
- stopped
- failed
- completed

### 3. 查看最近输出摘要
不是完整日志，而是“最多三行 recent non-empty output lines”。

### 4. 关联到底层受管命令会话
虽然 `/ps` 文档没直接暴露内部 id，但它背后显然对应某个受管 command session。

### 5. 只在特定执行模式下有内容
官方明确说：只有 `unified_exec` 在用时，background terminals 才会出现；否则 `/ps` 可能为空。

这说明 `/ps` 不是通用的系统进程查看器，而是：
- 只查看 Codex 自己托管的后台终端
- 不查看你机器上的所有进程
- 不等同于系统 `ps`

## 这套 background process 能力和普通 shell `&` 的差别

普通 shell `cmd &`：
- 把进程丢到 shell 后台
- shell 本身通常只知道 job id / pid
- 日志和交互能力依赖用户自己管理

Codex background terminals：
- 是 Codex 自己托管的执行会话
- 有统一状态模型
- 有输出 tail 视图
- 能从 `/ps` 回看
- 能继续写 stdin / resize / terminate
- 有清理逻辑

因此它更像“产品化的后台终端管理层”，而不是单纯 shell 语法糖。

## 对内部数据结构的推断

虽然原生实现未直接反编译成源码，但从字段名可以合理推测一个后台终端对象大致包含：

- `processId`
- `command`
- `executionMode`
- `status`
- `stdout`
- `stderr`
- `stdout_tail`
- `stderr_tail`
- `completedAt`
- 可能还有 `tty`、`exitCode`、`cwd`

这些字段足以支撑：
- `/ps` 摘要展示
- 命令结果展示
- 会话控制
- 生命周期收敛

## 一个更接近真实实现的事件流

### 同步执行
1. 创建 command session
2. 启动命令
3. 实时收 stdout/stderr
4. 等待退出
5. 收敛为 completed/failed

### 异步执行
1. 创建 command session
2. 启动命令
3. 将 session 标记为 async/background terminal
4. 主交互先返回
5. 后台继续收集输出
6. `/ps` 可查看状态与 tail
7. 用户或系统可继续 write / resize / terminate
8. 结束后进入 completed/failed/stopped
9. 最后清理会话

## 限制与不确定点

当前分析仍有几个边界：

1. 没有直接反编译 Rust 源实现，只能通过：
- 官方文档
- npm wrapper
- native binary strings
来重建能力模型

2. 不能 100% 确认：
- 后台终端是否总是 PTY
- async/sync 的完整切换条件
- `/ps` 是否还有未公开的二级交互命令
- tail buffer 的精确长度

3. 但可以高置信度确认：
- `/ps` 是官方能力
- background terminals 是真实产品概念
- 它依赖 `unified_exec`
- 底层具备 output streaming、stdin write、terminate、状态管理、清理能力

## 最终判断

Codex CLI 的 background process 能力，本质上是“受管后台终端会话”。

它的核心不是把进程简单扔到后台，而是：
- 统一执行入口
- 统一状态机
- 统一输出流和 tail
- 统一的交互控制接口
- 统一的 `/ps` 查询视图
- 统一清理逻辑

所以如果要准确描述它，最好的说法不是“Codex 支持 shell background process”，而是：

> Codex 支持由 `unified_exec` 驱动的 background terminals。`/ps` 是这些后台终端的观察入口，而 `command/exec` 系列接口则体现了它们在底层是可流式、可交互、可终止、可清理的受管命令执行会话。

## 关键证据摘录

### 官方文档
- `/ps`：显示 experimental background terminals 和 recent output
- `Background terminals appear when unified_exec is in use`
- `command/exec`
- `command/exec/write`
- `command/exec/resize`
- `command/exec/terminate`
- `streamStdoutStderr: true`
- `command/exec/outputDelta`

### 本机安装包
- `@openai/codex@0.117.0` 只是 JS launcher + 原生 binary 分发壳
- 真正实现位于平台原生二进制中
- 原生字符串可见：
  - `background terminal`
  - `/ps to view`
  - `executionMode`
  - `stdout_tail=`
  - `stderr_tail=`
  - `write_stdin failed:`
  - `failed to clean background terminals:`
  - `running / blocked / stopped / failed / completed`

## 关键引用定位

- npm 包元数据：`/home/steven/.nvm/versions/node/v24.14.0/lib/node_modules/@openai/codex/package.json:2`
- JS 启动器执行原生二进制：`/home/steven/.nvm/versions/node/v24.14.0/lib/node_modules/@openai/codex/bin/codex.js:175`
- 官方 `/ps` 命令列表：`/home/steven/.agent-teams/workspaces/hello/tmp/webfetch/call_5qNaaPOzySBgzVPDzjr2d7Kg.txt:4479`
- 官方 `/ps` 行为说明：`/home/steven/.agent-teams/workspaces/hello/tmp/webfetch/call_5qNaaPOzySBgzVPDzjr2d7Kg.txt:4620`
- 官方 `command/exec` 系列：`/home/steven/.agent-teams/workspaces/hello/tmp/webfetch/call_5qNaaPOzySBgzVPDzjr2d7Kg.txt:1881`
- 官方 stdout/stderr 实时流：`/home/steven/.agent-teams/workspaces/hello/tmp/webfetch/call_5qNaaPOzySBgzVPDzjr2d7Kg.txt:2387`
