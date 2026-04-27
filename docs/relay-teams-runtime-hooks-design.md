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
- guarantee full Claude Code feature parity

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

Relay Teams implements hook events incrementally, but the design should be read as a support matrix rather than as a strict Claude Code parity promise. Claude Code is the main reference model, while Relay Teams keeps events that map cleanly to its server-side run, task, tool, and session boundaries.

### 6.1 Currently supported events

- `SessionStart`
- `SessionEnd`
- `UserPromptSubmit`
- `PreToolUse`
- `PermissionRequest`
- `PostToolUse`
- `PostToolUseFailure`
- `Stop`
- `StopFailure`
- `SubagentStart`
- `SubagentStop`
- `TaskCreated`
- `TaskCompleted`
- `PreCompact`
- `PostCompact`
- `PermissionDenied`
- `InstructionsLoaded`
- `Notification`

These events cover the core Relay Teams runtime: run startup and shutdown, prompt submission, tool execution and approval, turn completion, orchestration lifecycle, and context maintenance.

### 6.2 Planned or candidate events

- `ConfigChange`
- `CwdChanged`
- `FileChanged`
- `WorktreeCreate`
- `WorktreeRemove`
- `PostToolBatch`
- `UserPromptExpansion`
- `TeammateIdle`
- `Elicitation`
- `ElicitationResult`

The first five are useful for environment automation and workspace lifecycle control. The remaining events are Claude Code parity candidates and should be added only when there is a Relay Teams-native integration point:

- `PostToolBatch` should attach to a full parallel tool batch boundary, not to individual tool completion.
- `UserPromptExpansion` is only needed if Relay Teams adds slash-command or command-expansion semantics before prompt submission.
- `TeammateIdle` should be modeled through existing teammate lifecycle services if implemented.
- `Elicitation` and `ElicitationResult` depend on MCP elicitation support and should not be designed in isolation from the MCP runtime.

### 6.3 Intentional differences from Claude Code

Relay Teams should keep a concise compatibility matrix in this document or a follow-up reference document. Each Claude Code hook event should be marked as one of:

- supported
- planned
- intentionally omitted
- not applicable to Relay Teams

The important design rule is that omission must be explicit. Silent drift from the Claude Code reference makes hook configs hard to reason about and makes future compatibility work more expensive.

### 6.4 Claude parity matrix

| Claude Code hook | Relay Teams status | Notes |
| --- | --- | --- |
| `SessionStart` | supported | Runs at Relay Teams run startup. |
| `SessionEnd` | supported | Observational end-of-run hook. |
| `UserPromptSubmit` | supported | Supports deny, prompt rewrite, and additional context. |
| `PreToolUse` | supported | Supports deny, ask, input rewrite, and defer through shared tool runtime. |
| `PermissionRequest` | supported | Integrates with Relay Teams approval manager. |
| `PostToolUse` | supported | Supports additional context and deferred follow-up turns. |
| `PostToolUseFailure` | supported | Supports failure review and deferred follow-up turns. |
| `Stop` | supported | Supports retry by injecting follow-up context and continuing the run. |
| `StopFailure` | supported | Observational failure hook. |
| `SubagentStart` | supported | Observational subagent lifecycle hook. |
| `SubagentStop` | supported | Supports retry-style rejection of subagent stop. |
| `PreCompact` | supported | Can block compaction before it starts. |
| `PostCompact` | supported | Observational compaction result hook. |
| `PostToolBatch` | planned | Requires an explicit batch boundary in the Relay Teams tool runtime. |
| `PermissionDenied` | supported, Relay-specific semantics | Observational event emitted after pre-tool hook denial, permission-request hook denial, user denial, or approval timeout. Claude Code currently reserves this event for auto-mode classifier denials and supports retry semantics that Relay Teams does not yet implement. |
| `UserPromptExpansion` | planned | Depends on slash-command or command-expansion semantics. |
| `InstructionsLoaded` | supported | Observational event emitted when runtime prompt instruction sources are loaded for a role. Relay Teams emits source-level events with `load_reason` matcher support, then preserves the prior aggregate event for compatibility. |
| `Notification` | supported | Observational event emitted before enabled notification requests are published and dispatched. May add follow-up context when an active injection target exists. |
| `TeammateIdle` | planned | Should integrate with teammate/background lifecycle services. |
| `Elicitation` | planned | Depends on MCP elicitation support. |
| `ElicitationResult` | planned | Depends on MCP elicitation support. |
| `ConfigChange` | planned | Relay Teams-specific runtime config hook candidate. |
| `CwdChanged` | planned | Relay Teams-specific workspace hook candidate. |
| `FileChanged` | planned | Relay Teams-specific workspace hook candidate. |
| `WorktreeCreate` | planned | Relay Teams-specific workspace hook candidate. |
| `WorktreeRemove` | planned | Relay Teams-specific workspace hook candidate. |

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
- `PermissionDenied`
  - observe only
  - `additional_context`
  - `deferred_action`
