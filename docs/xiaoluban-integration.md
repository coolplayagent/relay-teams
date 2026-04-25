# Xiaoluban Integration

## Scope

Agent Teams now supports Xiaoluban as an outbound automation delivery provider.

Current scope:

- one or more Xiaoluban accounts under `IM Gateway`
- personal token based account setup
- server-side secret storage through the shared secret store/keyring path
- automation `started`, `completed`, and `failed` notifications
- delivery to the account owner's derived UID or a configured Xiaoluban receiver/group ID
- workspace-scoped run completion notifications from selected workspaces

Current non-goals for this phase:

- inbound Xiaoluban conversations
- Xiaoluban session creation or bound-session reuse
- Xiaoluban-triggered task execution
- Xiaoluban tool execution or plugin orchestration

## Account Model

Each Xiaoluban account stores:

- `account_id`
- `display_name`
- `base_url`
- `status`
- `derived_uid`
- `notification_workspace_ids`
- `notification_receiver`
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
- enable or disable account
- delete account

The UI only asks for:

- `display_name`, prefilled as `小鲁班`
- personal token, which can be obtained by sending `获取发送token` to Xiaoluban in WeLink
- notification workspaces, defaulting to none selected
- optional notification recipient/group ID

The delivery endpoint is fixed to `http://xiaoluban.rnd.huawei.com:80/` and is not exposed for editing in the UI.

Editing an existing account allows leaving the token field empty to keep the current stored token.

### Automation Binding

Open `Automation`, then create or edit an automation project.

The `Binding and Notifications` section now uses the shared delivery provider picker instead of a Feishu-only binding list.

For Xiaoluban candidates:

- provider is `xiaoluban`
- the candidate label is `发送给自己（uid）` or `发送给 {receiver}` when a recipient/group is configured
- binding does not require an IM session

## Delivery Semantics

Xiaoluban automation delivery is handled by the shared automation delivery dispatcher.

Provider behavior in this phase:

- `supports_bound_session_reuse = false`
- notifications are sent to the account's `notification_receiver` when configured, otherwise to `derived_uid`
- no inbound session is created or reused
- Xiaoluban notification bodies are prefixed through the shared Xiaoluban formatter:

```text
【relay-teams】
session: <session_id>
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

## APIs

Gateway APIs:

- `GET /api/gateway/xiaoluban/accounts`
- `POST /api/gateway/xiaoluban/accounts`
- `PATCH /api/gateway/xiaoluban/accounts/{account_id}`
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
