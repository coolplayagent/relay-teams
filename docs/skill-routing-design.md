# Skill Routing Design

## Summary

This document defines the non-negotiable constraints for skill routing in Agent Teams.
It is the primary reference for the `#92` skill-routing refactor and reuses the
SQLite FTS / BM25 retrieval foundation from `#64`.

The design covers four execution surfaces:

- local runtime
- prompt preview
- gateway ACP stdio
- external ACP role-bound execution

The main goal is to make skill selection body-aware without damaging prompt-cache
reuse or narrowing `load_skill` into a hard blocker.

## Goals

- Build one retrieval-backed skill index from full skill definitions.
- Route large skill catalogs with BM25 using stable textual context.
- Keep `runtime_system_prompt` and `provider_system_prompt` stable across
  objective changes for the same role/workspace/topology/instruction set.
- Keep small authorized skill sets (`<= 8`) in stable system-prompt skill catalog form.
- Show routed skill candidates for large skill sets in the per-turn user prompt instead
  of the system prompt.
- Keep `load_skill` authorized by role capability, not by top-k visibility.
- Reuse the same routing and authorization rules across runtime, preview, gateway ACP,
  and external ACP.

## Non-Goals

- Do not inline full skill bodies into any system prompt.
- Do not inline skill-local role prompts into any system prompt.
- Do not shrink the runtime tool catalog on every routing decision.
- Do not change ACP wire protocol payloads.
- Do not make execution fail when an existing role references a missing skill; runtime
  consumers must filter missing capabilities and log warnings.
- Do not introduce a backend workflow engine for team skills. The model decides whether
  to use skill-local roles after progressive discovery.

## Invariants

- Objective-dependent routed skill text must never enter any system prompt.
- `runtime_system_prompt` and `provider_system_prompt` must remain byte-stable for the
  same role/workspace/topology/instruction set, regardless of objective or routed skills.
- When authorized skill count is `<= 8`, the visible skill catalog is stable capability
  context and may appear in the system prompt as `name + description`.
- Dynamic routed skill candidates may only appear in the per-turn user prompt appendix.
- `load_skill` may only load skills authorized for the active role.
- `load_skill` authorization is based on the role capability set, not on routed top-k visibility.
- External ACP keeps a stable `Role Prompt`; routed skill candidates only appear in `User Prompt`.
- Gateway ACP stdio gets no protocol changes and inherits routing through the internal runtime.
- `skills:reload` must atomically replace both `skill_registry` and `skill_runtime_service`.

## Routing Model

### Skill Index

Each display skill name is indexed into retrieval scope `scope_kind=skill`, `scope_id=skills`.

Document projection:

- `document_id = skill_name`
- `title = skill_name`
- `body = description + instructions + scripts summary + resources summary +
  lightweight team-role signals`
- `keywords = normalized tokens from skill name, scope, script names, and resource names`

If both app and builtin scopes provide the same skill name, routing and prompt rendering
use the app-scoped variant as the preferred document/source of truth.

Team-role signals are intentionally small. When a skill contains `agents/` or `roles/`
with role Markdown files, the index may include the directory names plus each role's
`role_id`, `name`, and `description`. It must not include member role system prompts.
Optional files such as `workflow.md`, `bind.md`, or `dependencies.yaml` may contribute
their filenames as discovery signals, but their full content is not part of the
retrieval document by default.

The index is rebuilt at startup and on `skills:reload`.

### Query Context

Skill routing queries may only use stable textual context:

- objective
- role name
- role description
- shared-state snapshot
- conversation context
- orchestration prompt

Routing must not read:

- runtime environment info
- workspace file contents
- loaded local instruction bodies
- full system prompt text

### Selection Rules

- Resolve the role-authorized skill set first.
- If authorized skill count is `<= 8`, use passthrough, keep all authorized skills visible,
  and prefer stable system-prompt injection over user-prompt candidates.
- If authorized skill count is `> 8`, search the shared skill index with BM25 using
  `search_limit = 24`.
