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

3. 上游开源 Rust 源码补充
- 仓库：`https://github.com/openai/codex`
- 本次补充分析使用快照：`ea650a91b31eef5b3b376ca4282686df242b9132`
- 关键模块：
  - `codex-rs/core/src/unified_exec/*`
  - `codex-rs/utils/pty/*`
  - `codex-rs/utils/pty/src/win/*`
  - `codex-rs/windows-sandbox-rs/*`

## 关键发现

### 1. npm 安装的 `@openai/codex` 本身不是主要实现层

npm 包里的 JS 代码只是一个启动器。它根据平台解析到对应的原生二进制包，然后 `spawn` 真正的 Codex 可执行文件。

证据：
- npm 包元数据表明入口是 `bin/codex.js`，并通过 `optionalDependencies` 分发平台专属原生包
- 本地安装包观察显示，`bin/codex.js` 负责解析平台包并启动原生二进制

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

### 5. 跨平台兼容不是 JS wrapper 做的，而是 Rust 统一抽象做的

结合上游开源 Rust 源码，可以把“Codex 为什么能在 Linux 和 Windows 上都暴露 background terminal”说得更精确。

首先，npm 包里的 `bin/codex.js` 只负责：
- 判断当前平台与架构
- 选择对应的原生包
- 启动真正的 `codex` 二进制

真正的跨平台终端能力位于 Rust 层：
- `unified_exec` 负责把长生命周期命令变成 background terminal/session
- `codex_utils_pty` 负责提供统一的 PTY/pipe 抽象
- 上层只依赖统一的 `ProcessHandle` / `SpawnedProcess` / `write_stdin(session_id)` 语义，不直接处理平台 API

可以把这层关系理解为：

1. `exec_command`
2. `UnifiedExecProcessManager`
3. `open_session_with_exec_env()`
4. 若 `tty=true`，走 `codex_utils_pty::pty::*`
5. 若 `tty=false`，走 `codex_utils_pty::pipe::*`
6. 产出统一的 `SpawnedProcess`
7. 上层把它存入 `ProcessStore`，后续继续 `write_stdin`

也就是说，Codex 的 background terminals 不是“Linux 实现一套、Windows 另一套产品语义”，而是：
- 统一 session 模型
- 平台差异下沉到 PTY backend

### 6. Linux 和 Windows 的后台终端后端差异

#### 6.1 Linux / Unix

Linux/Unix 侧更接近传统 PTY 与 shell session：

- PTY 后端走 `portable_pty::native_pty_system()`
- 非 PTY pipe 路径会 `detach_from_tty()`，其内部优先 `setsid()`
- Linux 还会设置 `PR_SET_PDEATHSIG`，尽量保证父进程退出后子进程收到 `SIGTERM`
- 结束时会尽量按 process group 清理，而不是只杀单个 pid

因此在 Linux 上，background terminal 更像一个真正受管的后台 shell/PTY 会话。

#### 6.2 Windows

Windows 不是模拟 Unix PTY，而是单独走 ConPTY：

- 先检查 `conpty_supported()`
- 要求系统 build `>= 17763`
- 真正启动时调用 `CreatePseudoConsole`
- 再通过 `STARTUPINFOEXW + PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE`
- 最终用 `CreateProcessW` 把子进程附着到 ConPTY 上

随后，Rust 继续用与 Linux 相同的高层接口来做：
- 写 stdin
- 读输出
- resize
- 等待退出
- 把会话存到后台进程表里

所以 Windows 上的兼容方式本质上是：
- 终端底座换成 ConPTY
- session 语义保持与 Linux 对齐

#### 6.3 Windows 的额外现实约束

虽然 Windows 也支持 background terminal，但约束比 Linux 多：

1. 旧版本 Windows 如果没有 ConPTY，就不会启用 unified exec，会退回普通 shell tool 路径。
2. Windows 的终止语义主要是 `TerminateProcess`，源码里没有看到 Linux 那样的 `killpg` 语义。
3. 上游测试里，Windows PTY 单测是有的，但 unified exec 的大量集成测试仍然跳过 Windows，因此 Windows 端到端覆盖弱于 Unix。

这意味着：
- Windows 支持 background terminal
- 但实现成熟度和进程族清理强度仍然弱于 Linux

### 7. 为什么 Windows sandbox 下会禁用交互式 background terminal

这部分如果只看公开文档很难看清，但源码里原因非常明确。

当前 Windows sandbox 主执行路径不是 session API，而是 capture API：

- `exec_windows_sandbox()` 调用的是
  - `run_windows_sandbox_capture_elevated(...)`
  - `run_windows_sandbox_capture_with_extra_deny_write_paths(...)`
