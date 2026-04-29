# API 与事件设计

## API Facade

前端所有后端调用应通过 `frontend/dist/js/core/api/index.js` 统一导出的 facade。它按后端领域聚合具体模块：

- `sessions.js`：session、history、round、tasks、agents、subagents、recovery、topology。
- `runs.js`：创建 run、stop/resume、gate、tool approval、user question、background task、message injection。
- `roles.js`：role config、options、validate。
- `system.js`：配置状态、模型、MCP、commands、hooks、agents、notifications、web、proxy、GitHub、ClawHub、SSH、environment、health。
- `workspaces.js`：workspace 列表、snapshot、tree、diff、open、fork、search。
- `triggers.js`：Feishu/GitHub trigger 相关接口。
- `gateway.js`：WeChat、Xiaoluban gateway。
- `automation.js`：automation project、delivery binding、sessions、run now。
- `observability.js`：overview 和 breakdowns。
- `token_usage.js`：run/session token usage。
- speech config 和 STT WebSocket URL 由 system/speech facade 暴露，供 Speech settings 与语音输入组件复用。

组件不应直接绕过这些 facade 在局部重新实现请求封装。新增 API 时应先放入对应领域模块，再从 `core/api/index.js` 导出。

## 请求策略

共享请求 helper 位于 `core/api/request.js`。

### requestJson

`requestJson(url, options, errorMessage)` 是基础 JSON helper：

- GET 和 HEAD 默认使用 `cache: 'no-store'`。
- 成功后返回 `res.json()`。
- HTTP 非 2xx 时尝试解析后端 error payload，并构造带 `status`、`detail`、`url`、`method` 的 Error。
- 请求异常时记录 frontend log，并派发后端 offline hint。
- AbortError 原样抛出，不当作普通错误记录。

### requestJsonManaged

`requestJsonManaged(key, url, options, errorMessage, options)` 用于 GET 请求合并和限流：

- 只管理 GET 请求，非 GET 回退到 `requestJson`。
- 使用 request key 做短 TTL 缓存，默认 600ms。
- 相同 key 的 in-flight 请求会合并，多个消费者共享同一个 promise。
- 支持 AbortSignal，消费者 abort 不会必然取消所有共享请求。
- 支持 lane 并发限制：
  - `critical`：4
  - `normal`：6
  - `heavy`：2
- 支持 high priority，把请求插到队列前端。
- `invalidateManagedRequests(prefix)` 会清理缓存并 abort in-flight。
- `invalidateManagedRequestCache(prefix)` 只失效缓存和 in-flight entry，不强调 abort 所有底层请求。

### 后端状态 Hint

请求成功会 emit `agent-teams-backend-status-hint` online，异常会 emit offline。hint 有 30 秒重复抑制，避免频繁刷新 UI。

`utils/backendStatus.js` 监听该 hint，并结合主动探测更新左下角 backend status。

## 主要 API 依赖地图

会话聊天：

- `fetchSessions`
- `fetchSessionHistory`
- `fetchSessionRounds`
- `fetchSessionRound`
- `fetchSessionRecovery`
- `sendUserPrompt`
- `stopRun`
- `resumeRun`

prompt composer：

- `fetchRoleConfigOptions`
- `fetchOrchestrationConfig`
- `fetchCommands`
- `resolveCommandPrompt`
- `searchWorkspacePaths`
- `updateSessionTopology`
- `fetchSpeechConfig`
- `createSpeechSttWebSocketUrl`

workspace/project：

- `fetchWorkspaces`
- `fetchWorkspaceSnapshot`
- `fetchWorkspaceTree`
- `fetchWorkspaceDiffs`
- `fetchWorkspaceDiffFile`
- `openWorkspaceRoot`
- `updateWorkspace`

settings：

- model、MCP、commands、hooks、roles、orchestration、notifications、web、proxy、workspace、environment 等均通过 `system.js` 和 `roles.js` 暴露。
- speech 设置通过 speech config API 读取/保存 STT profile、语言、提示词和高级实时转写参数。

project features：

- Skills 主要使用 system/ClawHub/skills 配置接口。
- Automation 使用 `automation.js`。
- Gateway 使用 `gateway.js` 和 `triggers.js`。

## 浏览器事件约定

前端用 DOM `CustomEvent` 做少量跨组件通知。事件名以 `agent-teams-*` 为主。

### session 相关

- `agent-teams-select-session`
  - 触发方：sidebar、搜索或其他组件。
  - detail：`{ sessionId }`
  - 处理方：`app/bootstrap.js` 监听后调用 `selectSession(sessionId)`。
- `agent-teams-session-activated`
  - 触发方：`app/session.js`，在设置 `state.currentSessionId` 后立即派发。
  - detail：`{ sessionId }`
  - 用途：通知依赖当前 session 的 UI 尽快同步。
- `agent-teams-session-selected`
  - 触发方：`app/session.js`，session history 和 view hydration 完成后派发。
  - detail：`{ sessionId }`
  - 用途：通知依赖完整 session 内容的 UI 刷新。
- `agent-teams-session-selection-cancelled`
  - 处理方：`app/session.js`。
  - 用途：取消当前 session selection 和 loading 状态。

### subagent 相关

- `agent-teams-select-subagent-session`
  - detail：`{ sessionId, subagent }`
  - 处理方：`app/bootstrap.js` 调用 `selectSubagentSession()`。
- `agent-teams-subagent-session-selected`
  - detail：`{ sessionId, instanceId }`
  - 触发方：`app/session.js`，subagent session 打开后派发。

### run/recovery 相关

- `run-approval-resolved`
  - detail：`{ runId }`
  - 处理方：`app/bootstrap.js`，调用 `resumeRecoverableRun()`。
- recovery 内部还会通过 round overlay 和 component render 函数同步 UI，不要求所有状态都走全局事件。

### 状态和偏好

- `agent-teams-backend-status-hint`
  - detail：`{ status }`
  - 触发方：`requestJson()`。
  - 处理方：backend status monitor。
- layout、theme、language 等偏好主要由对应组件和 localStorage/API 维护。

## SSE Event Router

SSE 事件统一进入 `core/eventRouter/index.js` 的 `routeEvent(evType, payload, eventMeta)`。

核心职责：

- 用 run id 和 event id 去重。
- 判断是否 subagent run。
- 更新 task instance map、task status map。
- 对 token usage、todo、background task 做专项处理。
- 将事件分发到 run/tool/human/notification handler。
- terminal event 后清理该 run 的 seen event ids。

### runEvents

`runEvents.js` 处理模型和 run 生命周期：

- run started/resumed/completed/failed/stopped。
- model step started/finished。
- LLM retry/fallback 事件。
- text/output delta。
- thinking started/delta/finished。
- generation progress。
- subagent terminal。

它主要更新 message renderer、round 状态、agent/subagent 状态和 recovery。

### toolEvents

`toolEvents.js` 处理工具调用：

- tool call。
- tool input validation failed。
- tool result。
- tool approval requested/resolved。

它主要更新 tool block、approval controls 和 recovery。

### humanEvents

`humanEvents.js` 处理人工 gate 和 subagent 控制：

- subagent gate。
- subagent stopped/resumed。
- gate resolved。

### notificationEvents

`notificationEvents.js` 处理 `notification_requested`，把后端通知请求映射到前端通知服务。

### background task

background task event 在 router 中先判断 payload 是否 displayable。可展示事件会：

- 调用 `rememberNormalModeSubagentFromBackgroundTask()`。
- 调用 `applyBackgroundTaskEvent()`。
- 延迟刷新 recovery continuity。

