# External ACP Agents Design

## 1. Goal

Agent Teams needs an open external-agent integration point instead of a closed list of built-in runtimes.

The target model is:

- users configure any ACP-compatible external agent in Settings
- roles may bind to one configured external agent
- a session may direct one turn to a specific role with a leading `@Role`
- that direct-chat turn keeps the current session topology unchanged

This keeps the role system as the stable product surface while allowing the execution backend for that role to stay open.

## 2. Configuration Model

External agents are stored in `~/.agent-teams/agents.json`.

Each agent record contains:

- `agent_id`
- `name`
- `description`
- `transport`

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

For `stdio` transports, Agent Teams starts the external ACP process inside the active session workspace. The working directory is derived at runtime from the session's project context and is not saved in `agents.json`.

## 3. Secret Handling

Secret values for external agents must not be written to `agents.json`.

Rules:

- secret `env[]` and `headers[]` bindings are stored only in the keyring-backed secret store
- non-secret bindings are stored directly in `agents.json`
- read APIs return `configured=true/false` for secrets instead of rehydrating the secret value into the UI payload
- runtime resolution reattaches secret values only at execution time

This matches the repository rule that secrets live in keyring only.

## 4. Role Binding

Role configuration adds `bound_agent_id`.

Behavior:

- `bound_agent_id = null`: role continues to use the local provider/runtime path
- `bound_agent_id = "<agent>"`: provider selection switches that role to the external ACP backend

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

The external provider is responsible for:

- resolving the saved external agent config, including secret bindings
- creating or loading the remote ACP session
- sending prompt turns to the remote agent
- translating remote updates back into Agent Teams messages and run events

The coordinator and task runtime stay in place. The change is backend selection, not a second orchestration system.

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

- `GET /api/system/configs/agents`
- `GET /api/system/configs/agents/{agent_id}`
- `PUT /api/system/configs/agents/{agent_id}`
- `DELETE /api/system/configs/agents/{agent_id}`
- `POST /api/system/configs/agents/{agent_id}:test`

Role settings now expose the available external agents in `/api/roles:options`, and role documents round-trip `bound_agent_id`.

The Settings UI adds an `Agents` tab for CRUD and transport-specific editing.

## 9. Non-Goals

This change does not:

- replace the internal orchestration model
- let users type raw arbitrary code for transport adapters in config
- change session topology persistently when using `@Role`
- store external-agent secrets in plaintext files
