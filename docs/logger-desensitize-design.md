# Logger Desensitize 方案说明

## 1. 目标

当前 `logger` 模块的脱敏方案只覆盖文件日志落盘路径，目标是：

- 防止 `backend.log`、`debug.log`、`frontend.log` 落入明文敏感信息。
- 尽量保留排障信息，只对高风险字段和值模式做统一替换。
- 不改变现有 logger 的对外使用方式，调用方继续使用 `log_event()`、`logger.error()`、`logger.exception()` 即可。

## 2. 实现入口

核心实现位于 `src/agent_teams/logger/logger.py`。

### 2.1 落盘前统一脱敏

文件日志最终通过 `HumanReadableFormatter.format()` 输出。在这里会统一处理：

- `message`：通过 `_sanitize_log_message()` 做字符串级脱敏。
- `payload`：通过 `_render_payload()` 做结构化递归脱敏后再序列化。
- `error_detail`：同样通过 `_render_payload()` 脱敏。

也就是说，只要日志最终写入文件，就一定会经过这套 redactor。

### 2.2 `log_event()` 只负责传递原始 payload

当前 `log_event()` 不提前脱敏，只把原始 `payload` 放进 `LogRecord.extra`：

```python
logger.log(
    level,
    message,
    extra={
        "event": event,
        "payload": payload or {},
        "duration_ms": duration_ms,
    },
    exc_info=exc_info,
)
```

真正的脱敏发生在 formatter 阶段，因此文件日志只会脱敏一次。

## 3. 脱敏实现方式

### 3.1 结构化递归脱敏

`payload` 和 `error_detail` 的结构化脱敏由 `_sanitize_json_value()` 完成。

处理规则：

- `dict`：递归处理每个键值对。
- `list`：递归处理每个元素。
- `tuple`：递归处理每个元素，最终按 JSON 数组样式输出。
- `None / bool / int / float`：原样保留。
- 其他值：转成字符串后做字符串级脱敏。

因此，多级 `map`、嵌套数组、元组都可以正常脱敏。

### 3.2 键名归一化

敏感键判断使用 `_normalize_redaction_key()`。归一化规则如下：

- 去掉首尾空格
- 转成小写
- `-` 替换成 `_`

例如：

- `API_KEY` -> `api_key`
- `api-key` -> `api_key`
- ` Api-Key ` -> `api_key`

### 3.3 键名命中时整值替换

当结构化字段的 key 命中敏感键集合时，整个 value 直接替换为占位符。

示例：

输入：

```json
{
  "api_key": "k-123",
  "client_secret": "s-456",
  "safe": "ok"
}
```

输出：

```json
{
  "api_key": "***",
  "client_secret": "***",
  "safe": "ok"
}
```

### 3.4 字符串级脱敏

字符串脱敏由 `_redact_string()` 完成。它用于：

- 普通日志 `message`
- `payload` 中未命中敏感 key 的字符串值
- `error_detail` 中的异常 message / stack 文本

当前默认模式包括：

1. `Bearer <token>`
2. `Basic <token>`
3. URL 中的 `user:pass@host`
4. URL query 中命中敏感键的参数值
5. `sk-...` 形态 token
6. 自定义 regex 规则

注意：当前默认规则已经移除了“普通文本中裸 `token=...` / `api_key=...` 片段”的独立匹配；如果这类内容不在 URL 中，则不会被默认规则单独替换。

### 3.5 URL 脱敏

URL 脱敏由 `_redact_url()` 完成。

它会：

- 保留 scheme、host、path、fragment
- 将 `user:pass@` 替换为 `***@`
- 仅对 query 中命中敏感键的 value 做替换

示例：

输入：

```text
https://user:pass@example.test/path?token=abc&x=1
```

输出：

```text
https://***@example.test/path?token=***&x=1
```

## 4. 默认敏感键与默认行为

### 4.1 默认敏感键

当前内置敏感键集合为：

- `password`
- `passwd`
- `secret`
- `api_key`
- `apikey`
- `token`
- `access_token`
- `refresh_token`
- `authorization`
- `client_secret`
- `proxy_password`


## 5. 配置方式

所有脱敏配置均通过环境变量生效，运行时由 `configure_logging()` 调用 `_refresh_redaction_settings()` 读取。

### 5.1 占位符配置

- `AGENT_TEAMS_LOG_REDACTION_PLACEHOLDER`

默认值：

```text
***
```

示例：

```text
AGENT_TEAMS_LOG_REDACTION_PLACEHOLDER=[REDACTED]
```

### 5.2 敏感键追加

- `AGENT_TEAMS_LOG_REDACTION_KEYS_ADD`

格式要求：JSON 字符串数组。

示例：

```text
AGENT_TEAMS_LOG_REDACTION_KEYS_ADD=["webhook_signature", "private_key"]
```

效果：在内置敏感键基础上追加。

### 5.3 敏感键整体替换

- `AGENT_TEAMS_LOG_REDACTION_KEYS_REPLACE`

格式要求：JSON 字符串数组。

示例：

```text
AGENT_TEAMS_LOG_REDACTION_KEYS_REPLACE=["webhook_signature"]
```

效果：完全替换内置敏感键集合。

### 5.4 自定义正则追加

- `AGENT_TEAMS_LOG_REDACTION_PATTERNS_ADD`

格式要求：JSON 字符串数组，每个元素是一条 regex。

示例：

```text
AGENT_TEAMS_LOG_REDACTION_PATTERNS_ADD=["CUST-[A-Z0-9]{6,}"]
```

效果：在默认字符串模式基础上额外增加匹配规则。

### 5.5 自定义正则整体替换

- `AGENT_TEAMS_LOG_REDACTION_PATTERNS_REPLACE`

格式要求：JSON 字符串数组。

示例：

```text
AGENT_TEAMS_LOG_REDACTION_PATTERNS_REPLACE=["CUST-[A-Z0-9]{6,}"]
```

效果：完全替换默认字符串模式集合。

## 6. 配置错误时的行为

配置容错策略如下：

- 非法 JSON：回退到默认规则
- 数组中出现非字符串元素：回退到默认规则
- 自定义 regex 编译失败：回退到默认规则
- 记录 warning，但不会阻止 logger 初始化

因此，错误配置不会导致 `configure_logging()` 失败。

## 7. 示例

### 7.1 普通 message

输入：

```text
request failed with Bearer abcdef while calling https://user:pass@example.test?a=1&token=xyz
```

输出：

```text
request failed with Bearer *** while calling https://***@example.test?a=1&token=***
```

### 7.2 多级 payload

输入：

```json
{
  "provider": {
    "auth": {
      "api_key": "nested-key",
      "client_secret": "nested-secret"
    },
    "endpoints": [
      {
        "url": "https://user:pass@example.test/path?token=query-token"
      }
    ]
  }
}
```

输出：

```json
{
  "provider": {
    "auth": {
      "api_key": "***",
      "client_secret": "***"
    },
    "endpoints": [
      {
        "url": "https://***@example.test/path?token=***"
      }
    ]
  }
}
```
