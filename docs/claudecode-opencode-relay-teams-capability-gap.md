# 对齐 Claude Code / opencode 内置 Tool 能力的差距清单

## 代码仓库

- Claude Code / cc-haha: https://github.com/NanmiCoder/cc-haha
- opencode: https://github.com/anomalyco/opencode
- relay-teams: https://github.com/coolplayagent/relay-teams

## 对比口径

- `cc-haha` 作为 Claude Code 源码能力的本地修复版代表。
- `opencode` 以 `packages/opencode/src/tool/registry.ts` 中注册的主内置工具为主要口径，同时参考 tool 源码中已存在但不一定进入主 registry 的实现。
- `relay-teams` 以 `src/relay_teams/tools/registry/defaults.py` 注册的默认工具为主要口径，同时参考 MCP、external agents、computer tools 等工具相关模块。

## 参考证据

- Claude Code / cc-haha 内置工具入口和过滤逻辑：`cc-haha/src/tools.ts`
- Claude Code / cc-haha 工具能力分组：`cc-haha/src/components/agents/ToolSelector.tsx`
- Claude Code / cc-haha MCP helper tools：`cc-haha/src/tools/ListMcpResourcesTool/ListMcpResourcesTool.ts`、`cc-haha/src/tools/ReadMcpResourceTool/ReadMcpResourceTool.ts`
- Claude Code / cc-haha Notebook 工具：`cc-haha/src/tools/NotebookEditTool/NotebookEditTool.ts`
- Claude Code / cc-haha Shell 工具：`cc-haha/src/tools/BashTool/BashTool.tsx`、`cc-haha/src/tools/PowerShellTool/PowerShellTool.tsx`
- opencode 内置工具注册入口：`opencode/packages/opencode/src/tool/registry.ts`
- opencode 文件与 patch 工具：`opencode/packages/opencode/src/tool/read.ts`、`opencode/packages/opencode/src/tool/edit.ts`、`opencode/packages/opencode/src/tool/write.ts`、`opencode/packages/opencode/src/tool/apply_patch.ts`
- opencode Web / code search 工具：`opencode/packages/opencode/src/tool/webfetch.ts`、`opencode/packages/opencode/src/tool/websearch.ts`、`opencode/packages/opencode/src/tool/codesearch.ts`
- opencode agent 默认工具权限中出现 `list`、`codesearch` 等工具名：`opencode/packages/opencode/src/agent/agent.ts`
- relay-teams 默认工具注册入口：`relay-teams/src/relay_teams/tools/registry/defaults.py`
- relay-teams workspace tools：`relay-teams/src/relay_teams/tools/workspace_tools/__init__.py`
- relay-teams task tools：`relay-teams/src/relay_teams/tools/task_tools/__init__.py`
- relay-teams web tools：`relay-teams/src/relay_teams/tools/web_tools/__init__.py`
- relay-teams computer tools：`relay-teams/src/relay_teams/tools/computer_tools/__init__.py`
- relay-teams MCP 模块：`relay-teams/src/relay_teams/mcp/`

## 现状总结

relay-teams 当前默认内置工具已经覆盖基础 Agent 工作流，并且每类工具的职责大致如下：

