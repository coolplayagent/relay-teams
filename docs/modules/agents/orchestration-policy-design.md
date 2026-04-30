# Orchestration Policy Design

## 1. Purpose

Configurable orchestration policy lets each orchestration preset, and optionally
each run request, constrain coordinator loops and delegated task concurrency
without changing role prompts or workflow graph definitions.

The feature has two limits:

- `max_orchestration_cycles`: maximum coordinator execution cycles after the
  optional first coordinator pass.
- `max_parallel_delegated_tasks`: maximum ready delegated task lanes that may
  run concurrently.

Both limits are runtime contracts, not prompt-only suggestions. The coordinator
may expose them to the model, but enforcement stays in the orchestration runtime.

## 2. Goals

- Preserve legacy behavior by default: eight orchestration cycles and four
  parallel delegated task lanes.
- Allow lightweight direct-answer presets by setting one or both limits to
  zero.
- Support preset-level policy through `/api/system/configs/orchestration`.
- Support per-run override through `/api/runs` without mutating saved presets.
- Snapshot the effective policy into the run topology so queued and recoverable
  runs resume with the same policy that was selected at creation time.
- Apply the same policy semantics in AI-mode orchestration, graph-mode
  orchestration, and explicit delegated task dispatch.
- Fail visibly instead of silently completing a run when policy limits block
  delegated work that already exists.

## 3. Non-Goals

- This feature does not add persisted policy versions or migration state.
- This feature does not replace graph `max_parallel_tasks`; graph concurrency is
  still useful as a graph-local upper bound.
- This feature does not allow role prompts to bypass runtime limits.
- This feature does not change task status values or the task repository schema.

## 4. Domain Contract

`src/relay_teams/agents/orchestration/policy_models.py` owns the policy model.

`OrchestrationPolicy` is a frozen Pydantic v2 model with `extra="forbid"`:

- `max_orchestration_cycles`: default `8`, valid range `0..64`.
- `max_parallel_delegated_tasks`: default `4`, valid range `0..16`.

The model is embedded in:

- `OrchestrationPreset.policy`
- `RunTopologySnapshot.orchestration_policy`
- optional `IntentInput.orchestration_policy`

The default values intentionally match the previous hard-coded coordinator
limits. A missing policy in saved config resolves to `OrchestrationPolicy()`
through normal Pydantic defaults.

## 5. API And Settings Flow

`GET /api/system/configs/orchestration` returns each preset with its `policy`
object. `PUT /api/system/configs/orchestration` validates the same object and
persists it through the orchestration settings config manager.

Validation rules:

- Unknown policy fields are rejected.
- `max_orchestration_cycles` must be `0..64`.
- `max_parallel_delegated_tasks` must be `0..16`.
- Role validation remains unchanged: preset roles must be known non-reserved
  roles, and graph nodes must reference roles listed by the preset.

`POST /api/runs` accepts optional `orchestration_policy`. When present, that
policy overrides the selected preset policy for that run only. The SDK forwards
the same payload field without adding a separate client-side schema.

The settings UI exposes both preset policy fields next to the preset definition.
Frontend parsing normalizes blank or invalid edits back to the default policy
values before submitting the settings payload.

## 6. Topology Resolution

`OrchestrationSettingsService.resolve_run_topology(...)` is the single backend
entry point for turning session settings plus an optional run override into a
`RunTopologySnapshot`.

Resolution rules:

- Normal sessions receive the run override when one is provided; otherwise they
  receive the default policy. This keeps the topology shape stable across modes.
- Orchestration sessions resolve the selected preset and use the run override
  when present; otherwise they use the preset policy.
- The selected policy is copied into `RunTopologySnapshot.orchestration_policy`.
- Run service preparation persists the topology snapshot with the run intent so
  queued, resumed, and recoverable runs do not observe later preset edits.

## 7. Prompt Visibility

`RuntimePromptBuilder` appends a compact "Orchestration Policy" section whenever
the prompt input includes a topology. The section lists the resolved cycle and
parallel delegation limits.

This is advisory context for the coordinator. It does not replace the runtime
checks described below.

## 8. Runtime Enforcement

### 8.1 AI Mode

AI mode uses `RunTopologySnapshot.orchestration_policy` as the loop contract.

Flow:

1. The optional first coordinator pass may create delegated tasks.
2. If `max_orchestration_cycles` is `0`, the coordinator checks whether any
   delegated tasks are already `CREATED` or `ASSIGNED`.
3. If delegated work exists, the root task is marked failed and the run returns
   an assistant error with code `orchestration_cycles_exhausted`.
4. Otherwise the coordinator executes at most `max_orchestration_cycles` cycles.
5. Each cycle runs ready delegated lanes through `_run_pending_delegated_tasks`
   with `max_parallel_tasks=policy.max_parallel_delegated_tasks`.
6. If ready lanes exist while `max_parallel_delegated_tasks` is `0`, delegated
   execution is blocked, the root task is marked failed, and the run returns an
   assistant error with code `delegated_task_execution_disabled`.

This prevents a blocked policy from looking like a normal "no pending subtasks"
completion.

### 8.2 Graph Mode

Graph mode combines graph-local and policy-local concurrency:

```text
resolved_max_parallel_tasks =
  min(graph.max_parallel_tasks, policy.max_parallel_delegated_tasks)
```

The graph still controls node dependency order and its own upper bound, while
the policy can reduce or disable delegated execution for the whole run. If ready
graph work exists while the resolved limit is zero, graph execution returns the
same `delegated_task_execution_disabled` assistant error and fails the root task.

### 8.3 Explicit Delegated Dispatch

`TaskOrchestrationService` uses the run intent topology to size the per-run
execution semaphore for explicit delegated task dispatch. When the snapshotted
policy has `max_parallel_delegated_tasks < 1`, explicit dispatch raises a
validation error instead of creating an unbounded or inconsistent execution path.

If the run intent or topology is unavailable, the service falls back to the
constructor default to preserve compatibility with older tests and narrow
runtime paths that do not have a run intent repository.

## 9. Failure And Observability

Policy blocks are logged with structured events:

- `coord.cycle.blocked`
- `coord.delegated_tasks.blocked`

The root task must reach a terminal failed state before the coordinator returns
an assistant error for policy blocks. This keeps run state, task state, and
resume/monitoring behavior consistent.

The assistant error payload carries:

- `completion_reason = assistant_error`
- `error_code = orchestration_cycles_exhausted` or
  `delegated_task_execution_disabled`
- a concrete error message explaining the blocked limit

## 10. Testing Requirements

Coverage should stay aligned with the runtime boundary touched by each behavior:

- Policy model defaults, bounds, and prompt rendering.
- Settings service preset policy resolution and run override resolution.
- System config API reads and writes for preset policy.
- Run creation API and SDK forwarding for per-run policy overrides.
- Runtime prompt output containing policy values.
- AI mode zero-cycle and zero-parallel blocked paths.
- Graph mode policy concurrency interaction.
- Explicit delegated dispatch semaphore sizing and disabled dispatch behavior.
- Frontend settings form parsing, rendering, and payload submission.

Regression tests must assert that policy-blocked paths fail the root task before
returning an assistant error.
