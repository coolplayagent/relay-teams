# Zed IDE ACP Usage

## 1. Scope

This document explains how to configure and use the local `agent-teams gateway acp stdio` entrypoint as an ACP agent inside Zed IDE.

The target scenario is local development and verification. This is not the ACP Registry publishing path.

## 2. Current Recommendation

As of March 17, 2026, Zed officially supports ACP-based external agents through two paths:

- ACP Registry for published agents
- `agent_servers` in `settings.json` for local custom agents

For this repository, use the second path. `agent-teams` is not published to ACP Registry yet.

## 3. Prerequisites

Before configuring Zed, make sure the ACP gateway can start from a terminal.

### 3.1 Install dependencies

Windows:

```powershell
.\setup.bat
uv sync --extra dev
```

Linux/macOS:

```bash
sh setup.sh
uv sync --extra dev
```

### 3.2 Configure runtime files

At minimum, complete the normal Agent Teams runtime setup first:

- `~/.config/agent-teams/model.json`
- optionally `~/.config/agent-teams/.env`
- optionally `~/.config/agent-teams/prompts.json`
- optionally one global instruction file:
  - `~/.config/agent-teams/AGENTS.md`
  - otherwise `~/.claude/CLAUDE.md`
  - otherwise `~/.gemini/GEMINI.md`

`prompts.json` uses this shape:

```json
{
  "instructions": [
    "docs/prompts/*.md",
    "~/shared/team-prompt.md",
    "https://example.com/prompt.txt"
  ]
}
```

Prompt instruction loading order is:

- project/workspace chain: `AGENTS.md`, otherwise `CLAUDE.md`, otherwise `GEMINI.md`
- one global file using the same fallback order
- extra `prompts.json` instruction sources

### 3.3 Verify the gateway command first

Windows example:

```powershell
uv --directory D:/openworkspace/agent_teams run agent-teams gateway acp stdio
```

If the process starts and stays attached to the terminal, the ACP stdio gateway is up. Stop it with `Ctrl+C` after the check.

Do not debug Zed first if this command fails. Fix the local startup issue first.

### 3.4 No special Zed environment variables are required

The gateway auto-detects the transport format from stdin:

- standard ACP clients can use `Content-Length` framing
- Zed local agent servers can use line-delimited JSON

You do not need a dedicated `ZED_ENVIRONMENT` flag or other Zed-only runtime toggle.

## 4. Configure a custom ACP agent in Zed

Open Zed user settings JSON and add an entry under `agent_servers`.

Typical user settings locations:

- Linux: `~/.config/zed/settings.json`
- macOS: `~/Library/Application Support/Zed/settings.json`
- Windows: `%APPDATA%\\Zed\\settings.json`

### 4.1 Windows example

```json
{
  "agent_servers": {
    "agent-teams": {
      "command": "uv",
      "args": [
        "--directory",
        "D:/openworkspace/agent_teams",
        "run",
        "agent-teams",
        "gateway",
        "acp",
        "stdio"
      ],
      "env": {
        "AGENT_LOG_LEVEL": "info"
      }
    }
  }
}
```

### 4.2 Linux/macOS example

```json
{
  "agent_servers": {
    "agent-teams": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/agent_teams",
        "run",
        "agent-teams",
        "gateway",
        "acp",
        "stdio"
      ],
      "env": {
        "AGENT_LOG_LEVEL": "info"
      }
    }
  }
}
```

Notes:

- `command` uses `uv`, and Zed launches the ACP agent as a stdio subprocess.
- `--directory` pins the command to this repository so `uv run` does not depend on the currently opened Zed project directory.
- `env` is optional and mainly useful for debugging.
- the stdio gateway already suppresses stdout console logging internally, so normal logs do not corrupt ACP responses

## 5. Use the agent in Zed

After the configuration is saved:

1. Restart Zed, or reload the window.
2. If Zed asks whether the repository is trusted, trust the workspace first.
3. Open the Agent Panel.
4. Start a new agent thread.
5. Select `agent-teams` from the agent list.
6. Send a simple prompt such as `Summarize the current repository layout.`

If the setup is correct, Zed starts the local `agent-teams gateway acp stdio` subprocess and communicates with it over ACP.

## 6. Configure MCP servers in Zed

If you want Zed to provide MCP tools to `agent-teams` over ACP, configure those servers in Zed itself under `context_servers`.

Example:

```json
{
  "context_servers": {
    "demo-mcp": {
      "command": "uvx",
      "args": ["some-mcp-server"]
    }
  }
}
```

