# cool-play-agent-teams

Role-driven multi-agent orchestration framework built with strong typing and tool-only collaboration flow.
Runtime model execution uses `pydantic_ai` with OpenAI-compatible endpoints.

## Evaluation Snapshot

Recent SWE-bench snapshots are archived under [`docs/evaluations/swebench/`](docs/evaluations/swebench/README.md).
Current snapshots cover only the first `100` items from `SWE-bench Verified`, not the full benchmark.

Using glm-5,Temperature: 0.7,Top P:0.95.

| Mode | Benchmark | Pass Rate | Passed | Failed | Mean Duration | Input Tokens | Cached Input | Output Tokens | Requests | Tool Calls | Report |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Normal | SWE-bench Verified 100 | 72.0% | 72 | 28 | 369.2s | 60,265,198 | 58,214,976 | 451,537 | 2,432 | 2,484 | [HTML](docs/evaluations/swebench/normal-swebench-verified-100-report.html) |
| Orchestration | SWE-bench Verified 100 | 73.0% | 73 | 27 | 704.2s | 103,016,077 | 95,659,776 | 1,886,195 | 6,026 | 7,171 | [HTML](docs/evaluations/swebench/orchestration-swebench-verified-100-report.html) |

Highlights:

- `Orchestration` currently reaches `73/100` on `SWE-bench Verified 100`, with `96` runs finishing in `completed` state and `4` ending in `failed`.
- `Normal` mode currently reaches `72/100` on `SWE-bench Verified 100`, with `97` runs finishing in `completed` state and `3` ending in `failed`.
- Token usage is reported directly in the table so model IO and tool activity can be compared without deriving cost assumptions.

## Web Interface

![Agent Teams Web Interface](docs/agent_teams.png)

Start the server with `uv run agent-teams server start` and open http://127.0.0.1:8000 in your browser.
Use `uv run agent-teams server restart` to restart the managed server, and `uv run agent-teams server stop --force` to force stop it.
The web UI now includes a language toggle beside the settings button so you can switch between English and Simplified Chinese in-page.

Frontend assets are now decoupled under `frontend/dist` and served by the backend.

## Quick start

### 1) Install dependencies

Use the setup script for your platform, install from PyPI, or install directly with `uv`.

Windows:

```powershell
.\setup.bat
```

Linux/macOS:

```bash
sh setup.sh
```

Install from PyPI:

```bash
pip install cool-play-agent-teams
```

Direct install:

```bash
uv sync --extra dev
uv pip install -e .
```

### 2) help

```bash
agent-teams --help

# for evals
agent-teams-evals --help
```

If the `agent-teams` command is still missing in a fresh local checkout, the project package was not installed into the active virtual environment. Run `uv pip install -e .` or use `uv run python -m agent_teams --help` as a fallback.

