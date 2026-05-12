# Agent Teams API Design

## Overview

- Base path: `/api`
- Content type: `application/json`
- Streaming endpoint: `text/event-stream`
- Time fields: ISO 8601 UTC strings
- Orchestration model: task-backed DAG. There is no separate workflow graph
  table or workflow template registry; persisted tasks are the durable
  orchestration graph through node ids and dependency task ids.

Common status codes:
- `200`: success
- `400`: invalid task/run request
- `404`: resource not found
- `409`: runtime conflict
- `503`: runtime capability not configured
- `422`: request validation error

Common validation rules:
- Identifier and reference fields reject blank strings, whitespace-only strings, and the explicit string values `"None"` and `"null"` with `422`.
- Optional identifier fields still accept real JSON `null`.

## Audit APIs

### `GET /audit`

Lists immutable security audit events for external compliance systems.

Query fields:
- `event_type`: optional `file_write`, `shell_command`, or `coordinator_decision`.
- `trace_id`, `run_id`, `session_id`, `task_id`, `role_id`: optional exact-match filters.
- `after_id`: optional cursor, default `0`.
- `since`, `until`: optional ISO 8601 timestamps matched against `occurred_at`; offsets are normalized to UTC before comparison.
- `limit`: optional page size from `1` to `500`, default `100`.

Response fields:
- `items[]`
  - `id`
  - `audit_event_id`
  - `event_type`
  - `trace_id`
  - `run_id`
  - `session_id`
  - `task_id`
  - `instance_id`
  - `role_id`
  - `tool_call_id`
  - `span_id`
  - `parent_span_id`
  - `action`
  - `target`
  - `content_digest`
  - `content_size_bytes`
  - `command`
  - `decision_reason`
  - `outcome`
  - `metadata`
  - `occurred_at`
  - `created_at`
- `next_after_id`: cursor for the next page when more rows are available.

Notes:
- File write audit events store a final content digest and size, not raw content.
- Shell command audit events store the command text and execution context.
- Coordinator decision audit events store the selected delegated task/role channel and the dispatch reason captured from `orch_dispatch_task`.
- `occurred_at` and `created_at` are persisted and filtered as UTC ISO 8601 timestamps.
- The API is read-only. Audit rows are written by backend runtime paths and cannot be mutated by Agent tools.

## Core Concepts

- A run starts from one root task.
- Sessions have a run mode:
  - `normal`: one session-selected root role handles the run directly. The default is `MainAgent`.
  - `orchestration`: the root role is `Coordinator`, and delegation is limited by the selected orchestration preset.
- Session mode and orchestration preset can be changed only before the session starts its first run.
- Every delegated task is a persisted task record under that root task.
- Orchestration is DAG-first: delegated work should be represented as durable
  task nodes with explicit dependency edges whenever the work is long-running,
  staged, or parallelizable.
- Long or spec-heavy non-graph orchestration runs may first create a
  `DelegationPlanner` task. The planner returns a bounded lane plan; Coordinator
  validates it, creates or reuses temporary roles, creates lane task nodes, and
  lets the runtime execute ready nodes concurrently.
- Fixed `graph` presets provide an explicit DAG template. When automatic
  `DelegationPlanner` planning is enabled and the planner role is allowed by the
  preset, Coordinator first gives the planner a chance to produce a dynamic
  bounded DAG. If the planner declines decomposition or planning fails, the
  fixed graph template runs as the fallback path.
- A delegated task binds to exactly one delegated role and one subagent instance on first dispatch.
- Re-dispatching an `assigned` or `stopped` task reuses its bound instance.
- `completed`, `failed`, and `timeout` tasks must be replaced instead of re-dispatched.
- In one session, non-concurrent delegated tasks with the same bound `role_id` reuse the same session-level subagent instance.
- Same-role concurrent dispatch uses an ephemeral clone with its own conversation; the reusable role instance remains the continuity anchor for future non-concurrent tasks.

Task status values:
- `created`
- `assigned`
- `running`
- `stopped`
- `completed`
- `failed`
- `timeout`

## System APIs

### `GET /system/health`

Returns service health.

Response fields:
- `status`
- `version`
- `python_executable`
- `package_root`
- `config_dir`
- `builtin_roles_dir`
- `builtin_skills_dir`
- `role_registry_sanity`
  - `builtin_role_count`
  - `builtin_role_ids`
  - `has_builtin_coordinator`
  - `has_builtin_main_agent`
- `skill_registry_sanity`
  - `builtin_skill_count`
  - `builtin_skill_names`
  - `has_builtin_deepresearch`
- `tool_registry_sanity`
  - `available_tool_count`
  - `available_tool_names`
  - `unavailable_tool_count`
  - `unavailable_tools[]`
    - `name`
    - `error_type`
    - `message`

Notes:
- Health stays `200 ok` for a reachable server even when builtin roles or local
  tools are degraded; inspect the `*_sanity` fields for diagnosis.

### `GET /system/commands`

Lists slash commands visible to a workspace.
Query:
- `workspace_id`

Each command includes `name`, `aliases`, `description`, `argument_hint`, `allowed_modes`, `scope`, and `source_path`.

### `GET /system/commands:catalog`

Lists commands for Settings without depending on the active session workspace.
Response fields:
- `app_commands`: global commands from the app config `commands/` directory.
- `workspaces[]`: each registered workspace with `workspace_id`, `root_path`, and project `commands[]`.

Catalog command entries include `template` so Settings can open any discovered command for editing.

### `POST /system/commands`

Creates a Markdown slash command file.
Request fields:
- `scope`: `global` or `project`
- `workspace_id`: required for `project`
- `source`: project directory, one of `claude`, `codex`, `opencode`, or `relay_teams`
- `relative_path`: relative `.md` file path inside the chosen command directory
- `name`
- `aliases`, optional
- `description`
- `argument_hint`
- `allowed_modes`
- `template`

Global commands are written under the app config `commands/` directory. Project commands are written under the selected workspace command directory and must stay within the workspace writable scope. Existing files return `409`.

### `PUT /system/commands`

Updates an existing Markdown slash command file.
Request fields:
- `source_path`: existing `.md` command file path returned by the catalog
- `name`
- `aliases`, optional
- `description`
- `argument_hint`
- `allowed_modes`
- `template`

The source path must be inside the app config `commands/` directory or a supported workspace command directory. Project command updates must stay within the workspace writable scope.

### `GET /system/commands/{name}`

Returns a command by canonical name or alias.
Query:
- `workspace_id`

Response includes the list fields plus `template`.

### `POST /system/commands:resolve`

Resolves a leading slash command before creating a run.
Request fields:
- `workspace_id`
- `raw_text`
- `mode`, default `normal`
- `cwd`, optional caller working directory

Response fields:
- `matched`
- `raw_text`
- `parsed_name`
- `resolved_name`
- `args`
- `command`
- `expanded_prompt`
- `expanded_prompt_length`

Unknown slash commands return `matched: false` so callers can preserve existing plain-text behavior. Known commands whose `allowed_modes` do not include the requested mode return `400`.

### `GET /system/configs`

Returns runtime config load status for model, MCP, skills, and effective proxy settings.

### `GET /system/configs/ui-language`

Returns the persisted web UI language preference.
Response field:
- `language`: `en-US` or `zh-CN`

### `PUT /system/configs/ui-language`

Persists the web UI language preference used by the frontend language toggle.
Request field:
- `language`: `en-US` or `zh-CN`

### `GET /system/configs/model`

Returns the persisted model config with secret-backed profile API keys rehydrated for UI editing.
Literal profile `api_key` values and secret header values are migrated out of `model.json` into the unified secret store on read.
The response body is a root object whose keys are profile ids and whose values use the same typed profile schema as `PUT /system/configs/model`, without any legacy top-level `config` wrapper.

### `GET /system/configs/model/profiles`

Returns normalized model profiles.
Each profile includes `has_api_key`, the currently stored `api_key` value so the web UI can mask it by default and reveal it on demand, `headers[]` for additional request headers, `is_default` to mark the runtime default profile, optional `context_window` for next-send context preview UI, optional `fallback_policy_id` to bind that profile to a fallback policy, `fallback_priority` to rank it as a fallback candidate, structured `capabilities.input/output.*`, and a derived `input_modalities[]` compatibility field so the UI can label profiles that accept direct media input.
Profiles created from the shared model directory may also include optional `catalog_provider_id`, `catalog_provider_name`, and `catalog_model_name` metadata. These fields are descriptive and do not change provider transport selection.
`provider` currently supports `openai_compatible`, `anthropic`, `bigmodel`, `minimax`, `maas`, `codeagent`, and the internal/testing-only `echo`. `anthropic` means the profile uses an Anthropic Messages API-compatible transport, including marketplace providers such as MiniMax entries that publish an `/anthropic/v1` API. MAAS profiles return `maas_auth` with `auth_source`, `username`, and `has_password` so the web UI can preserve the stored password without echoing it back. CodeAgent profiles return `codeagent_auth`; `auth_method = "sso"` exposes `has_access_token` and `has_refresh_token`, while `auth_method = "password"` exposes `auth_source`, `username`, and `has_password`. The MaaS login endpoint and `app-id`, and the CodeAgent OAuth/login endpoints and inference base URL, are fixed by the backend.
When no profile is explicitly marked default, the backend resolves the default in this order: a profile named `default`, the only configured profile, then the first profile by name.

### `GET /system/configs/model/catalog`

Returns the shared provider/model directory used by the settings UI to prefill model profiles.
The backend fetches `https://models.dev/api.json`, normalizes provider entries and model metadata, adds `runtime_provider` for transport selection, and caches the result in the app config directory.
When a cache exists, the default `GET` path returns it immediately, even after the five-minute freshness window. The settings UI uses this cache-first path only from the add-profile editor, then starts a background refresh. The add-profile catalog also exposes a manual refresh button that calls the forced refresh endpoint.
Query field:
- `refresh`: optional boolean. When `true`, bypasses the cache-first path and attempts to fetch the remote directory.

Response fields:
- `ok`: `true` when the returned directory came from cache or a successful fetch.
- `source_url`: directory source URL.
- `fetched_at`: timestamp for the cached directory, when available.
- `cache_age_seconds`: age of the cached directory, when available.
- `stale`: `true` when the returned cache is older than the freshness window, or when the backend returned stale cache after a refresh/fetch failure.
- `providers[]`: normalized providers. Each provider includes `id`, `name`, optional `api`, optional `doc`, `env[]`, and `models[]`.
- `error_code` and `error_message`: populated when `ok` is `false`.

Model entries include `id`, `name`, optional family/date/limit fields, capability flags, normalized `capabilities`, and `input_modalities[]`.

### `POST /system/configs/model/catalog:refresh`

Forces a refresh of the shared provider/model directory and returns the same response shape as `GET /system/configs/model/catalog`.
If the remote source is unavailable and a previous cache exists, the backend returns the stale cache with `ok = false` and `stale = true`.
Selecting a catalog model in the UI only pre-fills the add-profile editor; persisting the profile still uses `PUT /system/configs/model/profiles/{name}`.

### `PUT /system/configs/model/profiles/{name}`

Upserts a model profile.
Request body may include optional `source_name` to rename an existing profile while preserving its stored API key and secret headers when `api_key` and `headers` are omitted.
If `source_name` does not exist, the backend returns `404`. Profile-level semantic validation failures that occur after request parsing, such as invalid secret-header state or missing MAAS password on first configuration, return `400`.
`provider` accepts `openai_compatible`, `anthropic`, `bigmodel`, `minimax`, `maas`, `codeagent`, and `echo`.
Profiles may also include optional `ssl_verify` to override the global outbound TLS verification default for that model only.
Profiles may include `is_default` to promote that profile to the runtime default; saving one default clears the flag from all others.
Profiles may include optional `context_window` to declare the total model context limit separately from `max_tokens`, which remains the output-token cap when explicitly set. If `max_tokens` is omitted, the backend preserves that unset state and lets the provider decide the default output cap for primary LLM requests.
Profiles may include optional `fallback_policy_id` to enable quota/rate-limit fallback for that profile. The referenced policy id must exist in `model-fallback.json`. Profiles may also include `fallback_priority`; higher values are preferred when the profile is selected as a fallback candidate.
Profiles may include optional `catalog_provider_id`, `catalog_provider_name`, and `catalog_model_name` metadata when the UI prefilled the draft from `GET /system/configs/model/catalog`.
Profiles may include `headers[]`, where each item has `name`, optional `value`, optional `secret`, and optional `configured`.
Profiles must provide at least one auth source: `api_key`, one configured header, `maas_auth` for `provider = "maas"`, or `codeagent_auth` for `provider = "codeagent"`.
When `provider = "maas"`, `maas_auth.auth_source` may be `profile` or `w3` and defaults to `profile`. `profile` requires `username`; `password` is accepted on write but persisted only in the unified secret store. `w3` stores only the W3 reference in the profile and requires the W3 connector to already have a saved username/password. The backend always authenticates against `http://rnd-idea-api.huawei.com/ideaclientservice/login/v4/secureLogin`, always sends `app-id: RelayTeams`, and always uses `http://snapengine.cida.cce.prod-szv-g.dragon.tools.huawei.com/api/v2/` as the MAAS inference base URL.
When `provider = "codeagent"`, `codeagent_auth.auth_method` must be either `sso` or `password`. `sso` accepts the saved-token flags plus an optional `oauth_session_id`; the backend persists the resulting CodeAgent tokens in the unified secret store and does not support W3 auth source. `password` accepts `auth_source = "profile"` or `"w3"` and defaults to `profile`; profile credentials keep `username` in the profile and persist `password` only in the unified secret store, while W3 source stores only the W3 reference and resolves the saved W3 username/password on demand. CodeAgent password login reuses the MaaS secure-login endpoint and request/response contract, but it remains a CodeAgent-only auth flow under `codeagent_auth`. The backend always uses `https://codeagentcli.rnd.huawei.com/codeAgentPro` as the CodeAgent inference base URL. When `context_window` is omitted and the backend recognizes the provider/model pair, it may auto-fill a known context limit during save and runtime load.

### `GET /system/configs/model-fallback`

Returns the model fallback policy config used after a profile exhausts its normal LLM retry budget because of a rate-limit or quota-style error.
The response body contains `policies[]`. Each policy includes:
- `policy_id`
- `name`
- `description`
- `enabled`
- `trigger`
- `strategy`
- `max_hops`
- `cooldown_seconds`

### `PUT /system/configs/model-fallback`

Replaces the full model fallback policy config.
The request body must match the same schema returned by `GET /system/configs/model-fallback`.
Profile writes still validate `fallback_policy_id` strictly, but runtime loading tolerates stale saved references by ignoring the missing policy and logging a warning instead of failing startup.

### `DELETE /system/configs/model/profiles/{name}`

Deletes a model profile.
If the profile does not exist, the backend returns `404`.
If the deleted profile was the current default and other profiles remain, the backend promotes the first remaining profile by name to stay default.

### `PUT /system/configs/model`

Replaces the full model config object.
The request body must be a root object keyed by profile id. Each profile value uses the explicit model-profile schema (`provider`, `model`, `base_url`, optional `api_key`, optional `headers[]`, optional `maas_auth`, sampling fields, and optional `is_default`/`ssl_verify`). The legacy `{ "config": { ... } }` wrapper is rejected.
Literal profile `api_key` values and secret header values are moved into the unified secret store before `model.json` is written.
Unknown profile fields and invalid profile ids are rejected at request validation time. Profile-level semantic validation failures that occur during save return `400`.

### `POST /system/configs/model:probe`

Tests model connectivity for a saved profile and/or draft override.
The backend implementation is async-only for provider network I/O; routes call
the async model connectivity service directly rather than dispatching sync probe
work through a thread bridge.
Draft overrides may include optional `ssl_verify`; effective TLS verification resolves as `override.ssl_verify` -> global `SSL_VERIFY` -> default `false`.
Draft overrides may include `headers[]` and may omit `api_key` when headers are provided. MAAS draft overrides use `maas_auth` instead of `api_key` and perform a login before probing `/chat/completions`; `auth_source = "w3"` resolves saved W3 credentials instead of requiring profile username/password in the override. CodeAgent draft overrides use `codeagent_auth`; `sso` reuses the saved OAuth session/tokens, while `password` logs in with profile username/password or W3 credentials depending on `auth_source`.
If `timeout_ms` is omitted, the backend uses the resolved profile `connect_timeout_seconds` value, or `15s` when no saved profile is involved.

### `POST /system/configs/model:discover`

Fetches the available model catalog for a saved profile and/or draft override.
Catalog and provider discovery fetches use async HTTP clients. The public HTTP
contract is unchanged, but there is no backend sync catalog fetch path.
Draft overrides may omit `model`, but must provide `base_url` and `api_key` or `headers` when `profile_name` is omitted. For `provider = "maas"`, the override must provide `base_url` and `maas_auth`; `auth_source = "w3"` may omit profile username/password when W3 is configured. For `provider = "codeagent"`, the override must provide `codeagent_auth`; password auth may use `auth_source = "w3"`, and the backend still forces the fixed CodeAgent base URL.
When `profile_name` is provided, the request may override `base_url`, `api_key`, `headers`, and `ssl_verify` while reusing the saved credentials for any omitted fields.
If `timeout_ms` is omitted, the backend uses the resolved profile `connect_timeout_seconds` value, or `15s` when no saved profile is involved.
Optional `metadata_policy = "endpoint_only"` disables built-in model-name inference for discovery metadata; this is used by custom endpoint drafts so only provider-returned model metadata is auto-filled. The default `metadata_policy = "allow_inference"` preserves the existing built-in fallback behavior.
`openai_compatible`, `bigmodel`, and `minimax` map this call to `GET {base_url}/models` and return the normalized `models` list sorted and deduplicated. `anthropic` maps this call to `GET {base_url}/models` using Anthropic-compatible request headers. `maas` maps this call to the fixed PromptCenter discovery endpoint after MAAS login, using the returned `X-Auth-Token` plus department info from `userInfo` to build the discovery request payload. `codeagent` resolves a CodeAgent token through either saved SSO credentials or username/password login, then calls the fixed CodeAgent model-discovery endpoint.
For `maas`, the backend merges model ids from top-level `user_model_list` and nested `plugin_config[].config` payloads, filters invalid ids, then returns sorted deduplicated ids in `models[]` and `model_entries[]`.
When the provider exposes per-model context-limit metadata in the catalog payload, the response also includes `model_entries[]` with:
- `model`
- optional `context_window`
- optional `output_limit`
- `capabilities`
- `input_modalities[]`

The settings UI uses `model_entries[].context_window`, `model_entries[].output_limit`, and image-input capability metadata to auto-fill empty profile advanced fields after model discovery. Providers that return only model ids will still populate `models[]`, but those advanced fields remain user-specified.
For a small set of known provider/model pairs, the backend also applies a built-in context-window fallback when the provider returns only model ids.

### `POST /system/configs/model/codeagent/auth:verify`

Verifies whether a saved CodeAgent profile still has usable auth state.
The request body is `{ "profile_name": "<saved profile name>" }`.
Verification resolves and refreshes CodeAgent tokens through the async token
service and sends the lightweight verification request with the shared async
HTTP client.
The backend only accepts saved `codeagent` profiles for this endpoint. It validates the saved auth state by making a lightweight authenticated CodeAgent request with the currently available token. For SSO profiles it retries once through the refresh path after `401/403`. For password profiles it retries once by logging in again with the saved username/password.

The response shape is:

- `status`: `valid`, `reauth_required`, or `error`
- `checked_at`
- optional `detail`

`reauth_required` means the saved CodeAgent credentials can no longer complete an authenticated CodeAgent request, including one retry through refresh or password re-login, and the user must authenticate again. The persisted `codeagent_auth.has_refresh_token` or `codeagent_auth.has_password` flags still mean saved credentials exist; they do not mean the current auth state has already been verified.

### `POST /system/configs/model:reload`

Reloads model config into runtime.
If persisted model config is syntactically valid JSON but semantically invalid for runtime loading, the backend returns `400`.

### `GET /system/configs/proxy`

Returns the saved proxy configuration assembled from app `.env` in the resolved config dir, by default `~/.relay-teams/.env`, plus the unified secret store.
Fields: `http_proxy`, `https_proxy`, `all_proxy`, `no_proxy`, `proxy_username`, `proxy_password`, `ssl_verify`.
Saved proxy URLs are returned without embedded credentials when the configured proxy URLs share the same username/password pair.
If the password was persisted through the secret store, the API rehydrates it into `proxy_password` for editing.
If a user manually forces `user:password@host` into `.env`, runtime loading still supports it and the API can read it back, but the save flow will not write that password back to `.env`.

### `PUT /system/configs/proxy`

Saves proxy values into app `.env` in the resolved config dir, by default `~/.relay-teams/.env`, and the unified secret store, then reloads runtime proxy state immediately.
Blank values remove the corresponding proxy key.
`proxy_username` and `proxy_password` are optional shared credentials.
`ssl_verify` controls the default TLS certificate verification policy for Agent Teams outbound HTTP clients.
When omitted or `null`, the backend removes `SSL_VERIFY` from `.env` and falls back to skipping certificate verification by default.
On save, proxy passwords are persisted through the unified secret store. When a usable system keyring backend exists, the secret store uses keyring; otherwise it falls back to `secrets.json` in the resolved config dir, by default `~/.relay-teams/secrets.json`.
The `.env` file stores proxy URLs without the password portion.
Runtime loading still supports manual `.env` proxy URLs that already contain embedded passwords.
`no_proxy` accepts both comma-separated and semicolon-separated entries. Wildcard host patterns such as `127.*`, `192.168.*`, and the special token `<local>` are supported.

