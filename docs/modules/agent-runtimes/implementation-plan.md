# Agent Runtime Implementation Plan

## Phase 1: Specification and Documentation

- Add the `docs/modules/agent-runtimes/` specification set.
- Update project layout, API, and database docs to identify `agent_runtimes` as
  the runtime owner.
- Keep production code unchanged during this phase.

Acceptance:

- Another engineer can identify module ownership, data flow, compatibility
  rules, subagent consolidation strategy, and required tests from the docs.

## Phase 2: Naming and Compatibility

- Create `src/relay_teams/agent_runtimes/`.
- Move external runtime implementation modules into `agent_runtimes`.
- Move tests from `tests/unit_tests/external_agents/` to
  `tests/unit_tests/agent_runtimes/`.
- Delete the old `relay_teams.external_agents` compatibility package once
  production and tests import the new owner directly.

Acceptance:

- New code imports from `relay_teams.agent_runtimes`.
- No production or test imports remain from `relay_teams.external_agents`.
- Existing public APIs and persisted config shapes are unchanged.

## Phase 3: A2A Consolidation

- Move external A2A client logic to `agent_runtimes.clients.a2a`.
- Move internal A2A bus, models, and tools to `agent_runtimes.bus`,
  `agent_runtimes.bus_models`, and `agent_runtimes.tools`.
- Keep existing API routers and old orchestration imports as compatibility
  layers.

Acceptance:

- `/api/runs/{run_id}/a2a/*` returns the same shapes.
- A2A client tests cover Agent Card discovery, direct endpoint fallback,
  `message/send`, task polling, and terminal failure states.

## Phase 4: Runtime Instance Consolidation

- Move runtime instance models, ids, lifecycle enums, and repository ownership
  to `agent_runtimes.instances`.
- Retain `relay_teams.agent_runtimes.instances` as a compatibility layer.
- Make `create_subagent_instance()` a compatibility helper around runtime
  instance identity creation.
- Update orchestration, session, and background-task code to prefer
  `agent_runtimes.instances`.

Acceptance:

- `AgentRuntimeRecord` is the only core persisted runtime instance model.
- `SubAgentInstance` is not used as a new architecture boundary.
- Existing subagent projections, recovery snapshots, and background task rows
  remain compatible.

## Phase 5: Runtime Router

- Introduce `AgentRuntimeRouter`.
- Rename generic provider code to `AgentRuntimeProvider`.
- Route local, ACP, A2A, and CLI runtime turns through one lifecycle contract.
- Ensure `orch_dispatch_task` and `spawn_subagent` do not branch on runtime
  protocol.

Acceptance:

- Bound and unbound roles share the same runtime request/result lifecycle.
- ACP keeps session reuse.
- CLI and A2A keep their current execution semantics.
- Run events and message persistence are normalized across runtimes.

## Phase 6: Cleanup

- Remove protocol-specific naming from generic paths.
- Remove compatibility imports only in a later explicit cleanup task.
- Update docs after each phase when behavior or module ownership changes.

Acceptance:

- No new code imports implementation from `external_agents`.
- No new orchestration code imports A2A bus/tool implementation from
  `agents.orchestration`.
