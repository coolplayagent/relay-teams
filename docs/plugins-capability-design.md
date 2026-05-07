# Relay Teams Plugins Capability Design

## 1. Goal

Relay Teams should support first-class plugins as installable, versioned capability
bundles. A plugin packages existing Relay Teams extension surfaces into one
runtime unit:

- skills
- roles
- commands
- hooks
- MCP servers
- monitors
- default settings
- supporting scripts and resources

The design is based on the Claude Code plugin reference, but it is intentionally
adapted to the current Relay Teams architecture. Plugins should be an assembly
layer over the existing registries and config loaders, not a parallel capability
system.

Reference material:

- Claude Code plugins reference: `https://code.claude.com/docs/en/plugins-reference`
- Claude Code create plugins guide: `https://code.claude.com/docs/en/plugins`

## 2. Existing Relay Teams Capabilities

The repository already has most runtime capability primitives:

- skills: `src/relay_teams/skills/`
- roles: `src/relay_teams/roles/`
- hooks: `src/relay_teams/hooks/`
- MCP: `src/relay_teams/mcp/`
- commands: `src/relay_teams/commands/`
- monitors: `src/relay_teams/monitors/`
- config paths: `src/relay_teams/paths/`
- server wiring: `src/relay_teams/interfaces/server/container.py`

The plugin layer should therefore focus on:

- plugin manifest parsing and validation
- install, enable, disable, update, and list state
- scope-aware plugin discovery
- namespacing plugin-owned capabilities
- resolving plugin paths and user config variables
- passing plugin component sources into existing registries
- surfacing load diagnostics through API and CLI

## 3. Product Model

A Relay Teams plugin is a directory with optional metadata and one or more
component directories:

```text
my-plugin/
  <relay-config-dir-name>/
    plugin.json
  .claude-plugin/
    plugin.json
  skills/
  roles/
  commands/
  hooks/
    hooks.json
  .mcp.json
  monitors/
    monitors.json
  bin/
  settings.json
  scripts/
  README.md
```

The Relay-native manifest lives under the active Relay Teams config directory
name inside the plugin root. By default this is `.relay-teams/plugin.json`; when
`RELAY_TEAMS_CONFIG_DIR` or runtime configuration points at a different config
directory name, use that directory name instead. Phase 1 also accepts Claude
Code's `.claude-plugin/plugin.json` as an import-compatible alias. All
components live at plugin root so that component paths stay simple and
inspectable.

The manifest is optional for local development plugins that use default
locations, but installed and marketplace plugins should require it.

## 4. Manifest Schema

Add a new package:

```text
src/relay_teams/plugins/
  __init__.py
  plugin_models.py
  manifest_loader.py
  discovery.py
  install_service.py
  registry.py
  config_manager.py
  path_resolution.py
  settings_service.py
  plugin_cli.py
```

Initial Pydantic models:

```python
class PluginScope(str, Enum):
    USER = "user"
    PROJECT = "project"
    PROJECT_LOCAL = "project_local"
    MANAGED = "managed"


class PluginComponentKind(str, Enum):
    SKILLS = "skills"
    ROLES = "roles"
    COMMANDS = "commands"
    HOOKS = "hooks"
    MCP_SERVERS = "mcp_servers"
    MONITORS = "monitors"
    SETTINGS = "settings"


class PluginManifest(BaseModel):
    name: RequiredIdentifierStr
    version: str | None = None
    description: str = ""
    author: PluginAuthor | None = None
    homepage: str | None = None
    repository: str | None = None
    license: str | None = None
    keywords: tuple[str, ...] = ()
    skills: PluginPathSpec | None = None
    roles: PluginPathSpec | None = None  # Claude alias: agents
    commands: PluginPathSpec | None = None
    hooks: PluginJsonOrPathSpec | None = None
    mcp_servers: PluginJsonOrPathSpec | None = None  # Claude alias: mcpServers
    monitors: PluginJsonOrPathSpec | None = None
    settings: PluginJsonOrPathSpec | None = None
    user_config: dict[str, PluginUserConfigField] = Field(default_factory=dict)  # Claude alias: userConfig
    dependencies: tuple[PluginDependency, ...] = ()
```

Rules:

- `name` is required and must be identifier-like.
- component path fields must be relative plugin-root paths beginning with `./`
  when explicitly configured.
- absolute paths and `..` traversal are rejected.
- inline hook, MCP, monitor, and settings configs are accepted after path-based
  loading has established the plugin source boundary.
- `user_config` values are parsed from manifests, persisted in install state,
  and substituted into plugin hook/MCP runtime config. Interactive prompting and
  sensitive value storage are deferred.

