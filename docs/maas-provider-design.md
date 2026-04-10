# MAAS Provider 对接设计说明

## 1. 背景

当前仓库已经支持 `openai_compatible`、`bigmodel`、`minimax` 和内部测试用 `echo` provider。
MAAS 对接不是引入新的推理协议，而是在保留 OpenAI-compatible `/chat/completions` 主链路的前提下，补充 MAAS 专用的登录、token 注入、模型发现、配置持久化和前端设置页行为。
本文档描述的是当前已经落地的实现设计。

## 2. 目标

- 将 `maas` 作为正式 provider 暴露给后端运行时和前端设置页。
- 在实际推理前按需调用固定 `secureLogin` 接口获取 token。
- 在推理请求中自动注入 `X-Auth-Token` 和固定 `app-id`。
- 继续复用现有 OpenAI-compatible `/chat/completions` 执行链路。
- 确保 MAAS 密码不写入 `model.json`。
- 已保存的 MAAS profile 在前端再次编辑或测试时，不要求用户重新输入密码。
- 支持 MAAS 连通性测试和 MAAS 可用模型发现。

## 3. 非目标

- 不抽象成通用 OAuth / SSO / 任意登录框架。
- 不支持在前端自定义 MAAS 登录 URL。
- 不支持在前端自定义 MAAS 推理 base URL。
- 不支持在前端自定义 MAAS 模型发现 endpoint 或 fixed payload 字段。
- 不支持在前端自定义 `app-id`。
- 不支持 external ACP agent 绑定 MAAS profile。

## 4. 固定约束

- 登录 URL 固定：`http://rnd-idea-api.huawei.com/ideaclientservice/login/v4/secureLogin`
- 推理 base URL 固定：`http://snapengine.cida.cce.prod-szv-g.dragon.tools.huawei.com/api/v2/`
- 模型发现 URL 固定：`https://promptcenter.aims.cce.prod.dragon.tools.huawei.com/PromptCenterService/v1/policy/bundle`
- 推理请求头固定注入：`app-id: RelayTeams`

这些值由后端强制控制，前端只能展示结果，不能修改。

## 5. 配置模型

### 5.1 Provider 类型

`ProviderType` 已包含 `maas`。该值已贯通 provider registry、runtime config、system config API 和前端 provider 下拉。

### 5.2 MAAS 认证结构

MAAS 使用 `MaaSAuthConfig`，包含 `username` 和 `password` 两个字段。
其中 `username` 写入 `model.json`，`password` 只写入 unified secret store。读取接口默认只返回 `username` 和 `has_password`，不回显已保存密码。

### 5.3 持久化规则

当 `provider = "maas"` 时：
- `base_url` 在保存和运行时加载时都会被归一化为固定 MAAS base URL
- `api_key` 不再生效
- `maas_auth.password` 不写入 `model.json`
- 若 profile 之前是其他 provider 且存在 `api_key` secret，切换到 MAAS 时会清理旧 `api_key` secret

## 6. 后端实现分层

### 6.1 配置与校验层

主要模块：
- `src/relay_teams/providers/model_config.py`
- `src/relay_teams/providers/model_config_manager.py`
- `src/relay_teams/sessions/runs/runtime_config.py`
- `src/relay_teams/interfaces/server/routers/system.py`

职责：
- 定义 `ProviderType.MAAS` 和 `MaaSAuthConfig`
- 强制 MAAS 使用固定 base URL
- 保存时将密码写入 secret store
- 运行时从 secret store 恢复密码
- 对前端返回 `username` 和 `has_password`

### 6.2 MAAS 鉴权层

主要模块：`src/relay_teams/providers/maas_auth.py`。

职责：
- 发起固定 `secureLogin`
- 提取 `cloudDragonTokens.authToken`
- 从 `userInfo.hwDepartName` 或部门层级字段中提取 `department`
- 在内存中缓存 MAAS auth context
- 支持临近过期刷新，在 `401/403` 时强制刷新一次
- 构造 MAAS 推理请求使用的鉴权对象

### 6.3 OpenAI-compatible 复用层

主要模块：
- `src/relay_teams/providers/openai_support.py`
- `src/relay_teams/providers/openai_compatible.py`

职责：
- 保持 MAAS 推理继续走 OpenAI-compatible `/chat/completions`
- 将 Bearer API key 认证替换为 MAAS request auth
- 过滤 `authorization`、`x-auth-token`、`app-id` 等 MAAS 保留头

### 6.4 MAAS 模型发现层

主要模块：`src/relay_teams/providers/model_connectivity.py`。

职责：
- 为 `POST /api/system/configs/model:discover` 增加 `provider = "maas"` 分支
- 复用 MAAS 登录拿到 token 和 department
- 调用固定 PromptCenter 模型发现接口
- 合并顶层和嵌套配置中的模型 id
- 过滤非法模型 id，去重、排序并返回标准化结果

## 7. 请求链路

### 7.1 主推理链路

1. 读取 profile，得到 `model`、固定 `base_url` 和 `maas_auth`。
2. 调用 `MaaSTokenService.get_token_sync()` 或异步版本。
3. 若本地没有有效 token，则先发起登录。
4. 登录成功后在内存中缓存 auth context。
5. 调用 `POST {base_url}/chat/completions`。
6. 注入请求头：`X-Auth-Token` 和 `app-id: RelayTeams`。
7. 若响应是 `401/403`，则强制刷新 token 后重试一次。

### 7.2 连通性测试链路

