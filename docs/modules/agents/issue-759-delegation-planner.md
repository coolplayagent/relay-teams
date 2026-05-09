# Issue 759 Delegation Planner Tracking

## Goal

Issue #759 targets a bottleneck in orchestration mode where Coordinator/Crafter
can become the serial owner of long work. The fix is to use temporary roles and
parallel delegated lanes while preserving the existing runtime path.

## Decisions

- Add built-in `DelegationPlanner` as a subagent role, not a second coordinator.
- `DelegationPlanner` may inspect context and output a structured `DelegationPlan`, but
  it cannot create roles, create tasks, dispatch tasks, edit files, or run shell.
- Coordinator remains the only orchestrator. It validates the plan, creates or
  reuses temporary roles through `RuntimeRoleResolver`, and creates lane tasks
  through `TaskOrchestrationService`.
- All planning and lane execution must pass through `TaskExecutionService` so
  reminders, spec checkpoints, runtime snapshots, hooks, approvals, wakeups, and
  recovery semantics remain active.
- Fixed graph presets stay fixed. Automatic DelegationPlanner planning applies only to
  non-graph orchestration runs unless a graph explicitly includes a DelegationPlanner
  node.

## Acceptance Scenarios

- A simple request does not trigger DelegationPlanner and continues through the existing
  fast path.
- A long or spec-heavy request creates one `auto_plan` task, then creates bounded
  `auto_lane_*` delegated tasks.
- Planner output with unknown dependencies, duplicate lanes, or too many lanes is
  rejected and falls back to normal Coordinator behavior.
- Temporary roles proposed by DelegationPlanner are run-scoped, template-based when
  possible, and never receive coordinator-only tools.
- Existing runtime features remain observable on planner and lane tasks through
  task state, instance state, runtime prompt/tools snapshots, and event logs.

## Not In Scope

- Remote VM or branch isolation.
- Teammate-to-teammate mailbox routing.
- Replacing existing graph presets with generated graphs.
