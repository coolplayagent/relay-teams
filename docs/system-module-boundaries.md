# System Module Boundaries and Shared Runtime Primitives

## Purpose

The monitor/event-driven substrate adds another asynchronous plane on top of runs,
background tasks, triggers, and notifications.
To keep that expansion maintainable, module ownership must stay explicit and shared
runtime concerns must stay centralized instead of being reimplemented per feature.

This document defines the intended boundaries for the current backend and the rules
for extending event sources such as CI, PR, and log monitoring.

## Layering

The backend should keep the following dependency direction:

`interfaces/*` -> `services` -> `repositories/shared infra`

With the current layout, that means:

- `interfaces/cli`, `interfaces/server`, and `interfaces/sdk` are transport adapters only.
- `interfaces/server/container.py` is the composition root. It wires dependencies and
  may choose implementations, but it should not contain feature logic.
- `sessions/runs/*` owns run lifecycle, recovery, injection, and SSE event projection.
- `sessions/runs/background_tasks/*` owns subprocess execution and local process event
  production.
- `triggers/*` owns external provider ingress, webhook verification, repository
  subscriptions, and provider-triggered automation behavior.
- `monitors/*` owns the event-driven substrate itself: normalized envelopes,
  subscription persistence, deterministic matching, cooldown/dedupe, and action
  dispatch.
- `notifications/*` owns outbound notification delivery only.
- `persistence/*` and module-local `repository.py` files own storage mechanics only;
  they should not perform orchestration decisions.

## Shared Runtime Modules

The following modules are shared runtime primitives and should be reused instead of
reimplemented inside feature modules.

### `relay_teams.paths`

Use `paths` for app/user/project root resolution and filesystem helpers.

Rules:

- Prefer `RuntimeConfig.paths` whenever runtime has already been resolved.
- Use `relay_teams.paths` helpers for global config roots and reusable filesystem
  operations.
- Do not rebuild app config paths ad hoc with `Path.home()`, string concatenation, or
  duplicate `~/.relay-teams` knowledge inside feature modules.
- If a new stable runtime/config location becomes shared across modules, add it to
  `RuntimeConfig.paths` or `relay_teams.paths` instead of duplicating the layout.

Boundary:

- `paths` is for global runtime roots and reusable filesystem helpers.
- `workspace/*` is for per-workspace execution layout and artifact placement.
- Feature modules should not guess workspace-local directories that belong to
  `WorkspaceManager`.

### `relay_teams.env`

Use `env` for environment loading, secret-backed environment variables, proxy-aware
runtime config, and subprocess env assembly.

Rules:

- `.env` parsing, secret-backed env loading, and merged env resolution stay in
  `relay_teams.env`.
- Proxy-aware network behavior should flow through `env.proxy_env` and `net.clients`,
  not ad hoc `httpx` configuration spread across feature code.
- Subprocess integrations should request prepared env maps such as
  `build_subprocess_env`, `build_github_cli_env`, or other `env` helpers rather than
  manually stitching together `os.environ`.
- Feature modules should depend on typed config services or prepared env maps instead
  of reading raw env keys directly.

Boundary:

- It is acceptable for process-launch edges to finally hand a fully assembled env map
  to `subprocess` or asyncio process APIs.
- It is not acceptable for domain services to become the source of truth for env file
  paths, secret resolution, or proxy rules.

### Other Shared Infra

- `logger/*`: structured logging and diagnostics.
- `net/*`: proxy-aware HTTP client construction and transport policy.
- `secrets/*`: secret persistence and masking.
- `trace/*`: trace/span context propagation.

Cross-cutting behavior should land in these shared modules when it is reused across
features.

## Event-Driven Substrate Placement

The monitor substrate is intentionally separate from each event source.

Flow:

1. Event source module ingests source-native input.
2. Event source module normalizes that input into `MonitorEventEnvelope`.
3. `MonitorService.emit(...)` evaluates subscriptions and records trigger audit data.
4. `MonitorService` dispatches through a narrow action sink boundary.
5. `RunManager` and `NotificationService` consume those actions without owning source
   normalization or matching rules.

Current placement:

- Local process output/state events are produced in
  `sessions/runs/background_tasks/manager.py`.
- GitHub webhook ingress and trigger normalization live in `triggers/service.py`,
  with HTTP transport adaptation in `interfaces/server/routers/triggers.py`.
- Run wake-up and follow-up routing live in `sessions/runs/run_manager.py`.
- Notification fan-out stays in `notifications/*`.

This keeps each module focused:

- event sources know how to ingest their own source
- monitors know how to subscribe/match/trigger
- run orchestration knows how to wake or continue work

## Monitor and Trigger Boundary Rules

The following rules apply to current and future event sources:

- `background_tasks/*` and `triggers/*` may emit monitor envelopes, but they must not
  query `MonitorRepository` directly.
- `monitors/*` must not parse GitHub signatures, read log files, or own source-native
  transport concerns.
- `interfaces/server/routers/*.py` should remain thin and only translate HTTP into
  service calls.
- `RunManager` should remain the place that decides how a run is resumed, injected, or
  followed up after a monitor trigger.
- Notification delivery should stay optional and side-effect-only; it should not own
  monitor matching behavior.

## Extending New Event Sources

When adding a new source such as CI providers, remote logs, or external delivery
systems:

1. Keep source-native auth, polling/webhook parsing, and normalization inside the
   source module.
2. Normalize into `MonitorEventEnvelope` as early as possible.
3. Emit through `MonitorService.emit(...)`.
4. Reuse shared `paths`, `env`, `logger`, `net`, and `secrets` modules instead of
   inventing source-local config/env/path helpers.
5. Do not bypass service boundaries to write directly into unrelated repositories.
6. Update `docs/api-design.md` and `docs/database-schema.md` whenever the source adds a
   new public API contract or durable storage contract.

For sources that only expose polling APIs, the polling loop belongs to an adapter in
the source module.
The monitor core should still only receive normalized event envelopes rather than
owning poll scheduling itself.

## Anti-Patterns

The following patterns should be treated as design drift:

- Reconstructing app config paths with hard-coded home-directory logic.
- Reading or mutating raw `os.environ` from domain services when `env` already owns the
  concern.
- Putting event matching, dedupe, or cooldown logic into routers or source modules.
- Letting interface layers access repositories directly.
- Mixing workspace-local path decisions into unrelated modules instead of going through
  `workspace/*`.
- Re-implementing proxy handling or secret loading in feature code.

## Current Working Model

For the monitor substrate introduced in this phase, the intended ownership is:

- `monitors/*`: durable subscription model and deterministic trigger engine.
- `sessions/runs/background_tasks/*`: local process event source.
- `triggers/*`: GitHub ingress and PR/CI-style source integration.
- `sessions/runs/run_manager.py`: wake-up/follow-up orchestration sink.
- `notifications/*`: optional operator-facing delivery sink.
- `interfaces/server/*`: transport surface only.

As new sources are added, they should fit this shape rather than introducing parallel
mini-frameworks for paths, env loading, event matching, or wake-up behavior.
