---
name: relay-knowledge
version: "1.0.0"
description: Use Relay Knowledge through the relay-knowledge CLI. Use this for local knowledge graph status, service diagnostics, code repository registration, cold or incremental repository indexing, index progress checks, code search, impact analysis, reports, file indexing, graph inspection, workers, audit queries, setup profiles, external vector/embedding configuration, and MCP service startup.
---

# Relay Knowledge

Use this skill when the user asks to use Relay Knowledge, build or refresh a code repository index, query indexed code, inspect graph or index health, run local knowledge/file retrieval, analyze code impact, configure external vector/embedding providers, or start/check the Relay Knowledge service.

Invoke Relay Knowledge directly with the `relay-knowledge` CLI. Prefer `--format json` for machine-readable commands. Use non-JSON formats only when the user explicitly wants a markdown report or human CLI output.

## Install And Verify

If `relay-knowledge` is missing, tell the user to install or update the Relay Knowledge CLI from GitHub releases:

- release page: `https://github.com/coolplayagent/relay-knowledge/releases`
- repository: `coolplayagent/relay-knowledge`

Download the platform-specific archive, verify it against the release `checksums.txt`, and place the `relay-knowledge` executable on `PATH`. If the host application provides a managed tool installer or connector for Relay Knowledge, using that installer is acceptable, but this skill must not depend on project-specific APIs or directories.

## Download Latest CLI

When asked to install or update Relay Knowledge, do not rely on a hard-coded version. Query the latest release first, then choose one install path.

GitHub release path:

```bash
gh release view --repo coolplayagent/relay-knowledge --json tagName,url,assets,isLatest
```

If `gh` is unavailable, use the GitHub release API:

```bash
curl -fsSL https://api.github.com/repos/coolplayagent/relay-knowledge/releases/latest
```

Select the asset matching the current OS and CPU. Known release target triples include `x86_64-unknown-linux-gnu`, `aarch64-unknown-linux-gnu`, `x86_64-apple-darwin`, `aarch64-apple-darwin`, `x86_64-pc-windows-msvc`, and `aarch64-pc-windows-msvc`. Do not guess asset names; inspect the latest release asset list.

After download, verify the archive with the release `checksums.txt` before installing:

```bash
sha256sum -c checksums.txt --ignore-missing
relay-knowledge --version
```

Rust registry path:

```bash
cargo binstall relay-knowledge --no-confirm
cargo install relay-knowledge --locked
```

Prefer `cargo binstall` when it is installed because it can use prebuilt Rust binary artifacts. Use `cargo install` as the Rust source-build fallback. Do not claim the Rust registry path is available until `cargo info relay-knowledge`, `cargo search relay-knowledge --limit 5`, or the install command confirms the package exists. If the Rust registry path is unavailable or fails, fall back to the GitHub release path.

Check availability and local configuration before doing work:

```bash
relay-knowledge --version
relay-knowledge version
relay-knowledge version --format json
relay-knowledge version check --format json
relay-knowledge status --format json
relay-knowledge health --format json
relay-knowledge setup doctor --format json
```

Allowed global flags:

- `--version`
- `--help`
- `--format text|json|markdown|streaming-json`

Do not use `--format streaming-json` with `version`; that command only supports `text`, `json`, and `markdown`. Prefer `text` or `json` for `help`.

## Version Checks

Use `version` for local binary identification:

```bash
relay-knowledge version
```

`version` only prints the current binary version. It does not load runtime configuration and does not access the network.

Use `version check` when the user asks whether a newer CLI is available:

```bash
relay-knowledge version check --format json
```

`version check` uses the CLI's configured `net::http` path to query GitHub Releases and crates.io, then caches the result in the runtime cache directory. Treat it as a read-only network diagnostic. It does not install, replace, or restart the binary.

Ordinary interactive `text` and `markdown` CLI commands may print a short update hint to stderr only when a stable newer version is found. The main command stdout is emitted first, and the hint must not be interpreted as command failure or automatic upgrade.

## Setup Profiles

Render setup guidance before asking the user to set environment variables:

```bash
relay-knowledge setup profile local --format json
relay-knowledge setup profile agent-readonly --format json
relay-knowledge setup profile service --format json
relay-knowledge setup profile external-embedding --format json
```

Use `setup profile` only with one of `local`, `agent-readonly`, `service`, or `external-embedding`. Profiles are recommendations only; they do not write environment files or install services.

For external semantic/vector backends, guide the user to configure these variables from `setup profile external-embedding`:

