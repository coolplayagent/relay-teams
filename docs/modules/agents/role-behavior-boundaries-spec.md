# SG-2 Role Behavior Boundaries Spec

## 1. Purpose

SG-2 makes role behavior boundaries deterministic at runtime. A role contract is
not only prompt guidance: every `must_not_have_tools` invariant removes matching
local tools from the runtime tool surface and blocks matching tool calls in the
shared tool execution policy.

This closes the shell/write_tmp escape path called out in
`docs/research/lessons-learned-2026.md`.

## 2. Goals

- Enforce role tool deny lists before tools are registered with a model.
- Enforce the same deny lists again inside `execute_tool(...)` so stale runtime
  state, old snapshots, or direct tool invocation cannot bypass the boundary.
- Keep dirty persisted role state tolerant: runtime reloads do not fail only
  because a saved role still contains a forbidden tool, but that tool is filtered
  and logged before use.
- Keep explicit role edits strict: save and validation paths still reject roles
  whose selected tools violate their own contract invariants.
- Remove the built-in Gater role's shell and write_tmp escape channels.

## 3. Non-Goals

- SG-2 does not add a new database table or public API field.
- SG-2 does not add parameter-level shell command classification. Roles that
  must not execute commands must deny the `shell` tool.
- SG-2 does not remove temporary report writing from Explorer or Designer,
  because their contracts allow temporary artifacts while denying production
  edit and shell tools.

## 4. Domain Contract

`RoleContractInvariantType.MUST_NOT_HAVE_TOOLS` is the source of truth for
runtime-denied local tool names.

`roles.runtime_tools.runtime_denied_tools_for_role(role)` returns the ordered,
deduplicated deny list declared by the role contract.

`roles.runtime_tools.strip_contract_denied_tools(...)` removes those denied
tools from a candidate runtime tool list and emits a warning event with the role
id, consumer, and removed tools.

`runtime_tools_for_role(...)` applies both existing coordinator-only filtering
and SG-2 contract-denied filtering. All prompt assembly, runtime snapshots, and
session tool registration paths that already use `runtime_tools_for_role(...)`
therefore receive the hardened tool list.

## 5. Execution Policy

The shared tool runtime policy now resolves the effective role allowlist through
`runtime_tools_for_role(...)` instead of raw `role.tools`.

The policy also removes contract-denied tools after loading persisted runtime
tool snapshots. This prevents stale snapshots from re-authorizing a tool that
the current role contract forbids.

When a denied tool is attempted, the existing tool result envelope is reused:

- `ok: false`
- `error.type: tool_policy_denied`
- `meta.runtime_policy_decision: deny`
- `meta.approval_status: denied_by_policy`

The action body is not invoked and approval/yolo mode cannot override the deny.

## 6. Built-In Role Boundary

Gater is now a read-only auditor:

- allowed: grep, glob, read, office_read_markdown, background task observation,
  and monitor controls
- denied by contract: edit, write, notebook_edit, write_tmp, shell

This matches the role's behavioral boundary: Gater audits existing evidence and
returns a conclusion; it does not create report files or execute commands.

## 7. Validation And Compatibility

Strict role mutation paths continue to call
`role_contract_invariant_failures(...)`, so explicitly saving a role with a
forbidden selected tool still fails validation.

Non-strict runtime reload paths may load stale role files. SG-2 filters the
forbidden tool at runtime instead of failing startup, which keeps existing dirty
configuration tolerant while preserving the security boundary.

## 8. Tests

Required coverage:

- Runtime tool helper filters `must_not_have_tools`.
- Dirty persisted role state cannot execute a contract-forbidden tool, even in
  yolo mode.
- Stale runtime tool snapshots cannot regrant a contract-forbidden tool.
- Built-in Gater does not expose shell or write_tmp and denies them in contract.

