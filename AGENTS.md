# Repository Guidelines

## Project Layout
- Core package: `src/relay_teams/`
- Main modules:
  - `agents/`: agent models, execution flow, orchestration, and task domain
    - `agents/execution/`: prompt assembly, message persistence, subagent running, LLM session flow
    - `agents/orchestration/`: coordinator, meta-agent, verification, human gate, task execution/orchestration
    - `agents/tasks/`: task models, ids, events, repository, and status helpers
  - `builtin/`: built-in roles, default config, logging config, and bundled skills/resources
  - `env/`: runtime env loading, proxy support, web connectivity, and env CLI
  - `hooks/`: runtime hook models, loader, matcher, service, executors, state, and event integration
  - `interfaces/`: external interfaces
    - `interfaces/cli/`: Typer commands for server, prompts, approvals, triggers, reflection, hooks, and skills
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
  - `tests/unit_tests/`: mirrors `src/relay_teams/` by module
  - `tests/unit_tests/hooks/`: hook loader, executors, and runtime behavior
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
- Do not use implicit imports or lazy imports. Resolve dependency boundaries directly instead of deferring imports.
- Do not use `TYPE_CHECKING`-only imports to hide circular dependencies. Fix the cycle in the module design instead.
- Each module owns its own configuration. Do not centralize unrelated module config elsewhere.
- Expose public package APIs through package-level `__init__.py`.
- Use the project logger in production paths; do not use `print()`.
- Do not use emoji in code, comments, docs, or commit messages.
- For packaged resource files such as tool description `.txt` files, avoid hand-maintained subpackage `package-data` whitelists; use parent-package globs and add a source-tree coverage test to catch drift.
- Keep transport semantics consistent for the same provider/model path. If the primary execution flow uses streaming, auxiliary LLM flows such as reflection, compaction, memory rewrite, or hooks must also use streaming APIs against that endpoint rather than mixing in non-streaming shortcuts.
- For outbound network changes, evaluate proxy requirements first and reuse the existing proxy module when needed.
- CLI modules should provide their own subcommands. List/query output must support default table output and `--format json`.
- Database schema and API changes do not need backward compatibility, but matching `docs/` updates must be included in the same task.
- Persisted capability references may contain dirty data. Runtime paths that consume existing role state from the database or already-saved config must tolerate missing `tools`, `mcp_servers`, and `skills` by filtering unknown entries and logging a warning with enough context to diagnose the source.
- Keep strict validation for explicit user mutations and validation endpoints. Creating or editing a role should still reject unknown `tools`, `mcp_servers`, and `skills` instead of silently accepting them.
- Do not let startup, config reload, prompt building, provider construction, or task execution fail only because persisted capability references point at missing registry entries.
- Apply the same validation split to hooks and similar runtime config: explicit user mutations must fail on unknown or invalid references, but persisted drift at runtime should degrade safely with warnings instead of crashing startup or execution.

## Development
- Initial setup:
  - Windows: `setup.bat`
  - Linux/macOS: `sh setup.sh`
  - If you skip the setup script, run: `uv sync --extra dev && uv pip install -e .`
  - Prefer `uv run --extra dev ...` for local tooling so commands use the project environment instead of the system Python.

## Coding Standards
- Prefer enums and Pydantic models over loose dictionaries.
- Do not use `typing.Any`, `hasattr`, or `# type: ignore`.
- Changed behavior must come with tests.
- `tests/unit_tests/` should mirror `src/relay_teams/`. Add matching `__init__.py` files for new test directories.
- Prefer focused unit tests first. Add integration coverage when run/SSE/interface flows change.
- New tools should follow the shared runtime middleware path via `execute_tool_call(..., raw_args=locals())`; do not reimplement hook-aware input parsing or approval plumbing inside each tool.
- Hook-driven tool input rewrites and hook-provided runtime environment must be handled in the shared tool runtime plus shared HTTP and command execution layers, not with per-tool special cases.
- For built-in PPT skills, when a user reports遮挡、重叠、溢出等版式问题, fix the artifact, upstream the reusable rule into the built-in ppt skill docs/tests, and verify end-to-end conversion before opening a PR.

## Interface Boundaries
- Public backend contract is `/api/*`.
- CLI, frontend, and SDK communicate with backend via HTTP/SSE only.
- Interface layers must not access backend repositories directly.

## Pre-Commit Self-Check
1. `uv run --extra dev ruff check --fix`
2. `uv run --extra dev ruff format --no-cache --force-exclude`
3. `uv run --extra dev basedpyright`
4. `uv run --extra dev pytest -q tests/unit_tests`
5. `uv run --extra dev pytest -q tests/integration_tests`

## Security
- Secrets only in keyring.
- Never commit keys or tokens.
