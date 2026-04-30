# 动效与加载状态设计

本文档描述当前 `frontend/dist` 中已经实现的动画、过渡和加载状态。动效主要由 CSS class、DOM 插入、运行态 class 和 hover/focus/disabled 状态触发；JavaScript 负责切换状态，CSS 负责表达动效。

## 设计目标

前端是高密度工作台界面，动效的作用是给用户状态反馈，而不是制造视觉表演：

- 轻量：多数动效控制在短时淡入、位移、旋转或 opacity 变化。
- 可感知：会话切换、列表增删、加载中、流式输出、modal 打开等状态需要被看见。
- 不干扰：聊天、代码、tool result、设置表单是阅读和操作密集区域，动效不能影响文本可读性。
- 不改变布局：动效应尽量使用 `opacity`、`transform`、`box-shadow`、颜色或边框变化，避免触发布局跳动。
- 和真实状态绑定：loading spinner、streaming caret、busy 按钮必须对应实际异步或流式状态。

## Keyframes 盘点

### 全局基础动画

来源：`frontend/dist/css/base.css`

- `fadeIn`：通用淡入。
- `slideUp`：轻微上移动画，用于提示或块级内容进入。
- `spin`：通用旋转 loading。
- `typing`：输入/生成中的点状节奏。
- `streamingCaretPulse`：流式输出光标闪烁。

### 会话与切换动画

来源：`frontend/dist/css/components/interface.css`

- `sessionItemEnter`：session item 插入时淡入并轻微位移。
- `sessionItemRemove`：session item 删除时收起和淡出。
- `sessionSwitchSpinner`：会话切换 loading spinner。
- `sessionSwitchContentReady`：会话内容 ready 后的轻微进入反馈。
- `sessionItemSwitchTarget`：目标 session item 被切换选中时的状态反馈。

来源：`frontend/dist/css/components/projects.css`

- `sessionItemTargetOverlay`：session item target overlay 的视觉反馈。

来源：`frontend/dist/css/components/session-search.css`

- `sessionSearchResultEnter`：会话搜索结果进入时淡入。

### 新会话草稿动画

来源：`frontend/dist/css/components/new-session-draft.css`

- `newSessionDraftEnter`：新会话草稿页进入时淡入和轻微位移。

### 设置弹窗动画

来源：`frontend/dist/css/components/settings.css`

- `settingsModalEnter`：设置弹窗打开时的 shell 进入。
- `settingsPanelEnter`：设置 tab panel 切换进入。
- `settingsCardEnter`：设置卡片或局部块进入。

### Subagent 与项目会话动画

来源：`frontend/dist/css/components/subagent.css`

- `sessionRunIndicatorSpin`：session run indicator 的运行中旋转。
- `sessionRunTimeFadeIn`：运行时间信息淡入。
- `projectSessionVisibilityEnter`：项目 session 显示时进入。
- `projectSessionVisibilityExit`：项目 session 隐藏时退出。
- `projectMenuEnter`：项目菜单打开时进入。
- `subagentSessionLoadingSpin`：subagent session 加载 spinner。

### Tool 详情动画

来源：`frontend/dist/css/components/tools.css`

- `tool-detail-in`：tool detail 展开或插入时进入。

## 页面级动效表现

## 新会话草稿页

入口模块：

- `components/newSessionDraft.js`
- `components/newSessionDraftView.js`
- `components/newSessionDraftQuickCards.js`
- `components/newSessionDraftAside.js`

主要动效：

- 草稿页根节点使用 `newSessionDraftEnter`，进入时从轻微下移和透明状态过渡到稳定状态。
- quick cards、workspace 区域和 composer 控件通过 hover/focus transition 表示可点击、可编辑。
- mention 菜单打开和选项 hover 依赖组件样式中的 transition，而不是额外 JS 动画。

触发来源：

- 没有可选 session 时 `openNewSessionDraft("")` 插入草稿视图。
- 切换 workspace、mode、role/preset 时通过 class 和控件 disabled 状态改变视觉。

维护要点：

- 草稿页进入动效不应改变 composer 的实际高度。
- quick card hover 只做颜色、边框、阴影或轻微 transform，避免卡片重排。

## 会话列表与侧边栏

入口模块：

- `components/sidebar.js`
- `components/sessionSidebarStore.js`
- `components/sessionSearch.js`

主要动效：

- 新 session item 插入时使用 `sessionItemEnter`。
- 删除 session item 时使用 `sessionItemRemove`。
- 目标 session 切换时使用 `sessionItemSwitchTarget` 或 target overlay。
- 会话搜索结果使用 `sessionSearchResultEnter`。
- sidebar 折叠、宽度调整和按钮 hover 依赖 transition。