- `RELAY_KNOWLEDGE_SEMANTIC_BACKEND=external`: enable external semantic read-model metadata.
- `RELAY_KNOWLEDGE_VECTOR_BACKEND=external`: enable external vector read-model metadata.
- `RELAY_KNOWLEDGE_LLM_PROVIDER=openai_compatible`: select the provider contract.
- `RELAY_KNOWLEDGE_EMBEDDING_BASE_URL`: embedding provider base URL.
- `RELAY_KNOWLEDGE_EMBEDDING_API_KEY`: provider API key; diagnostics should only report whether it is configured.
- `RELAY_KNOWLEDGE_TEXT_EMBEDDING_MODEL`: text embedding model identity stored with cursor metadata.
- `RELAY_KNOWLEDGE_IMAGE_EMBEDDING_MODEL`: optional image embedding model identity for multimodal workers.
- `RELAY_KNOWLEDGE_EMBEDDING_DIMENSION`: vector dimension recorded in backend and cursor metadata.

After configuration, verify with:

```bash
relay-knowledge provider probe --format json
relay-knowledge index refresh --kind semantic --kind vector --format json
relay-knowledge health --format json
```

`setup doctor` and `setup profile` may return suggested remediation commands containing raw `relay-knowledge ...` invocations. Apply this skill's timeout, mutation, background-service, and concurrency rules before running them.

## Command Allowlist

Only use the Relay Knowledge commands listed in this skill. Do not infer, invent, or try adjacent commands.

Allowed top-level commands:

- `status`
- `health`
- `version`
- `version check`
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
- `repo scope preview`
- `repo update`
- `repo query`
- `repo impact`
- `repo report`
- `repo status`

Do not run bare `repo`; only run the listed `repo ...` subcommands. Do not use `repo list`; this CLI does not provide it. If an alias is unknown, do not try to enumerate repositories.

## Repository Workflow

For diagnostic or status-only requests, do not register a repository just to discover an alias; ask for the alias or report that the alias is required. Treat requests to build, open, inspect, or query a code knowledge graph, code graph, repository graph, code knowledge map, or code map as repository indexing/setup intent. For indexing, code-query, code graph, or code map requests where the user asks to use the current repository and no alias is provided, derive a conservative alias from the current repository directory name and run `repo register "<repo-root>" --alias <alias> --format json` before indexing or querying.

Register and preview:

```bash
relay-knowledge repo register "<repo-root>" --alias <alias> --format json
relay-knowledge repo scope preview <alias> --ref HEAD --format json
relay-knowledge repo index <alias> --dry-run --format json
```

Use `--path <filter>` and repeated `--language <id>` on `repo register` and `repo query` when the user scopes the request to specific source paths or languages. Do not pass `--path` or `--language` to `repo index`; that subcommand only accepts the alias plus `--ref` and `--dry-run`.

Index and check progress:

```bash
relay-knowledge repo index <alias> --ref HEAD --format json
relay-knowledge repo status <alias> --format json
relay-knowledge worker status --format json
relay-knowledge worker status --kind embedding --format json
```

Cold `repo index` can take a long time. Run it synchronously with a long outer command timeout when available, then inspect the JSON before claiming the index is usable. If the response contains a `task`, use `task.state` as the authoritative state for this index attempt; treat the index as ready only when `task.state` is `succeeded`. Use `status.state=fresh` only when the response has no `task`.

If the response contains an active, queued, running, retrying, or failed task state, report that task state and tell the user they can continue checking progress with `relay-knowledge repo status <alias> --format json`. Use `relay-knowledge worker status --format json` or `relay-knowledge worker status --kind embedding --format json` only as a supporting queue/lease diagnostic. Do not start `repo query`, `repo impact`, or `repo report` unless the user explicitly asks to continue with stale data.

Only run `repo status <alias> --format json` after `repo index` if the index response does not already prove `task.state=succeeded` or, when no task is present, `status.state=fresh`.

Query code:

```bash
relay-knowledge repo query <alias> --query "<text>" --kind hybrid --ref HEAD --limit 10 --format json
relay-knowledge repo query <alias> --query "<symbol>" --kind symbol --format json
relay-knowledge repo query <alias> --query "<symbol>" --kind definition --format json
relay-knowledge repo query <alias> --query "<symbol>" --kind references --format json
relay-knowledge repo query <alias> --query "<symbol>" --kind callers --format json
relay-knowledge repo query <alias> --query "<symbol>" --kind callees --format json
relay-knowledge repo query <alias> --query "<module>" --kind imports --format json
```

Allowed `repo query --kind` values: `hybrid`, `symbol`, `definition`, `references`, `callers`, `callees`, `imports`.

Update and analyze changes:

```bash
relay-knowledge repo update <alias> --base main --head HEAD --format json
relay-knowledge repo impact <alias> --base main --head HEAD --format json
relay-knowledge repo report <alias> --format markdown
```

After `repo update` returns, inspect the response before running `repo impact`, `repo query`, or `repo report`. If the command fails or reports a missing or stale base scope, report that result and index or reindex the base ref before retrying the update; do not continue analysis from a failed update.