- 文件 / 工作区工具：`read` 用于读取工作区文本文件、目录与 Notebook 内容并记录读取状态；`office_read_markdown` 用于把 `.pdf`、`.docx`、`.pptx`、`.xlsx` 转成 Markdown 后分页返回；`edit` 用于基于旧文本匹配替换文件内容，包含一定的模糊匹配和 diff 输出；`write` 用于写入完整文件；`write_tmp` 用于向工作区临时目录写入临时产物；`glob` 用于按 glob 模式查找路径；`grep` 用于按文本模式搜索文件内容。
- Shell / 后台任务工具：`shell` 用于在工作区执行命令；`spawn_subagent` 用于从当前工作区上下文启动子代理；`list_background_tasks`、`wait_background_task`、`stop_background_task` 用于查看、等待和停止后台任务。
- Monitor 工具：`create_monitor` 用于创建长期监控任务；`list_monitors` 用于列出已有 monitor；`stop_monitor` 用于停止 monitor。
- 任务编排工具：`create_tasks` 用于把目标拆成可分派任务；`create_temporary_role` 用于临时创建角色；`update_task` 用于修改任务内容；`list_available_roles` 用于列出可用角色；`list_delegated_tasks` 用于查看已委派任务；`dispatch_task` 用于把任务派发给指定角色执行。
- Web 工具：`webfetch` 用于抓取指定网页内容；`websearch` 用于执行网页搜索并返回相关结果。
- Computer Use 工具：`capture_screen` 用于截图；`list_windows` / `focus_window` 用于窗口发现和聚焦；`click_at` / `double_click_at` / `drag_between` 用于鼠标操作；`type_text` / `hotkey` 用于键盘输入；`scroll_view` 用于滚动；`launch_app` / `wait_for_window` 用于启动应用和等待窗口出现。
- IM 工具：`im_send` 用于通过 IM gateway 发送消息，默认隐藏在 role config 中，避免普通角色随意使用。

与 Claude Code / cc-haha、opencode 相比，relay-teams 的基础读写、搜索、shell、web、多 Agent 任务分发已经具备，但在 Notebook 原生编辑、MCP resource 一等工具、patch / multi-edit 风格编辑、LSP 工具、code search、工具级状态展示与部分 Shell/Terminal 专用工具上仍有缺口。

## Tool 能力对齐总表

