# SP-1 Formal Task Spec Lifecycle Design And Implementation Spec

## Purpose

SP-1 turns task specifications from prompt-only context into durable lifecycle
artifacts. A task can now carry a normalized `TaskSpec`, bind that spec to a
versioned `task_spec_artifacts` row, derive work from an upstream spec-bearing
task, and persist a normalized Evidence Bundle after verification.

The implementation closes the loop described in issue #646:

- Persist versioned task spec artifacts and source-task links.
- Extend `TaskSpec` with REASONS Canvas fields, prompt/code sync state, and
  formal verification metadata.
- Normalize verification output into an Evidence Bundle.
- Expose spec artifacts and evidence bundles through `/api/*` task and session
  contracts.
- Reflect spec and evidence state in prompts, built-in roles, tools, docs, and
  UI projections.

## Goals

- Preserve the exact spec used to create or update a task so prompt
  reconstruction, audit, and downstream reviews do not depend on mutable prompt
  text.
- Let derived tasks inherit a single upstream spec automatically when the task
  graph makes the source unambiguous.
- Keep validation strict for explicit user/API mutations: a caller cannot bind a
  `spec_artifact_id` whose stored spec differs from the submitted `spec`, and a
  task cannot reuse an artifact row owned by another task.
- Make verification evidence machine-readable and tied back to acceptance
  criteria, evidence expectations, and formal verification checks.
- Surface the same spec/evidence metadata through backend API, session
  projection, frontend UI, roles, and orchestration tools.

## Non-Goals

- No semantic LLM evaluator is added in this change. Spec compliance is still
  enforced through structural checks, acceptance/evidence text coverage,
  command checks, required files, and optional formal replay/proof artifacts.
- No migration compatibility guarantee is required for existing development
  databases. Existing envelopes without spec artifact fields remain valid
  because the new fields are optional.
- No source-file or package-specific Qodana exclusions are introduced.

## Domain Contract

`TaskSpec` remains the canonical task specification model. Its base fields are:

- `summary`
- `requirements`
- `constraints`
- `acceptance_criteria`
- `out_of_scope`
- `verification_commands`
- `evidence_expectations`
- `strictness`

SP-1 adds the REASONS Canvas and lifecycle metadata:

- `entities`
- `approach`
- `structure`
- `operations`
- `norms`
- `safeguards`
- `prompt_artifact_version`
- `prompt_code_sync_status`
- `formal_verification`

The envelope fields that bind runtime tasks to spec artifacts are:

- `spec`: normalized `TaskSpec`.
- `spec_artifact_id`: current artifact row for this task.
- `spec_source_task_id`: upstream task whose spec this task derives from.
- `evidence_bundle`: latest normalized verification evidence.

## Storage Model

`task_spec_artifacts` stores immutable versions of task specs:

- `artifact_id`: generated as `spec-{uuid}`.
- `task_id`, `trace_id`, and `session_id`: bind the artifact to the owning task
  and run/session scope.
- `source_task_id`: optional upstream spec-bearing task.
- `spec_json`: serialized `TaskSpec`.
- `version`: increments per task whenever the effective spec changes.
- `created_at` and `updated_at`: UTC timestamps.

Task create/update flows call repository preparation before writing the task
row. That preparation either reuses a compatible same-task artifact, creates a
new artifact version, or rejects a mismatched artifact/spec pair. When a new
task is created from another task's `spec_artifact_id`, orchestration imports
the stored spec and writes a fresh artifact for the new task with
`source_task_id` pointing back to the upstream owner.

## Lifecycle Flow

1. A coordinator or API caller creates a task draft with `spec`,
   `spec_source_task_id`, or `spec_artifact_id`.
2. `TaskOrchestrationService` resolves the effective spec binding:
   - Direct artifact bindings load the stored spec; cross-task artifact
     references are treated as source imports during creation and rejected
     during updates.
   - Source task bindings copy the source task spec and artifact reference.
   - A provided source task must already have a bound spec.
   - A draft with exactly one spec-bearing dependency inherits that dependency's
     spec.
3. `TaskRepository` persists the task envelope and writes a versioned spec
   artifact when the effective spec is new for the task.
4. Prompt assembly injects the full task spec into worker context.
5. Crafter/Gater roles are instructed to reference acceptance criteria,
   evidence expectations, and Evidence Bundles in handoff/review paths.
6. Verification generates normalized evidence and writes it back to the task
   envelope after reloading the latest task row, so concurrent handoff or
   envelope updates are preserved.
