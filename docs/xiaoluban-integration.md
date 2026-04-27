# Xiaoluban Integration

## Scope

Agent Teams supports Xiaoluban as an outbound automation delivery provider and as a
minimal inbound IM task entry point.

Current scope:

- one or more Xiaoluban accounts under `IM Gateway`
- personal token based account setup
- server-side secret storage through the shared secret store/keyring path
- automation `started`, `completed`, and `failed` notifications
- delivery to the account owner's derived UID or a configured Xiaoluban receiver/group ID
- workspace-scoped run completion notifications from selected workspaces
- Xiaoluban IM forwarding for the current token owner
- inbound WeLink messages forwarded through Xiaoluban's manual forwarding mode
- Xiaoluban-triggered Relay task execution with a final-result reply

Current non-goals for this phase:

- Xiaoluban tool execution or plugin orchestration
- group, department, or collaborator whitelist editing
- intermediate progress pushes
- Xiaoluban plugin script publishing

## Account Model

Each Xiaoluban account stores:

- `account_id`
- `display_name`
- `base_url`
- `status`
- `derived_uid`
- `notification_workspace_ids`
- `notification_receiver`
- `im_config.workspace_id`
- `created_at`
- `updated_at`
- `secret_status.token_configured`

The personal token itself is not stored in the account record. It is persisted through the server secret store.

## Token Rules

The Xiaoluban token entered in the UI must be a personal token.

Validation rules:

- token must not be empty when creating an account
- plugin-style tokens with the `p_` prefix are rejected
- token format must match `<uid_prefix>_<32-char suffix>`
- the derived UID is computed from the token prefix before the first underscore

Example:

- token: `uidself_1234567890abcdef1234567890abcdef`
- derived UID: `uidself`

## UI Flow

### IM Gateway

Open `IM Gateway` from the left-side feature navigation.

The Xiaoluban section supports:

- create account
- edit account display name / token
- choose workspaces that should send run completion notifications
- set an optional notification recipient/group ID
- set the required Xiaoluban IM workspace from the account create/edit dialog
- enable or disable account
- delete account

The UI only asks for:

- `display_name`, prefilled as `小鲁班`
- personal token, which can be obtained by sending `获取发送token` to Xiaoluban in WeLink
- notification workspaces, defaulting to none selected
- optional notification recipient/group ID
- IM workspace, required for inbound IM-triggered tasks

The delivery endpoint is fixed to `http://xiaoluban.rnd.huawei.com:80/` and is not exposed for editing in the UI.

Editing an existing account allows leaving the token field empty to keep the current stored token.
Saving the account form persists the Xiaoluban account and IM settings in one request. The UI shows the Xiaoluban forwarding command the user must send manually in WeLink.

### Xiaoluban IM Forwarding

The IM settings are separate from notification delivery settings.

The first version exposes:

- IM workspace, required for inbound IM-triggered tasks
- read-only forwarding command, for example `http://10.88.1.23:9009/{account_id}?auth=... g`

Forwarding uses the dedicated Xiaoluban IM listener, not the main Relay Teams
web port. The listener binds to `0.0.0.0:9009` by default and generates a
forwarding URL with the machine's detected local IPv4 address and a per-account
auth token, for example `http://10.88.1.23:9009/{account_id}?auth=...`. The user sends
`http://10.88.1.23:9009/{account_id}?auth=... g` to Xiaoluban in WeLink to enter
message forwarding mode, and sends `q` to exit that mode. The default
`relay-teams server start` command remains loopback-only for the main web UI and
still shows `http://127.0.0.1:8000`.

The listener can be adjusted with:

- `RELAY_TEAMS_XIAOLUBAN_IM_LISTENER_HOST`
- `RELAY_TEAMS_XIAOLUBAN_IM_LISTENER_PORT`
- `RELAY_TEAMS_XIAOLUBAN_IM_PUBLIC_HOST`

### Automation Binding

Open `Automation`, then create or edit an automation project.

The `Binding and Notifications` section now uses the shared delivery provider picker instead of a Feishu-only binding list.

For Xiaoluban candidates:

- provider is `xiaoluban`
- the candidate label is `发送给自己（uid）` or `发送给 {receiver}` when a recipient/group is configured
- binding does not require an IM session

## Delivery Semantics

Xiaoluban outbound messages are sent through the gateway service
`send_notification_message` path. That method is the single formatting and
send entry point for workspace notifications, automation delivery, and inbound
IM replies.

Provider behavior in this phase:

- `supports_bound_session_reuse = false`
- notifications are sent to the account's `notification_receiver` when configured, otherwise to `derived_uid`
- automation delivery bindings do not require or reuse an IM session
- Xiaoluban notification bodies are prefixed through the shared Xiaoluban formatter:

```text
【relay-teams】
<session_id>
────────────────────
<message body>
```

Event behavior:

- `started`: sends `定时任务 {project_name} 开始执行。`
- `completed`: always sends one completion message; if terminal output exists it is included, otherwise a default completion summary is sent
- `failed`: sends a failure summary plus terminal error or fallback error text

Workspace completion behavior:

- `notification_workspace_ids` is empty by default, so regular workspace run completion notifications are disabled until the user selects one or more workspaces
- when a normal run completes, the Xiaoluban notification dispatcher checks the session's `workspace_id`
- matching enabled accounts with usable credentials receive the run completion body
- automation-owned terminal notifications are suppressed from the workspace dispatcher to avoid duplicate Xiaoluban messages
- IM-triggered run terminal notifications are also suppressed from the workspace dispatcher because the IM path sends the final reply itself

## Inbound IM Semantics

Xiaoluban calls the dedicated IM listener callback at:

```text
POST http://{detected-local-ip}:9009/{account_id}?auth=...
```

The route returns immediately with:

```json
{"message":"Forwarding received"}
```

Processing continues in the background:

- the account must exist and be enabled
- the request must include the account-specific auth token in the callback URL
- the IM workspace must exist
- the stored personal token is reused for Xiaoluban replies
- `keep_alive` is attempted when a Xiaoluban `session_id` is present
- empty content sends a short usage hint through the shared Xiaoluban notification formatter
- gateway sessions use the Xiaoluban channel and an external key of `xiaoluban:{account_id}:{session_id}` when a session id is present
- if no `session_id` is present, the key falls back to `xiaoluban:{account_id}:{sender}:{receiver}`
- busy sessions are rejected with a short "try again later" message through the shared Xiaoluban notification formatter
- the task runs through the shared gateway session ingress path
- only the terminal result is sent back to Xiaoluban through the shared Xiaoluban notification formatter

## APIs

Gateway APIs:

- `GET /api/gateway/xiaoluban/accounts`
- `POST /api/gateway/xiaoluban/accounts`
- `PATCH /api/gateway/xiaoluban/accounts/{account_id}`
- `PATCH /api/gateway/xiaoluban/accounts/{account_id}/im`
- `GET /api/gateway/xiaoluban/accounts/{account_id}/im:forwarding-command`
- `POST /api/gateway/xiaoluban/accounts/{account_id}:enable`
- `POST /api/gateway/xiaoluban/accounts/{account_id}:disable`
- `DELETE /api/gateway/xiaoluban/accounts/{account_id}`

Automation APIs:

- `GET /api/automation/delivery-bindings`
- `delivery_binding` now accepts the provider-discriminated union for Feishu and Xiaoluban

## Notes

- existing Feishu automation bindings remain valid
- old Feishu binding APIs remain available as compatibility aliases for one phase
- runtime loading tolerates persisted drift for missing capabilities, but explicit user mutations still validate strictly