| Tool 能力域 | Claude Code / cc-haha | opencode | relay-teams 当前能力 | relay-teams 缺口 |
| --- | --- | --- | --- | --- |
| 文件读取 | `FileReadTool` 用于读取文件内容，并处理大文件截断、图片读取、Notebook 内容映射等场景。 | `read` 用于读取文件内容，是模型理解代码和上下文的基础工具。 | `read` 负责工作区文本/目录/Notebook 读取并与 edit 状态联动；`office_read_markdown` 负责 Office/PDF 到 Markdown 的独立转换读取。 | 基础读取具备；Notebook cell 级读取已覆盖，但图片/多媒体读取和更丰富的结构化预览能力仍可继续补齐。 |
| 文件编辑 | `FileEditTool` 做基于旧文本的精确编辑；`FileWriteTool` 写入完整文件；`NotebookEditTool` 专门编辑 `.ipynb` cell，避免直接改 JSON。 | `edit` 执行字符串替换；`write` 写入文件；`apply_patch` 让模型以 patch 方式表达改动；源码中还有 `multiedit` 用于顺序执行多处编辑。 | `edit` 支持文本替换、模糊匹配和 diff 输出；`write` 写完整文件；`write_tmp` 写临时产物。 | 缺 Notebook 原生编辑；缺 apply_patch 风格补丁工具；缺同一文件或多文件 multi-edit 批量编辑工具。 |
| 搜索 / 列举 | `GlobTool` 按路径模式找文件；`GrepTool` 按内容搜索文件。 | `glob` / `grep` 覆盖路径和内容搜索；源码中有 `list` 可输出目录树形结构，但主 registry 启用路径需以实际代码为准。 | `glob` 用于路径匹配；`grep` 用于内容检索。 | 基础搜索具备；可补目录树 / list 工具，让模型先理解目录结构，减少用 grep/glob 代替目录浏览。 |
| Shell | `BashTool` 执行 shell 命令并处理权限、sandbox、危险命令和后台任务；Windows 下还有 `PowerShellTool` 用于 PowerShell 语义。 | `bash` 执行命令，并与权限模型、会话输出展示集成。 | `shell` 用于执行工作区命令；后台任务由 `list/wait/stop_background_task` 配套管理。 | 缺一等 PowerShell tool；shell dialect、安全校验、只读判断、危险命令解释需要与 Claude Code 级别能力对齐评估。 |
| 后台任务 | `BashTool` 可产生后台任务；`TaskOutputTool` 查看任务输出；`TaskStopTool` 停止任务。 | `bash` 与 session / task 展示结合，TUI 中可渲染工具输出。 | `list_background_tasks` 查看后台任务；`wait_background_task` 等待任务；`stop_background_task` 停止任务。 | 缺更完整的 output tail / attach / stream status 语义；后台任务输出与 shell 输出存储、run event 的统一投影不足。 |
| Agent / 子任务 | `AgentTool` 启动子代理；TaskCreate/Get/List/Update/Output/Stop 管理任务；TeamCreate/Delete 管理团队；SendMessage 向 agent 投递消息。 | `task` 用于启动子 agent 做复杂搜索或多步任务，结合 agent 权限模型限制工具范围。 | `create_tasks` 创建可分派任务；`dispatch_task` 派发给角色；`update_task` 更新任务；`list_delegated_tasks` 查看任务；`spawn_subagent` 启动子代理。 | 编排基础具备；缺 task get/output/stop/resume、agent message 投递、任务结果汇总等更细颗粒工具闭环。 |
| Todo / Plan / Question | `TodoWriteTool` 维护当前任务 todo；Enter/Exit Plan Mode 切换计划/执行阶段；AskUserQuestionTool 让模型结构化询问用户。 | `todo` 维护待办；`plan` 管理计划阶段；`question` 向用户提问。 | 主要通过 task tools 表达任务拆分和角色派发，没有独立 todo/plan/question 工具。 | 缺单 agent 局部 todo 工具；缺 plan enter/exit 语义；缺 ask user/question 类结构化交互工具。 |
| Web | `WebFetchTool` 抓取网页内容；`WebSearchTool` 执行搜索。 | `webfetch` 抓取网页；`websearch` 搜索网页；`codesearch` 面向 API / Library / SDK 文档获取代码上下文。 | `webfetch` 抓取网页；`websearch` 搜索网页。 | 基础 Web 具备；缺 `codesearch` 类面向 SDK/API/库文档的 code context search。 |
| MCP tools | `MCPTool` 将外部 MCP server 的 tools 动态纳入工具池。 | MCP service / registry 支持 MCP 连接、鉴权和工具暴露。 | MCP service / registry / CLI 存在，支持配置和管理 MCP。 | 动态 tool 调用已有基础；需要确认默认 runtime tool pool 中动态 MCP tools 的暴露、权限和审计是否与主工具一致。 |
| MCP resources | `ListMcpResourcesTool` 列出 MCP resources；`ReadMcpResourceTool` 读取指定 resource 内容。 | MCP 模块主要侧重 tools/auth，resource 一等工具不是主内置工具重点。 | MCP 模块存在，但默认工具集中没有 resource list/read 工具。 | 缺 MCP resource 一等工具、权限、展示和审计模型。 |
| LSP | `LSPTool` 可提供语言服务相关能力，通常受环境开关控制。 | `lsp` 可暴露语言服务能力，受 experimental flag 控制。 | 无默认 LSP tool。 | 缺 diagnostics / symbols / definitions / references 等 LSP 只读语义工具。 |
| Skill | `SkillTool` 让模型调用或注入扩展技能工作流。 | `skill` 让模型使用已发现 skills，并与 agent 描述结合。 | skills registry、routing、installer 存在，但默认工具集中无统一 `skill` 调用工具。 | 缺面向模型的一等 Skill 调用、选择和解释工具。 |
| Tool search / discovery | `ToolSearchTool` 用于发现和检索可用工具，避免一次性暴露全部工具说明。 | registry 提供 ids/all/tools，并可接入 plugin tools。 | ToolRegistry 存在，但没有默认注册的模型可调用 `tool_search` / `list_tools` 工具。 | 缺 tool discovery / tool search 的模型侧工具能力。 |
| Config / settings | `ConfigTool` 在部分构建中允许模型读取或调整受控配置。 | 配置主要通过 config / server API 暴露。 | 设置主要在 CLI / API / UI 层处理。 | 缺模型可调用的安全配置读取/修改工具；是否需要开放需结合权限模型决定。 |
| Computer Use | 通过 Computer Use 相关 MCP / 工具能力完成截图、点击、输入等桌面操作。 | 非主内置工具重点。 | 默认注册截图、窗口、点击、拖拽、输入、滚动、热键、启动应用、等待窗口等工具。 | relay-teams 工具覆盖较显式；仍需补工具级安全审批、执行回放、失败截图、平台边界说明。 |
| Remote / schedule | CronCreate/Delete/List 用于定时任务；RemoteTriggerTool 用于远程触发，通常受 feature gate 控制。 | 非主内置工具重点。 | automation / gateway 层有相关能力，但默认模型工具集中没有完整 schedule / remote trigger 工具。 | 缺 schedule / remote trigger 的模型侧一等工具抽象。 |

