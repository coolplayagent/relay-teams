# Project Layout

## Source Package

- Core package: `src/relay_teams/`
- Main modules:
  - `agent_runtimes/`: agent-runtime configuration, ACP/A2A/CLI protocol clients, runtime instance records, runtime message bus, probes, host-tool bridge, native config, and skill bridge
  - `agents/`: agent models, execution flow, orchestration, and task domain
    - `agents/execution/`: prompt assembly, message persistence, subagent running, LLM session flow
    - `agents/orchestration/`: coordinator, meta-agent, verification, human gate, task execution/orchestration
    - `agents/tasks/`: task models, ids, events, repository, and status helpers
  - `boards/`: TODO/task-board contracts, board state mapping, tracker adapters, board-controlled tools, and future board services
  - `builtin/`: built-in roles, default config, logging config, and bundled skills/resources
  - `connector/`: connector aggregation facade and API contracts for built-in platform connection status
  - `env/`: runtime env loading, proxy support, web connectivity, and env CLI
  - `hooks/`: runtime hook models, loader, matcher, service, executors, state, and event integration
  - `gateway/`: IM and protocol gateway runtime
    - `gateway/discord/`: Discord bot account models, repository, Gateway worker, inbound queue, client, service, and secret store
    - `gateway/feishu/`: Feishu gateway accounts, long-connection runtime, and message pool
    - `gateway/im/`: shared IM context resolution, commands, and `im_send` delivery service
    - `gateway/wechat/`: WeChat account login, workers, queue, client, and service
    - `gateway/xiaoluban/`: Xiaoluban notification delivery and IM forwarding
    - `gateway_session_service.py` and `session_ingress_service.py`: shared external-session mapping and busy-session run handoff
  - `interfaces/`: external interfaces
    - `interfaces/cli/`: Typer commands for server, prompts, approvals, triggers, memory, hooks, and skills
    - `interfaces/server/`: FastAPI app, DI container, config services, and `/api/*` routers
    - `interfaces/sdk/`: HTTP client SDK
  - `logger/`: runtime logging
  - `mcp/`: MCP config, registry, service, and CLI
  - `notifications/`: notification models, settings, config, and delivery service
  - `paths/`: repository and runtime path helpers
  - `persistence/`: DB access and shared persistence models/repos
  - `plugins/`: plugin manifests, registry, component source resolution, and plugin-provided MCP source loading
  - `providers/`: provider contracts, model config, HTTP client factory, OpenAI-compatible adapters, token usage
  - `memory/`: Memory Bank models, repository, service, event handler, consolidation, and retrieval integration
  - `roles/`: role models, registry, settings, and CLI
  - `sessions/`: session models, repository, service, round projection
    - `sessions/runs/`: active run registry, control, runtime config, event log/stream, injection queue, state repos
  - `skills/`: skill discovery, registry, config reload, and CLI
  - `tools/`: tool registry, runtime policy/state, and built-in tools
  - `trace/`: trace/span context
  - `triggers/`: trigger models, repository, service, and CLI
  - `workspace/`: workspace ids, handles, memory, artifacts, and manager

## Frontend

- Frontend assets: `frontend/dist/`

## Tests

- `tests/unit_tests/`: mirrors `src/relay_teams/` by module
- `tests/unit_tests/boards/`: board models, adapters, controlled tools, and dispatcher behavior
- `tests/unit_tests/gateway/`: gateway session mapping, provider services, IM forwarding, queues, and frontend-facing gateway behavior
- `tests/unit_tests/hooks/`: hook loader, executors, and runtime behavior
- `tests/unit_tests/plugins/`: plugin manifest loading, namespacing, component source wiring, and runtime integration
- `tests/integration_tests/api/`: HTTP/SSE integration flows
- `tests/integration_tests/browser/`: browser scenarios
- `tests/integration_tests/cli/`: CLI integration coverage
- `tests/integration_tests/support/`: shared integration helpers