触发来源：

- `loadProjects()`、`scheduleSessionsRefresh()`、session store 更新会触发列表 DOM 更新。
- 搜索输入改变会重绘结果列表。
- session 切换过程会为目标 item 添加状态 class。

维护要点：

- 列表增删动效需要和真实数据变化一致，不能只做视觉删除而不更新 store。
- session item 的高度变化应短且可预测，避免用户点击目标漂移。

## 会话切换加载态

入口模块：

- `app/session.js`
- `components/messageRenderer.js`
- `components/rounds/`

主要动效：

- session switch pending 时 chat container 添加 `is-session-switch-pending`。
- pending 阶段会创建并显示 loading node；超过短延迟后添加 `is-session-switching`，让较慢请求进入持续 loading 状态。
- 如果 session history 很快返回且还没等到延迟 timer，`finishSessionSwitchLoading()` 会先强制进入一帧 `is-session-switching`，再切到 `is-session-switch-ready`，避免快速切换时 loading/ready 动效完全丢失。
- spinner 使用 `sessionSwitchSpinner`。
- 内容 ready 后添加 `is-session-switch-ready`，使用 `sessionSwitchContentReady` 反馈完成。

触发来源：

- `beginSessionSwitchLoading()` 设置 pending/switching class。
- `finishSessionSwitchLoading()` 移除 pending/switching 并短暂添加 ready class。
- `sessionSelectionToken` 和 AbortController 防止旧请求返回后触发错误动效。

维护要点：

- loading 延迟是为了避免快速切换闪烁；新增加载动效要保留这个思路。
- 快速返回路径也必须能被看见，不能只依赖 80ms timer 成功触发；修改时要保留 pending、switching、ready 三段状态。
- ready 动效只表示视图同步完成，不代表 run 完成。

## 聊天与流式输出

入口模块：

- `components/messageRenderer/stream.js`
- `components/messageTimeline/renderer.js`
- `components/messageTimeline/scrollController.js`
- `core/eventRouter/runEvents.js`

主要动效：

- 流式文本持续追加，末尾通过 `streamingCaretPulse` 提示输出仍在继续。
- thinking、tool call、tool result、status block 插入时使用淡入或局部进入效果。
- 通用 typing/loading 点使用 `typing`。
- 消息区滚动由 scroll controller 控制，避免每次 delta 都强制跳动。

触发来源：

- SSE `text_delta`、`output_delta`、`thinking_delta` 进入 event router 后更新 stream block。
- `run_completed`、`run_failed`、`run_stopped` 会 finalize stream 并清理运行中动效。

维护要点：

- 流式动效必须能被 terminal event 停止。
- caret 或 typing 这类无限动画只能出现在真实生成中。
- 长输出时优先保障滚动、复制和文本选择体验。

## Tool Detail

入口模块：

- `components/messageRenderer/helpers/toolBlocks.js`
- `components/messageRenderer/stream.js`
- `css/components/tools.css`

主要动效：

- tool detail 插入或展开使用 `tool-detail-in`。
- tool header、toggle、copy、approval 按钮使用 hover/focus transition。
- tool result 更新时保持块尺寸稳定，避免结果到达导致页面跳动过大。

触发来源：

- SSE `tool_call` 添加 tool call block。
- `tool_result` 更新已有块。
- `tool_approval_requested` 挂载 approval controls。
- 用户展开/收起 tool detail 时切换对应 class。

维护要点：

- tool detail 动效不能遮挡参数、结果或 approval 按钮。
- tool result 可能很长，进入动效只作用于外层，不逐行动画。

## Recovery、Approval 与 User Question

入口模块：

- `app/recovery.js`
- `components/rounds/timeline.js`
- `css/components/recovery.css`

主要动效：

- recovery banner、approval host、question host 主要通过 transition 和局部淡入表达出现/隐藏。
- busy 状态通过按钮 disabled、loading 文案或 spinner 表示。
- round overlay 会同步恢复状态，但不应重排整个 round 列表。

触发来源：

- `applyRecoverySnapshot()` 更新 `state.currentRecoverySnapshot` 后重新渲染。
- `markToolApprovalPending()`、`markPausedSubagent()` 等运行时函数会触发 recovery banner 更新。
- approval/question action busy map 控制按钮状态。

维护要点：

- 用户决策类 UI 的动效要克制，不能把 approve/reject 按钮移出用户视线。
- action 失败后 pending 项需要保留，错误反馈应稳定显示。

## 设置弹窗