### `GET /system/configs/web`

Returns the saved web tool configuration.
Fields:
- `provider`: always `exa`
- `exa_api_key`: optional Exa key rehydrated from the unified secret store
- `fallback_provider`: `searxng` by default, or `disabled` when automatic fallback is explicitly turned off
- `searxng_instance_url`: the SearXNG base URL used for fallback, defaulting to `https://search.mdosch.de/`
- `searxng_instance_seeds`: the built-in SearXNG seed instances exposed read-only for the settings UI

`websearch` returns structured search hits and accepts optional allow/block domain filters at tool-call time. Exa remains the primary hosted search backend. By default, Exa quota and rate-limit failures automatically retry against SearXNG. Setting `fallback_provider=disabled` explicitly turns that retry path off. The runtime uses `searxng_instance_url` as the first candidate and, when that URL is the shared default, continues through a built-in public SearXNG instance pool sourced from `searx.space` and seed URLs, with short-lived per-instance cooldowns for failing endpoints. Persisted tool state stores only sanitized host/tool metadata and must not retain API-key-bearing URLs. `webfetch` keeps a fixed `5 MiB` limit for textual responses, while binary responses are streamed to the workspace temp directory with a fixed `512 MiB` cap. When the upstream origin proves `Range` support through a valid byte-range probe and returns a strong validator such as `ETag` or `Last-Modified`, binary downloads use segmented fetching and workspace-scoped resume state to continue later calls from the last completed offset.

### `PUT /system/configs/web`

Saves the web tool configuration.
`provider` accepts only `exa`.
`exa_api_key` remains optional because Exa hosted MCP can be used without a key; providing one only raises the rate-limit ceiling.
`fallback_provider` defaults to `searxng`. Set it to `disabled` to opt out of automatic retry after Exa quota and rate-limit failures.
`searxng_instance_url` defaults to `https://search.mdosch.de/`.
The backend persists the Exa API key only through the unified secret store and does not write it back to `.env`.

### `GET /system/configs/github`

Returns the saved GitHub CLI configuration.
Fields:
- `token_configured`: whether a GitHub token is already stored in the unified secret store
- `webhook_base_url`: optional public base URL used to derive `.../api/triggers/github/deliveries`

The GitHub settings UI exists specifically for the bundled `gh` CLI integration used by shell subprocesses. When configured, the runtime injects the token into shell environments as both `GH_TOKEN` and `GITHUB_TOKEN`, and also disables interactive auth/update prompts for non-interactive runs.
The backend never returns the stored GitHub token value from this read endpoint.
`webhook_base_url` must be a publicly reachable `http/https` URL. Localhost and private RFC1918 addresses are rejected because GitHub cannot deliver webhooks to them.
Legacy `GH_TOKEN` / `GITHUB_TOKEN` values still found in `.env` are migrated into the secret store on read and removed from `.env`.

### `GET /system/configs/hooks/runtime`

Returns the runtime-loaded hook view for the current workspace.
The response flattens effective hook handlers across user, project, and project-local config sources so the frontend can show which hooks are actually loaded.
Each entry includes at least the handler name, hook event, matcher, source scope/path, and any scoped filters such as tool names or role IDs.
When no hook files are active, the endpoint returns an empty `loaded_hooks` list.

### `GET /system/configs/plugins`

Returns the persisted plugin configuration registry for user, project,
project-local, managed, and local development plugin sources. The response
includes plugin records, masked `user_config` values for sensitive fields,
component source paths, component counts, settings sources, dependency/runtime
diagnostics, high-trust command audit diagnostics for plugin hooks, MCP servers,
and monitors, and disabled plugin records.

### `GET /system/configs/plugins/runtime`

Returns the runtime plugin registry currently loaded by the server. Local
development roots are configured through `RELAY_TEAMS_PLUGIN_DIRS`, typically
from the process environment or the `.env` file in the resolved app config
directory. Installed plugins are loaded from the Relay Teams plugin state files
and immutable installed-copy directory.

The response includes enabled and disabled plugin records, `component_counts`,
component source paths for skills, roles, commands, hooks, MCP server configs,
parsed monitor definitions, parsed settings sources, required user config fields
with sensitive values masked, and plugin diagnostics. Invalid persisted runtime
plugin entries are reported through diagnostics instead of crashing startup.
Diagnostics also include informational command audit rows for plugin-provided
command hooks, MCP commands, and monitor commands; sensitive `user_config` values
remain masked in public registry views.

### `POST /system/configs/plugins:validate`

Validates a plugin directory without installing it.

Request:

```json
{
  "path": "C:/plugins/quality"
}
```

The response is a plugin registry containing the validated plugin record and
diagnostics. Explicit validation is strict for manifest shape, component paths,
settings schema, and JSON-compatible plugin component configs.

### `POST /system/configs/plugins/marketplace`

Loads a marketplace index so clients can browse available plugins and versions
before installing. `marketplace_provider` defaults to `local_json`; `claude`
loads a Claude official marketplace repository or local checkout.

Request:

```json
{
  "marketplace": "C:/plugins/marketplace.json",
  "marketplace_provider": "local_json",
  "marketplace_source": "",
  "marketplace_ref": "",
  "refresh": false
}
```

The response is a marketplace index with plugin names, descriptions, latest
versions, and version entries. Version entries may include `warnings` and
`unsupported_reason`; unsupported versions remain visible for browsing but cannot
be installed. The frontend uses this endpoint instead of reading marketplace
files directly.

### `POST /system/configs/plugins:install`

Installs a plugin into the requested scope.

Request:

```json
{
  "source": "C:/plugins/quality",
  "scope": "user",
  "enabled": true,
  "source_kind": "local",
  "source_ref": "",
  "marketplace": null,
  "marketplace_provider": "local_json",
  "marketplace_source": "",
  "marketplace_ref": "",
  "version": null
}
```

`source_kind` may be `local` or `git` for direct installs. When omitted, the
server infers git sources from common git URL forms. For direct git installs,
`source_ref` may name a branch, tag, or commit to check out before validation.
Persisted git sources reuse the same `source_ref` on update: commit and tag refs
therefore remain pinned, while an empty ref or a branch can resolve to newer
source content on later updates.
`source` may also be a marketplace plugin name when `marketplace` identifies a
marketplace. For local JSON marketplaces, `marketplace` is the JSON file path.
For Claude marketplaces, `marketplace` is the marketplace name and
`marketplace_source` is a Git URL, GitHub `owner/repo` shorthand, or local
checkout path. Marketplace version entries may include `sha256`, `dependencies`,
and git source `ref` or `sha`; when present, the backend verifies the
materialized source and installed copy before updating plugin state. Claude
marketplace `npm` sources are currently reported as unsupported instead of being
installed. Git-backed marketplace loads and installs reuse the saved proxy
environment when launching git. Claude agent front matter is normalized during
install so official agent files can be loaded as plugin roles; Claude-specific
tool names are not mapped to Relay tools.
Use `marketplace_ref` to pin the marketplace repository to a branch, tag, or
commit. Use `refresh: true` on marketplace browsing requests to discard the
cached checkout and fetch the marketplace again. The manual verification script
`scripts/verify_claude_marketplace_plugins.py` can parse the official Claude
marketplace and optionally install every listed plugin into a temporary config
directory.

### `POST /system/configs/plugins/{name}:configure`

Stores plugin `user_config` values for one installed plugin and scope.

Request:

```json
{
  "scope": "user",
  "user_config": {
    "token": "secret-token"
  }
}
```

Sensitive values are persisted through the unified secret infrastructure and are
not written in clear text to plugin state files or returned in registry views.
Returned sensitive values are masked as configured.

### `POST /system/configs/plugins/{name}:enable`

Enables an installed plugin in the requested scope and reloads plugin-dependent
runtime registries for new runs.

Request:

```json
{
  "scope": "user"
}
```

Required `user_config` values must be configured before the plugin can be
enabled.

### `POST /system/configs/plugins/{name}:disable`

Disables an installed plugin in the requested scope and reloads plugin-dependent
runtime registries for new runs.

Request:

```json
{
  "scope": "user"
}
```

### `POST /system/configs/plugins/{name}:update`

Updates one installed plugin to a requested version or the latest marketplace
version. Older installed copies are retained until pruned.

For direct git installs, update reinstalls from the persisted source and ref.
Pinned commit/tag refs update to the same revision; branch refs and empty refs
are resolved again by git.

Request:

```json
{
  "scope": "user",
  "version": null
}
```

### `DELETE /system/configs/plugins/{name}`

Uninstalls one plugin state record from the requested scope.

Query parameters:

- `scope`: `user`, `project`, or `project_local`
- `prune`: when true, removes installed plugin copies no longer referenced by
  any mutable plugin state file

### `PUT /system/configs/github`

Saves the GitHub CLI configuration.
`token` is optional and write-only. The backend persists it through the unified secret store and removes any managed `GH_TOKEN` / `GITHUB_TOKEN` entries from `.env`.
`webhook_base_url` is optional. When configured, saving it also refreshes repo subscriptions that were using a previous auto-generated callback URL or a local-only callback URL.

### `POST /system/configs/github:probe`

Tests the bundled or system `gh` CLI against `github.com` using the saved token or an optional request override.
The backend runs `gh api user` in a non-interactive subprocess and returns:
- `ok`
- `username`
- `gh_path`
- `gh_version`
- `status_code`
- `exit_code`
- `latency_ms`
- `diagnostics.binary_available`
- `diagnostics.auth_valid`
- `diagnostics.used_proxy`
- `diagnostics.bundled_binary`

The request may include:
- optional `token`
- optional `timeout_ms`

### `POST /system/configs/github/webhook:probe`

Tests the configured public GitHub webhook base URL using `${webhook_base_url}/api/system/health`.
The request may include:
- optional `webhook_base_url`
- optional `timeout_ms`

The response includes:
- `ok`
- `webhook_base_url`
- `callback_url`
- `health_url`
- `final_url`
- `status_code`
- `latency_ms`
- `diagnostics.endpoint_reachable`
- `diagnostics.used_proxy`
- `diagnostics.redirected`

### Public Host Guard

When the server receives a request through a non-local hostname, it only exposes:
- `GET/HEAD /api/system/health`
- `POST /api/triggers/github/deliveries`

All other UI and API routes return `403` by default. This prevents tunnel or reverse-proxy domains from exposing the full web application and sensitive settings pages. Set `AGENT_TEAMS_UNSAFE_ALLOW_PUBLIC_ACCESS=1` only if you intentionally want to publish the full UI/API surface.

### `GET /system/configs/clawhub`

Returns the saved ClawHub configuration.
Fields:
- `token`: optional value rehydrated from the unified secret store

The ClawHub settings currently exist to support authenticated `clawhub` shell workflows and future ClawHub-backed skill operations. When configured, the runtime injects `CLAWHUB_TOKEN` into shell subprocess environments.
When no explicit ClawHub site or registry override exists, China-oriented environments default ClawHub subprocesses to `https://mirror-cn.clawhub.com` through both `CLAWHUB_SITE` and `CLAWHUB_REGISTRY`.
Legacy plaintext `CLAWHUB_TOKEN` values still found in `.env` are migrated into the secret store on read and removed from `.env`.

### `PUT /system/configs/clawhub`

Saves the ClawHub configuration.
`token` is optional. The backend persists it through the unified secret store and removes any managed `CLAWHUB_TOKEN` entries from `.env`.

### `POST /system/configs/clawhub:probe`

Runs a lightweight ClawHub CLI probe using the supplied or persisted token.

The probe currently verifies:
- a token is configured
- `clawhub` is available on `PATH` or can be resolved from npm's global bin directory
- if `clawhub` is missing, the backend attempts to install it automatically with `npm install -g clawhub`, preferring `https://mirrors.huaweicloud.com/repository/npm/`
- `clawhub --cli-version` can run with `CLAWHUB_TOKEN` injected
- the same ClawHub site and registry defaults used by search/install are applied during auth verification
- when a configured site or registry returns a malformed `whoami` payload such as `user: invalid value`, the backend retries once without `CLAWHUB_SITE` and `CLAWHUB_REGISTRY`

Response fields include:
- `ok`
- `clawhub_path`
- `clawhub_version`
- `exit_code`
- `latency_ms`
- `diagnostics.binary_available`
- `diagnostics.token_configured`
- `diagnostics.installation_attempted`
- `diagnostics.installed_during_probe`
- optional `diagnostics.registry`
- `diagnostics.endpoint_fallback_used`
- optional `error_code`
- optional `error_message`

The request may include:
- optional `token`
- optional `timeout_ms`

### `GET /system/configs/clawhub/skills`

Returns app-scoped ClawHub-managed skills discovered under the app config skill directory.

Each item includes:
- `skill_id`: directory id under `~/.relay-teams/skills`
- `runtime_name`: runtime skill name parsed from `SKILL.md` front matter when valid
- `description`
- `ref`: effective runtime skill name from `SKILL.md`, such as `skill-creator`
- `source`: current source bucket for the effective skill, typically `user_relay_teams`
- `directory`
- `manifest_path`
- `valid`
- `error`

Notes:
- `skill_id` is the on-disk directory identity.
- `runtime_name` is the runtime authorization identity used by the skill registry.
- These values may differ.

Request body:
- `slug`
- optional `version`
- optional `force`
- optional `token`

Response fields include:
- `ok`
- `slug`
- optional `requested_version`
- optional `installed_skill`
  - `skill_id`
  - `runtime_name`
  - `description`
  - `ref`
  - `scope`
  - `directory`
  - `manifest_path`
  - `valid`
  - optional `error`
- `clawhub_path`
- `latency_ms`
- `checked_at`
- `diagnostics.binary_available`
- `diagnostics.token_configured`
- `diagnostics.installation_attempted`
- `diagnostics.installed_during_install`
- optional `diagnostics.registry`
- `diagnostics.endpoint_fallback_used`
- optional `diagnostics.workdir`
- `diagnostics.skills_reloaded`
- optional `error_code`
- optional `error_message`

### `GET /system/configs/clawhub/skills/{skill_id}`

Returns one ClawHub-managed app skill snapshot.

In addition to summary fields, the payload includes:
- `instructions`
- `manifest_content`
- `files[]`: relative file path, content, and encoding (`utf-8` or `base64`)

### `PUT /system/configs/clawhub/skills/{skill_id}`

Creates or replaces one app-scoped ClawHub-managed skill directory and reloads the runtime skill registry.

Request body:
- `runtime_name`
- `description`
- `instructions`
- optional `files[]`

The backend always regenerates `SKILL.md` from these structured fields and treats the request as the full desired directory snapshot for managed files.

Validation rules:
- `skill_id` and `runtime_name` must be identifier-like values
- app-scoped runtime names must be unique across other ClawHub-managed app skills
- file paths must be relative and may not target `SKILL.md`

### `DELETE /system/configs/clawhub/skills/{skill_id}`

Deletes one ClawHub-managed app skill directory and reloads the runtime skill registry.

### `GET /system/configs/agent-runtimes`

Returns configured agent runtimes.

Implementation ownership lives in `relay_teams.agent_runtimes`.

Each item includes:
- `agent_id`
- `name`
- `description`
- `protocol`: `acp`, `a2a`, or `cli`
- `transport`: `stdio`, `streamable_http`, or `custom`

### `GET /system/configs/agent-runtimes/{agent_id}`

Returns one saved agent runtime config.

The `transport` field is a discriminated union:
- `stdio`: `command`, `args[]`, optional `env[]`
- `streamable_http`: `url`, optional `headers[]`, optional `ssl_verify`
- `custom`: `adapter_id`, `config`

The `protocol` field selects the runtime protocol:
- `acp`: existing Agent Client Protocol session lifecycle over stdio, HTTP, or custom transport
- `a2a`: Agent2Agent JSON-RPC over HTTP; `streamable_http.url` may point at an Agent Card or direct JSON-RPC endpoint
- `cli`: stdio JSON-RPC execution for open coding agent runtimes such as local Codex app-server; requires `stdio` transport

Binding items under `env[]` or `headers[]` include:
- `name`
- `value`
- `secret`
- `configured`

Notes:
- Secret binding values are not returned on read. Instead, `configured=true` tells the UI that a secret exists in the unified secret store.
- Any ACP-compatible agent runtime may be configured here, including tools such as Claude Code or OpenCode, as long as it speaks the expected transport.
- A2A runtimes follow the public Agent2Agent Agent Card and `message/send` JSON-RPC flow.
- CLI runtimes are process-based JSON-RPC servers over stdio. The backend performs `initialize`, sends `initialized`, creates an ephemeral `thread/start`, submits the composed runtime prompt through `turn/start`, collects assistant output from `item/agentMessage/delta` or completed `agentMessage` items, and waits for `turn/completed`.
- Bare `codex` CLI configs are launched as `codex app-server --listen stdio://`. Legacy `codex exec` prompt flags are not forwarded to app-server; approval policy is set through JSON-RPC thread/turn params.
- `stdio` runtimes always start inside a workspace. Prompt execution uses the active session workspace; `/system/configs/agent-runtimes/{agent_id}:test` uses the default workspace workdir so relative CLI command paths are validated from the same kind of runtime cwd.

### `PUT /system/configs/agent-runtimes/{agent_id}`

Upserts one agent runtime config.

Rules:
- Path `agent_id` must match body `agent_id`.
- Secret env/header values are persisted only through the unified secret store.
- Sending a secret binding with `configured=false` and no value removes the stored secret for that binding.

### `DELETE /system/configs/agent-runtimes/{agent_id}`

Deletes one saved agent runtime config and its stored secrets.

### `POST /system/configs/agent-runtimes/{agent_id}:test`

Tests connectivity against the saved runtime-resolved agent runtime config.

For CLI runtimes, the probe starts the process in the default workspace workdir and resolves relative command paths from that cwd. For A2A direct JSON-RPC runtimes, the probe requires a JSON-RPC 2.0 response with a matching id and rejects `-32601` method-not-found because that endpoint does not implement A2A `tasks/get`.

Response fields:
- `ok`
- `message`
- `protocol`
- optional `protocol_version`
- optional `protocol_version_text`
- optional `agent_name`
- optional `agent_version`

### `POST /system/configs/proxy:reload`

Reloads effective proxy env into runtime.
The reload source is the current effective merged environment, not only app-saved `.env` values.
This updates process-level proxy variables for future HTTP requests and shell/MCP subprocesses, clears removed proxy keys, and refreshes MCP runtime state.
Remote MCP discovery and connection clients use the per-server effective `env`
proxy values instead of reading process-global proxy variables at connection
time.

### `POST /system/configs/mcp:reload`

Reloads MCP config into runtime.
For stdio MCP servers launched through `uvx` or `uv tool run`, the backend clears the relevant `uv` package cache before rebuilding runtime state so newly added MCP tools are visible on the next load.
Reloading MCP config reconciles the MCP discovery cache by server fingerprint:
unchanged `ready`, `loading`, and `failed` entries are preserved, while new,
enabled, or changed entries queue asynchronous tool discovery. The reload
response does not wait for remote `initialize` or `tools/list` calls.
The server also watches the app `mcp.json` file for external edits and triggers
the same reload path after a debounced `mtime` / size change. Invalid JSON file
edits are logged and ignored so the previous runtime registry and discovery
cache stay active.
If persisted MCP config is semantically invalid, the backend returns `400`.

### `POST /system/configs/skills:reload`

Reloads skills config into runtime.
If persisted skills config is semantically invalid, the backend returns `400`.

### `GET /system/configs/notifications`

Returns notification rules by event type.
Each rule includes:
- `enabled`
- `channels[]`: `browser`, `toast`, `feishu`
- `feishu_format`: `text` or `card`

### `PUT /system/configs/notifications`

Replaces notification rules.
The request body is the `NotificationConfig` object directly; the backend no longer accepts an extra top-level `config` field. Unknown top-level fields are rejected.
Notes:
- `feishu` delivery is best-effort and only applies when the session/run has Feishu chat context.
- Feishu credentials are resolved from the Feishu gateway account bound to that session, not from `notifications.json`.

### `GET /system/configs/general`

Returns saved General settings that apply to future runs started from the web UI.

Fields:
- `shell_safety_policy_enabled`

### `PUT /system/configs/general`

Replaces saved General settings.

Fields:
- `shell_safety_policy_enabled`

### `GET /system/configs/orchestration`

Returns global orchestration settings.

Response fields:
- `default_orchestration_preset_id`
- `presets[]`
  - `preset_id`
  - `name`
  - `description`
  - `role_ids`
  - `orchestration_prompt`
  - `policy`
    - `max_orchestration_cycles`
    - `max_parallel_delegated_tasks`
    - `auto_plan_long_tasks`
    - `planner_role_id`
    - `coordinator_inline_budget_steps`
    - `max_temporary_roles_per_run`
    - `prefer_temporary_roles_for_long_tasks`
  - `graph`, optional fixed DAG template

### `PUT /system/configs/orchestration`

Replaces global orchestration settings.
The request body is the `OrchestrationSettings` object directly; the backend no longer accepts an extra top-level `config` field. Unknown top-level fields are rejected.

