# 页面与布局设计

## 页面骨架

`frontend/dist/index.html` 定义了长期存在的页面骨架：

- `.app-shell`：全屏应用容器，纵向包含 topbar 和 app container。
- `.topbar`：顶部栏，包含 sidebar toggle、workspace 标题、语言、observability、settings、theme、subagents toggle。
- `.app-container`：主体横向布局，包含左侧栏、主工作区、右侧 rail。
- `.sidebar`：左侧项目和会话列表，底部显示后端状态。
- `.workspace`：主工作区，可显示 observability view、project view 或 chat container。
- `.chat-container`：消息滚动区和输入区。
- `.right-rail`：subagent rail 与 inspector。
- `.input-container`：prompt composer、恢复审批入口、模式/role 控件、usage strip。

CSS 中 `base.css` 定义主题 token，`layout.css` 定义 shell、sidebar、workspace、composer 等主要布局，`components/*` 定义页面局部样式。

动画、过渡和加载态的完整说明见 `motion-and-loading-states.md`。本页只在各页面小节说明动效入口，不重复展开具体 keyframes。

## 顶部栏

入口 DOM：

- `#toggle-sidebar`
- `#language-toggle-btn`
- `#observability-btn`
- `#settings-btn`
- `#toggle-theme`
- `#toggle-subagents`

负责模块：

- `components/navbar.js` 绑定 sidebar、theme、right rail 等布局行为。
- `components/settings.js` 打开设置弹窗。
- `components/observability.js` 打开观测视图。
- `utils/i18n.js` 切换语言。

页面表现：

- 左侧是应用标题和 sidebar toggle。
- 右侧是一排操作按钮，语言按钮显示当前语言，settings/theme/observability 使用图标按钮，subagents 按钮显示汇总状态。
- topbar 常驻，主工作区切换时不重建。
- 按钮 hover、focus、disabled 状态使用轻量 transition；subagents toggle 会驱动右侧 rail 的展开/收起表现。

## 左侧栏

入口 DOM：

- `#projects-list`
- `#rounds-list`
- `#back-btn`
- `#backend-status`
- `#sidebar-resizer`

负责模块：

- `components/sidebar.js`
- `components/sessionSidebarStore.js`
- `components/sessionSearch.js`
- `utils/backendStatus.js`

主要状态：

- 当前 workspace/project 列表。
- 当前 session 列表、排序模式、活动 session、terminal run 未读/失败/停止指示。
- rounds mode 和 projects mode 的切换状态。

页面表现：

- 默认显示项目和会话树。
- 进入 round 导航模式时，`#rounds-list` 显示轮次列表，back 按钮返回项目/会话列表。
- 底部 status indicator 显示后端 checking、online、offline 等状态。
- sidebar 可折叠，也可通过 resizer 调整宽度。
- session item 插入、删除、切换目标和搜索结果进入有独立动画，详见动效文档的“会话列表与侧边栏”。

空态、加载态、错误态：

- 没有项目时显示新建项目相关空态。
- 会话或 subagent session 加载中会展示 loading 行。
- 删除、fork、加载失败等错误通过 feedback dialog、toast 或 system log 呈现。

关键交互：

- 选择 session 会触发 `agent-teams-select-session` 或直接调用注册的 `selectSession` handler。
- 可按最近或名称排序。
- 可新建 session、删除 subagent session、fork/remove workspace。
- feature navigation 可进入 Skills、Automation、Gateway 等 project feature view。

依赖 API：

- sessions、workspaces、automation、gateway、trigger 等 API 通过 `core/api` facade 调用。

## 新会话草稿页

入口 DOM：

- 主容器由 `components/newSessionDraft.js` 在 workspace 中渲染。
- 输入仍复用 `#prompt-input`、`#chat-form`、`#send-btn` 等 composer DOM。

负责模块：

- `components/newSessionDraft.js`
- `components/newSessionDraftView.js`
- `components/newSessionDraftQuickCards.js`
- `components/newSessionDraftAside.js`
- `components/newSessionDraftIcons.js`
- `app/prompt.js`

主要状态：

- `state.pendingNewSessionActive`
- `state.pendingNewSessionWorkspaceId`
- `state.currentSessionMode`
- `state.currentNormalRootRoleId`
- `state.currentOrchestrationPresetId`
- prompt attachment、mention、slash command 状态由 `app/prompt.js` 维护。

用户可见区域：

- workspace 选择区域。
- 会话模式 segmented control：Normal / Orchestration。
- normal role select 或 orchestration preset select。
- 常用能力快捷卡片。
- 输入框、mention 菜单、附件预览、token chip。
- 开始按钮。
- 页面进入、快捷卡片、workspace 控件和 mention 菜单的动效入口见 `motion-and-loading-states.md`。

