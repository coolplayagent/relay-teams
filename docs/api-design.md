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
- Re-dispatching the same task reuses its bound instance.
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
  - `has_write_tmp`

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
Literal profile `api_key` values are migrated out of `model.json` into the unified secret store on read.

### `GET /system/configs/model/profiles`

Returns normalized model profiles.
Each profile includes `has_api_key`, the currently stored `api_key` value so the web UI can mask it by default and reveal it on demand, `is_default` to mark the runtime fallback profile, and optional `context_window` for next-send context preview UI.
`provider` currently supports `openai_compatible`, `bigmodel`, and the internal/testing-only `echo`.
When no profile is explicitly marked default, the backend resolves the default in this order: a profile named `default`, the only configured profile, then the first profile by name.

### `PUT /system/configs/model/profiles/{name}`

Upserts a model profile.
Request body may include optional `source_name` to rename an existing profile while preserving its stored API key when `api_key` is omitted.
`provider` accepts `openai_compatible`, `bigmodel`, and `echo`.
Profiles may also include optional `ssl_verify` to override the global outbound TLS verification default for that model only.
Profiles may include `is_default` to promote that profile to the runtime default; saving one default clears the flag from all others.
Profiles may include optional `context_window` to declare the total model context limit separately from `max_tokens`, which remains the output-token cap.
When `context_window` is omitted and the backend recognizes the provider/model pair, it may auto-fill a known context limit during save and runtime load.

### `DELETE /system/configs/model/profiles/{name}`

Deletes a model profile.
If the deleted profile was the current default and other profiles remain, the backend promotes the first remaining profile by name to stay default.

### `PUT /system/configs/model`

Replaces the full model config object.
Literal profile `api_key` values are moved into the unified secret store before `model.json` is written.

### `POST /system/configs/model:probe`

Tests model connectivity for a saved profile and/or draft override.
Draft overrides may include optional `ssl_verify`; effective TLS verification resolves as `override.ssl_verify` -> global `SSL_VERIFY` -> default `false`.
If `timeout_ms` is omitted, the backend uses the resolved profile `connect_timeout_seconds` value, or `15s` when no saved profile is involved.

### `POST /system/configs/model:discover`

Fetches the available model catalog for a saved profile and/or draft override.
Draft overrides may omit `model`, but must provide `base_url` and `api_key` when `profile_name` is omitted.
When `profile_name` is provided, the request may override `base_url`, `api_key`, and `ssl_verify` while reusing the saved credentials for any omitted fields.
If `timeout_ms` is omitted, the backend uses the resolved profile `connect_timeout_seconds` value, or `15s` when no saved profile is involved.
`openai_compatible` and `bigmodel` both map this call to `GET {base_url}/models` and return the normalized `models` list sorted and deduplicated.
When the provider exposes per-model context-limit metadata in the catalog payload, the response also includes `model_entries[]` with:
- `model`
- optional `context_window`

The settings UI uses `model_entries[].context_window` to auto-fill the profile context window field after model discovery. Providers that return only model ids will still populate `models[]`, but `context_window` remains user-specified.
For a small set of known provider/model pairs, the backend also applies a built-in context-window fallback when the provider returns only model ids.

### `POST /system/configs/model:reload`

Reloads model config into runtime.

### `GET /system/configs/proxy`

Returns the saved proxy configuration assembled from app `~/.agent-teams/.env` plus the unified secret store.
Fields: `http_proxy`, `https_proxy`, `all_proxy`, `no_proxy`, `proxy_username`, `proxy_password`, `ssl_verify`.
Saved proxy URLs are returned without embedded credentials when the configured proxy URLs share the same username/password pair.
If the password was persisted through the secret store, the API rehydrates it into `proxy_password` for editing.
If a user manually forces `user:password@host` into `.env`, runtime loading still supports it and the API can read it back, but the save flow will not write that password back to `.env`.

### `PUT /system/configs/proxy`

Saves proxy values into app `~/.agent-teams/.env` and the unified secret store, then reloads runtime proxy state immediately.
Blank values remove the corresponding proxy key.
`proxy_username` and `proxy_password` are optional shared credentials.
`ssl_verify` controls the default TLS certificate verification policy for Agent Teams outbound HTTP clients.
When omitted or `null`, the backend removes `SSL_VERIFY` from `.env` and falls back to skipping certificate verification by default.
On save, proxy passwords are persisted through the unified secret store. When a usable system keyring backend exists, the secret store uses keyring; otherwise it falls back to `~/.agent-teams/secrets.json`.
The `.env` file stores proxy URLs without the password portion.
Runtime loading still supports manual `.env` proxy URLs that already contain embedded passwords.
`no_proxy` accepts both comma-separated and semicolon-separated entries. Wildcard host patterns such as `127.*`, `192.168.*`, and the special token `<local>` are supported.