Rules:
- `presets[].role_ids` may contain only normal roles; reserved system roles are rejected.
- `presets[].policy.max_orchestration_cycles` accepts `0..64`.
- `presets[].policy.max_parallel_delegated_tasks` accepts `0..16`; `0` disables automatic delegated task execution for simple direct-answer presets.
- `presets[].policy.planner_role_id` defaults to `DelegationPlanner`; when automatic planning is enabled, this role is executed through the normal delegated task runtime path.
- `presets[].policy.max_temporary_roles_per_run` accepts `0..16` and bounds automatic DelegationPlanner temporary role proposals.
- `graph` presets are fixed DAG templates with planner-first preflight when
  `auto_plan_long_tasks` is enabled and `planner_role_id` is listed in
  `role_ids`. Non-graph presets can still produce a dynamic task DAG through
  automatic `DelegationPlanner` planning or through Coordinator-created task
  nodes.
- `graph.nodes[].node_id` must not use the reserved `auto_lane_` prefix, which
  identifies dynamic DelegationPlanner lane tasks during resume.
- The default preset id must match one existing preset.
- `MainAgent` and `Coordinator` base role prompts are edited through `/roles/configs/*`, not this config.
- `orchestration_prompt` is appended only for `Coordinator` in `orchestration` session mode.

### `GET /system/configs/workspace/ssh-profiles`

Returns the saved SSH profile list used by workspace mounts.
Each item includes:
- `ssh_profile_id`
- `host`
- `username` for profiles created or updated through the API
- optional `port`
- optional `remote_shell`
- optional `connect_timeout_seconds`
- optional `private_key_name`
- `has_password`
- `has_private_key`
- `created_at`
- `updated_at`

Passwords and private key bodies are not echoed by list/get/upsert responses.
Legacy rows that predate the username requirement may return `username: null`; they must be edited before probe, mount, or remote command execution.

### `GET /system/configs/workspace/ssh-profiles/{ssh_profile_id}`

Returns one SSH profile with the same response shape as the list endpoint.

### `POST /system/configs/workspace/ssh-profiles/{ssh_profile_id}:reveal-password`

Returns the stored SSH password for one profile:

```json
{
  "password": "optional-password"
}
```

The response returns `null` when no password is stored. Private key bodies remain non-readable through the settings API.

### `POST /system/configs/workspace/ssh-profiles:probe`

Tests whether an SSH profile can open a remote SSH session. The probe is used by both the saved profile list and the profile editor.

Request body for a saved profile:

```json
{
  "ssh_profile_id": "prod",
  "timeout_ms": 15000
}
```

Request body for unsaved editor values or a saved profile with draft overrides:

```json
{
  "ssh_profile_id": "prod",
  "override": {
    "host": "prod-alias",
    "username": "deploy",
    "password": "optional-password",
    "port": 22,
    "remote_shell": "/bin/bash",
    "connect_timeout_seconds": 15,
    "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\n...\n-----END OPENSSH PRIVATE KEY-----",
    "private_key_name": "id_ed25519"
  },
  "timeout_ms": 15000
}
```

Rules:
- Either `ssh_profile_id` or `override` is required.
- `username` is required for draft overrides and saved profiles used by the probe.
- When `ssh_profile_id` is supplied and `override.password` or `override.private_key` is omitted, the probe reuses the stored secret for that field.
- When no password or private key is available, the probe may use host system SSH authentication material such as `ssh-agent`, default identities, and SSH config. The login username still comes from the SSH profile or draft override.
- The server shells out to `ssh`, writes any draft private key to a temporary 0600 identity file, and removes temporary files after the probe.

Response body:

```json
{
  "ok": true,
  "ssh_profile_id": "prod",
  "host": "prod-alias",
  "port": 22,
  "username": "deploy",
  "latency_ms": 44,
  "checked_at": "2026-04-21T00:00:00Z",
  "diagnostics": {
    "binary_available": true,
    "host_reachable": true,
    "used_password": false,
    "used_private_key": false,
    "used_system_config": true,
    "exit_code": 0
  },
  "error_code": null,
  "error_message": null,
  "retryable": false
}
```

### `PUT /system/configs/workspace/ssh-profiles/{ssh_profile_id}`

Upserts one SSH profile.

Request body:

```json
{
  "config": {
    "host": "prod-alias",
    "username": "deploy",
    "password": "optional-password",
    "port": 22,
    "remote_shell": "/bin/bash",
    "connect_timeout_seconds": 15,
    "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\n...\n-----END OPENSSH PRIVATE KEY-----",
    "private_key_name": "id_ed25519"
  }
}
```

Rules:
- `host` is required and whitespace-only values are rejected.
- `username` is required and whitespace-only values are rejected.
- `password` is optional.
- `private_key` is optional and may be pasted or imported from the settings UI.
- `password` and `private_key` are stored through the unified secret store, not in the SQLite row.
- Omitting `password` or `private_key` on edit preserves the existing stored secret for that field.
- Responses only return `has_password`, `has_private_key`, and `private_key_name`; they do not return the secret contents.

### `DELETE /system/configs/workspace/ssh-profiles/{ssh_profile_id}`

Deletes one SSH profile and its stored password/private key secrets.

### `GET /system/configs/environment-variables`

Returns environment variables grouped by `system` and `app` scope.
`system` is read-only and reflects the effective runtime environment currently visible to the Agent Teams server and newly spawned child processes.
`app` is editable and is stored across `.env` in the resolved config dir, by default `~/.relay-teams/.env`, and the unified secret store.
Sensitive-looking app keys such as `*_API_KEY`, `*_TOKEN`, `*_SECRET`, and `*_PASSWORD` are stored in the secret store and excluded from `.env`.
The server loads app environment values at startup and watches the app `.env` file for external edits, so saved or manually edited app values take effect without restarting the server.
Each record includes `key`, `value`, `scope`, and `value_kind` (`string` or `expandable`).

### `PUT /system/configs/environment-variables/{scope}/{key}`

Upserts one environment variable in the target `scope`.
Request body fields:
- `value`: raw variable value
- optional `source_key`: rename from an existing key before saving the new key

