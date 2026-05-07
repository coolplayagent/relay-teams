# 功能视图与设置设计

## Project Feature View 总览

项目功能视图由 `components/projectView.js` 管理。左侧 feature navigation 进入对应 feature 后，主工作区仍使用 `#project-view`，但 `currentProjectViewMode` 和 `state.currentFeatureViewId` 会切到功能模式。

当前 feature id：

- `skills`
- `automation`
- `gateway`

通用页面表现：

- 顶部保留 project view toolbar，展示标题、摘要、刷新和关闭。
- 内容区由 feature 专属 markup 替换。
- 加载时延迟显示 loading，避免快速请求闪烁。
- 请求失败通过 toast、dialog 或 system log 呈现。

## Skills 视图

负责模块：

- `components/projectView.js`
- `components/settings/clawhubSettings.js`

主要状态：

- `currentSkillsStatus`
- 当前 workspace。
- ClawHub 配置和技能列表相关状态。

用户可见区域：

- 技能状态概览。
- reload skills 操作。
- ClawHub 配置入口或状态卡片。
- 技能列表/资源状态。

空态、加载态、错误态：

- 技能状态未加载时显示 loading。
- 没有技能或 ClawHub 未配置时显示配置引导。
- reload 或 probe 失败显示错误消息。

关键交互：

- 刷新技能配置。
- 打开或保存 ClawHub 设置。
- 删除 ClawHub skill。
- 测试 ClawHub 连通性。

依赖 API：

- `fetchConfigStatus`
- `reloadSkillsConfig`
- `fetchClawHubConfig`
- `fetchClawHubSkills`
- `saveClawHubConfig`
- `probeClawHubConnectivity`
- `deleteClawHubSkill`

## Automation 视图

负责模块：

- `components/projectView.js`

主要状态：

- `currentAutomationProjects`
- `selectedAutomationHomeProjectId`
- `currentAutomationHomeDetail`
- `currentAutomationFeatureSection`
- `currentAutomationEditorState`

用户可见区域：

- automation project 列表。
- 选中 automation 的详情，包括 workspace、prompt、session mode、role/preset、delivery binding、最近 sessions。
- automation 编辑器提供固定间隔、每天、工作日、每周、每月、一次性和高级 Cron 调度方式。固定间隔保存为 `interval`，每天/工作日/每周/每月保存为五段式 cron，高级 Cron 直接编辑 `cron_expression`。
- schedules 与相关分区。
- run now、enable/disable、edit、delete 等操作。
- automation editor modal，用于创建或编辑自动化项目。

编辑弹窗表现：

- 基础字段：名称、workspace、prompt。
- 调度字段：daily、weekdays、weekly、monthly、one shot、unsupported fallback。
- session 字段：normal/orchestration，normal role 或 orchestration preset。
- thinking/YOLO 等运行参数。
- delivery binding：可选择 IM/网关投递绑定，并选择 started/completed/failed 事件。
- 保存时按钮进入 submitting，错误显示在弹窗内。

空态、加载态、错误态：

- 没有 automation project 时显示空态和创建入口。
- 详情加载中显示 loading。
- schedule 类型无法解析时要求用户重设。
- delivery binding 不可用或凭据缺失时显示明确错误。

关键交互：

- 新建、编辑、删除自动化项目。
- 启用或禁用自动化项目。
- 立即运行自动化项目，并在启动后记录 session/run 信息。
- 切换选中的 automation project，加载详情和 sessions。

依赖 API：

- `fetchAutomationProjects`
- `fetchAutomationProject`
- `fetchAutomationProjectSessions`
- `createAutomationProject`
- `updateAutomationProject`
- `deleteAutomationProject`
- `enableAutomationProject`
- `disableAutomationProject`
- `runAutomationProject`
- `fetchAutomationDeliveryBindings`
- `fetchAutomationFeishuBindings`
- `fetchRoleConfigOptions`
- `fetchOrchestrationConfig`

## Gateway 视图

负责模块：

- `components/projectView.js`

主要状态：

- `currentGatewayFeatureState`
- Feishu trigger draft。
- Xiaoluban accounts。
- WeChat accounts 和 login session。
- workspace、normal roles、orchestration presets。

用户可见区域：

- Feishu 触发器列表和编辑器。
- Xiaoluban gateway accounts。
- WeChat gateway accounts 和登录流程。
- workspace/role/preset 选择。
- trigger rule、session mode、YOLO、thinking 配置。
- Xiaoluban IM forwarding command 预览。

Feishu 编辑器表现：

- 触发器名称、账号或目标字段。
- workspace 和 session core 配置。
- normal/orchestration 模式切换。
- YOLO 和 thinking 开关。
- 保存/取消操作。

Xiaoluban 表现：

- 账号 token 配置。
- enable/disable/delete。
- IM 配置和回调 URL 相关错误提示。
- token reveal 或连接测试。

WeChat 表现：

- 登录启动、等待登录结果。
- 启用/禁用账号。
- 删除账号。
- 登录中展示连接状态。

空态、加载态、错误态：

