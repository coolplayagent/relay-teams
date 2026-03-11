# Subagent Reflection and Layered Memory Design

## 1. Problem Statement

Subagents already have isolated workspace, conversation, and memory scope bindings, but they do not yet keep reusable file-based memory across re-entry. A single rolling `MEMORY.md` is also not enough because short-lived daily observations and long-lived stable learnings have different lifecycle and prompt-value characteristics.

## 2. Design Goals

- Only non-coordinator subagents participate in reflection memory.
- Successful subagent completion must enqueue reflection asynchronously and must not block the main task result.
- Daily memory stays instance-scoped.
- Long-term memory stays `session + role` scoped so new instances of the same role can inherit stable learnings.
- Runtime prompt injection must be bounded and deterministic.
- Reflection failure must never fail the original task.

## 3. Memory Topology

### Instance-scoped daily memory

Paths under the instance workspace:

- `.agent_teams/workspaces/{workspace_id}/memory/daily/raw/YYYY-MM-DD.md`
- `.agent_teams/workspaces/{workspace_id}/memory/daily/digest/YYYY-MM-DD.md`

`daily raw` stores structured reflection output for the day:
- `Session Facts`
- `Observations`
- `Decisions`
- `Failures And Recoveries`
- `Open Threads`
- `Candidate Long-Term Learnings`

`daily digest` stores only high-signal items intended for runtime injection.

### Session-role long-term memory

Path outside instance workspace:

- `.agent_teams/memory/session_roles/{session_id}/{role_id}/MEMORY.md`

This file stores only stable, reusable memory:
- `Role Identity`
- `Stable User / Project Preferences`
- `Proven Strategies`
- `Reusable Constraints And Boundaries`
- `Important Ongoing Tendencies`

## 4. Reflection Jobs

Reflection is implemented as a dedicated module with a persistent SQLite-backed job queue.

Job types:
- `daily_reflection`
- `long_term_consolidation`

Lifecycle:
- subagent success -> enqueue `daily_reflection`
- worker claims queued job and updates daily raw + digest
- if no same-day consolidation job exists for the same `session + role`, enqueue `long_term_consolidation`
- consolidation updates role-level `MEMORY.md`

## 5. Runtime Injection

On the next non-coordinator subagent run:
- read role-level long-term memory
- read today's daily digest from the current instance workspace
- inject both into the system prompt under `## Workspace Memory`

Injection order:
1. long-term memory
2. today's digest

The runtime never injects daily raw and never injects historical daily files.

## 6. Budgeting Rules

Reflection config controls prompt budget:
- `max_injected_memory_chars`
- `max_long_term_injection_chars`
- `max_daily_digest_injection_chars`

Trimming order:
- trim daily digest first
- trim long-term memory second

## 7. Failure Handling

- Reflection runs on a background worker started by the server container.
- Worker startup resets leftover `running` jobs back to `queued`.
- Failed jobs stay in `failed` state and can be retried from CLI/API.
- Reflection failure only affects the reflection job and is logged separately.

## 8. Cleanup Rules

When a session is deleted:
- delete reflection jobs by `session_id`
- delete long-term memory files for all roles in the session
- delete instance daily memory indirectly when the corresponding workspace is removed

## 9. Current Implementation Notes

The current implementation uses a dedicated reflection model client with its own `model_profile` from `.agent_teams/reflection.json`. It does not reuse role tools, MCP servers, or skills, and it does not write reflection traffic into the main session message history.
