# Gateway, ACP, and MCP-over-ACP Design

## 1. Background

The current architecture has one strong internal runtime and one primary external control surface:

- `interfaces/server`: HTTP and SSE control plane for web UI, CLI, and SDK
- `sessions/` and `sessions/runs/`: internal session and run lifecycle truth
- `agents/` and `tools/`: orchestration, execution, and tool runtime
- `mcp/`: app-scoped MCP configuration and registry
- `triggers/`: stateless event ingestion and audit

This works well for backend-driven flows, but ACP stdio introduces a different shape:

- the process is launched by an external host
- communication is bidirectional over stdio JSON-RPC
- the host may provide session-scoped capabilities
- the host may provide session-scoped MCP servers over ACP transport
- some future channels, such as Feishu or WeChat, are long-lived conversation channels rather than simple webhooks

That shape does not fit cleanly into `interfaces/server` or `triggers`.

## 2. Why a Gateway Module

A dedicated `gateway/` module should be introduced as the external channel session layer.

Responsibilities:

- manage external channel sessions and map them to internal `session_id` and `run_id`
- translate external protocol messages into internal runtime calls
- translate internal runtime events into channel-specific updates
- manage channel-scoped state, capabilities, cursors, and permissions
- host session-scoped MCP-over-ACP relay state

Non-responsibilities:

- owning the internal run state machine
- replacing `SessionService`, `RunManager`, or `TaskExecutionService`
- becoming a second business truth source for tasks, messages, or approvals
- replacing `triggers` for stateless event ingestion

The core runtime remains the source of truth. Gateway owns only external channel mapping and protocol state.

## 3. Boundary With Existing Modules

### 3.1 `interfaces/server`

`interfaces/server` remains the control plane.

Responsibilities:

- `/api/*` for web UI, CLI, and SDK
- configuration, inspection, admin, and recovery endpoints
- backend-hosted HTTP and SSE access

Non-responsibilities:

- ACP protocol handling
- IM conversation protocol handling
- MCP-over-ACP message relay

Rule:

- gateway must not call back into the local `/api/*` routes to drive runtime behavior

Both the server and the gateway should call the same container-managed core services directly.

### 3.2 `triggers`

`triggers` remains the stateless event ingress layer.

Responsibilities:

- webhook and generic event ingest
- event auth and audit log
- trigger configuration and replayable ingest history

Non-responsibilities:

- maintaining conversation sessions
- driving continuous back-and-forth messaging
- carrying protocol-specific session state

Rule:

- IM as a webhook or event source stays in `triggers`
- IM as a long-lived conversation channel goes through `gateway`
- one incoming IM message may be processed by `gateway` and optionally mirrored into `triggers` when product rules require event-style side effects

### 3.3 `mcp`

`mcp/` remains the app-scoped MCP management layer.

Responsibilities:

- load app config from `mcp.json`
- validate and expose known MCP servers
- connect to stdio, HTTP, or SSE MCP servers configured for the application

Non-responsibilities:

- storing ACP session-scoped MCP servers
- relaying `mcp/connect`, `mcp/message`, or `mcp/disconnect` across ACP
- owning host-provided MCP transport state

Rule:

- app-scoped MCP belongs to `mcp/`
- session-scoped MCP over ACP belongs to `gateway/`

### 3.4 Core Runtime

The following remain business truth:

- `SessionService`
- `RunManager`
- `TaskExecutionService`
- runtime repositories under `sessions/runs/`, `agents/`, `tools/`, and `persistence/`

Gateway must adapt to them, not replace them.

## 4. Target Layering

The recommended layering is:

- `gateway/`: external channel session and routing layer
- `gateway/bridges/`: protocol bridges
- `gateway/backends/`: execution backends

This translates the OpenClaw-style split into the current repository without moving the business truth out of the existing runtime.

### 4.1 `gateway/`

Responsibilities:

- external session lifecycle
- internal session and run mapping
- channel capability storage
- update fanout
- approval and permission bridge
- session-scoped MCP state

### 4.2 `gateway/bridges/`

Responsibilities:

- implement wire protocols only
- parse external messages and emit typed gateway commands
- serialize gateway updates back to the external protocol

The first bridge is `acp_stdio`.

Future bridges may include:

- `feishu`
- `wechat`

### 4.3 `gateway/backends/`

Responsibilities:

- define how one gateway session turn is executed
- keep protocol bridges independent from the actual execution engine

The first backend should be `internal_runtime`, which directly reuses the current Agent Teams runtime.