- `InstructionsLoaded`
  - observe only
- `Notification`
  - observe only
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
  - `additional_context`
- `SessionEnd`
  - observe only
- `SubagentStart`
  - observe only
  - `additional_context`
- `SubagentStop`
  - `allow`
  - `retry`
  - `additional_context`
- `TaskCreated`
  - `allow`
  - `deny`
- `TaskCompleted`
  - `allow`
  - `deny`
- `PreCompact`
  - `allow`
  - `deny`
  - `additional_context`
- `PostCompact`
  - observe only

### 7.2 Merge rules

When multiple matched hooks run for the same event, Relay Teams should merge decisions conservatively:

- `deny` overrides all other outcomes
- `ask` overrides `allow`
- `retry` overrides `allow` for `Stop`
- `retry` overrides `allow` for `SubagentStop`
- only one `updated_input` may be applied
- `additional_context` values are concatenated in priority order
- `set_env` is only valid for `SessionStart`
- `defer` is only valid for `PreToolUse`

Synchronous handlers for the same event run concurrently and their completed decisions are merged in deterministic configuration order. Identical command and HTTP handlers are deduplicated within one event firing, using the command string plus explicit shell for command hooks and the URL for HTTP hooks. Async handlers are also deduplicated within a single event firing, but not across separate event firings.

If multiple hooks return incompatible decisions, the runtime should:

- apply the highest-priority safe decision
- publish a hook conflict warning event
- log enough detail to diagnose the configuration

For observe-only events, handler output is normalized to `observe` before merge and publication. Control decisions such as `deny`, `ask`, `retry`, `updated_input`, `set_env`, and `defer` must not affect the lifecycle boundary. `PermissionDenied` is observational but may preserve `additional_context` and `deferred_action` as best-effort follow-up context when the run still has an active injection target. `Notification` may preserve `additional_context` for the same best-effort follow-up injection path. `SubagentStart` may preserve `additional_context` and append it to the subagent launch prompt before execution starts.

Tool-event matcher values use Relay Teams tool names such as `read`, `write`, `edit`, `shell`, `webfetch`, and `spawn_subagent`. Matcher values support `|` alternation such as `write|edit|shell`; each segment is matched with the same case-sensitive glob semantics as a standalone matcher. For compatibility, exact Claude Code tool aliases in hook config are normalized at load time, for example `Read` to `read`, `Write` to `write`, `Edit` to `edit`, and `Bash` to `shell`. Globbed aliases such as `Write*` are kept as written rather than guessed.

### 7.3 External output protocol

Relay Teams keeps an internal decision model (`HookDecisionType`) so runtime integration points can stay typed and explicit. Handler executors are responsible for translating external hook output into that model.

Command hooks should follow these rules:

- stdin receives the typed event input as JSON
- explicit `shell` values run the command through `bash -lc` or PowerShell; when omitted, Relay Teams preserves the direct-exec behavior used by existing hook configs
- exit code `0` allows execution to continue and permits stdout JSON parsing
- non-zero exit codes are executor failures unless the executor explicitly maps an event-specific code to a decision
- stdout JSON must be a single object when structured output is expected
- stdout, stderr, and parsed JSON should be size-limited before persistence or event publication

HTTP hooks should follow these rules:

- the event input is sent as the POST body
- transport failures, DNS failures, TLS failures, proxy failures, and timeout are executor failures
- non-2xx status is reported as a non-blocking hook failure and must not deny a runtime action by itself
- a successful plain text response body is treated as additional context
- a successful response body is parsed through the same decision translator used by command hooks
- header values may interpolate `$VAR` or `${VAR}` only when the variable is listed in `allowed_env_vars`; unlisted references resolve to an empty string
- outbound HTTP must reuse the existing proxy module

