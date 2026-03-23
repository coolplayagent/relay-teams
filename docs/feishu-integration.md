# Feishu Integration

## Overview

Agent Teams supports a Feishu app bot integration that covers:

- inbound IM trigger delivery through the Feishu SDK long connection
- outbound notifications back to the originating Feishu group

Inbound and outbound Feishu handling now use Feishu's official Python SDK (`lark-oapi`).

Inbound event handling uses the SDK long connection mode, so:

- no public callback URL is required
- no public IP or reverse proxy is required just for Feishu event delivery
- encrypted event delivery is supported when `FEISHU_ENCRYPT_KEY` is configured

This version is designed for group-chat workflows:

- only group messages are supported
- one Feishu group maps to one internal session
- only `@bot` text messages create runs
- tool approvals are still resolved through the existing UI/API, not inside Feishu

## Required Environment Variables

Set these in app environment variables:

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_ENCRYPT_KEY` if encrypted event delivery is enabled
- `FEISHU_VERIFICATION_TOKEN` is optional and is not required for the long-connection trigger flow

## Trigger Setup

Create a trigger with:

```json
{
  "name": "feishu_group",
  "source_type": "im",
  "source_config": {
    "provider": "feishu",
    "trigger_rule": "mention_only"
  },
  "target_config": {
    "workspace_id": "default"
  }
}
```

The server opens the Feishu SDK long connection automatically when:

- `FEISHU_APP_ID` and `FEISHU_APP_SECRET` are configured
- at least one enabled Feishu trigger exists

If multiple Feishu triggers are enabled at the same time, Agent Teams uses the first enabled trigger deterministically and logs a warning.

## Notifications

Notification rules now support:

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

Feishu notifications are only sent when the run/session already has Feishu chat context, which is established automatically when the run starts from a Feishu message.
