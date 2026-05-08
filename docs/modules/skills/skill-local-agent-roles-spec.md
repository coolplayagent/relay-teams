# Skill-Local Agent Roles

## Purpose

Skills may carry local subagent role documents so a model can discover a skill,
inspect its available helper roles, and activate only the roles needed for the
current run. These roles are exposed through `list_skill_roles(skill_name)` and
materialized through `activate_skill_roles(skill_name, role_ids)`.

## Discovery

`list_skill_roles` scans Markdown files under the selected skill directory.
`SKILL.md` is excluded. Any other Markdown file with YAML front matter containing
`role_id` is treated as a candidate skill-local role.

The `agents/` directory is a first-class convention for skill-local subagents.
For example:

```text
my-skill/
  SKILL.md
  agents/
    ci-analyzer.md
```

Directory names are not part of the authorization boundary. Authorization is
still based on whether the calling role is allowed to use the parent skill.

## Role Document Format

A skill-local role document is a Markdown file with YAML front matter followed by
the role system prompt body.

Required fields:

- `role_id`
- `name`
- `description`
- `tools`

Optional fields:

- `version`, defaults to `"1"`
- `mode`, defaults to `subagent`
- `mcp_servers`, defaults to an empty list
- `skills`, defaults to an empty list
- `model_profile`, defaults to `default`
- `bound_agent_id`
- `execution_surface`, defaults to `api`
- `memory_profile`
- `contract`
- `hooks`

Example:

```markdown
---
role_id: ci-analyzer
name: CI Pipeline Analyzer
description: Analyzes CI pipeline failures and fixes targeted issues.
tools:
  - read
  - edit
  - shell
---

You are a CI Pipeline Fixer agent.
```

## Runtime Semantics

Skill-local roles are always activated as run-scoped subagent roles. If a role
document sets another `mode`, `activate_skill_roles` still produces a
`TemporaryRoleSpec` with `mode=subagent` so the effective role can be used by
`spawn_subagent` and orchestration dispatch.

The effective role id is deterministic:

```text
skill_team_<skill-fragment>_<role-fragment>_<hash>
```

The original `role_id` remains the selector used by `activate_skill_roles`.

## Validation

Invalid role documents are ignored and logged with the skill name, source path,
and validation error. Duplicate `role_id` values within the same skill keep the
first sorted document and ignore later duplicates.

Explicit role mutations outside skill directories remain strict and must still
provide the full role document fields required by the role editor and role
validation APIs.