`app` writes preserve unrelated `.env` lines and comments where possible.
Sensitive app keys are written to the secret store instead of `.env`, and any managed plaintext copy is removed from `.env`.
The backend preserves the existing value kind on edit or rename when possible, otherwise it infers `expandable` when the value contains `%NAME%` placeholders.
Saving an app variable also reloads runtime model config immediately, so `model.json` placeholders such as `${OPENAI_API_KEY}` take effect without a restart.
Saving an app variable also refreshes MCP runtime state and skills runtime state for future tool and skill use, so newly spawned stdio MCP subprocesses observe the updated environment without a server restart.
Changes to `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, or `SSL_VERIFY` also trigger the same proxy runtime refresh side effects as the dedicated proxy settings API.
`system` scope is read-only and returns a user-facing validation error on mutation.

### `DELETE /system/configs/environment-variables/{scope}/{key}`

Deletes one app environment variable from the target scope.
Deleting an app variable also refreshes MCP runtime state and skills runtime state for future tool and skill use.
Deleting a missing key returns a user-facing validation error.

### `POST /system/configs/web:probe`

Tests whether a target `http` or `https` URL is reachable under the current proxy and global SSL settings.
The request may also include `proxy_override` with `http_proxy`, `https_proxy`, `all_proxy`, `no_proxy`, `proxy_username`, `proxy_password`, and `ssl_verify` to run a one-shot probe against unsaved form values.
The backend uses `HEAD` first and falls back to `GET` when the target does not support `HEAD`.
Any HTTP response (`2xx` through `5xx`) counts as reachable.
Only transport-level failures such as timeout, DNS, TLS, or proxy handshake errors return `ok=false`.

## Session APIs

### `POST /sessions`

Creates a session.

Request:

```json
{
  "session_id": null,
  "workspace_id": "default",
  "metadata": {
    "title": "Customer Support",
    "source_label": "Group Chat",
    "source_icon": "im",
    "custom_metadata": {"project": "demo"}
  }
}
```

Notes:
- New sessions default to `session_mode = "normal"`.
- New sessions default to `normal_root_role_id = "MainAgent"`.
- New sessions also store the current default orchestration preset id so they can be switched to orchestration before the first run.
- Omitting `session_id` or sending `session_id = null` auto-generates a session id. Sending `"None"` or `"null"` as a string is rejected with `422`.
- `metadata` uses the same explicit fields as session metadata updates: `title`, `title_source`, `source_label`, `source_icon`, and `custom_metadata`.
- `custom_metadata` cannot overwrite reserved session keys such as `title`, `title_source`, `source_label`, `source_icon`, `source_kind`, `source_provider`, or any key with the `feishu_` prefix.
- `title_source` requires `title`. When `title` is provided and `title_source` is omitted, the backend stores `title_source = "manual"`.

### `GET /sessions`

Lists sessions.

### `GET /sessions/{session_id}`

Gets one session.

Response fields also include:
- `session_mode`
- `normal_root_role_id`
- `orchestration_preset_id`
- `started_at`
- `can_switch_mode`

### `PATCH /sessions/{session_id}`

Updates session metadata with an explicit patch body.

Request:

```json
{
  "title": "Renamed Session",
  "title_source": "manual",
  "source_label": "Customer Support",
  "source_icon": "support-bot",
  "custom_metadata": {
    "project": "demo",
    "ticket_id": "A-1024"
  }
}
```

Rules:
- The request body must contain at least one field.
- `title`, `title_source`, `source_label`, and `source_icon` are optional patch fields. Sending `null` clears that value.
- `title_source` requires the resulting session title to be set. Sending `title_source` without a non-empty `title` returns `422`.
- `custom_metadata` replaces only the caller-managed custom metadata subset. System-managed metadata keys remain intact.
- `custom_metadata` keys must be non-empty and cannot overwrite reserved keys such as `title`, `title_source`, `source_label`, `source_icon`, `source_kind`, `source_provider`, or any key with the `feishu_` prefix.
- `custom_metadata` values must be non-empty strings.

### `PATCH /sessions/{session_id}/topology`

Updates session run mode, normal-mode root role, and orchestration preset.

Request:

```json
{
  "session_mode": "normal",
  "normal_root_role_id": "Crafter",
  "orchestration_preset_id": null
}
```

Rules:
- Only sessions that have not started their first run may be changed.
- `normal_root_role_id` is used only when `session_mode = "normal"`.
- `normal_root_role_id` may be `MainAgent` or any non-system role. `Coordinator` is rejected.
- `orchestration_preset_id` is required when `session_mode = "orchestration"`.
- `orchestration_preset_id` is ignored when `session_mode = "normal"`.

### `DELETE /sessions/{session_id}`

Deletes a session and all persisted runtime data under that session.
The bound workspace record is preserved.
Request body may include:
- optional `force`: required when the session still has an active or recoverable run
- optional `cascade`: required when persisted session-scoped data already exists and the caller wants the backend to remove messages, tasks, agents, runtime rows, background-task logs, bindings, and other related session data
If a session still has related data and `cascade` is omitted or `false`, the backend returns `409` instead of performing an implicit cascading delete.

### `GET /sessions/{session_id}/rounds`

Returns paged round projections.
Query parameters:
- optional `limit`: number of round projections to return for the normal paged view
- optional `cursor_run_id`: cursor for loading older normal round projections
- optional `timeline=true`: returns the full lightweight round index for timeline navigation; this mode ignores `limit` and `cursor_run_id`, sets `has_more = false`, and omits heavy message/task mapping fields such as `coordinator_messages`, `tasks`, `instance_role_map`, `role_instance_map`, `task_instance_map`, and `task_status_map`
- optional `summary=true`: returns the paged lightweight round index for fast session switching; this mode honors `limit` and `cursor_run_id`, keeps pagination fields, and omits the same heavy fields as `timeline=true`

Response shape:

```json
{
  "items": [
    {
      "run_id": "run-1",
      "created_at": "2026-03-11T12:00:00Z",
      "intent": "Implement endpoint X",
      "primary_role_id": "Coordinator",
      "coordinator_messages": [],
      "tasks": [
        {
          "task_id": "task-2",
          "title": "Write API code",
          "assigned_role_id": "spec_coder",
          "role_id": "spec_coder",
          "status": "completed",
          "assigned_instance_id": "inst-2",
          "instance_id": "inst-2"
        }
      ],
      "instance_role_map": {"inst-2": "spec_coder"},
      "role_instance_map": {"spec_coder": "inst-2"},
      "task_instance_map": {"task-2": "inst-2"},
      "task_status_map": {"task-2": "completed"},
      "retry_events": [
        {
          "occurred_at": "2026-03-11T12:00:04Z",
          "instance_id": "inst-2",
          "role_id": "spec_coder",
          "attempt_number": 2,
          "total_attempts": 6,
          "retry_in_ms": 2000,
          "phase": "scheduled",
          "is_active": true,
          "error_code": "429",
          "error_message": "Rate limited"
        }
      ],
      "pending_tool_approvals": [],
      "pending_tool_approval_count": 0,
      "run_status": "running",
      "run_phase": "idle",
      "is_recoverable": true,
      "microcompact": {
        "applied": true,
        "estimated_tokens_before": 139920,
        "estimated_tokens_after": 9009,
        "compacted_message_count": 1,
        "compacted_part_count": 3
      },
      "clear_marker_before": {
        "marker_id": "marker-1",
        "marker_type": "clear",
        "created_at": "2026-03-11T11:59:30Z",
        "label": "History cleared"
      },
      "compaction_marker_before": {
        "marker_id": "marker-2",
        "marker_type": "compaction",
        "created_at": "2026-03-11T12:10:00Z",
        "label": "History compacted (rolling summary)"
      }
    }
  ],
  "has_more": false,
  "next_cursor": null
}
```

Notes:
- `tasks` contains delegated task summaries only. The root coordinator task is omitted.
- `task_instance_map` is the authoritative mapping when multiple tasks use the same `role_id`.
- `primary_role_id` is the resolved root role for that round. It matches the session topology by default, or the one-run `target_role_id` override when the run was created through direct `@Role` chat.
- `retry_events` reflects the current retry card for the run timeline. The array is empty when no retry state should be shown and contains at most one entry.
- Active retry countdowns are anchored to the event `occurred_at` timestamp, not to the browser receive time.
- `retry_events[].phase` is `scheduled` while backoff is pending and `failed` when retries have been exhausted.
- `microcompact` is present when the run used request-level prompt-view compaction. It is not a persisted history boundary and does not imply that a history marker was written.
- `microcompact.estimated_tokens_before/after` reflects history-token estimates around the request-level microcompact pass, not the full prompt token total.
- `microcompact` reflects the latest model-step payload for that run. If a later attempt in the same run reports `microcompact_applied = false`, the round projection clears the stale badge state instead of keeping the older value.
- `todo` is present when the run has a persisted run-scoped todo snapshot. It mirrors `GET /runs/{run_id}/todo`.
- `clear_marker_before` is present on the first round after a session history clear boundary. The frontend uses it to render a divider and collapse older segments by default.
- `compaction_marker_before` is present on the first round whose coordinator conversation continues after an automatic history compaction boundary. The frontend uses it to render a non-collapsing divider.
- `compaction_marker_before.label` is `History compacted (rolling summary)` when the marker metadata reports `compaction_strategy = rolling_summary`; older markers without strategy metadata may still render as `History compacted`.
- `microcompact` and `compaction_marker_before` may both be present on the same round. In that case the request first used microcompact and then also crossed a persisted full-compaction boundary.
- Automatic history compaction is logical only. Older messages are marked hidden-from-context for model reads, but remain available to raw/history endpoints.
- A terminal `run_completed` event with non-empty `output` is a valid final-answer source. The live frontend must render that terminal output when no `text_delta` or `output_delta` has already produced final coordinator content for the run.
- A terminal `run_failed` event with `completion_reason = "assistant_response"` follows the same final-output rule; other failed terminal outputs remain diagnostic and are not treated as final answers.
- The round projection synthesizes an assistant text message from terminal final output when persisted coordinator history does not already contain that final assistant text. This also covers runs that persisted intermediate tool or thinking history but lost the final answer row.

### `GET /sessions/{session_id}/rounds/{run_id}`

Gets one round projection.

### `GET /sessions/{session_id}/recovery`

Returns active run recovery state, pending tool approvals, pending user questions, managed background task state, paused subagent state, and round snapshot.

`active_run` also includes:
- `last_event_id`
- `checkpoint_event_id`
- `pending_tool_approval_count`
- `pending_user_question_count`
- `background_task_count`
- `stream_connected`
- `should_show_recover`
- `primary_role_id`

For `running` or `queued` recoverable runs, the frontend uses these event ids to automatically reconnect the SSE stream without a manual "Connect Stream" action.
`round_snapshot` mirrors the same round projection contract as `/sessions/{session_id}/rounds/{run_id}`, including `primary_role_id` and any persisted `todo` snapshot.
`round_snapshot.background_task_count` mirrors the current managed background task count for the active run.
When a run is waiting for an `ask_question` answer, the public `active_run.phase` is `awaiting_manual_action`.

`pending_user_questions[]` entries include:
- `question_id`
- `run_id`
- `session_id`
- `task_id`
- `instance_id`
- `role_id`
- `tool_name`: currently always `ask_question`
- `status`: `requested | answered | timed_out | completed`
- `questions[]`
  - `header`
  - `question`
  - `options[]`
    - `label`
    - `description`
  - `multiple`
  - `placeholder`
- `answers[]`
  - `selections[]`
    - `label`
    - `supplement`
- `created_at`
- `updated_at`
- `resolved_at`

Notes:
- The `ask_question` tool uses one batched request per tool call, so `questions[]` may contain multiple prompts.
- Each prompt must provide at least one caller-defined option in `options[]`.
- Every question automatically includes a synthetic `None of the above` option in `options[]`.
- `answers[]` follows the same validation rules as `POST /runs/{run_id}/questions/{question_id}:answer`.

`background_tasks[]` entries include:
- `background_task_id`
- `run_id`
- `session_id`
- `kind`: `command | subagent`
- `instance_id`
- `role_id`
- `tool_call_id`
- `title`
- `command`
- `cwd`
- `execution_mode = "background"`
- `status`: `running | blocked | stopped | failed | completed`
- `tty`
- `timeout_ms`
- `exit_code`
- `recent_output[]`
- `output_excerpt`
- `log_path`
- `subagent_role_id`
- `subagent_run_id`
- `subagent_task_id`
- `subagent_instance_id`
- `created_at`
- `updated_at`
- `completed_at`
- `completion_notified_at`

### `GET /sessions/{session_id}/agents`

Lists one reusable session-level agent instance per delegated role in the session. Each entry includes the latest runtime system prompt snapshot and runtime tools JSON captured before the most recent subagent execution step.

Notes:
- This endpoint continues to back the orchestration/legacy right-rail agent list.
- Ephemeral same-role clones are excluded from this projection.
- Normal-mode `spawn_subagent` child sessions are excluded from this projection.

Response fields include:
- `instance_id`
- `role_id`
- `status`
- `created_at`
- `updated_at`
- `runtime_system_prompt`
- `runtime_tools_json`

### `GET /sessions/{session_id}/subagents`

Lists normal-mode `spawn_subagent` runs as instance-level child-session projections.

Notes:
- Returns only subagent instances whose `run_id` is a normal-mode synthetic `subagent_run_*`.
- Results are instance-scoped and are not collapsed by `role_id`, so multiple subagent runs under the same role are all returned.
- Intended for the left sidebar child-session navigation, not the orchestration right rail.

Response fields include:
- `instance_id`
- `role_id`
- `run_id`
- `status`
- `run_status`
- `run_phase`
- `last_event_id`
- `checkpoint_event_id`
- `stream_connected`
- `created_at`
- `updated_at`
- `conversation_id`
- `title`
- `runtime_system_prompt`
- `runtime_tools_json`

### `DELETE /sessions/{session_id}/subagents/{instance_id}`

Deletes one normal-mode child-session subagent projection and its persisted instance/run history.

Notes:
- Only applies to normal-mode synthetic `subagent_run_*` instances listed by `GET /sessions/{session_id}/subagents`.
- Deletes the subagent instance, its run/task records, messages, run state, token usage, related compaction markers, and matching subagent background-task records.
- Returns `409` if the target subagent or its matching background task is still running.

### `GET /sessions/{session_id}/events`

Lists persisted business events in the session.

### `GET /sessions/{session_id}/messages`

Lists persisted messages in the active session segment only. Rows before the latest logical `clear` marker are excluded from this endpoint, and rows marked hidden-from-context by automatic compaction are also excluded.

### `GET /sessions/{session_id}/agents/{instance_id}/messages`

Lists the raw history timeline for one agent instance, including:
- original message rows, even when they were marked hidden-from-context by automatic compaction
- session `clear` dividers
- conversation-local `compaction` dividers

Notes:
- History markers are resolved against the instance's persisted `conversation_id`.
- This matters for normal-mode subagent child sessions, whose instance conversation may differ from the legacy `session_id + role_id` conversation id.

Response entries are ordered oldest to newest and use `entry_type`:
- `message`: original persisted message row. Includes `hidden_from_context`, `hidden_reason`, `hidden_at`, and `hidden_marker_id`.
- `marker`: logical history divider with `marker_id`, `marker_type`, `created_at`, and `label`.

### `GET /sessions/{session_id}/tasks`

Lists delegated tasks in the session.

Task summaries include the persisted lifecycle view plus spec/evidence metadata used by the subagent panel:
- `spec_artifact_id`
- `spec_source_task_id`
- `spec_summary`
- `spec_strictness`
- `evidence_bundle`

### `GET /sessions/{session_id}/token-usage`

Returns aggregated token usage for the active session segment, grouped by `role_id`. The totals include the coordinator agent and every subagent run recorded under the same `session_id` after the latest logical `clear` marker. Response totals expose `total_cached_input_tokens` and `total_reasoning_output_tokens` alongside the existing input/output/request counters. Legacy local rows with missing or `NULL` counters are normalized to `0` before aggregation.

### `GET /sessions/{session_id}/runs/{run_id}/token-usage`

Returns token usage for a single run, grouped by agent instance. Response totals expose `total_cached_input_tokens` and `total_reasoning_output_tokens` alongside the existing input/output/request counters. Legacy local rows with missing or `NULL` counters are normalized to `0` before aggregation.

### `GET /sessions/{session_id}/media`

Lists session-scoped media assets as normalized `media_ref` content parts.

### `POST /sessions/{session_id}/media`

Uploads one image, audio, or video file into the session media store.

Response fields mirror a typed content part:
- `kind = "media_ref"`
- `asset_id`
- `session_id`
- `modality = "image" | "audio" | "video"`
- `mime_type`
- `url`
- optional metadata such as `size_bytes`, `width`, `height`, and `duration_ms`

### `GET /sessions/{session_id}/media/{asset_id}`

Returns media metadata as the same normalized `media_ref` content part used by runs, ACP, and the frontend.

### `GET /sessions/{session_id}/media/{asset_id}/file`

Streams a locally stored session media file or redirects to the saved remote URL for remote references.

## Run APIs

### `POST /runs`

Creates a run.

Request:

```json
{
  "session_id": "session-1",
  "input": [
    {
      "kind": "text",
      "text": "Implement endpoint X"
    }
  ],
  "run_kind": "conversation",
    "generation_config": null,
    "execution_mode": "ai",
    "yolo": false,
    "shell_safety_policy_enabled": true,
    "target_role_id": "Architect",
  "thinking": {
    "enabled": false,
    "effort": null
  },
  "orchestration_policy": {
    "max_orchestration_cycles": 8,
    "max_parallel_delegated_tasks": 4,
    "auto_plan_long_tasks": true,
    "planner_role_id": "DelegationPlanner",
    "coordinator_inline_budget_steps": 2,
    "max_temporary_roles_per_run": 5,
    "prefer_temporary_roles_for_long_tasks": true
  }
}
```

Notes:

- `input` is now the canonical run payload. It is an ordered array of typed content parts:
  - `{"kind":"text","text":"..."}`
  - `{"kind":"media_ref", ...}`
  - `{"kind":"inline_media", ...}` for small ingress-only image/audio payloads that are normalized immediately into stored `media_ref` assets
- Conversation runs may combine text and pasted images in the same `input` array.
- For image paste ingress, the frontend sends `inline_media` parts with:
  - `modality: "image"`
  - `mime_type`
  - `base64_data`
  - optional `name`, `size_bytes`, `width`, and `height`
- `run_kind` supports:
  - `conversation`
  - `generate_image`
  - `generate_audio`
  - `generate_video`
- Native media generation also enters through `/api/runs`; the backend chooses the provider-native generation branch from `run_kind`.
- `generation_config` is optional and modality-specific:
  - image: `kind`, `count`, `size`, `seed`
  - audio: `kind`, `count`, `voice`, `format`, `duration_ms`, `seed`
  - video: `kind`, `count`, `resolution`, `duration_ms`, `seed`
- `yolo` is optional.
- `yolo: false` preserves the existing tool approval flow.
- `yolo: true` skips human tool approval for that run, including resumed recoverable runs, but tool-local validation still applies.
- `shell_safety_policy_enabled` is optional.
- When omitted, the backend uses the saved value from `GET /system/configs/general`.
- When no General setting has been saved yet, the effective default remains `true`.
- `shell_safety_policy_enabled: true` preserves the current local shell pre-execution safety policy.
- `shell_safety_policy_enabled: false` disables only the shell-local deny layer and directory-change restrictions; normal tool authorization, approval, timeout, and audit behavior still apply.
- `thinking` is optional.
- `thinking.enabled` enables model thinking streams for providers that emit thinking parts.
- `thinking.effort` optionally sets provider reasoning effort (`minimal`, `low`, `medium`, `high`); when set, it is forwarded to OpenAI-compatible providers as `openai_reasoning_effort`.
- `target_role_id` is optional. When set, that run starts from the specified role instead of the session-default root role, without mutating the saved session topology.
- `target_role_id` may point to `Coordinator`, `MainAgent`, or any normal role known to the role registry.
- `orchestration_policy` is optional. When provided, it overrides the selected orchestration preset policy for that run only and is stored in the run topology snapshot.
- The backend resolves the session mode at run creation time and snapshots the chosen root topology into the run intent for queued and recoverable resume flows.
- `session_id`, `target_role_id`, `run_id`, and other identifier-style request fields follow the common identifier validation rules above.

Response:

```json
{"run_id": "run-1", "session_id": "session-1", "target_role_id": "Architect"}
```

### `GET /runs/events`

Streams multiple main-run event streams over one SSE connection.

Query:
- `run_id`: repeat once per run, up to 32 values.
- `after_event_id`: optional repeated replay offset matching each `run_id`; omitted values default to `0`.

The response emits the same `RunEvent` JSON shape as `GET /runs/{run_id}/events`.
Clients route each event by its `run_id` or `trace_id`. This endpoint is intended
for browser-side multiplexing of active and recently viewed main sessions so the
UI does not open one SSE connection per running session.

### `GET /runs/{run_id}/events`

Streams run events via SSE.

Multimodal events:
- `output_delta`: payload includes `output`, an array of typed content parts. Text streaming may still emit `text_delta`; media outputs are emitted through `output_delta`.
- `generation_progress`: payload includes `run_kind`, `phase`, `progress`, and optional `preview_asset_id` for provider-native image/audio/video generation runs.

Thinking events:
- `thinking_started`: payload includes `part_index`, `role_id`, `instance_id`.
- `thinking_delta`: payload includes `part_index`, `text`, `role_id`, `instance_id`.
- `thinking_finished`: payload includes `part_index`, `role_id`, `instance_id`.

Spec checkpoint events:
- `spec_checkpoint_applied`: emitted at a safe model boundary when a non-coordinator task with a `TaskSpec` crosses its `lifecycle.spec_checkpoint` refresh threshold. The backend persists an internal system prompt containing the current task spec before rebuilding the next model request. Payload includes `task_id`, `role_id`, `instance_id`, `sequence`, `reason`, `tool_calls_since_last_checkpoint`, `messages_since_last_checkpoint`, and `history_tokens_since_last_checkpoint`.

Retry events:
- `llm_retry_scheduled`: payload includes `instance_id`, `role_id`, `attempt_number`, `total_attempts`, `retry_in_ms`, `error_code`, and `error_message`.
- `llm_retry_exhausted`: payload includes `instance_id`, `role_id`, `attempt_number`, `total_attempts`, `error_code`, and `error_message`.
- `run_paused`: payload includes `task_id`, `instance_id`, `role_id`, `error_code`, `error_message`, `retries_used`, `total_attempts`, and `phase="awaiting_recovery"`. For `model_tool_args_invalid_json`, the payload also includes `auto_recovery_exhausted`, `attempt`, and `max_attempts`.
- `run_resumed`: payload always includes `session_id` and `reason`. When the backend auto-recovers a malformed tool-arguments response, `reason="auto_recovery_invalid_tool_args_json"` and the payload also includes `attempt` and `max_attempts`.
- User-question lifecycle events:
  - `user_question_requested`: payload includes `question_id`, `instance_id`, `role_id`, and `questions[]`.
  - `user_question_answered`: payload includes `question_id`, `instance_id`, `role_id`, and `answers[]`.
- Background task lifecycle events:
  - `background_task_started`
  - `background_task_updated`
  - `background_task_completed`
  - `background_task_stopped`
  Each payload is the current background task snapshot, including `background_task_id`, `kind`, `title`, `status`, `command`, `cwd`, `recent_output[]`, `output_excerpt`, `log_path`, and subagent linkage fields when `kind="subagent"`.
- Monitor lifecycle events:
  - `monitor_created`
  - `monitor_triggered`
  - `monitor_stopped`
  `monitor_created` and `monitor_stopped` payloads include `monitor_id`. `monitor_triggered` also includes `monitor_trigger_id`, `event_name`, `source_kind`, `source_key`, and `action_type`.

Frontend behavior:
- The web UI uses `llm_retry_scheduled` to render one active retry card in the round timeline and keep its countdown live while the retry backoff window is active.
- Retry countdowns are computed from the SSE event `occurred_at` timestamp plus `retry_in_ms`, so delayed delivery or page refresh does not restart the timer.
- Later retry events replace the same card instead of stacking multiple historical cards.
- Once a retried model attempt produces successful output, the retry card is removed.
- If a model emits malformed tool arguments JSON after a safe checkpoint, the backend may emit `run_resumed` with `reason="auto_recovery_invalid_tool_args_json"` and continue the same stream without surfacing `run_paused`.
- If the run still cannot continue safely after retries are exhausted, `llm_retry_exhausted` is followed by `run_paused` and the SSE stream closes for that turn.
- `run_paused` represents a recoverable interruption, not a terminal failure. Public run phase becomes `awaiting_recovery`.
- `user_question_requested` is a manual-interaction pause, not a failure. Public run phase becomes `awaiting_manual_action` until the question is answered or times out.
- Background task events are operator/UI continuity signals only. They update recovery state and `/ps`-style UI surfaces, but do not become model-visible conversation messages.

### `POST /runs/{run_id}/inject`

Queues injectable content for an active run. The model-visible behavior is an
injected user message; there is no separate runtime guidance channel.

Request body:
- `content`: non-empty text to inject.
- `source`: optional injection source, defaults to `user`.
- `mode`: optional delivery mode, defaults to `queued`.
- `client_message_id`: optional client-generated correlation ID. UI clients use
  it to reconcile the local pending queue item with the backend
  `injection_enqueued` event and response for the same injected message.

Delivery modes:
- `queued`: enqueue a user-visible injection for the run coordinator. It is
  applied at the earliest safe boundary: before starting a model request, after
  a complete tool-call/tool-result batch has been persisted, or before accepting
  a final answer.
- `interrupt`: enqueue a user-visible injection that interrupts the current
  model step at the next runtime interrupt check, then rebuilds the model
  iteration from the last persisted safe conversation boundary.

Injected content is never inserted between an assistant tool call and the
matching tool result. If the model has produced a tool-call batch, queued
injections wait for the matching tool results to be committed. The injection is
then appended after the tool result and before the next model request. For
example, if the first shell `pwd` call finishes while a queued injection is
waiting, the next model request history must be original user message, `pwd`
tool call, `pwd` tool result, then the injected user message.
Multiple queued public user injections drained at the same boundary are merged
into one user message with blank lines between entries.

### `POST /runs/{run_id}/inject:force`

Forces queued user injections for the run coordinator into one interrupt
injection for the same run. Messages are merged in queue order with blank lines
between entries. The response includes the promoted `injection_id` and
`applied_injection_ids` covering the original queued injection IDs plus the
promoted interrupt ID. When the original queued messages carried
`client_message_id` values, the response also includes
`superseded_client_message_ids` so clients can remove matching optimistic queue
items. This endpoint does not create a new run.

SSE events:
- `injection_enqueued`: emitted when an injection is accepted or forced.
- `injection_applied`: emitted when the injection is appended to the target
  agent conversation and the model iteration is rebuilt. This event means the
  next model request for that target includes the injected message; it is not
  only a UI marker.

`injection_applied` includes `restart_scope` (`turn_boundary`,
`pre_tool_call`, or `interrupt`) and `supersedes_pending_tool_calls`. When
`supersedes_pending_tool_calls` is true, the client should drop any streamed
tool-call UI that has not yet received a tool result for that run turn.

Session round projections expose public user/subagent injections as
`injection_messages`. Internal system reminders remain hidden or redacted.

### `GET /runs/{run_id}/tool-approvals`

Lists pending tool approvals.

### `GET /runs/{run_id}/questions`

Lists persisted `ask_question` requests for the run.

Response fields:
- `question_id`
- `run_id`
- `session_id`
- `task_id`
- `instance_id`
- `role_id`
- `tool_name`: currently always `ask_question`
- `status`: `requested | answered | timed_out | completed`
- `questions[]`
  - `header`
  - `question`
  - `options[]`
    - `label`
    - `description`
  - `multiple`
  - `placeholder`
- `answers[]`
  - `selections[]`
    - `label`
    - `supplement`
- `created_at`
- `updated_at`
- `resolved_at`

Notes:
- The endpoint returns all persisted question requests for the run, ordered by creation time.
- Open requests are the rows with `status="requested"`.
- The `ask_question` tool uses one batched request per tool call, so `questions[]` may contain multiple prompts.
- Each prompt must provide at least one caller-defined option in `options[]`.
- Every question automatically includes a synthetic `None of the above` option in `options[]`.
- The tool has an internal default wait timeout of 20 minutes. Timed-out requests remain queryable with `status="timed_out"`.

### `POST /runs/{run_id}/questions/{question_id}:answer`

Answers one pending `ask_question` request.

Request:

```json
{
  "answers": [
    {
      "selections": [
        {"label": "Backend"},
        {"label": "CLI", "supplement": "Primary implementation surface"}
      ]
    },
    {
      "selections": [
        {
          "label": "__none_of_the_above__",
          "supplement": "Use the opencode-style batched question format."
        }
      ]
    }
  ]
}
```

Answer rules:
- `answers[]` length must exactly match the original `questions[]` length.
- Each answer must provide `selections[]`.
- `selections[].label` values must match the original option `label` values exactly.
- `selections[].supplement` is optional and applies only to that selected option.
- For prompts with `multiple=false`, `selections[]` may contain at most one item.
- `__none_of_the_above__` is always available and cannot be combined with any other option.

Notes:
- Successful answers are persisted first, then the in-flight waiting tool call is resumed when it is still open.
- If the run is recoverable and currently paused or stopped, the backend may resume it automatically after accepting the answer.
- Returns `409` when the run is stopping or the question is no longer pending.

### `GET /runs/{run_id}/background-tasks`

Lists managed background tasks bound to the run.

Response:

```json
{
  "items": [
    {
      "background_task_id": "exec_a1b2c3d4e5f6",
      "run_id": "run-1",
      "session_id": "session-1",
      "kind": "command",
      "title": "",
      "command": "sleep 30",
      "cwd": "/workspace/project",
      "execution_mode": "background",
      "status": "running",
      "tty": false,
      "timeout_ms": 1800000,
      "exit_code": null,
      "recent_output": [],
      "output_excerpt": "",
      "log_path": "tmp/background_tasks/exec_a1b2c3d4e5f6.log",
      "subagent_role_id": null,
      "subagent_run_id": null,
      "subagent_task_id": null,
      "subagent_instance_id": null,
      "created_at": "2026-03-31T10:00:00Z",
      "updated_at": "2026-03-31T10:00:00Z",
      "completed_at": null,
      "completion_notified_at": null
    }
  ]
}
```

### `GET /runs/{run_id}/background-tasks/{background_task_id}`

Returns one managed background task snapshot for the run.

### `GET /runs/{run_id}/todo`

Returns the latest persisted run-scoped todo snapshot for the run.

Response:

```json
{
  "todo": {
    "run_id": "run-1",
    "session_id": "session-1",
    "items": [
      {"content": "Inspect issue 399 requirements", "status": "completed"},
      {"content": "Implement run todo persistence", "status": "in_progress"},
      {"content": "Verify API and CLI output", "status": "pending"}
    ],
    "version": 2,
    "updated_at": "2026-04-20T10:00:00Z",
    "updated_by_role_id": "MainAgent",
    "updated_by_instance_id": "instance-1"
  }
}
```

Notes:
- Missing todo state returns an empty snapshot with `items=[]` and `version=0`; the endpoint does not return `404` just because the run has not written todo state yet.
- `status` is one of `pending`, `in_progress`, or `completed`.
- At most one item may be `in_progress`.

### `POST /runs/{run_id}/background-tasks/{background_task_id}:stop`

Stops one managed background task and returns its final snapshot.

### `GET /runs/{run_id}/monitors`

Lists monitor subscriptions bound to the run.

### `POST /runs/{run_id}/monitors`

Creates a run-scoped monitor subscription.

Request fields:
- `source_kind`: currently `background_task` or `github`
- `source_key`: background task id or GitHub `owner/repo`
- `event_names[]`: monitor event names such as `background_task.line`, `background_task.completed`, `pr.opened`, `check_run.completed`
- `patterns[]`: optional substring match set applied to `body_text`
- `action_type`: `wake_instance`, `wake_coordinator`, or `start_followup_run`
- `cooldown_seconds`
- `max_triggers`
- `auto_stop_on_first_match`
- `case_sensitive`

Response:

```json
{
  "monitor": {
    "monitor_id": "mon_ab12cd34ef56",
    "run_id": "run-1",
    "session_id": "session-1",
    "source_kind": "background_task",
    "source_key": "background_task_ab12cd34ef56",
    "status": "active",
    "rule": {
      "event_names": ["background_task.line"],
      "text_patterns_any": ["ERROR"],
      "cooldown_seconds": 0,
      "max_triggers": null,
      "auto_stop_on_first_match": false,
      "case_sensitive": false
    },
    "action": {"action_type": "wake_instance"},
    "trigger_count": 0
  }
}
```

### `POST /runs/{run_id}/monitors/{monitor_id}:stop`

Stops one monitor subscription and returns its final snapshot.

Notes:
- Background tasks are scoped to the owning run. Cross-run access returns `404`.
- `kind="command"` rows represent managed shell execution. `kind="subagent"` rows represent one-shot background subagent runs created from normal-mode tool calls.
- Subagent rows use a synthetic `command` value of the form `subagent:<role_id>` for continuity with existing recovery and tool result surfaces. UI should prefer `title` and `subagent_role_id` for display.
- The public API is intentionally read-mostly. Interactive stdin/resize remains tool-only, not a human-facing REST surface.
- Runtime shell selection is internal, not part of the REST contract. Linux/macOS use the managed bash path; Windows prefers Git Bash and falls back to PowerShell when Git Bash is unavailable.
- `tty=true` background tasks use a platform TTY backend: POSIX PTY on Linux/macOS and ConPTY via `pywinpty` on supported Windows hosts. When Windows TTY support is unavailable, only non-TTY background tasks remain available.
- Unlike Codex's stricter unified-exec contract, Agent Teams keeps non-TTY `write_stdin` enabled for compatibility with existing pipe-style workflows.
- `create_monitor` is also exposed as a tool for run-local `background_task` subscriptions; REST keeps the generic `source_kind/source_key` contract so future event sources reuse the same substrate.
- System/module boundary guidance for this substrate lives in `docs/core/system-module-boundaries.md`.

### `GET /triggers/github/accounts`

Lists configured GitHub webhook accounts.

### `POST /triggers/github/accounts`

Creates one GitHub webhook account.

Request fields:
- `name`
- `display_name`
- `token`
- `webhook_secret`
- `enabled`

Notes:
- If `token` is omitted, the runtime falls back to `/api/system/configs/github` when that system token is configured.
- If `webhook_secret` is omitted, the backend generates one.

### `PATCH /triggers/github/accounts/{account_id}`

Updates one GitHub webhook account.

Request fields:
- `name`
- `display_name`
- `token`
- `webhook_secret`
- `clear_token`
- `clear_webhook_secret`
- `enabled`

Notes:
- `clear_token=true` and `clear_webhook_secret=true` explicitly remove the stored per-account secret.
- Empty strings do not clear an existing secret; omit the field or use the explicit clear flag.

### `DELETE /triggers/github/accounts/{account_id}`

Deletes one GitHub webhook account and its repositories/rules.

### `POST /triggers/github/accounts/{account_id}:enable`

Enables one GitHub webhook account and reconciles all bound repository webhooks.

### `POST /triggers/github/accounts/{account_id}:disable`

Disables one GitHub webhook account and unregisters bound repository webhooks when possible.

### `GET /triggers/github/repos`

Lists configured GitHub repository subscriptions.

### `GET /triggers/github/accounts/{account_id}/repositories`

Lists GitHub repositories visible to the effective token for one account.

Query fields:
- `query`

Notes:
- The backend uses the account token override when configured, otherwise it falls back to `/api/system/configs/github`.
- Results are intended for UI repository pickers and include canonical `owner`, `repo_name`, and `full_name`.

### `POST /triggers/github/repos`

Creates one GitHub repository subscription.

Request fields:
- `account_id`
- `owner`
- `repo_name`
- `callback_url`
- `enabled`

Notes:
- `callback_url` is optional for clients. When omitted, the backend derives it from the current server base URL and `/api/triggers/github/deliveries`.
- If `/api/system/configs/github.webhook_base_url` is configured, that public base URL wins over the inbound request host when deriving the callback URL.
- The backend does not auto-fill local-only request hosts such as `127.0.0.1` or `localhost`; in that case `callback_url` remains unset until a public base URL or explicit callback is provided.
- Public webhook base URLs should point at a host that exposes only `/api/system/health` and `/api/triggers/github/deliveries` by default.
- The stored callback URL is reused for automatic webhook registration.
- `subscribed_events` is derived from the union of enabled rule `event_name` values for that repository; clients do not manage it directly.

### `PATCH /triggers/github/repos/{repo_subscription_id}`

Updates one GitHub repository subscription.

Request fields:
- `owner`
- `repo_name`
- `callback_url`
- `enabled`

Notes:
- Any repository identity, callback, or enabled-state change triggers webhook reconciliation.

### `DELETE /triggers/github/repos/{repo_subscription_id}`

Deletes one GitHub repository subscription and its rules.

### `POST /triggers/github/repos/{repo_subscription_id}:enable`

Enables one GitHub repository subscription and reconciles the remote webhook.

### `POST /triggers/github/repos/{repo_subscription_id}:disable`

Disables one GitHub repository subscription and unregisters its remote webhook when possible.

### `GET /triggers/github/rules`

Lists configured GitHub trigger rules.

### `POST /triggers/github/rules`

Creates one GitHub trigger rule.
Supported `match_config` fields for the current GitHub automation UI and API are:
- `event_name`
- `actions`
- `base_branches`
- `draft_pr`
- `check_conclusions`

The API no longer accepts older GitHub-specific filters such as label, sender, or path match fields.

### `PATCH /triggers/github/rules/{trigger_rule_id}`

Updates one GitHub trigger rule.

### `DELETE /triggers/github/rules/{trigger_rule_id}`

Deletes one GitHub trigger rule.

### `POST /triggers/github/rules/{trigger_rule_id}:enable`

Enables one GitHub trigger rule and reconciles the repository webhook event set.

### `POST /triggers/github/rules/{trigger_rule_id}:disable`

Disables one GitHub trigger rule and reconciles the repository webhook event set.

### `POST /triggers/github/deliveries`

Accepts one inbound GitHub webhook delivery.

Notes:
- Signature validation uses the configured repository/account webhook secret.
- A valid delivery is normalized once and then feeds both the existing GitHub trigger pipeline and the monitor substrate.
- GitHub monitor source keys use `repository.full_name` such as `owner/repo`.
- Current normalized monitor event names are `pr.opened`, `pr.updated`, `pr.review_requested`, `issue.opened`, `issue.updated`, `check_run.completed`, `check_suite.completed`, and `status.updated`.

### `POST /runs/{run_id}/tool-approvals/{tool_call_id}/resolve`

Approves or denies a pending tool call.

Request:

```json
{"action": "approve", "feedback": ""}
```

Allowed `action` values:
- `approve`: legacy one-time approval, equivalent to `approve_once`
- `approve_once`: execute this pending tool call once
- `approve_exact`: for shell, also save an exact reusable approval for the normalized command
- `approve_prefix`: for shell, also save reusable prefix approvals such as `git status`
- `deny`: deny the pending tool call

Notes:
- Shell exact/prefix approvals are project-scoped and shell-runtime-scoped. Git Bash approvals do not automatically apply to PowerShell, and vice versa.
- Shell `workdir` values must stay inside the workspace writable roots even when the command itself targets external executables or scripts.

### `POST /runs/{run_id}/stop`

Stops the full run or a specific subagent.

### `POST /runs/{run_id}:resume`

Resumes a recoverable run.

Behavior:
- Recoverable runs in `queued`, `paused`, or `stopped` may be resumed.
- Runs paused for `awaiting_tool_approval`, `awaiting_manual_action`, or `awaiting_subagent_followup` are not resumed by this endpoint; those flows still require their dedicated resolution action.

### `POST /runs/{run_id}/subagents/{instance_id}/inject`

Injects follow-up content to one paused/running subagent.

## Task APIs

### `POST /tasks/runs/{run_id}`

Creates delegated tasks under the run root task.

Request:

```json
{
  "tasks": [
    {
      "title": "Write API code",
      "objective": "Implement the endpoint and tests",
      "spec": {
        "summary": "Endpoint contract and persistence behavior",
        "requirements": ["Expose a typed API response"],
        "acceptance_criteria": ["Unit tests cover success and validation failures"],
        "evidence_expectations": ["Test command output is attached to the verification report"],
        "strictness": "medium",
        "prompt_code_sync_status": "unknown"
      }
    }
  ]
}
```

Behavior:
- Creates delegated task contracts and, when `role_id` plus
  `orchestration_node_id` are provided, queues task nodes for DAG execution.
- If `spec` is provided, it is persisted as a versioned task spec artifact and the created task envelope is bound through `spec_artifact_id`.
- If `spec_artifact_id` is provided during creation, the stored spec is imported and the new task receives its own artifact version; cross-task artifact rows are not reused as the task's current `spec_artifact_id`.
- If `spec_source_task_id` is provided, or when a draft depends on exactly one existing spec-bearing task, the new task is linked to that source task and can inherit the current spec artifact.
- A provided `spec_source_task_id` must resolve to a task with a bound spec.
- Role binding happens later during dispatch unless the draft includes
  `role_id`; pre-bound task nodes are assigned immediately and ready nodes run
  automatically after the current Coordinator turn.
- `depends_on_node_ids` expresses dynamic DAG edges inside the create batch or
  against existing `orchestration_node_id` values. The backend resolves them to
  `depends_on_task_ids`, rejects unknown nodes, rejects self-dependencies, and
  requires the resulting graph to be acyclic.
- If a task contract sets `lifecycle.timeout_seconds`, the timeout is progress-sensitive rather than a strict wall clock cap. The dispatch starts with one timeout window, and each persisted model/tool message for the same task and assigned instance extends the deadline by another full window. If no new task message is persisted before the current deadline, the worker is cancelled and `lifecycle.on_timeout` is applied. Task status heartbeats only keep the running row fresh; they do not extend the lifecycle timeout by themselves.
- `lifecycle.spec_checkpoint` controls automatic spec refresh for long non-coordinator runs. Defaults are enabled with refresh thresholds of 12 completed tool calls, 48 active history messages, or 8000 estimated history tokens since the previous checkpoint. The object accepts `enabled`, `refresh_interval_tool_calls`, `refresh_interval_messages`, `refresh_interval_history_tokens`, and `max_summary_chars`.

Response:

```json
{
  "created_count": 1,
  "tasks": [
    {
      "task_id": "task-2",
      "title": "Write API code",
      "objective": "Implement the endpoint and tests",
      "status": "created",
      "assigned_role_id": null,
      "assigned_instance_id": null,
      "role_id": null,
      "instance_id": null,
      "parent_task_id": "task-root",
      "spec_artifact_id": "spec-1234",
      "spec_source_task_id": null,
      "evidence_bundle": null
    }
  ]
}
```

### `GET /tasks/runs/{run_id}`

Lists tasks in a run.

Query:
- `include_root`: `true|false`

### `GET /tasks`

Lists all persisted tasks.

### `GET /tasks/{task_id}`

Gets one task record.

The embedded `envelope` may include:
- `spec`: normalized task specification.
- `spec_artifact_id`: current versioned spec artifact bound to this task.
- `spec_source_task_id`: upstream task whose specification this task derives from.
- `orchestration_node_id`: stable DAG node id for fixed graph nodes,
  Coordinator-created dynamic nodes, and automatic `auto_plan` / `auto_lane_*`
  planning nodes.
- `depends_on_task_ids`: resolved upstream task ids that must reach a completed
  state before this node is ready.
- `evidence_bundle`: normalized verification evidence generated by the latest verification pass.

### `PATCH /tasks/{task_id}`

Updates a delegated task definition.

Request:

```json
{
  "title": "Review code",
  "objective": "Review the implementation and report issues",
  "spec_source_task_id": "task-2"
}
```

Rules:
- Only `created` delegated tasks can be updated.
- `role_id` cannot be updated through task APIs.
- Root coordinator tasks cannot be updated through task APIs.
- Updating `spec` creates a new spec artifact version for the task.
- Providing `spec_artifact_id` binds the task to an existing artifact only when the artifact matches the task spec and belongs to the same task.
- Providing `spec_source_task_id` links the task to an upstream spec source; omitting it leaves the existing source binding unchanged.

### `GET /tasks/{task_id}/spec-artifact`

Returns the latest spec artifact bound to the task.

Response fields:
- `artifact_id`
- `task_id`
- `session_id`
- `trace_id`
- `source_task_id`
- `spec`
- `version`
- `created_at`
- `updated_at`

### `GET /tasks/{task_id}/evidence-bundle`

Returns the latest normalized evidence bundle stored on the task envelope. If verification has not produced evidence yet, the endpoint returns `404`.

During verification, the backend builds an effective verification plan from the
stored task plan plus any bound `TaskSpec.formal_verification` found on the task
envelope or rehydrated from `spec_artifact_id`. Spec-derived formal checks are
deduplicated against explicit `formal_checks`, and the evidence bundle formal
flags reflect the effective plan.

Response fields:
- `task_id`
- `spec_artifact_id`
- `spec_source_task_id`
- `items[]`
- `acceptance_links[]`
- `expectation_links[]`
- `formal_verification_required`
- `formal_verification_passed`

Evidence items include `evidence_id`, `kind`, `summary`, `source`, `passed`, optional `path`, optional `command`, optional tool identifiers, parsed `metrics`, supported target text, and a short `output_excerpt`. Evidence links identify the target text, matching evidence item ids, satisfaction state, and reason.

### `GET /tasks/{task_id}/spec-artifacts`

Lists all spec artifact versions for a task in ascending version order. Supports lightweight summaries for version listings and full artifacts for detailed inspection.

Path parameters:
- `task_id`: task identifier (required)

Query parameters:
- `format`: `"summary"` (default) or `"full"`. Summary mode omits the `spec` payload from each artifact. Full mode returns the complete `TaskSpecArtifact` model dump including `spec`.

Response (format=summary):
- `task_id`: task identifier
- `versions[]`: list of `SpecArtifactVersionSummary` objects
  - `artifact_id`
  - `task_id`
  - `session_id`
  - `trace_id`
  - `source_task_id` (nullable)
  - `version`: integer (>= 1)
  - `created_at`: ISO 8601 timestamp
  - `updated_at`: ISO 8601 timestamp

Response (format=full):
- `task_id`: task identifier
- `versions[]`: list of full `TaskSpecArtifact` objects (including `spec`)

Status codes:
- `200`: success
- `404`: task not found

Example request:
```
GET /api/tasks/task-123/spec-artifacts?format=summary
```

Example response (format=summary):
```json
{
  "task_id": "task-123",
  "versions": [
    {
      "artifact_id": "spec-aaa",
      "task_id": "task-123",
      "session_id": "sess-1",
      "trace_id": "trace-1",
      "source_task_id": null,
      "version": 1,
      "created_at": "2026-05-02T10:00:00Z",
      "updated_at": "2026-05-02T10:00:00Z"
    },
    {
      "artifact_id": "spec-bbb",
      "task_id": "task-123",
      "session_id": "sess-1",
      "trace_id": "trace-2",
      "source_task_id": null,
      "version": 2,
      "created_at": "2026-05-02T11:00:00Z",
      "updated_at": "2026-05-02T11:00:00Z"
    }
  ]
}
```

### `GET /tasks/{task_id}/spec-artifacts/{version}/diff`

Computes and returns the field-level diff between two spec artifact versions. By default, diffs the given version against its predecessor (version - 1). An explicit `from_version` query parameter allows comparing any two versions.

Path parameters:
- `task_id`: task identifier (required)
- `version`: target version integer, >= 2 (required)

Query parameters:
- `from_version`: optional integer >= 1. If omitted, defaults to `version - 1`.

Response fields (`SpecArtifactDiffResult`):
- `task_id`: task identifier
- `from_artifact_id`: artifact identifier for the source version
- `to_artifact_id`: artifact identifier for the target version
- `from_version`: integer
- `to_version`: integer
- `has_changes`: boolean
- `summary`: human-readable change summary string
- `field_changes[]`: list of `SpecArtifactDiffFieldChange` objects
  - `field_name`: model field name
  - `field_label`: human-readable display name
  - `change_type`: `"added"`, `"removed"`, `"modified"`, or `"unchanged"`
  - `old_value`: string or null (for scalar fields)
  - `new_value`: string or null (for scalar fields)
  - `old_items[]`: string list (for tuple/list fields)
  - `new_items[]`: string list (for tuple/list fields)
  - `added_items[]`: items present in new but not old
  - `removed_items[]`: items present in old but not new

Status codes:
- `200`: success
- `400`: version is 1 and no explicit `from_version` provided
- `404`: task or version not found
- `422`: invalid path parameters

Example request:
```
GET /api/tasks/task-123/spec-artifacts/2/diff
```

Example response:
```json
{
  "task_id": "task-123",
  "from_artifact_id": "spec-aaa",
  "to_artifact_id": "spec-bbb",
  "from_version": 1,
  "to_version": 2,
  "has_changes": true,
  "summary": "3 fields changed: requirements (+2 items), constraints (+1 item, -1 item), summary (modified)",
  "field_changes": [
    {
      "field_name": "requirements",
      "field_label": "Requirements",
      "change_type": "modified",
      "old_value": null,
      "new_value": null,
      "old_items": ["req-A", "req-B"],
      "new_items": ["req-A", "req-B", "req-C", "req-D"],
      "added_items": ["req-C", "req-D"],
      "removed_items": []
    },
    {
      "field_name": "summary",
      "field_label": "Summary",
      "change_type": "modified",
      "old_value": "Build feature X",
      "new_value": "Build feature X with error handling",
      "old_items": [],
      "new_items": [],
      "added_items": [],
      "removed_items": []
    }
  ]
}
```

### `GET /tasks/{task_id}/spec-checkpoint-evaluations`

Lists drift-detection evaluation results produced when spec checkpoints are rendered with `auto_evaluate_drift` enabled.

Path parameters:
- `task_id`: task identifier (required)

Query parameters:
- `checkpoint_seq`: optional integer to filter evaluations by checkpoint sequence number

Response fields:
- `task_id`: task identifier
- `evaluations[]`: list of `SpecCheckpointEvaluation` objects
  - `evaluation_id`: identifier (format `speval-{uuid}`)
  - `task_id`: task identifier
  - `artifact_id`: spec artifact that was current when the checkpoint was rendered
  - `session_id`: session identifier
  - `trace_id`: trace identifier
  - `checkpoint_seq`: integer, corresponds to the checkpoint sequence
  - `evaluator`: evaluator type (default `"llm"`)
  - `fallback`: boolean, true if the evaluation used rule-based fallback due to LLM failure
  - `overall_score`: float (0.0-5.0), composite score across all dimensions
  - `scores[]`: list of per-dimension score objects
    - `dimension`: evaluation dimension name
    - `score`: integer score
    - `reasoning`: evaluator explanation text
  - `summary`: evaluation summary text
  - `drift_detected`: boolean, true when `overall_score < drift_score_threshold`
  - `drift_detail`: structured JSON string describing which dimensions flagged drift
  - `created_at`: ISO 8601 timestamp

Status codes:
- `200`: success (returns empty list if no evaluations exist)
- `404`: task not found

Example request:
```
GET /api/tasks/task-123/spec-checkpoint-evaluations
```

Example response:
```json
{
  "task_id": "task-123",
  "evaluations": [
    {
      "evaluation_id": "speval-xxx",
      "task_id": "task-123",
      "artifact_id": "spec-bbb",
      "session_id": "sess-1",
      "trace_id": "trace-1",
      "checkpoint_seq": 3,
      "evaluator": "llm",
      "fallback": false,
      "overall_score": 4.2,
      "scores": [
        {"dimension": "completeness", "score": 4, "reasoning": "All requirements addressed"},
        {"dimension": "clarity", "score": 5, "reasoning": "Clear and unambiguous"}
      ],
      "summary": "Spec remains consistent with initial requirements.",
      "drift_detected": false,
      "drift_detail": "",
      "created_at": "2026-05-02T11:00:00Z"
    }
  ]
}
```

#### Task Spec Shape

`TaskSpec` is the durable spec contract embedded in task envelopes and spec artifacts.

Fields:
- `summary`
- `requirements`
- `constraints`
- `acceptance_criteria`
- `out_of_scope`
- `verification_commands`
- `evidence_expectations`
- `strictness`: `low`, `medium`, or `high`
- `entities`, `approach`, `structure`, `operations`, `norms`, `safeguards`: REASONS Canvas fields
- `prompt_artifact_version`
- `prompt_code_sync_status`: `unknown`, `in_sync`, `spec_ahead`, `code_ahead`, or `needs_review`
- `formal_verification`: optional formal verification plan

Formal verification plans support:
- `spec_language`: `tla_plus`, `alloy`, `lean`, `coq`, `isabelle`, or `custom`
- `tool_profile`: `tlc`, `alloy_analyzer`, `lean`, `coq`, `isabelle`, or `custom`
- `properties`
- `proof_artifacts`
- `counterexample_path`
- `replay_command`
- `required`

#### SpecCheckpointPolicy Shape

`SpecCheckpointPolicy` controls spec checkpoint injection behavior during task execution.

Fields:
- `enabled`: boolean (default `true`)
- `refresh_interval_tool_calls`: integer, tool call count threshold (default 12, range 1-1000)
- `refresh_interval_messages`: integer, message count threshold (default 48, range 1-5000)
- `refresh_interval_history_tokens`: integer, token count threshold (default 8000, range 1-1,000,000)
- `max_summary_chars`: integer, maximum rendered checkpoint length (default 6000, range 500-50,000)
- `include_reasons`: boolean, include REASONS Canvas section (default `true`)
- `refresh_on_version_change`: boolean, trigger immediate checkpoint when spec artifact version increments (default `false`)
- `auto_evaluate_drift`: boolean, run LLM drift evaluator after each checkpoint injection (default `false`)
- `drift_score_threshold`: float, overall score threshold below which drift is flagged (default 3.0, range 1.0-5.0)

There is no public manual dispatch endpoint for delegated tasks.

Delegated task dispatch is performed internally by the Coordinator through the `orch_dispatch_task` tool.

Internal dispatch rules:
- `created`: bind the task to the provided `role_id`, create or reuse the session-level subagent instance for that role, then execute.
- `assigned` or `stopped`: reuse the bound instance and continue.
- `completed`, `failed`, or `timeout`: rejected; create a replacement task instead.
- `running`: rejected as a conflict.
- After the first dispatch, the delegated role is fixed for that task. To change roles, create a replacement task.
- If another task already holds the reusable role instance in `assigned`, `running`, or `stopped`, a created task is assigned to an ephemeral clone with a private conversation.
- If the target role defines a `contract`, dispatch fails before execution when
  role preconditions or capability invariants are not satisfied. Automatic DAG
  scheduling applies the same checks to ready delegated tasks and marks the task
   failed with `role_contract_preconditions_failed` when the contract is violated.

### `GET /runs/{run_id}/tasks/{task_id}/artifact`

Returns the full `TaskArtifact` for a task, including all entries and the summary.

Path parameters:
- `run_id`: run identifier (for URL consistency)
- `task_id`: task identifier

Response fields (`TaskArtifact`):
- `task_id`
- `spec_artifact_id`: linked spec artifact
- `entries`: list of `TaskArtifactEntry` objects
- `summary`: `TaskArtifactSummary` with `task_id`, `summary` text, `entry_count`, `created_at`, `updated_at`
- `evidence_bundle_json`

Status codes:
- `200`: artifact found
- `404`: no artifact exists for the given task

### `GET /runs/{run_id}/tasks/{task_id}/artifact/entries`

Returns individual entries within a task artifact, with optional filtering.

Path parameters:
- `run_id`: run identifier (for URL consistency)
- `task_id`: task identifier

Query parameters:
- `phase`: optional filter by `TaskArtifactPhase` (`spec`, `execution`, `verification`, `delivery`)
- `event_type`: optional filter by event type string
- `limit`: page size (default 100, range 1-500)
- `offset`: page offset (default 0, minimum 0)

Response fields:
- `task_id`: the task identifier
- `items`: list of `TaskArtifactEntry` objects, each with `entry_id`, `phase`, `timestamp`, `role_id`, `instance_id`, `event_type`, `description`, `payload_json`
- `total`: total matching entries
- `next_offset`: offset for the next page, or `null` if no more entries

Status codes:
- `200`: entries returned (may be empty)

### `GET /runs/{run_id}/tasks/{task_id}/artifact/summary`

Returns the summary entry of a task artifact.

Path parameters:
- `run_id`: run identifier (for URL consistency)
- `task_id`: task identifier

Response fields (`TaskArtifactSummary`):
- `task_id`
- `spec_artifact_id`: linked spec artifact
- `total_entries`: number of entries in the artifact
- `phase_counts`: dict mapping phase names to entry counts
- `evidence_item_count`: number of evidence items
- `has_verification_bundle`: whether a verification bundle exists
- `has_summary`: whether a summary has been written
- `created_at`: ISO 8601 timestamp
- `updated_at`: ISO 8601 timestamp

Status codes:
- `200`: summary found
- `404`: no artifact exists for the given task

## Role APIs

### `GET /roles`

Lists loaded role definitions.

Each role may include a `contract` object. The contract is a behavioral
contract, not prompt text: dispatch and verification use it for deterministic
checks.

`contract` fields:
- `version`
- `preconditions[]`
  - `condition`: `task_has_spec`, `task_has_acceptance_criteria`,
    `dependencies_completed`, or `dependency_role_completed`
  - `role_ids[]`: required dependency roles for `dependency_role_completed`
  - `description`
- `postconditions[]`
  - `guarantee`: `verification_commands_configured`,
    `result_mentions_acceptance_criteria`,
    `result_mentions_evidence_expectations`, or `handoff_present`
  - `description`
- `invariants[]`
  - `invariant`: `must_have_tools`, `must_not_have_tools`,
    `must_have_mcp_servers`, `must_not_have_mcp_servers`,
    `must_have_skills`, or `must_not_have_skills`
  - `tools[]`
  - `mcp_servers[]`
  - `skills[]`
  - `description`

### `GET /roles:options`

Returns editor options for role settings.

Response fields:
- `coordinator_role_id`
- `main_agent_role_id`
- `coordinator_role`
  - `role_id`
  - `name`
  - `description`
  - `model_profile`
  - `capabilities`
  - `input_modalities[]`
- `main_agent_role`
  - `role_id`
  - `name`
  - `description`
  - `model_profile`
  - `capabilities`
  - `input_modalities[]`
- `normal_mode_roles[]`
  - `role_id`
  - `name`
  - `description`
  - `model_profile`
  - `capabilities`
  - `input_modalities[]`
- `subagent_roles[]`
  - `role_id`
  - `name`
  - `description`
  - `model_profile`
  - `capabilities`
  - `input_modalities[]`
- `role_modes[]`: `primary | subagent | all`
- `tool_groups[]`
  - `id`
  - `name`
  - `description`
  - `tools[]`
- `tools`
- `mcp_servers`
- `skills[]`
  - `ref`: effective runtime skill name
  - `name`
  - `description`
  - `source`: one of `builtin`, `user_relay_teams`, `user_agents`,
    `project_relay_teams`, or `project_agents`
- `agents[]`
  - `agent_id`
  - `name`
  - `transport`

Notes:
- Skills are loaded in override order: builtin, `~/.relay-teams/skills`,
  `~/.agents/skills`, project `.relay-teams/skills` from cwd up to git root,
  then project `.agents/skills` from cwd up to git root.
- Same-name skills do not coexist in the effective options payload. Later
  sources override earlier ones, and the returned `ref` is the surviving bare
  skill name.
- `capabilities.input/output.*` is the canonical multimodal contract for a role's
  resolved runtime model profile. `input_modalities[]` is derived from
  `capabilities.input` for compatibility with existing consumers.
- `tool_groups[]` is an editor convenience for bulk selection only. Role
  documents and validation/save requests still persist plain `tools[]`.
- Role `mcp_servers[]` and `skills[]` may contain the exact value `"*"`.
  This grants all currently configured MCP servers or all currently discovered
  skills at runtime, including entries added after config reload. Partial glob
  patterns such as `docs-*` or `builtin:*` are not supported.
- `normal_mode_roles[]` contains non-system roles whose `mode` is `primary` or `all`.
- `subagent_roles[]` contains non-system roles whose `mode` is `subagent` or `all`.
- Returns `503` when required builtin/system roles such as `Coordinator` or
  `MainAgent` are unavailable in the current runtime.

### `GET /roles/configs`

Lists editable role document summaries for the settings UI.

Existing saved role files are still listed when they contain unknown `tools`,
`mcp_servers`, or `skills`. Those stale capability references are ignored for
read/reload flows so the settings UI can still load.

When no builtin or app role files are available, this endpoint returns `200 []`
instead of failing the whole request. If builtin role files are missing but app
role files exist, the response contains the app-backed documents only.

Response fields:
- `role_id`
- `name`
- `description`
- `version`
- `model_profile`
- `bound_agent_id`
- `mode`
- `source`

### `GET /roles/configs/{role_id}`

Returns one editable role document.

Response fields:
- `source_role_id`
- `role_id`
- `name`
- `description`
- `version`
- `tools`
- `mcp_servers`
- `skills`
- `model_profile`
- `memory_profile`
- `contract`
- `bound_agent_id`
- `mode`
- `source`
- `system_prompt`
- `file_name`
- `content`

Notes:
- `skills` in saved role documents are returned as effective runtime skill
  names. Existing unknown saved values are preserved so the UI can still
  display and edit the role.

### `PUT /roles/configs/{role_id}`

Validates and saves one role document, then reloads the runtime role registry.

Request:

```json
{
  "source_role_id": "spec_coder",
  "role_id": "spec_coder",
  "name": "Spec Coder",
  "description": "Implements requested changes.",
  "version": "1.0.0",
  "tools": ["read_file", "write_file"],
  "mcp_servers": [],
  "skills": [],
  "model_profile": "default",
  "bound_agent_id": "codex_local",
  "mode": "subagent",
  "memory_profile": {
    "enabled": true
  },
  "contract": {
    "preconditions": [
      {"condition": "dependencies_completed"}
    ],
    "postconditions": [
      {"guarantee": "result_mentions_acceptance_criteria"}
    ],
    "invariants": [
      {"invariant": "must_not_have_tools", "tools": ["edit", "write"]}
    ]
  },
  "system_prompt": "Implement the requested change."
}
```

Rules:
- Path `role_id` must match body `role_id`.
- Unknown tools, MCP servers, or skills are rejected.
- Unknown tools, MCP servers, or skills referenced by `contract.invariants[]`
  are also rejected. Invariant violations such as a role selecting a forbidden
  tool are rejected during validate/save.
- The exact value `"*"` is accepted in `mcp_servers` and `skills` to mean all
  current entries for that capability type. It is preserved in saved role
  documents instead of being expanded.
- Unknown `bound_agent_id` values are rejected.
- Unrelated saved role files with stale `tools`, `mcp_servers`, or `skills` do
  not block the reload after a successful save; those references are ignored
  with warnings until they are cleaned up.
- When `source_role_id` is omitted and the file does not exist yet, a new role file is created.
- Renaming a role writes a new file and removes the previous file when validation succeeds.
- When `bound_agent_id` is set, that role executes through the configured agent runtime provider instead of the local model provider chain.
- `mode` controls where the role can be selected: `primary` for normal-mode root roles, `subagent` for background/delegated subagent roles, `all` for both.
- Reserved system roles keep fixed identity fields (`role_id`, `name`, `description`, `version`), fixed `mode`, and fixed `system_prompt` through this API.
- `contract` is serialized into role YAML front matter and is included in the
  runtime system prompt as `## Role Contract` when non-empty; enforcement does
  not rely on model compliance with that prompt section.