Implementation constraints:

- Explicit plugin validation and mutation paths must fail on invalid manifests,
  paths, and capability references.
- Runtime loading of configured plugin directories must degrade safely with
  diagnostics instead of crashing startup.
- Plugin component paths must stay inside the plugin root. Reject absolute paths
  and `..` traversal; do not resolve plugin components from sibling directories
  or global paths.
- Plugin-provided capabilities must be exposed through stable namespaced runtime
  identifiers such as `plugin-name:local-name`.
- Local references inside plugin roles, skills, and hooks should be normalized
  at plugin load boundaries when they target plugin-owned roles, skills, MCP
  servers, commands, or hook agent handlers.
- Preserve the current Claude Code manifest compatibility aliases for local
  plugins: `.claude-plugin/plugin.json`, `agents`, `mcpServers`, and
  `userConfig`.
- Keep Relay Teams runtime names and docs anchored to Relay Teams component
  names.

## 5. Plugin Scopes and Files

Phase 1 does not read plugin install state files. Runtime plugin roots are
configured with `RELAY_TEAMS_PLUGIN_DIRS`, usually from the process environment
or the `.env` file in the resolved app config directory.

Relay Teams should mirror Claude Code's scope model, but the JSON state files
must live under Relay Teams config paths:

- `user` state uses `get_app_config_dir()`. This is normally the user's Relay
  Teams app config directory, and can be redirected with
  `RELAY_TEAMS_CONFIG_DIR`.
- `project` and `project_local` state use the repository root resolved from
  `get_project_root_or_none()`, then append the active Relay Teams config
  directory name. Do not hardcode the default directory name; derive it from the
  active config-dir setting, including `RELAY_TEAMS_CONFIG_DIR` when present.
- `managed` state is read-only policy supplied by an admin-managed config
  source. Runtime loading reads the JSON state file referenced by
  `RELAY_TEAMS_MANAGED_PLUGINS_FILE`; it must not be mixed into user-writable
  files.

Recommended future JSON state files:

```text
<resolved-app-config-dir>/plugins/
  installed/
  cache/
  data/
  plugins.json

<project-root>/<active-relay-config-dir-name>/plugins.json
<project-root>/<active-relay-config-dir-name>/plugins.local.json
```

Scope mapping:

- `user`: available across workspaces, stored under the resolved app config
  directory at `<resolved-app-config-dir>/plugins/plugins.json`.
- `project`: shared with a repository, stored in the resolved project config
  directory at
  `<project-root>/<active-relay-config-dir-name>/plugins.json`.
- `project_local`: local project-only config, stored in
  `<project-root>/<active-relay-config-dir-name>/plugins.local.json`.
- `managed`: read-only enterprise/admin state loaded from
  `RELAY_TEAMS_MANAGED_PLUGINS_FILE` when configured.

The existing `get_project_root_or_none()` helper should resolve project scope.
The current `get_project_config_dir()` returns app config, so plugin project
state must not reuse it blindly. Add explicit plugin path helpers, for example
`get_plugin_user_state_file()` and `get_plugin_project_state_file()`, that
honor the active config-dir settings and keep the default config directory name
as an implementation detail.

## 6. Runtime Registry

Create `PluginRegistry` as a loaded snapshot:

```python
class PluginRecord(BaseModel):
    name: RequiredIdentifierStr
    version: str
    scope: PluginScope
    enabled: bool = True
    root_dir: Path
    data_dir: Path
    source: PluginSource
    user_config: dict[str, JsonValue] = Field(default_factory=dict)
    manifest: PluginManifest


class PluginRegistry(BaseModel):
    plugins: tuple[PluginRecord, ...] = ()
    diagnostics: tuple[PluginDiagnostic, ...] = ()
```

`PluginConfigManager.load_registry()` should be tolerant: invalid persisted
plugin records are skipped with warnings. Explicit user mutations through CLI/API
must stay strict.

This mirrors the repository rule already used by roles, skills, hooks, and MCP:
runtime loads degrade safely; explicit edits validate strictly.

## 7. Namespacing

Plugins must avoid capability collisions.

Recommended first-cut behavior:

- plugin skills are exposed as `plugin-name:skill-name`
- plugin roles are exposed as `plugin-name:role-id`
- plugin commands are exposed as `plugin-name:command-name`
- plugin MCP servers are exposed as `plugin-name:server-name`
- plugin hook source paths include plugin scope and plugin name

This is a breaking semantic difference from current unscoped app skills, but it
keeps plugin capabilities predictable. Standalone app/project skills can keep
their existing unscoped names.

Implementation detail:

