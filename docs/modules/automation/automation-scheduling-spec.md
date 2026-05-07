# Automation Scheduling Spec

## Status

Implemented for the interval scheduling and advanced cron editor update.

## Contract

Automation projects support three persisted schedule modes:

- `interval`: runs every `interval_every` `interval_unit`, where unit is
  `minutes`, `hours`, or `days`.
- `cron`: runs from a five-field cron expression.
- `one_shot`: runs once at `run_at`.

The UI exposes friendly schedule controls for intervals, daily, weekdays,
weekly, monthly, and one-time runs. It also exposes advanced cron input for
operators who need direct cron interoperability. Friendly daily, weekday,
weekly, and monthly controls continue to persist as `cron` schedules.

## Data Model

`automation_projects` stores the selected schedule shape in `schedule_mode`.
Interval schedules use `interval_every` and `interval_unit`; cron schedules use
`cron_expression`; one-shot schedules use `run_at`. Unused schedule fields are
stored as null.

Validation is strict for explicit create/update requests:

- `interval` requires `interval_every >= 1` and `interval_unit`, and rejects
  `cron_expression` and `run_at`.
- `cron` requires a valid five-field `cron_expression`, and rejects
  `interval_every`, `interval_unit`, and `run_at`.
- `one_shot` requires timezone-aware `run_at`, and rejects cron and interval
  fields.

## Runtime Semantics

The scheduler scans enabled projects by `next_run_at`.

- First interval run is scheduled at create/enable time plus one full interval.
- Scheduled interval runs advance from the scheduler fire time by one interval.
- Downtime or polling delays do not backfill missed intervals; one due project
  creates one run and then computes the next cursor.
- Manual `:run` creates an immediate run without changing the scheduled cursor
  for recurring interval and cron schedules. Manual one-shot runs keep the
  existing one-shot behavior: they disable the project and clear `next_run_at`.
- Scheduled runs do not check whether a previous run is still active. Bound IM
  sessions still use the existing bound-session queue behavior.

## UI Semantics

The automation editor parses persisted schedules into the most specific friendly
control it can represent. Cron expressions that are not one of the friendly
daily, weekday, weekly, or monthly forms open in advanced cron mode and remain
editable as raw cron.

Schedule summaries must be readable in the project list, detail view, and editor
preview for all modes.

## Validation

Coverage should include model validation, repository roundtrip, scheduler
cursor math, manual-run cursor preservation, router payloads, frontend payload
construction, friendly cron parsing, advanced cron editing, and browser-level
request payload checks.
