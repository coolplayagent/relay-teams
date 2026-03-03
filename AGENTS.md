# Repository Guidelines (Simplified)

数据库schema与api的修改不需要考虑兼容旧版本，修改后需要同步修改docs目录下对应的文档。

## Project Layout
- Core code: `src/agent_teams/`
- Main modules:
  - `application/`: application service/facade
  - `core/`: models, enums, IDs, config
  - `agents/`, `coordination/`, `workflow/`: orchestration
  - `providers/`: LLM providers
  - `state/`, `runtime/`: persistence and runtime event/injection
  - `interfaces/server`: FastAPI HTTP/SSE API
  - `interfaces/cli`: CLI (HTTP client)
  - `interfaces/sdk`: Python HTTP client
- Frontend: `frontend/` (served from `frontend/dist`)
- Tests: `tests/test_*.py`

## Dev Commands
- Install deps: `uv sync`
- Start server: `uv run agent-teams serve`
- CLI prompt: `uv run agent-teams prompt -m "hello"`
- Validate roles: `uv run agent-teams roles-validate`
- Run tests: `uv run pytest -q`

## Coding Rules
- Python 3.12+, 4 spaces, type annotations required.
- Prefer Pydantic models/enums over loose dicts.
- `from __future__ import annotations` in Python modules.
- Imports order: stdlib / third-party / local.
- Avoid `Any` in core flow unless absolutely necessary.
- Logging via runtime logger; avoid `print()` in production paths.

## API & Data Contracts
- Backend public contract is `/api/*`.
- CLI/frontend/SDK must communicate via HTTP/SSE only.
- Do not directly access backend internal repositories from interface layer.

## Testing
- Add/update tests for behavior changes, especially orchestration and streaming.
- Use focused unit tests first; add integration tests for run/SSE flows when needed.

## Security
- Secrets only in `.agent_teams/.env`.
- Never commit keys/tokens.
