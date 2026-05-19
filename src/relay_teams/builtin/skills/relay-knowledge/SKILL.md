---
name: relay-knowledge
description: Use Relay Knowledge from Agent Teams through the built-in CLI connector. Use this for local knowledge graph status, service diagnostics, code repository registration, full or incremental repository indexing, code search, impact analysis, reports, file indexing, graph inspection, workers, audit queries, setup profiles, and MCP service startup.
---

# Relay Knowledge

Use this skill when the user asks Agent Teams to use Relay Knowledge, build or refresh a code repository index, query indexed code, inspect graph or index health, run local knowledge/file retrieval, analyze code impact, or start/check the Relay Knowledge service.

Always invoke Relay Knowledge through the wrapper script so the runtime uses the connector-managed CLI path first and falls back to a system `relay-knowledge` on `PATH`.

```bash
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- <relay-knowledge arguments>
```

Prefer `--format json` for machine-readable commands. Use non-JSON formats only when the user explicitly wants a markdown report or human CLI output.

## Global Flags

Allowed global flags:

- `--version`
- `--help`
- `--format text|json|markdown|streaming-json`

Use global flags before the Relay Knowledge command and after the wrapper separator:

```bash
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- --version
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- --help
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- --format json status
```

Do not use `--format streaming-json` with `version`; that command only supports `text`, `json`, and `markdown`. Prefer `text` or `json` for `help`.

## Command Allowlist

Only use the Relay Knowledge commands listed in this skill. Do not infer, invent, or try adjacent commands.

Allowed top-level commands:

- `status`
- `health`
- `version`
- `help`
- `ingest`
- `query`
- `graph inspect`
- `index refresh`
- `files index`
- `files query`
- `worker status`
- `worker run-once`
- `audit query`
- `provider probe`
- `proposal list`
- `proposal show`
- `proposal accept`
- `proposal reject`
- `proposal supersede`
- `service status`
- `service doctor`
- `service run`
- `service plan install`
- `service plan uninstall`
- `service definition write`
- `service operator status`
- `service operator pause`
- `service operator resume`
- `setup doctor`
- `setup profile`

Allowed `repo` subcommands:

- `repo register`
- `repo index`
- `repo index-worker`
- `repo scope preview`
- `repo update`
- `repo query`
- `repo impact`
- `repo report`
- `repo status`

Do not run bare `repo`; only run the listed `repo ...` subcommands. Do not use `repo list`; this CLI does not provide it. If an alias is unknown, do not try to enumerate repositories.

For diagnostic or status-only requests, do not register a repository just to discover an alias; ask for the alias or report that the alias is required. For indexing or code-query requests where the user asks to use the current repository and no alias is provided, derive a conservative alias from the current repository directory name and run `repo register "<repo-root>" --alias <alias> --format json` before indexing or querying.

## Common Workflows

Check availability and health:

```bash
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- --version
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- version --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- status --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- health --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- service doctor --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- setup doctor --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- setup profile local --format json
```

Register and index a repository:

```bash
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- repo register "<repo-root>" --alias <alias> --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" --timeout 1140 -- repo index <alias> --ref HEAD --format json
```

Long-running relay-teams shell tool shape:

```text
shell(command='python "<skill_dir>/scripts/relay_knowledge_cli.py" --timeout 1140 -- repo index <alias> --ref HEAD --format json', timeout_ms=1200000)
```

When running `repo index`, configure the outer relay-teams command execution timeout to `1200000` ms. This is the current maximum supported by relay-teams command execution. The wrapper `--timeout 1140` controls the Relay Knowledge child process and leaves cleanup/error-reporting time before the outer shell timeout; it does not override an outer `timeout_ms` such as 120000 ms.

After `repo index` returns, inspect the JSON before claiming the index is usable. If the response contains a `task`, use `task.state` as the authoritative state for this index attempt; treat the index as ready only when `task.state` is `succeeded`. Use `status.state=fresh` only when the response has no `task`. If the response contains an active, queued, running, retrying, or failed task state, report that task state and do not start `repo query`, `repo impact`, or `repo report` unless the user explicitly asks to continue with stale data.

Only run `repo status <alias> --format json` after `repo index` if the index response does not already prove `task.state=succeeded` or, when no task is present, `status.state=fresh`.

Use `--path <filter>` and repeated `--language <id>` on `repo register` and `repo query` when the user scopes the request to specific source paths or languages. Do not pass `--path` or `--language` to `repo index`; that subcommand only accepts the alias plus `--ref` and `--dry-run`.

