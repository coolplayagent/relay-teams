# CodeAgent Provider Design

## Overview

CodeAgent is a first-class model provider with `provider = "codeagent"`.
Its inference endpoint, OAuth client parameters, and password-login endpoint are fixed by the backend.
The user does not configure a separate CodeAgent login URL.

CodeAgent profile auth always lives under `codeagent_auth`.
There are two supported auth modes:

- `sso`: CodeAgent OAuth / refresh-token flow
- `password`: username/password login that reuses the MaaS secure-login endpoint and payload contract

## Fixed Endpoints And OAuth Constants

The backend enforces these constants:

| Name | Value |
| --- | --- |
| `DEFAULT_CODEAGENT_BASE_URL` | `https://codeagentcli.rnd.huawei.com/codeAgentPro` |
| `DEFAULT_CODEAGENT_SSO_BASE_URL` | `https://ssoproxysvr.cd-cloud-ssoproxysvr.szv.dragon.tools.huawei.com/ssoproxysvr` |
| `DEFAULT_CODEAGENT_CLIENT_ID` | `com.huawei.devmind.codebot.apibot` |
| `DEFAULT_CODEAGENT_SCOPE` | `1000:1002` |
| `DEFAULT_CODEAGENT_SCOPE_RESOURCE` | `devuc` |

`ModelEndpointConfig` always rewrites CodeAgent `base_url` to `DEFAULT_CODEAGENT_BASE_URL`.
`CodeAgentAuthConfig` also enforces the fixed OAuth client values even if callers submit different ones.

For password auth, the backend reuses the MaaS secure-login endpoint and request/response shape:

- `POST http://rnd-idea-api.huawei.com/ideaclientservice/login/v4/secureLogin`
- request headers include `app-id: RelayTeams`

The returned token is then used as the CodeAgent `X-Auth-Token`.

## Auth Modes

### SSO Mode

`codeagent_auth.auth_method = "sso"` uses the existing OAuth flow:

- `POST /api/system/configs/model/codeagent/oauth:start`
- `GET /api/system/configs/model/codeagent/oauth/{auth_session_id}`

The frontend starts OAuth, opens the returned authorization URL, and polls for completion.
Completed OAuth sessions yield CodeAgent `access_token` and `refresh_token`.

At runtime:

- the current `access_token` is used first when present
- `401` or `403` triggers one refresh attempt through `refresh_token`
- refreshed tokens are persisted back to the secret store

### Password Mode

`codeagent_auth.auth_method = "password"` requires:

- `username`
- `password`

The login exchange does not use OAuth sessions or refresh tokens.
Instead, the CodeAgent token service logs in with the MaaS-compatible secure-login API and caches the returned token for a short TTL.

At runtime:

- if no cached token is available, the provider logs in with the saved username/password
- if a CodeAgent request returns `401` or `403`, the provider logs in again and retries once
- password-mode tokens are not persisted as refreshable CodeAgent credentials

## Persisted Profile Shape

Saved CodeAgent profiles keep auth state in `codeagent_auth`.
The backend never stores raw CodeAgent password credentials or OAuth tokens in `model.json`.

### Stored SSO Profile

```json
{
  "provider": "codeagent",
  "model": "codeagent-chat",
  "base_url": "https://codeagentcli.rnd.huawei.com/codeAgentPro",
  "codeagent_auth": {
    "auth_method": "sso",
    "has_access_token": true,
    "has_refresh_token": true
  }
}
```

### Stored Password Profile

```json
{
  "provider": "codeagent",
  "model": "codeagent-chat",
  "base_url": "https://codeagentcli.rnd.huawei.com/codeAgentPro",
  "codeagent_auth": {
    "auth_method": "password",
    "username": "relay-user",
    "has_password": true
  }
}
```

Persistence rules:

- SSO `access_token` and `refresh_token` are stored in the unified secret store.
- Password-mode `password` is stored in the unified secret store.
- Password-mode `username` stays in the profile JSON.
- Editing a saved password profile with an empty password field preserves the stored password secret.
- Switching from SSO to password removes saved SSO tokens.
- Switching from password to SSO removes the saved password secret.

## Runtime Config Resolution

Runtime profile loading resolves `codeagent_auth` differently by mode:

- `sso`: requires a saved `refresh_token` or an in-progress `oauth_session_id`
- `password`: requires `username` plus a password from the secret store or inline override

This keeps `codeagent_auth` as the single CodeAgent auth contract.
CodeAgent does not reuse top-level `maas_auth`.

## Model Discovery And Chat Requests

CodeAgent model discovery and chat requests use the same provider auth resolver as save-time verification and runtime execution.

### Model Discovery

- `GET {DEFAULT_CODEAGENT_BASE_URL}/chat/modles?checkUserPermission=TRUE`

Required request headers:

| Header | Value |
| --- | --- |
| `X-Auth-Token` | resolved CodeAgent token |
| `app-id` | `CodeAgent2.0` |
| `User-Agent` | `AgentKernel/1.0` |
| `gray` | `false` |
| `oc-heartbeat` | `1` |
| `X-snap-traceid` | generated UUID |
| `X-session-id` | generated `ses_...` id |

The discovery parser accepts a bare JSON list or objects with `data` / `models`.
Model ids are normalized from `name`, `id`, or `model` and deduplicated.

### Chat

- `POST {DEFAULT_CODEAGENT_BASE_URL}/chat/completions`

Required request headers:

| Header | Value |
| --- | --- |
| `X-Auth-Token` | resolved CodeAgent token |
| `app-id` | `CodeAgent2.0` |
| `Content-Type` | `application/json` |
| `Accept` | `text/event-stream` |
| `User-Agent` | `AgentKernel/1.0` |
| `gray` | `false` |
| `oc-heartbeat` | `1` |
| `X-snap-traceid` | generated UUID |
| `X-session-id` | generated `ses_...` id |

The provider strips any preexisting OpenAI `Authorization`, `X-Auth-Token`, and CodeAgent-specific headers before injecting the resolved CodeAgent headers.

## Frontend Behavior

When the user selects `codeagent` in Settings:

- the UI hides API-key auth
- the UI shows an auth-method selector
- `sso` shows the existing SSO button and status
- `password` shows username and password inputs

The draft flow is:

1. select `CodeAgent`
2. choose `SSO` or `Username and Password`
3. either complete SSO or enter username/password
4. fetch the model list using the resolved CodeAgent token

For saved password profiles, the password field remains masked by default and the UI preserves the stored secret unless the user enters a new password.

## Auth Verification API

The settings page uses:

- `POST /api/system/configs/model/codeagent/auth:verify`

The request body only includes the saved profile name.
The backend validates the saved CodeAgent auth state for either mode:

- `status = "valid"`: the profile can still obtain or use a CodeAgent token
- `status = "reauth_required"`: SSO refresh failed or password re-login failed with an auth-invalid result
- `status = "error"`: transport or upstream failure prevented verification

This endpoint distinguishes “saved credentials exist” from “the credentials were verified successfully right now”.

## Validation Notes

- CodeAgent profiles always require `codeagent_auth`.
- `sso` mode requires a saved refresh path or OAuth session id.
- `password` mode requires `username` and `password` for new drafts.
- The backend always forces the default CodeAgent inference base URL.
- Password login is a CodeAgent-only auth mode even though it reuses the MaaS login endpoint.
