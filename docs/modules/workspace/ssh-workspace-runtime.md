# SSH Workspace Runtime

## 目标

SSH workspace mount 对 agent 表现为普通 workspace 工具面。Agent 只使用 shell、read、write 等 workspace tools；后端根据 workspace mount 引用的 SSH profile 透明完成远端登录、认证和命令执行。

## 配置模型

SSH profile 是系统级配置，workspace mount 只保存 `ssh_profile_id` 和 `remote_root`。

SSH profile 的非敏感字段包括：

- `host`
- `username`
- `port`
- `remote_shell`
- `connect_timeout_seconds`
- `private_key_name`

`username` 是必填远端登录身份。新建、编辑、probe 和运行时执行都必须有明确 username；历史脏数据缺少 username 时，运行时在启动 subprocess 前失败，并提示用户编辑 profile。

## 认证边界

密码和私钥正文只写入 unified secret store，不写入 SQLite row，不进入 prompt、tool result 或日志。

运行时由 `SshProfileService` 负责：

- 为密码认证生成临时 askpass 脚本和环境变量。
- 为私钥认证生成临时 0600 identity 文件。
- 为远程 shell 命令构造 `ssh -l <username> -- <host> <command>`。
- 为 SSHFS mount 构造 `<username>@<host>:<remote_root>`。
- 在命令结束或 probe 结束后清理临时认证文件。

当没有保存密码或私钥时，可以使用系统 `ssh`、`ssh-agent`、默认 identity 和 `~/.ssh/config` 里的认证材料。它们只提供认证材料，不提供登录身份；登录 username 始终来自 SSH profile。

## Agent 行为

Prompt 可以展示当前 workspace mount 的非敏感 SSH metadata，包括 `ssh_profile_id`、host、username、port、remote shell、remote root 和 materialized local root。

Agent 不应编写显式 `ssh` 登录脚本，不应把本机操作系统用户当作远端用户，也不应请求 password、private key、askpass path 或临时 key path。需要远程执行时，agent 继续调用 workspace tools，后端根据 mount/profile 完成远端访问。

## 失败模式

- 缺少 username：保存或 probe 请求被拒绝；历史 profile 在运行时执行前返回明确错误。
- 缺少 `ssh` 或 `sshfs`：返回可操作错误，不泄露环境变量或 secret。
- 认证失败：返回分类后的认证错误，只披露脱敏诊断。
- 现有 SSHFS mount 的 profile、username、host、port 或 remote root 与请求不匹配：拒绝复用，并要求先卸载。
