# Metrics Platform Design

## Summary

`agent-teams` now exposes a dedicated `metrics/` platform layer for system metrics. Business modules register metric definitions and emit normalized metric events through a shared recorder. Consumers such as the frontend observability view, CLI commands, prettylog output, and future Grafana exporters all read from the same metric domain.

## Layers

1. `metrics core`
   - `MetricDefinition`
   - `MetricRegistry`
   - `MetricRecorder`
   - `MetricEvent`
2. `domain adapters`
   - session metrics
   - llm metrics
   - tool metrics
   - retrieval metrics
3. `sinks`
   - aggregate store sink
   - prettylog sink
   - Grafana exporter sink placeholder
4. `consumers`
   - `/api/observability/*`
   - `agent-teams metrics ...`
   - frontend observability view

## Naming And Tags

Built-in metrics:
- `agent_teams.session.steps`
- `agent_teams.llm.input_tokens`
- `agent_teams.llm.cached_input_tokens`
- `agent_teams.llm.output_tokens`
- `agent_teams.tool.calls`
- `agent_teams.tool.duration_ms`
- `agent_teams.tool.failures`
- `agent_teams.skill.calls`
- `agent_teams.mcp.calls`
- `agent_teams.retrieval.searches`
- `agent_teams.retrieval.search_duration_ms`
- `agent_teams.retrieval.search_failures`
- `agent_teams.retrieval.rebuilds`
- `agent_teams.retrieval.rebuild_duration_ms`
- `agent_teams.retrieval.document_count`

Standard tags:
- `workspace_id`
- `session_id`
- `run_id`
- `instance_id`
- `role_id`
- `tool_name`
- `tool_source`
- `mcp_server`
- `retrieval_backend`
- `retrieval_scope_kind`
- `retrieval_operation`
- `status`

## Storage And Queries

The current aggregate store writes normalized metric points into SQLite and expands every event into `global`, `session`, and `run` scopes. Query services derive:
- cached token ratio
- tool success rate
- average tool duration

## Extension Rules

When a new module adds metrics:
1. Register the metric definition.
2. Add or extend a metrics adapter for that module.
3. Emit metrics through `MetricRecorder`.
4. Do not add a module-specific exporter path.

## Consumers

CLI:
- `agent-teams metrics overview`
- `agent-teams metrics breakdowns`
- `agent-teams metrics tail`

HTTP API:
- `GET /api/observability/overview`
- `GET /api/observability/breakdowns`

Frontend:
- topbar `Observability` view