## 差距清单

### 1. Notebook 原生工具缺失

Claude Code / cc-haha 有 `NotebookEditTool`，其职责是按 Notebook cell 操作 `.ipynb`，并保留 Notebook 结构，而不是让模型直接编辑 JSON。`FileEditTool` 在遇到 Jupyter Notebook 时也会引导使用 Notebook 专用工具。relay-teams 当前默认文件工具仍是通用 `read` / `edit` / `write`，更适合普通文本文件。

价值：
- 避免直接按 JSON 修改 `.ipynb` 带来的脆弱性。
- 支持 cell 级别读取、替换、追加、删除和 metadata 保持。
- 更适合数据分析、科研、Notebook 驱动的工程工作流。

建议：
- 新增 `notebook_read` 与 `notebook_edit`，至少支持 cell 级读取、替换、追加、删除。
- 对 `.ipynb` 在通用 `edit` 中给出专用工具提示，避免误改 JSON。
- 记录 Notebook cell diff，进入 tool event / audit。

### 2. MCP Resource 一等工具不足

Claude Code / cc-haha 除动态 MCP tool 调用外，还有 `ListMcpResourcesTool` 与 `ReadMcpResourceTool`：前者列出 MCP server 暴露的资源，后者读取指定资源内容。relay-teams 当前 MCP 模块存在，但默认工具注册集中没有 `list_mcp_resources` / `read_mcp_resource` 这类模型可直接调用的 resource 工具。

价值：
- 很多 MCP server 的核心价值在 resource，而不只是 tool。
- resource 能把文档、远端上下文、配置模板、远端文件系统内容纳入 agent workflow。

建议：
- 在 MCP registry / service / runtime 中补 `list_mcp_resources`、`read_mcp_resource`。
- 明确 resource 权限、展示、缓存和审计模型。
- 在 Web 和 CLI 中展示 MCP server 的 tools 与 resources 两类能力。

### 3. Patch / Multi-edit 风格编辑能力不足

opencode 主 registry 有 `apply_patch`，用于让模型以补丁形式表达文件修改；源码中还有 `multiedit`，用于对同一文件顺序执行多处替换。Claude Code / cc-haha 主要通过 `FileEditTool`、`FileWriteTool` 和 Notebook 专用编辑来覆盖文件改写。relay-teams 的 `edit` 已有较强的模糊替换能力，`write` / `write_tmp` 也支持 diff，但缺少显式 patch 和批量编辑工具。

价值：
- patch 工具更适合跨多处、多文件、可审计的结构化修改。
- multi-edit 能减少多轮工具调用，降低上下文和失败成本。

