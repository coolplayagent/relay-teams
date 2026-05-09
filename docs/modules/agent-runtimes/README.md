# Agent Runtimes

Agent runtimes are the execution backends used by Relay Teams roles. A role is
the product-level identity and policy surface. A runtime is the process,
protocol, or provider that executes one turn for that role.

This module owns the architecture for:

- local Relay Teams model-provider execution
- Agent Client Protocol runtimes
- Agent2Agent runtimes
- CLI app-server runtimes
- runtime instances used by delegated tasks and normal-mode subagents
- the runtime message bus used for agent-to-agent messages inside a run

## Terms

- Role: user-configured identity, instructions, tools, contracts, memory, and
  optional `bound_agent_id`.
- Agent runtime: execution backend selected for a role. Public API paths use
  `agent-runtimes`; Python modules use `agent_runtimes`.
- Runtime instance: one persisted execution carrier with role, workspace,
  conversation, lifecycle, status, and latest runtime prompt/tool snapshots.
- Subagent projection: UI and task terminology for work delegated to a
  non-root role. It is not a separate execution architecture.
- Runtime message bus: in-process A2A-style bus used by runtime instances in a
  run to publish and subscribe to structured role messages.

## Boundaries

The `agent_runtimes` package is the owner of runtime configuration, protocol
clients, runtime routing, runtime instance models, runtime probes, host-tool
bridging, native config generation, skill bridging, and runtime message bus
contracts.

Orchestration remains responsible for task planning, role selection, and task
lifecycle. It does not own protocol-specific execution branches.

Sessions and run services remain responsible for recovery, projection, and
public API response shaping. They should consume runtime instance records rather
than reconstructing protocol-specific state.

Interface layers continue to use `/api/*` only. CLI, SDK, and frontend code do
not access runtime repositories directly.

## Compatibility

Existing public APIs remain stable:

- `/api/system/configs/agent-runtimes`
- `/api/runs/{run_id}/a2a/*`
- role payload field `bound_agent_id`
- `spawn_subagent`
- `orch_dispatch_task`

The persisted role field remains `bound_agent_id` in this feature. New internal
code may use `runtime_id` names at runtime boundaries, but serialization keeps
the existing field until a separate migration is planned.

The legacy `relay_teams.agents.instances` module remains as a compatibility
import layer during migration. A2A bus, model, and tool implementations are
owned directly by `relay_teams.agent_runtimes`.
