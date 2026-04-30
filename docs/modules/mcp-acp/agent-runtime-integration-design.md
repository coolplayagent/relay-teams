# Agent Runtime Integration Design

## 1. Goal

Agent Teams needs an open agent-runtime integration point instead of a closed list of built-in local runtimes.

The target model is:

- users configure ACP, A2A, or CLI agent runtimes in Settings
- roles may bind to one configured agent runtime
- a session may direct one turn to a specific role with a leading `@Role`
- that direct-chat turn keeps the current session topology unchanged

This keeps the role system as the stable product surface while allowing the execution backend for that role to stay open.

## 2. Configuration Model

Agent runtimes are stored in the resolved app config dir `agents.json`, by default `~/.relay-teams/agents.json`.

Each agent record contains:

- `agent_id`
- `name`
- `description`
- `protocol`
- `transport`

`protocol` selects the runtime behavior:

- `acp`: reusable Agent Client Protocol session over stdio, HTTP, or custom transport
- `a2a`: Agent2Agent JSON-RPC over Streamable HTTP
- `cli`: stdio JSON-RPC agent runtime execution for local coding CLIs

`transport` is a discriminated union:

- `stdio`
  - `command`
  - `args[]`
  - optional `env[]`
- `streamable_http`
  - `url`
  - optional `headers[]`
  - optional `ssl_verify`
- `custom`
  - `adapter_id`
  - `config`

The `custom` transport is an adapter extension point. Config only stores structured data. It does not execute arbitrary user-provided code.

For `stdio` transports, Agent Teams starts the runtime process inside the active session workspace. The working directory is derived at runtime from the session's project context and is not saved in `agents.json`.

CLI runtimes use the Codex app-server style lifecycle over stdio JSON-RPC: `initialize`, `initialized`, `thread/start`, `turn/start`, streamed assistant-message notifications, and `turn/completed`. A bare `codex` command is treated as a local Codex app-server runtime and launched as `codex app-server --listen stdio://`.

## 3. Secret Handling

Secret values for agent runtimes must not be written to `agents.json`.

Rules:

- secret `env[]` and `headers[]` bindings are stored only in the unified secret store
- non-secret bindings are stored directly in `agents.json`
- read APIs return `configured=true/false` for secrets instead of rehydrating the secret value into the UI payload
- runtime resolution reattaches secret values only at execution time

When a usable system keyring backend exists, the secret store uses keyring.
Otherwise it falls back to the resolved app config dir `secrets.json`, by default `~/.relay-teams/secrets.json`.

## 4. Role Binding

Role configuration adds `bound_agent_id`.

Behavior:

- `bound_agent_id = null`: role continues to use the local provider/runtime path
- `bound_agent_id = "<agent>"`: provider selection switches that role to the configured agent runtime backend

The binding lives on the role, not on the session, so the same product concept still works across:

- normal session topology
- orchestration delegation
- one-turn direct `@Role` chat

## 5. Session Reuse

External ACP sessions are reused by internal session and role identity.

The persistence key is:

- `session_id`
- `role_id`
- `agent_id`

The repository stores:

- outbound transport type
- remote `external_session_id`
- last-known session status
- timestamps

This allows later turns in the same Agent Teams session to reuse the remote ACP conversation/session instead of reinitializing it each time.

## 6. Runtime Flow

Provider selection now branches on role binding:

- unbound role -> existing local provider path
- bound role -> `ExternalAcpProvider`

The external runtime provider is responsible for:

- resolving the saved agent runtime config, including secret bindings
- creating or loading the remote ACP session
- sending prompt turns to the remote agent
- translating remote updates back into Agent Teams messages and run events
- exposing Agent Teams host tools to the remote ACP session when the bound role has local tools or skill tools

The coordinator and task runtime stay in place. The change is backend selection, not a second orchestration system.

### 6.1 Prompt Packaging

External ACP prompt turns send the effective provider system prompt on every `session/prompt`, not only the user prompt.

The outbound prompt text is packaged as:

- `Role Prompt`: current `request.system_prompt`
- `Host Tools`: host-tool usage guidance when Agent Teams exposes local tools to the remote agent
- `User Prompt`: the current user/task prompt text, including any routed `## Skill Candidates` appendix

This keeps the remote ACP session aligned with the active runtime role instructions even when the remote session itself is reused across turns.

Cache-safety constraint:

- routed skill candidates must never be injected into `Role Prompt`
- objective-dependent skill routing text only appears in `User Prompt`
- this preserves a stable provider system prompt prefix for the bound role while still surfacing relevant skills per turn

### 6.2 Host Tool Bridge

When a bound role has local tools or skill tools, Agent Teams injects one session-scoped stdio MCP server into `mcpServers` during `session/new` or `session/load`.

Rules:

- reserved MCP server id: `agent_teams_host_tools`
- local tool names are exported as `agent_teams_builtin_<tool_name>`
- skill tool names are exported as `agent_teams_skill_<tool_name>`
- the injected server is launched as a local stdio process using the current Agent Teams Python environment
- the stdio server receives the active run/task/session context through environment variables
- because the stdio payload is prompt-scoped, Agent Teams refreshes the remote ACP session when that context changes between prompts

The exported names are fully namespaced so they do not collide with native tools provided by the external ACP agent itself.

### 6.3 Runtime Model Profile Propagation

Bound agent runtimes still inherit the effective Agent Teams model selection for the current role and session.

Rules:

- Agent Teams resolves the effective `model_profile` using the role setting plus any session-scoped default-model override
- for OpenCode stdio ACP agents, Agent Teams injects that resolved model through `OPENCODE_CONFIG_CONTENT`, not through `--model`
- BigModel and other Z.AI-compatible profiles are projected onto OpenCode's built-in `zai` provider with the runtime API key injected through `ZHIPU_API_KEY`, so OpenCode keeps its provider-specific request shaping instead of falling back to a generic OpenAI-compatible transport
- when a Z.AI-compatible profile does not declare `context_window`, Agent Teams synthesizes a conservative OpenCode `limit.context` so custom injected model entries remain callable
- other OpenAI-compatible profiles still use an ephemeral custom provider with provider-level `api` and env-backed API key injection, instead of relying on the user's persisted OpenCode default
- outside the Z.AI special case above, Agent Teams only emits OpenCode `limit` values when both `context` and `output` are available, because ACP startup rejects partial limit objects
- when the resolved model runtime changes between prompts, Agent Teams recreates the external OpenCode transport instead of reusing the old process

This keeps a bound OpenCode role aligned with the current Agent Teams profile selection, rather than silently falling back to OpenCode's local default model.

## 7. Direct `@Role` Chat

The web composer supports a leading `@Role` mention.

Behavior:

- the mention is resolved against `Coordinator`, `MainAgent`, and normal roles
- the stripped prompt body is sent with `target_role_id`
- run creation stores `target_role_id` in `run_intents`
- coordinator root-role resolution prefers that `target_role_id` for the current run

Important constraint:

- this is a one-run override only
- it does not change `session_mode`
- it does not change `normal_root_role_id`
- it does not change the selected orchestration preset

The round and recovery projections expose `primary_role_id` so the frontend can render the correct main timeline and approval ownership for each run.

## 8. API and UI Surface

New backend surface:

- `GET /api/system/configs/agent-runtimes`
- `GET /api/system/configs/agent-runtimes/{agent_id}`
- `PUT /api/system/configs/agent-runtimes/{agent_id}`
- `DELETE /api/system/configs/agent-runtimes/{agent_id}`
- `POST /api/system/configs/agent-runtimes/{agent_id}:test`

Role settings now expose the available agent runtimes in `/api/roles:options`, and role documents round-trip `bound_agent_id`.

The Settings UI adds an `Agent Runtime` tab for CRUD and protocol-specific transport editing.

## 9. Non-Goals

This change does not:

- replace the internal orchestration model
- let users type raw arbitrary code for transport adapters in config
- change session topology persistently when using `@Role`
- store agent-runtime secrets in plaintext files