Future backends may include an external runtime adapter similar to an `acpx`-style harness, but that is not required for the first version.

## 5. Module Layout

Recommended new package layout:

- `src/relay_teams/gateway/models/`
- `src/relay_teams/gateway/session/`
- `src/relay_teams/gateway/dispatch/`
- `src/relay_teams/gateway/bridges/acp_stdio/`
- `src/relay_teams/gateway/backends/internal_runtime/`
- `src/relay_teams/gateway/cli.py`

Suggested responsibilities:

- `models/`: typed gateway records, capabilities, permissions, MCP connection records, and update payloads
- `session/`: repository and service for external session mapping and recovery
- `dispatch/`: external-message to internal-call mapping and internal-event to external-update mapping
- `bridges/acp_stdio/`: ACP stdio transport and JSON-RPC dispatcher
- `backends/internal_runtime/`: adapter from gateway commands to `SessionService` and `RunManager`

## 6. ACP stdio Design

ACP stdio should be implemented as a bridge under `gateway/bridges/acp_stdio/`.

Responsibilities:

- own stdio JSON-RPC framing
- handle ACP lifecycle methods
- advertise capabilities during `initialize`
- open and load gateway sessions
- map `session/prompt` to internal runtime actions
- publish `session/update` messages from internal runtime events
- bridge permission requests
- host MCP-over-ACP methods

Critical process rule:

- in ACP stdio mode, `stdout` must be reserved for protocol messages only
- logs must go to `stderr` or files

This implies a runtime mode or logger configuration path separate from the current HTTP server defaults.

### 6.1 Multimodal ACP Transport

ACP stdio must project the same visible multimodal payloads used by the model and frontend.

Rules:

- advertise `promptCapabilities.image = true`
- advertise `promptCapabilities.audio = true`
- ACP does not expose a first-class video prompt capability; video input and output must use `resource` / `resource_link`
- inbound ACP `image` and `audio` blocks are normalized into session-scoped media assets before entering the run layer
- inbound ACP `resource` / `resource_link` blocks may represent image, audio, or video references; video stays reference-only
- outbound text remains `agent_message_chunk`
- outbound image and audio payloads should be emitted as ACP content blocks
- outbound video payloads should be emitted as `resource_link`
- native media-generation progress should be bridged as ACP-compatible progress/tool updates so ACP hosts can render long-running generation without custom extensions

## 7. Gateway Session Model

Gateway needs its own typed session state instead of overloading `SessionRecord.metadata`.

Recommended fields:

- `gateway_session_id`
- `channel_type`
- `external_session_id`
- `internal_session_id`
- `active_run_id`
- `peer_user_id`
- `peer_chat_id`
- `capabilities_json`
- `last_sync_cursor`
- `channel_state_json`
- `created_at`
- `updated_at`

Rules:

- one external channel session maps to one internal `session_id`
- the gateway may track one active `run_id` for follow-up and recovery
- recoverable run state still lives in the current run runtime repositories
- gateway session state stores only the mapping and channel-specific context needed to resume the conversation

## 8. Event and Command Flow

The gateway command flow should be:

1. bridge receives an external message
2. bridge validates and converts it into typed gateway input
3. gateway session service resolves or creates the internal session mapping
4. backend adapter calls `SessionService`, `RunManager`, or related core services
5. runtime emits events through the existing event hub and repositories
6. gateway dispatch maps those internal events to channel updates
7. bridge serializes the updates back to the external protocol

This keeps the business truth in one place while allowing multiple channels.

### 8.1 Tool Result Visibility Rule

Tool results should have two separate representations:

- a visible result used by the model, ACP, and frontend
- an internal runtime record used for audit, recovery, and diagnostics

Rules:

- ACP and frontend must render the same tool result payload the model receives
- visible tool results should be output-first, with only a small number of
  top-level state fields that materially affect the next model step
- runtime-only fields such as approval bookkeeping, duration, and raw debug output must not appear in the visible result
- approval denial and approval timeout must be returned as normal tool errors in the visible result, with the message written for model consumption rather than human operator instructions

## 9. MCP-over-ACP

MCP-over-ACP must be treated as a first-class capability.

The official ACP RFD defines a model where the host can provide MCP servers with `transport: "acp"` during session setup, and the ACP channel then carries MCP control and message traffic.

That impacts the design in three ways.

### 9.1 Session-scoped MCP State

Gateway sessions must store session-scoped MCP server definitions and connection state.

Recommended records:

- `GatewayMcpServerSpec`
- `GatewayMcpConnectionRecord`

