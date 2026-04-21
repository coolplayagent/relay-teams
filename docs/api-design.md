# Agent Teams API Design

## Overview

- Base path: `/api`
- Content type: `application/json`
- Streaming endpoint: `text/event-stream`
- Time fields: ISO 8601 UTC strings
- Orchestration model: task-only. There is no workflow graph API, workflow template registry, or persisted dependency DAG.

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

## Core Concepts

- A run starts from one root task.
- Sessions have a run mode:
  - `normal`: one session-selected root role handles the run directly. The default is `MainAgent`.
  - `orchestration`: the root role is `Coordinator`, and delegation is limited by the selected orchestration preset.
- Session mode and orchestration preset can be changed only before the session starts its first run.
- Every delegated task is a persisted task record under that root task.
- A delegated task binds to exactly one delegated role and one subagent instance on first dispatch.
- Re-dispatching an `assigned` or `stopped` task reuses its bound instance.
- `completed`, `failed`, and `timeout` tasks must be replaced instead of re-dispatched.
- In one session, delegated tasks with the same bound `role_id` reuse the same session-level subagent instance.
- Same-role task dispatch is serial only. If a role instance is already busy or paused on another task, dispatch returns a runtime conflict.

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
  - `builtin_skill_refs`
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
Each profile includes `has_api_key`, the currently stored `api_key` value so the web UI can mask it by default and reveal it on demand, `headers[]` for additional request headers, `is_default` to mark the runtime default profile, optional `context_window` for next-send context preview UI, optional `fallback_policy_id` to bind that profile to a fallback policy, and `fallback_priority` to rank it as a fallback candidate.
`provider` currently supports `openai_compatible`, `bigmodel`, `minimax`, `maas`, and the internal/testing-only `echo`. MAAS profiles also return `maas_auth` with `username` and `has_password` so the web UI can preserve the stored password without echoing it back. The MAAS login endpoint and `app-id` are fixed by the backend.
When no profile is explicitly marked default, the backend resolves the default in this order: a profile named `default`, the only configured profile, then the first profile by name.

### `PUT /system/configs/model/profiles/{name}`

Upserts a model profile.
Request body may include optional `source_name` to rename an existing profile while preserving its stored API key and secret headers when `api_key` and `headers` are omitted.
If `source_name` does not exist, the backend returns `404`. Profile-level semantic validation failures that occur after request parsing, such as invalid secret-header state or missing MAAS password on first configuration, return `400`.
`provider` accepts `openai_compatible`, `bigmodel`, `minimax`, `maas`, and `echo`.
Profiles may also include optional `ssl_verify` to override the global outbound TLS verification default for that model only.
Profiles may include `is_default` to promote that profile to the runtime default; saving one default clears the flag from all others.
Profiles may include optional `context_window` to declare the total model context limit separately from `max_tokens`, which remains the output-token cap when explicitly set. If `max_tokens` is omitted, the backend preserves that unset state and lets the provider decide the default output cap for primary LLM requests.
Profiles may include optional `fallback_policy_id` to enable quota/rate-limit fallback for that profile. The referenced policy id must exist in `model-fallback.json`. Profiles may also include `fallback_priority`; higher values are preferred when the profile is selected as a fallback candidate.
Profiles may include `headers[]`, where each item has `name`, optional `value`, optional `secret`, and optional `configured`.
Profiles must provide at least one auth source: `api_key`, one configured header, or `maas_auth` for `provider = "maas"`.
When `provider = "maas"`, `maas_auth` must include `username`; `password` is accepted on write but persisted only in the unified secret store. The backend always authenticates against `http://rnd-idea-api.huawei.com/ideaclientservice/login/v4/secureLogin`, always sends `app-id: RelayTeams`, and always uses `http://snapengine.cida.cce.prod-szv-g.dragon.tools.huawei.com/api/v2/` as the MAAS inference base URL. When `context_window` is omitted and the backend recognizes the provider/model pair, it may auto-fill a known context limit during save and runtime load.

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
Draft overrides may include optional `ssl_verify`; effective TLS verification resolves as `override.ssl_verify` -> global `SSL_VERIFY` -> default `false`.
Draft overrides may include `headers[]` and may omit `api_key` when headers are provided. MAAS draft overrides use `maas_auth` instead of `api_key` and perform a login before probing `/chat/completions`.
If `timeout_ms` is omitted, the backend uses the resolved profile `connect_timeout_seconds` value, or `15s` when no saved profile is involved.