建议：
- 新增 `apply_patch` 工具，接受 unified diff 或受控 patch 格式。
- 新增 `multi_edit`，支持同一文件多处顺序替换，后续扩展到多文件。
- 将 patch / multi_edit 与现有 approval、diff summary、read-before-write 校验复用。

### 4. LSP 工具缺失

Claude Code / cc-haha 有 `LSPTool`，opencode 有 experimental `lsp` tool。这类工具的核心功能是通过语言服务器获取诊断、符号、定义、引用等语义信息。relay-teams 当前没有默认 LSP tool。

价值：
- LSP 能提供 diagnostics、definition、references、symbols、rename/format 等语义信息。
- 相比纯 grep，LSP 更适合大型代码库定位和安全改动。

建议：
- 先做只读 LSP 工具：`lsp_diagnostics`、`lsp_symbols`、`lsp_definition`、`lsp_references`。
- 将写入类 LSP action，如 rename/format，放到后续阶段并接入审批。
- 与 workspace / session 绑定，避免跨项目语言服务器污染。

### 5. Code Search / 文档上下文工具缺失

opencode 有 `codesearch`，用于面向 API、Library、SDK 文档检索代码上下文。relay-teams 当前有 `websearch` / `webfetch`，前者面向泛搜索，后者面向指定 URL 抓取，但没有专门面向开发文档和代码上下文的 search tool。

价值：
- coding agent 经常需要检索第三方库 API、SDK 用法、版本差异。
- 独立 code search 工具比泛 web search 更容易约束输出和权限。

建议：
- 新增 `code_search`，先接入可配置 provider，避免硬编码单一服务。
- 与 `websearch` 区分用途：`websearch` 面向泛搜索，`code_search` 面向库/API/SDK 上下文。
- 输出结构化来源、摘要和上下文片段。

### 6. Todo / Plan / Question 类交互工具缺失

Claude Code / cc-haha 有 `TodoWriteTool`、Enter/Exit Plan Mode、AskUserQuestionTool；opencode 有 `todo`、`plan`、`question`。这些工具分别用于维护当前任务待办、显式切换计划/执行阶段、向用户请求结构化输入。relay-teams 目前更偏任务编排工具，没有独立 todo、plan、question 工具。

价值：
- todo 是单 agent 内部执行计划的轻量状态，不等同于多 agent task。
- plan enter/exit 能将“计划阶段”和“执行阶段”显式化。
- question 工具能让模型以结构化方式请求用户决策。

建议：
- 新增 `todo_write`，与 task tools 区分：todo 面向当前 run 的局部计划，task 面向多 Agent 分派。
- 新增 `enter_plan_mode` / `exit_plan_mode` 或对应 run phase tool。
- 新增 `ask_user_question`，支持选项、自由文本、超时和审计。

### 7. Agent / Task 工具闭环仍不完整

relay-teams 的 task tools 覆盖 create、update、dispatch、list、role listing，且 workspace tools 有 `spawn_subagent`。这些工具能创建任务、创建临时角色、派发任务和启动子代理。Claude Code / cc-haha 还有 TaskGet、TaskOutput、TaskStop、TeamCreate/Delete、SendMessage 等更细颗粒工具，用于查看单个任务、获取输出、停止任务、管理团队、向 agent 发送消息。

价值：
- 多 Agent 编排需要“创建、派发、查看、停止、恢复、消息投递、输出汇总”的完整工具闭环。
- 只靠 dispatch/list 很难支持复杂失败恢复和人工接管。

建议：
- 补 `get_task`、`get_task_output`、`stop_task`、`resume_task`。
- 补 `send_message_to_agent` 或等价工具，明确与 IM `im_send` 的边界。
- 将 task output 与 run event log、background task output 建立统一投影。

### 8. Shell / PowerShell / Terminal 专用能力不足

Claude Code / cc-haha 除 `BashTool` 外还有 Windows `PowerShellTool`，并且包含大量只读命令、危险命令、路径、sandbox 相关校验。relay-teams 有 `shell` 和后台任务工具，但不是 Bash / PowerShell 分离的一等工具；后台任务也更多通过 list/wait/stop 进行基本管理。