Recommended stored fields:

- server display name
- provider-generated MCP server `id`
- effective transport kind
- connection state
- channel binding information
- last activity timestamps

The provider-generated `id` is the routing key for MCP-over-ACP and must not be replaced by a local alias.

### 9.2 ACP Relay Responsibilities

The ACP bridge must handle the MCP-over-ACP methods:

- `mcp/connect`
- `mcp/message`
- `mcp/disconnect`

Gateway owns these relay flows because they are channel-scoped and session-scoped, not app-scoped.

### 9.3 Effective MCP View

The current runtime uses a single application MCP registry.
That is not enough once ACP sessions can inject their own MCP servers.

A new composition layer is needed.

Recommended abstraction:

- one effective MCP provider per runtime session
- effective MCP view equals app-scoped MCP plus gateway session-scoped MCP

Consumers that currently take the global `McpRegistry` will need to be able to resolve an effective registry or toolset source for the active session.

This affects at least:

- prompt assembly
- runtime tool snapshots
- provider factory wiring
- task execution runtime

Skill-routing note:

- gateway ACP stdio continues to reuse the internal runtime prompt pipeline
- routed skill candidates therefore appear only in the per-turn user prompt appendix
- gateway ACP must not add protocol fields just to carry skill-routing metadata

## 10. Bridge Strategy for MCP-over-ACP

The current execution stack is not ACP-native.
The safest first implementation is therefore a bridge strategy.

Bridge strategy:

- gateway accepts ACP-hosted MCP servers
- gateway relays ACP MCP traffic across the active ACP channel
- internal execution continues to consume MCP through a local adapter that looks like an MCP-capable tool source

This avoids rewriting the entire current MCP integration layer while still allowing ACP-hosted MCP servers to participate in the runtime.

## 11. IM Extension Strategy

Future IM channels should be split into two shapes.

### 11.1 Conversation Channels

Long-lived conversational IM channels belong in `gateway`.

Examples:

- private Feishu chats
- private WeChat conversations
- persistent support or assistant threads

Responsibilities:

- message receive and reply
- external session identity
- channel-specific presentation or approval UX
- resume and follow-up behavior

ACP-specific paused run behavior:

- `session/prompt` should treat `run_paused` as the end of the current turn, not as a protocol error.
- gateway session state must keep `active_run_id` while a run is paused for `awaiting_recovery`.
- `session/resume` resumes the gateway session's active recoverable run and starts a new event watch cycle.
- if the runtime replays already-emitted text while re-attaching the resumed watcher, the ACP bridge must suppress the previously delivered prefix and only forward the new continuation tail to the host.
- the resume stream contract is "continue from the interruption point", not "re-send the already rendered answer".

### 11.2 Event Sources

Webhook-like IM events belong in `triggers`.

Examples:

- a message that should trigger a workflow without opening a persistent chat session
- a bot callback that should only record an event and fan out elsewhere

Rules:

- gateway may mirror selected conversation messages into trigger ingest when needed
- triggers do not become session managers

## 12. Documentation Follow-ups

This design note should be the first documentation artifact.

Later, once interfaces and persistence are frozen, update:

- `docs/api-design.md`
- `docs/database-schema.md`

Recommended rule:

- do not write gateway protocol details into the API reference until the management API shape is stable
- do not write gateway tables into the schema document until the typed models and persistence fields are frozen

## 13. Non-goals for the First Version

The first version should not:

- turn gateway into the sole business truth source
- remove or bypass the current HTTP server
- rewrite the internal run state machine
- move app-scoped MCP into gateway
- require an external runtime backend before ACP stdio can work

## 14. Summary of Decisions

The architecture decisions in this document are:

- add `gateway/` as the external channel session layer
- keep `interfaces/server` as the HTTP control plane
- keep `triggers` as the stateless event ingress layer
- keep `mcp/` as the app-scoped MCP management layer
- put ACP stdio under `gateway/bridges/acp_stdio/`
- treat MCP-over-ACP as a gateway-owned session capability
- use an `internal_runtime` backend first and reuse the current core services

## 15. References

- ACP architecture: https://agentclientprotocol.com/get-started/architecture
- MCP-over-ACP RFD: https://agentclientprotocol.com/rfds/mcp-over-acp
- OpenClaw architecture overview: https://clawdocs.org/architecture/overview/
- OpenClaw gateway: https://clawdocs.org/architecture/gateway/
- OpenClaw ACP CLI: https://docs.openclaw.ai/cli/acp
