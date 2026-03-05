# Repository Guidelines

Database schema and API changes do not need to maintain backward compatibility. After making such changes, update the corresponding documentation in the `docs/` directory in the same task.

## Project Layout
- Core code: `src/agent_teams/`
- Main modules:
  - `application/`: application services and facades
  - `core/`: models, enums, IDs, and configuration
  - `agents/`, `coordination/`, `workflow/`: orchestration logic
  - `providers/`: LLM provider integrations
  - `state/`, `runtime/`: persistence, runtime events, and dependency injection
  - `interfaces/server`: FastAPI HTTP/SSE API
  - `interfaces/cli`: CLI (HTTP client)
  - `interfaces/sdk`: Python HTTP client
- Frontend: `frontend/` (served from `frontend/dist`)
- Tests:
  - `tests/unit_tests/`: unit tests (must mirror `src/agent_teams/` structure)
  - `tests/integration_tests/`: integration tests (API/SSE and end-to-end backend flows)

## Core Principles
- **提交规范**: 禁止绕过pre-commit的检查
- **文件编码规范**: 在python文件头统一添加utf-8编码声明
- **编程规范**: 禁止使用os.path，应该使用pathlib.path
- **Strong typing**: Never use untyped `{}` structures, `typing.Any`, or `dataclass` for domain contracts. Use explicit strong types and Pydantic v2 models for schema safety.
- **Clean code**: Follow SOLID principles, keep modules high-cohesion/low-coupling, and depend on abstractions rather than concrete implementations.
- **Public interfaces**: Expose package-level public APIs through `__init__.py`.
- **Test-driven changes**: Every feature and bug fix must be guarded by unit tests. Unit test directories and files must correspond one-to-one with business code paths (for example, `src/agent_teams/tools/` -> `tests/unit_tests/tools/`).
- **No emoji policy**: Do not use emoji in code, comments, docs, or commit messages.
- **Import policy**: Do not place imports inside functions; keep imports at module top level to expose circular dependencies early.
- **CLI 模块规范**: 每个模块必须提供本模块的 CLI 子命令，且列表/查询类输出必须同时支持表格（默认）与 `--format json`。

## Development Setup
Run setup before starting implementation work.

1. Run setup script:
   - Windows: `setup.bat`
   - Linux/macOS: `sh setup.sh`
2. Activate virtual environment:
   - Windows: `.venv\\Scripts\\activate`
   - Linux/macOS: `source .venv/bin/activate`
3. Ensure development dependencies are installed:
   - `uv sync --extra dev`

## Development Commands
- Install dependencies: `uv sync --extra dev`
- Start server: `uv run agent-teams serve`
- CLI prompt: `uv run agent-teams prompt -m "hello"`
- Validate roles: `uv run agent-teams roles-validate`
- Run all tests: `uv run pytest -q`
- Run unit tests: `uv run pytest -q tests/unit_tests`
- Run integration tests: `uv run pytest -q tests/integration_tests`

## Coding Standards
- Python 3.12+, 4 spaces, and explicit type annotations are required.
- Use `from __future__ import annotations` in Python modules.
- Import order: standard library / third-party / local.
- Prefer Pydantic models and enums over loose dictionaries.
- Do not use `typing.Any` in project code (parameters, return types, fields, or local variables).
- Do not use `hasattr` for schema decisions; fix the type design instead.
- Follow PEP 8.
- Do not use `# type: ignore` unless absolutely required for third-party compatibility, and always include a clear inline reason.
- Use runtime logger facilities; avoid `print()` in production code paths.

### Recommended Practices
1. Defensive programming: perform `None` checks before consuming dictionary values from untrusted inputs.
2. Explicit return contracts: annotate expected return types for all functions.
3. Scenario-based tests: for changed files, add or update unit tests that cover real usage paths.

## API and Data Contracts
- Public backend contract is `/api/*`.
- CLI/frontend/SDK must communicate via HTTP/SSE only.
- Interface layers must not access backend internal repositories directly.

## Testing Rules
- `tests/unit_tests/` must mirror `src/agent_teams/` structure.
- `tests/integration_tests/` stores integration test scenarios and API/SSE flow coverage.
- When adding new test folders, also add corresponding `__init__.py` files.
- Add or update tests for behavior changes, especially orchestration and streaming.
- Prefer focused unit tests first; add integration tests for run/SSE flows when needed.

## Commit Self-Check (Required Before Every Commit)
1. Run Ruff autofix and clean all possible lint issues:
   - `uv run ruff check --fix`
2. Run basedpyright and resolve all type issues:
   - `uv run basedpyright`
3. Run unit tests and ensure all pass:
   - `uv run pytest -q tests/unit_tests`

## Security
- Store secrets only in `.agent_teams/.env`.
- Never commit keys or tokens.
