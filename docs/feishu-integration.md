# Feishu Integration

## Overview

Agent Teams supports Feishu app bot integration for:

- inbound IM trigger delivery through the Feishu SDK long connection
- outbound notifications back to the originating Feishu chat

Inbound and outbound Feishu handling use Feishu's official Python SDK (`lark-oapi`).
Inbound delivery uses SDK long connection mode, so no public callback URL or reverse
proxy is required just for Feishu event delivery.

The integration model is now:

- one Feishu trigger equals one Feishu bot
- each Feishu trigger carries its own app identity and runtime preset
- multiple Feishu bots can run at the same time
- the same Feishu chat is isolated per bot, not shared globally

This version is designed for Feishu chat workflows:

- group chats and single chats are supported
- one Feishu chat under one bot maps to one internal session
- the same chat under different bots creates different sessions
- group chats require `@App Name` when `trigger_rule = "mention_only"`
- single chats accept any text message and do not require mention
- tool approvals are still resolved through the existing UI/API, not inside Feishu

## Trigger Model

Feishu credentials are no longer loaded from global `FEISHU_*` app environment
variables for trigger runtime.

Each Feishu trigger stores:

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

Secrets are stored only in the system keyring. They are not written back to the
trigger table or `.env`.

Read APIs expose `secret_status` instead of secret values:

- `app_secret_configured`
- `verification_token_configured`
- `encrypt_key_configured`

## Trigger Setup

Create a Feishu trigger with:

```json
{
  "name": "feishu_ops",
  "display_name": "Feishu Ops",
  "source_type": "im",
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

- `app_id`, `app_name`, and `app_secret` are required when creating a Feishu trigger
- `session_mode = "orchestration"` requires `orchestration_preset_id`
- `thinking.enabled = true` should include an explicit `thinking.effort`

The server opens one Feishu SDK long connection per enabled Feishu trigger whose
credentials are ready.

## Session Binding

Inbound Feishu session reuse is keyed by:

- `platform`
- `trigger_id`
- `tenant_key`
- `external_chat_id`

That means:

- the same bot returns to the same session for the same chat
- a different bot in the same chat creates or reuses a different session

If a bot's runtime preset changes, Agent Teams clears that bot's external chat
bindings. Existing sessions keep their history, but the next inbound message starts
or rebinds to a new session using the new preset.

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
