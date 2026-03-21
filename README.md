# agent-teams

Role-driven multi-agent orchestration framework built with strong typing and tool-only collaboration flow.
Runtime model execution uses `pydantic_ai` with OpenAI-compatible endpoints.

## Project Layout

Core code lives under `src/agent_teams/`:

- `agents/`: agent models and orchestration domain
  - `agents/execution/`: prompt assembly, message persistence, subagent running, and LLM session flow
  - `agents/instances/`: subagent instance models, ids, enums, and repositories
  - `agents/orchestration/`: coordinator flow, task execution, verification, role communication, and human gate logic
  - `agents/tasks/`: task models, ids, events, repositories, and status helpers
- `builtin/`: built-in roles, default config, logging config, and bundled skills/resources
- `env/`: runtime env loading, proxy support, connectivity checks, and env CLI support
- `gateway/`: external channel session mapping, ACP stdio bridge, and future IM gateway transports
- `interfaces/`: external interfaces
  - `interfaces/cli/`: Typer CLI entrypoints for prompts, approvals, triggers, environment, skills, and server control
  - `interfaces/sdk/`: Python HTTP client SDK
  - `interfaces/server/`: FastAPI app, DI container, config services, static asset serving, and `/api/*` routers
- `logger/`: runtime logging configuration and structured logger helpers
- `mcp/`: MCP config loading, registry, service, reload flow, and CLI support
- `net/`: HTTP client helpers, LLM client wrappers, constants, and proxy-aware transports
- `notifications/`: notification models, config management, settings service, and delivery service
- `paths/`: app/project path helpers
- `persistence/`: database access and shared persistence models/repos
- `providers/`: model config, provider contracts, connectivity probes, OpenAI-compatible adapters, factories, and token usage
- `roles/`: role models, registry, settings, memory injection, and CLI support
- `sessions/`: session models, repository, service, and round projection
  - `sessions/runs/`: active-run registry, run control, event log/stream, injection queue, runtime config, and run state repositories
- `skills/`: discovery, registry, config reload, and CLI support
- `tools/`: built-in tool registration and runtime policy/state
  - `tools/registry/`: default tool registration and registry composition
  - `tools/runtime/`: execution context, approval state, persisted runtime state, and policy enforcement
  - `tools/task_tools/`: task creation, listing, dispatch, and update tools
  - `tools/workspace_tools/`: workspace read/write, shell, grep, glob, and ripgrep tools
- `trace/`: request/span trace context
- `triggers/`: trigger models, repository, service, and CLI support
- `workspace/`: workspace ids, handles, git worktree support, repositories, manager, and services

Frontend assets are built into `frontend/dist` (`css/` and `js/`) and served by the backend.

## Skills

Skills are composable capability modules. Agents load skills at runtime based on the current task context, so the same role can attach different capability sets for different runs.

## Coding Standards

- Do not use `typing.Any` or `hasattr` in production code paths.
- Changed behavior must come with tests.

## Web Interface

![Agent Teams Web Interface](docs/agent_teams.png)

Start the server with `uv run agent-teams server start` and open http://127.0.0.1:8000 in your browser.
Use `uv run agent-teams server restart` to restart the managed server, and `uv run agent-teams server stop --force` to force stop it.

Frontend assets are now decoupled under `frontend/dist` and served by the backend.

## Quick start

### 1) Install dependencies

Use the setup script for your platform, or install directly with `uv`.

Windows:

```powershell
.\setup.bat
```

Linux/macOS:

```bash
sh setup.sh
```

Direct install:

```bash
uv sync --extra dev
```

### 2) Create runtime config files

Linux/macOS:

```bash
mkdir -p ~/.config/agent-teams
cp src/agent_teams/builtin/config/model.json ~/.config/agent-teams/model.json
```

Windows PowerShell:

```powershell
New-Item -ItemType Directory -Force "$HOME/.config/agent-teams" | Out-Null
Copy-Item src/agent_teams/builtin/config/model.json "$HOME/.config/agent-teams/model.json"
```

Then edit `~/.config/agent-teams/model.json`. You must configure the `default` profile, and optionally add more profiles for different roles.

```json
{
  "default": {
    "model": "gpt-4o-mini",
    "base_url": "https://api.openai.com/v1",
    "api_key": "${OPENAI_API_KEY}",
    "temperature": 0.2
  },
  "fast": {
    "model": "gpt-4o-mini",
    "base_url": "https://api.openai.com/v1",
    "api_key": "${OPENAI_API_KEY}",
    "temperature": 0.1
  }
}
```

If you use placeholders such as `${OPENAI_API_KEY}`, define them in the ignored `~/.config/agent-teams/.env` file or in the process environment before starting the server.

```dotenv
OPENAI_API_KEY=<your-openai-api-key>
ANTHROPIC_API_KEY=<your-anthropic-api-key>
# Optional: proxy settings used by LLM requests and all MCP transports
HTTP_PROXY=http://proxy.example:8080
HTTPS_PROXY=http://proxy.example:8080
NO_PROXY=localhost,127.0.0.1
```

