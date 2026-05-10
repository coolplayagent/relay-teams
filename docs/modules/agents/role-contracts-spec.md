# SP-2 Role Contracts Spec

## 1. Purpose

SP-2 adds runtime role contracts so each role can declare the task inputs it
requires, the result evidence it must produce, and the capability invariants
that must remain true for safe dispatch and verification.

The contract is a typed runtime agreement. It is loaded from role front matter,
validated with the role definition, rendered into prompts, enforced before
delegated execution, and converted into verification checks after execution.

## 2. Goals

- Let built-in, app, and plugin roles declare preconditions, postconditions, and
  capability invariants without custom orchestration code.
- Keep explicit user role mutations strict: unknown tools, MCP servers, skills,
  and contract capability references are rejected.
- Keep runtime reload paths tolerant of dirty persisted role state: unknown
  top-level and contract capability references are filtered with warnings.
- Surface contract context in prompts and timeline/UI state without changing the
  public task execution transport.
- Enforce contracts from the shared orchestration path used by coordinator and
  explicit delegated task dispatch.

## 3. Non-Goals

- This feature does not add database tables for role contracts. Contracts remain
  part of the existing role definition document/config payload.
- This feature does not make contract checks a replacement for normal
  verification commands or human gates.
- This feature does not allow saved dirty capability references to bypass strict
  validation endpoints when the user explicitly edits or validates a role.

## 4. Domain Contract

`src/relay_teams/roles/role_contracts.py` owns the public role contract models.
All contract structures are Pydantic v2 models with `extra="forbid"`.

`RoleContract` contains:

- `version`: string, default `"1"`.
- `preconditions`: ordered `RoleContractPrecondition` entries.
- `postconditions`: ordered `RoleContractPostcondition` entries.
- `invariants`: ordered `RoleContractInvariant` entries.

Supported preconditions:

- `task_has_spec`
- `task_has_acceptance_criteria`
- `dependencies_completed`
- `dependency_role_completed`

Supported postconditions:

- `verification_commands_configured`
- `result_mentions_acceptance_criteria`
- `result_mentions_evidence_expectations`
- `handoff_present`

Supported invariants:

- `must_have_tools`
- `must_not_have_tools`
- `must_have_mcp_servers`
- `must_not_have_mcp_servers`
- `must_have_skills`
- `must_not_have_skills`

## 5. Role Loading And Validation

`RoleLoader` parses the optional `contract` front matter block into
`RoleDefinition.contract`.

`RoleSettingsService` applies two validation modes:

- Strict mode is used for explicit create/edit/validate flows. Unknown
  top-level capabilities and unknown contract invariant references raise
  validation errors.
- Non-strict mode is used while reading already-saved app/builtin/plugin roles
  during startup, listing, save reloads, and delete reloads. Unknown top-level
  capability references are filtered. Contract invariant references are filtered
  through the same registries and the sanitized contract is copied back into the
  loaded role definition.

This preserves the repository rule that persisted capability references may
contain dirty data while explicit user mutations remain strict.

## 6. Prompt And Dispatch Flow

`build_role_contract_prompt()` renders non-empty contracts into a compact
"Role Contract" prompt section. `system_prompts.py` includes that section with
the role prompt so models can see the same agreement the runtime enforces.

Before delegated dispatch, orchestration calls
`role_contract_precondition_failures(...)` with the target role, task envelope,
and dependency records. Dispatch is rejected or the task is failed when
preconditions or invariants are not satisfied.

## 7. Verification Flow

`role_contract_verification_checks(...)` turns postconditions and capability
invariants into `VerificationCheckResult` entries in the `CONTRACT` layer.
These checks augment existing command, evidence, and human verification layers.

Result-text postconditions check the completed result against configured
acceptance criteria or evidence expectations. Handoff and verification-command
postconditions check the task envelope itself.

## 8. API And UI Surface

Role API payloads include `contract` through the existing role definition and
document draft models. The sessions API streams task contract and verification
state without a separate transport path.

The settings UI keeps role contract data with the role document payload, and
the prompt/timeline UI can display contract-driven task state from the existing
run event stream.

## 9. Built-In Role Contracts

Built-in role documents can declare contracts in front matter. The SP-2 built-in
contracts focus on:

- Designer tasks requiring task specs and acceptance criteria.
- Crafter tasks requiring specs and evidence-aware results.
- Explorer tasks requiring dependency or spec context where applicable.
- Gater tasks requiring verification evidence and handoff context.

## 10. Testing Requirements

Coverage should stay aligned with the runtime boundary touched by each behavior:

- Role loader parses contract front matter into typed models.
- Role settings strict validation rejects unknown contract capability refs.
- Non-strict role reload filters dirty top-level and contract capability refs.
- Prompt assembly includes role contract sections.
- Coordinator and explicit delegated dispatch enforce preconditions.
- Verification returns contract-layer checks for postconditions and invariants.
- Frontend role settings and timeline tests cover contract-related display and
  stream behavior.
