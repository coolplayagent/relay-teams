# Repository Guidelines

## Project Layout
- Core package: `src/agent_teams/`
- Main modules:
  - `agents/`: agent models, execution flow, orchestration, and task domain
    - `agents/execution/`: prompt assembly, message persistence, subagent running, LLM session flow
    - `agents/orchestration/`: coordinator, meta-agent, verification, human gate, task execution/orchestration
    - `agents/tasks/`: task models, ids, events, repository, and status helpers
  - `builtin/`: built-in roles, default config, logging config, and bundled skills/resources
  - `env/`: runtime env loading, proxy support, web connectivity, and env CLI
  - `interfaces/`: external interfaces
    - `interfaces/cli/`: Typer commands for server, prompts, approvals, triggers, reflection, and skills
    - `interfaces/server/`: FastAPI app, DI container, config services, and `/api/*` routers
    - `interfaces/sdk/`: HTTP client SDK
  - `logger/`: runtime logging
  - `mcp/`: MCP config, registry, service, and CLI
  - `notifications/`: notification models, settings, config, and delivery service
  - `paths/`: repository and runtime path helpers
  - `persistence/`: DB access and shared persistence models/repos
  - `providers/`: provider contracts, model config, HTTP client factory, OpenAI-compatible adapters, token usage
  - `reflection/`: reflection config, models, repository, service, and CLI
  - `roles/`: role models, registry, settings, and CLI
  - `sessions/`: session models, repository, service, round projection
    - `sessions/runs/`: active run registry, control, runtime config, event log/stream, injection queue, state repos
  - `skills/`: skill discovery, registry, config reload, and CLI
  - `tools/`: tool registry, runtime policy/state, and built-in tools
  - `trace/`: trace/span context
  - `triggers/`: trigger models, repository, service, and CLI
  - `workspace/`: workspace ids, handles, memory, artifacts, and manager
- Frontend assets: `frontend/dist/`
- Tests:
  - `tests/unit_tests/`: mirrors `src/agent_teams/` by module
  - `tests/integration_tests/api/`: HTTP/SSE integration flows
  - `tests/integration_tests/browser/`: browser scenarios
  - `tests/integration_tests/cli/`: CLI integration coverage
  - `tests/integration_tests/support/`: shared integration helpers

## Working Rules
- Do not bypass pre-commit checks.
- Use UTF-8 for all files. Python modules should include `from __future__ import annotations`.
- Use `pathlib.Path`; do not use `os.path`.
- For domain contracts, do not use loose `{}` structures, `typing.Any`, or `dataclass`. Prefer explicit types and Pydantic v2 models.
- Keep imports at module top level.
- Each module owns its own configuration. Do not centralize unrelated module config elsewhere.
- Expose public package APIs through package-level `__init__.py`.
- Use the project logger in production paths; do not use `print()`.
- Do not use emoji in code, comments, docs, or commit messages.
- For outbound network changes, evaluate proxy requirements first and reuse the existing proxy module when needed.
- CLI modules should provide their own subcommands. List/query output must support default table output and `--format json`.
- Database schema and API changes do not need backward compatibility, but matching `docs/` updates must be included in the same task.

## Development
- Initial setup:
  - Windows: `setup.bat`
  - Linux/macOS: `sh setup.sh`
  - Then: `uv sync --extra dev`

## Coding Standards
- Prefer enums and Pydantic models over loose dictionaries.
- Do not use `typing.Any`, `hasattr`, or `# type: ignore`.
- If typing is hard, fix it with explicit types, `Protocol`, `cast`, or local `.pyi` stubs instead of suppressing the checker.
- Changed behavior must come with tests.
- `tests/unit_tests/` should mirror `src/agent_teams/`. Add matching `__init__.py` files for new test directories.
- Prefer focused unit tests first. Add integration coverage when run/SSE/interface flows change.

## Interface Boundaries
- Public backend contract is `/api/*`.
- CLI, frontend, and SDK communicate with backend via HTTP/SSE only.
- Interface layers must not access backend repositories directly.

## Pre-Commit Self-Check
1. `uv run ruff check --fix`
2. `uv run ruff format --no-cache --force-exclude`
3. `uv run basedpyright`
4. `uv run pytest -q tests/unit_tests`
5. `uv run pytest -q tests/integration_tests`

## Security
- Secrets only in keyring.
- Never commit keys or tokens.
