# Workspace 多 Provider 多目录设计

## 1. 背景

当前实现把 `workspace` 视为单一本地目录，并围绕该目录派生：

- 执行根目录
- 文件读写边界
- shell 工作目录
- Git worktree 分叉目录

这个模型适合单仓库本地开发，但不适合下面这些真实场景：

- 一个微服务同时依赖多个代码仓库
- 开发环境、测试环境、个人部署环境对应不同目录
- 一部分目录在本机，一部分目录在远端主机
- 后续还需要扩展更多目录 provider，而不希望影响 session、automation、tool runtime 等已有边界

本设计把 `workspace` 升级为稳定的工具作用域边界。目录、远端连接、provider 只是 `workspace` 内部的挂载实现细节，而不是跨模块的一等绑定键。

## 2. 设计目标

- `workspace` 继续作为第一等公民，是系统里唯一稳定的执行作用域边界
- `session`、`automation`、run、审批、角色记忆都只绑定 `workspace_id`
- 一个 `workspace` 可以挂载多个命名目录根
- 每个目录根通过 provider 访问，首版支持 `local` 和 `ssh`
- 未来新增 provider 时，不需要修改 session、automation、tool runtime 的公共合同
- 明确 provider 内部能力差异，但对外暴露统一的 workspace 运行时接口和错误模型

## 3. 非目标

- 本次不实现对象存储 provider
- 本次不在 session 或 automation 中新增 provider 级绑定
- 本次不把角色长期记忆拆分到 mount 级别
- 本次不实现自定义 SSH 密码登录或私钥托管
- 本次不做跨 mount 聚合 diff

## 4. 核心概念

### 4.1 Workspace

`workspace` 是稳定的工具作用域，而不是某个目录本身。

职责：

- 持有稳定的 `workspace_id`
- 定义当前可用的 mounts 集合
- 指定默认 mount
- 为工具运行时提供统一的路径解析、能力查询和 provider 路由入口
- 作为 session、automation、审批、角色记忆的稳定绑定对象

非职责：

- 直接暴露 provider 私有配置给其它模块
- 充当长期记忆或 artifact 的所有者

### 4.2 Mount

`mount` 是 workspace 内部的命名目录挂载点。

每个 mount：

- 属于一个 `workspace_id`
- 具有稳定的 `mount_name`
- 对应一个 provider
- 对应一个目录根
- 可定义默认工作目录、读路径和写路径范围
- 具备自己的能力声明，例如是否支持 shell、diff、preview

本次采用扁平 mount 模型，不做环境分组树。

### 4.3 Provider

provider 是 mount 的访问实现。

首版 provider：

- `local`
- `ssh`

provider 只存在于 workspace 模块内部。其它域只知道：

- `workspace_id`
- `mount_name`
- workspace 路径
- provider-neutral capability/error

### 4.4 SSH Profile

`ssh profile` 是系统级全局配置对象，用于复用远端连接信息。

workspace 中的 `ssh` mount 只引用 `ssh_profile_id`，并额外声明远端目录根。SSH profile 不属于 session，也不属于某个单独的 workspace。

## 5. 数据模型

### 5.1 WorkspaceRecord

`WorkspaceRecord` 保存：

- `workspace_id`
- `default_mount_name`
- `created_at`
- `updated_at`

不再保存单一 `root_path`。

### 5.2 WorkspaceMountRecord

新增 `WorkspaceMountRecord`：

- `workspace_id`
- `mount_name`
- `provider`
- `provider_config`
- `working_directory`
- `readable_paths`
- `writable_paths`
- `capabilities`
- `created_at`
- `updated_at`

### 5.3 Provider Config

provider 配置使用判别联合：

- `LocalMountConfig`
  - `root_path`
- `SshMountConfig`
  - `ssh_profile_id`
  - `remote_root`

### 5.4 SSH Profile

新增 `SshProfileRecord`：

- `ssh_profile_id`
- `host_alias`
- `port`
- `username`
- `shell`
- `connect_timeout_seconds`
- `created_at`
- `updated_at`

首版推荐优先通过 `host_alias` 对接系统 OpenSSH 配置；`port`、`username`、`shell` 作为可选覆盖字段。

## 6. 持久化与迁移

### 6.1 Workspace 主表

`workspaces` 主表收缩为 workspace 级元数据，不再把单目录根和 profile 混在一行里。

### 6.2 Workspace Mounts 表

新增 `workspace_mounts` 表，存储 workspace 下的所有 mounts。

### 6.3 SSH Profiles 表

新增 `ssh_profiles` 表，用于保存系统级 SSH profile。

### 6.4 旧数据迁移

现有单目录 workspace 自动迁移为一个名为 `default` 的本地 mount：

- 旧 `workspace.root_path` 迁移到 `LocalMountConfig.root_path`
- 原 `file_scope.working_directory`、`readable_paths`、`writable_paths` 保持原语义
- 旧 Git worktree workspace 迁移为 `provider=local` 的 mount，并保留当前工作树目录作为根

迁移完成后：

- `workspace_id` 保持不变
- session、automation、role memory 不需要重写绑定键

## 7. 运行时模型

### 7.1 Workspace Runtime Facade

workspace 模块对外提供统一运行时 facade：

- `WorkspaceScopeHandle`
- `MountHandle`
- `WorkspaceProvider` SPI

其它模块只能通过该 facade 访问 workspace，不得自行分支 provider 逻辑。

### 7.2 WorkspaceProvider SPI

provider SPI 至少覆盖：

