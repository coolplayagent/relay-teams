# Unlocking the Codex harness: how we built the App Server

- Source: OpenAI
- Original URL: https://openai.com/index/unlocking-the-codex-harness/
- Access date: 2026-04-19
- Published: 2026-02-04
- Type: Web article

## Summary / key value

Detailed implementation write-up for the **Codex harness** and the App Server that exposes it. Valuable for anyone studying agent harness architecture, client/server protocol design, thread/turn/item abstractions, approval flow handling, and multi-surface integration for coding agents.

## Why it is relevant to AI Harness Engineering

- Explicitly explains what is inside the Codex harness.
- Documents JSON-RPC protocol choices for exposing an agent loop to clients.
- Covers thread lifecycle, persistence, tool execution, config/auth, and approval pauses.
- High-value reference for AI developer workflow infrastructure.

## Important excerpts

> Under the hood, they’re all powered by the same Codex harness—the agent loop and logic that underlies all Codex experiences.

> Thread lifecycle and persistence ... Config and auth ... Tool execution and extensions.

> Designing an API for an agent loop is tricky because the user/agent interaction is not a simple request/response.

## Archived content

OpenAI explains that the App Server is the stable, client-friendly layer around the Codex harness. The article introduces three core conversation primitives:

- **Item**: atomic unit of input/output with started/delta/completed lifecycle
- **Turn**: one unit of agent work initiated by user input
- **Thread**: durable session container across turns

It also documents:

- bidirectional JSON-RPC over stdio/JSONL
- approval requests that pause execution until client response
- integration patterns for local IDE clients, Codex web runtime, and CLI/TUI
- tradeoffs between App Server, MCP, Codex Exec, and SDK integration methods

This is a primary source for the control-plane side of agent harness engineering.