### `POST /system/configs/model:discover`

Fetches the available model catalog for a saved profile and/or draft override.
Draft overrides may omit `model`, but must provide `base_url` and `api_key` or `headers` when `profile_name` is omitted. For `provider = "maas"`, the override must provide `base_url` and `maas_auth`.
When `profile_name` is provided, the request may override `base_url`, `api_key`, `headers`, and `ssl_verify` while reusing the saved credentials for any omitted fields.
If `timeout_ms` is omitted, the backend uses the resolved profile `connect_timeout_seconds` value, or `15s` when no saved profile is involved.
`openai_compatible`, `bigmodel`, and `minimax` map this call to `GET {base_url}/models` and return the normalized `models` list sorted and deduplicated. `maas` maps this call to the fixed PromptCenter discovery endpoint after MAAS login, using the returned `X-Auth-Token` plus department info from `userInfo` to build the discovery request payload.
For `maas`, the backend merges model ids from top-level `user_model_list` and nested `plugin_config[].config` payloads, filters invalid ids, then returns sorted deduplicated ids in `models[]` and `model_entries[]`.
When the provider exposes per-model context-limit metadata in the catalog payload, the response also includes `model_entries[]` with:
- `model`
- optional `context_window`

The settings UI uses `model_entries[].context_window` to auto-fill the profile context window field after model discovery. Providers that return only model ids will still populate `models[]`, but `context_window` remains user-specified.
For a small set of known provider/model pairs, the backend also applies a built-in context-window fallback when the provider returns only model ids.

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
- `ref`: canonical runtime ref such as `app:skill-creator` when valid
- `scope`: always `app`
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

### `GET /system/configs/agents`

Returns configured external ACP agents.

Each item includes:
- `agent_id`
- `name`
- `description`
- `transport`: `stdio`, `streamable_http`, or `custom`

### `GET /system/configs/agents/{agent_id}`

Returns one saved external agent config.

The `transport` field is a discriminated union:
- `stdio`: `command`, `args[]`, optional `env[]`
- `streamable_http`: `url`, optional `headers[]`, optional `ssl_verify`
- `custom`: `adapter_id`, `config`

Binding items under `env[]` or `headers[]` include:
- `name`
- `value`
- `secret`
- `configured`

Notes:
- Secret binding values are not returned on read. Instead, `configured=true` tells the UI that a secret exists in the unified secret store.
- Any ACP-compatible external agent may be configured here, including tools such as Claude Code, Codex, or OpenCode, as long as it speaks the expected transport.
- `stdio` external agents always start inside the active session workspace. The working directory is runtime-derived from the session's project context and is not stored in agent config.

### `PUT /system/configs/agents/{agent_id}`

Upserts one external ACP agent config.

Rules:
- Path `agent_id` must match body `agent_id`.
- Secret env/header values are persisted only through the unified secret store.
- Sending a secret binding with `configured=false` and no value removes the stored secret for that binding.

### `DELETE /system/configs/agents/{agent_id}`

Deletes one saved external ACP agent config and its stored secrets.

### `POST /system/configs/agents/{agent_id}:test`

Tests connectivity against the saved runtime-resolved external ACP agent config.

Response fields:
- `ok`
- `message`
- optional `protocol_version`
- optional `agent_name`
- optional `agent_version`

