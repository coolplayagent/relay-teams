# Gateway Async HTTP Runtime

Gateway integrations use native async HTTP clients for all outbound provider
traffic. Feishu, WeChat, and Xiaoluban gateway code must create HTTP clients
through `relay_teams.net.create_async_http_client()` or
`relay_teams.net.create_runtime_async_http_client()`.

The gateway runtime must not keep sync and async implementations of the same
provider operation. If an operation sends provider traffic, the public Python
method for that operation is async and callers must `await` it. Synchronous
entrypoints such as listener callbacks may bridge into the async method at the
process boundary, but they must not contain a second synchronous HTTP path.

This contract applies to:

- Feishu message send, reply, reaction, asset upload, profile lookup, and
  websocket endpoint resolution.
- WeChat login, long polling, message send, typing indicator, media upload URL
  lookup, and CDN upload.
- Xiaoluban text notification, keep-alive, and utility-route requests.

Non-gateway modules may still use the shared sync HTTP factory when they have a
deliberate synchronous boundary. Gateway outbound HTTP is async-only.