### `GET /system/configs/web`

Returns the saved web tool configuration.
Fields:
- `provider`: currently only `exa`
- `api_key`: optional value rehydrated from the unified secret store

The web settings UI intentionally stays minimal. All other `websearch` and `webfetch` behavior is fixed in code, including the Exa MCP endpoint and temp file location under `~/.agent-teams/.../tmp`. `webfetch` keeps a fixed `5 MiB` limit for textual responses, while binary responses are streamed to the workspace temp directory with a fixed `512 MiB` cap. When the upstream origin proves `Range` support through a valid byte-range probe and returns a strong validator such as `ETag` or `Last-Modified`, binary downloads use segmented fetching and workspace-scoped resume state to continue later calls from the last completed offset.

### `PUT /system/configs/web`

Saves the web tool configuration.
`provider` is currently fixed to `exa`.
`api_key` is optional because Exa hosted MCP can be used without a key; providing one only raises the rate-limit ceiling.
The backend persists the API key only through the unified secret store and does not write it back to `.env`.

### `GET /system/configs/github`

Returns the saved GitHub CLI configuration.
Fields:
- `token`: optional value rehydrated from the unified secret store

The GitHub settings UI exists specifically for the bundled `gh` CLI integration used by shell subprocesses. When configured, the runtime injects the token into shell environments as both `GH_TOKEN` and `GITHUB_TOKEN`, and also disables interactive auth/update prompts for non-interactive runs.
Legacy `GH_TOKEN` / `GITHUB_TOKEN` values still found in `.env` are migrated into the secret store on read and removed from `.env`.

### `PUT /system/configs/github`

Saves the GitHub CLI configuration.
`token` is optional. The backend persists it through the unified secret store and removes any managed `GH_TOKEN` / `GITHUB_TOKEN` entries from `.env`.

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

### `POST /system/configs/skills:reload`

Reloads skills config into runtime.

### `GET /system/configs/notifications`

Returns notification rules by event type.
Each rule includes:
- `enabled`
- `channels[]`: `browser`, `toast`, `feishu`
- `feishu_format`: `text` or `card`

### `PUT /system/configs/notifications`

Replaces notification rules.
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

Rules:
- `presets[].role_ids` may contain only normal roles; reserved system roles are rejected.
- The default preset id must match one existing preset.
- `MainAgent` and `Coordinator` base role prompts are edited through `/roles/configs/*`, not this config.
- `orchestration_prompt` is appended only for `Coordinator` in `orchestration` session mode.

### `GET /system/configs/environment-variables`

Returns environment variables grouped by `system` and `app` scope.
`system` is read-only and reflects the effective runtime environment currently visible to the Agent Teams server and newly spawned child processes.
`app` is editable and is stored across `~/.agent-teams/.env` and the unified secret store.
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
Changes to `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, or `SSL_VERIFY` also trigger the same proxy runtime refresh side effects as the dedicated proxy settings API.
`system` scope is read-only and returns a user-facing validation error on mutation.

### `DELETE /system/configs/environment-variables/{scope}/{key}`

Deletes one app environment variable from the target scope.
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
  "metadata": {"project": "demo"}
}
```

Notes:
- New sessions default to `session_mode = "normal"`.
- New sessions default to `normal_root_role_id = "MainAgent"`.
- New sessions also store the current default orchestration preset id so they can be switched to orchestration before the first run.
- Omitting `session_id` or sending `session_id = null` auto-generates a session id. Sending `"None"` or `"null"` as a string is rejected with `422`.

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

Updates session metadata.

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
        "label": "History compacted"
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
- `clear_marker_before` is present on the first round after a session history clear boundary. The frontend uses it to render a divider and collapse older segments by default.
- `compaction_marker_before` is present on the first round whose coordinator conversation continues after an automatic history compaction boundary. The frontend uses it to render a non-collapsing divider.
- Automatic history compaction is logical only. Older messages are marked hidden-from-context for model reads, but remain available to raw/history endpoints.
- When legacy destructive clear behavior left a completed run with no persisted coordinator message rows, the round projection may synthesize one assistant text message from the persisted `run_completed.output`.

### `GET /sessions/{session_id}/rounds/{run_id}`

Gets one round projection.

### `GET /sessions/{session_id}/recovery`

Returns active run recovery state, pending tool approvals, paused subagent state, and round snapshot.

`active_run` also includes:
- `last_event_id`
- `checkpoint_event_id`
- `stream_connected`
- `should_show_recover`
- `primary_role_id`

For `running` or `queued` recoverable runs, the frontend uses these event ids to automatically reconnect the SSE stream without a manual "Connect Stream" action.
`round_snapshot` mirrors the same round projection contract as `/sessions/{session_id}/rounds/{run_id}`, including `primary_role_id`.

### `GET /sessions/{session_id}/agents`

