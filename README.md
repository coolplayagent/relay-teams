# agent-teams

Role-driven multi-agent orchestration framework built with strong typing and tool-only collaboration flow.
Runtime model execution uses `pydantic_ai` with OpenAI-compatible endpoints.

## Web Interface

![Agent Teams Web Interface](docs/agent_teams.png)

Start the server with `uv run agent-teams server serve` and open http://127.0.0.1:8000 in your browser.

Frontend assets are now decoupled under `frontend/dist` and served by the backend.

## Quick start

### 1) Install dependencies

```bash
uv sync
```

### 2) Create runtime config files

Linux/macOS:

```bash
cp .agent_teams/model.json.example .agent_teams/model.json
```

Windows PowerShell:

```powershell
Copy-Item .agent_teams/model.json.example .agent_teams/model.json
```

Then edit `.agent_teams/model.json`. You must configure the `default` profile, and optionally add more profiles for different roles.

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

#### Per-role model configuration

In each role's markdown file (e.g., `.agent_teams/roles/coordinator_agent.md`), add `model_profile` to use a specific model:

```yaml
---
role_id: coordinator_agent
name: Coordinator Agent
model_profile: fast
...
---
```

Roles without `llm_profile` will use the `default` profile.

### 3) Validate roles

```bash
uv run agent-teams roles validate
```

### 4) Start web server

```bash
uv run agent-teams server serve
```

Then open http://127.0.0.1:8000 in your browser to access the web interface.

You can override runtime config directory (for isolated environments such as integration tests):

```bash
uv run agent-teams server serve --config-dir ./.agent_teams
```

### 5) Run a prompt (CLI via HTTP/SSE)

```bash
uv run agent-teams -m "Draft a release note"
```

### 5.1) List merged environment variables

```bash
uv run agent-teams env list
```

### 5.2) Create a run and stream events (HTTP SDK)

```python
from agent_teams.interfaces.sdk.client import AgentTeamsClient

client = AgentTeamsClient(base_url="http://127.0.0.1:8000")
run = client.create_run(intent="do multi-step work", session_id="s1")
for event in client.stream_run_events(run.run_id):
    print(event.get("event_type"))
```

### 6) Query task records

```bash
uv run agent-teams tasks-list
# then:
uv run agent-teams tasks-query --task-id <task_id>
```

## Testing Layout

Unit and integration tests are split under `tests/`:

- `tests/unit_tests/`
- `tests/integration_tests/`

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