- `stat`
- `list_tree`
- `read_file`
- `grep`
- `glob`
- `write_file`
- `edit_file`
- `run_shell`
- `diff_summary`
- `diff_file`
- `preview_file`
- `get_capabilities`

返回统一的 capability 结果和错误类型。

### 7.3 Local Provider

`local` provider 复用当前本地文件系统能力：

- 本地 Path 校验
- 本地 shell cwd
- 本地 Git diff/worktree 行为

### 7.4 SSH Provider

`ssh` provider 通过系统 `ssh` 客户端和远端 shell 实现：

- 目录树读取
- 文件读取和搜索
- 文件写入和编辑
- shell 命令执行
- 条件允许时的 diff

SSH provider 不把远端路径伪装成本地 Path。远端资源通过 provider 方法调用，不通过本地绝对路径传递。

## 8. 稳定交互边界

### 8.1 Session 与 Automation

- `SessionRecord` 继续只绑定 `workspace_id`
- `AutomationProjectRecord` 继续只绑定 `workspace_id`
- session 或 automation 不直接持有 provider、目录根或 SSH 配置

### 8.2 Role Memory

- durable role memory 仍按 `role_id + workspace_id` 存储
- mount 只影响执行面，不影响长期记忆分区

### 8.3 Tool Runtime

工具运行时改为依赖 workspace scope，而不是单一本地目录句柄。

工具层只感知：

- `workspace_id`
- `mount_name`
- workspace-qualified path

### 8.4 Approval 与 Background Task

- shell 审批键切换为稳定的 `workspace_id + mount_name`
- 后台任务运行上下文也按 `workspace_id + mount_name` 建模
- 不再依赖某个本地绝对目录作为唯一标识

## 9. 路径与命令语法

### 9.1 Workspace 路径

统一路径语法：

`mount_name:/path/to/file`

规则：

- `mount_name` 必须存在于当前 workspace
- `/path/to/file` 是 mount 根下的 provider 内部路径
- 未带前缀时，只能回落到 `default_mount`

### 9.2 Shell

`shell` 增加显式 `mount` 参数。

规则：

- 多 mount workspace 下，未指定 mount 且无法安全回落时直接报错
- `workdir` 相对所选 mount 的 `working_directory`
- shell、写入、编辑、后台命令都必须严格限定在该 mount 的可写范围内

### 9.3 只读旁路兼容

为了兼容现有行为，首版保留主机本地只读越界能力，但它不是 workspace provider 合同的一部分：

- 仅 `read`
- 仅 `grep`
- 仅 `glob`
- 仅显式主机本地绝对路径

限制：

- 不支持远端 provider 越界读取
- 不支持写入、编辑、shell、后台任务越界
- 不把 `../` 相对逃逸视为多 mount 新合同的一部分

## 10. API 合同

### 10.1 Workspace 创建与更新

workspace 创建/更新请求改为：

- `workspace_id`
- `default_mount_name`
- `mounts[]`

每个 mount 至少包含：

- `mount_name`
- `provider`
- `provider_config`
- `working_directory`
- `readable_paths`
- `writable_paths`

### 10.2 Workspace 查询

workspace 查询接口返回：

- workspace 基本信息
- default mount
- mounts 列表

### 10.3 Mount-Aware 文件接口

以下接口改为支持 `mount`：

- `snapshot`
- `tree`
- `diffs`
- `diff`
- `preview-file`

`snapshot` 的根节点改为 workspace 逻辑根，第一层 children 是 mount 名称。

### 10.4 SSH Profile 接口

新增系统级 SSH profile API/CLI/SDK：

- list
- get
- create/update
- delete

workspace mount 只能引用已存在的 `ssh_profile_id`。

## 11. Prompt 与指令发现

系统提示词注入：

- 当前 workspace id
- default mount
- mounts 清单
- provider 类型
- 每个 mount 的关键能力说明

首版只在本地 mount 上保留本地指令文件发现逻辑，例如：

- `AGENTS.md`
- `CLAUDE.md`
- `GEMINI.md`

远端 mount 暂不做自动指令发现，避免首版把远端文件发现、缓存和优先级问题一并放大。

## 12. 错误模型

provider 私有错误必须映射到统一 workspace 域错误，例如：

- `workspace_mount_not_found`
- `workspace_default_mount_required`
- `workspace_provider_capability_unsupported`
- `workspace_path_out_of_scope`
- `workspace_provider_connection_failed`
- `workspace_provider_execution_failed`

调用方不能直接依赖 provider 私有报错文本。

## 13. 测试要求

至少覆盖：

- 单目录旧数据迁移为 `default` local mount
- 多 local mounts
- local + ssh 混合 workspace
- mount 路径解析与默认 mount 回落
- shell 审批键迁移到 `workspace_id + mount_name`
- session 和 automation 继续只绑定 `workspace_id`
- role memory 继续按 `workspace_id` 隔离
- 主机本地只读越界兼容仍可用
- 不支持的 provider 能力返回统一错误

## 14. 实施顺序

1. 先更新文档，冻结合同
2. 改 workspace 数据模型和仓储迁移
3. 落 provider SPI 和 workspace runtime facade
4. 改 tool runtime、shell、background task、approval
5. 改 API/SDK/CLI
6. 补测试与数据库文档

## 15. 首版结论

首版正式交付范围：

- workspace 作为稳定作用域中心
- 多 mount
- `local` provider
- `ssh` provider
- 系统级 SSH profile
- mount-aware tool/runtime/API

首版明确不交付：

- object storage provider
- mount 级长期记忆
- SSH 密码托管
- 远端指令文件自动发现
- 跨 mount 聚合 diff
