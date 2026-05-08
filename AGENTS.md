# Repository Guidelines

## Project Layout
- Agent Teams is a Python backend under `src/relay_teams/` with static frontend assets under `frontend/dist/`, mirrored by focused unit/integration test directories.
- See `docs/core/project-layout.md` for the maintained source, frontend, and test directory map.

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
- Do not fix Qodana CI failures by adding source-file, package, or inspection-specific excludes to `qodana.yaml`. Fix the source issue or the CI workflow instead. Only non-source generated/cache/output directories may be excluded under `name: All`, such as virtualenvs, caches, docs, and built frontend artifacts.
- For frontend UI work under `frontend/dist/`, split substantial pages, components, state logic, and styles into focused modules instead of continually appending to one large file. When a file is growing because it mixes view markup, interactions, data helpers, and CSS for multiple areas, extract cohesive pieces into separate JS/CSS files and link/import them explicitly.
- For packaged resource files such as tool description `.txt` files, avoid hand-maintained subpackage `package-data` whitelists; use parent-package globs and add a source-tree coverage test to catch drift.
- Keep transport semantics consistent for the same provider/model path. If the primary execution flow uses streaming, auxiliary LLM flows such as reflection, compaction, memory rewrite, or hooks must also use streaming APIs against that endpoint rather than mixing in non-streaming shortcuts.
- For outbound network changes, evaluate proxy requirements first and reuse the existing proxy module when needed.
- CLI modules should provide their own subcommands. List/query output must support default table output and `--format json`.
- Database schema and API changes do not need backward compatibility, but matching updates to `docs/core/api-design.md` and `docs/core/database-schema.md` must be included in the same task.
- Persisted capability references may contain dirty data. Runtime paths that consume existing role state from the database or already-saved config must tolerate missing `tools`, `mcp_servers`, and `skills` by filtering unknown entries and logging a warning with enough context to diagnose the source.
- Keep strict validation for explicit user mutations and validation endpoints. Creating or editing a role should still reject unknown `tools`, `mcp_servers`, and `skills` instead of silently accepting them.
- Do not let startup, config reload, prompt building, provider construction, or task execution fail only because persisted capability references point at missing registry entries.

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
- Runtime, service, and repository code must not add paired synchronous and asynchronous public methods for the same operation, such as `get_entry()` plus `get_entry_async()`, when they only delegate to matching lower-layer methods. Use one real runtime interface, normally async, and migrate callers through the stack instead of adding shallow wrappers.
- Async request, run, hook, memory, retrieval, network, and LLM paths must not call synchronous SQLite, HTTP, retrieval, or provider helpers internally. Add or use the real async downstream API and await it through the stack.

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