- Filter hits to the authorized set.
- Use the ranked hits first, then fill the remainder to `top_k = 8` with the remaining
  authorized skills in stable name order.
- If the query is empty, there are no authorized hits, or search fails, fall back to
  all authorized skills.

## Prompt Composition

### System Prompt

System prompts keep static role rules, capability summary, and workspace context.
They may contain the stable small-catalog skill list (`<= 8` authorized skills) as
`name + description`, but must not contain routed skill candidates or any objective-specific
skill text.

### User Prompt

The user prompt is the task objective plus an optional `## Skill Candidates` appendix.
That appendix is only used when the authorized skill catalog is larger than `8`.
It contains routed `name + description` entries only.

The appendix also reminds the model that:

- listed skills are recommended candidates
- `load_skill` can still be used for other role-authorized skills when needed

This structure preserves provider-side prompt cache opportunities while still surfacing
relevant skills on each turn.

## Authorization

`load_skill` authorization is enforced at tool execution time.

- authorized: any skill present in the active role capability set
- unauthorized: any skill outside that set
- top-k visibility does not change the authorization boundary

This rule applies equally to local runtime tools and external ACP host tools.

The same authorization boundary applies to skill-team tools. A role may list or
activate skill-local roles only for skills already authorized by its capability set.
The built-in `MainAgent` and `Coordinator` roles both use the wildcard skill
capability so normal and orchestration mode can activate team roles without
manual role edits.

## Skill-Team Role Tools

Skill teams are discovered progressively by the model, not by a required manifest
flag. The normal flow is:

1. BM25 routing recommends candidate skills.
2. The model uses `load_skill` to inspect the selected skill's lightweight manifest
   and file index.
3. If the skill appears to contain team members, the model uses the `skill-teams`
   tool group to inspect and activate those members.

The `skill-teams` group contains:

- `list_skill_roles(skill_name)`: scans skill-local markdown files that expose role
  front matter and returns role summaries only; directory and workflow file names
  are not part of the contract.
- `activate_skill_roles(skill_name, role_ids)`: materializes selected skill-local
  roles as run-scoped effective roles. Returned `effective_role_id` values may be
  passed to existing `spawn_subagent` or `orch_dispatch_task`.

These tools are registered in the default tool registry so role configuration and
tool-group UI can expose them. They are not dynamic tools produced by
`SkillRegistry.get_toolset_tools()`.

## Interface Changes

### `POST /api/prompts:preview`

Request additions:

- optional `orchestration_prompt`

Response additions:

- optional `skill_routing`

`skill_routing` contains:

- routing mode
- query text
- authorized skill count
- visible skills
- scored candidates
- fallback reason when applicable

The preview `user_prompt` returns the final turn prompt text.
For small skill catalogs it is the objective only.
For routed catalogs it includes the skill-candidate appendix.

## ACP Behavior

### Gateway ACP stdio

Gateway ACP stdio reuses the internal runtime. No ACP protocol changes are required.
The gateway therefore inherits:

- stable system prompts
- routed skill candidates in user prompt only
- unchanged tool catalog behavior

### External ACP Role-Bound

External ACP prompt packaging stays:

- `Role Prompt`
- optional `Host Tools`
- `User Prompt`

Only the `User Prompt` may contain routed skill candidates. The `Role Prompt` must remain stable.
Session reuse continues to depend on session-scoped MCP payload and working directory,
not on prompt text.

## Test Coverage

Minimum coverage:

- skill document projection includes instructions, scripts, and resources
- routing query text only includes the allowed context fields
- passthrough, ranked search, fill-to-top-k, and fallback behavior
- `load_skill` rejects unauthorized skills
- preview returns `skill_routing` and a routed `user_prompt`
- system prompts stay stable across objectives
- runtime stores routed skill candidates in user prompt history, not system prompt
- external ACP packages skill candidates inside `User Prompt`
- gateway ACP protocol behavior remains unchanged
