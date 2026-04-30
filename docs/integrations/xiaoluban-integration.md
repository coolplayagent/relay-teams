# Xiaoluban Integration

## Scope

Agent Teams supports Xiaoluban as an outbound automation delivery provider and as a
minimal inbound IM task entry point.

Current scope:

- one or more Xiaoluban accounts under `IM Gateway`
- personal token based account setup
- server-side secret storage through the shared secret store/keyring path
- automation `started`, `completed`, and `failed` notifications
- delivery to the account owner's derived UID, one or more configured Xiaoluban groups, or both
- workspace-scoped run completion notifications from selected workspaces
- Xiaoluban IM forwarding for the current token owner
- inbound WeLink messages forwarded through Xiaoluban's manual forwarding mode
- Xiaoluban-triggered Relay task execution with a final-result reply
- Agent-initiated text notifications through the configurable `notify` tool
- interactive session management via `/new`, `/resume`, `/help` commands
- instant acknowledgement on every received message

Current non-goals for this phase:

- Xiaoluban plugin-hosted tool execution or plugin orchestration
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
- `notification_receivers`
- `notify_self`
- `notification_receiver` (legacy compatibility field)
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
- set optional extra notification group IDs, separated by new lines, commas, or semicolons
- set the required Xiaoluban IM workspace from the account create/edit dialog
- enable or disable account
- delete account

The UI only asks for:

- `display_name`, prefilled as `小鲁班`
- personal token, which can be obtained by sending `获取发送token` to Xiaoluban in WeLink
- notification workspaces, defaulting to none selected
- optional extra notification group IDs
- IM workspace, required for inbound IM-triggered tasks

When a selected workspace sends a Xiaoluban completion notification, the token owner is always included as a recipient. Notification groups are additional recipients, not a replacement for notifying yourself.

The delivery endpoint is fixed to `http://xiaoluban.rnd.huawei.com:80/` and is not exposed for editing in the UI.

Editing an existing account shows a masked token by default. Clicking the reveal control fetches the saved token from the server secret store; hiding restores the masked value. Leaving the token field unchanged keeps the current stored token.

Creating a new account first prepares an unused `account_id` without writing to the database. The account form can therefore show the future account id and the Xiaoluban forwarding command before save. Saving the account form persists the Xiaoluban account and IM settings in one request. The forwarding command is shown as a copyable read-only field.

### Xiaoluban IM Forwarding

The IM settings are separate from notification delivery settings.

The first version exposes:

- IM workspace, required for inbound IM-triggered tasks
- read-only forwarding command, for example `http://10.88.1.23:9009/{account_id} g`

Important URL rule:

- The forwarding URL shown in the UI, returned by gateway APIs, copied by the copy button, and sent by the user to WeLink Xiaoluban must not contain a query string. Use `http://10.88.1.23:9009/{account_id} g`, never `http://10.88.1.23:9009/{account_id}?auth=... g`.
- Xiaoluban manual forwarding does not support query-string callback URLs reliably. Do not add `?auth=...` or any other query parameters to user-visible forwarding commands, even if an internal listener callback helper later grows an auth query.

Forwarding uses the dedicated Xiaoluban IM listener, not the main Relay Teams
web port. The listener binds to `0.0.0.0:9009` by default and generates a
forwarding URL with the machine's detected local IPv4 address, for example
`http://10.88.1.23:9009/{account_id}`. The user sends
`http://10.88.1.23:9009/{account_id} g` to Xiaoluban in WeLink to enter
message forwarding mode, and sends `q` to exit that mode. The default
`relay-teams server start` command remains loopback-only for the main web UI and
still shows `http://127.0.0.1:8000`.

Once in forwarding mode, the following interactive commands are available:

- `/new` -- create a new session (becomes the active session for the IM conversation)
- `/new <task>` -- create a new session and immediately submit a task
- `/resume` -- list up to 15 most recently active sessions in the workspace, with session IDs, relative timestamps, and titles
- `/resume {session_id}` -- switch to a previously created session (supports exact ID, prefix match, or list index)
- `/resume {index}` -- switch to a session by its list index (e.g. `/resume 3`)
- `/help` -- display available commands

Every non-command message receives an instant acknowledgement ("收到，正在处理中...")
before the task runs, confirming the connection is alive. The session ID shown in
the acknowledgement header matches the `internal_session_id` used by `/resume`.

The listener can be adjusted with:

- `RELAY_TEAMS_XIAOLUBAN_IM_LISTENER_HOST`
- `RELAY_TEAMS_XIAOLUBAN_IM_LISTENER_PORT`
- `RELAY_TEAMS_XIAOLUBAN_IM_PUBLIC_HOST`

### Automation Binding

Open `Automation`, then create or edit an automation project.

The `Binding and Notifications` section now uses the shared delivery provider picker instead of a Feishu-only binding list.

For Xiaoluban candidates:

- provider is `xiaoluban`
- the candidate label describes the effective targets, for example `发送给自己（uid）` or `发送给自己（uid）和 2 个群`
- binding does not require an IM session