Prompt and agent hooks should follow these rules:

- hook input is included in the evaluator prompt, using `$ARGUMENTS` when present
- the evaluator must return structured JSON
- a positive result maps to `allow` or `continue` depending on the event
- a negative result maps to the event's blocking or retry decision with a reason
- provider calls must use the same streaming-compatible transport semantics as the primary execution flow
- `agent` handler `role_id` is optional. When omitted, execution uses the current hook event `role_id`; if neither is available, execution fails with a clear configuration error.

Claude Code supports top-level fields such as `continue`, `stopReason`, `systemMessage`, `additionalContext`, and event-specific `hookSpecificOutput`. Relay Teams may accept a compatible subset, but every accepted field must have an explicit mapping to the typed decision model. Unsupported fields should be ignored with a warning during tolerant runtime loading or rejected during strict validation, depending on where they enter the system.

### 7.4 Async hook semantics

By default, hooks are synchronous and block the lifecycle boundary until they finish or time out. `run_async` is for side effects that must not delay the agentic loop.

Async hooks must not control the action that has already continued:

- `deny`, `ask`, `retry`, `updated_input`, and `defer` are ignored for async handlers
- `additional_context`, `systemMessage`, or equivalent informational output may be delivered on a later turn if the runtime has a safe injection point
- async completion should be observable through run events or verbose diagnostics
- async execution is initially safest for `command` handlers; other handler types need explicit design before being enabled

`async_rewake` is a Relay Teams-specific extension for cases where a background hook completion should schedule a follow-up turn. It is opt-in, requires an active run injection queue and recipient instance, and publishes `HOOK_DEFERRED` when it enqueues follow-up context.

## 8. Handler Types

Relay Teams currently supports four handler types. Claude Code also supports `mcp_tool`; Relay Teams should treat that as a separate future decision because MCP tool hooks can recursively interact with tool execution, approval, timeout, and hook matching.

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

### 8.5 `mcp_tool` candidate

Behavior:

- calls a tool on an already-connected MCP server
- treats the MCP tool output as hook stdout or structured hook JSON
- uses the existing MCP registry, timeout, approval, and error handling model

Open design requirements before enabling:

- prevent recursive hook invocation when an MCP hook calls tools
- define whether MCP hook tools require normal tool approval
- preserve event identity and auditability for hook-triggered MCP calls
- decide whether MCP hook output can rewrite input or only observe/block
- ensure MCP elicitation support is designed together with `Elicitation` and `ElicitationResult` events

Until those questions are resolved, `mcp_tool` should be documented as unsupported rather than partially accepted.

## 9. Configuration Model

Hooks should be defined with a JSON settings structure that remains idiomatic to Relay Teams and fits the existing config system.

### 9.1 Storage locations

Currently supported:

- user scope: `~/.relay-teams/hooks.json`
- project shared scope: `<repo>/.relay-teams/hooks.json`
- project local scope: `<repo>/.relay-teams/hooks.local.json`
- role frontmatter hooks
- skill frontmatter hooks

Managed policy hooks remain a future governance layer.

### 9.2 Top-level structure