- 它们返回的是 `CaptureResult { exit_code, stdout, stderr, timed_out }`
- 这意味着主路径只承诺“跑完并收集结果”
- 不承诺“返回一个可持续交互的后台 session”

这直接导致：
- 没法像 unified exec 那样保存一个长期存活的 process/session handle
- 没法自然暴露 `write_stdin`
- 没法自然暴露 `resize`
- 也没法把它作为 `/ps` 中持续可见的 background terminal

更细一点说：

- Windows sandbox crate 里其实已经有更强的 elevated runner IPC 协议，定义了 `SpawnRequest`、`Stdin`、`Terminate`、`Output`、`Exit`
- 这个 runner 在内部也支持 `tty=true` 时用 ConPTY 启动
- 但 Codex 当前对外使用它时，仍把它封装成 capture 流程，而且发送给 runner 的请求被写死成 `tty: false`、`stdin_open: false`

因此当前版本里的真实约束不是“Windows 做不到交互式后台终端”，而是：

> Windows sandbox 这条主线路径目前只接成了 capture 语义，没有接成 unified exec 需要的长生命周期 session 语义。

这也解释了为什么：
- tool 选择层会在某些 Windows sandbox 组合下禁用 unified exec
- app-server 会拒绝 Windows sandbox 进程的 `write` / `resize` / `terminate`

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

1. 现在已经能直接查看上游 Rust 源实现，但本地 npm 已安装版本与上游开源仓库快照不一定完全同 commit。

2. 不能 100% 确认：
- async/sync 的完整切换条件
- `/ps` 是否还有未公开的二级交互命令
- tail buffer 的精确长度
- Windows 发行版与开源仓库中的某些实现细节是否完全一致

3. 但可以高置信度确认：
- `/ps` 是官方能力
- background terminals 是真实产品概念
- 它依赖 `unified_exec`
- Linux/Unix 通过原生 PTY/process group 语义支撑
- Windows 通过 ConPTY 支撑
- Windows sandbox 主路径当前是 capture-only，因此交互式 background terminal 会被禁掉

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

> Codex 支持由 `unified_exec` 驱动的 background terminals。Linux/Unix 通过原生 PTY 与 process group 语义来保活和清理这些后台终端，Windows 则通过 ConPTY 提供对等的交互终端抽象。`/ps` 是这些后台终端的观察入口，而 `command/exec` 系列接口则体现了它们在底层是可流式、可交互、可终止、可清理的受管命令执行会话。需要额外注意的是，一旦走到 Windows sandbox 的 capture-only 路径，交互式 background terminal 会被显式禁用。

## Agent Teams 对齐说明

当前仓库里的 `background task` 运行时已经按这个模型对齐到统一会话抽象，而不是继续把 “foreground shell” 和 “background terminal” 拆成两套实现：

- Linux/macOS:
  - `tty=true` 继续走 POSIX PTY
  - 非 TTY 会话继续保留 stdin pipe 写入能力
  - run 级生命周期、head/tail 输出缓冲、长轮询语义与现有实现保持一致
- Windows:
  - shell 选择改成 `Git Bash first, PowerShell fallback`
  - `tty=true` 通过 `pywinpty` 挂接 ConPTY
  - 如果 Windows 主机不满足 ConPTY 运行条件，则明确拒绝 TTY background task，但仍允许非 TTY background task
- 审批缓存:
  - 规范化不再只认 `bash -lc`
  - 现在会同时归一 Git Bash 路径包装和 PowerShell `-Command` 包装

这意味着当前 Agent Teams 已经具备和 Codex 更接近的跨平台 exec-session 结构，但刻意保留了一点行为差异：非 TTY session 仍允许 `write_stdin`，以兼容仓库里既有的 pipe 工作流。

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

## 稳定引用

- Codex CLI slash commands: `https://developers.openai.com/codex/cli/slash-commands/`
- Codex 单文件文档导出: `https://developers.openai.com/codex/llms-full.txt`
- npm 包仓库声明: `https://github.com/openai/codex`

## 可复现性说明

本文中关于 `/ps`、`command/exec`、`streamStdoutStderr` 的结论，优先基于 OpenAI Developers 的公开文档页面。

本文中关于 npm 包包装层与原生二进制分发方式的结论，基于本地安装的 `@openai/codex@0.117.0` 包进行观察，但不再引用作者机器上的绝对路径或临时抓取文件路径，以避免文档失效。本文中关于 Linux/Windows 跨平台兼容、ConPTY、Windows sandbox capture-only 约束的结论，则优先基于上游开源 Rust 源码快照 `ea650a91b31eef5b3b376ca4282686df242b9132`。若后续需要长期保存二进制逆向证据，建议把可公开分发的摘录整理为仓库内附录或直接改为引用上游开源仓库中的稳定源码位置。