## Delivery Semantics

Xiaoluban outbound messages are sent through the gateway service
`send_notification_message` path. That method is the single formatting and
send entry point for workspace notifications, automation delivery, and inbound
IM replies.

Provider behavior in this phase:

- `supports_bound_session_reuse = false`
- notifications fan out to the effective target list: always `derived_uid`, plus every normalized `notification_receivers` group ID
- a failed target does not block delivery to later targets; if every attempted target fails, the send call raises the first delivery error
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

### Agent-Initiated Notify Tool

Roles can be explicitly configured with the `notify` tool. Unlike `im_send`,
`notify` is not implicitly opened for IM sessions; it is a normal configurable
tool for proactive outbound notifications.

The first provider is `provider="xiaoluban"` and supports text-only messages:

- `target="owner"` is the default and sends only to the selected account's `derived_uid`
- `target="configured_groups"` sends to every configured `notification_receivers` group
- `target="owner_and_configured_groups"` sends to the token owner plus configured groups
- `target="explicit"` accepts `recipients` as candidate group IDs, then filters them through `notification_receivers`

Account selection is automatic when the current session came from Xiaoluban IM
and its gateway session has `channel_state.account_id`, or when exactly one
enabled Xiaoluban account has a configured token. If multiple usable accounts
exist, the tool asks the Agent to retry with `account` set to an account ID or a
unique display name.

Explicit group recipients are never sent directly. They are intersected with the
selected account's configured notification group whitelist. Partial matches send
to the allowed groups and report filtered values in the tool result; no matches
fail the tool call.

Approval behavior is target-based:

- owner-only notifications do not create an internal tool approval request
- any resolved group target creates a guarded tool approval request with the account and group count in the target summary

## Inbound IM Semantics

Xiaoluban calls the dedicated IM listener callback at:

```text
POST http://{detected-local-ip}:9009/{account_id}
```

The route returns immediately with:

```json
{"message":"Forwarding received"}
```

Processing continues in the background:

- the account must exist and be enabled
- the IM workspace must exist
- the stored personal token is reused for Xiaoluban replies
- `keep_alive` is attempted when a Xiaoluban `session_id` is present
- empty content sends a short usage hint through the shared Xiaoluban notification formatter

### Message Routing

Normal messages (not starting with `/`) are routed to the active session for the
IM conversation. By default, the first message in an IM conversation creates a
gateway session keyed by `xiaoluban:{account_id}:{workspace_id}:{session_id}`.
When the user switches sessions via `/new` or `/resume`, subsequent messages are
routed to the chosen session through an in-memory active-session mapping.

### Session Management

An in-memory mapping tracks which gateway session is active for each IM
conversation. The mapping is keyed by `{account_id}:{xiaoluban_session_id}`.

| Command | Behaviour |
|---------|-----------|
| `/new` | Creates a new gateway session (external key suffixed with a UUID), sets it as active for this IM conversation, and replies with the created `internal_session_id`. |
| `/new <task>` | Same as `/new` but also submits the given task text immediately in the new session. |
| `/resume` | Lists up to 15 most recently active sessions in the workspace. Each entry shows `internal_session_id`, relative timestamp, and the session title (auto-generated from the first user message, or `新会话`). Over 15 sessions, a `...(N more)` line is appended. |
| `/resume {id}` | Switches the IM conversation's active session to the matched one. Matching order: exact `internal_session_id`, prefix match, list index. If the target session has no gateway session record yet (e.g. a web-created session), one is created on the fly. |
| `/help` | Displays available commands. |

### Acknowledgement

Every non-command message that successfully starts a run receives an instant
acknowledgement (`收到，正在处理中...`). Busy sessions are rejected before the
acknowledgement, so the user does not receive a misleading "processing" message.

### Run Execution

- gateway sessions use the Xiaoluban channel and an external key of `xiaoluban:{account_id}:{workspace_id}:{session_id}` when a session id is present
- if no `session_id` is present, the key falls back to `xiaoluban:{account_id}:{sender}:{receiver}`
- sessions created by `/new` append an additional `:{uuid_suffix}` to the external key
- busy sessions are rejected with a short "try again later" message through the shared Xiaoluban notification formatter
- the task runs through the shared gateway session ingress path
- only the terminal result is sent back to Xiaoluban through the shared Xiaoluban notification formatter

## APIs

Gateway APIs:

- `GET /api/gateway/xiaoluban/accounts`
- `POST /api/gateway/xiaoluban/accounts:prepare`
- `POST /api/gateway/xiaoluban/accounts`
- `PATCH /api/gateway/xiaoluban/accounts/{account_id}`
- `PATCH /api/gateway/xiaoluban/accounts/{account_id}/im`
- `GET /api/gateway/xiaoluban/accounts/{account_id}/im:forwarding-command`
- `POST /api/gateway/xiaoluban/accounts/{account_id}:reveal-token`
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