入口模块：

- `components/settings/index.js`
- `css/components/settings.css`

主要动效：

- 打开设置弹窗使用 `settingsModalEnter`。
- 切换 tab 时 panel 使用 `settingsPanelEnter`。
- 卡片或局部内容进入使用 `settingsCardEnter`。
- tab、button、input、toggle、row hover/focus 使用 transition。

触发来源：

- `openSettings()` 显示 modal。
- tab click 更新 `currentTab`，切换 panel 可见性和 action ownership。
- panel load 完成后填充内容。

维护要点：

- panel 切换动画不能影响当前表单值。
- 保存中按钮 disabled 后不能因为 hover transition 看起来仍可点击。
- 设置弹窗内容多且可滚动，动画应局限在 panel 内，不推动 modal shell。

## Project、Workspace 与 Feature View

入口模块：

- `components/projectView.js`
- `css/components/projects.css`
- `css/components/automation.css`
- `css/components/gateway.css`
- `css/components/features.css`

主要动效：

- feature navigation、workspace tree、diff list、automation/gateway 控件主要使用 hover/focus transition。
- session target overlay 使用 `sessionItemTargetOverlay`。
- feature loading 延迟显示，避免快速请求时 loading 闪烁。

触发来源：

- 打开 project view、切换 feature id、刷新 workspace snapshot。
- tree 节点展开、diff 文件选择、automation/gateway 编辑状态变化。

维护要点：

- 文件树和 diff 是高信息密度区域，展开/选中反馈要清楚但不能拖慢操作。
- feature loading 动效要和 request token/AbortController 对齐，旧请求返回不能覆盖新状态。

## Observability

入口模块：

- `components/observability.js`
- `css/components/observability.css`

主要动效：

- scope button、刷新状态、指标卡 hover 使用 transition。
- overview、trend、breakdown 加载时使用占位或 loading 状态。

触发来源：

- topbar observability 按钮打开视图。
- global/session scope 切换。
- 当前 session 变化后刷新 session scope。

维护要点：

- 指标数字变化可以即时更新，不需要强动画。
- 错误态和空态必须比 loading 态优先级更高。

## Subagent Rail 与 Inspector

入口模块：

- `components/subagentRail.js`
- `components/subagentSessions.js`
- `components/agentPanel/`
- `css/components/subagent.css`

主要动效：

- run indicator 使用 `sessionRunIndicatorSpin` 表示运行中。
- run time 使用 `sessionRunTimeFadeIn` 出现。
- project session visibility 使用 enter/exit keyframes。
- project menu 使用 `projectMenuEnter`。
- subagent session loading 使用 `subagentSessionLoadingSpin`。
- rail 展开、按钮 hover、agent drawer 状态使用 transition。

触发来源：

- 顶部 subagents toggle 展开/收起 rail。
- subagent session 加载、选择、删除。
- run event 更新 agent、task、role 状态。
- reflection refresh 或 stop 操作改变按钮 busy/disabled。

维护要点：

- right rail 是辅助工作区，动效不能抢主聊天区注意力。
- loading spinner 必须在 subagent list 加载结束后停止。

## 触发机制

当前动效主要由以下机制触发：

- DOM 插入：新节点带着默认 animation 进入，例如 settings panel、session search result、new session draft。
- CSS class 切换：如 `is-session-switch-pending`、`is-session-switching`、`is-session-switch-ready`、active、busy、loading。
- 运行态 class：run indicator、streaming block、tool approval、disabled 按钮。
- 用户交互伪类：hover、focus、focus-within、disabled。
- JS 定时器：session switch loading 和 ready class 有短延迟，减少闪烁。

## 维护约束

新增或修改动效时遵循这些规则：

- 不要让动画改变文本内容的最终位置，尤其是 chat、tool result、diff、settings form。
- 无限动画只用于真实等待或生成中，例如 spinner、streaming caret、typing。
- 动画时长应短，通常使用 120ms 到 300ms；loading spinner 可循环。
- 优先使用 `transform` 和 `opacity`，谨慎动画化 `height`、`width`、`top`、`left`。
- loading class 必须由真实异步状态驱动，并在 abort、error、success 三类路径中都能清理。
- session switch、feature loading、settings panel load 这类并发区域必须配合 token 或 AbortController，避免旧请求触发旧动画。
- 深浅主题下都要可读，动画不能只依赖单一颜色。
- 动效不能遮挡 approval、delete、stop、save 等关键按钮。
- 新增页面或功能视图时，要在 `pages-and-layout.md` 或 `features.md` 描述页面表现，并在本文档补充动效入口。

