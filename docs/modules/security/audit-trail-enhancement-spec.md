# SG-3 Audit Trail Enhancement Spec

## Goal

Implement SG-3 from `docs/research/lessons-learned-2026.md`: security-focused audit tracking for file writes, shell commands, and Coordinator channel-selection decisions, with an external read-only `/api/audit` query endpoint.

## Scope

- Add a dedicated `relay_teams.audit` module.
- Persist audit events in `security_audit_events`, separate from the run `events` stream.
- Record a `security.audit` trace span for each audit row.
- Capture these runtime actions:
  - workspace file writes from `write`, `write_tmp`, `edit`, and `notebook_edit`
  - shell tool command executions and denied/failed shell attempts that reach tool runtime handling
  - Coordinator `orch_dispatch_task` task-to-role decisions
- Expose read-only filtering through `GET /api/audit`.

## Event Contract

Common fields:
- `audit_event_id`
- `event_type`
- `trace_id`, `run_id`, `session_id`, `task_id`, `instance_id`, `role_id`, `tool_call_id`
- `span_id`, `parent_span_id`
- `action`, `target`, `outcome`
- `metadata`
- `occurred_at`, `created_at`

Event-specific fields:
- `file_write`: `target` is the logical path, `content_digest` is `sha256:<hex>`, and `content_size_bytes` is the final file byte size.
- `shell_command`: `command` contains the command text, and metadata may include `workdir`, `background`, `tty`, `timeout_ms`, `status`, and `exit_code`.
- `coordinator_decision`: `target` is `task:<task_id>->role:<role_id>`, and `decision_reason` is the dispatch prompt or the default dispatch reason.

Raw file content is never stored in audit rows. Dispatch reasons are capped at 4,000 characters and accompanied by a digest/length in metadata.

## Runtime Integration

Audit recording is centralized in `relay_teams.tools.runtime.execution` after hook rewriting has produced the effective tool input. The runtime records successful audit events after the tool result envelope is persisted so persistence failures do not create contradictory completed and failed audit rows. Audit persistence failures are logged without failing the user-visible tool result.

The audit service is carried on `ToolDeps` as an optional backend dependency. Agent tools do not expose any audit mutation function, and the repository has no update/delete API.

## Implementation Design

`relay_teams.audit` owns the event models, SQLite repository, and service wrapper. The repository stores audit rows as immutable append-only records and normalizes all persisted timestamps to UTC ISO-8601 text before SQL comparison. Dirty persisted JSON and integer values are converted through typed helpers so the read path remains explicit without exposing loose dictionaries.

The tool runtime builds audit events from the effective tool input and the persisted result envelope:
- file write events derive logical paths from the file-oriented tool input or result metadata, then calculate a final `sha256:<hex>` digest and byte size when the target exists;
- shell command events capture the command and selected execution metadata without storing stdout/stderr bodies;
- Coordinator dispatch events capture the selected task/role target and a bounded dispatch reason.

Successful audit rows are written only after tool result persistence succeeds. If result persistence fails, the failure path records one failed audit row for the same action. If audit persistence itself fails, the runtime logs `security.audit.record_failed` and preserves the user-visible tool outcome.

## API

`GET /api/audit` accepts exact filters for event type and trace/session/run/task/role identifiers, cursor pagination with `after_id`, time filtering with `since`/`until`, and `limit` up to 500. Persisted timestamps and query timestamp offsets are normalized to UTC before range comparison.

The response is:

```json
{
  "items": [],
  "next_after_id": null
}
```

## Verification

Unit coverage includes:
- repository append/filter/pagination
- API route filtering and validation
- UTC time filtering, dirty persisted value conversion, and missing-row behavior
- tool runtime audit creation for file write variants, shell command, Coordinator dispatch decision, persistence-failure ordering, and audit-write failure isolation

End-to-end acceptance should start the FastAPI app, call `/api/audit` through a browser context, and confirm the endpoint returns the immutable audit page shape.
