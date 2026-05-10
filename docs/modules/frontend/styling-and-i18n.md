# 样式与国际化设计

## CSS 组织

前端样式位于 `frontend/dist/css/`。

### 基础样式

- `base.css`
  - 主题变量、字体、颜色、阴影、圆角。
  - `body.light-theme` 下的浅色主题变量。
  - 全局 reset、选区、基础动画 keyframes。动画详情见 `motion-and-loading-states.md`。
- `layout.css`
  - `.app-shell`、`.app-container`、`.topbar`、`.sidebar`、`.workspace`。
  - chat container、chat scroll、input container、prompt attachment、prompt token 等主布局。

### 组件样式

`css/components/` 按功能拆分：

- `base.css`：通用按钮、状态、小组件基础样式。
- `features.css`：feature navigation 和功能入口。
- `session-search.css`：会话搜索。
- `projects.css`：项目、workspace、文件树、diff 等。
- `automation.css`：自动化页面和编辑器。
- `gateway.css`：Connectors 页面、Feishu/Discord/Xiaoluban/WeChat 配置。
- `messages.css`：消息样式 facade，继续引入 messages 子模块。
- `orchestration.css`：编排相关表现。
- `feedback.css`：toast、confirm、form dialog 等。
- `rounds.css`：rounds 样式 facade，继续引入 rounds 子模块。
- `recovery.css`：恢复 banner、approval、question 等。
- `settings.css`：设置弹窗主样式。
- `tokens.css`：token usage。
- `tools.css`：tool call/result block。
- `triggers.css`：trigger 相关 UI。
- `subagent.css`：右侧 rail、subagent、agent drawer。
- `observability.css`：观测视图。
- `interface.css`：界面级控件补充。
- `new-session-draft*.css`：新会话草稿页拆分样式。
- `highlight.css`：代码高亮。

### 子目录拆分

- `components/messages/`
  - `base.css`
  - `actions.css`
  - `blocks.css`
  - `markdown.css`
  - `prompt.css`
  - `status.css`
  - `streaming.css`
  - `thinking.css`
- `components/rounds/`
  - `cards.css`
  - `detail.css`
  - `history.css`
  - `navigator.css`
  - `retry.css`
  - `todo.css`
- `components/settings/`
  - `model-profiles.css`

新增样式时应优先放入对应功能文件，避免继续把大量不相关样式塞入同一个大文件。

动画、过渡和加载状态不在本文展开；维护这些表现时同时参考 `motion-and-loading-states.md`。

## 主题 Token

`base.css` 使用 CSS custom properties 管理主题。常用变量包括：

- 背景：`--bg-base`、`--bg-surface`、`--bg-surface-glass`、`--bg-surface-muted`。
- 文本：`--text-primary`、`--text-secondary`、`--text-on-primary`、`--text-msg-content`。
- 边框：`--border-color`。
- 操作色：`--primary`、`--primary-hover`、`--success`、`--danger`、`--warning`。
- 按钮：`--button-primary-*`、`--button-secondary-*`。
- 工具块：`--bg-tool-header`、`--bg-tool-body`、`--bg-tool-block`。
- 设置弹窗：`--settings-*`。
- 字体：`--font-ui`、`--font-mono`。
- 圆角和阴影：`--radius-*`、`--shadow-*`。

深色主题是默认主题，浅色主题通过 `body.light-theme` 覆盖变量实现。组件样式应引用变量，不应硬编码成只能适配单一主题的颜色。

## 页面视觉约定

整体视觉是紧凑、工具型、控制台式应用，而不是营销页：

- 主布局保持 topbar、sidebar、workspace、right rail 的工作台结构。
- 信息密度偏高，但通过分区、边框、弱背景和状态色保持可扫描。
- 按钮、select、toggle、segmented control 等控件使用稳定尺寸，避免状态变化导致布局跳动。
- 卡片只用于可重复项目、modal 或明确 framed tool，不把页面大段 section 套成多层 card。
- 状态色语义固定：
  - success 表示成功、在线、已完成。
  - danger 表示失败、删除、危险操作。
  - warning 表示警告、等待、需要用户处理。
  - primary 表示当前选择、焦点或主要操作。
