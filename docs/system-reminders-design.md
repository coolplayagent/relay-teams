# System Reminders Design

## 1. Goal

Relay Teams needs built-in runtime reminders that turn important runtime state into
deterministic guidance for the active agent loop.

This feature covers:

- tool failure reminders
- consecutive read-only tool reminders
- incomplete todo completion guards
- context compaction reminders

The goal is not to add another user-configurable extension system. Runtime reminders
are product-owned behavior that should work without user hook configuration.

## 2. Boundary With Runtime Hooks

Runtime hooks and system reminders intentionally overlap at lifecycle boundaries, but
they own different concerns.

Hooks are external extension points:

- configured by user, project, role, or skill sources
- implemented through command, HTTP, prompt, or agent handlers
- allowed to fail, timeout, or be absent without disabling core behavior
- useful for custom governance and integrations

System reminders are built-in runtime policy:

- enabled by default
- deterministic and covered by unit tests
- stateful across a run for cooldowns and counters
- allowed to block task completion before terminal state is written

Both systems share the same message injection primitive. Hooks use it to enqueue
`additional_context` or `deferred_action`; reminders use it to inject
`<system-reminder>` messages.

## 3. Runtime Flow

The reminder pipeline is:

```text
runtime boundary
-> typed observation
-> SystemReminderPolicy
-> ReminderStateRepository
-> SystemReminderRenderer
-> SystemInjectionSink
-> active LLM loop or persisted retry history
```

`relay_teams.reminders` owns policy, state, and rendering. Execution, orchestration,
and prompt modules only report observations and consume decisions.

`sessions/runs/system_injection.py` owns the shared delivery boundary:

- `enqueue_only` wakes an already-active loop at the next safe boundary.
- `append_and_enqueue` also persists the reminder into conversation history before
  retrying a completion attempt.

## 4. V1 Policies

Tool failure:

- triggered after a failed tool result
- deduped per run, tool name, and error type with a cooldown
- reminds the agent to inspect the error and avoid repeating the same call unchanged

Read-only streak:

- triggered after five consecutive known read-only tools
- unknown tools are neutral and do not count
- `shell` is not considered read-only in v1

Incomplete todos:

- evaluated only for root task completion attempts
- pending or in-progress todos block successful completion
- the reminder is appended to history and the agent gets another turn
- after three reminder retries, the task returns an assistant error instead of being
  marked complete

Context pressure:

- triggered after compaction is applied
- reminds the agent that old tool output may no longer be available verbatim

## 5. Public Interfaces

No new `/api/*`, CLI, SDK, or database schema is introduced.

Reminder state is stored through `SharedStateRepository` with run-scoped keys under
the session scope. The schema is private to `relay_teams.reminders`.

## 6. Testing

The feature is covered by focused unit tests for:

- reminder rendering
- policy decisions and cooldown behavior
- persisted reminder state recovery
- service-to-injection behavior
- shared system injection sink behavior
- root task retry when incomplete todos block completion
