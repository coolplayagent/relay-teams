# Slash Commands

Relay Teams discovers Markdown slash commands from app and project directories, then resolves them through the backend before a run starts. CLI and frontend callers must use the `/api/*` command endpoints instead of reading command files directly.

## Command Files

Supported directories:

- App scope: `<app_config_dir>/commands`
- Project native scope: `<workspace_root>/.relay-teams/commands`
- Codex-compatible project scope: `<workspace_root>/.codex/commands`
- Claude/OpenSpec-compatible project scope: `<workspace_root>/.claude/commands`
- OpenCode/OpenSpec-compatible project scope: `<workspace_root>/.opencode/command` and `<workspace_root>/.opencode/commands`

Command files are Markdown. The filename is the default command name, and nested files become names such as `review/security`. Project commands override app commands with the same canonical name.

Optional YAML front matter:

```markdown
---
name: review
description: Review the requested file or topic
aliases: [review/topic]
argument_hint: file-or-topic
allowed_modes: [normal]
---

Review {{args}} in {{workspace_root}} from {{cwd}}.
```

Supported template variables:

- `{{args}}`: raw text after the slash command token
- `{{workspace_root}}`: local workspace root, when available
- `{{cwd}}`: caller working directory, or the workspace root when omitted
- `$ARGUMENTS`: OpenSpec/Codex-compatible alias for `{{args}}`

If a command receives arguments but the template does not include an args placeholder, Relay Teams appends the arguments to the expanded prompt. This keeps commands such as `/opsx:propose add-login` from dropping `add-login`.

## OpenSpec Compatibility

Relay Teams consumes OpenSpec command files after OpenSpec has installed them into the project. It does not install OpenSpec, generate command files, or scan global Codex prompt directories.

OpenSpec mappings:

- `.claude/commands/opsx/propose.md` is available as `/opsx:propose`.
- `.opencode/commands/opsx-propose.md` is available as `/opsx-propose` and alias `/opsx:propose`.
- Front matter field `argument-hint` is accepted as `argument_hint`.
- Extra third-party fields such as `category` and `tags` are ignored.

## API

- `GET /api/system/commands?workspace_id=<id>` lists commands visible to the workspace.
- `GET /api/system/commands:catalog` lists global app commands and project commands for every registered workspace.
- `GET /api/system/commands/{name}?workspace_id=<id>` returns a single command by canonical name or alias.
- `POST /api/system/commands` creates a Markdown command file.
- `PUT /api/system/commands` updates an existing Markdown command file by `source_path`.
- `POST /api/system/commands:resolve` resolves a slash command.

Create request:

```json
{
  "scope": "project",
  "workspace_id": "workspace-1",
  "source": "claude",
  "relative_path": "opsx/propose.md",
  "name": "opsx:propose",
  "aliases": ["opsx/propose"],
  "description": "Create an OpenSpec proposal",
  "argument_hint": "<change-id>",
  "allowed_modes": ["normal"],
  "template": "Draft a proposal for {{args}}"
}
```

For `scope: "global"`, omit `workspace_id` and `source`; the file is written under `<app_config_dir>/commands`. For `scope: "project"`, `source` chooses `.claude/commands`, `.codex/commands`, `.opencode/command`, or `.relay-teams/commands`.

Update request:

```json
{
  "source_path": "C:/work/project/.relay-teams/commands/review.md",
  "name": "review",
  "aliases": ["review/change"],
  "description": "Review the requested change",
  "argument_hint": "<change-id>",
  "allowed_modes": ["normal"],
  "template": "Review {{args}}"
}
```

Updates overwrite the existing file at `source_path`. The path must be an existing `.md` file inside a supported global or project command directory.

Resolve request:

```json
{
  "workspace_id": "workspace-1",
  "raw_text": "/opsx:propose add-login",
  "mode": "normal",
  "cwd": "C:/work/project"
}
```

Resolve response includes `matched`, `parsed_name`, `resolved_name`, `args`, `expanded_prompt`, and `expanded_prompt_length`. Unknown slash commands return `matched: false` so callers can preserve the original prompt.

## CLI And Frontend

CLI:

- `relay-teams commands list --workspace .`
- `relay-teams commands show opsx:propose --workspace . --format json`
- `relay-teams -m "/opsx:propose add-login"` resolves the command server-side before creating the run.

Frontend:

- Settings includes a Commands panel that shows global commands and every registered workspace, and can create or edit command files.
- The composer shows `/` autocomplete for discovered commands.
- Sending a known slash command replaces the text part with the expanded prompt while keeping attachments.
- Unknown slash text is sent unchanged.
