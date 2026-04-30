# Agent Runtime A2A and CLI Spec

## 1. Scope

This feature extends the existing external ACP role backend into a protocol-aware agent runtime surface. It covers:

- ACP runtimes that keep the existing reusable remote session flow
- A2A runtimes that use Agent Card discovery plus JSON-RPC `message/send`
- CLI runtimes that expose a stdio JSON-RPC agent server in the active workspace
- public API, SDK, CLI, and Settings UI naming that uses `agent-runtimes`

The runtime config object still uses `agent_id` because role settings already bind through `bound_agent_id`; the public management interface is named for the runtime concept.

Design basis:

- Local research in `/opt/workspace/hello` identifies Codex app-server as a bidirectional JSON-RPC protocol over stdio/JSONL.
- The Codex app-server lifecycle is thread/turn based (`initialize`, `initialized`, `thread/start`, `turn/start`) rather than prompt text piped to stdin.
- Codex command execution and background-terminal capabilities are exposed as JSON-RPC methods such as `command/exec`, `command/exec/write`, `command/exec/resize`, and `command/exec/terminate`; the CLI runtime integration therefore treats a local CLI as a managed agent server, not as a one-shot shell command.

## 2. Public Interface

Backend routes:

- `GET /api/system/configs/agent-runtimes`
- `GET /api/system/configs/agent-runtimes/{agent_id}`
- `PUT /api/system/configs/agent-runtimes/{agent_id}`
- `DELETE /api/system/configs/agent-runtimes/{agent_id}`
- `POST /api/system/configs/agent-runtimes/{agent_id}:test`

SDK methods:

- `list_agent_runtimes()`
- `get_agent_runtime(agent_id)`
- `save_agent_runtime(agent_id, payload)`
- `delete_agent_runtime(agent_id)`
- `test_agent_runtime(agent_id)`

CLI surface:

- `relay-teams agent-runtimes list`
- `relay-teams agent-runtimes get <agent_id>`
- `relay-teams agent-runtimes save <agent_id> --config-json ...`
- `relay-teams agent-runtimes delete <agent_id>`
- `relay-teams agent-runtimes test <agent_id>`

CLI commands percent-encode `agent_id` before placing it in `/api/*` paths so runtime ids with URI-reserved characters use the same route semantics as the SDK and Settings UI.

Settings UI labels use `Agent Runtime` for the tab and editor surface.

## 3. Config Model

Each saved runtime entry contains:

- `agent_id`
- `name`
- `description`
- `protocol`: `acp`, `a2a`, or `cli`
- `transport`: `stdio`, `streamable_http`, or `custom`

Validation rules:

- `a2a` requires `streamable_http`
- `cli` requires `stdio`
- `acp` may use the existing stdio, streamable HTTP, or custom transports
- secret env/header values are persisted through the secret store and returned only as `configured`

## 4. A2A Runtime Flow

An A2A runtime uses Streamable HTTP.

Endpoint resolution:

- A URL ending in `/.well-known/agent.json` is treated as an Agent Card URL.
- Other URLs are first probed through root and path-relative Agent Card candidates.
- If Agent Card discovery fails for a non-card URL, the original URL is treated as a direct JSON-RPC endpoint.
- A direct endpoint such as `/rpc.json` is not treated as an Agent Card just because it ends in `.json`.
- Runtime `:test` uses the same endpoint semantics. When an Agent Card is available, the resolved `card.url` JSON-RPC endpoint is probed with a non-mutating `tasks/get` request before returning success. When no Agent Card is available for a non-card URL, the same probe is sent to the original direct endpoint. A response must be JSON-RPC shaped with `jsonrpc: "2.0"`, the matching id, and either a result field or a structured error object with a numeric code before it is considered reachable. `-32601` method-not-found is rejected because it proves the endpoint is not serving the A2A `tasks/get` method.

Prompt execution:

- `message/send` sends a user message with text parts and Relay Teams metadata.
- Agent Card discovery, `message/send`, and `tasks/get` share the configured prompt timeout as a global execution budget.
- Each outbound A2A request uses the remaining prompt budget as its per-request HTTP timeout.
- Explicit `message` responses return immediately.
- Task responses return successfully only when the task state is `completed`.
- Task states `failed`, `rejected`, `canceled`, `input-required`, and `auth-required` raise runtime errors using the status message when available.
- If the response contains an active task id, the runtime polls `tasks/get` until the task completes or fails.

## 5. CLI Runtime Flow

A CLI runtime uses stdio transport and starts in the active session workspace.

Protocol rules:

- stdin/stdout carry newline-delimited JSON-RPC messages.
- Initialization, `thread/start`, `turn/start`, and turn output waiting share the configured prompt timeout as one global execution budget. Prompt execution does not apply fixed per-phase caps to initialization; slow startup can use the remaining configured budget.
- The runtime is initialized with `initialize`, followed by an `initialized` notification.
- Runtime test/probe starts the CLI with an explicit cwd and resolves relative command paths from that same cwd. The Settings/API test endpoint uses the default workspace workdir; session execution uses the active session workspace.
- Runtime test/probe uses the configured stdio transport env, including `PATH`, when validating whether the CLI command can be started.
- Runtime test/probe accepts command paths only when they resolve to executable files; existing directories or non-executable files are rejected before subprocess startup.
- Agent Teams creates an ephemeral thread with `thread/start`.
- Prompt execution uses `turn/start` with the composed runtime prompt as a text input item.
- Assistant output is collected from `item/agentMessage/delta`; if no deltas are emitted, the completed `agentMessage` item is used as a fallback.
- `turn/completed` closes the prompt only when the turn status is `completed`.
- Failed or interrupted turns raise a runtime error from the turn error payload when present.
- If the CLI JSON-RPC process closes stdout during an active turn, the prompt fails immediately instead of waiting for the overall prompt timeout.
- Empty completed output is considered a runtime error.

Codex CLI handling:

- A saved `codex` command without an explicit app-server subcommand starts as `codex app-server --listen stdio://`.
- A saved `codex app-server ...` command is preserved, and `--listen stdio://` is appended when absent.
- Legacy `codex exec` options such as `--yolo`, `--model`, `--cd`, and `--output-last-message` are not forwarded to the app-server process.
- App-server configuration options such as `--config`, `--enable`, and `--disable` are preserved during migration from a bare `codex` command.
- Approval behavior is controlled through JSON-RPC thread/turn params (`approvalPolicy: "never"`), not by rewriting CLI prompt-execution flags.

## 6. Provider Integration

Role execution still starts from `bound_agent_id`.

Provider behavior:

- Resolve the saved runtime config, including runtime-only secret values.
- Dispatch by `protocol`.
- ACP keeps remote session reuse through `external_agent_sessions`.
- A2A and CLI do not write `external_agent_sessions` rows because they do not expose reusable ACP session ids.
- A2A and CLI responses are adapted into the same provider response shape used by ACP-backed roles.

## 7. Verification Plan

Required local validation:

- A2A unit tests for Agent Card discovery, direct `/rpc.json` endpoint fallback in probe and prompt execution, `message/send`, and task polling.
- CLI unit tests for stdio JSON-RPC initialize/thread/turn flow, streamed delta collection, completed-item fallback, Codex app-server command construction, legacy exec option filtering, and non-Codex argument preservation.
- Server, SDK, frontend API facade, and CLI tests for the `agent-runtimes` public route and command names.
- Browser smoke test through the Settings UI using the real local frontend/backend with Chrome DevTools MCP.

Regression risks covered:

- Direct A2A JSON-RPC endpoints ending in `.json` no longer fail during Agent Card parsing.
- Non-Codex CLI runtimes keep their own `--yolo` argument semantics.
- Bare Codex CLI configs no longer depend on prompt stdin/stdout or `codex exec`; they use the app-server JSON-RPC runtime.