价值：
- Windows 场景下 PowerShell 语义与 Bash 不同，统一 shell 容易导致权限与安全判断不准确。
- 长时间命令需要更好的输出 tail、attach、stop、status 工具。

建议：
- 评估是否新增 `powershell` 工具，或在 `shell` 中显式区分 shell dialect。
- 补 shell output tail / attach / stream status，和 background task tools 打通。
- 对齐只读命令判断、危险命令提示、路径越界与 git 安全校验。

### 9. Tool Search / Tool Discovery 模型侧能力不足

Claude Code / cc-haha 有 `ToolSearchTool`，用于在工具数量较多时让模型检索可用工具。opencode registry 能提供 ids/all/tools，并支持 plugin tool。relay-teams 有 ToolRegistry，但没有默认注册的模型可调用 `tool_search` / `list_tools` 工具。

价值：
- 工具数量变多后，模型需要按任务发现可用工具，而不是一次性暴露全部说明。
- tool discovery 对 plugin / skills / MCP resources 尤其重要。

建议：
- 新增 `list_available_tools` 或 `tool_search`，返回当前 role / run 可用工具、参数摘要、权限状态。
- 将隐藏工具，如 `im_send`，按 policy 控制是否出现在搜索结果。
- 与 roles / skills / MCP / computer tools 统一展示。

### 10. Skill 一等调用工具不足

Claude Code / cc-haha 有 `SkillTool`，opencode 有 `skill`。这类工具让模型能够主动使用已发现的技能或工作流，而不只是被动接收 prompt 注入。relay-teams 有 skills registry、routing、installer，但默认工具集中没有统一的 `skill` 调用工具。

价值：
- skills 不只是 prompt 注入，也可以是模型主动选择的工作流能力。
- 一等 skill 工具能提升可解释性：哪个 skill 被调用、为什么、输出是什么。

建议：
- 新增 `list_skills` / `invoke_skill`，或统一为 `skill` 工具。
- skill 调用事件进入 run timeline / audit。
- 与 skill routing 结果联动，显示自动触发和手动触发的区别。

### 11. Remote / Schedule 类工具缺口

Claude Code / cc-haha 在 feature gate 下有 CronCreate/Delete/List、RemoteTriggerTool 等能力。Cron 工具用于创建、列出和删除定时触发任务，RemoteTriggerTool 用于远程触发执行。relay-teams 有 automation / gateway 层能力，但默认模型工具集中缺少 schedule / remote trigger 的一等工具抽象。

价值：
- 定时任务、远程触发和 IM/gateway 入口是 agent team 的自然延伸。
- 如果只放在 API/UI 层，模型无法把它们纳入任务计划。

建议：
- 新增 `schedule_task`、`list_schedules`、`delete_schedule`。
- 新增 `trigger_remote_run` 或明确 gateway session 工具。
- 先限制为低风险通知 / 调度类动作，再扩展到远程执行。

### 12. Computer Use 工具覆盖较强，但缺少工具级安全与结果闭环

relay-teams 默认注册了 capture、window、click、drag、type、scroll、hotkey、launch app、wait window 等 computer tools。这些工具用于截图、发现窗口、聚焦窗口、鼠标点击/拖拽、键盘输入、滚动、快捷键、启动应用和等待窗口。从工具数量看，relay-teams 在这块比 opencode 主内置工具更显式，也接近 Claude Code / cc-haha 的 Computer Use 方向。

价值：
- Computer Use 风险高，需要比普通工具更强的审批、回放和失败解释。

建议：
- 每个 computer tool 输出截图 / window state / action summary。
- 高风险动作，如 hotkey、type_text、launch_app，进入更细粒度 approval。
- 支持执行回放、失败截图、平台能力检测。
