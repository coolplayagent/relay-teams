# Speeding up agentic workflows with WebSockets in the Responses API | OpenAI

Source: https://openai.com/index/speeding-up-agentic-workflows-with-websockets/
Published: 2026-04-22
Person association: sama; kevinweil; joshwoodward; alexalbert_; petergyang; thenanyu (via OpenAI organization; direct byline is Brian Yu and Ashwin Nathan)

This engineering article explains how OpenAI reduced end-to-end latency for agent loops in the Responses API by about 40% through persistent WebSocket connections and incremental state reuse.

## Key engineering points

- agent loops create dozens of back-and-forth API requests
- as inference speeds improve, API/service overhead becomes the bottleneck
- OpenAI added caching, reduced network hops, improved safety-path latency, and introduced WebSocket mode
- the server keeps connection-scoped in-memory previous response state
- `previous_response_id` lets follow-up requests reuse cached conversation state instead of rebuilding everything
- post-inference and validation work can be overlapped more effectively

## Why it matters for multi-agent engineering

The piece is highly relevant because modern multi-agent and tool-using systems depend on fast iterative loops. This article addresses:

- transport/protocol design for agent loops
- state reuse across multi-turn workflows
- tool-call round-trip efficiency
- infrastructure needed to make agentic workflows practical at scale

## Note

This archive preserves the core content of a substantial 2026 OpenAI engineering post.