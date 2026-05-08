# Agent Runtime Testing Matrix

## Unit Tests

- Config and model validation:
  - ACP allows stdio, streamable HTTP, and custom transports.
  - A2A requires streamable HTTP.
  - CLI requires stdio.
  - secret values are stored and rehydrated only through the secret store.
- Runtime routing:
  - unbound roles use local runtime execution.
  - `bound_agent_id` roles dispatch by configured protocol.
  - unknown runtime ids remain rejected during explicit role save/validation.
- ACP:
  - remote session creation and reuse.
  - prompt packaging includes runtime role prompt and user prompt.
  - host-tool bridge context refresh.
- A2A:
  - Agent Card discovery.
  - direct JSON-RPC endpoint fallback.
  - `message/send` success.
  - task polling success and terminal failure states.
- CLI:
  - initialize, initialized notification, thread start, turn start.
  - streamed delta collection.
  - completed item fallback.
  - empty output failure.
  - closed stdout failure.
  - Codex app-server command normalization.
- Runtime message bus:
  - publish, subscribe, receive, snapshot.
  - compatibility with existing `/a2a` response shapes.
- Runtime instances:
  - reusable instance selection for delegated tasks.
  - ephemeral instance creation for same-role concurrency.
  - fresh ephemeral instance creation for `spawn_subagent`.
  - `create_subagent_instance()` compatibility behavior.

## Integration Tests

- `orch_dispatch_task` executes a delegated task for:
  - local role
  - bound ACP role
  - bound A2A role
  - bound CLI role
- `spawn_subagent` executes:
  - synchronous foreground run
  - background run
  - recovery of a pending synchronous run
  - recovery of a background run
- Session projections:
  - reusable delegated-role instances appear in the agent panel.
  - normal-mode `spawn_subagent` runs appear in subagent child-session
    projections.
  - runtime prompt/tool snapshots remain visible.
- Run controls:
  - stop, retry, inject, and recovery behavior continue to target the correct
    runtime instance.

## Required Local Checks

Run the full repository self-check after implementation:

```bash
uv run --extra dev ruff check --fix
uv run --extra dev ruff format --no-cache --force-exclude
uv run --extra dev basedpyright
uv run --extra dev pytest -q tests/unit_tests
uv run --extra dev pytest -q tests/integration_tests
```

Focused checks while developing:

```bash
uv run --extra dev pytest -q tests/unit_tests/agent_runtimes
uv run --extra dev pytest -q tests/unit_tests/agents/orchestration
uv run --extra dev pytest -q tests/unit_tests/sessions
uv run --extra dev pytest -q tests/unit_tests/interfaces/server/test_a2a_router.py
```