前端在模型配置页点击“测试”时，MAAS 使用 probe 路径：
1. 前端构造 `override`。
2. 如果是编辑已有 MAAS profile 且用户没有重新输入密码，前端只会发送 `username`。
3. 后端 merge 逻辑会把 override 中的 `username` 与已保存 profile 中的 `password` 合并。
4. 后端先进行 MAAS 登录。
5. 然后请求 `/chat/completions`。
6. 返回标准化的 `ModelConnectivityProbeResult`。

### 7.3 模型发现链路

前端在模型配置页点击“获取模型列表”时，MAAS 使用 discovery 路径：
1. 前端发送 `provider`、固定 `base_url` 和 `maas_auth`。
2. 如果是编辑已有 MAAS profile 且用户没有重新输入密码，前端只会发送 `username` 和 `profile_name`。
3. 后端 merge 逻辑会复用已保存密码。
4. 后端执行 MAAS 登录，获取 token 和 department。
5. 后端调用固定 PromptCenter `policy/bundle` endpoint，并通过 `X-Auth-Token` 鉴权。
6. 后端解析并标准化返回的模型目录。
7. 后端返回标准 `ModelDiscoveryResult`。

### 7.4 event-stream 包装响应兼容

部分 MAAS probe 响应不是普通 JSON body，而是如下形式：

```text
data: {"id":"cmpl-test","usage":{"total_tokens":3}}

data: [DONE]
```

为了兼容该行为，`model_connectivity.py` 的 probe 解析采用 fallback 策略：先尝试 `response.json()`；如果失败，再按 `data:` event-stream chunk 进行解析；忽略 `[DONE]`；从最后一个可解析的 `data:` chunk 中提取 JSON。

## 8. 前端设置页行为

主要模块：
- `frontend/dist/js/components/settings/index.js`
- `frontend/dist/js/components/settings/modelProfiles.js`

当 provider 切换为 `maas` 时：
- 隐藏 API Key 输入区
- 显示 MAAS 用户名和密码字段
- 自动填充固定 base URL
- 将 base URL 输入框设为禁用态
- 保持模型发现按钮可用，并走 MAAS 专用 discovery 链路

当从 `maas` 切换到其他 provider 时，前端会清空固定 base URL，恢复普通 provider 的交互流程。

## 9. 模型发现策略

MAAS 模型发现不复用 OpenAI-compatible `GET /models`。
后端在登录后调用固定 PromptCenter endpoint，并从以下位置提取模型：
- 顶层 `user_model_list[*].model_id`
- 解析后的 `plugin_config[*].config[].composor_act_mode_model_list[*].model_id`
- 解析后的 `plugin_config[*].config[].composor_plan_mode_model_list[*].model_id`
- 解析后的 `plugin_config[*].config[].user_model_list[*].model_id`

过滤规则：
- 只保留非空字符串
- 过滤纯数字 id
- 过滤包含 `:` 的 id
- 对剩余 id 去重并排序

## 10. 安全设计

- MAAS 密码不写入 `model.json`，只写入 unified secret store。
- auth token 和 department 只在内存中缓存，不写回持久化配置。
- 保留头会被过滤，避免用户自定义请求头和系统鉴权冲突。

## 11. 已知限制

- 登录 URL 固定，不支持多环境切换。
- 推理 base URL 固定，不支持多集群切换。
- 模型发现 endpoint 和请求字段固定，不支持前端自定义。
- `app-id` 固定为 `RelayTeams`。
- probe 的 event-stream 解析是最小兼容实现，不是通用 SSE 框架。
- external ACP agent 路径仍不支持 MAAS profile。

## 12. 测试覆盖

当前 MAAS 相关覆盖包括：
- provider registry 能识别 `maas`
- model profile 保存和读取时密码进入 secret store
- runtime config 能从 secret store 恢复 MAAS 密码
- probe 能完成 MAAS 登录和 `/chat/completions`
- 编辑已有 profile 时能复用已保存密码
- probe 能兼容 `data: {...}` 包装响应
- discovery 能完成 MAAS 登录、构造 PromptCenter 请求、在 `401/403` 后重试一次，并提取标准化模型 id 列表
- 前端设置页在 `maas` 下展示固定 base URL、禁用编辑并允许模型发现
- 从 `maas` 切回其他 provider 时会清空固定 base URL

## 13. 相关文件

核心实现文件：
- `src/relay_teams/providers/model_config.py`
- `src/relay_teams/providers/model_config_manager.py`
- `src/relay_teams/providers/maas_auth.py`
- `src/relay_teams/providers/openai_support.py`
- `src/relay_teams/providers/model_connectivity.py`
- `src/relay_teams/sessions/runs/runtime_config.py`
- `src/relay_teams/interfaces/server/routers/system.py`
- `frontend/dist/js/components/settings/index.js`
- `frontend/dist/js/components/settings/modelProfiles.js`

主要测试文件：
- `tests/unit_tests/providers/test_model_config_manager.py`
- `tests/unit_tests/providers/test_maas_auth.py`
- `tests/unit_tests/providers/test_model_connectivity.py`
- `tests/unit_tests/providers/test_provider_registry.py`
- `tests/unit_tests/sessions/runs/test_runtime_config.py`
- `tests/unit_tests/interfaces/server/test_system_router.py`
- `tests/unit_tests/frontend/test_model_profiles_ui.py`
- `tests/unit_tests/frontend/test_settings_shell_ui.py`
