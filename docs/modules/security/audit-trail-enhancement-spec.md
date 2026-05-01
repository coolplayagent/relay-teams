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
- tool runtime audit creation for file write, shell command, and Coordinator dispatch decision

End-to-end acceptance should start the FastAPI app, call `/api/audit` through a browser context, and confirm the endpoint returns the immutable audit page shape.
