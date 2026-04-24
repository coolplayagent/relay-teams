# System Prompt 分层与稳定前缀

## 目标

运行时 system prompt 需要同时满足两类要求：

- 前缀稳定，尽量复用模型侧 KV cache / prompt cache。
- 尾部完整，向模型提供当前会话真实可用的 workspace、工具、时间与会话上下文。

因此 prompt 构建采用“稳定前缀 + 动态上下文尾部”的结构。新增运行时信息必须优先放入动态尾部，不能插入 role prompt、runtime rules、skill catalog、capability summary 之前。

## 分层顺序

当前 provider system prompt 的高层顺序是：

1. `base_instructions`
   - role 自身 `system_prompt`
   - 通用 runtime rules
   - normal mode subagent rules 或 orchestration rules
2. skill catalog
3. capability summary
   - available roles
   - MCP/tool/skill 能力摘要
4. `workspace_context`
   - runtime environment information
   - workspace environments
   - execution surface
   - 本地/全局 prompt instruction 文件
   - 会话来源规则
   - authorized runtime tools
   - orchestration prompt

`workspace_context` 是放置 workspace、日期、shell、run-specific 信息的默认位置。

## 稳定前缀规则

为了保护 KV cache，下面内容不得因为 workspace、SSH profile、日期或 run 状态改变而移动或重写：

- role prompt 起始位置
- 通用 runtime rules
- skill catalog 在 capability summary 之前的顺序
- available roles/subagents 在 workspace context 之前的顺序
- `base_instructions` 必须继续以 role prompt 开头

新增动态 section 时，应满足：

- 不插入 `workspace_context` 之前。
- 不修改 `COMMON_MODE_PROMPT` 文本，除非任务明确要求调整全局行为。
- 不把时间、workspace、SSH、tool authorization 等 run-specific 内容放进 `base_instructions`。
- 不把 objective-dependent 内容放进 provider system prompt 的稳定前缀。

实现上应保留测试，断言不同 workspace mounts 生成的 prompt 在 `## Runtime Environment Information` 之前完全一致。

## Workspace Environments

`## Workspace Environments` 描述当前会话绑定 workspace 内可用的 mounts。它只列当前 workspace 的环境，不列全局未挂载的 SSH profiles。

每个 mount 可披露：

- mount name
- 是否默认 mount
- provider
- root / remote root
- working directory
- readable / writable scope
- capabilities
- workspace 路径语法
- SSH mount 的非敏感连接元数据

SSH mount 的 `username` 是必需的远端登录身份。`## Workspace Environments` 可以展示该 username 和登录用户规则，但不得要求模型写 `ssh` 登录脚本，也不得把本机操作系统用户当作远端登录用户。缺少 username 的历史 profile 不进入 prompt metadata；后端运行时会在发起 subprocess 前返回明确错误。

SSH mount 不得披露：

- password
- private key 正文
- private key 文件名或临时路径
- secret flags
- askpass 环境变量
- probe latency 或临时认证诊断

LLM 通过 workspace tools 使用这些环境。认证由后端 `SshProfileService` 和 secret store 在工具运行时透明完成；系统 `ssh`、`ssh-agent` 和 `~/.ssh/config` 只可提供认证材料，不能替代 profile username。