Once the MCP server is active in Zed, `agent-teams` can receive it from ACP session setup, typically through `session/new` or `session/load`. Depending on how Zed provides that server, it may arrive as an ACP transport server or as a host-provided `stdio` server definition. No extra `agent-teams` bridge process or MCP config file is required for that Zed-provided server.

For Context7 specifically, prefer a custom context server entry such as:

```json
{
  "context_servers": {
    "mcp-server-context7": {
      "command": "npx",
      "args": ["-y", "@upstash/context7-mcp"]
    }
  }
}
```

## 7. What to expect in Zed

During a normal prompt turn, Zed should display:

- streamed assistant output as it arrives
- intermediate progress updates before the final answer text
- tool call progress updates
- raw tool input when the tool call includes arguments, including shell-style string arguments
- MCP tools provided by Zed as normal tool calls when the model decides to use them

Zed renders your own user message itself, so the gateway does not send a second user echo in Zed mode.

Formatting of streamed assistant text is preserved. Multi-line answers, indentation, and blank lines should render correctly in a new thread after upgrading.

## 8. Verifying MCP-over-ACP in Zed

The shortest manual verification flow is:

1. Add or enable one MCP server in Zed under `context_servers`.
2. Restart Zed and confirm the MCP server is active in the Agent Panel settings view.
3. Open a new `agent-teams` thread.
4. Send a prompt that explicitly asks for that MCP server by name and requests an action only that server can perform.

Good validation prompts look like:

- `Use the demo-mcp server and list its available tools before answering.`
- `Call the <tool_name> tool from demo-mcp and show me the result.`

When MCP-over-ACP is working, you should see:

- the MCP tool name appear in the thread
- the raw tool input rendered in Zed
- the tool result streamed back into the reply

For example, a Zed-provided Context7 server should surface runtime tool names such as `mcp-server-context7_resolve-library-id` and `mcp-server-context7_query-docs` inside an `agent-teams` thread.

If the model does not pick the MCP tool reliably, create a dedicated Zed agent profile with conflicting built-in tools disabled and the target context server enabled.

## 9. Debugging

### 9.1 Open ACP logs

Zed provides ACP debug logs.

Run this from the Command Palette:

```text
dev: open acp logs
```

This is the most direct place to inspect ACP requests, responses, and startup errors.

### 9.2 Recommended troubleshooting order

If `agent-teams` does not show up in Zed, check in this order:

1. Confirm `uv --directory <repo> run agent-teams gateway acp stdio` works in a terminal.
2. Confirm Zed can resolve `uv` from `PATH`.
3. Confirm `agent_servers` JSON is valid.
4. Confirm the target Zed MCP server is active under `context_servers`.
5. Restart Zed after changing settings or upgrading the gateway implementation.
6. Open `dev: open acp logs` and inspect the handshake or process startup failure.

### 9.3 Optional ACP wire tracing

If you need the gateway to record raw ACP request and response payloads, enable tracing explicitly:

```bash
ACP_TRACE_STDIO=1 uv --directory /path/to/agent_teams run agent-teams gateway acp stdio
```

Tracing is off by default so prompt content and tool payloads are not written to logs during normal use.

### 9.4 Common Windows issues

If Zed cannot find `uv`:

- add `uv` to system `PATH`
- or change `command` to the absolute path of `uv.exe`

If the agent exits immediately:

- check `model.json` and `.env`
- then confirm `uv sync --extra dev` was completed in this repository

## 10. Current implementation limits

The current ACP gateway is still a first implementation. Keep these limits in mind:

- implemented: `initialize`, `session/new`, `session/load`, `session/prompt`, `session/cancel`
- implemented: `mcp/connect`, `mcp/message`, `mcp/disconnect`
- implemented: MCP over ACP capability advertisement via `mcpCapabilities.acp`
- current MCP-over-ACP support is focused on session-scoped tool loading and tool invocation flows in Zed
- the current milestone is prompt-turn and session-lifecycle interoperability, not full ACP feature parity

In Zed, the best initial verification targets are:

- the agent appears in the list
- a new thread can be created
- a prompt starts an internal run
- `session/update` messages stream back correctly
- tool progress and raw tool input are visible in the thread UI
- Zed-provided MCP tools can be invoked from an `agent-teams` thread

## 11. Future improvements

To make the Zed integration closer to a production path later, two follow-up steps are likely:

- publish Agent Teams to ACP Registry
- expand MCP-over-ACP coverage beyond the current tool-centric Zed flow

## 12. References

- Zed external agents: https://zed.dev/docs/ai/external-agents
- Zed agent servers and ACP Registry: https://zed.dev/docs/extensions/agent-servers
- ACP editor integration for Zed: https://agentclientprotocol.com/editors/zed
