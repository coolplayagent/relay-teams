# 安全边界

## 模型上下文不得携带凭据

LLM 的 system prompt、user prompt、tool result、历史消息和可见日志都不得包含明文凭据。

禁止进入模型上下文的内容包括：

- API key、token、password、client secret
- SSH private key 正文
- SSH private key 文件名或临时路径
- askpass 环境变量和值
- secret store 内部 key
- 标记 secret 是否存在的调试字段，除非它是明确脱敏且必要的用户界面状态

模型可以看到完成任务所需的非敏感元数据，例如当前 workspace mount 名称、provider、远端 host、username、port、remote root 和能力声明。

## SSH 凭据边界

SSH profile 是系统级配置对象。workspace mount 只引用 `ssh_profile_id` 和 `remote_root`。

SSH profile 必须显式保存远端登录 `username`。运行时不得回退到本机操作系统用户，也不得让模型自己拼接 `ssh user@host` 登录脚本。系统 `ssh` 配置、`ssh-agent` 和默认 identity 只能作为认证材料来源；登录身份始终来自 SSH profile。

SSH 密码和私钥由 unified secret store 保存，运行时由后端 `SshProfileService` 使用：

- `prepare_remote_command()` 为远程 shell 命令准备脱敏进程参数。
- `ensure_filesystem_mount()` 为默认 SSH mount 准备 SSHFS 挂载。
- 临时 private key 文件和 askpass 脚本只存在于后端进程执行边界。

LLM 不执行显式登录，也不接收用于登录的工具参数。LLM 只调用 workspace tools，后端根据 mount/profile 透明完成认证和远程执行。

## Prompt 披露规则

`## Workspace Environments` 只允许披露当前 workspace 已挂载环境的非敏感信息：

- local mount 的本地 root、working directory、读写范围和 capabilities
- SSH mount 的 `ssh_profile_id`、host、username、port、remote shell、remote root、capabilities 和 materialized local root 状态
- SSH login user rule，即远端登录用户必须使用 profile 中的 username
- workspace 路径语法

不得披露：

- password
- private key
- private key name
- secret flags
- askpass env
- probe latency
- 临时 key path

## 工具结果与日志

工具错误应描述可操作的失败原因，例如认证失败、连接超时、profile 不存在、远端路径不可访问。错误中不得包含 secret 值、临时认证文件路径或完整环境变量。

生产日志必须继续通过 logger 脱敏链路输出。涉及 secret 的模块不得用 `print()` 输出，也不得绕过 `log_event()` / project logger。

## AutoHarness 生成工具边界

AutoHarness 只生成确定性的 JSON utility tool。生成工具必须以 `generated_` 开头，先保存为 pending 角色资产，再经过 `auto_harness_enable_tool` 的强制审批后才能注册和调用。

生成工具资产保存在 app config 的 `generated_tools/{tool_name}/` 下：

- `tool.json` 保存描述、输入 schema、测试用例、状态、目标角色和代码 hash。
- `implementation.py` 保存被验证的 `run(tool_input)` 实现。

安全边界：

- 合成阶段必须通过 AST 校验和测试用例执行；启用阶段会重新校验代码、核对 `code_hash` 并重跑测试，执行已启用工具前也会再次核对 `implementation.py` 与已审批 hash。
- 同名 pending 资产不可被新合成覆盖；持久化 manifest 的 `tool_name` 必须保持 `generated_` 命名空间，否则运行时加载会记录 warning 并跳过。
- 生成代码不得使用 import、文件系统、网络、subprocess、动态 import、显式循环、`range`、dunder/private attribute、attribute mutation、未批准的 attribute call、`eval`、`exec`、`compile`、`open` 等能力。
- 执行环境只注入受限 builtins 与 `json`、`math`、`re`、`datetime`、`statistics` 模块；模块/对象方法调用必须在 AST allowlist 中，并在可终止的子进程内通过超时限制运行。
- 生成工具通过共享 `execute_tool_call(..., raw_args=locals())` 路径执行，保留 hook、审批、状态持久化和观测语义。
- 启用工具会修改持久角色工具列表；显式启用必须失败于未知角色或 hash 不匹配，同一 run 中会刷新当前角色或已解析目标角色实例的运行时工具目录。