### `POST /system/configs/proxy:reload`

Reloads effective proxy env into runtime.
The reload source is the current effective merged environment, not only app-saved `.env` values.
This updates process-level proxy variables for future HTTP requests and shell/MCP subprocesses, clears removed proxy keys, and refreshes MCP runtime state.

### `POST /system/configs/mcp:reload`

Reloads MCP config into runtime.
For stdio MCP servers launched through `uvx` or `uv tool run`, the backend clears the relevant `uv` package cache before rebuilding runtime state so newly added MCP tools are visible on the next load.
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

### `PUT /system/configs/orchestration`

Replaces global orchestration settings.
The request body is the `OrchestrationSettings` object directly; the backend no longer accepts an extra top-level `config` field. Unknown top-level fields are rejected.

Rules:
- `presets[].role_ids` may contain only normal roles; reserved system roles are rejected.
- The default preset id must match one existing preset.
- `MainAgent` and `Coordinator` base role prompts are edited through `/roles/configs/*`, not this config.
- `orchestration_prompt` is appended only for `Coordinator` in `orchestration` session mode.

### `GET /system/configs/workspace/ssh-profiles`

Returns the saved SSH profile list used by workspace mounts.
Each item includes:
- `ssh_profile_id`
- `host`
- optional `username`
- optional `port`
- optional `remote_shell`
- optional `connect_timeout_seconds`
- optional `private_key_name`
- `has_password`
- `has_private_key`
- `created_at`
- `updated_at`

Passwords and private key bodies are not echoed by the API.

### `GET /system/configs/workspace/ssh-profiles/{ssh_profile_id}`

Returns one SSH profile with the same response shape as the list endpoint.

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
- `clear_marker_before` is present on the first round after a session history clear boundary. The frontend uses it to render a divider and collapse older segments by default.
- `compaction_marker_before` is present on the first round whose coordinator conversation continues after an automatic history compaction boundary. The frontend uses it to render a non-collapsing divider.
- `compaction_marker_before.label` is `History compacted (rolling summary)` when the marker metadata reports `compaction_strategy = rolling_summary`; older markers without strategy metadata may still render as `History compacted`.
- `microcompact` and `compaction_marker_before` may both be present on the same round. In that case the request first used microcompact and then also crossed a persisted full-compaction boundary.
- Automatic history compaction is logical only. Older messages are marked hidden-from-context for model reads, but remain available to raw/history endpoints.
- When legacy destructive clear behavior left a completed run with no persisted coordinator message rows, the round projection may synthesize one assistant text message from the persisted `run_completed.output`.

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
`round_snapshot` mirrors the same round projection contract as `/sessions/{session_id}/rounds/{run_id}`, including `primary_role_id`.
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

Lists one session-level agent instance per delegated role in the session. Each entry also includes a compact reflection preview for the subagent role in the current workspace, plus the latest runtime system prompt snapshot and runtime tools JSON captured before the most recent subagent execution step.

Notes:
- This endpoint continues to back the orchestration/legacy right-rail agent list.
- Normal-mode `spawn_subagent` child sessions are excluded from this projection.

Response fields include:
- `instance_id`
- `role_id`
- `status`
- `created_at`
- `updated_at`
- `reflection_summary_preview`
- `reflection_updated_at`
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
- `reflection_summary_preview`
- `reflection_updated_at`
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

### `GET /sessions/{session_id}/agents/{instance_id}/reflection`

Returns the full stored reflection summary for one subagent instance.

Response fields:
- `instance_id`
- `role_id`
- `summary`
- `preview`
- `updated_at`
- `source`

### `POST /sessions/{session_id}/agents/{instance_id}/reflection:refresh`

Triggers reflection recomputation for one subagent instance and returns the refreshed summary. Reflection memory is separate from automatic conversation compaction summaries.

### `PATCH /sessions/{session_id}/agents/{instance_id}/reflection`