Proxy behavior:
- LLM OpenAI-compatible requests read proxy settings from app `~/.config/agent-teams/.env` and the process environment.
- All MCP transports read proxy settings from the merged env.
- Every MCP server config inherits merged proxy env values in its `env` settings.
- stdio MCP servers consume those values when Agent Teams launches subprocess transports such as `uvx` and `npx`.
- Remote MCP transports (`sse`, `http`, `streamable-http`) also use the merged proxy env through the backend process environment.
- If an MCP server defines explicit `env` entries in `mcp.json`, those values override the inherited proxy defaults.
- `verify_ssl` is managed from `Settings -> Proxy`, not from `.env`.

#### Per-role model configuration

In each role's markdown file (stored under `~/.config/agent-teams/roles/` when overridden), add `model_profile` to use a specific model:

```yaml
---
role_id: coordinator_agent
name: Coordinator Agent
model_profile: fast
...
---
```

Roles without `model_profile` will use the `default` profile.

### 3) Validate roles

```bash
uv run agent-teams roles validate
```

### 4) Start web server

```bash
uv run agent-teams server start
```

Then open http://127.0.0.1:8000 in your browser to access the web interface.

The server CLI now manages a local PID record in `~/.config/agent-teams/server-process.json` for `restart` and `stop --force`:

```bash
uv run agent-teams server restart
uv run agent-teams server stop --force
```

### 5) Run a prompt (CLI via HTTP/SSE)

```bash
uv run agent-teams -m "Draft a release note"
```

### 5.1) List merged environment variables

```bash
uv run agent-teams env list
```

### 5.1.1) Manage Windows environment variables in Settings

After the server starts, open `Settings -> Environment` in the web UI.
The page shows read-only `System` variables and editable `App` variables.
App variables are stored in `~/.config/agent-teams/.env`.
This settings page edits Agent Teams app runtime variables, while `uv run agent-teams env list` shows the merged runtime environment seen by Agent Teams.

### 5.2) Inspect merged MCP servers

`mcp` config is app-scoped:
- `~/.config/agent-teams/mcp.json`
- All MCP transports read proxy env values from merged runtime env.
- Every MCP server config inherits merged proxy env values in its `env` settings.
- stdio MCP servers consume those values when Agent Teams starts subprocess transports.
- Remote MCP transports (`sse`, `http`, `streamable-http`) also use those merged proxy env values through the backend process environment.
- If an MCP server defines its own `env` entries in `mcp.json`, those explicit values override the inherited proxy defaults.

```bash
uv run agent-teams mcp list
uv run agent-teams mcp tools filesystem --format json
```

### 5.2.1) Start the ACP stdio gateway

Use the gateway entrypoint when an ACP host launches Agent Teams over stdio JSON-RPC:

```bash
uv run agent-teams gateway acp stdio
```

Current ACP coverage includes `initialize`, `session/new`, `session/load`, `session/prompt`, `session/cancel`, and MCP connection lifecycle scaffolding (`mcp/connect`, `mcp/disconnect`). Session-scoped ACP metadata is persisted in the local SQLite database so follow-up turns can reuse the same internal Agent Teams session.

For Zed IDE setup, see `docs/zed-acp-usage.md`.

### 5.3) Create a run and stream events (HTTP SDK)

```python
from agent_teams.interfaces.sdk.client import AgentTeamsClient

client = AgentTeamsClient(base_url="http://127.0.0.1:8000")
session = client.create_session(workspace_id="default", session_id="s1")
run = client.create_run(intent="do multi-step work", session_id=session["session_id"])
for event in client.stream_run_events(run.run_id):
    print(event.get("event_type"))
```

### 5.4) Preview assembled prompts for a role

```bash
uv run agent-teams roles prompt --role-id coordinator_agent
```

```bash
uv run agent-teams roles prompt --role-id coordinator_agent --format json --section provider
```

### 6) List triggers

```bash
uv run agent-teams triggers list
```

### 6.1) Query tool approvals for a run

```bash
uv run agent-teams approvals list --run-id <run_id>
```

### 6.2) Notification config API

```bash
curl http://127.0.0.1:8000/api/system/configs/notifications
```

```bash
curl -X PUT http://127.0.0.1:8000/api/system/configs/notifications \
  -H "Content-Type: application/json" \
  -d '{"config":{"tool_approval_requested":{"enabled":true,"channels":["browser","toast"]},"run_completed":{"enabled":false,"channels":["toast"]},"run_failed":{"enabled":true,"channels":["browser","toast"]},"run_stopped":{"enabled":false,"channels":["toast"]}}}'
```

## Testing Layout

Unit and integration tests are split under `tests/`:

- `tests/unit_tests/` directory structure must mirror `src/agent_teams/` one-to-one.
- `tests/unit_tests/sessions/runs/`: unit coverage for the run execution package nested under `sessions/`
- `tests/integration_tests/api/`: HTTP and SSE integration flows against the backend
- `tests/integration_tests/browser/`: browser automation scenarios
- `tests/integration_tests/cli/`: CLI integration coverage
- `tests/integration_tests/support/`: shared integration helpers

Run unit tests:

```bash
uv run pytest -q tests/unit_tests
```

Run integration API tests (real backend process + real HTTP):

```bash
uv run pytest -q tests/integration_tests/api
```

Run browser automation tests (Playwright):

```bash
uv run playwright install chromium
uv run pytest -q tests/integration_tests/browser
```
