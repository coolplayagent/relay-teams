# agent-teams

Role-driven multi-agent orchestration framework built with strong typing and tool-only collaboration flow.
Runtime model execution uses `pydantic_ai` with OpenAI-compatible endpoints.

## Web Interface

![Agent Teams Web Interface](docs/agent_teams.png)

Start the server with `uv run agent-teams serve` and open http://127.0.0.1:8000 in your browser.

## Quick start

### 1) Install dependencies

```bash
uv sync
```

### 2) Create runtime config files

Linux/macOS:

```bash
cp .agent_teams/llm.json.example .agent_teams/llm.json
```

Windows PowerShell:

```powershell
Copy-Item .agent_teams/llm.json.example .agent_teams/llm.json
```

Then edit `.agent_teams/llm.json`. You must configure the `default` profile, and optionally add more profiles for different roles.

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

In each role's markdown file (e.g., `.agent_teams/roles/coordinator_agent.md`), add `llm_profile` to use a specific model:

```yaml
---
role_id: coordinator_agent
name: Coordinator Agent
llm_profile: fast
...
---
```

Roles without `llm_profile` will use the `default` profile.

### 3) Validate roles

```bash
uv run agent-teams roles-validate
```

### 4) Start web server

```bash
uv run agent-teams serve
```

Then open http://127.0.0.1:8000 in your browser to access the web interface.

### 5) Run an intent

```bash
uv run agent-teams run-intent --intent "Draft a release note"
```

### 5.1) Stream run events

```bash
uv run agent-teams run-intent-stream --intent "Draft a release note"
```

### 5.2) Inject messages during a running stream (SDK)

```python
from pathlib import Path
from agent_teams.interfaces.sdk.client import AgentTeamsApp
from agent_teams.core.enums import InjectionSource
from agent_teams.core.models import IntentInput

app = AgentTeamsApp(config_dir=Path(".agent_teams"))
stream = app.run_intent_stream(IntentInput(session_id="s1", intent="do multi-step work"))
first_event = next(stream)
app.inject_message(first_event.run_id, InjectionSource.USER, "Additional constraint from user")
for event in stream:
    print(event.event_type, event.payload_json)
```

### 6) Query task records

```bash
uv run agent-teams tasks-list
# then:
uv run agent-teams tasks-query --task-id <task_id>
```
