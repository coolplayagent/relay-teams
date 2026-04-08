# ClawHub Skill Installation Lessons

## Summary

This document captures practical lessons from installing ClawHub-managed skills into `agent-teams`, with a focus on making future upgrades stronger, safer, and more reliable.

The key finding is simple:

- **ClawHub publish/install identity is not the same as Agent Teams runtime identity.**
- A skill can be installed successfully by slug while still being undiscoverable at runtime.
- A skill can also expose a runtime name that differs from the published slug.

If we want the product to feel robust, the install path must validate against the **runtime-discovered capability**, not just the package manager result.

---

## What happened

We installed a ClawHub skill published under the slug `skill-creator-2`.

At first glance the installation looked successful:

- `clawhub install skill-creator-2` completed successfully
- the files existed on disk
- the skill looked usable from the package-manager perspective

But the runtime still could not use it.

There were two separate causes:

1. **Wrong install directory for Agent Teams runtime discovery**
   - `clawhub` installed to the current working directory's `skills/`
   - `agent-teams` runtime discovers app skills from `~/.relay-teams/skills` by default
   - result: install succeeded, runtime discovery failed

2. **Published slug differed from runtime skill name**
   - package slug: `skill-creator-2`
   - `SKILL.md` frontmatter name: `skill-creator`
   - Agent Teams authorizes and resolves by discovered skill name, not package slug
   - result: role authorization using `skill-creator-2` failed even after the files were moved into the correct runtime directory

The working runtime authorization was:

- `app:skill-creator`

not:

- `skill-creator-2`

---

## Confirmed runtime facts

### Runtime discovery source

Agent Teams app-scoped skill discovery reads from the app config directory skill path:

- `src/relay_teams/skills/discovery.py:38`
- `src/relay_teams/skills/discovery.py:106`

That means the correct install target for runtime-visible app skills is:

- `~/.relay-teams/skills`

not an arbitrary repository-local `skills/` folder, unless the runtime is explicitly configured to use that path.

### Runtime skill identity source of truth

The discovered runtime skill name comes from `SKILL.md` frontmatter `name`, not from the ClawHub slug:

- `src/relay_teams/skills/discovery.py:215`
- `src/relay_teams/skills/discovery.py:276`
- `src/relay_teams/skills/discovery.py:284`

This is why a directory named `skill-creator-2/` was discovered by Agent Teams as the skill:

- `skill-creator`

### Role authorization behavior

Role skill authorization resolves against the discovered runtime name or canonical ref:

- `src/relay_teams/skills/skill_registry.py:161`
- `src/relay_teams/skills/skill_registry.py:172`
- `src/relay_teams/skills/skill_registry.py:178`

When app and builtin scopes may collide, canonical refs are the stable choice:

- `app:skill-creator`

---

## Installation rules that worked

### Rule 1: Install into the runtime-visible app skills directory

Use ClawHub with the Agent Teams app config dir as workdir:

```bash
clawhub --workdir ~/.relay-teams install <slug>
```

This ensures the resulting skill lands under:

```text
~/.relay-teams/skills/<slug>
```

### Rule 2: Read `SKILL.md` after install

After installation, inspect:

- `~/.relay-teams/skills/<slug>/SKILL.md`

The frontmatter `name` is the actual runtime identity used by Agent Teams.

### Rule 3: Bind roles to the runtime name, not the publish slug

If the installed slug and runtime name differ, role config must use the runtime name or canonical ref.

Preferred form:

```text
app:<skill_name_from_SKILL_md>
```

Example:

```text
app:skill-creator
```

### Rule 4: Verify through the runtime registry, not only the installer

A successful `clawhub install` is only half the check.

The real validation should be:

1. Agent Teams skill registry can discover the skill
2. role config can resolve the skill successfully
3. the intended role is authorized to load it

---

## Security lessons

During installation, some candidate skills were flagged as suspicious by registry scanning.

That exposed an important operational rule:

- **do not auto-force install suspicious skills in non-interactive mode**
- prefer a clean equivalent when available
- if a suspicious skill is the only candidate, inspect its files and external dependencies before deciding whether to allow it

For this workflow, a safer approach was to choose the clean candidate package and then adapt the runtime authorization to the discovered internal name.

---

## What should be improved in Agent Teams

## 1. Add a first-class ClawHub installer bridge

Agent Teams should provide a native install flow that:

- installs into the runtime-visible app skills directory automatically
- reloads skills after installation
- inspects the discovered runtime name after install
- offers the exact role-binding value to use
- warns when slug and runtime name differ

Ideal output:

- installed slug
- discovered runtime name
- canonical runtime ref
- bindable role value
- any ambiguity or scope conflict

### Why this matters

It removes the current gap between:

- package-manager success
- runtime capability success

---

## 2. Surface slug-to-runtime-name mapping in the UI and CLI

The system should make the distinction explicit.

A good `skills list` or install response would show:

- source slug
- discovered name
- scope
- canonical ref
- install path

Example:

```text
slug: skill-creator-2
name: skill-creator
scope: app
ref: app:skill-creator
path: ~/.relay-teams/skills/skill-creator-2
```

### Why this matters

This would have made the failure mode obvious immediately.

---

## 3. Add post-install runtime verification

After installation, Agent Teams should run a verification pass:

- confirm `SKILL.md` parses
- confirm runtime discovery sees the skill
- confirm no ambiguous-name conflict blocks usage
- confirm role binding candidates are valid

If any of those fail, the install result should be downgraded from success to partial success with actionable remediation.

---

## 4. Add an install-and-bind workflow

For actual user productivity, the strongest flow is not just install, but:

- install skill
- discover runtime identity
- bind to one or more roles
- reload skills config
- validate role authorization

That could live as:

- a CLI workflow
- a built-in installer skill
- a server API endpoint

---

## 5. Add runtime-facing skill health reporting

A skill status page or API should answer:

- installed on disk?
- parsed successfully?
- discovered by runtime?
- authorized for which roles?
- ambiguous with builtin/app duplicate?
- requires external env vars?
- flagged suspicious?

This would make future capability upgrades much stronger because the operator can reason about the real runtime state instead of guessing from file presence.

---

## Recommended immediate follow-ups

### Product

1. add a native ClawHub install helper in Agent Teams
2. add post-install skill discovery verification
3. expose canonical bindable refs in skill listing and install output
4. support install-and-bind in one operation
5. show security and dependency warnings in one place

### Documentation

1. document that Agent Teams runtime app skills live under `~/.relay-teams/skills` by default
2. document that ClawHub slug may differ from Agent Teams runtime skill name
3. recommend canonical refs when binding app skills to roles
4. document the verification workflow after installation

### Testing

1. add coverage for app skill discovery from the user config dir
2. add coverage for slug-directory vs internal-name mismatch
3. add coverage for role binding using canonical refs after install
4. add coverage for ambiguous app/builtin skill names in role config

---

## Practical operator checklist

When installing a new ClawHub skill for Agent Teams runtime use:

1. install into `~/.relay-teams`
2. inspect the installed `SKILL.md`
3. read the frontmatter `name`
4. bind the role using `app:<name>`
5. reload or reinitialize runtime discovery if needed
6. verify the skill registry can resolve it
7. only then treat the upgrade as complete

---

## Bottom line

The biggest lesson is:

**Skill installation is not done when files are downloaded. It is done when the runtime can discover, authorize, and load the skill correctly.**

If Agent Teams closes that gap with native install verification and bindable runtime refs, the next capability upgrade path will be much stronger, faster, and less error-prone.