### `POST /roles:validate`

Validates role files against registered tools and skills.

Constraint:
- `depends_on` is invalid in role front matter. Ordering is runtime task orchestration state, not role metadata.
- Returns `503` when required builtin/system roles are unavailable in the
  current runtime.

### `POST /roles:validate-config`

Validates one in-memory role draft without saving it.

Use cases:
- settings UI inline validation
- pre-save editor checks for tools, MCP servers, skills, and role schema

## Built-in Runtime Tool Contracts

### Background Task Tool Family

The following built-in tools all project the same managed background-task snapshot used by `/runs/{run_id}/background-tasks*` and recovery payloads:
- `shell` with `background=true`
- `list_background_tasks`
- `wait_background_task`
- `stop_background_task`
- `spawn_subagent` with `background=true`

Shared result fields include:
- `background_task_id`
- `kind`
- `title`
- `status`
- `command`
- `cwd`
- `recent_output`
- `output_excerpt`
- `log_path`
- `subagent_role_id`
- `subagent_run_id`
- `subagent_task_id`
- `subagent_instance_id`
- `completed`

`wait_background_task` is a completion wait, not a polling primitive: it accepts only `background_task_id` and returns after the managed task reaches a terminal state. Use `list_background_tasks` for in-progress status snapshots.

### Todo Tool Family