Overwrites the stored reflection summary for that subagent role in the current workspace.

Request:

```json
{
  "summary": "- Prefer concise implementation notes"
}
```

### `DELETE /sessions/{session_id}/agents/{instance_id}/reflection`

Deletes the stored reflection summary for that subagent role in the current workspace. The response returns an empty reflection projection with `updated_at=null`.

### `GET /sessions/{session_id}/tasks`

Lists delegated tasks in the session.

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
  "target_role_id": "Architect",
  "thinking": {
    "enabled": false,
    "effort": null
  }
}
```

Notes:

- `input` is now the canonical run payload. It is an ordered array of typed content parts:
  - `{"kind":"text","text":"..."}`
  - `{"kind":"media_ref", ...}`
  - `{"kind":"inline_media", ...}` for small ingress-only image/audio payloads that are normalized immediately into stored `media_ref` assets
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
- `thinking` is optional.
- `thinking.enabled` enables model thinking streams for providers that emit thinking parts.
- `thinking.effort` optionally sets provider reasoning effort (`minimal`, `low`, `medium`, `high`); when set, it is forwarded to OpenAI-compatible providers as `openai_reasoning_effort`.
- `target_role_id` is optional. When set, that run starts from the specified role instead of the session-default root role, without mutating the saved session topology.
- `target_role_id` may point to `Coordinator`, `MainAgent`, or any normal role known to the role registry.
- The backend resolves the session mode at run creation time and snapshots the chosen root topology into the run intent for queued and recoverable resume flows.
- `session_id`, `target_role_id`, `run_id`, and other identifier-style request fields follow the common identifier validation rules above.

Response:

```json
{"run_id": "run-1", "session_id": "session-1", "target_role_id": "Architect"}
```

### `GET /runs/{run_id}/events`

Streams run events via SSE.

Multimodal events:
- `output_delta`: payload includes `output`, an array of typed content parts. Text streaming may still emit `text_delta`; media outputs are emitted through `output_delta`.
- `generation_progress`: payload includes `run_kind`, `phase`, `progress`, and optional `preview_asset_id` for provider-native image/audio/video generation runs.

Thinking events:
- `thinking_started`: payload includes `part_index`, `role_id`, `instance_id`.
- `thinking_delta`: payload includes `part_index`, `text`, `role_id`, `instance_id`.
- `thinking_finished`: payload includes `part_index`, `role_id`, `instance_id`.

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

Injects follow-up content to active agents in a run.

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
- System/module boundary guidance for this substrate lives in `docs/system-module-boundaries.md`.

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
      "objective": "Implement the endpoint and tests"
    }
  ]
}
```

Behavior:
- Creates delegated task contracts only.
- Role binding happens later during dispatch.

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
      "parent_task_id": "task-root"
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

### `PATCH /tasks/{task_id}`

Updates a delegated task definition.

Request:

```json
{
  "title": "Review code",
  "objective": "Review the implementation and report issues"
}
```

Rules:
- Only `created` delegated tasks can be updated.
- `role_id` cannot be updated through task APIs.
- Root coordinator tasks cannot be updated through task APIs.

There is no public manual dispatch endpoint for delegated tasks.

Delegated task dispatch is performed internally by the Coordinator through the `dispatch_task` tool.

Internal dispatch rules:
- `created`: bind the task to the provided `role_id`, create or reuse the session-level subagent instance for that role, then execute.
- `assigned` or `stopped`: reuse the bound instance and continue.
- `completed`, `failed`, or `timeout`: rejected; create a replacement task instead.
- `running`: rejected as a conflict.
- After the first dispatch, the delegated role is fixed for that task. To change roles, create a replacement task.
- If another task already holds the same role instance in `assigned`, `running`, or `stopped`, dispatch is rejected as a conflict.

## Role APIs

### `GET /roles`

Lists loaded role definitions.

### `GET /roles:options`

