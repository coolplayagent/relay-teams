# CodeAgent Provider 设计

CodeAgent 已作为一等模型提供商注册，`provider` 值为 `"codeagent"`。
它沿用常规模型配置中的模型名和采样参数，并通过 `codeagent_auth`
保存 SSO 登录后得到的认证状态。CodeAgent 的 API 端点和 OAuth 应用参数
均由代码写死，不对用户暴露配置入口。

## 常量

后端在 `relay_teams.providers.model_config` 中维护以下常量：

| 名称 | 值 |
| --- | --- |
| `DEFAULT_CODEAGENT_BASE_URL` | `https://codeagentcli.rnd.huawei.com/codeAgentPro` |
| `DEFAULT_CODEAGENT_SSO_BASE_URL` | `https://ssoproxysvr.cd-cloud-ssoproxysvr.szv.dragon.tools.huawei.com/ssoproxysvr` |
| `DEFAULT_CODEAGENT_CLIENT_ID` | `com.huawei.devmind.codebot.apibot` |
| `DEFAULT_CODEAGENT_SCOPE` | `1000:1002` |
| `DEFAULT_CODEAGENT_SCOPE_RESOURCE` | `devuc` |

`ModelEndpointConfig` 会强制 CodeAgent 配置使用
`DEFAULT_CODEAGENT_BASE_URL`。`CodeAgentAuthConfig` 也会强制覆盖
`client_id`、`scope` 和 `scope_resource`，即使调用方提交了其他值也不会生效。

## OAuth 流程

CodeAgent 使用 issue #461 描述的 `client_code` OAuth 轮询流程。前端通过以下
接口启动和查询登录：

- `POST /api/system/configs/model/codeagent/oauth:start`
- `GET /api/system/configs/model/codeagent/oauth/{auth_session_id}`

本地回调路由已删除。SSO 完成后，前端只轮询
`GET /api/system/configs/model/codeagent/oauth/{auth_session_id}`，后端在该接口中向
CodeAgent `getToken` 接口查询 token 状态。

启动接口不接收用户提供的 provider 配置。后端会生成 32 位 `client_code`，
并构造：

- 回调地址：
  `https://codeagentcli.rnd.huawei.com/codeAgentPro/codeAgent/oauth/callback?client_code={client_code}`
- 授权地址：
  `https://ssoproxysvr.cd-cloud-ssoproxysvr.szv.dragon.tools.huawei.com/ssoproxysvr/oauth2/authorize`

授权地址查询参数如下：

| 参数 | 值 |
| --- | --- |
| `client_id` | `com.huawei.devmind.codebot.apibot` |
| `redirect_uri` | 上面的回调地址 |
| `scope` | `1000:1002` |
| `response_type` | `code` |
| `scope_resource` | `devuc` |

前端打开授权地址后，会轮询登录状态接口。每次状态查询会由后端向 CodeAgent
轮询 token：

- `POST https://codeagentcli.rnd.huawei.com/codeAgentPro/codeAgent/oauth/getToken`
- 请求头：`Content-Type: application/json`
- 请求体：

```json
{
  "clientCode": "{client_code}",
  "redirectUrl": "https://codeagentcli.rnd.huawei.com/codeAgentPro/codeAgent/oauth/callback?client_code={client_code}"
}
```

如果 CodeAgent 尚未返回 `access_token`，会话保持 pending。CodeAgent 返回
`access_token` 和 `refresh_token` 后，内存中的 OAuth 会话会被标记为完成。
前端最长轮询 30 分钟。

## 配置存储

保存后的 profile JSON 不包含明文 token，只保存能力标记和 OAuth 状态信息：

```json
{
  "provider": "codeagent",
  "model": "codeagent-chat",
  "base_url": "https://codeagentcli.rnd.huawei.com/codeAgentPro",
  "codeagent_auth": {
    "has_access_token": true,
    "has_refresh_token": true
  }
}
```

保存 CodeAgent profile 时，服务端会消费已完成的 OAuth 会话，并把 token 写入
secret store。运行时配置会从 secret store 解析 token，再注入到
`codeagent_auth` 中用于构建 provider client。

## Token 使用

CodeAgent API 使用 `X-Auth-Token` 认证。

SSO 完成后的首个请求会直接使用 OAuth 返回的 `access_token`。如果运行时配置中
同时存在 `access_token` 和 `refresh_token`，token service 会先用该
`access_token` 初始化缓存，不会立刻调用 refresh 接口。

当请求返回 `401` 或 `403` 时，provider 会调用 refresh 接口刷新一次 token：

- `POST https://codeagentcli.rnd.huawei.com/codeAgentPro/codeAgent/oauth/refreshToken`

随后使用刷新后的 access token 重试原请求。

## API 传输

CodeAgent chat 请求保留 OpenAI-compatible 的请求体和流式语义，但实际 URL 会被
重写为：

- `POST https://codeagentcli.rnd.huawei.com/codeAgentPro/chat/completions`

chat 请求必需请求头：

| Header | 值 |
| --- | --- |
| `X-Auth-Token` | 当前 OAuth access token |
| `app-id` | `CodeAgent2.0` |
| `Content-Type` | `application/json` |
| `Accept` | `text/event-stream` |
| `User-Agent` | `AgentKernel/1.0` |
| `gray` | `false` |
| `oc-heartbeat` | `1` |
| `X-snap-traceid` | 生成的 UUID |
| `X-session-id` | `ses_` 加生成 UUID 去连字符后的前 20 位 |

请求认证层会先移除 OpenAI SDK 请求中已有的 `Authorization`、`X-Auth-Token`
以及 CodeAgent 专用请求头，再注入上表中的值。

模型发现使用：

- `GET https://codeagentcli.rnd.huawei.com/codeAgentPro/chat/modles?checkUserPermission=TRUE`

模型发现必需请求头：

| Header | 值 |
| --- | --- |
| `X-Auth-Token` | 当前 OAuth access token |
| `app-id` | `CodeAgent2.0` |
| `User-Agent` | `AgentKernel/1.0` |
| `gray` | `false` |
| `oc-heartbeat` | `1` |
| `X-snap-traceid` | 生成的 UUID |
| `X-session-id` | `ses_` 加生成 UUID 去连字符后的前 20 位 |

模型发现解析器支持 JSON 数组，也支持带 `data` 或 `models` 字段的包装对象。
模型 ID 会从 `name`、`id` 或 `model` 字段读取，并进行去重。

## 前端行为

当用户选择 `codeagent` provider 时，设置页会隐藏 API Key 和 Base URL 输入框，
改为显示 CodeAgent SSO 控件。前端向 OAuth 启动接口发送空请求体，所有
CodeAgent OAuth 和 API 参数均由后端硬编码值决定。

前端在准备 profile payload 时也会使用硬编码的 CodeAgent base URL，用户输入的
端点值不会覆盖后端常量。

## 验证

当前已做的聚焦检查：

- `node --check frontend\dist\js\components\settings\modelProfiles.js`
- `.venv\Scripts\ruff.exe check` 检查 CodeAgent provider 和相关测试
- CodeAgent 单元测试覆盖 OAuth、token 缓存初始化、模型发现和 chat probe 行为
- 使用 DevTools 验证选择 `codeagent` 后会打开 SSO URL，且 URL 中包含硬编码
  client ID、scope、scope resource 和带 `client_code` 的回调地址

外部 CodeAgent 网络连通性依赖运行环境，因此浏览器验证只要求前端流程和本地 API
请求/响应结构正确。
