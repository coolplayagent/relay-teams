# SG-1 Runtime Guardrails

## 1. Purpose

SG-1 adds a deterministic runtime safety layer around tool execution and task acceptance. The implementation turns runtime safety from prompt-only guidance into auditable controls that run before tool execution, during tool result handling, and after task completion.

## 2. Goals

- Deny tool calls outside the effective role tool boundary before the action body runs.
- Deny tools explicitly blocked by runtime policy, even when approval mode would otherwise allow them.
- Block common destructive shell command patterns before shell execution.
- Warn on unusually large tool inputs, unusually large tool outputs, and frequent write-capable calls.
- Persist task-scoped guardrail state and publish guardrail alert/report events for auditability.
- Make Gater verification require a parseable runtime guardrail report for coordinator-driven acceptance.

## 3. Non-Goals

- SG-1 does not replace role contract enforcement. SG-2 remains the source of truth for role-level tool boundary filtering.
- SG-1 does not add a new public API surface or database table.
- SG-1 does not attempt full shell semantic analysis; it applies deterministic pattern checks for high-risk commands.
- SG-1 does not make persistence failures block unrelated tool results. Failures are logged and the original tool flow continues unless a guardrail rule itself denies the call.

## 4. Feature Design

The guardrail layer has three stages:

1. Pre-execution guardrails run after hook input rewriting and before reusable tool results, approval prompts, or tool actions. They enforce the effective role tool boundary, explicit denied tools, destructive shell command patterns, tool input size limits, and per-task call-frequency thresholds.
2. In-execution guardrails run after tool output normalization and post-tool hooks. They inspect the final tool result envelope and can warn or block the visible result before it is persisted and returned to the model.
3. Post-validation guardrails generate a `RuntimeGuardrailReport` when task execution completes. The report is persisted in shared task state and published as a `runtime_guardrail_report` run event so Gater verification can use it as required acceptance evidence.

## 5. Configuration Contract

`ToolApprovalPolicy` now owns `guardrails: RuntimeGuardrailPolicy`. A policy contains ordered `RuntimeGuardrailRule` entries with:

- `layer`: `pre_execution`, `in_execution`, or `post_validation`
- `rule_type`: allowlist, denylist, size, frequency, or destructive shell pattern
- `action`: `allow`, `warn`, or `deny`
- optional selectors for tool names, role ids, session modes, and run kinds
- rule-specific thresholds such as `max_bytes`, `max_calls_per_task`, and `blocked_patterns`

The default rules deny tools outside the effective role tool set, deny explicitly disabled tools, block common destructive shell patterns, warn on large inputs, warn on frequent write-capable calls, and warn on large outputs. Callers can replace the rules through `ToolApprovalPolicy(guardrails=...)`.

## 6. Implementation Spec

Runtime execution integrates SG-1 through `execute_tool_call(...)` and `execute_tool(...)`:

- Pre-tool hooks may rewrite arguments first; guardrails evaluate the rewritten tool input.
- The guardrail context records run, session, task, instance, role, tool name, tool call id, session mode, and run kind.
- Tool call counts and findings are persisted through a single shared-state update operation so parallel tool calls cannot overwrite each other.
- A pre-execution denial returns a deterministic `ToolError` without opening approval or invoking the action body.
- In-execution warnings annotate the result envelope; in-execution denials replace the visible result with a deterministic guardrail error.
- Guardrail alerts are published as run events containing the tool name, tool call id, and serialized non-allow findings.

## 7. Persistence and Events

Guardrail state is stored in `SharedStateRepository` under the task scope:

- `runtime_guardrails`: tool call counters and recorded findings
- `runtime_guardrail_report`: generated task-level report

`runtime_guardrails` keeps cumulative warning and blocked counts separately from retained observations. Only the newest observations are retained to bound state size, but the report status and aggregate counts remain stable after older WARN/DENY observations are truncated.

Run events:

- `runtime_guardrail_alert`: emitted when a rule produces a warning or denial
- `runtime_guardrail_report`: emitted when the task-level report is generated

Tool result metadata includes `runtime_guardrail_status`, warning/block counts, and serialized findings when guardrails trigger.

## 8. Verification Contract

`verify_task(..., require_guardrail_report=True)` fails if no parseable report event exists for the task. Coordinator-driven verification sets this flag so Gater acceptance requires the Proof-of-Guardrail report. If an existing report event is stale or malformed, coordinator verification regenerates and republishes the report before calling Gater verification.

The report also becomes structured verification evidence with `runtime_guardrail_report` evidence kind and `security` verification layer checks.

## 9. Failure Handling

Guardrail state persistence and alert publishing failures are logged through the project logger and do not hide the original tool result. A guardrail denial itself returns a deterministic tool error before action execution for pre-execution rules, or replaces the visible result for in-execution deny rules.

## 10. Required Tests

- Default policy construction uses the rule factory directly.
- Concurrent guardrail state updates preserve all per-task tool calls and findings.
- Truncated observations do not downgrade cumulative WARN/DENY report counts or status.
- Malformed runtime guardrail report events do not satisfy coordinator verification prechecks.
- Pre-execution and in-execution guardrail denials produce deterministic tool errors and metadata.