The built-in todo tools maintain a run-scoped local execution plan that is separate from delegated task contracts:
- `todo_write`
- `todo_read`

Shared todo snapshot fields include:
- `run_id`
- `session_id`
- `items`
- `version`
- `updated_at`
- `updated_by_role_id`
- `updated_by_instance_id`

`todo_write` always replaces the full table. It does not append or patch individual rows.

Todo item fields:
- `content`
- `status`: `pending | in_progress | completed`

Rules:
- At most one todo item may be `in_progress`.
- An empty `items` array clears the run todo while preserving versioned history through the latest snapshot row.
- `todo_read` returns the latest persisted snapshot or the synthetic empty snapshot when no todo has been written yet.

### `spawn_subagent`

Starts a fresh one-shot subagent run under a subagent-capable role.

Runtime ownership: the launched child is persisted as an agent-runtime instance.
The subagent name is a product/API projection, not a separate execution
architecture.

Arguments:
- `role_id`: target role. The role must resolve to `mode="subagent"` or `mode="all"`.
- `description`: short task label used for background task lists and completion notifications.
- `prompt`: full task instructions. Each spawned subagent starts from a fresh conversation and does not inherit ad-hoc conversational follow-up state from the caller.
- `background`: optional boolean. Defaults to `false`. When `false`, the tool waits for the subagent to finish and returns its final text output. When `true`, the tool returns immediately with a managed `background_task_id`.

Rules:
- Only available to normal-mode runs. Orchestration mode continues to use delegated task dispatch instead of this tool.
- Default behavior is synchronous: the tool waits for the subagent to finish and returns `{ completed, output }` as the model-visible payload.
- Synchronous runs still persist their own subagent run/instance/message history, but they are not managed through the background-task API.
- The spawned work is one-shot. There is no mid-run `send_input` or resume contract for this v1 path.
- When `background=true`, operators and agents should manage spawned work through `list_background_tasks`, `wait_background_task`, and `stop_background_task`.
- Completion notifications are not user-visible by themselves; the calling agent is responsible for summarizing relevant results back to the user.

## Workspace APIs

### `GET /workspaces`

Lists registered execution workspaces.

### `POST /workspaces`

Creates one execution workspace.

Request:

```json
{
  "workspace_id": "default",
  "root_path": "D:/workspace/agent_teams"
}
```

Rules:
- `root_path` must already exist.
- `root_path` must be a directory.
- `workspace_id` must be unique.
- `workspace_id` follows the common identifier validation rules above.

### `GET /workspaces/{workspace_id}`

Returns one registered execution workspace.

### `POST /workspaces/{workspace_id}:open-root`

Opens the workspace root directory in the native file manager on the machine
running the backend.

Rules:
- The workspace must exist and its `root_path` must still exist on disk.
- The action is best-effort and returns `503` when the runtime cannot launch a
  native file manager.
- This endpoint performs a local side effect and is intended for local desktop
  operator flows, not remote browser clients.

### `GET /workspaces/{workspace_id}/snapshot`

Returns the fast project snapshot used for initial project-view rendering.
The response includes:
- workspace metadata such as `workspace_id` and `root_path`
- the root tree node plus only the first visible level of children under `root_path`
- per-node `has_children` so the frontend can lazy-load deeper folders on demand

Rules:
- The workspace must exist and its `root_path` must still exist on disk.
- The snapshot excludes `.git` and does not build recursive descendants or file diffs.
- The frontend should treat this response as the initial shell for progressive loading.

### `GET /workspaces/{workspace_id}/tree?path=...`

Returns one directory listing for a relative workspace path.
The response includes:
- `directory_path`
- one level of `children[]`
- per-node `has_children` to support further lazy expansion

Rules:
- `path` must be relative to the workspace root.
- Paths that escape the workspace root are rejected.
- Non-directory paths are rejected.
- The listing excludes `.git`.

### `GET /workspaces/{workspace_id}/diffs`

Returns the workspace diff summary used for initial change-list rendering.
The response includes:
- per-file summary entries for modified, added, deleted, renamed, copied, and untracked files
- Git metadata such as `git_root_path` and a `diff_message` when diff inspection is unavailable

Rules:
- The workspace must exist and its `root_path` must still exist on disk.
- Diff inspection is best-effort. Non-Git directories return `is_git_repository = false` with a `diff_message`.
- The response intentionally excludes inline patch text so the project view can render quickly even for large workspaces.

### `GET /workspaces/{workspace_id}/diff?path=...`

Returns the full diff payload for one changed file.
The response includes:
- the changed file `path` and `change_type`
- optional `previous_path` for renames and copies
- the inline `diff` text or a binary marker

Rules:
- `path` must be a relative workspace path and must match one file currently reported by `/diffs`.
- Binary files are reported with `is_binary = true` and a summary diff message instead of inline text hunks.

### `GET /workspaces/{workspace_id}/preview-file?path=...`

Streams one workspace image file for inline UI preview.

Rules:
- `path` may be a relative workspace path or an absolute path inside the workspace root.
- Paths that escape the workspace root are rejected.
- Only raster image files are supported for preview.
- Missing files return `404`.

### `POST /workspaces/{workspace_id}:fork`

Creates a forked execution workspace backed by a Git worktree.

Request:

```json
{
  "name": "alpha-project-fork",
  "start_ref": "origin/main"
}
```

Rules:
- Source workspace must exist and its `root_path` must be inside a Git repository.
- The backend normalizes `name` into the new `workspace_id`.
- `start_ref` is optional. When omitted, the backend fetches `origin main` and forks from the resolved `origin/main` commit.
- When `start_ref` is provided, the backend resolves that ref and forks from the resolved commit.
- The fork creates branch `fork/{workspace_id}` from the resolved start commit.
- The worktree directory is created under the managed workspace storage directory and becomes the new workspace `root_path`.
- The returned workspace profile uses `file_scope.backend = "git_worktree"` and includes `source_root_path`, `branch_name`, and `forked_from_workspace_id`.

### `POST /workspaces/pick`

Opens a native directory picker on the local machine, then registers the chosen
directory as a workspace. If the selected directory is already registered, the
existing workspace record is returned.

Request:

```json
{
  "root_path": "D:/workspace/agent_teams"
}
```

If `root_path` is provided, the server skips the native picker and registers the
specified directory directly.

Response:

```json
{
  "workspace": {
    "workspace_id": "agent-teams",
    "root_path": "D:/workspace/agent_teams",
    "profile": {
      "backend": "filesystem",
      "file_scope": {
        "backend": "project",
        "working_directory": ".",
        "readable_paths": ["."],
        "writable_paths": ["."],
        "branch_binding": "shared",
        "branch_name": null,
        "source_root_path": null,
        "forked_from_workspace_id": null
      }
    },
    "created_at": "2026-03-14T12:00:00Z",
    "updated_at": "2026-03-14T12:00:00Z"
  }
}
```

Rules:
- Returns `{ "workspace": null }` when the picker is cancelled.
- `root_path`, when provided, must already exist and be a directory.
- Linux native picking requires an installed desktop picker such as `zenity`,
  `qarma`, `yad`, or `kdialog`.
- Returns `503` when the runtime cannot open a native directory picker.

### `DELETE /workspaces/{workspace_id}`

Deletes one registered execution workspace.

Query:
- `remove_directory`: `true|false`
- `remove_worktree`: `true|false` (deprecated alias for `remove_directory`)

Request body:
- optional `force`: required when `remove_directory=true` or `remove_worktree=true`

Rules:
- When `remove_directory=false`, the backend deletes only the workspace record.
- When `remove_directory=true` and `force` is omitted or `false`, the backend returns `409` instead of removing the directory.
- When `remove_directory=true` for `file_scope.backend = "git_worktree"`, the backend runs `git worktree remove --force` before deleting the workspace record.
- When `remove_directory=true` for other workspace types, the backend deletes `root_path` before deleting the workspace record.

## Prompt APIs

### `POST /prompts:preview`

Builds prompt preview payload for a specific role. Coordinator role IDs are resolved from the loaded role files and are not hardcoded to any legacy identifier.

Request:

```json
{
  "role_id": "Coordinator",
  "workspace_id": "default",
  "objective": "Draft release note",
  "orchestration_prompt": "Delegate by capability and finalize yourself.",
  "shared_state": {"lang": "zh-CN", "priority": 1},
  "conversation_context": {
    "source_provider": "feishu",
    "source_kind": "im",
    "feishu_chat_type": "group"
  },
  "tools": ["orch_dispatch_task"],
  "skills": ["time"]
}
```

Notes:
- `objective` is optional.
- `workspace_id` is optional.
- `orchestration_prompt` is optional and participates in skill routing; coordinator preview also renders it into the prompt layers that normally receive runtime orchestration prompt context.
- `conversation_context` is optional.
- `shared_state` remains a free-form key/value object, but keys are trimmed and blank keys are rejected with `422`.
- When `workspace_id` is provided, `runtime_system_prompt` resolves `Working Directory` from the workspace execution root using the same workspace path resolution as real agent execution.
- `runtime_system_prompt` also includes any resolved instruction files loaded from the workspace/project chain, user-level prompt files, and `prompts.json` in the resolved config dir, by default `~/.relay-teams/prompts.json`.
- When `conversation_context.source_provider = "feishu"` and `conversation_context.feishu_chat_type = "group"`, both `runtime_system_prompt` and `provider_system_prompt` append the extra Feishu-group instruction:
  `当前对话来自飞书群聊；用户输入会包含发送者标识，你必须明确区分不同发送者，不要把群成员当作同一用户。`
- Other contexts leave the role system prompt unchanged.
- Skill requests accept effective runtime skill names.
- Prompt-facing preview output returns plain skill names.
- Roles/settings/skills management APIs also use effective runtime skill names.
- Same-name conflicts are resolved by source ordering before these APIs respond.
- When `workspace_id` does not exist, the endpoint returns `404`.
- When the authorized skill count is `<= 8`, the preview injects the stable
  skill catalog into `runtime_system_prompt` and `provider_system_prompt`, and
  `user_prompt` stays as the objective only.
- When the authorized skill count is `> 8`, routed skill candidates do not
  appear in `runtime_system_prompt` or `provider_system_prompt`; they only
  appear in `user_prompt`.
- When `objective` is omitted or blank, the preview response returns `objective: ""` and `user_prompt: ""`, but `skill_routing` may still report the effective authorized and visible skill sets.

Response:

```json
{
  "role_id": "Coordinator",
  "objective": "Draft release note",
  "tools": ["orch_dispatch_task"],
  "skills": ["time"],
  "runtime_system_prompt": "...",
  "provider_system_prompt": "...",
  "user_prompt": "Draft release note",
  "skill_routing": {
    "mode": "passthrough",
    "query_text": "Objective: Draft release note",
    "authorized_count": 1,
    "visible_skills": ["time"],
    "candidates": [],
    "fallback_reason": null
  }
}
```

## MCP APIs

### `GET /mcp/servers`

Lists effective MCP servers from app scope.
Each server summary includes MCP discovery cache fields:
- `discovery_status`: one of `disabled`, `pending`, `loading`, `ready`, or `failed`
- `tool_count`
- `last_checked_at`
- `error`

### `DELETE /mcp/servers/{server_name}`

Deletes one app-managed MCP server from the persisted app config and reloads the
runtime registry.

Behavior:
- Only app-managed MCP servers are deletable. Plugin-managed or other
  non-app-backed MCP servers are rejected.
- On success, the response returns the deleted server summary so callers can
  remove it from cached lists immediately.
- Unknown servers return `404`.
- Runtime config-manager unavailability returns `503`.

### `GET /mcp/servers/{server_name}/tools`

Returns cached discovery state and cached tools for one MCP server. This endpoint
does not open a live MCP connection. Unknown servers return `404`; known servers
return `200` for `pending`, `loading`, `ready`, `failed`, and `disabled` states.
Returned tool names are the effective callable names registered at runtime in
the form `<server_name>_<tool_name>` so tools from different MCP servers cannot
collide.

Response fields:
- `server`
- `source`
- `transport`
- `enabled`
- `tools`
- `status`: one of `disabled`, `pending`, `loading`, `ready`, or `failed`
- `last_checked_at`
- `error`

### `POST /mcp/servers/{server_name}/tools:refresh`

Queues background discovery for one enabled MCP server and returns the current
cached tool summary. This endpoint does not wait for `initialize` or
`tools/list` to complete. It forces rediscovery even when the cached status is
already `ready`. Unknown servers return `404`.

### `POST /mcp/servers/{server_name}/test`

Runs an explicit live connection test for one MCP server and may block while the
server initializes and lists tools. This endpoint is intended for the user's
manual "Test" action and is separate from the settings page's cached tool
display.

## Connector APIs

Connector APIs are aggregation endpoints under `/api/*`. Most connectors derive
state from their owning domain: GitHub state comes from `triggers`; Feishu,
WeChat, Discord, and Xiaoluban state comes from `gateway`. W3 is the unified
authentication connector exception: it stores non-sensitive connector metadata
in a JSON config file and stores the password in the unified secret store.

### `GET /connectors`

Returns only built-in connectors backed by existing implementations: GitHub,
Discord, Feishu, WeChat, Xiaoluban, and W3. Gmail, Slack, Jira, and other future
providers are not returned until their backend capability exists.

Response shape:
- `summary`: counts for `connected`, `needs_config`, `disabled`, `error`, and
  `total`
- `items`: connector rows with `connector_id`, `provider`, `category`,
  `display_name`, `description`, `status`, `auth_type`, `account_count`,
  `enabled_count`, `last_activity_at`, `last_error`, and `capabilities`

Enums:
- `provider`: `github`, `discord`, `feishu`, `wechat`, `xiaoluban`, or `w3`
- `category`: `auth`, `development`, `im`, or `models`
- `status`: `needs_config`, `connected`, `disabled`, or `error`

### `POST /connectors/{connector_id}:test`

