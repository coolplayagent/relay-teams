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
