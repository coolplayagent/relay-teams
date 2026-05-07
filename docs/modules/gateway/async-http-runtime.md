# Gateway Async HTTP Runtime

All outbound provider traffic uses native async HTTP clients. Feishu, WeChat,
Xiaoluban, GitHub triggers, provider connectivity probes, provider auth helpers,
model catalog fetches, and skill installer downloads must create HTTP clients
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

The net module is async-only for HTTP. Synchronous public APIs may remain where
they are deliberate compatibility boundaries, but those methods must delegate to
async HTTP implementations instead of creating a second synchronous transport.

Xiaoluban inbound IM callback handling enters the gateway through
`handle_im_inbound_async()`. The listener may enqueue that coroutine as a
FastAPI background task, but it must not call a duplicate synchronous inbound
handler.