Query code:

```bash
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- repo query <alias> --query "<text>" --kind hybrid --ref HEAD --limit 10 --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- repo query <alias> --query "<symbol>" --kind symbol --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- repo query <alias> --query "<symbol>" --kind definition --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- repo query <alias> --query "<symbol>" --kind references --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- repo query <alias> --query "<symbol>" --kind callers --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- repo query <alias> --query "<symbol>" --kind callees --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- repo query <alias> --query "<module>" --kind imports --format json
```

Allowed `repo query --kind` values: `hybrid`, `symbol`, `definition`, `references`, `callers`, `callees`, `imports`.

Update and analyze changes:

```bash
python "<skill_dir>/scripts/relay_knowledge_cli.py" --timeout 1140 -- repo update <alias> --base main --head HEAD --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- repo impact <alias> --base main --head HEAD --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- repo report <alias> --format markdown
```

Use outer `timeout_ms=1200000` for `repo update`:

```text
shell(command='python "<skill_dir>/scripts/relay_knowledge_cli.py" --timeout 1140 -- repo update <alias> --base main --head HEAD --format json', timeout_ms=1200000)
```

After `repo update` returns, inspect the response before running `repo impact`, `repo query`, or `repo report`. If the command fails or reports a missing or stale base scope, report that result and index or reindex the base ref before retrying the update; do not continue analysis from a failed update.

Continue an existing code index task:

```bash
python "<skill_dir>/scripts/relay_knowledge_cli.py" --timeout 1140 -- repo index-worker --task-id <task_id> --format json
```

Use outer `timeout_ms=1200000` for `repo index-worker`:

```text
shell(command='python "<skill_dir>/scripts/relay_knowledge_cli.py" --timeout 1140 -- repo index-worker --task-id <task_id> --format json', timeout_ms=1200000)
```

Use `repo index-worker` only for executing or recovering an already queued code index task. Do not use it as the normal first choice for building an index; use `repo index <alias> --ref <ref>` for ordinary repository indexing. If `repo index-worker` returns empty output, report that no queued task was claimed or executed; do not claim that indexing completed.

Graph, index, files, workers, and audit:

```bash
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- ingest --source <scope> --content "<text>" --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- query "<text>" --source <scope> --limit 10 --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- graph inspect --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" --timeout 1140 -- index refresh --kind bm25 --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" --timeout 1140 --env RELAY_KNOWLEDGE_FILE_INDEX_ROOTS="<absolute-root>" -- files index --root "<absolute-root>" --source local-files --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- files query "<text>" --source local-files --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- worker status --kind embedding --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" --timeout 1140 -- worker run-once --kind embedding --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- audit query --limit 50 --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- provider probe --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- proposal list --state proposed --limit 20 --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- proposal show <proposal_id> --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- proposal reject <proposal_id> --by <actor> --reason "<reason>" --format json
```

Long-running relay-teams shell tool shapes:

```text
shell(command='python "<skill_dir>/scripts/relay_knowledge_cli.py" --timeout 1140 -- index refresh --kind bm25 --format json', timeout_ms=1200000)
shell(command='python "<skill_dir>/scripts/relay_knowledge_cli.py" --timeout 1140 --env RELAY_KNOWLEDGE_FILE_INDEX_ROOTS="<absolute-root>" -- files index --root "<absolute-root>" --source local-files --format json', timeout_ms=1200000)
shell(command='python "<skill_dir>/scripts/relay_knowledge_cli.py" --timeout 1140 -- worker run-once --kind embedding --format json', timeout_ms=1200000)
```

Allowed `index refresh --kind` values: `bm25`, `semantic`, `vector`.
Allowed `worker --kind` values: `embedding`, `ocr`, `vision`, `extractor`.
Allowed `--freshness` values for knowledge and repo queries: `allow-stale`, `wait-until-fresh`, `graph-only`.
Use `allow-stale` by default. Use `wait-until-fresh` only when the user explicitly asks for the latest indexed state or strong freshness; it may wait for indexing work. When using `wait-until-fresh`, pass wrapper `--timeout 1140` and outer `timeout_ms=1200000`.
Allowed `proposal list --state` values: `proposed`, `accepted`, `rejected`, `superseded`.
`proposal accept`, `proposal reject`, and `proposal supersede` require a proposal id and `--by <actor>`. Do not run a proposal decision command if either value is unknown.

Service and MCP access:

```bash
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- service status --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- service plan install --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- service operator status --format json
```

