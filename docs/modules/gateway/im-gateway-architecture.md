# IM Gateway Architecture

## Purpose

The IM Gateway layer connects external chat systems to internal Relay Teams sessions.
It owns transport-specific account configuration, message acceptance, durable inbound
queues, and final-result replies. Core session and run execution stay in
`sessions/*` and `sessions/runs/*`.

## Module Boundaries

| Module | Owns | Does Not Own |
| --- | --- | --- |
| `relay_teams.gateway.gateway_session_service` | Mapping external channel conversations to internal `SessionRecord` ids, active run binding, channel state | Provider auth, HTTP clients, run creation |
| `relay_teams.gateway.session_ingress_service` | Shared busy-session policy and detached run start handoff | Provider message parsing, provider replies |
| `relay_teams.gateway.feishu` | Feishu gateway accounts, SDK long connection, Feishu message pool, Feishu replies | WeChat/Discord/Xiaoluban account semantics |
| `relay_teams.gateway.wechat` | WeChat accounts, login, long-poll worker, inbound queue, WeChat replies | Feishu/Discord/Xiaoluban account semantics |
| `relay_teams.gateway.discord` | Discord bot accounts, Discord Gateway worker, REST sends, inbound queue, Discord replies | Discord application provisioning outside Relay Teams |
| `relay_teams.gateway.xiaoluban` | Xiaoluban notification delivery and Xiaoluban IM forwarding | General Gateway session mapping |
| `relay_teams.gateway.im` | IM tool context resolution, `im_send` delivery, common lightweight session commands | Provider account persistence, provider workers |
| `interfaces/server/routers/gateway.py` | HTTP shape for `/api/gateway/*` | Domain decisions or repository access outside services |
| `interfaces/server/container.py` | Dependency wiring | Provider-specific business logic |

Interface layers must communicate through service APIs. Frontend, CLI, and SDK use
HTTP/SSE only and do not access repositories directly.

## Shared Inbound Flow

1. A provider worker normalizes source-native input into a typed inbound message.
2. The provider service validates account status and provider-specific acceptance
   rules.
3. `GatewaySessionService` resolves or creates a stable internal session using a
   provider-scoped external session key.
4. Lightweight session commands are handled before run creation.
5. Accepted normal messages are persisted to the provider inbound queue.
6. `GatewaySessionIngressService` starts a detached run only when the internal
   session is idle; otherwise the provider queue waits.
7. Terminal run output is sent through the provider reply client.
8. Queue records are marked completed or failed, then the next queued item drains.

This flow prevents IM messages from being injected into an already running session
without an explicit runtime boundary.

## Provider Session Keys

| Provider | External Session Key |
| --- | --- |
| Feishu | `feishu:{account_id}:{tenant_key}:{chat_id}` through `external_session_bindings` |
| WeChat | `wechat:{account_id}:{peer_user_id}` |
| Discord DM | `discord:{account_id}:dm:{author_id}` |
| Discord guild channel | `discord:{account_id}:guild:{guild_id}:channel:{channel_id}` |
| Discord thread | `discord:{account_id}:guild:{guild_id}:channel:{channel_id}:thread:{thread_id}` |
| Xiaoluban IM | `xiaoluban:{account_id}:{workspace_id}:{session_id}` or sender/receiver fallback |

The internal `session_id` is the execution source of truth. Gateway keys only provide
stable external lookup and channel reply metadata.

## Persistence And Secrets

Provider account repositories own their tables and read validation. Persisted dirty
rows should be filtered with structured warnings instead of failing startup or API
list calls.

Secrets are never stored in account tables:

- Feishu: `app_secret`, `verification_token`, `encrypt_key`
- WeChat: bot token
- Discord: bot token
- Xiaoluban: personal token

Secret stores expose provider-specific methods so account services do not share a
central unrelated config object.

## Extending A Provider

A new IM provider should add:

1. Explicit Pydantic models for account records, create/update inputs, inbound
   messages, and queue records.
2. A provider account repository and optional inbound queue repository.
3. A provider secret store wrapper.
4. A provider client/worker boundary that normalizes source input and sends replies.
5. A provider service that uses `GatewaySessionService` and
   `GatewaySessionIngressService`.
6. Optional `gateway.im` context support if `im_send` should work from provider
   sessions.
7. `/api/gateway/{provider}/*` routes plus frontend facade exports.
8. Focused unit coverage for account persistence, acceptance rules, queueing, reply
   behavior, and frontend configuration.

Do not add paired sync/async repository or service methods for the same operation.
Async workers, queues, network calls, and run handoff should use async APIs through
the full stack.