- do not mutate files on disk to add prefixes
- apply namespace while loading plugin component definitions
- keep original local names in diagnostics for debugging

## 8. Component Integration

### 8.1 Skills

Current loader:

- `SkillsDirectory.from_config_dirs()`
- `SkillSource`
- `SkillRegistry`

Changes:

- add `SkillSource.PLUGIN`
- add `PluginSkillSourceInfo` or equivalent metadata if source needs plugin name
- let `SkillsDirectory` accept plugin skill roots from `PluginRegistry`
- when loading plugin skills, prefix `Skill.metadata.name` and `Skill.ref`

Important compatibility rule:

- role `skills` references must use runtime names, so plugin roles should refer
  to `plugin-name:skill-name` unless a role owns a local shorthand mapping.

### 8.2 Roles

Current loader:

- `RoleLoader.load_builtin_and_app()`
- `RoleRegistry`
- `RoleSettingsService`

Changes:

- add `RoleConfigSource.PLUGIN`
- add `RoleLoader.load_builtin_app_and_plugins()`
- prefix non-reserved plugin role IDs with `plugin-name:`
- reject plugin attempts to define or override reserved system roles
- plugin roles may reference plugin skills and MCP servers with namespaced refs

Plugin roles should be read-only from the role settings editor. Users can copy a
plugin role into app scope before editing.

### 8.3 Commands

Current loader:

- `discover_commands(app_config_dir, workspace_root)`
- `CommandDiscoverySource`

Changes:

- add `CommandDiscoverySource.PLUGIN`
- add plugin command discovery locations
- prefix plugin command names unless frontmatter already includes a valid
  `plugin-name:*` name

Commands are lower risk than roles because they expand into prompts, but they
still need runtime visibility and source attribution.

### 8.4 Hooks

Current loader:

- user/project/project-local files
- role frontmatter hooks
- skill frontmatter hooks

Changes:

- add `HookSourceScope.PLUGIN`
- load enabled plugin `hooks/hooks.json` or manifest-declared hook configs
- plugin hook commands receive:
  - `RELAY_TEAMS_PLUGIN_ROOT`
  - `RELAY_TEAMS_PLUGIN_DATA`
  - `RELAY_TEAMS_PLUGIN_NAME`
- declared `user_config` substitutions are deferred until plugin enable-time
  configuration exists
- validate plugin agent hooks against namespaced plugin roles, and normalize
  local role references to `plugin-name:role-id`

Precedence:

1. managed hooks
2. project local hooks
3. project shared hooks
4. user hooks
5. plugin hooks
6. role hooks
7. skill hooks

This keeps user and project governance higher than convenience hooks shipped by
plugins.

### 8.5 MCP Servers

Current loader:

- `McpConfigManager.load_registry()` loads app `mcp.json`
- `McpRegistry`

Changes:

- add `McpConfigScope.PLUGIN`
- merge MCP specs from enabled plugins after app config
- prefix plugin server names
- perform variable substitution for plugin paths
- declared `user_config` substitution is deferred until plugin enable-time
  configuration exists
- apply existing proxy env handling after substitution

Plugin MCP servers should not be written into app `mcp.json`; they are effective
runtime specs derived from plugin records.

### 8.6 Monitors

Current monitors are persisted runtime subscriptions, not yet plugin background
process declarations like Claude Code monitors.

First cut:

- define plugin monitor schema but do not auto-start monitor processes.
- expose parsed plugin monitor definitions in diagnostics.

Second cut:

- integrate plugin monitor declarations with background task runtime.
- start `always` monitors at session start.
- start `on-skill-invoke:<skill>` monitors when the namespaced skill is loaded.
- stop plugin monitors when the owning session ends.

### 8.7 Default Settings

Support a narrow `settings.json` first:

```json
{
  "agent": "plugin-name:security-reviewer"
}
```

Rules:

- unknown settings are ignored with warning in runtime load.
- explicit validation rejects unknown keys.
- plugin settings never override explicit user or project session settings.

Initial implementation note: plugin settings are parsed from `settings.json` or
the manifest-declared settings path, exposed through `PluginRegistry`, and
support plugin variable substitution. The initial runtime merge supports the
narrow `agent` setting as the default normal-mode root role for newly created
sessions when the user did not explicitly provide `normal_root_role_id`.
Invalid plugin agent settings are ignored with a warning so persisted plugin
state cannot break startup or session creation.

## 9. Path and Variable Resolution

Support these substitutions in plugin component configs:

- `${RELAY_TEAMS_PLUGIN_ROOT}`
- `${RELAY_TEAMS_PLUGIN_DATA}`
- `${plugin_root}`
- `${plugin_data}`
- `${user_config.key}` (deferred until plugin enable-time configuration exists)
- `${env:VAR}` only for high-trust plugin component configs

