# Discord Gateway Integration

Relay Teams supports Discord as an IM Gateway provider for bot direct messages,
mentions, and configured guild channels.

## Scope

Supported:

- multiple Discord bot accounts
- direct-message inbound tasks
- guild messages that mention the bot
- optional non-mention messages from allowlisted guild channels
- deterministic per-channel session reuse
- durable inbound queueing before run start
- terminal run replies through Discord REST
- `im_send` replies from Discord-originated sessions

Not supported yet:

- Discord slash-command registration
- Discord interaction callbacks
- attachment ingestion from inbound Discord messages
- server-side bot invite creation

## Discord Bot Requirements

Create a Discord application and bot in the Discord developer portal, then enable:

- Message Content intent
- Direct Messages intent
- Guild Messages intent

The bot needs permission to read the target channels and send messages. If replies
should happen in threads, it also needs the matching thread send permissions.

## Account Configuration

Each Discord account stores:

- `account_id`: Discord bot user id, derived from the bot token
- `display_name`
- `application_id`
- `status`: `enabled` or `disabled`
- `allowed_channel_ids`
- `allow_channel_messages`
- `workspace_id`
- `session_mode`
- `normal_root_role_id`
- `orchestration_preset_id`
- `yolo`
- `thinking`

The bot token is stored in the unified secret store under the account id. It is not
stored in SQLite or returned by list APIs.

## Inbound Acceptance

The Discord service ignores messages from bots and messages sent by the configured
bot user itself.

Accepted text:

- DM text from a human user
- guild text that mentions the bot, with Discord mention tokens stripped before run
  creation
- guild text from `allowed_channel_ids` when `allow_channel_messages = true`

Mention-only guild messages are ignored after mention stripping because they do not
contain a task.

## Session And Queue Flow

Discord inbound messages use the shared IM Gateway architecture:

1. `DiscordGatewayWorker` receives Discord Gateway events through `discord.py`.
2. The worker converts each Discord message into `DiscordInboundMessage`.
3. `DiscordGatewayService` applies account and message acceptance rules.
4. `GatewaySessionService` resolves the external Discord conversation to one
   internal session.
5. `discord_inbound_queue` persists the accepted message before execution.
6. `GatewaySessionIngressService` starts a detached run when the internal session is
   idle.
7. The service sends a receipt to the triggering Discord message.
8. Terminal run output is sent back to the same channel or thread.

Busy sessions do not receive implicit prompt injection. Later Discord messages stay
queued until the active run reaches a terminal state.

## HTTP APIs

Gateway APIs:

- `GET /api/gateway/discord/accounts`
- `POST /api/gateway/discord/accounts`
- `PATCH /api/gateway/discord/accounts/{account_id}`
- `POST /api/gateway/discord/accounts/{account_id}:enable`
- `POST /api/gateway/discord/accounts/{account_id}:disable`
- `DELETE /api/gateway/discord/accounts/{account_id}`
- `POST /api/gateway/discord/reload`

Create request example:

```json
{
  "display_name": "Discord Ops",
  "bot_token": "...",
  "application_id": "123456789012345678",
  "allowed_channel_ids": ["234567890123456789"],
  "allow_channel_messages": true,
  "workspace_id": "default",
  "session_mode": "normal",
  "normal_root_role_id": "MainAgent",
  "yolo": true,
  "thinking": {
    "enabled": false,
    "effort": null
  },
  "enabled": true
}
```

Responses expose `secret_status.bot_token_configured` instead of the token.

## Frontend

The Connectors feature view includes a Discord connector card with:

- account list and runtime status
- create/edit form
- bot token field with masked keep-current behavior
- application id field
- allowed channel list
- non-mention channel-message toggle
- workspace, session mode, role/preset, YOLO, and thinking controls
- enable, disable, and delete actions

Frontend calls go through `frontend/dist/js/core/api/gateway.js` and are re-exported
from the API facade.

## Security And Operations

- Rotate the Discord bot token from Discord first, then update the Relay Teams account.
- Deleting an enabled Discord account requires force semantics through the backend
  route; the UI sends force on delete after user confirmation.
- Runtime worker errors are surfaced on account list responses as in-memory
  `last_error` fields.
- If Message Content intent is disabled, the bot can connect but inbound message
  content may be blank and no run will start.