7. APIs and session projections expose `spec_artifact_id`,
   `spec_source_task_id`, and `evidence_bundle` metadata.

## Verification Contract

`VerificationPlan` derives from the resolved task spec when the draft does not
provide an explicit plan:

- `acceptance_criteria` mirrors `TaskSpec.acceptance_criteria`.
- `evidence_expectations` mirrors `TaskSpec.evidence_expectations`.
- `command_checks` are parsed from `TaskSpec.verification_commands`.
- `strictness` mirrors `TaskSpec.strictness`.
- `formal_checks` includes the optional `TaskSpec.formal_verification` plan.

Verification also re-resolves the bound task spec at validation time. If a task
has `TaskSpec.formal_verification` on the envelope, or only a bound
`spec_artifact_id` that can be rehydrated, the formal plan is appended to the
effective verification plan unless an identical explicit formal check is already
present. This keeps existing and updated spec-bearing tasks inside the same
formal proof/replay path even when their stored `VerificationPlan` predates the
formal spec binding.

Verification output is normalized into `VerificationEvidenceBundle`:

- `items` capture task-result context, file, command, scoped event, and formal
  verification evidence.
- `acceptance_links` and `expectation_links` identify which concrete evidence
  items satisfy each spec obligation.
- `formal_verification_required` and `formal_verification_passed` summarize
  formal proof/replay status for strict tasks.

## API Contract

Task APIs remain under `/api/tasks`:

- `POST /api/tasks/runs/{run_id}` accepts drafts with spec bindings.
- `PATCH /api/tasks/{task_id}` can update `spec`, `spec_artifact_id`, and
  `spec_source_task_id` for created delegated tasks.
- `GET /api/tasks/{task_id}` returns the task envelope with spec/evidence
  metadata.
- `GET /api/tasks/{task_id}/spec-artifact` returns the latest bound artifact.
- `GET /api/tasks/{task_id}/evidence-bundle` returns the latest Evidence Bundle
  or `404` when verification has not generated evidence.

Session task projections include the same metadata so CLI, SDK, and frontend
consumers do not need repository access.

## Frontend Projection

The frontend keeps the public `/api/*` boundary. SP-1 UI work exposes:

- spec artifact/source metadata in delegated task history.
- evidence bundle status in task projections and subagent rail/timeline views.
- localized labels for the new spec/evidence fields.
- draft submission behavior that keeps send feedback fast while detached draft
  creation can continue in the background.

The implementation remains in `frontend/dist/`, split across the existing
application, component, core stream, layout, and i18n modules.

## Implementation Map

- `src/relay_teams/agents/tasks/models.py`: spec, formal verification, evidence,
  and artifact contracts.
- `src/relay_teams/agents/tasks/enums.py`: strictness, sync status, verification
  layer, formal language/tool, and evidence kind enums.
- `src/relay_teams/agents/tasks/task_repository.py`: artifact persistence,
  versioning, lookup, and task-envelope preparation.
- `src/relay_teams/agents/orchestration/task_orchestration_service.py`: spec
  binding resolution, inheritance, API projections, and service accessors.
- `src/relay_teams/agents/orchestration/verification.py`: formal checks,
  strictness checks, Evidence Bundle generation, and task write-back.
- `src/relay_teams/agents/orchestration/harnesses/prompt_harness.py`: prompt
  rendering for spec artifacts and evidence context.
- `src/relay_teams/interfaces/server/routers/tasks.py`: task spec/evidence API
  endpoints.
- `src/relay_teams/sessions/session_service.py`: session projection metadata.
- `src/relay_teams/tools/orchestration_tools/`: tool descriptions and update
  arguments for spec bindings.
- `src/relay_teams/builtin/roles/`: role guidance for spec/evidence handling.
- `frontend/dist/`: task history, subagent rail, timeline, stream, layout, and
  i18n projections.

## Validation Coverage

Unit coverage is focused around the new contract boundaries:

- task model normalization and enum behavior.
- repository artifact creation, versioning, lookup, update, delete, and
  mismatch validation.
- orchestration service spec inheritance and binding validation.
- verification Evidence Bundle generation and formal check behavior.
- task router success and validation paths.
- frontend projection tests for task history and subagent rail evidence display.

Full repository validation should run the standard pre-commit self-check:

```bash
uv run --extra dev ruff check --fix
uv run --extra dev ruff format --no-cache --force-exclude
uv run --extra dev basedpyright
uv run --extra dev pytest -q tests/unit_tests
uv run --extra dev pytest -q tests/integration_tests
```
