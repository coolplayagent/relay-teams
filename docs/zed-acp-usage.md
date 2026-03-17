# Zed IDE ACP Usage

## 1. Scope

This document explains how to use the local `agent-teams gateway acp stdio` entrypoint as an ACP agent inside Zed IDE.

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

### 3.3 Verify the gateway command first

Windows example:

```powershell
uv --directory D:/openworkspace/agent_teams run agent-teams gateway acp stdio
```

If the process starts and stays attached to the terminal, the ACP stdio gateway is up. Stop it with `Ctrl+C` after the check.

Do not debug Zed first if this command fails. Fix the local startup issue first.

## 4. Configure a custom ACP agent in Zed

Open Zed user settings JSON and add an entry under `agent_servers`.

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

## 5. Use the agent in Zed

After the configuration is saved:

1. Restart Zed, or reload the window.
2. Open the Agent Panel.
3. Start a new agent thread.
4. Select `agent-teams` from the agent list.
5. Send a simple prompt such as `Summarize the current repository layout.`

If the setup is correct, Zed starts the local `agent-teams gateway acp stdio` subprocess and communicates with it over ACP.

## 6. Debugging

### 6.1 Open ACP logs

Zed provides ACP debug logs.

Run this from the Command Palette:

```text
dev: open acp logs
```

This is the most direct place to inspect ACP requests, responses, and startup errors.

### 6.2 Recommended troubleshooting order

If `agent-teams` does not show up in Zed, check in this order:

1. Confirm `uv --directory <repo> run agent-teams gateway acp stdio` works in a terminal.
2. Confirm Zed can resolve `uv` from `PATH`.
3. Confirm `agent_servers` JSON is valid.
4. Open `dev: open acp logs` and inspect the handshake or process startup failure.

### 6.3 Common Windows issues

If Zed cannot find `uv`:

- add `uv` to system `PATH`
- or change `command` to the absolute path of `uv.exe`

If the agent exits immediately:

- check `model.json` and `.env`
- then confirm `uv sync --extra dev` was completed in this repository

## 7. Current implementation limits

The current ACP gateway is still a first implementation. Keep these limits in mind:

- implemented: `initialize`, `session/new`, `session/load`, `session/prompt`, `session/cancel`
- implemented: `mcp/connect`, `mcp/disconnect`
- not implemented yet: full `mcp/message` relay
- MCP over ACP capability advertisement is not fully enabled yet
- the current milestone is prompt-turn and session-lifecycle interoperability, not full ACP feature parity

In Zed, the best initial verification targets are:

- the agent appears in the list
- a new thread can be created
- a prompt starts an internal run
- `session/update` messages stream back correctly

## 8. Future improvements

To make the Zed integration closer to a production path later, two follow-up steps are likely:

- publish Agent Teams to ACP Registry
- complete MCP over ACP relay and capability advertisement

## 9. References

- Zed external agents: https://zed.dev/docs/ai/external-agents
- Zed agent servers and ACP Registry: https://zed.dev/docs/extensions/agent-servers
- ACP editor integration for Zed: https://agentclientprotocol.com/editors/zed