- 没有账号时显示添加或登录入口。
- workspace 或 role/preset 依赖未加载时禁用相关控件。
- token 无效、个人 token 类型不对、callback URL 本地不可用、workspace 缺失等错误会映射为用户可读消息。

关键交互：

- 创建、更新、启停、删除 Feishu trigger。
- 创建、更新、启停、删除 Xiaoluban account。
- 启动 WeChat 登录并轮询登录结果。
- 更新 gateway 的目标 workspace、session mode、role/preset。

依赖 API：

- Feishu trigger：`fetchTriggers`、`createTrigger`、`updateTrigger`、`deleteTrigger`、`enableTrigger`、`disableTrigger`。
- Xiaoluban：`fetchXiaolubanGatewayAccounts`、`prepareXiaolubanGatewayAccount`、`createXiaolubanGatewayAccount`、`updateXiaolubanGatewayAccount`、`enableXiaolubanGatewayAccount`、`disableXiaolubanGatewayAccount`、`deleteXiaolubanGatewayAccount`、`fetchXiaolubanGatewayImForwardingCommand`。
- WeChat：`fetchWeChatGatewayAccounts`、`startWeChatGatewayLogin`、`waitWeChatGatewayLogin`、`updateWeChatGatewayAccount`、`enableWeChatGatewayAccount`、`disableWeChatGatewayAccount`、`deleteWeChatGatewayAccount`、`reloadWeChatGateway`。
- 配置依赖：`fetchWorkspaces`、`fetchRoleConfigOptions`、`fetchOrchestrationConfig`。

## 设置弹窗 Shell

入口 DOM：

- 设置弹窗由 `components/settings/index.js` 动态创建并挂载到 document body。
- topbar 的 `#settings-btn` 调用 `openSettings()`。

负责模块：

- `components/settings/index.js`
- 各 panel：`appearanceSettings.js`、`modelProfiles.js`、`speechSettings.js`、`systemStatus.js`、`commandsSettings.js`、`hooksSettings.js`、`agentsSettings.js`、`rolesSettings.js`、`orchestrationSettings.js`、`notifications.js`、`webSettings.js`、`proxySettings.js`、`workspaceSettings.js`、`environmentVariables.js`。

主要状态：

- `currentTab`
- `initialized`
- `panelLoadRequestId`
- `settingsWarmupPromise`
- `ACTION_TAB_OWNERS`

页面表现：

- 弹窗左侧是 settings sidebar 和 tab list。
- 右侧是 panel header、description、body。
- panel action button 的显示由 action ownership 控制，避免不同 tab 的按钮互相污染。
- overlay 点击和关闭按钮可关闭弹窗。
- 打开设置时默认进入 appearance tab，并可预热 model/role/orchestration 等常用配置。

通用交互模式：

- tab click 切换 `currentTab`。
- 切换 tab 时更新标题、描述、可见 panel 和 action buttons。
- 每个 panel 自己负责 load 和 bind handlers。
- 保存、验证、测试等操作走对应 `core/api` facade。
- 成功或失败通过 inline status、toast、dialog 或 system log 展示。

## 设置 Tab 说明

### Appearance

负责外观偏好，包括主题、字体大小、密度等。启动时 `initAppearanceOnStartup()` 会在应用初始化阶段应用持久化外观。

### Model

负责模型配置和 model profiles。提供 provider/model profile 编辑、测试连接、catalog 发现/刷新、fallback 配置等能力。

### Speech

负责语音输入和浏览器朗读配置。语音输入使用后端 STT WebSocket 与模型配置中的 STT profile；朗读使用浏览器原生 `speechSynthesis`。Speech tab 提供 STT 配置选择、语言下拉、提示词保存，并复用 settings shell 的 `ACTION_TAB_OWNERS` 管理 `save-speech-btn`，避免切换 tab 后残留错误的保存按钮。

### MCP

由 system status panel 管理 MCP server 状态。提供 server 列表、启停、连接测试、reload、工具查看等能力。

### Commands

负责 workspace commands 配置。提供命令 catalog、命令创建/更新/删除、预览 prompt resolve 等能力。

### Hooks

负责 runtime hooks 配置和 runtime view。提供 hook 编辑、验证、保存，并展示有效 source scope/path 和 scoped filters。

### Agents

负责 external agents 配置。提供 agent 列表、添加、保存、删除、测试等能力。

### Roles

负责 role config。提供 role 列表、编辑、验证、保存，并保持未知 tools、mcp servers、skills 的显式 mutation 严格校验语义。

### Orchestration

负责 orchestration presets。提供 preset 列表、编辑、保存、取消等能力，并与 composer 的 orchestration preset select 共享配置来源。

### Notifications

负责通知设置保存和展示。

### Web

负责 web connectivity 相关配置和探测。

### Proxy

负责代理配置、保存、reload 和连通性探测。前端只调用代理相关 API，不自行实现网络代理逻辑。

### Remote Workspace

负责 SSH profile、远程 workspace 相关设置。提供 SSH profile 新增、保存、测试、密码 reveal、删除等能力。

### Environment

负责环境变量配置。提供新增、保存、取消、删除等能力。