Lists one session-level agent instance per delegated role in the session. Each entry also includes a compact reflection preview for the subagent role in the current workspace, plus the latest runtime system prompt snapshot and runtime tools JSON captured before the most recent subagent execution step.

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

### `GET /sessions/{session_id}/events`

Lists persisted business events in the session.

### `GET /sessions/{session_id}/messages`

Lists persisted messages in the active session segment only. Rows before the latest logical `clear` marker are excluded from this endpoint, and rows marked hidden-from-context by automatic compaction are also excluded.

### `GET /sessions/{session_id}/agents/{instance_id}/messages`

Lists the raw history timeline for one agent instance, including:
- original message rows, even when they were marked hidden-from-context by automatic compaction
- session `clear` dividers
- conversation-local `compaction` dividers

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
- `yolo: true` skips tool approval for all tools in that run, including resumed recoverable runs.
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

Frontend behavior:
- The web UI uses `llm_retry_scheduled` to render one active retry card in the round timeline and keep its countdown live while the retry backoff window is active.
- Retry countdowns are computed from the SSE event `occurred_at` timestamp plus `retry_in_ms`, so delayed delivery or page refresh does not restart the timer.
- Later retry events replace the same card instead of stacking multiple historical cards.
- Once a retried model attempt produces successful output, the retry card is removed.
- If a model emits malformed tool arguments JSON after a safe checkpoint, the backend may emit `run_resumed` with `reason="auto_recovery_invalid_tool_args_json"` and continue the same stream without surfacing `run_paused`.
- If the run still cannot continue safely after retries are exhausted, `llm_retry_exhausted` is followed by `run_paused` and the SSE stream closes for that turn.
- `run_paused` represents a recoverable interruption, not a terminal failure. Public run phase becomes `awaiting_recovery`.

### `POST /runs/{run_id}/inject`

Injects follow-up content to active agents in a run.

### `GET /runs/{run_id}/tool-approvals`

Lists pending tool approvals.

### `POST /runs/{run_id}/tool-approvals/{tool_call_id}/resolve`

Approves or denies a pending tool call.

Request:

```json
{"action": "approve", "feedback": ""}
```

### `POST /runs/{run_id}/stop`

Stops the full run or a specific subagent.

### `POST /runs/{run_id}:resume`

Resumes a recoverable run.

Behavior:
- Recoverable runs in `queued`, `paused`, or `stopped` may be resumed.
- Runs paused for `awaiting_tool_approval` or `awaiting_subagent_followup` are not resumed by this endpoint; those flows still require their dedicated resolution action.

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

### `POST /tasks/{task_id}/dispatch`

Dispatches or re-dispatches a delegated task.

Request:

```json
{"role_id": "spec_coder", "prompt": "Address pagination concerns"}
```

Rules:
- `created`: bind the task to the provided `role_id`, create or reuse the session-level subagent instance for that role, then execute.
- `assigned` or `stopped`: reuse the bound instance and continue.
- `completed`: requires non-empty `prompt`, then reuses the same instance.
- `running`: rejected as a conflict.
- `failed` or `timeout`: rejected; create a new task instead.
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
- Reserved system roles keep fixed identity fields (`role_id`, `name`, `description`, `version`) and fixed `system_prompt` through this API.

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
- `remove_worktree`: `true|false`

Rules:
- `remove_worktree=true` only affects workspaces with `file_scope.backend = "git_worktree"`.
- When `remove_worktree=true`, the backend runs `git worktree remove --force` before deleting the workspace record.
- When `remove_worktree=false`, the backend deletes only the workspace record.

## Prompt APIs

### `POST /prompts:preview`

Builds prompt preview payload for a specific role. Coordinator role IDs are resolved from the loaded role files and are not hardcoded to `coordinator_agent`.

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
- When `workspace_id` is provided, `runtime_system_prompt` resolves `Working Directory` from the workspace execution root using the same workspace path resolution as real agent execution.
- `runtime_system_prompt` also includes any resolved instruction files loaded from the workspace/project chain, user-level prompt files, and `~/.agent-teams/prompts.json`.
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

### `PATCH /gateway/feishu/accounts/{account_id}`

Updates a Feishu gateway account. If the runtime credential signature changes, the backend reloads the Feishu long-connection runtime. If the target session preset changes, the backend clears the existing external chat bindings for that account.

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

### `GET /observability/breakdowns`

Returns tool-level breakdown rows for `scope=global|session|run`. Non-global scopes require `scope_id`.

Breakdown payload includes:
- `rows`: tool-level call/failure/latency breakdown
- `role_rows`: role-level token/cache/tool-failure breakdown

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

### `POST /automation/projects/{automation_project_id}:run`

Starts the automation project immediately. Unbound projects create a fresh automation
session; projects with a Feishu delivery binding reuse the exact saved bound session.
Response fields:
- `automation_project_id`
- `session_id`

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