Runs a lightweight health check for one built-in connector. GitHub uses the
existing GitHub connectivity probe. Feishu checks account secret readiness and
subscription runtime state. WeChat returns account running, login, and recent
error state. Xiaoluban checks token configuration, listener state, and IM
workspace configuration. W3 validates that its saved username/password can
obtain a non-empty MaaS `cloudDragonTokens.authToken`. This token is also the
W3 `WEB_TOKEN` and the `X-Auth-Token` used by MaaS/CodeMate and CodeAgent
password auth. Unknown connector ids return `404`.

### `GET /connectors/w3`

Returns W3 connector state for the unified authentication connector:
`username`, `has_password`, `status`, `updated_at`, `last_sync`, and
`last_error`. The password and raw `WEB_TOKEN` / `X-Auth-Token` are never
returned. `last_sync` is retained only for hidden maintenance compatibility and
is not part of the normal W3 setup flow.

### `PUT /connectors/w3`

Accepts `username` and an optional `password`. The first save requires a
password; later saves may omit it to keep the existing secret. The backend calls
the existing MaaS secure-login path and treats a non-empty
`cloudDragonTokens.authToken` as success. That token is the W3 `WEB_TOKEN` and
the request-header `X-Auth-Token`. On success, it saves the connector
credentials only. It does not discover models, create profiles, update existing
profiles, or persist the raw token. MaaS and CodeAgent password profiles can
reference W3 by saving `auth_source = "w3"` in their provider auth config.

### `POST /connectors/w3:test`

Validates request credentials or the saved W3 credentials. Success only means
the backend can obtain a non-empty `X-Auth-Token`; it does not call an inference
endpoint.

Future MCP or gateway integrations that need a W3 token should resolve it from
the saved W3 credentials on demand and map the resulting `WEB_TOKEN` to their
own environment variable names, such as `PRIVATE_TOKEN`, instead of persisting
the raw token.

### `GET /connectors/runtime-tools`

Lists project-managed runtime CLI tools shown on the Connectors page. This
endpoint only reports status and never starts a download or installation.

Response fields:
- `items[]`
  - `tool_id`: `rg`, `gh`, or `clawhub`
  - `display_name`
  - `version`, when the executable can be probed
  - `source_kind`: `github_release` or `npm_global`
  - `status`: `ready`, `missing`, `downloading`, or `error`
  - `path_source`: `managed`, `system`, or `npm_global`
  - `path`
  - `executable_name`
  - `download_job_id`, when a download is currently running
  - `error_message`

Notes:
- `rg` and `gh` are downloaded from their pinned GitHub release assets into the
  app bin directory when manually downloaded or first needed by runtime paths.
- `clawhub` is installed with npm and reports system or npm global paths.
- System tools such as `git`, `npm`, and shell binaries are intentionally not
  listed because the project probes them but does not download them.

### `POST /connectors/runtime-tools/{tool_id}:download`

Starts a manual runtime tool download or installation, or returns the existing
running job for the same tool. If the tool is already available, the response is
an immediately completed job. Unknown tool ids return `404`.

Response fields:
- `job_id`
- `tool_id`
- `status`: `queued`, `running`, `succeeded`, or `failed`
- `started_at`
- `updated_at`
- `downloaded_bytes`
- `total_bytes`
- `progress_percent`
- `message`
- `path`
- `error_message`

### `GET /connectors/runtime-tools/downloads/{job_id}`

Returns the latest status for a runtime tool download job. Direct release
downloads report byte progress when the server supplies `Content-Length`.
ClawHub npm installs report stage-based progress because npm does not provide a
stable byte count. Unknown job ids return `404`.

## Gateway APIs

### `GET /gateway/feishu/accounts`

Lists all persisted Feishu gateway accounts.

Each record includes:
- `account_id`
- `name`
- `display_name`
- `status`
- `source_config`
- `target_config`
- `secret_status`
- `secret_config`

### `POST /gateway/feishu/accounts`

Creates a Feishu gateway account and persists its secret config.
The request `name` and all `account_id` path parameters follow the common identifier validation rules above.

Rules:
- `source_config`, `target_config`, and `secret_config` use explicit nested Feishu models with `extra = forbid`; unknown nested fields return `422`.
- Omitting `target_config` uses the default Feishu trigger target settings.
- `secret_config.app_secret` is required on create.

### `PATCH /gateway/feishu/accounts/{account_id}`

Updates a Feishu gateway account. If the runtime credential signature changes, the backend reloads the Feishu long-connection runtime. If the target session preset changes, the backend clears the existing external chat bindings for that account.

Rules:
- The request body must include at least one field.
- `source_config`, `target_config`, and `secret_config` are explicit nested patch fields, not loose dictionaries.
- Empty config objects such as `{"target_config": {}}` or `{"secret_config": {}}` are rejected with `422`.
- Unknown nested fields return `422`.

### `POST /gateway/feishu/accounts/{account_id}:enable`

Enables a Feishu gateway account and reloads the Feishu long-connection runtime.

### `POST /gateway/feishu/accounts/{account_id}:disable`

Disables a Feishu gateway account and reloads the Feishu long-connection runtime.

### `DELETE /gateway/feishu/accounts/{account_id}`

Deletes the Feishu gateway account, removes its stored secret config, and clears its external chat bindings.

### `POST /gateway/feishu/reload`

Reloads all Feishu long-connection clients from the persisted gateway account list.

### Feishu Gateway Accounts

Feishu IM ingress is managed only through `/api/gateway/feishu/accounts`. The legacy `/api/triggers/*` endpoints have been removed.

Behavior:
- Uses the Feishu Python SDK long connection mode for inbound `im.message.receive_v1` events.
- Accepts `group` and `p2p` text messages.
- For `group` chats configured with `trigger_rule = "mention_only"`, only messages whose mention list includes the configured application name create runs.
- For `p2p` chats, any text message creates a run and `mention_only` does not require an application mention.
- Persists accepted inbound messages in a local message pool before execution.
- Resolves group sender names when possible and injects the actual run intent as
  `收到来自 {sender_name} 的飞书消息：{message}` with `sender_open_id` fallback.
- Deduplicates delivery using Feishu `message_id`, falling back to `event_id`.
- Same-chat inbound messages are processed in queue order.
- Inbound Feishu messages enter the shared gateway session ingress path and start
  detached runs only when the bound internal session is idle.
- A Feishu message never implicitly attaches to an already running session run.
- Accepted group messages use a Feishu reaction acknowledgement with emoji `eyes`.
- Only queued messages send a separate text reply: `已进入队列，前面还有 N 条消息。`
- Group command responses and group final run replies use Feishu reply-to-message on the triggering message.
- Reuses one internal session per `account_id + tenant_key + chat_id`.
- Requires no public callback URL.
- Runs one SDK long connection per enabled Feishu gateway account whose credentials are ready.
- Supports multiple Feishu bots at the same time.
- Supports session commands `help`, `status`, and `clear`.
- `status` shows both session usage and the current chat queue state.
- `clear` inserts the logical session history divider and also cancels active queued
  messages for that chat so they do not continue executing.

Recommended Feishu gateway contract:
- `source_config.provider = "feishu"`
- `source_config.trigger_rule = "mention_only"`
- `source_config.app_id = "<feishu_app_id>"`
- `source_config.app_name = "<feishu_app_name>"`
- `target_config.workspace_id = "default"` (or another registered workspace)
- `target_config.session_mode = "normal" | "orchestration"`
- `target_config.normal_root_role_id` is optional for normal mode
- `target_config.orchestration_preset_id` is required for orchestration mode
- `target_config.yolo = true` by default for Feishu-triggered runs
- `target_config.shell_safety_policy_enabled = true` by default for Feishu-triggered runs
- `target_config.thinking.enabled` and `target_config.thinking.effort` control per-bot run thinking settings
- Set `target_config.yolo = false` only when you want Feishu-triggered runs to keep the normal tool approval flow
- Set `target_config.shell_safety_policy_enabled = false` only when you want Feishu-triggered runs to skip the local shell safety policy and rely on the normal approval/runtime path instead
- `secret_config.app_secret` is required on create
- `secret_config.verification_token` is optional
- `secret_config.encrypt_key` is optional

Feishu-specific request shape:

```json
{
  "name": "feishu_ops",
  "source_config": {
    "provider": "feishu",
    "trigger_rule": "mention_only",
    "app_id": "cli_demo",
    "app_name": "Agent Teams Bot"
  },
  "target_config": {
    "workspace_id": "default",
    "session_mode": "normal",
    "normal_root_role_id": "MainAgent",
    "yolo": true,
    "shell_safety_policy_enabled": true,
    "thinking": {
      "enabled": false,
      "effort": "medium"
    }
  },
  "secret_config": {
    "app_secret": "..."
  }
}
```

Feishu-specific response additions:

- `secret_status.app_secret_configured`
- `secret_status.verification_token_configured`
- `secret_status.encrypt_key_configured`
- `secret_config.app_secret`

Notes:
- Feishu secrets are stored in the unified secret store, not in `.env`, and gateway account read/list responses include the current `secret_config` so the settings UI can mask it by default and reveal it on demand.
- When a Feishu gateway account's runtime preset changes, the backend clears that account's external chat bindings so the next message creates a session with the new preset.
- For inbound Feishu chat messages, terminal Feishu replies are owned by the message pool worker, and the generic Feishu `run_completed` / `run_failed` notification path is suppressed to avoid duplicate replies.

### `GET /gateway/xiaoluban/accounts`

Lists all persisted Xiaoluban gateway accounts.

Each record includes:
- `account_id`
- `display_name`
- `base_url`
- `status`: `enabled` or `disabled`
- `derived_uid`
- `notification_workspace_ids`
- `notification_receivers`
- `notify_self`: always `true` in responses; retained for compatibility
- `notification_receiver`: legacy compatibility projection of the first configured receiver
- `im_config.workspace_id`
- `secret_status.token_configured`
- `created_at`
- `updated_at`

### `POST /gateway/xiaoluban/accounts:prepare`

Prepares a Xiaoluban account id before creation and returns the corresponding IM forwarding data without writing a database row.

Response fields:
- `account_id`
- `forwarding_url`
- `forwarding_command`
- `listener_running`

The settings UI uses this endpoint so a new account form can display a copyable forwarding command before the account is saved.

Rules:
- `forwarding_url` and `forwarding_command` are user-visible Xiaoluban manual forwarding values and must not include a query string.
- Xiaoluban does not support forwarding commands such as `http://host:9009/xlb_123?auth=... g`; API responses must return `http://host:9009/xlb_123 g`.

### `POST /gateway/xiaoluban/accounts`

Creates a Xiaoluban gateway account and stores its personal token in the unified secret store.

Request fields:
- `account_id` optional prepared id from `/gateway/xiaoluban/accounts:prepare`
- `display_name`
- `token`
- `notification_workspace_ids`
- `notification_receivers`
- `notify_self` compatibility field; false values are ignored and notifications still include the token owner
- `notification_receiver` legacy compatibility field
- `im_config.workspace_id`

Rules:
- `token` is required on create, must be a personal token, and must not use a plugin-token `p_` prefix.
- `account_id`, when supplied, must use the generated Xiaoluban id format and must not already exist.
- `notification_receivers` accepts one or more group ids; server-side validation trims, splits common separators, removes blanks, and deduplicates.
- The token owner's `derived_uid` is always included as a notification recipient. Group IDs are additional recipients.
- Legacy `notification_receiver` input is still accepted for compatibility and is converted to a single `notification_receivers` value; it no longer disables notification to the token owner.

### `PATCH /gateway/xiaoluban/accounts/{account_id}`

Updates mutable Xiaoluban account settings.

Mutable fields:
- `display_name`
- `token`
- `base_url`
- `enabled`
- `notification_workspace_ids`
- `notification_receivers`
- `notify_self` compatibility field; false values are ignored and notifications still include the token owner
- `notification_receiver` legacy compatibility field
- `im_config.workspace_id`

### `POST /gateway/xiaoluban/accounts/{account_id}:reveal-token`

Returns the saved token for an existing Xiaoluban account from the server secret store:

```json
{"token":"uidself_1234567890abcdef1234567890abcdef"}
```

If no token is configured, `token` is `null`. List and account read responses continue to expose only `secret_status.token_configured`.

### `GET /gateway/xiaoluban/accounts/{account_id}/im:forwarding-command`

Returns the Xiaoluban manual forwarding URL and command for an existing account.

The returned `forwarding_url` and `forwarding_command` follow the same no-query rule as `/gateway/xiaoluban/accounts:prepare`; stripping query parameters is intentional for Xiaoluban compatibility.

### `PATCH /gateway/xiaoluban/accounts/{account_id}/im`

Updates the workspace used by inbound Xiaoluban IM-triggered tasks.

### `POST /gateway/xiaoluban/accounts/{account_id}:enable`

Enables one Xiaoluban account.

### `POST /gateway/xiaoluban/accounts/{account_id}:disable`

Disables one Xiaoluban account.

### `DELETE /gateway/xiaoluban/accounts/{account_id}`

Deletes one Xiaoluban account and removes its stored personal token.

Notes:
- Xiaoluban notification delivery always fans out to `derived_uid` and to every configured `notification_receivers` group id.
- Delivery continues after a single target fails; the operation raises only when all target sends fail.
- Existing rows with only the legacy `notification_receiver` column are read as one group receiver and still notify the token owner.

### `GET /gateway/discord/accounts`

Lists all persisted Discord gateway accounts.

Each record includes:
- `account_id`
- `display_name`
- `status`: `enabled` or `disabled`
- `bot_user_id`
- `application_id`
- `allowed_channel_ids`
- `allow_channel_messages`
- `workspace_id`
- `session_mode`
- `normal_root_role_id`
- `orchestration_preset_id`
- `yolo`
- `shell_safety_policy_enabled`
- `thinking`
- `secret_status.bot_token_configured`
- runtime status fields: `running`, `last_error`, `last_event_at`, `last_inbound_at`, `last_outbound_at`
- `created_at`
- `updated_at`

### `POST /gateway/discord/accounts`

Creates a Discord gateway account and stores its bot token in the unified secret store.
The backend validates the token by calling Discord's current-user API and uses the
returned bot user id as `account_id`.

Request fields:
- `display_name`
- `bot_token`
- `application_id`
- `enabled`
- `allowed_channel_ids`
- `allow_channel_messages`
- `workspace_id`
- `session_mode`
- `normal_root_role_id`
- `orchestration_preset_id`
- `yolo`
- `shell_safety_policy_enabled`
- `thinking`

Rules:
- `bot_token` is required on create.
- Unknown fields return `422`.
- `session_mode = "orchestration"` requires `orchestration_preset_id` or a configured default orchestration preset.
- `allowed_channel_ids` controls guild channel messages only; direct messages and bot mentions have separate acceptance rules.

### `PATCH /gateway/discord/accounts/{account_id}`

Updates mutable Discord account settings.

Mutable fields:
- `display_name`
- `bot_token`
- `application_id`
- `enabled`
- `allowed_channel_ids`
- `allow_channel_messages`
- `workspace_id`
- `session_mode`
- `normal_root_role_id`
- `orchestration_preset_id`
- `yolo`
- `shell_safety_policy_enabled`
- `thinking`

Notes:
- Updating `bot_token` must resolve to the same Discord bot user id as the existing account.
- The request body must include at least one field.
- Saving account settings immediately reloads Discord gateway workers.

### `POST /gateway/discord/accounts/{account_id}:enable`

Enables one Discord account and reloads gateway workers.

### `POST /gateway/discord/accounts/{account_id}:disable`

Disables one Discord account and reloads gateway workers.

### `DELETE /gateway/discord/accounts/{account_id}`

Deletes one Discord account and removes its stored bot token from the unified secret store.

### `POST /gateway/discord/reload`

Reloads all Discord gateway workers against the current persisted account set.

Discord behavior:
- Discord is managed as a long-lived conversational gateway, not as a trigger.
- Accepted direct messages, bot mentions, and configured channel messages are persisted into a local inbound queue before run start.
- Guild messages that only mention the bot and contain no task text are ignored.
- Inbound Discord messages use the shared gateway session ingress path, so busy sessions queue later messages instead of auto-attaching them to the active run.
- Final run output is sent back through Discord REST to the source channel or thread.

### `GET /gateway/wechat/accounts`

Lists all persisted WeChat gateway accounts.

Each record includes:
- `account_id`
- `display_name`
- `base_url`
- `cdn_base_url`
- `route_tag`
- `status`: `enabled` or `disabled`
- `remote_user_id`
- `sync_cursor`
- `workspace_id`
- `session_mode`
- `normal_root_role_id`
- `orchestration_preset_id`
- `yolo`
- `thinking`
- `last_login_at`
- `running`
- `last_error`
- `last_event_at`
- `last_inbound_at`
- `last_outbound_at`
- `created_at`
- `updated_at`

Notes:
- WeChat is managed as a long-lived conversational gateway, not as a trigger.
- Current implementation handles direct chat only. Group chat routing is reserved for later expansion.
- Accepted WeChat direct messages are persisted into a local inbound queue before run start.
- WeChat inbound messages also use the shared gateway session ingress path, so busy
  sessions queue later messages instead of auto-attaching them to the active run.

### `POST /gateway/wechat/login/start`

Starts a QR-code login flow for one WeChat account.

Request fields:
- `base_url` optional
- `route_tag` optional
- `bot_type` optional, defaults to `3`

Response fields:
- `session_key`
- `qr_code_url`
- `message`

### `POST /gateway/wechat/login/wait`

Waits for a previously started QR login flow to finish.

Request fields:
- `session_key`
- `timeout_ms`

Response fields:
- `connected`
- `account_id`
- `message`

Notes:
- On success, the backend stores the returned bot token in the unified secret store and upserts the account into `wechat_accounts`.
- `session_key` and `account_id` follow the common identifier validation rules above.
- Newly connected accounts default to `workspace_id = "default"` and `session_mode = "normal"` unless a previous record already exists for that account id.

### `PATCH /gateway/wechat/accounts/{account_id}`

Updates mutable WeChat gateway account settings.

Mutable fields:
- `display_name`
- `base_url`
- `cdn_base_url`
- `route_tag`
- `enabled`
- `workspace_id`
- `session_mode`
- `normal_root_role_id`
- `orchestration_preset_id`
- `yolo`
- `thinking`

Notes:
- The request body must contain at least one field.
- `display_name`, `base_url`, `cdn_base_url`, and `route_tag` are trimmed. Blank strings are rejected with `422`.
- Sending `route_tag = null` clears the stored route tag.
- `session_mode = "orchestration"` requires `orchestration_preset_id` or a configured default orchestration preset.
- Saving account settings immediately reloads the WeChat gateway workers.

### `POST /gateway/wechat/accounts/{account_id}:enable`

Enables one WeChat account and reloads gateway workers.

### `POST /gateway/wechat/accounts/{account_id}:disable`

Disables one WeChat account and reloads gateway workers.

### `DELETE /gateway/wechat/accounts/{account_id}`

Deletes one WeChat account and removes its stored bot token from the unified secret store.

### `POST /gateway/wechat/reload`

Reloads all WeChat gateway workers against the current persisted account set.

## Memory Notes

- `workspace` now means execution workspace only.
- Durable role memory is stored in the database and keyed by `role_id + workspace_id`.
- Daily role memory is stored in the database and keyed by `role_id + workspace_id + memory_date + kind`.

## Observability APIs

### `GET /observability/overview`

Returns observability KPIs and trend buckets for `scope=global|session|run`. Non-global scopes require `scope_id`.

Overview KPIs include:
- `steps`
- `input_tokens`
- `cached_input_tokens`
- `uncached_input_tokens`
- `output_tokens`
- `cached_token_ratio`
- `tool_calls`
- `tool_success_rate`
- `tool_avg_duration_ms`
- `skill_calls`
- `mcp_calls`
- `retrieval_searches`
- `retrieval_failure_rate`
- `retrieval_avg_duration_ms`
- `retrieval_document_count`
- `gateway_calls`
- `gateway_failure_rate`
- `gateway_avg_duration_ms`
- `gateway_prompt_avg_start_ms`
- `gateway_prompt_avg_first_update_ms`
- `gateway_mcp_calls`
- `gateway_cold_start_calls`

### `GET /observability/breakdowns`

Returns tool-level breakdown rows for `scope=global|session|run`. Non-global scopes require `scope_id`.

Breakdown payload includes:
- `rows`: tool-level call/failure/latency breakdown
- `role_rows`: role-level token/cache/tool-failure breakdown
- `gateway_rows`: gateway ACP and MCP operation call/failure/latency breakdown grouped by operation, phase, and transport

## Automation APIs

### `GET /automation/feishu-bindings`

Lists Feishu chat bindings that automation projects are allowed to target.
Candidates come from existing inbound Feishu IM chat bindings already recorded by the backend.

Each record includes:
- `provider`: `feishu`
- `trigger_id`
- `trigger_name`
- `tenant_key`
- `chat_id`
- `chat_type`
- `source_label`
- `session_id`
- `session_title`
- `updated_at`

### `GET /automation/projects`

Returns all automation projects.
Each record includes:
- `automation_project_id`
- `name`
- `display_name`
- `status`: `enabled` or `disabled`
- `prompt`
- `schedule_mode`: `interval`, `cron`, or `one_shot`
- `interval_every`
- `interval_unit`: `minutes`, `hours`, or `days`
- `cron_expression`
- `run_at`
- `timezone`
- `run_config`
  - `session_mode`
  - `normal_root_role_id`
  - `orchestration_preset_id`
  - `execution_mode`
  - `yolo`
  - `thinking`