Do not run `service run` as a normal synchronous shell command. It starts a long-lived service and should only be used when the user explicitly asks to start the Relay Knowledge service. When using relay-teams shell execution, run `python "<skill_dir>/scripts/relay_knowledge_cli.py" -- service run --web --mcp streamable-http` with the outer shell tool `background=true`.

Pseudo tool call shape:

```text
shell(command='python "<skill_dir>/scripts/relay_knowledge_cli.py" -- service run --web --mcp streamable-http', background=true)
```

Setup profiles:

```bash
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- setup profile local --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- setup profile agent-readonly --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- setup profile service --format json
python "<skill_dir>/scripts/relay_knowledge_cli.py" -- setup profile external-embedding --format json
```

`setup doctor` and `setup profile` may return suggested remediation commands containing raw `relay-knowledge ...` invocations. Do not execute those commands verbatim. Convert each suggestion to the wrapper form in this skill, then apply this skill's timeout, mutation, and background-service rules before running it.

## Operating Rules

- If the CLI is missing, tell the user Relay Knowledge CLI must be installed from the Relay Knowledge connector. Use `--install-if-missing` only when the user asks you to install or update it.
- Do not use wrapper `--detach`. Run `repo index` synchronously with a long outer command timeout so Relay Knowledge owns the SQLite database in a single CLI process until indexing completes.
- For cold `repo index`, request outer `timeout_ms=1200000` and pass wrapper `--timeout 1140` before the first `--`. If a repository is expected to take more than 19 minutes, do not claim that the skill-only synchronous path can finish it; tell the user relay-teams must raise its command timeout limit or relay-knowledge must support safe background status checks.
- Run `repo status <alias> --format json` after the synchronous `repo index` command returns only if the index response does not already prove `task.state=succeeded` or, when no task is present, `status.state=fresh`. If both `task` and `status` are present, `task.state` is authoritative for the current attempt. Do not start status/query/report commands concurrently with the indexing command.
- Use `repo scope preview <alias> --ref <ref> --format json` or `repo index <alias> --dry-run --format json` before indexing if the requested scope is unclear or large.
- Do not use `--path` or `--language` with `repo index`; use scoped `repo register` first when path or language filters are needed.
- Use `repo index-worker --task-id <task_id>` only for existing queued index tasks. Do not invent a task id, and do not use `repo index-worker` when the goal is to start indexing a repository from an alias. Use wrapper `--timeout 1140` and outer `timeout_ms=1200000` for `repo index-worker`.
- Treat empty output from `repo index-worker` as "no queued task was claimed"; do not interpret it as success.
- Use wrapper `--timeout 1140` and outer `timeout_ms=1200000` for potentially long mutation or worker commands: `repo update`, `files index`, `index refresh`, and `worker run-once`.
- Use `--freshness allow-stale` unless the user explicitly asks for latest indexed data. For `--freshness wait-until-fresh`, use wrapper `--timeout 1140` and outer `timeout_ms=1200000`.
- Run `service run` only as an outer background shell task. Do not wait on it synchronously as part of a normal question-answering workflow.
- Use `setup profile` only with one of `local`, `agent-readonly`, `service`, or `external-embedding`.
- Convert raw `relay-knowledge ...` commands returned by setup diagnostics into `python "<skill_dir>/scripts/relay_knowledge_cli.py" -- ...` wrapper commands before execution.
- Only run mutation commands when the user explicitly asks to change Relay Knowledge state, start work, or control a service. Mutation commands are `ingest`, `repo register`, `repo index`, `repo index-worker`, `repo update`, `index refresh`, `files index`, `worker run-once`, `proposal accept`, `proposal reject`, `proposal supersede`, `service definition write`, `service operator pause`, `service operator resume`, and `service run`.
- Use `repo update` for explicit base-to-head diffs. If it reports a missing base scope, index the base ref first.
- Use `--ref worktree` only when the user wants dirty worktree or overlay indexing.
- Do not mutate Relay Knowledge state for diagnostic requests. Status, health, version, help, setup doctor/profile, provider probe, service status/doctor/plan, service operator status, proposal list/show, reports, and audit queries are read-only diagnostics; repository registration, ingest, repository index/update/index-worker, index refresh, worker run-once, proposal accept/reject/supersede, service definition writes, service operator pause/resume, and service run mutate runtime state or start long-lived processes.

## Scripts

- relay_knowledge_cli: Resolve the connector-managed Relay Knowledge CLI and pass through arbitrary CLI arguments. (scripts/relay_knowledge_cli.py)
