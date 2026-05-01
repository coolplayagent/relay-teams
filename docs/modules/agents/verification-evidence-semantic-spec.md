# Verification Evidence and Semantic Checks Spec

## 1. Goal

FE-5 upgrades task verification from result-text matching to evidence-backed verification.

The runtime should produce a `VerificationReport` that explains:

- which structure, behavior, evidence, and semantic checks ran
- which concrete evidence items were collected
- which evidence items support each acceptance criterion or expected evidence statement
- which semantic verdict was reached for each acceptance criterion

This makes task verification auditable while keeping the public task contract explicit and serializable.

## 2. Scope

This phase covers the in-process verification engine used by `verify_task(...)`.

Included behavior:

- normalize task result text, required-file checks, command checks, scoped tool events, and gate findings into a `VerificationEvidenceBundle`
- classify command output as test, lint, diff, formal-proof, or generic command evidence
- parse lightweight metrics from command output, such as passed tests, lint errors, changed files, and formal proof completion
- link acceptance criteria and evidence expectations to concrete evidence items
- add evidence and semantic check layers to the report
- support an optional semantic evaluator callback with rule fallback

Out of scope for this phase:

- wiring an LLM semantic evaluator into provider runtime configuration
- changing database schema or HTTP API contracts
- making task-result self-reports sufficient evidence
- accepting unscoped run events as task evidence

## 3. Runtime Contract

### 3.1 Inputs

`VerificationPlan` remains the input contract. Existing fields continue to drive verification:

- `checklist` performs structural result checks such as `non_empty_response`
- `required_files` checks workspace artifacts
- `command_checks` runs approved shell commands with bounded output capture
- `acceptance_criteria` defines the semantic target statements
- `evidence_expectations` defines expected proof artifacts or command output

`verify_task(...)` also accepts an optional `semantic_evaluator` callback. The callback receives a `SemanticEvaluationRequest` with task id, criterion, result excerpt, and linked evidence items.

### 3.2 Outputs

`VerificationReport` now carries:

- `checks`: structure, behavior, evidence, and semantic check results
- `evidence_bundle`: normalized evidence items plus acceptance and expectation links
- `semantic_results`: per-criterion semantic verdicts with confidence, reason, evaluator name, and evidence ids

The report still determines pass/fail from failed checks. A task only passes when all required checks, evidence links, and semantic acceptance checks pass.

## 4. Evidence Model

`VerificationEvidenceItem` is the normalized evidence unit.

Supported kinds:

- `task_result`: task result text, retained for context but not linkable evidence
- `required_file`: required artifact exists and is a regular file
- `command`: generic command output
- `test_result`: command output with test markers
- `lint_result`: command output with lint/type-check markers
- `diff_summary`: command output with diff summary markers
- `formal_proof`: command output with formal verification markers
- `tool_call`: scoped tool invocation event
- `tool_result`: scoped tool result event
- `gate_finding`: scoped gate, finding, or timeout event; gate and finding events are successful evidence unless they explicitly carry `passed=false`, while timeout events are failed evidence

`VerificationEvidenceLink` connects one acceptance criterion or evidence expectation to matching evidence item ids. A link is satisfied only when at least one eligible evidence item matches the target text.

Eligibility rules:

- failed evidence cannot support a link
- skipped verification checks cannot support a link
- `task_result` cannot support acceptance or evidence links
- `tool_call` cannot support acceptance criteria because a call alone does not prove completion
- run-event evidence must have `task_id` equal to the verified task id

## 5. Semantic Checks

Semantic checks run after evidence linking.

The rule evaluator accepts an acceptance criterion only when linked evidence includes at least one strong evidence item:

- required file
- command result
- test result
- lint result
- diff summary
- formal proof
- tool result
- gate finding

Self-reported result text and tool-call-only evidence are treated as weak evidence and fail semantic acceptance. This prevents a task from passing by repeating the criterion in its final answer without independent proof.

If an external semantic evaluator is provided, its result is used after normalizing the evaluator name. If it raises, `verify_task(...)` logs `verification.semantic_evaluator_failed` and uses the rule verdict instead.

## 6. Implementation

Primary source files:

- `src/relay_teams/agents/tasks/enums.py`
- `src/relay_teams/agents/tasks/models.py`
- `src/relay_teams/agents/tasks/__init__.py`
- `src/relay_teams/agents/orchestration/verification.py`

The orchestration flow is:

1. Build baseline checklist, required-file, and command checks.
2. Convert checks into normalized evidence items.
3. Collect task-scoped run events from `EventLog`.
4. Link evidence items to acceptance criteria and evidence expectations.
5. Add evidence coverage checks.
6. Run rule or external semantic evaluation.
7. Add semantic checks and emit the final `VerificationReport`.

The implementation keeps imports at module scope and uses explicit Pydantic models instead of loose dictionaries for public task contracts.

## 7. Validation

Unit coverage lives in:

- `tests/unit_tests/agents/orchestration/test_verification.py`
- `tests/unit_tests/agents/tasks/test_task_models.py`

The tests cover:

- structured report generation
- command output evidence linking
- missing evidence failures
- task-scoped tool result evidence
- rejection of unscoped run events
- semantic evaluator fallback
- required-file edge cases
- bounded command output behavior
- evidence helper edge cases
- lint classification before generic test/pass terms
- singular/plural token normalization for evidence matching

Recommended validation for this feature:

```bash
uv run --extra dev pytest -q tests/unit_tests/agents/orchestration/test_verification.py tests/unit_tests/agents/tasks/test_task_models.py
uv run --extra dev ruff check src/relay_teams/agents/orchestration/verification.py src/relay_teams/agents/tasks tests/unit_tests/agents/orchestration/test_verification.py tests/unit_tests/agents/tasks/test_task_models.py
```