Recommended structure:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "name": "Dangerous shell guard",
        "matcher": "shell",
        "hooks": [
          {
            "type": "command",
            "name": "Block dangerous shell",
            "if": "shell(rm *)",
            "timeout": 10,
            "on_error": "fail",
            "command": "python .relay/hooks/block_dangerous_shell.py"
          }
        ]
      }
    ],
    "Notification": [
      {
        "name": "Notification webhook",
        "hooks": [
          {
            "type": "http",
            "name": "Send notification payload",
            "url": "https://example.test/hooks/relay",
            "headers": {
              "Authorization": "Bearer $HOOK_TOKEN"
            },
            "allowed_env_vars": ["HOOK_TOKEN"],
            "timeout": 5
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "name": "Prompt policy reviewer",
        "hooks": [
          {
            "type": "prompt",
            "name": "Review submitted prompt",
            "prompt": "Review the submitted prompt and return a hook decision JSON object."
          }
        ]
      }
    ],
    "Stop": [
      {
        "name": "Final answer verifier",
        "matcher": "*",
        "hooks": [
          {
            "type": "agent",
            "name": "Verify final answer",
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

Matcher group fields:

- optional `name`, the display name for the hook event card and runtime view
- `matcher`
- `hooks`

Handler fields:

- `type`
- optional `name`, the display name for the handler editor and diagnostics
- optional `if`, a handler-level condition for tool events only
- optional `timeout`, measured in seconds; defaults are omitted by the settings UI
- optional `on_error`, currently `ignore` or `fail`; defaults are omitted by the settings UI
- command handler: `command`
- HTTP handler: `url`, optional `headers`, and optional `allowed_env_vars`
- prompt handler: `prompt`
- agent handler: `prompt` and optional `role_id`; omitted `role_id` falls back to the current hook event role

Compatibility rules:

- Runtime loading still tolerates older optional fields such as `timeout`, `timeout_seconds`, `async`, `run_async`, `on_error`, handler `name`, handler `if`, `shell`, HTTP `headers`, `allowed_env_vars`, `async_rewake`, `status_message`, and model overrides where supported.
- New settings UI exposes the commonly recommended optional fields: handler `name`, tool-event `if`, `timeout` in seconds, `on_error`, HTTP `headers`, and HTTP `allowed_env_vars`. It does not expose agent `role_id`; that remains an advanced hand-authored compatibility field. It still omits optional defaults when saving.
- `timeout` is accepted as an alias for `timeout_seconds` when present in existing config.
- `async` is accepted as an alias for `run_async` when present in existing config.
- `if` is the canonical handler-level condition field.
- legacy `if_condition` may be migrated when it can be represented without ambiguity; new examples should not use it.
- `shell`, `allowed_env_vars`, `async_rewake`, and `status_message` are Relay Teams extensions and are not Claude Code hook schema fields. The settings UI currently exposes `allowed_env_vars` for HTTP handlers but does not expose `shell`, `async_rewake`, `status_message`, `run_async`, or model overrides.
- `role_ids`, `session_modes`, and `run_kinds` are Relay Teams scoped filters, not Claude Code matcher equivalents.

Matcher rules should stay explicit:

- `*`, an empty matcher, or an omitted matcher means match all for events that support matchers
- tool events match against tool name
- `SessionStart` matches against the session start source
- `SessionEnd` matches against the session end reason
- `SubagentStart` and `SubagentStop` match against the subagent role or type
- `PreCompact` and `PostCompact` match against the compaction trigger
- events without matcher support only accept `*`, empty, or omitted group matchers during validation
- handler-level `if` is evaluated only for tool events unless a future event explicitly defines semantics for it

Handler-level `if` rules currently support `Tool(pattern)` conditions for tool events. The tool name uses the same exact Claude alias normalization as matcher configuration, so `Bash(git *)` is evaluated as `shell(git *)`. The pattern is matched with case-sensitive glob semantics against the tool's primary input field:

- `shell`: `command`
- `read`, `write`, `edit`, `notebook_edit`, `office_read_markdown`, `write_tmp`: `file_path` or `path`
- `glob`, `grep`: `pattern` or `path`
- `webfetch`: `url`
- `websearch`: `query`

Unsupported or malformed `if` expressions evaluate to false rather than widening execution.

## 10. Runtime Data Model

Runtime package:

- `src/relay_teams/hooks/`

Primary modules:

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

The exact order for role and skill hooks may be adjusted as the governance model matures, but the important rule is:

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

Tool authoring rule:

- new tools should not manually parse `tool_input` in each tool implementation
- the default pattern is `execute_tool_call(..., raw_args=locals())` with an `action` callable that uses normal named parameters
- hook-driven input rewrite must be applied in the shared tool runtime, not reimplemented in individual tools
- shared runtime env from hooks should flow through common HTTP and command execution layers automatically rather than being manually threaded by each tool

Example pattern:

```python
return await execute_tool_call(
    ctx,
    tool_name="write",
    args_summary={"path": path, "content_len": len(content)},
    action=_action,
    raw_args=locals(),
)
```

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

- expose orchestration lifecycle hooks through the existing task and background subagent services
- keep `SubagentStart` observational unless a concrete pre-start blocking use case is designed
- allow `SubagentStop` to request a retry or follow-up pass when verification fails
- allow task creation and completion hooks to block invalid task state transitions where the runtime can still safely abort the transition
- publish hook events with enough task and subagent identity to diagnose which orchestration branch fired the hook

### 12.8 `PreCompact` and `PostCompact`

Primary integration points:

- `conversation_compaction.py`
- `conversation_microcompact.py`

Behavior:

- allow observability and optional policy checks around context compaction
- do not permit compaction hooks to bypass replay safety constraints
- allow `PreCompact` to block compaction only before compaction starts
- keep `PostCompact` observational because the compacted context has already been produced

## 13. Run Events and Observability

Hooks should be visible in the same event stream as the rest of the runtime.

Use these hook-related `RunEventType` entries:

- `HOOK_MATCHED`
- `HOOK_STARTED`
- `HOOK_COMPLETED`
- `HOOK_FAILED`
- `HOOK_CONFLICT`
- `HOOK_DECISION_APPLIED`
- `HOOK_DEFERRED`

Suggested event payload fields:

- `hook_event`
- `hook_source`
- `hook_handler_type`
- `hook_name`
- `decision`
- `reason`
- `conflicts`
- `duration_ms`
- `tool_name`
- `tool_call_id`

Benefits:

- frontend can show why a tool was denied or why the run continued
- debugging becomes possible through the existing SSE path
- test assertions can observe hook execution without scraping logs

The settings UI should use `/api/system/configs/hooks/runtime` as the read-only effective runtime view. It should show the handler, event, matcher, `if` rule, source scope, source path, and scoped filters (`role_ids`, `session_modes`, `run_kinds`) so project, project-local, role, and skill hooks can be diagnosed without reading config files directly. User-scope hooks may be edited through `/api/system/configs/hooks`, while project, role, and skill sources should remain read-only in that UI unless an explicit source editor is added later.

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
- unknown explicit role references for agent hooks must fail validation; omitted `role_id` is valid and falls back to the hook event role at runtime
- structurally invalid matcher groups must fail validation

### 15.2 Tolerant runtime behavior for persisted drift

When loading persisted config at runtime:

- unknown references should be ignored
- the system should log a warning with enough context to diagnose the source
- startup and run execution should not fail solely because a persisted hook points at a missing capability

## 16. API and CLI Surface

The hook system should expose explicit management surfaces.

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

## 17. Implementation Status and Phase Plan

### 17.1 Current implementation status

As of 2026-04-24, the repository has a working runtime hook system with these implemented pieces:

- `src/relay_teams/hooks/` exists with typed models, event models, matcher, loader, runtime state, command/http/prompt/agent executors, and `HookService`
- config loading supports user, project shared, project local, role frontmatter, and skill frontmatter hook sources
- CLI and API surfaces exist for hook config show/list/validate and user-scope save
- hook run events are published through the existing `RunEventHub`
- runtime integration exists for `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `PermissionDenied`, `PostToolUse`, `PostToolUseFailure`, `Stop`, `StopFailure`, `SubagentStart`, `SubagentStop`, `TaskCreated`, `TaskCompleted`, `PreCompact`, `PostCompact`, `InstructionsLoaded`, and `Notification`
- run snapshots are captured at run creation and cleared when the run finalizes
- `SessionStart.set_env` is consumed by provider and tool runtime execution paths for the active run
- `UserPromptSubmit.updated_input` rewrites the live prompt, and `additional_context` is persisted as a separate system-originated conversation record
- `PreToolUse.updated_input` rewrites the live tool input that is sent through approval and execution paths
- `PermissionDenied` is emitted after pre-tool hook denial, permission-request hook denial, user approval denial, reusable denied approvals, and approval timeout; it is observational and may add or defer follow-up context, but it cannot re-allow a denied call
- `InstructionsLoaded` is emitted from the runtime prompt builder after local/configured instruction sources are loaded and before they are appended to the prompt; source-level events include `mode=source`, `source`, `source_type`, `file_path`, `load_reason`, and `memory_type` where available, followed by a compatibility `mode=aggregate` event
- `Notification` is emitted by the notification service after notification rules enable a request and before the request is published or dispatched; notification hook recursion is guarded, synchronous `emit()` waits for the hook even when called from a thread that already has an event loop, and additional context is injected when an active run target exists
- tool authoring has a default middleware-style path through `execute_tool_call(..., raw_args=locals())`, so new tools inherit hook-aware input rewrite without per-tool `tool_input` parsing
- hook-provided runtime environment for tool execution propagates through shared HTTP and command execution layers instead of being manually passed by each affected tool
- `PostToolUse` and `PostToolUseFailure` can schedule deferred follow-up turns through the injection queue and emit `HOOK_DEFERRED`
- synchronous hook handlers run concurrently with command/HTTP deduplication inside one event firing
- `SubagentStart.additional_context` is appended to the launched subagent prompt before the subagent starts executing
- prompt hooks can evaluate hook input through the provider path and return structured decisions
- agent hooks can run verifier-style roles and return structured decisions, including `Stop` retry behavior
- focused unit coverage and API integration coverage exist for prompt rewrite, tool rewrite, session env propagation, deferred follow-up behavior, prompt/agent handlers, task/subagent hooks, and compaction hooks

The implementation is still intentionally narrower than full Claude Code parity:

- `mcp_tool` handlers are not supported
- `PostToolBatch`, `UserPromptExpansion`, `TeammateIdle`, `ConfigChange`, `CwdChanged`, `FileChanged`, `WorktreeCreate`, `WorktreeRemove`, `Elicitation`, and `ElicitationResult` are not implemented
- async hook control semantics are intentionally narrow; only `command` handlers may run in the background, and `async_rewake` only schedules follow-up context when there is an active run injection target
- managed policy hooks are not implemented

### 17.2 Completed first cut

Delivered:

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

### 17.3 Completed second cut

Delivered:

- `prompt` and `agent` handlers
- role and skill frontmatter hook declarations
- event support for tasks, subagents, and compaction

Expected outcomes:

- verifier style stop hooks
- task and subagent governance
- component-scoped runtime behavior

### 17.4 Hooks vs Built-In System Reminders

Runtime hooks are the user/project/role/skill extension layer. They remain the right
place for custom command, HTTP, prompt, or agent handlers.

Built-in system reminders live in `src/relay_teams/reminders/` and own product
runtime policy such as tool failure nudges, read-only streak nudges, incomplete todo
completion guards, and post-compaction reminders. These policies must not be modeled
as default hooks because they need deterministic execution, run-scoped state, and
pre-terminal completion decisions.

Hooks and reminders share the low-level `SystemInjectionSink`, but hooks do not own
the reminder policy engine.

### 17.5 Next phase

Deliver:

- config change and file-system-reactive hooks
- more advanced async hook scheduling for non-command handlers if needed
- managed policy controls if needed
- explicit Claude parity matrix
- possible `mcp_tool` hook support after recursive execution and approval semantics are designed

Expected outcomes:

- broader automation and environment management
- tighter organizational governance
- clearer compatibility boundaries for users who bring Claude Code hook configs to Relay Teams

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

These questions do not block the current implementation, but they should be resolved before expanding hook parity further.

- whether role hooks should have higher precedence than skill hooks or vice versa
- whether `updated_input` should support multiple sequential rewrites or only one winning rewrite
- whether deferred tool execution should reuse the existing background task subsystem directly
- whether managed policy settings are needed in the first release or can wait until the hook core stabilizes
- how much hook execution detail should be exposed in the default frontend versus debug-only views
- whether to support `mcp_tool` handlers and how to prevent recursive hook/tool execution
- whether to expand the current Claude Code-compatible `hookSpecificOutput` subset beyond the fields already mapped into the Relay Teams decision schema
- whether async hook completion should always be visible in SSE, only in verbose diagnostics, or only when it injects follow-up context
- whether `PostToolBatch` requires a new explicit batch boundary in the tool runtime
- whether MCP elicitation events belong in the hook system or in a dedicated MCP interaction layer

## 21. Recommended Next Implementation Cut

The next implementation cut should improve correctness and compatibility boundaries before adding many more events:

- add candidate design notes for non-command async hooks if needed
- decide whether Relay Teams should broaden the current Claude Code `hookSpecificOutput` compatibility layer
- add candidate design notes for `PostToolBatch` and `mcp_tool` before implementation
- extend tests for unsupported-field validation and candidate event rejection

This keeps the hook system stable while making future compatibility work explicit and testable.

