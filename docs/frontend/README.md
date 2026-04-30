# 前端设计文档

本文档组描述 Agent Teams 当前前端实现。前端位于 `frontend/dist/`，由后端 FastAPI 服务在运行时静态托管，是一个基于原生 HTML、CSS 和 ES Modules 的单页应用。当前没有 React、Vue、Vite 或单独源码构建层，`frontend/dist` 下的文件就是需要维护的前端实现。

前端与后端的公共边界是 `/api/*` HTTP JSON 接口和 run 事件 SSE。CLI、SDK、后端仓储等内部实现不应被前端直接访问。

## 阅读顺序

1. `architecture.md`：先了解分层、启动流程和全局状态。
2. `pages-and-layout.md`：理解用户看到的页面区域和每个视图的表现。
3. `features.md`：查看项目功能视图和设置弹窗的详细设计。
4. `runtime-flows.md`：理解会话切换、prompt 提交、SSE、恢复等动态流程。
5. `api-and-events.md`：查看 API facade、请求策略、浏览器事件和 SSE 分发约定。
6. `motion-and-loading-states.md`：查看动画、过渡、加载态和流式输出动效。
7. `styling-and-i18n.md`：查看 CSS、主题和国际化约定。
8. `testing-and-maintenance.md`：查看测试地图和维护规则。

## 主要代码路径

- `frontend/dist/index.html`：静态页面外壳，声明顶部栏、左侧栏、主工作区、输入区、右侧 rail、设置/观测入口等持久 DOM。
- `frontend/dist/js/app.js`：入口 facade，启动应用。
- `frontend/dist/js/app/`：应用编排层，负责 bootstrap、session、prompt、recovery、session view。
- `frontend/dist/js/core/`：核心运行层，负责 API facade、全局状态、run stream、事件路由、提交状态。
- `frontend/dist/js/components/`：UI 组件和页面功能区，包括 sidebar、project view、settings、message renderer、rounds、subagent 等。
- `frontend/dist/js/utils/`：DOM 查询、日志、反馈、i18n、Markdown、后端状态、通知、token 预览等横切工具。
- `frontend/dist/css/`：主题变量、布局和组件样式。

## 前端职责边界

前端负责：

- 展示 workspace、session、round、message、tool、subagent、automation、gateway、settings 等用户界面。
- 通过 `core/api` 调用 `/api/*` 接口。
- 通过 `core/stream.js` 建立 run SSE 连接，并交给 `core/eventRouter` 更新 UI。
- 在浏览器本地维护当前会话、运行中 run、prompt 控件、布局偏好、语言和主题等 UI 状态。

前端不负责：

- 直接读取后端数据库或仓储。
- 绕过 `/api/*` 访问后端内部模块。
- 维护持久业务状态的最终真相。会话、run、workspace、role、hook、trigger、automation 等真相来自后端 API。