空态、加载态、错误态：

- 没有 workspace 时提示先选择或添加 workspace。
- role/preset 加载失败时使用 system log 提示，控件回退为空。
- composer 校验失败时通过 input status 或 toast 提示。

关键交互：

- 选择 workspace 后更新 pending session workspace。
- 切换 normal/orchestration 模式会改变 role/preset 控件可见性。
- 快捷卡片会把预设 prompt 填入 composer。
- `@` 触发 repository、files、skills 等 mention。
- `/` 触发 workspace command autocomplete。
- 点击 Start 或 Enter 提交。

依赖 API：

- `fetchWorkspaces`
- `fetchRoleConfigOptions`
- `fetchOrchestrationConfig`
- `fetchCommands`
- `searchWorkspacePaths`
- `resolveCommandPrompt`
- `startNewSession`
- `updateSessionTopology`

## 会话聊天页

入口 DOM：

- `#chat-container`
- `#chat-messages`
- `#input-container`
- `#recovery-approval-host`
- `#prompt-input`
- `#prompt-attachments`
- `#prompt-mention-menu`
- `.composer-actions`
- `#resume-run-btn`
- `#voice-input-btn`
- `#send-btn`
- `#stop-btn`

负责模块：

- `app/session.js`
- `app/sessionView.js`
- `app/prompt.js`
- `app/recovery.js`
- `components/messageRenderer/`
- `components/messageTimeline/`
- `components/rounds/`

主要状态：

- `state.currentSessionId`
- `state.isGenerating`
- `state.activeRunId`
- `state.currentRecoverySnapshot`
- message timeline store 中的历史和流式状态。

用户可见区域：

- 历史消息列表。
- 流式输出块，包括 text/output delta、thinking、tool call、tool result、approval controls。
- Markdown、代码高亮、图片或富内容渲染。
- 最后一条回答复制按钮。
- composer 附近的上下文窗口、session token usage、debug badge。
- composer 输入框内的操作按钮由 `.composer-actions` 统一承载，运行控制在左、语音输入居中、发送在右，避免 stop/resume/send/voice 在运行中插入消息时互相遮挡。
- stop 或 resume 按钮在运行中或恢复态显示；已配置 STT 时，语音输入按钮不因为运行控制按钮显示而隐藏，运行中可继续向 prompt 写入待插入内容。未配置 STT 时，语音按钮默认隐藏。
- 流式 caret、typing/loading、thinking/tool block 插入和 session switch loading 详见动效文档。

空态、加载态、错误态：

- 会话切换时 chat container 进入 pending/switching/ready 状态，并显示 loading node。
- 历史加载失败会通过 system log 或错误反馈呈现。
- stream 创建失败会释放 busy 状态并在 system log 记录。

关键交互：

- Enter 发送，Shift+Enter 换行。
- 粘贴图片或文件时生成 attachment。
- 运行中主 prompt 可根据 runtime inject 状态继续输入，send 走运行中插入消息；YOLO、thinking 等运行参数控件保持禁用。
- 已配置 STT 时，点击麦克风或长按空格可启动语音输入；长按空格时自动聚焦 prompt，松开后停止语音输入。未配置 STT 时，空格保持普通文本输入行为。
- stop 按钮调用 run stop。
- approval resolved 后触发恢复尝试。

依赖 API：

- `fetchSessionHistory`
- `sendUserPrompt`
- `stopRun`
- `fetchSessionRecovery`
- `resolveToolApproval`
- `answerUserQuestion`
- `resumeRun`

## 轮次视图

入口 DOM：

- `#rounds-list`
- `#chat-messages`
- timeline 节点由 `components/rounds/timeline.js` 和 message timeline 渲染。

负责模块：

- `components/rounds/index.js`
- `components/rounds/timeline.js`
- `components/rounds/navigator.js`
- `components/rounds/paging.js`
- `components/rounds/scrollController.js`
- `components/rounds/todo.js`
- `components/messageTimeline/*`

主要状态：

- 当前 session 的 round 列表。
- 当前选中 round。
- round run 状态、todo 状态、retry 状态。
- 分页和滚动锚点。

用户可见区域：

- 左侧 round list 或浮动/docked navigator。
- 主区 round card、prompt、answer、tool/thinking/message block。
- todo card 和 retry 操作。
- 历史分页加载出的旧 round。
- round 内容和恢复 overlay 的动效保持克制，重点是状态可感知和滚动稳定。

空态、加载态、错误态：

- 没有 round 时显示空历史。
- 分页加载中保持滚动位置。
- retry 或 round 加载失败通过状态块或 system log 呈现。

关键交互：

- 点击 round item 切换当前轮次。
- 滚动时 navigator 同步当前可见 round。
- 长历史通过分页和虚拟可见窗口控制渲染量。
- run 事件会 overlay 到当前 round recovery state。

