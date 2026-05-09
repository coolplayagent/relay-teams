# Agent Runtime Architecture

## Goal

Relay Teams should have one execution architecture for roles, regardless of
whether the role uses the built-in local provider, ACP, A2A, or a CLI
app-server runtime. Existing subagent flows should become runtime instance
projections instead of separate execution concepts.

## Runtime Flow

Every role turn follows the same sequence:

1. Resolve the effective role, including temporary role snapshots and memory.
2. Resolve runtime binding from `bound_agent_id`.
3. Build a runtime request with run, task, instance, workspace, prompt, tool,
   and media context.
4. Dispatch through `AgentRuntimeRouter`.
5. Persist normalized run events, messages, runtime prompt snapshots, tool
   snapshots, status transitions, and final output.

Memory and context-window management are session-runtime concerns, not adapter
concerns. Before dispatch reaches the local provider, ACP, A2A, or CLI adapter,
the shared prompt path must prepare the role prompt and history in this order:

```text
resolve role
-> inject legacy reflection memory when still available
-> inject Memory Bank project memory
-> load session history
-> prune to a safe tool-call boundary
-> inject existing compaction summary
-> compute full prompt budget
-> microcompact old tool results in the live prompt view
-> run full rolling-summary compaction when still over budget
-> repair provider-replayable history
-> dispatch to runtime adapter
```

`MemoryBankService` is the long-term memory system for the unified runtime. It
owns three tiers, Working, Medium-term, and Persistent, and the six memory
operations: consolidation, updating, indexing, forgetting, retrieval, and
condensation. `role_memories` remains only a legacy reflection-memory
compatibility layer during migration.

`ConversationMicrocompactService` and `ConversationCompactionService` are short
term session context controls. Microcompact changes only the prompt view sent to
the runtime. Full compaction writes session history markers, hides old messages,
and injects the rolling summary on later turns. Neither one replaces Memory
Bank, and Memory Bank does not replace context compaction.

The router selects one adapter:

- local: the existing Relay Teams provider chain
- acp: reusable remote ACP session
- a2a: remote Agent2Agent task/message lifecycle
- cli: stdio JSON-RPC app-server turn

## Package Responsibilities

`agent_runtimes.models`
: Runtime config, protocol, transport, request, result, probe, and session
  models.

`agent_runtimes.instances`
: Runtime instance record, lifecycle, id generation, and instance factories.

`agent_runtimes.provider`
: LLM provider adapter that turns a Relay Teams provider request into a runtime
  turn.

`agent_runtimes.router`
: Protocol dispatch and common lifecycle normalization.

`agent_runtimes.clients`
: Protocol-specific clients for ACP, A2A, and CLI.

`agent_runtimes.bus`, `agent_runtimes.bus_models`, `agent_runtimes.tools`
: Runtime message bus and tools previously owned by orchestration A2A modules.

`agent_runtimes.host_tools`
: Host-tool bridge that exposes Relay Teams tools to compatible external
  runtimes while preserving approval, audit, hook, and state semantics.

`agent_runtimes.native_config` and `agent_runtimes.skill_bridge`
: Native runtime config generation and skill projection for runtimes that can
  consume their own config files or skill directories.

## Subagent Consolidation

Relay Teams currently has two subagent product paths.

Orchestration mode uses `orch_dispatch_task`. It binds a delegated task to one
role and one reusable session-level runtime instance. Non-concurrent work for
the same `session_id + role_id` reuses the instance. Same-role concurrent work
may create an ephemeral clone.

Normal mode uses `spawn_subagent`. It creates a fresh one-shot `subagent_run_*`,
an ephemeral runtime instance, and an independent conversation. The caller can
wait synchronously or manage it as a background task.

Both paths should keep their user-visible behavior, but execution should be
identical after launch:

1. Create or reuse an `AgentRuntimeRecord`.
2. Create or update task/run lifecycle records.
3. Execute the role turn through `AgentRuntimeRouter`.
4. Project the instance as a subagent when returning session, recovery, or
   background-task state.

`SubAgentInstance` is retained only as a compatibility model. New code should
use `AgentRuntimeRecord` and runtime instance factories.

## A2A Consolidation

External A2A is a runtime protocol adapter. It owns Agent Card discovery,
direct JSON-RPC endpoint fallback, `message/send`, and `tasks/get` polling.

Internal A2A is a runtime message bus. It owns publish/subscribe state for
runtime instances inside a Relay Teams run.

Both live under `agent_runtimes` so orchestration no longer owns a separate A2A
implementation.

## System Reminder Delivery

External runtimes participate in the same system-reminder delivery contract as
the built-in provider runtime, but they do not own reminder policy.

`sessions/runs/system_injection.py` owns reminder queue filtering, classifier
disposition, redacted run events, and conversation-history persistence through
`SystemInjectionConsumer`. `agent_runtimes.provider` only invokes that consumer
before composing ACP, A2A, or CLI prompts. This lets queued startup reminders
become the latest user prompt without teaching each protocol adapter how to
classify or persist reminder messages.

ACP host tools have one additional boundary. After a hosted Relay Teams tool
returns, `agent_runtimes.host_tool_bridge` asks the same consumer for any
boundary reminders addressed to the active runtime instance. If reminders are
applied, the bridge preserves the original tool return value and appends the
rendered reminder text to the tool result content. The external ACP agent then
sees the guidance next to the host tool result, while reminder state, event
redaction, and history writes remain centralized in sessions/runs.

## Complexity Guardrails

- Do not introduce another planner or orchestration framework inside
  `agent_runtimes`.
- Keep protocol adapters thin. Shared behavior belongs in router/provider
  lifecycle helpers.
- Keep host tools behind one bridge.
- Keep interface layers on HTTP/SSE only.
- Do not bypass role validation or persisted-dirty-data tolerance rules.