- 右侧 rail 和 sidebar 通过 resizer 调整宽度，主工作区应在窄宽下保持 `min-width: 0` 防止溢出。

## 响应与滚动

当前前端是桌面工作台优先：

- `body` 高度为 `100vh`，整体禁止页面级滚动。
- sidebar、chat scroll、project content、settings body 等内部区域自己滚动。
- chat 输入区固定在底部，消息区使用 `flex: 1`。
- 长消息和长历史通过 message timeline、round paging、scroll controller 处理。
- 文件树、diff、设置列表等区域应避免撑破父容器，必要时使用内部滚动。

## Composer 样式约定

composer 包含：

- textarea 输入。
- attachment 预览。
- mention menu。
- `.composer-actions` 操作 rail，统一排布 resume、stop、voice、send。
- topology 控件：mode segmented、normal role、orchestration preset。
- YOLO 和 thinking toggle。
- context/token usage strip。

运行中：

- stop 或 resume 按钮显示在 action rail 左侧。
- 已配置 STT 时，voice 按钮保持可见，不因 stop/resume 显示而隐藏；未配置 STT 时，voice 按钮隐藏且不占用 action rail 空间。
- send 和 prompt 在 runtime inject 可用时继续用于插入消息。
- YOLO、thinking 等运行参数控件禁用。
- input wrapper 继续保持可读 busy 状态。

布局约束：

- 输入框内的浮动按钮只能通过 `.composer-actions` 统一定位；不要再给 `#send-btn`、`#stop-btn`、`#resume-run-btn`、`.composer-voice-btn` 分别设置互相竞争的 right/bottom 坐标。
- `#prompt-input` 右侧 padding 要能容纳 action rail 的最大组合，包含异常情况下 stop 和 resume 同时可见。
- 新会话草稿 composer 的 Start 宽按钮和普通 composer 的图标按钮都要通过同一个 rail 规则保证不遮挡文本、attachment chip、mention menu 或 slash/resource menu。

错误或校验失败：

- 使用 `prompt-input-status` 或 toast 显示用户可理解信息。
- 附件错误可通过 `.prompt-attachments.is-error` 增强视觉。

## Modal 与反馈

反馈工具位于 `utils/feedback.js`，样式在 `components/feedback.css` 和各功能 CSS 中。

常见 UI：

- toast：短反馈。
- confirm dialog：删除、启停、危险操作确认。
- form dialog：需要输入或选择的操作。
- settings modal：大型配置弹窗。
- automation editor modal、gateway modal：feature 内专用编辑弹窗。

Modal 约定：

- overlay 点击可关闭的弹窗应明确处理 pointer down/up，避免拖拽误关。
- 保存中禁用提交按钮。
- 错误优先显示在弹窗内部，必要时同步 toast 或 system log。

## 国际化设计

国际化工具位于 `frontend/dist/js/utils/i18n.js`。

当前支持语言：

- `zh-CN`
- `en-US`

默认语言：

- `zh-CN`

语言来源：

- 后端 UI language settings。
- localStorage：`agent_teams_ui_language`。
- 运行时切换按钮：`#language-toggle-btn`。

## DOM 翻译约定

静态 DOM 使用 data attribute：

- `data-i18n`：设置文本内容。
- `data-i18n-title`：设置 title。
- `data-i18n-aria-label`：设置 aria-label。

JS 动态 HTML 应使用：

- `t(key)` 获取翻译。
- `formatMessage(key, values)` 插值。
- 渲染用户或 API 内容时先 escape，再和翻译拼接。
- 动态插入大量 DOM 后调用 `translateDocument()` 或对应局部同步函数。

## 文案维护规则

新增用户可见文案时：

1. 同时添加 `zh-CN` 和 `en-US` key。
2. key 按功能域命名，例如 `settings.proxy.*`、`automation.schedule.*`、`sidebar.*`。
3. 不在组件中写只支持一种语言的固定文本。
4. title、aria-label、empty state、error message 都属于用户可见文案，应进入 i18n。