Security rules:

- manifest component paths must resolve inside the plugin root.
- plugin data paths must resolve inside the plugin data directory.
- plugin code should not be able to reference sibling plugin cache folders by
  relative traversal.
- command hooks and MCP commands are high-trust local execution and must be
  displayed clearly in plugin diagnostics.

## 10. Installation and Distribution

MVP install sources:

- local directory
- git repository URL
- marketplace JSON entry

Commands:

```text
relay-teams plugin install <source> [--scope user|project|project-local]
relay-teams plugin uninstall <name> [--scope ...] [--prune]
relay-teams plugin enable <name> [--scope ...]
relay-teams plugin disable <name> [--scope ...]
relay-teams plugin update <name> [--scope ...]
relay-teams plugin configure <name> [--scope ...] --set key=value
relay-teams plugin list [--format json] [--available]
relay-teams plugin validate <path>
```

CLI output must follow repository rules: table by default, `--format json` for
query/list commands.

Marketplace support should be separate from plugin loading:

```text
src/relay_teams/plugins/marketplace_models.py
src/relay_teams/plugins/marketplace_service.py
```

The marketplace only resolves available plugin sources and versions. Installed
plugin state remains local Relay Teams config.

Marketplace version entries may include `sha256`; when present, install/update
verifies the materialized plugin source and the installed copy before updating
state.

## 11. API Surface

Add endpoints under `/api/system/configs/plugins`:

- `GET /api/system/configs/plugins`
- `GET /api/system/configs/plugins/runtime`
- `POST /api/system/configs/plugins:validate`
- `POST /api/system/configs/plugins:install`
- `POST /api/system/configs/plugins/{name}:enable`
- `POST /api/system/configs/plugins/{name}:disable`
- `POST /api/system/configs/plugins/{name}:update`
- `DELETE /api/system/configs/plugins/{name}`

The runtime endpoint should return:

- enabled plugin records
- disabled plugin records
- component counts
- diagnostics
- effective component source paths
- required user config fields with sensitive values masked

Interface layers must continue using HTTP/SSE only and must not read plugin files
directly.

## 12. Server Integration

In `ServerContainer`:

1. load `PluginRegistry` after `ensure_app_config_bootstrap()`
2. build MCP registry from app config plus plugin MCP specs
3. build skill registry from standard config dirs plus plugin skill dirs
4. build role registry from builtin, app, and plugin roles
5. build hook service with plugin-aware hook loader
6. build command registry with plugin command locations
7. refresh all runtime dependents on plugin reload

Reload behavior:

- plugin enable/disable/update should refresh plugin registry, MCP, skills,
  roles, hooks, commands, and runtime dependents.
- active runs keep their captured hook/runtime snapshots where those snapshots
  already exist.
- new runs use the new plugin state.

## 13. Validation Strategy

Strict explicit validation:

- invalid manifest fails
- path traversal fails
- unknown component paths fail
- plugin roles referencing unknown tools, MCP servers, or skills fail
- plugin hook agent roles must resolve to subagent-capable roles
- plugin MCP configs must be valid JSON-compatible objects

Initial implementation note: strict install/validate now checks plugin role
tool/MCP/skill references against the effective app config plus the candidate
plugin component sources, and checks plugin hook agent handlers against
namespaced plugin subagent roles. Runtime loading remains tolerant for persisted
dirty state.

Tolerant runtime loading:

- invalid installed plugins are skipped
- invalid component files are skipped when the underlying component loader already
  supports tolerant behavior
- missing dependency plugins disable the dependent plugin at runtime and emit a
  diagnostic
- unknown persisted references are filtered with warning, matching existing role
  and hook behavior

## 14. Observability

Add plugin diagnostics to:

- config status service
- `/api/system/configs/plugins/runtime`
- CLI `plugin list --format json`
- debug logs during server startup and reload

Diagnostics should include:

- plugin name and scope
- manifest path
- component kind
- source path
- severity
- message

The hooks runtime view should include plugin hook source scope and path so users
can diagnose hook behavior without reading plugin directories manually.

## 15. Security Model

Plugins are a high-trust local extension mechanism.

Required controls:

- explicit install source and scope
- no path traversal outside plugin root or data dir
- sensitive `user_config` stored through existing secret infrastructure and not
  written in clear text to plugin state files or registry views
- command hooks, MCP commands, and monitor commands shown in diagnostics
- plugin `bin/` PATH injection is deferred in Phase 1; plugin commands should
  use `${RELAY_TEAMS_PLUGIN_ROOT}` or `${RELAY_TEAMS_PLUGIN_DATA}` explicitly
