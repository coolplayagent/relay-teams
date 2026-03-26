# WeChat Gateway Integration

## Overview

Agent Teams supports WeChat as a conversational `gateway` channel.

This differs from Feishu:

- WeChat inbound chat goes through `gateway -> session/run`
- Feishu inbound chat currently still uses the `triggers` backend path
- the settings UI groups both under the Gateway section, but the backend ownership is different

The current WeChat scope is:

- QR-code login from the frontend
- multiple WeChat accounts
- one runtime preset per account
- direct chat only
- text in, text out
- typing indicator on reply lifecycle

Not included yet:

- group chat routing
- media upload / download handling
- richer mention parsing

## Runtime Model

Each connected WeChat account stores:

- transport settings: `base_url`, `cdn_base_url`, `route_tag`
- runtime binding: `workspace_id`, `session_mode`, `normal_root_role_id`, `orchestration_preset_id`
- execution behavior: `yolo`, `thinking`
- sync state: `sync_cursor`

The bot token is stored in keyring, not in SQLite.

Enabled accounts run a background long-poll worker. For each accepted message:

1. the worker polls `getupdates`
2. the service extracts direct-message text content
3. `GatewaySessionService.resolve_or_create_session(...)` maps `wechat:{account_id}:{peer_user_id}` to one internal session
4. the backend creates or reuses a run for that session
5. terminal run output is sent back through WeChat `sendmessage`

## Login Flow

Frontend and backend use this flow:

1. `POST /api/gateway/wechat/login/start`
2. render the returned QR code
3. `POST /api/gateway/wechat/login/wait`
4. on success, persist the account and token
5. let the user edit account runtime settings after login

If the same WeChat account logs in again, the backend preserves the existing runtime configuration and only refreshes login metadata and token state.

## Session Mapping

WeChat session reuse is keyed by:

- `channel_type = "wechat"`
- `external_session_id = "wechat:{account_id}:{peer_user_id}"`

That means:

- one WeChat peer maps to one internal session per connected account
- the same peer talking to two different connected accounts creates two isolated sessions
- reconnecting the same account preserves the same routing key shape

## Operations

CLI management lives under:

- `agent-teams gateway wechat list`
- `agent-teams gateway wechat connect`
- `agent-teams gateway wechat wait`
- `agent-teams gateway wechat update`
- `agent-teams gateway wechat enable`
- `agent-teams gateway wechat disable`
- `agent-teams gateway wechat delete`
- `agent-teams gateway wechat reload`

Frontend management lives under Settings -> Gateway.