- `delivery_binding`
- `delivery_events[]`: `started`, `completed`, `failed`
- `trigger_id`
- `last_session_id`
- `last_run_started_at`
- `last_error`
- `next_run_at`

### `POST /automation/projects`

Creates an automation project and a backing schedule trigger.
Request fields:
- `name`
- `display_name` optional
- `prompt`
- `schedule_mode`
- `interval_every` and `interval_unit` for `interval`
- `cron_expression` for `cron`
- `run_at` for `one_shot`
- `timezone`
- `run_config`
  - `session_mode`
  - `normal_root_role_id` optional for `normal`
  - `orchestration_preset_id` required for `orchestration`
  - `execution_mode`
  - `yolo`
  - `thinking`
- `delivery_binding` optional
  - `provider = "feishu"`
  - `trigger_id`
  - `tenant_key`
  - `chat_id`
  - `session_id`
  - `chat_type`
  - `source_label`
- `delivery_events[]` optional
- `enabled`

Notes:
- Interval schedules run every `interval_every` `interval_unit` and do not
  backfill missed periods after downtime or polling delays.
- Friendly daily, weekday, weekly, and monthly UI schedules are persisted as
  five-field cron expressions. Advanced cron mode writes the same
  `cron_expression` field directly.
- `delivery_binding` must reference an existing Feishu IM chat binding returned by `GET /automation/feishu-bindings`.
- `delivery_binding.session_id` is required for explicit create/update requests and binds the automation project to that exact saved session.
- When `delivery_binding` is present and `delivery_events` is omitted, the backend defaults to `started`, `completed`, and `failed`.
- Explicit create/update requests validate `run_config.normal_root_role_id` against the current normal-mode role registry.
- Explicit create/update requests validate `run_config.orchestration_preset_id` against the current orchestration presets and reject missing presets in orchestration mode.
- `run_config.normal_root_role_id` is ignored when `session_mode = "orchestration"`.
- `run_config.orchestration_preset_id` is ignored when `session_mode = "normal"`.
- When a bound session cannot be resolved at run time, the run fails instead of falling back to a fresh automation session.
- `workspace_id`, `automation_project_id`, and delivery-binding identifiers follow the common identifier validation rules above.

### `GET /automation/projects/{automation_project_id}`

Returns one automation project.

### `PATCH /automation/projects/{automation_project_id}`

Updates automation project definition, schedule, stored run config, and optional Feishu delivery binding.
Explicit `run_config` updates follow the same validation rules as create requests.
Schedule validation is mode-specific: `interval` accepts only interval fields,
`cron` accepts only `cron_expression`, and `one_shot` accepts only `run_at`.

### `DELETE /automation/projects/{automation_project_id}`

Deletes the automation project and its backing trigger. Historical sessions are preserved.
Request body may include:
- optional `force`: required when the project is still enabled
- optional `cascade`: required when delivery records or bound-session queue records already exist and the caller wants the backend to remove them together with the project
If related delivery or queue data exists and `cascade` is omitted or `false`, the backend returns `409` instead of performing an implicit cascading delete.

### `POST /automation/projects/{automation_project_id}:run`

Starts the automation project immediately. Unbound projects create a fresh automation
session; projects with a Feishu delivery binding reuse the exact saved bound session.
Response fields:
- `automation_project_id`
- `session_id`

Behavior notes:
- Bound-session automation execution also goes through the shared gateway session
  ingress path and always starts detached runs.
- If the bound session is busy, the automation job queues behind the current
  session backlog instead of inserting prompt text into the active run.
- Manual runs do not advance the scheduled `next_run_at` cursor for recurring
  `interval` and `cron` schedules. Manual `one_shot` runs still disable the
  project and clear `next_run_at`.

### `POST /automation/projects/{automation_project_id}:enable`

Enables scheduling and recomputes `next_run_at`.

### `POST /automation/projects/{automation_project_id}:disable`

Disables scheduling and clears `next_run_at`.

### `GET /automation/projects/{automation_project_id}/sessions`

Returns sessions generated for one automation project.

## Session Projection Additions

`GET /sessions` and `GET /sessions/{session_id}` now also include:
- `project_kind`: `workspace` or `automation`
- `project_id`: workspace id or automation project id used by the sidebar grouping logic

## Guardrail Audit API

### `GET /guardrails/audit`

Lists guardrail audit records for compliance and debugging.

Query fields:
- `run_id`: optional exact-match filter.
- `task_id`: optional exact-match filter.
- `role_id`: optional exact-match filter.
- `layer`: optional filter by guardrail layer (`PRE_EXECUTION`, `IN_EXECUTION`, `POST_EXECUTION`).
- `action`: optional filter by action (`DENY`, `WARN`, `ALLOW`, `REMASk`).
- `triggered_only`: optional boolean, default `false`. When `true`, only returns records where a guard rule triggered.
- `since`, `until`: optional ISO 8601 timestamp filters on `evaluated_at`.
- `limit`: page size from `1` to `500`, default `100`.
- `offset`: default `0`.

Response fields:
- `items[]`: array of guardrail audit record objects.
- `total`: total number of matching records.
- `next_offset`: offset for the next page, or `null` if no more results.

Router: `src/relay_teams/interfaces/server/routers/guardrails_router.py`

## Memory Bank APIs

Structured Memory Bank entries with three tiers (`working`, `medium_term`, `persistent`), three scopes (`workspace`, `session`, `role`), typed content, tags, confidence, consolidation, and search. The legacy role-memory endpoints were removed; subagent panels and the global Memory page read from these endpoints.

### `GET /memories`

Lists Memory Bank entries across all workspaces, or within one workspace when
`workspace_id` is supplied.

Query fields:
- `workspace_id`: optional exact-match filter.
- `tier`: optional `working`, `medium_term`, or `persistent`.
- `scope`: optional `workspace`, `session`, or `role`.
- `session_id`: optional exact-match filter.
- `role_id`: optional exact-match filter.
- `kind`: optional `insight`, `constraint`, `decision`, `failure_mode`, `preference`, `fact`, or `summary`.
- `status`: optional `active`, `superseded`, or `expired`.
- `tags`: comma-separated tag filter.
- `min_confidence`: minimum confidence score `0.0..1.0`, default `0.0`.
- `limit`: page size `1..100`, default `20`.
- `offset`: default `0`.

Response: `MemoryQueryResult`.

### `POST /memories/search`

Searches Memory Bank entries across all workspaces, or within one workspace
when `workspace_id` is supplied. Search defaults to `status=active`; pass
`status=superseded`, `status=expired`, or `status=null` to inspect non-active
history.

Request: `GlobalMemorySearchRequest`
- `workspace_id`: optional exact-match filter.
- `text_query`: search text.
- `tier`, `scope`, `session_id`, `role_id`, `kind`, `status`, `tags`: optional filters.
- `min_confidence`: minimum confidence score `0.0..1.0`.
- `limit`: max results, default `20`.

Response: `MemorySearchResult`.

### `GET /workspaces/{workspace_id}/memories`

Lists memory entries with optional filters.

Query fields:
- `tier`: optional `working`, `medium_term`, or `persistent`.
- `scope`: optional `workspace`, `session`, or `role`.
- `session_id`: optional exact-match filter.
- `role_id`: optional exact-match filter.
- `kind`: optional entry kind filter (`insight`, `constraint`, `decision`, `failure_mode`, `preference`, `fact`, `summary`).
- `status`: optional `active`, `superseded`, or `expired`.
- `tags`: comma-separated tag filter.
- `min_confidence`: minimum confidence score `0.0..1.0`, default `0.0`.
- `limit`: page size `1..100`, default `20`.
- `offset`: default `0`.

Response: `MemoryQueryResult`
- `items[]`: array of `MemoryEntrySummary` objects.
- `total_count`: total matching entries.
- `offset`: current page offset.
- `limit`: current page size.

### `POST /workspaces/{workspace_id}/memories`

Creates a memory entry. Returns `201` on success.

Request: `CreateMemoryEntryRequest`
- `workspace_id`: path-derived.
- `tier`: `working`, `medium_term`, or `persistent`.
- `scope`: `workspace`, `session`, or `role`.
- `kind`: `insight`, `constraint`, `decision`, `failure_mode`, `preference`, `fact`, or `summary`.
- `content`: object with `title`, `body`, and optional `context`/`outcome`.
- `tags`: optional list of tag strings.
- `source`: optional `consolidation`, `manual`, `condensation`, or `task_result`.
- `confidence_score`: optional `0.0..1.0`.
- `session_id`, `role_id`, `run_id`: optional scoping references.
- `expires_at`: optional ISO 8601 expiry timestamp.

Response: `MemoryEntry` (full entry with generated `id`, `version`, timestamps).

### `GET /workspaces/{workspace_id}/memories/{memory_id}`

Returns a single memory entry.

Response: `MemoryEntry`. Returns `404` when the entry does not exist or does not belong to the workspace.

### `PUT /workspaces/{workspace_id}/memories/{memory_id}`

Updates a memory entry.

Request: `UpdateMemoryEntryRequest`
- `content`: optional replacement content object.
- `tags`: optional replacement tags.
- `confidence_score`: optional new score.
- `status`: optional new status (`active`, `superseded`, or `expired`).
- `expires_at`: optional new expiry.
- `metadata`: optional replacement metadata.

Response: `MemoryEntry`. Returns `404` when the entry does not exist or does not belong to the workspace.

### `DELETE /workspaces/{workspace_id}/memories/{memory_id}`

Deletes a memory entry. Returns `204` on success. Returns `404` when the entry does not exist or does not belong to the workspace.

### `POST /workspaces/{workspace_id}/memories/consolidate`

Triggers memory consolidation from working-tier entries into medium-term or persistent entries.

Request: `MemoryConsolidationRequest`
- `workspace_id`: path-derived.
- `target_tier`: consolidation target tier.
- `target_scope`: consolidation target scope.
- Optional filtering fields.

Response: `MemoryConsolidationResult`
- `consolidated_entry_count`: number of new entries created.
- `source_entry_count`: number of source entries examined.
- `superseded_entry_ids[]`: source entries superseded during consolidation.
- `new_entry_ids[]`: newly created memory entry IDs.

### `POST /workspaces/{workspace_id}/memories/search`

Full-text search across memory entries.

Request: `MemorySearchRequest`
- `workspace_id`: path-derived.
- `text_query`: search text.
- `tier`, `scope`, `session_id`, `role_id`, `kind`, `status`, `tags`: optional filters.
- `min_confidence`: minimum confidence score `0.0..1.0`.
- `limit`: max results, default `20`.

Response: `MemorySearchResult`
- `items[]`: array of ranked results with `entry`, `score`, `rank`, and optional `snippet`.
- `total_count`: total matching entries.

Router: `src/relay_teams/interfaces/server/routers/memories.py`

## Client SDK

### `AsyncAgentTeamsClient`

Async HTTP client for the Agent Teams server API. All methods are
`async` and return JSON-marshalled dictionaries or typed models
(`RunHandle`).

Construction:
- `base_url`: default `"http://127.0.0.1:8000"`.
- `timeout_seconds`: request timeout, default `30.0`.
- `stream_timeout_seconds`: SSE stream timeout, default `600.0`.

Module: `src/relay_teams/interfaces/sdk/client.py`
Package export: `from relay_teams import AsyncAgentTeamsClient`

### `SyncAgentTeamsClient`

Synchronous wrapper around `AsyncAgentTeamsClient`. Every method
delegates to the async client via `asyncio.run`, providing a
blocking API for scripts, tests, and non-async callers.

Construction mirrors `AsyncAgentTeamsClient` exactly. Streaming
methods (`stream_run_events`) are adapted to return plain `list`.

Module: `src/relay_teams/interfaces/sdk/client.py`
Package export: `from relay_teams import SyncAgentTeamsClient`

---

## Boards

Task board integration for external trackers (GitHub Issues, Linear, internal).

### GET /api/boards

List configured boards.

Response: array of `BoardSummaryResponse`:
- `board_id`: board identifier
- `adapter`: adapter type ("internal", "github", "linear")
- `config`: full board configuration object

### GET /api/boards/{board_id}/tasks

List tasks on a board.

Response: array of `BoardTaskResponse`:
- `board_task_id`: external tracker task ID
- `title`: task title
- `description`: task description
- `state`: current board state
- `assignee`: assigned user (nullable)
- `labels`: array of label strings
- `source_url`: link to external tracker

### POST /api/boards/{board_id}/sync

Manually trigger a board synchronization.

Response:
- `synced`: true
- `board_id`: board identifier

### PUT /api/boards/{board_id}/tasks/{task_id}/state

Manually update a board task's state.

Request body:
- `state`: new board state (backlog/ready/in_progress/in_review/blocked/completed/cancelled)

Response:
- `updated`: true
- `board_id`: board identifier
- `task_id`: task identifier
- `state`: new state

### GET /api/boards/state-map

Get the current `TaskBoardStateMap` -- bidirectional mapping between internal `TaskStatus` and board `BoardTaskState`.

Response: `StateMapResponse`
- `task_status_to_board`: mapping from TaskStatus name to BoardTaskState name
- `board_state_to_task_status`: mapping from BoardTaskState name to tuple of TaskStatus names

### Workspace TODO Board

Workspace TODO board APIs are owned by the boards domain and are independent from
external tracker state. Configured GitHub issue sources import TODOs. Local or
manual TODO rows are no longer supported board items; if old data exists, board
list, delta, and sync responses ignore it. Pull requests are linked
review/completion evidence. Board columns are Agent Teams state.

- `GET /api/boards/todos?workspace_id=...&include_archived=false` returns a full `BoardTodoBoardResponse`, including `source_groups` for grouped board rendering.
- `GET /api/boards/todos:changes?workspace_id=...&include_archived=false&after_revision=...` returns a `BoardTodoDeltaResponse`, including the current `source_groups`.
- `GET /api/boards/todo-sources?workspace_id=...` returns user-configurable external TODO sources for the resolved board workspace.
- `POST /api/boards/todo-sources` creates a user-managed `github_issues` source.
- `PATCH /api/boards/todo-sources/{source_id}` edits `display_name`, `repository_full_name`, or `enabled` for a user-managed source.
- `DELETE /api/boards/todo-sources/{source_id}` deletes an unused user-managed source. Sources with imported TODOs must be disabled with `enabled=false` instead.
- `POST /api/boards/todos:sync` performs a force-full sync across enabled GitHub issue sources and returns a full board response.
- `POST /api/boards/todos:sync-changes` performs an incremental GitHub issue/PR sync against per-source cursors and returns a delta response. `force_full=true` performs the same open-issue reconciliation as `/api/boards/todos:sync`.
- `POST /api/boards/todos/{todo_id}:preview-start` renders the default start prompt and runtime-control defaults for user review without changing status or creating a session/run.
- `POST /api/boards/todos/{todo_id}:start` requires a non-empty `final_prompt` after user review, creates a dedicated session/run with the selected runtime controls, and moves the item to `in_progress`. `prompt` is accepted as a compatibility alias.
- `POST /api/boards/todos/{todo_id}:request-changes` creates a new run in the bound session and moves the item back to `in_progress`.
- `POST /api/boards/todos/{todo_id}:mark-done` accepts an optional reason and moves a `review` item to `done`; non-`review` items return `409`.
- `POST /api/boards/todos/{todo_id}:archive` soft-deletes the item into `archived`.
- `POST /api/boards/todos/{todo_id}:restore` restores an archived item to `todo`.
- `POST /api/boards/todos/{todo_id}:link-pr` links an imported issue item to a pull request.

The first source settings/read or board sync bootstraps GitHub source
configuration once per board workspace. If the root workspace git remote can be
resolved to `owner/repo`, the backend creates one enabled `github_issues`
source. If it cannot be resolved, the API returns diagnostics and no source is
created; users can add sources manually. Once bootstrap has run, future syncs use
the persisted source list and do not recreate deleted or disabled sources from
the git remote.

`source_groups` is a non-persisted display contract derived from configured
external sources and current imported items. It contains one group per configured
external source and may contain missing-source groups for existing imported items
whose source setting is no longer present. There is no Manual pseudo group.
Frontends may render `grouped` mode by nesting every status column under these
groups, or `mixed` mode by showing status columns without group nesting while
still displaying each card's source label.

`preview-start` accepts optional `view_workspace_id` and returns
`board_workspace_id`, `view_workspace_id`, `is_fork_view`,
`forked_from_workspace_id`, the rendered `prompt`, and runtime defaults:
`session_mode`, `normal_root_role_id`, `normal_mode_roles`,
`orchestration_preset_id`, `orchestration_presets`, `yolo`, and `thinking`.
The first phase may return empty role/preset option arrays when clients already
load those option sets through the shared role and orchestration config APIs.

`start` accepts `view_workspace_id`, `final_prompt`, `session_mode`,
`normal_root_role_id`, `orchestration_preset_id`, `yolo`, and `thinking`.
For fork views, `view_workspace_id` selects the workspace where the run session
is created while the TODO item still belongs to the resolved root board.
`session_mode = normal` applies `normal_root_role_id` to the created session and
run `target_role_id`; `session_mode = orchestration` applies
`orchestration_preset_id` to the created session and does not set a normal target
role. `thinking.enabled` and `thinking.effort` are passed to the run intent.

`BoardTodoItem.status` is one of `todo`, `in_progress`, `review`, `done`, or
`archived`. Run completion moves bound items to `review`; users can mark
review items done after manual acceptance; linked GitHub pull request merges
move non-archived items to `done`. Bound `in_progress` items do
not automatically regress to `todo` for `failed`, `stopped`, `paused`,
`stopping`, `queued`, or `running` run runtime states while their runtime row
still exists. A missing bound run is treated as stale and returns to `todo`
with cleared session/run references. If the bound session is deleted,
non-archived and non-`done` items return to `todo` and clear stale session/run
references.

Board item responses include non-persisted runtime display fields when a bound
run runtime exists: `run_status`, `run_phase`, `run_recoverable`, and
`run_last_error`. These fields are derived from `run_runtime` and are not stored
in `board_todo_items`.

`BoardTodoItem.updated_at` is the local board row update time used for
revision/delta bookkeeping and status-machine mutations. GitHub issue items also
include `source_updated_at`, copied from GitHub issue `updated_at`; UI time
sorting uses `source_updated_at || updated_at` so issue ordering follows GitHub
activity rather than local sync/write time.

`workspace_id` in board API requests is the current view workspace. If the view
workspace is a `git_worktree` fork, board sources and sync cursors resolve to the
root board workspace. Board, delta, source settings, and prompt preview responses
include `board_workspace_id`, `view_workspace_id`,
`is_fork_view`, and `forked_from_workspace_id` so clients can cache settings by
the root board while preserving the current view.

On first board load, source settings load, or sync, if source bootstrap has not
run and the root workspace git remote resolves to `owner/repo`, the service
creates an enabled persisted `github_issues` source for that repository. After
creation, sync uses the persisted source configuration as the source of truth;
users can edit the repo, disable the source, delete unused sources, or add
another source in settings. If no GitHub remote can be resolved, the API returns
diagnostics and no source is created.

`BoardTodoBoardResponse` includes the current `revision`. Delta responses include
`changed_items`, `removed_todo_ids`, `status_counts`, diagnostics, `synced_at`,
and the latest `revision`. Active-view deltas report newly archived rows as
`removed_todo_ids`; archived-view deltas return them as changed items.

GitHub issues are the only GitHub TODO source. Pull requests are linked review
evidence and are not imported as standalone TODO cards. Closed GitHub issues are
not imported as new TODOs; existing active issue items observed as closed are
archived unless they already have a merged linked PR, in which case they move to
`done`. Full GitHub sync treats the current GitHub open issue set as the active
truth and archives previously active GitHub issue items that are no longer open,
with status reason `GitHub issue no longer open`. If GitHub later reports the
same issue as open, sync restores items archived by GitHub closed/non-open
reconciliation to `todo`; user-archived rows still require explicit `restore`.

Domain module: `src/relay_teams/boards/`

Router: `src/relay_teams/interfaces/server/routers/boards.py`

---

## A2A Internal Bus

Run-scoped internal Agent-to-Agent event bus. Provides real-time
message passing between peer agents within the same run.

### GET /api/runs/{run_id}/a2a/bus

Get the A2A bus state snapshot for a run.

Response: `A2aBusStateResponse`
- `run_id`: run identifier
- `message_count`: total messages published
- `subscription_count`: active subscriptions
- `active_topics`: tuple of topic names with active subscribers

### GET /api/runs/{run_id}/a2a/messages

Query published A2A messages for a run.

Query parameters:
- `topic`: filter by topic (optional)
- `role_id`: filter by sender/target role (optional)

Response: array of `A2aMessageResponse`

### GET /api/runs/{run_id}/a2a/subscriptions

Query A2A subscriptions for a run.

Response: array of `A2aSubscriptionResponse`

### POST /api/runs/{run_id}/a2a/messages

Manually publish an A2A message (debug/diagnostic).

Request body:
- `sender_role_id`: role publishing the message
- `sender_instance_id`: instance publishing the message
- `topic`: message topic
- `content`: message body
- `payload_json`: structured payload (optional, default `{}`)
- `target_role_id`: target role for direct message (optional, null = broadcast)

Response:
- `published`: true
- `run_id`: run identifier
- `topic`: message topic

Router: `src/relay_teams/interfaces/server/routers/a2a_internal.py`