- plugin cache should be immutable per installed version where practical
- plugin updates should not mutate active run snapshots silently

Non-goals for MVP:

- sandboxing plugin scripts
- remote marketplace trust scoring
- signed plugin verification
- managed enterprise policy plugins

## 16. Implementation Phases

### Phase 1: Local Plugin Runtime

Deliver:

- manifest models and loader
- local runtime loading for development
- plugin-aware skills, roles, hooks, MCP, and commands
- diagnostics and unit tests
- Claude manifest compatibility aliases for `.claude-plugin/plugin.json`,
  `agents`, `mcpServers`, and `userConfig`

This phase proves runtime composition without install/update complexity.

Initial implementation note: Phase 1 uses the `RELAY_TEAMS_PLUGIN_DIRS`
environment variable as the local plugin entrypoint. Multiple plugin roots are
separated by the platform path separator, for example `;` on Windows and `:` on
Linux/macOS. This keeps server daemon and CLI state untouched while proving the
runtime composition path.

### Phase 2: Install State and CLI

Deliver:

- user and project plugin state files
- install, uninstall, enable, disable, list, validate commands
- plugin data directory
- strict user mutation validation
- API runtime listing

This phase makes plugins usable by teams.

### Phase 3: Marketplace and Updates

Deliver:

- marketplace JSON model
- git/local source installers
- version resolution
- update and prune
- dependency handling

This phase makes plugins distributable.

Initial implementation note: Phase 3 keeps marketplace resolution separate from
runtime loading. Marketplace indexes are local JSON files that resolve plugin
names and versions to install sources. Local installs and marketplace installs
copy plugin files into immutable app-config storage under
`<resolved-app-config-dir>/plugins/installed/<plugin-name>/<version>/`; runtime
state points at that installed copy. `plugin update` installs a new version and
updates the state record without deleting older installed copies. `plugin prune`
removes installed versions that are no longer referenced by user, project, or
project-local state. Dependency checks are strict for explicit install/update
and tolerant at runtime: a persisted plugin with missing or mismatched
dependencies is skipped with diagnostics rather than failing startup.

### Phase 4: Monitors and Managed Policy

Deliver:

- plugin monitor process integration
- managed scope
- richer admin diagnostics
- optional plugin signing or integrity checks

This phase should wait until the core plugin runtime is stable.

## 17. Test Plan

Unit tests:

- `tests/unit_tests/plugins/test_manifest_loader.py`
- `tests/unit_tests/plugins/test_path_resolution.py`
- `tests/unit_tests/plugins/test_registry.py`
- `tests/unit_tests/plugins/test_component_sources.py`
- `tests/unit_tests/plugins/test_install_service.py`

Integration tests:

- plugin skill is loadable only under namespaced ref
- plugin role can use plugin skill and plugin MCP server
- plugin hook appears in hook runtime view
- disabling plugin removes its roles, skills, commands, and MCP servers from new
  runtime state
- invalid persisted plugin does not crash server startup

Regression tests:

- explicit role edits still reject unknown plugin capability refs
- runtime role sanitization still filters stale plugin refs with warning
- app/project standalone capabilities keep existing names and behavior

Changes to plugin manifest loading, component source resolution, namespacing, or
skill/role/hook/MCP/command plugin wiring must include focused coverage under
`tests/unit_tests/plugins/`. When plugin wiring touches an existing component
registry or loader, also run the corresponding focused regression tests for that
component.

## 18. Open Questions

- Should Relay Teams eventually make `.claude-plugin/plugin.json` the primary
  marketplace import format, or keep it as a local compatibility alias?
- Should plugin skills be manually invokable by slash command names, or only
  through `load_skill` and role-bound skill routing?
- Should plugin roles be editable in place, or copied into app scope before edit?
- Should plugin dependency version ranges be enforced in MVP or deferred?
- Should plugin MCP servers be available globally, or only to roles that
  reference them?
- Should local development plugins bypass install state only for CLI sessions, or
  also for the server?

## 19. Recommended First Cut

The best first implementation cut is local runtime loading plus namespaced
component registration:

1. implement `PluginManifest`, `PluginRecord`, `PluginRegistry`, and path
   resolution
2. add `PluginConfigManager.load_registry()` with a development plugin directory
   input
3. extend skill, role, hook, MCP, and command loaders to accept plugin component
   sources
4. expose `GET /api/system/configs/plugins/runtime`
5. add tests for namespacing, invalid path rejection, and tolerant runtime skip

This creates a working plugin runtime while leaving marketplace, install, update,
and monitor process management for later phases.
