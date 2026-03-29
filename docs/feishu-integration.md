# Feishu Integration

## Overview

Agent Teams supports Feishu app bot integration for:

- inbound IM trigger delivery through the Feishu SDK long connection
- outbound notifications back to the originating Feishu chat

The settings UI now groups Feishu and WeChat under a shared Gateway section, and the
backend ownership is also unified under `agent_teams.gateway`.

Inbound and outbound Feishu handling use Feishu's official Python SDK (`lark-oapi`).
Inbound delivery uses SDK long connection mode, so no public callback URL or reverse
proxy is required just for Feishu event delivery.

The integration model is now:

- one Feishu gateway account equals one Feishu bot
- each Feishu gateway account carries its own app identity and runtime preset
- multiple Feishu bots can run at the same time
- the same Feishu chat is isolated per bot, not shared globally

This version is designed for Feishu chat workflows:

- group chats and single chats are supported
- one Feishu chat under one bot maps to one internal session
- the same chat under different bots creates different sessions
- group chats require `@App Name` when `trigger_rule = "mention_only"`
- single chats accept any text message and do not require mention
- tool approvals are still resolved through the existing UI/API, not inside Feishu
- session commands include `help`, `status`, `clear`, and `resume`
- when a run enters `awaiting_recovery`, Feishu sends a pause hint and the user can reply with `resume`

## Account Model

Feishu credentials are no longer loaded from global `FEISHU_*` app environment
variables for gateway runtime.

Each Feishu gateway account stores:

- `source_config`
  - `provider = "feishu"`
  - `trigger_rule`
  - `app_id`
  - `app_name`
- `target_config`
  - `workspace_id`
  - `session_mode`
  - `normal_root_role_id`
  - `orchestration_preset_id`
  - `yolo`
  - `thinking.enabled`
  - `thinking.effort`
- `secret_config` on create/update only
  - `app_secret`
  - `verification_token`
  - `encrypt_key`

Secrets are stored in the unified Agent Teams secret store. When a usable system
keyring backend exists, that store uses keyring; otherwise it falls back to
`~/.agent-teams/secrets.json`. They are not written back to the gateway account
table or `.env`.

Read APIs expose both the current `secret_config` payload and `secret_status`.
The settings UI uses that to render `App Secret` as masked by default and reveal
it on demand.

- `secret_config.app_secret`
- `secret_status.app_secret_configured`
- `secret_status.verification_token_configured`
- `secret_status.encrypt_key_configured`

## Account Setup

Create a Feishu gateway account with:

```json
{
  "name": "feishu_ops",
  "display_name": "Feishu Ops",
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
  },
  "enabled": true
}
```

Rules:

- `app_id`, `app_name`, and `app_secret` are required when creating a Feishu gateway account
- `session_mode = "orchestration"` requires `orchestration_preset_id`
- `thinking.enabled = true` should include an explicit `thinking.effort`

The server opens one Feishu SDK long connection per enabled Feishu gateway account whose
credentials are ready.

## Session Binding

Inbound Feishu session reuse is keyed by:

- `platform`
- `trigger_id`
- `tenant_key`
- `external_chat_id`

For Feishu bindings, `trigger_id` now carries the gateway `account_id`.

That means:

- the same bot returns to the same session for the same chat
- a different bot in the same chat creates or reuses a different session

If a bot's runtime preset changes, Agent Teams clears that bot's external chat
bindings. Existing sessions keep their history, but the next inbound message starts
or rebinds to a new session using the new preset.

## Inbound Message Pool

Inbound Feishu text messages are now processed through a durable message pool.

Behavior:

- the SDK callback no longer creates runs directly
- each accepted message is first written to the local `feishu_message_pool`
- for group chats, the runtime resolves the sender display name before execution when possible
- the actual run input for group chats is wrapped as `???? {sender_name} ??????{message}`
  and falls back to `sender_open_id` when the name cannot be resolved
- deduplication still uses the Feishu `message_id`, falling back to `event_id`
- duplicate deliveries do not send a second acknowledgement
- same-chat messages are processed in order
- accepted group and p2p messages use a Feishu message reaction acknowledgement
  - default reaction emoji: `OK`
- only queued messages emit a separate text reply
  - queue reply: `已进入队列，前面还有 N 条消息。`
- group command replies and group final run replies use Feishu reply-to-message on the triggering message
- p2p queued replies and final run replies also use Feishu reply-to-message on the triggering message
- final Feishu replies for inbound chat messages are sent by the message-pool worker
  after the run reaches a terminal state
- waiting messages reconcile against `run_runtime`; stalled rows are retried instead
  of remaining stuck in `waiting_result`

Automation projects that bind to an existing Feishu chat follow a different outbound
rule from inbound chat messages:

- bound scheduled/manual automation runs persist and reuse the exact selected
  internal session instead of creating a fresh automation-only session
- if that session is busy, the automation run is queued behind the current session
  work and the bound chat receives `定时任务 {display_name} 准备执行，当前任务前面有 n 个消息`
- if that saved session later disappears or becomes unusable, the automation run
  fails and does not fall back to a new `MainAgent` automation session
- for these automation-bound runs, receipts, terminal result messages, and `im_send`
  tool output all use direct send to the chat, not reply-to-message, even in group chats
- when a bound run enters recoverable `awaiting_recovery`, the bound-session queue
  persists auto-resume retry state and retries `resume` with exponential backoff
  (`10s`, `20s`, `40s`, `80s`, `160s`) before sending a final failure
- Feishu provider `message_id` values are persisted for automation queue receipts and
  started/terminal messages so superseded non-terminal messages can be deleted later
- queue receipts are best-effort deleted after the queued run actually starts or after
  a final queue-owned failure replaces them
- started automation messages are best-effort deleted after a terminal completed/failed
  message is successfully sent
- cleanup failures are logged and retried, but they do not roll back the primary send
  or change the run's terminal state

This separates three concerns:

- `feishu_gateway_accounts`: bot identity and runtime targeting
- `feishu_message_pool`: inbound message lifecycle and retry state
- `run_runtime` / run events: actual run execution state

For inbound Feishu chat messages, automatic `run_completed` / `run_failed`
notifications to Feishu are suppressed so the user receives only the message-pool
final reply, not a duplicate terminal notification.

For prompt assembly, Feishu group runs also persist a conversation context marker.
Only when `source_provider = "feishu"` and `feishu_chat_type = "group"`, the runtime
and provider system prompts append this extra instruction:

- `??????????????????????????????????????????????????`

Non-Feishu-group runs keep the original system prompt unchanged.

## Session Commands

Feishu chat sessions also support lightweight chat commands:

- `help`: shows the command list
- `status`: shows the active session summary and the current chat queue state
- `clear`: clears the active session context and cancels queued messages for that chat

For group chats, command responses also use reply-to-message instead of a plain send.

`clear` still does not delete persisted `messages` or `token_usage`. It inserts the
session history divider as before, and also marks the current chat's active
`feishu_message_pool` items as cancelled so they no longer execute or emit final
chat replies.

## Notifications

Notification rules support:

- `channels`: `browser`, `toast`, `feishu`
- `feishu_format`: `text` or `card`

Example:

```json
{
  "run_completed": {
    "enabled": true,
    "channels": ["toast", "feishu"],
    "feishu_format": "card"
  }
}
```

Feishu notifications are only sent when the run/session already has Feishu chat
context. Outbound delivery is bot-aware: the dispatcher uses the `feishu_trigger_id`
stored on the session metadata to resolve the correct Feishu bot credentials.

Related diagrams:

- [IM Message Flow](./im-message-flow.md)
