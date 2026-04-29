# 测试与维护

## 测试地图

前端测试分布在两个主要目录。

### 单元测试

`tests/unit_tests/frontend/` 覆盖静态前端模块的行为和结构。当前测试主题包括：

- API facade 和 request helper。
- backend status。
- i18n。
- logger。
- Markdown 和 rich content。
- message renderer、message timeline、scroll controller、streaming cursor、tool block。
- run events、stream session overlay、recovery stream/background tasks。
- rounds paging、retry、todo、history。
- session selection、session view、session sidebar store。
- project view、projects sidebar。
- settings shell、accessibility、各 settings panel。
- model profiles、roles、agents、commands、hooks、proxy、workspace、environment、system status。
- subagent rail、subagent sessions、subagent streams。
- prompt tokens、YOLO、workspace prompt。
- speech UI、语音输入状态、composer action rail 布局。
- CSS module split 和样式辅助校验。

### 浏览器集成测试

`tests/integration_tests/browser/` 覆盖真实浏览器或浏览器式场景：

- backend status pressure。
- browser smoke。
- frontend module loading。
- streaming message timeline。
- message copy actions。
- ClawHub browser flow。
- GitHub browser flow。
- voice input audio 和 composer action button layout。

## 推荐验证命令

文档变更通常不需要跑前端测试。前端代码或页面表现变更建议至少运行：

```powershell
uv run --extra dev pytest -q tests/unit_tests/frontend/test_core_api_facade_exports.py
uv run --extra dev pytest -q tests/unit_tests/frontend/test_message_renderer_facade_exports.py
uv run --extra dev pytest -q tests/integration_tests/browser/test_frontend_module_loading.py
```

涉及消息流、SSE、round、recovery 的变更建议补充：

```powershell
uv run --extra dev pytest -q tests/unit_tests/frontend/test_run_events_ui.py
uv run --extra dev pytest -q tests/unit_tests/frontend/test_recovery_stream_ui.py
uv run --extra dev pytest -q tests/unit_tests/frontend/test_message_timeline_ui.py
uv run --extra dev pytest -q tests/integration_tests/browser/test_streaming_message_timeline.py
```

涉及 settings 的变更建议运行对应 panel 测试，例如：

```powershell
uv run --extra dev pytest -q tests/unit_tests/frontend/test_settings_shell_ui.py
uv run --extra dev pytest -q tests/unit_tests/frontend/test_model_profiles_ui.py
uv run --extra dev pytest -q tests/unit_tests/frontend/test_hooks_settings_ui.py
```

涉及 speech、composer action rail 或 session switch loading 的变更建议补充：

```powershell
uv run --extra dev pytest -q tests/unit_tests/frontend/test_speech_ui.py
uv run --extra dev pytest -q tests/integration_tests/browser/test_voice_input_audio.py
uv run --extra dev pytest -q tests/unit_tests/frontend/test_session_selection_ui.py
```

完整仓库自检仍以根目录 `AGENTS.md` 的 pre-commit self-check 为准。

## 维护规则

### 保持接口边界

- 前端只能通过 `/api/*` HTTP/SSE 与后端交互。
- 新增后端接口时，在 `frontend/dist/js/core/api/` 对应领域模块中封装，并从 `core/api/index.js` 导出。
- 组件中不要散落裸 `fetch()`，除非是底层请求 helper 或非常明确的浏览器能力。

### 保持模块边界

- `app/` 负责流程编排，不承载大量局部 UI markup。
- `core/` 负责共享运行能力，不包含页面专属布局。
- `components/` 负责 UI 和功能区。
- `utils/` 放横切工具，不放业务状态机。
- 大文件继续拆分。特别是 project view、settings、message renderer、rounds 相关变更，应优先抽出 cohesive 子模块。

### 状态同步

- 当前会话、run、role、subagent 等共享状态使用 `core/state.js`。
- 跨组件通知优先使用已有 DOM `CustomEvent` 约定。
- 对异步请求要使用 token 或 AbortController 防止旧请求覆盖新 UI。
- session switch、run creation、SSE attach/detach 是高并发区域，修改时要明确处理用户快速切换会话的情况。

### 请求和缓存

- GET 请求优先使用 `requestJsonManaged()`，尤其是列表、状态、配置、观测类接口。
- mutation 后要调用对应 invalidate helper，确保列表和详情刷新。
- 重请求或重 UI 刷新应设置合理 delay，避免 SSE 密集事件导致请求风暴。
- AbortError 不应显示为普通错误。

### SSE 和消息渲染

- 新增 run event type 时，要更新 `core/eventRouter` 的分发、对应 handler、message/round/recovery 表现和测试。
- stream block 更新要走 message renderer/timeline 的公共 API。
- tool approval、user question、paused subagent 等会同时影响 message block、recovery banner 和 round overlay，需要一起验证。
- terminal event 后要清理 run stream state，避免旧 overlay 残留。

### 页面表现

页面或组件新增状态时，至少考虑：

- 正常数据态。
- 空态。
- 加载态。
- 错误态。
- 禁用态。
- 长文本和窄宽显示。
- 深色和浅色主题。
- 中英文文案长度差异。

### 国际化

- 新增文案必须同时添加 `zh-CN` 和 `en-US`。
- 静态 DOM 使用 `data-i18n`、`data-i18n-title`、`data-i18n-aria-label`。
- 动态 HTML 使用 `t()` 或 `formatMessage()`。
- 用户输入和 API 返回内容必须 escape 后再写入 HTML。

### CSS

- 优先使用 `base.css` 的主题变量。
- 新样式放到对应组件 CSS，不要继续扩大不相关文件。
- 避免只在深色或浅色主题下可读的硬编码颜色。
- 固定格式 UI 需要稳定尺寸，防止 hover、loading、label 或动态内容改变布局。

## 文档维护

当以下内容变化时，应同步更新 `docs/modules/frontend/`：

- 新增或移除顶层页面/feature view。
- 调整 `app/`、`core/`、`components/` 的职责边界。
- 新增 SSE event type 或浏览器 CustomEvent。
- 修改 settings tab、project feature、gateway、automation 的页面结构。
- 改变 API facade 或请求策略。
- 改变主题、i18n 或测试组织。