Returns editor options for role settings.

Response fields:
- `coordinator_role_id`
- `main_agent_role_id`
- `normal_mode_roles[]`
  - `role_id`
  - `name`
  - `description`
- `subagent_roles[]`
  - `role_id`
  - `name`
  - `description`
- `role_modes[]`: `primary | subagent | all`
- `tools`
- `mcp_servers`
- `skills[]`
  - `ref`: canonical skill ref. Uses `builtin:<name>` for built-in skills and
    `app:<name>` for user/app skills.
  - `name`
  - `description`
  - `scope`: `builtin` or `app`
- `agents[]`
  - `agent_id`
  - `name`
  - `transport`

Notes:
- Same-name builtin/app skills are both returned. Frontends must treat `ref` as
  the stable identity and use `name` only for display.
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
- `bound_agent_id`
- `mode`
- `source`
- `system_prompt`
- `file_name`
- `content`

Notes:
- `skills` in saved role documents are returned as canonical refs when they can
  be resolved uniquely. Existing unknown saved values are preserved so the UI
  can still display and edit the role.

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
  "system_prompt": "Implement the requested change."
}
```

Rules:
- Path `role_id` must match body `role_id`.
- Unknown tools, MCP servers, or skills are rejected.
- Unknown `bound_agent_id` values are rejected.
- Unrelated saved role files with stale `tools`, `mcp_servers`, or `skills` do
  not block the reload after a successful save; those references are ignored
  with warnings until they are cleaned up.
- When `source_role_id` is omitted and the file does not exist yet, a new role file is created.
- Renaming a role writes a new file and removes the previous file when validation succeeds.
- When `bound_agent_id` is set, that role executes through the external ACP provider instead of the local model provider chain.
- `mode` controls where the role can be selected: `primary` for normal-mode root roles, `subagent` for background/delegated subagent roles, `all` for both.
- Reserved system roles keep fixed identity fields (`role_id`, `name`, `description`, `version`), fixed `mode`, and fixed `system_prompt` through this API.

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

### `spawn_subagent`

Starts a fresh one-shot subagent run under a subagent-capable role.

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
  "tools": ["dispatch_task"],
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
- Skill requests accept canonical refs or unique plain names.
- Prompt-facing preview output returns plain skill names.
- Roles/settings/skills management APIs continue to use canonical refs so same-name
  builtin/app skills remain distinguishable.
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
  "tools": ["dispatch_task"],
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

### `GET /mcp/servers/{server_name}/tools`

Lists tools exposed by one MCP server. Returned tool names are the effective callable names registered at runtime in the form `<server_name>_<tool_name>` so tools from different MCP servers cannot collide.

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
- `target_config.thinking.enabled` and `target_config.thinking.effort` control per-bot run thinking settings
- Set `target_config.yolo = false` only when you want Feishu-triggered runs to keep the normal tool approval flow
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
- `schedule_mode`: `cron` or `one_shot`
- `cron_expression`
- `run_at`
- `timezone`
- `run_config`
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
- `cron_expression` for `cron`
- `run_at` for `one_shot`
- `timezone`
- `run_config`
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
- `delivery_binding` must reference an existing Feishu IM chat binding returned by `GET /automation/feishu-bindings`.
- `delivery_binding.session_id` is required for explicit create/update requests and binds the automation project to that exact saved session.
- When `delivery_binding` is present and `delivery_events` is omitted, the backend defaults to `started`, `completed`, and `failed`.
- When a bound session cannot be resolved at run time, the run fails instead of falling back to a fresh automation session.
- `workspace_id`, `automation_project_id`, and delivery-binding identifiers follow the common identifier validation rules above.

### `GET /automation/projects/{automation_project_id}`

Returns one automation project.

### `PATCH /automation/projects/{automation_project_id}`

Updates automation project definition, schedule, stored run config, and optional Feishu delivery binding.

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
