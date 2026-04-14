# Relay Teams Runtime Hooks Design

## 1. Goal

Relay Teams needs a first-class runtime hooks system for lifecycle interception around prompts, tool calls, approvals, stop decisions, tasks, subagents, and compaction.

The target model is:

- hooks are configured declaratively and loaded through the existing config model
- hooks execute at stable lifecycle boundaries that already exist in the current run, task, and tool flow
- hooks may observe, deny, request approval, rewrite input, defer work, or force another model turn depending on the event
- hooks integrate with existing run events, approvals, persistence, and streaming semantics instead of creating a parallel execution model

This adds a runtime governance layer to Relay Teams without changing the product's role, session, or HTTP/SSE architecture.

## 2. Background

Relay Teams needs this as a native runtime capability. External systems are reference material, not the product target.

Their core value is turning soft prompt instructions into deterministic runtime controls:

- prompt instructions say what the model should do
- hooks decide what the runtime will allow to happen

That distinction matters in Relay Teams because the project already has:

- an explicit run lifecycle
- a structured tool runtime
- an approval system
- subagent execution
- SSE-visible run events

These are the exact ingredients needed for a hook system with strong engineering semantics.

Relevant references:

- Claude Code hooks reference: `https://code.claude.com/docs/en/hooks`
- Claude Code from Source chapter 12: `https://claude-code-from-source.com/ch12-extensibility/`
- `cc-haha` repository: `https://github.com/NanmiCoder/cc-haha/tree/main`

## 3. Scope and Non-Goals

This design covers:

- hook configuration, loading, validation, and runtime resolution
- hook event schemas and decision models
- synchronous and asynchronous handler execution
- integration with tool execution, run lifecycle, and prompt submission
- run event publication for hook observability
- staged rollout plan

This design does not:

- replace the current approval system
- replace roles, skills, or orchestration
- allow arbitrary inline Python or unsafe config-defined code execution beyond explicit hook handler types
- introduce frontend-direct repository access
- guarantee full feature coverage in the first phase

## 4. Existing Integration Boundaries

The current repository already exposes the main boundaries required by hooks.

### 4.1 Run lifecycle

Primary module:

- `src/relay_teams/sessions/runs/run_manager.py`

Relevant behavior:

- creates and resumes runs
- publishes `RUN_STARTED`, `RUN_COMPLETED`, `RUN_FAILED`, and `RUN_STOPPED`
- owns stop requests and stop state transitions

This is the correct integration point for:

- `SessionStart`
- `SessionEnd`
- `Stop`
- `StopFailure`

### 4.2 Main LLM loop

Primary module:

- `src/relay_teams/agents/execution/llm_session.py`

Relevant behavior:

- persists user prompt content
- prepares prompt context
- streams model output
- publishes model and tool-related run events

This is the correct integration point for:

- `UserPromptSubmit`
- `PreCompact`
- `PostCompact`
- turn-level stop interception

### 4.3 Tool runtime

Primary module:

- `src/relay_teams/tools/runtime/execution.py`

Relevant behavior:

- central tool execution entrypoint
- approval handling
- visible/internal tool result normalization
- tool record persistence

This is the correct integration point for:

- `PreToolUse`
- `PermissionRequest`
- `PostToolUse`
- `PostToolUseFailure`

### 4.4 Task and subagent lifecycle

Primary modules:

- `src/relay_teams/agents/orchestration/task_execution_service.py`
- `src/relay_teams/sessions/runs/background_tasks/service.py`

Relevant behavior:

- task start and completion
- subagent creation and stop semantics
- run runtime phase changes

These are the correct integration points for:

- `TaskCreated`
- `TaskCompleted`
- `SubagentStart`
- `SubagentStop`

### 4.5 Role and skill frontmatter

Primary modules:

- `src/relay_teams/roles/role_registry.py`
- `src/relay_teams/skills/discovery.py`
- `src/relay_teams/skills/skill_registry.py`

Relevant behavior:

- YAML frontmatter parsing already exists
- skill and role config are already validated and reloaded through dedicated services

This makes role-scoped and skill-scoped hooks feasible in a later phase without inventing a new config format.

## 5. Design Principles

The hook system should follow these principles:

