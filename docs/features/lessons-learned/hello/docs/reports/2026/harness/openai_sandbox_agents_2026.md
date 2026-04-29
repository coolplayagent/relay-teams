# Sandbox Agents

- Source: OpenAI API Docs
- Original URL: https://developers.openai.com/api/docs/guides/agents/sandboxes
- Access date: 2026-04-19
- Published: 2026-04-15 (page timestamp seen in fetch result)
- Type: Documentation page

## Summary / key value

Documents the separation between **harness** and **compute** for agent systems. Particularly valuable for infrastructure design: the harness owns orchestration, tool routing, approvals, tracing, recovery, and state, while sandbox compute provides isolated execution.

## Why it is relevant to AI Harness Engineering

- Explicit definition of harness responsibilities.
- Strong engineering guidance for isolating execution environments from control-plane logic.
- Useful for AI engineering platform and agent runtime architecture work.

## Important excerpts

> The harness is the control plane around the model: it owns the agent loop, model calls, tool routing, handoffs, approvals, tracing, recovery, and run state.

> Running the harness inside the sandbox can be convenient for prototypes, but it puts orchestration and model-directed execution in the same compute boundary.

## Archived content

This page describes sandbox agents as isolated Unix-like workspaces with filesystems, shells, ports, snapshots, and controlled access. The key harness lesson is architectural: keep orchestration in your infrastructure and let sandboxes host narrowly-scoped execution when stateful workspace or provider-specific compute is required.
