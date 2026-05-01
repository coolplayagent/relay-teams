# SP-3 Spec Checkpoint Refresh Spec

## Status

Implemented for the 2026-05-01 PR scope. This document records the feature
design and implementation contract for SP-3, the Spec-Checkpoint
anti-degradation mechanism.

## Context

Long non-coordinator task runs can drift away from the original task contract
after many tool calls, active history messages, or a large prompt history. Full
conversation compaction preserves useful progress, but it can still make the
current task specification compete with older conversational context. SP-3 adds a
deterministic refresh layer: when a task has a persisted `TaskSpec`, the runtime
can persist a bounded internal system prompt that restates the current spec
before the next model request.

The refresh is not a user-visible answer, not a new public API command, and not a
replacement for compaction. It is a durable runtime marker that keeps requirements,
constraints, acceptance criteria, verification commands, and expected evidence in
the prompt after long-running work.

## Goals

- Keep non-coordinator agents aligned to persisted task specs during long runs.
- Trigger refreshes from explicit lifecycle thresholds for completed tool calls,
  active history messages, and estimated history tokens.
- Persist refresh prompts as internal system messages so retries and recovery see
  the same context boundary.
- Emit a durable `spec_checkpoint_applied` run event for timeline and recovery
  observability.
- Preserve model fallback eligibility when the only new persisted state is the
  internal checkpoint prompt.
- Avoid restarts once a final answer is already present at the safe boundary.

## Non-Goals

- Changing coordinator orchestration prompts.
- Replacing full conversation compaction or hidden-history markers.
- Introducing new `/api/*` routes, CLI commands, or database tables.
- Accepting unknown lifecycle fields beyond the explicit task model contract.
- Injecting checkpoints for tasks without a persisted `TaskSpec`.

## Feature Design

`TaskLifecyclePolicy` owns the feature configuration through
`SpecCheckpointPolicy`. The default policy is enabled and refreshes after any of
these thresholds is crossed since the previous spec checkpoint:

- `refresh_interval_tool_calls`: 12 completed tool calls or retry prompts with a
  tool name.
- `refresh_interval_messages`: 48 active history messages.
- `refresh_interval_history_tokens`: 8000 estimated history tokens.

The rendered checkpoint is intentionally bounded by `max_summary_chars`. It
includes a marker comment, task and role identity, checkpoint sequence, trigger
reason, refresh counters, and the populated sections of the persisted
`TaskSpec`. The marker lets later decisions count from the latest checkpoint
instead of repeatedly triggering from the beginning of the conversation.

Coordinator roles are exempt because their prompt and task responsibilities are
orchestration-level contracts. SP-3 is scoped to worker/subagent execution where
the persisted task spec is the strongest local contract.

## Runtime Contract

The runtime may apply a spec checkpoint only at a safe model boundary:

1. Resolve the current task record from `request.task_id`.
2. Skip when the role registry marks the role as coordinator.
3. Skip when the task has no meaningful spec content or the policy is disabled.
4. Count completed tool calls, messages, and estimated tokens since the latest
   checkpoint marker for the same task.
5. Persist the rendered checkpoint with
   `append_system_prompt_if_missing_async(...)`.
6. Emit `RunEventType.SPEC_CHECKPOINT_APPLIED` with sequence, reason, and
   counter payload.
7. Rebuild the agent iteration context so the next model request sees the
   checkpoint prompt.

The checkpoint prompt is internal state. It must not mark the current attempt as
user-visible message committed by itself, because that would incorrectly block
provider fallback after retry exhaustion. Actual assistant/tool messages still
set the normal commit flags.

When a boundary batch already contains a final text response, checkpoint
refreshes are skipped for that boundary. User-visible completion has priority
over optional spec refresh so the runtime does not add an extra model turn,
duplicate output, or change an already-finished answer.

## Implementation Spec

### Task Models

`src/relay_teams/agents/tasks/models.py` defines:

- `SpecCheckpointPolicy`
- `TaskLifecyclePolicy.spec_checkpoint`

The policy uses strict Pydantic v2 models and normalizes `None` to the default
policy for persisted lifecycle payload tolerance.

### Checkpoint Decision And Rendering

`src/relay_teams/agents/execution/spec_checkpoint.py` owns deterministic decision
logic:

- `build_spec_checkpoint_decision(...)`
- `spec_checkpoint_reason(...)`
- `render_spec_checkpoint(...)`
- `latest_spec_checkpoint_position(...)`
- `is_spec_checkpoint_content(...)`
- `count_completed_tool_calls(...)`

The module does not access repositories. It receives a `TaskEnvelope`, role id,
and history, then returns a frozen `SpecCheckpointDecision`.

### Session Runtime Integration

`src/relay_teams/agents/execution/session_runtime.py` adds
`apply_spec_checkpoint_if_due()` inside the LLM generation loop. It runs before a
new model request and after safe-boundary processing when no interrupt or queued
injection has already restarted the turn.

The integration preserves these ordering rules:

- Interrupt injections are checked first.
- Queued user/runtime injections take precedence at model boundaries.
- Spec checkpoint injection happens only when no final answer is ready.
- Tool input validation restarts still rebuild context through the existing
  shared path.
- AutoHarness dirty-tool rebuilds remain separate from checkpoint refresh.

### Compaction Integration

`src/relay_teams/agents/execution/conversation_compaction.py` renders preserved
system prompts in compaction summaries, including Task Spec and Spec Checkpoint
messages. This keeps the spec contract visible even after older transcript
sections are summarized.

### Observability And API Docs

`RunEventType.SPEC_CHECKPOINT_APPLIED` records checkpoint application in the run
stream. `docs/core/api-design.md` documents the event payload, and
`docs/core/database-schema.md` documents the persisted lifecycle policy and event
storage semantics.

## Validation Matrix

| Area | Coverage |
| --- | --- |
| Decision thresholds | `tests/unit_tests/agents/execution/test_spec_checkpoint.py` covers tool-call, message, token, marker, clipping, and task-spec filtering behavior. |
| Runtime repository lookup | `tests/unit_tests/agents/execution/test_session_runtime.py` covers task lookup, coordinator exemption, missing task records, event payloads, and safe-boundary behavior. |
| Fallback eligibility | `tests/unit_tests/agents/execution/test_session_runtime.py` verifies checkpoint persistence alone does not block provider fallback exhaustion handling. |
| Final answer priority | `tests/unit_tests/agents/execution/test_session_runtime.py` verifies a final answer in the current boundary batch does not trigger a spec-checkpoint restart. |
| Compaction preservation | `tests/unit_tests/agents/execution/test_conversation_compaction.py` covers preservation of spec checkpoint system messages in rendered transcript summaries. |
| Task model contract | `tests/unit_tests/agents/tasks/test_task_models.py` covers lifecycle defaults and explicit checkpoint policy parsing. |

## Operational Invariants

- Checkpoints are only persisted for tasks with meaningful `TaskSpec` content.
- Checkpoints are bounded, deterministic, and marked with task id plus sequence.
- Runtime checkpoint application is idempotent through the message repository's
  missing-prompt append operation.
- Checkpoint event payloads include enough context to diagnose trigger source.
- No public API or database schema migration is required beyond existing JSON
  lifecycle/event payload fields.