依赖 API：

- `fetchSessionRounds`
- `fetchSessionRound`
- `fetchSessionTasks`
- run/recovery 相关 API。

## 项目与 Workspace 视图

入口 DOM：

- `#project-view`
- `#project-view-title`
- `#project-view-summary`
- `#project-view-content`
- `#project-view-reload`
- `#project-view-close`

负责模块：

- `components/projectView.js`
- feature 局部复用 `settings/clawhubSettings.js` 和 `settings/githubSettings.js` 的渲染/handler。

主要状态：

- `state.currentMainView`
- `state.currentProjectViewWorkspaceId`
- `state.currentFeatureViewId`
- `currentProjectViewMode`
- `currentWorkspace`
- `currentSnapshot`
- `currentMountName`
- tree、diff、feature view 的局部状态。

用户可见区域：

- toolbar：标题、摘要、刷新、关闭。
- workspace snapshot：文件树、挂载点、workspace 信息。
- diff 视图：文件变更列表、diff 内容。
- open workspace/root 操作。
- feature navigation：Skills、Automation、Gateway。
- workspace tree、diff、feature navigation 和 feature loading 的动效入口见动效文档。

空态、加载态、错误态：

- workspace 不存在或未选择时显示空态。
- snapshot、tree、diff 加载中显示加载 UI。
- tree 节点加载失败记录错误并可重试刷新。

关键交互：

- 打开项目进入 workspace mode。
- 切换 feature 进入 skills、automation、gateway。
- 选择文件树路径加载子树或预览。
- 刷新重新拉取 snapshot。
- 关闭返回 session/chat 视图。

依赖 API：

- `fetchWorkspaceSnapshot`
- `fetchWorkspaceTree`
- `fetchWorkspaceDiffs`
- `fetchWorkspaceDiffFile`
- `openWorkspaceRoot`
- `updateWorkspace`
- feature 相关 API。

## Observability 视图

入口 DOM：

- `#observability-view`
- `#observability-global-btn`
- `#observability-session-btn`
- `#observability-scope-indicator`
- `#observability-overview`
- `#observability-trends`
- `#observability-breakdowns`
- `#observability-back-btn`

负责模块：

- `components/observability.js`

主要状态：

- 当前 scope：global 或 session。
- 当前 session label。
- overview、trend、breakdown 请求结果。

用户可见区域：

- 顶部 toolbar，展示标题、scope 切换、当前 session。
- overview 指标区。
- trends 区。
- breakdowns 区。
- scope 切换和指标区域以 hover/focus transition、loading/empty/error 状态为主，不做强数字动画。

空态、加载态、错误态：

- session scope 没有当前 session 时显示不可用或空态。
- 请求加载中显示占位。
- 请求失败显示错误提示并记录日志。

关键交互：

- 顶部按钮切换 global/session scope。
- back 返回主会话或项目视图。
- session 切换事件会刷新 session scope 的 label 和数据。

依赖 API：

- `fetchObservabilityOverview`
- `fetchObservabilityBreakdowns`

## 右侧 Rail 与 Inspector

入口 DOM：

- `#right-rail`
- `#right-rail-resizer`
- `#subagent-role-select`
- `#subagent-status-summary`
- `#subagent-role-meta`
- `#agent-drawer`
- `#rail-inspector`
- `#system-logs`

负责模块：

- `components/subagentRail.js`
- `components/subagentSessions.js`
- `components/agentPanel/*`
- `utils/logger.js`
- `components/navbar.js`

主要状态：

- `state.rightRailExpanded`
- `state.activeSubagentSession`
- `state.activeView`
- `state.activeAgentRoleId`
- `state.activeAgentInstanceId`
- `state.sessionAgents`
- `state.sessionTasks`

用户可见区域：

- subagent 当前 agent 名称、id、role select、token badge。
- Memory Bank 角色记忆列表和 stop 按钮。
- 状态摘要和 role meta。
- agent drawer 中的 agent history、Memory、task prompt 等面板。
- inspector 中的 system logs。
- run indicator、subagent session loading、project menu、rail 展开收起等动效详见 `motion-and-loading-states.md`。

空态、加载态、错误态：

- 没有 subagent 时显示空提示。
- 加载 subagent 列表时显示 loading。
- Memory Bank 加载或 stop 失败通过 log/feedback 呈现。

关键交互：

- 顶部 subagents toggle 展开/收起 rail。
- role select 切换观察的 agent。
- stop 按钮停止 subagent 或相关 run。
- system log 持续追加前端运行日志。

依赖 API：

- `fetchSessionAgents`
- `fetchSessionSubagents`
- `fetchAgentMessages`
- `fetchMemories`
- `stopBackgroundTask`
