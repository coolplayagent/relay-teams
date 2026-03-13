# agent-teams

Role-driven multi-agent orchestration framework built with strong typing and tool-only collaboration flow.
Runtime model execution uses `pydantic_ai` with OpenAI-compatible endpoints.

## Project Layout

Core code lives under `src/agent_teams/`:

- `agents/`: agent domain package
  - `agents/execution/`: agent turn execution, prompt assembly, and LLM session flow
  - `agents/orchestration/`: coordinator flow, task orchestration, verification, and human gate logic
  - `agents/tasks/`: task domain models, ids, events, and task status utilities
- `env/`: runtime environment loading and env-related CLI support
- `interfaces/`: external interfaces
  - `interfaces/server/`: FastAPI HTTP/SSE API and routers
  - `interfaces/cli/`: Typer CLI entrypoints, HTTP/SSE client behavior, and prompt inspection commands
  - `interfaces/sdk/`: Python HTTP client SDK
- `logger/`, `trace/`: structured logging and trace context
- `mcp/`: MCP capability integration
- `notifications/`: backend-driven notification rules and event dispatch
- `paths/`: path and filesystem location helpers
- `providers/`: provider contracts, model configuration, registries, and OpenAI-compatible adapters
- `reflection/`: reflection result modeling and reflection services
- `roles/`: role definitions and role validation
- `runs/`: run-time orchestration, run control, event streaming, and injection flows
- `sessions/`: session lifecycle and round projection services
- `shared_types/`: cross-domain shared type aliases and lightweight contracts
- `skills/`: skill loading/registry support
- `state/`: persistence and state repositories
- `tools/`: built-in tool registration and implementations (`registry/`, `runtime/`, `stage_tools/`, `task_tools/`, `workspace_tools/`)
- `triggers/`: trigger management and event ingestion flows
- `workspace/`: workspace indexing, materialization, and workspace-facing services

Frontend assets are built into `frontend/dist` (`css/` and `js/`) and served by the backend.

## Skills

Skills are composable capability modules. Agents load skills at runtime based on the current task context, so the same role can attach different capability sets for different runs.

## Web Interface

![Agent Teams Web Interface](docs/agent_teams.png)

Start the server with `uv run agent-teams server start` and open http://127.0.0.1:8000 in your browser.
Use `uv run agent-teams server restart` to restart the managed server, and `uv run agent-teams server stop --force` to force stop it.

Frontend assets are now decoupled under `frontend/dist` and served by the backend.

## Quick start

### 1) Install dependencies

```bash
uv sync
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

### 5.3) Create a run and stream events (HTTP SDK)

```python
from agent_teams.interfaces.sdk.client import AgentTeamsClient

client = AgentTeamsClient(base_url="http://127.0.0.1:8000")
run = client.create_run(intent="do multi-step work", session_id="s1")
for event in client.stream_run_events(run.run_id):
    print(event.get("event_type"))
```

### 5.4) Preview assembled prompts for a role

```bash
uv run agent-teams prompts get --role-id coordinator_agent
```

```bash
uv run agent-teams prompts get --role-id coordinator_agent --format json --section provider
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
- `tests/integration_tests/`: integration scenarios split by `api/`, `browser/`, and shared `support/`

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