- lifecycle-first: hook events attach to existing run and tool boundaries
- typed contracts: all inputs and outputs are explicit Pydantic v2 models
- no duplicate orchestration: hooks extend the current runtime, they do not replace it
- approval compatibility: hook decisions must compose with the existing tool approval manager
- streaming consistency: prompt and agent hooks must use the same provider transport semantics as the main model path
- safe degradation: persisted or stale references to unknown hooks must not crash startup or run execution
- strict mutation validation: explicit user configuration changes must reject invalid references
- strong observability: hook execution must appear in run events, logs, and debug output

## 6. Hook Event Model

Relay Teams should implement runtime hook events in phases.

### 6.1 Phase 1 events

- `SessionStart`
- `SessionEnd`
- `UserPromptSubmit`
- `PreToolUse`
- `PermissionRequest`
- `PostToolUse`
- `PostToolUseFailure`
- `Stop`
- `StopFailure`

These events provide the highest leverage with the smallest surface area.

### 6.2 Phase 2 events

- `SubagentStart`
- `SubagentStop`
- `TaskCreated`
- `TaskCompleted`
- `PreCompact`
- `PostCompact`

These events extend governance to orchestration and prompt maintenance flows.

### 6.3 Phase 3 events

- `ConfigChange`
- `CwdChanged`
- `FileChanged`
- `WorktreeCreate`
- `WorktreeRemove`

These events are useful, but they are not required for the initial Relay Teams rollout target.

## 7. Decision Semantics

Not every hook event should support every control action.

### 7.1 Supported decisions by event

- `UserPromptSubmit`
  - `allow`
  - `deny`
  - `updated_input`
  - `additional_context`
- `PreToolUse`
  - `allow`
  - `deny`
  - `ask`
  - `updated_input`
  - `defer`
- `PermissionRequest`
  - `allow`
  - `deny`
  - `ask`
- `PostToolUse`
  - `continue`
  - `additional_context`
  - `deferred_action`
- `PostToolUseFailure`
  - `continue`
  - `additional_context`
- `Stop`
  - `allow`
  - `retry`
  - `additional_context`
- `StopFailure`
  - observe only
- `SessionStart`
  - `allow`
  - `set_env`
- `SessionEnd`
  - observe only

### 7.2 Merge rules

When multiple matched hooks run for the same event, Relay Teams should merge decisions conservatively:

- `deny` overrides all other outcomes
- `ask` overrides `allow`
- `retry` overrides `allow` for `Stop`
- only one `updated_input` may be applied
- `additional_context` values are concatenated in priority order
- `set_env` is only valid for `SessionStart`
- `defer` is only valid for `PreToolUse`

If multiple hooks return incompatible decisions, the runtime should:

- apply the highest-priority safe decision
- publish a hook conflict warning event
- log enough detail to diagnose the configuration

## 8. Handler Types

Relay Teams should support four handler types. The implementation remains native to Relay Teams and should be judged by Relay Teams runtime needs.

### 8.1 `command`

Behavior:

- executes a local command
- receives event JSON on stdin
- returns structured output on stdout
- uses exit code and JSON payload to communicate decisions

Use cases:

- local policy checks
- shell command blocking
- repository-specific validation

### 8.2 `http`

Behavior:

- POSTs the event payload to a configured endpoint
- reuses the existing proxy module for outbound connectivity
- interprets the response as a hook decision

Use cases:

- team policy services
- centralized governance
- shared security checks

### 8.3 `prompt`

Behavior:

- runs a lightweight LLM-based validator against the event payload
- returns a typed decision object
- must use the same streaming-compatible provider pathway as the main runtime for the selected endpoint

Use cases:

- semantic approval checks
- completion quality review
- nuanced prompt rewrites

### 8.4 `agent`

Behavior:

- invokes a dedicated verifier role or bounded subagent
- can perform deeper analysis than a prompt hook
- must stay inside the existing agent execution model

Use cases:

- stop verification
- post-tool review
- task completion verification

## 9. Configuration Model

Hooks should be defined with a JSON settings structure that remains idiomatic to Relay Teams and fits the existing config system.

### 9.1 Storage locations

Phase 1 should support:

- user scope: `~/.relay-teams/hooks.json`
- project shared scope: `<repo>/.relay-teams/hooks.json`
- project local scope: `<repo>/.relay-teams/hooks.local.json`

Phase 2 should add:

- role frontmatter hooks
- skill frontmatter hooks

### 9.2 Top-level structure

Recommended structure:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "shell_command",
        "hooks": [
          {
            "type": "command",
            "command": "python .relay/hooks/block_dangerous_shell.py",
            "timeout_seconds": 5
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "agent",
            "role_id": "Verifier",
            "prompt": "Review whether the pending answer is complete and verified."
          }
        ]
      }
    ]
  }
}
```

### 9.3 Config schema

Core models:

- `HooksConfig`
- `HookEventConfig`
- `HookMatcherGroup`
- `HookHandlerConfig`
- `HookSourceInfo`

Suggested matcher fields:

- `matcher`
- optional `if_condition`
- optional `tool_names`
- optional `role_ids`
- optional `session_modes`
- optional `run_kinds`

Suggested handler fields:

- `type`
- `name`
- `timeout_seconds`
- `run_async`
- `on_error`
- type-specific fields such as `command`, `url`, `prompt`, or `role_id`

## 10. Runtime Data Model

Add a new package:

- `src/relay_teams/hooks/`

Recommended modules:

- `hook_models.py`
- `hook_event_models.py`
- `hook_loader.py`
- `hook_registry.py`
- `hook_matcher.py`
- `hook_service.py`
- `hook_runtime_state.py`
- `hook_event_publisher.py`
- `executors/command_executor.py`
- `executors/http_executor.py`
- `executors/prompt_executor.py`
- `executors/agent_executor.py`

### 10.1 Core models

Suggested high-level models:

- `HookEventName`
- `HookHandlerType`
- `HookDecisionType`
- `HookExecutionStatus`
- `HookInvocationContext`
- `HookDecision`
- `HookExecutionResult`
- `HookDecisionBundle`

### 10.2 Event input models

Each event should have its own schema rather than a loose dictionary.

Examples:

- `UserPromptSubmitInput`
- `PreToolUseInput`
- `PermissionRequestInput`
- `PostToolUseInput`
- `StopInput`
- `SessionStartInput`

These models should include the shared run identity fields already used in `RunEvent`:

- `session_id`
- `run_id`
- `trace_id`
- `task_id`
- `instance_id`
- `role_id`

Event-specific fields should then be added explicitly, for example:

- prompt text and content parts for `UserPromptSubmit`
- tool name, tool input, approval summary, and tool call id for `PreToolUse`
- completion reason and pending output for `Stop`

### 10.3 Runtime state

Some hook outputs need temporary run-scoped state:

- session environment variables set during `SessionStart`
- deferred tool executions created by `PreToolUse`
- debug and tracing metadata for hook execution

This state should live in a dedicated runtime component instead of being scattered through the run manager and tool runtime.

## 11. Resolution and Precedence

The hook system should resolve active hooks into a runtime snapshot before or at run start.

### 11.1 Precedence order

Recommended precedence:

- managed policy hooks
- project local hooks
- project shared hooks
- user hooks
- role hooks
- skill hooks

The exact order for role and skill hooks may be adjusted in phase 2, but the important rule is:

- broader governance layers override narrower convenience layers for deny/ask decisions

### 11.2 Snapshot semantics

To avoid malicious or surprising mid-run behavior:

- a run should capture a resolved hook snapshot at start
- changed config should not silently alter an in-flight run
- later config reload only affects new runs by default

This matches the security posture required for a Relay Teams runtime governance feature.

## 12. Integration Plan

### 12.1 `SessionStart` and `SessionEnd`

Primary integration point:

- `src/relay_teams/sessions/runs/run_manager.py`

Behavior:

- run `SessionStart` after the run transitions to active state and before the main worker starts
- apply allowed `set_env` values into hook runtime state
- run `SessionEnd` in worker finalization after completion, failure, or stop

### 12.2 `UserPromptSubmit`

Primary integration point:

- `run_intent()` before prompt content is persisted or passed to the meta agent

Behavior:

- build a typed prompt submission event from `IntentInput`
- allow hooks to deny or rewrite the user prompt
- append `additional_context` as a system-originated injection, not as silent mutation of user text

### 12.3 `PreToolUse`

Primary integration point:

- `src/relay_teams/tools/runtime/execution.py`
- inside `execute_tool()` before `_handle_tool_approval()`

Behavior:

- inspect tool name, summarized args, and run identity
- support deny, ask, rewrite, or defer
- if a tool input rewrite is accepted, update the action input before approval evaluation

Important rule:

- `PreToolUse` must execute before existing approval policy evaluation

### 12.4 `PermissionRequest`

Primary integration point:

- `_handle_tool_approval()` in `execution.py`

Behavior:

- allow hooks to auto-approve, auto-deny, or force explicit approval
- continue to use `ToolApprovalManager` and `ApprovalTicketRepository` as the canonical approval mechanism

Important rule:

- hooks enhance approval policy; they do not replace the approval infrastructure

### 12.5 `PostToolUse` and `PostToolUseFailure`

Primary integration point:

- success and failure branches of `execute_tool()`

Behavior:

- run post-execution review hooks
- optionally schedule deferred work
- optionally attach additional context to the next model turn

### 12.6 `Stop` and `StopFailure`

Primary integration points:

- `src/relay_teams/agents/orchestration/task_execution_service.py`
- `src/relay_teams/sessions/runs/run_manager.py`

Behavior:

- when a run is about to complete with a model answer, execute `Stop`
- if a hook returns `retry`, do not finalize the run yet
- instead inject the hook's `additional_context` and run another model step
- if a turn ends in provider failure, publish `StopFailure` as an observational hook event

Important rule:

- `Stop` should not directly mutate the final assistant output text
- it should block completion and request another model turn

### 12.7 `TaskCreated`, `TaskCompleted`, `SubagentStart`, `SubagentStop`

Primary integration points:

- task creation and status update paths
- background subagent service start and finalization

Behavior:

- expose orchestration lifecycle hooks after phase 1 stabilizes

### 12.8 `PreCompact` and `PostCompact`

Primary integration points:

- `conversation_compaction.py`
- `conversation_microcompact.py`

Behavior:

- allow observability and optional policy checks around context compaction
- do not permit compaction hooks to bypass replay safety constraints

## 13. Run Events and Observability

Hooks should be visible in the same event stream as the rest of the runtime.

Add new `RunEventType` entries:

- `HOOK_MATCHED`
- `HOOK_STARTED`
- `HOOK_COMPLETED`
- `HOOK_FAILED`
- `HOOK_DECISION_APPLIED`
- `HOOK_DEFERRED`

Suggested event payload fields:

- `hook_event`
- `hook_source`
- `hook_handler_type`
- `hook_name`
- `decision`
- `reason`
- `duration_ms`
- `tool_name`
- `tool_call_id`

Benefits:

- frontend can show why a tool was denied or why the run continued
- debugging becomes possible through the existing SSE path
- test assertions can observe hook execution without scraping logs

## 14. Security Model

Hooks are a high-trust feature and must be treated accordingly.

### 14.1 Command hook controls

- explicit max timeout
- bounded stdout and stderr capture
- no implicit shell concatenation for structured handlers
- clear logging of command path and exit status

### 14.2 HTTP hook controls

- reuse existing proxy support
- configurable timeouts
- optional allowlist for destinations in managed environments

### 14.3 Prompt and agent hook controls

- only allowed through approved provider/runtime paths
- no hidden non-streaming shortcut on a streaming-only endpoint
- explicit role allowlist for agent handlers

### 14.4 Snapshot and reload behavior

- hooks are resolved into a snapshot at run start
- config reload does not silently change in-flight runs
- config changes may publish a notification or event for future runs

## 15. Validation Rules

This repository already distinguishes between strict user mutation validation and tolerant runtime resolution for persisted dirty data. Hooks should follow the same rule.

### 15.1 Strict validation for explicit mutations

When a user edits hook config through CLI or API:

- unknown handler types must fail validation
- unknown role references for agent hooks must fail validation
- structurally invalid matcher groups must fail validation

### 15.2 Tolerant runtime behavior for persisted drift

When loading persisted config at runtime:

- unknown references should be ignored
- the system should log a warning with enough context to diagnose the source
- startup and run execution should not fail solely because a persisted hook points at a missing capability

## 16. API and CLI Surface

Phase 1 should add explicit hook management surfaces.

### 16.1 CLI

Suggested commands:

- `relay-teams hooks list`
- `relay-teams hooks validate`
- `relay-teams hooks show --format json`

Output rules should follow existing CLI conventions:

- table output by default
- `--format json` support for list and show commands

### 16.2 API

Suggested endpoints:

- `GET /api/system/configs/hooks`
- `PUT /api/system/configs/hooks`
- `POST /api/system/configs/hooks:validate`

Optional later endpoint:

- `POST /api/system/hooks/test`

This remains inside the `/api/*` contract and does not bypass the backend.

## 17. Phase Plan

### 17.1 Phase 1

Deliver:

- `hooks/` package
- config loader and validator
- `command` and `http` handlers
- event support for `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PostToolUseFailure`, `Stop`, and `StopFailure`
- run event publication for hook execution
- CLI and API config read and validate surface

Expected outcomes:

- block or require approval for risky tool usage
- rewrite prompts and tool inputs deterministically
- run completion gates before a turn ends

### 17.2 Phase 2

Deliver:

- `prompt` and `agent` handlers
- role and skill frontmatter hook declarations
- event support for tasks, subagents, and compaction

Expected outcomes:

- verifier style stop hooks
- task and subagent governance
- component-scoped runtime behavior

### 17.3 Phase 3

Deliver:

- config change and file-system-reactive hooks
- more advanced async hook scheduling
- managed policy controls if needed

Expected outcomes:

- broader automation and environment management
- tighter organizational governance

## 18. Testing Strategy

Changed behavior must come with tests. Hooks should follow that rule.

### 18.1 Unit tests

Add focused unit tests for:

- hook config parsing and validation
- matcher evaluation
- decision merge rules
- command executor result parsing
- HTTP executor response parsing
- event input model validation

Suggested location:

- `tests/unit_tests/hooks/`

### 18.2 Runtime unit tests

Add tests around integration seams:

- `PreToolUse` deny blocks tool execution
- `PermissionRequest` auto-approve resolves without manual approval
- `UserPromptSubmit` rewrite updates intent input
- `Stop` retry prevents immediate completion

Suggested locations:

- `tests/unit_tests/tools/runtime/`
- `tests/unit_tests/sessions/runs/`
- `tests/unit_tests/agents/orchestration/`

### 18.3 Integration tests

Add API and SSE coverage for:

- hook-driven tool denial
- hook-driven approval state transitions
- hook run events appearing in event streams
- stop hook causing another model turn

Suggested locations:

- `tests/integration_tests/api/`
- `tests/integration_tests/browser/` for visible approval and event behavior if UI support is added

## 19. Example Use Cases

The initial implementation should support practical policies immediately.

### 19.1 Block dangerous shell commands

- event: `PreToolUse`
- handler: `command`
- result: `deny`

### 19.2 Route specific web fetches to approval

- event: `PermissionRequest`
- handler: `http`
- result: `ask` or `allow`

### 19.3 Force completion verification

- event: `Stop`
- handler: `agent`
- result: `retry` with follow-up instructions

### 19.4 Inject environment on run start

- event: `SessionStart`
- handler: `command`
- result: `set_env`

## 20. Open Questions

These questions do not block phase 1, but they should be resolved during implementation.

- whether role hooks should have higher precedence than skill hooks or vice versa
- whether `updated_input` should support multiple sequential rewrites or only one winning rewrite
- whether deferred tool execution should reuse the existing background task subsystem directly
- whether managed policy settings are needed in the first release or can wait until the hook core stabilizes
- how much hook execution detail should be exposed in the default frontend versus debug-only views

## 21. Recommended First Implementation Cut

The recommended first implementation cut is intentionally narrow:

- add the `hooks/` package and typed config model
- support user and project hook files
- implement `command` and `http` handlers only
- integrate `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PostToolUseFailure`, `Stop`, `SessionStart`, and `SessionEnd`
- publish hook run events
- add unit tests before expanding to prompt and agent hooks

This gives Relay Teams a strong first cut of runtime hooks with limited architectural risk and minimal conflict with the current approval and orchestration model.