Use `repo update` for explicit base-to-head diffs. If it reports a missing base scope, index the base ref first. Use `--ref worktree` only when the user wants dirty worktree or overlay indexing.

## Knowledge, Files, Workers, And Proposals

Use these commands for graph, retrieval index, file, worker, proposal, and audit tasks:

```bash
relay-knowledge ingest --source <scope> --content "<text>" --format json
relay-knowledge query "<text>" --source <scope> --limit 10 --format json
relay-knowledge graph inspect --format json
relay-knowledge index refresh --kind bm25 --format json
relay-knowledge files index --root "<absolute-root>" --source local-files --format json
relay-knowledge files query "<text>" --source local-files --format json
relay-knowledge worker status --kind embedding --format json
relay-knowledge worker run-once --kind embedding --format json
relay-knowledge audit query --limit 50 --format json
relay-knowledge provider probe --format json
relay-knowledge proposal list --state proposed --limit 20 --format json
relay-knowledge proposal show <proposal_id> --format json
relay-knowledge proposal reject <proposal_id> --by <actor> --reason "<reason>" --format json
```

Allowed `index refresh --kind` values: `bm25`, `semantic`, `vector`.
Allowed `worker --kind` values: `embedding`, `ocr`, `vision`, `extractor`.
Allowed `--freshness` values for knowledge and repo queries: `allow-stale`, `wait-until-fresh`, `graph-only`.
Use `allow-stale` by default. Use `wait-until-fresh` only when the user explicitly asks for the latest indexed state or strong freshness; it may wait for indexing work.
Allowed `proposal list --state` values: `proposed`, `accepted`, `rejected`, `superseded`.
`proposal accept`, `proposal reject`, and `proposal supersede` require a proposal id and `--by <actor>`. Do not run a proposal decision command if either value is unknown.

## Service And MCP

Use service diagnostics first:

```bash
relay-knowledge service status --format json
relay-knowledge service doctor --format json
relay-knowledge service plan install --format json
relay-knowledge service operator status --format json
```

Do not run `service run` as a normal synchronous shell command. It starts a long-lived service and should only be used when the user explicitly asks to start the Relay Knowledge service. Start `relay-knowledge service run --web --mcp streamable-http` with the host's background process mechanism when one is available; do not run it as a blocking foreground command.

Command to run in the background:

```bash
relay-knowledge service run --web --mcp streamable-http
```

## Concurrency And Subagents

Use subagents only when the user explicitly asks for parallel agent work or when the host runtime has already authorized subagent delegation. Keep Relay Knowledge state mutations serialized in the main agent.

Good parallel subagent tasks are read-only: independent `repo query` searches, separate `repo impact` review angles after indexes are ready, `repo status` progress checks, `worker status` diagnostics, report review, and audit/result summarization.

Do not let subagents concurrently run mutation commands. Mutation commands are `ingest`, `repo register`, `repo index`, `repo update`, `index refresh`, `files index`, `worker run-once`, `proposal accept`, `proposal reject`, `proposal supersede`, `service definition write`, `service operator pause`, `service operator resume`, and `service run`.

Subagents should return observations only. The main agent decides whether an index is usable, whether stale data is acceptable, and what to report to the user.

## Upgrade And Database Reuse

Relay Knowledge should reuse its existing runtime database and config across CLI upgrades. By default, keep using the platform runtime directories reported by `relay-knowledge status --format json`; do not delete data/config directories to fix upgrade issues unless the user explicitly asks to reset state.

If the user configured an isolated runtime, preserve and reuse the same `RELAY_KNOWLEDGE_HOME` value during and after CLI upgrades. After upgrading the binary, run:

```bash
relay-knowledge setup doctor --format json
relay-knowledge status --format json
relay-knowledge health --format json
```

Refresh indexes only when diagnostics or the user's request require it. Do not assume an upgraded CLI requires rebuilding a cold repository index.

## Operating Rules

- Only run mutation commands when the user explicitly asks to change Relay Knowledge state, start work, or control a service.
- Do not mutate Relay Knowledge state for diagnostic requests. Status, health, version, version check, help, setup doctor/profile, provider probe, service status/doctor/plan, service operator status, proposal list/show, reports, audit queries, `repo status`, and worker status are read-only diagnostics.
- For potentially long mutation or worker commands such as `repo index`, `repo update`, `files index`, `index refresh`, and `worker run-once`, request the longest supported outer command timeout available from the host runtime.
- Use `repo scope preview <alias> --ref <ref> --format json` or `repo index <alias> --dry-run --format json` before indexing if the requested scope is unclear or large.
- Run `service run` only through a background process mechanism. Do not wait on it synchronously as part of a normal question-answering workflow.
