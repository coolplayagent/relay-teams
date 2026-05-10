# DelegationPlanner Dynamic DAG Spec

## Purpose

`DelegationPlanner` removes the serial Coordinator/Crafter bottleneck for long
or ambiguous orchestration work. It produces a bounded parallel lane plan that
Coordinator converts into a task-backed DAG with static or run-scoped temporary
roles.

The first principle is that long work should be decomposed into explicit,
recoverable task nodes before execution. `DelegationPlanner` is planning-only;
Coordinator remains the sole orchestrator, and all planner and lane execution
continues through the existing task runtime.

## Decisions

- Add built-in `DelegationPlanner` as a subagent role, not a second coordinator.
- `DelegationPlanner` may inspect context and output a structured `DelegationPlan`, but
  it cannot create roles, create tasks, dispatch tasks, edit files, or run shell.
- Coordinator remains the only orchestrator. It validates the plan, creates or
  reuses temporary roles through `RuntimeRoleResolver`, and creates lane tasks
  through `TaskOrchestrationService`.
- Planner lanes are DAG nodes. `lane_id` values become `auto_lane_*`
  `orchestration_node_id` values, and `depends_on_lane_ids` become dependency
  edges resolved into `depends_on_task_ids`.
- All planning and lane execution must pass through `TaskExecutionService` so
  reminders, spec checkpoints, runtime snapshots, hooks, approvals, wakeups, and
  recovery semantics remain active.
- Fixed graph presets stay fixed. Automatic DelegationPlanner planning applies only to
  non-graph orchestration runs unless a graph explicitly includes a DelegationPlanner
  node.
- The default orchestration preset should keep automatic planning enabled and
  expose `DelegationPlanner` in `role_ids` so long tasks can use dynamic roles
  and concurrent lanes without switching presets.

## Runtime Flow

1. Coordinator evaluates the root task and the resolved `OrchestrationPolicy`.
2. If `auto_plan_long_tasks` is enabled and the root task is long, spec-heavy,
   or likely to benefit from parallel lanes, Coordinator creates and dispatches
   one `auto_plan` task to `DelegationPlanner`.
3. `DelegationPlanner` returns exactly one JSON plan with
   `should_decompose`, `rationale`, and `lanes`.
4. Coordinator validates the JSON shape, lane count, unique lane ids, known
   dependencies, acyclicity, allowed static roles, and temporary role limits.
5. Coordinator creates or reuses temporary roles only when the plan requires
   them, preferring `template_role_id` to inherit the closest existing role.
6. Coordinator creates lane tasks through `TaskOrchestrationService`; ready
   nodes are scheduled by dependency order and bounded by
   `max_parallel_delegated_tasks`.
7. If planning is disabled, unnecessary, invalid, or unavailable, Coordinator
   falls back to its normal orchestration path instead of failing the run only
   because planning could not be used.

## Acceptance Scenarios

- A simple request does not trigger DelegationPlanner and continues through the existing
  fast path.
- A long or spec-heavy request creates one `auto_plan` task, then creates bounded
  `auto_lane_*` delegated tasks.
- Lane dependencies execute as a DAG: independent lanes can run concurrently,
  and dependent lanes wait for upstream task completion.
- Planner output with unknown dependencies, duplicate lanes, or too many lanes is
  rejected and falls back to normal Coordinator behavior.
- Temporary roles proposed by DelegationPlanner are run-scoped, template-based when
  possible, and never receive coordinator-only tools.
- Existing runtime features remain observable on planner and lane tasks through
  task state, instance state, runtime prompt/tools snapshots, and event logs.
- Fixed graph presets do not auto-inject `DelegationPlanner`; a preset that
  needs planning inside a fixed DAG must declare a planner node explicitly.

## Not In Scope

- Remote VM or branch isolation.
- Teammate-to-teammate mailbox routing.
- Replacing existing graph presets with generated graphs.
