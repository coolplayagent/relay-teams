# Terminal-Bench Evaluations

relay-teams can run Terminal-Bench tasks through `relay-teams-evals` with:

- `dataset: terminalbench`
- `workspace_mode: terminalbench`
- `scorer: terminalbench`

The integration launches each task's Docker Compose environment, starts a
relay-teams server inside the Terminal-Bench client container, and scores the
result by copying the official `run-tests.sh` and `tests/` into `/tests` after
the agent finishes. If `dataset_path` does not already contain Terminal-Bench
tasks, the loader downloads `terminalbench.dataset_name` automatically through
the official Terminal-Bench registry client.

## Quick Run

```bash
relay-teams-evals run \
  --config .agent_teams/evals/configs/normal/eval-terminalbench-smoke.yaml \
  --restart
```

Use `eval-terminalbench-full.yaml` for the full local dataset and the matching
`orchestration/` configs to evaluate orchestration mode.

## Container Startup

`workspace_mode: terminalbench` handles the Docker lifecycle for each task:

1. Creates one stopped relay-teams runtime data container from
   `terminalbench.agent_runtime_image`.
2. Copies the Terminal-Bench task directory into
   `.agent_teams/evals/workspaces/terminalbench/<task>/<run>/task`.
3. Patches that copied `docker-compose.yaml` so the `client` service mounts the
   relay-teams runtime, receives forwarded API/proxy environment variables, and
   publishes the relay-teams server port.
4. Runs `docker compose build` unless `terminalbench.no_rebuild: true`, then
   `docker compose up -d`.
5. Starts `relay-teams server start` inside the running Terminal-Bench client
   container.
6. Registers the container working directory as the agent workspace.
7. After the agent run, copies official tests to `/tests`, runs
   `bash /tests/run-tests.sh`, parses the output, and tears down the compose
   project.

## Dataset Download

The checked-in configs enable automatic dataset download:

```yaml
dataset: terminalbench
dataset_path: .agent_teams/evals/datasets/terminal-bench-core

terminalbench:
  auto_download_dataset: true
  dataset_name: "terminal-bench-core"
  dataset_version: "head"
  overwrite_dataset: false
```

Set `auto_download_dataset: false` if you want to manage the dataset directory
yourself. Set `overwrite_dataset: true` to force a fresh download into
`dataset_path`.
