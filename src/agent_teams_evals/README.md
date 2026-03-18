# agent_teams_evals

Benchmark evaluation framework for agent-teams. Lives under `src/agent_teams_evals/` as part of the main project package layout. Drives the agent system through its HTTP SDK.

## Setup

The evals dependencies (`swebench`, `docker`, `datasets`) are declared as a dependency group in the root `pyproject.toml` and installed automatically by `uv sync`:

```bash
uv sync
```

## Quick start

```bash
# 1. Start the agent-teams backend
agent-teams server start

# 2. Generate a config file
agent-teams-evals init-config --output eval.yaml

# 3. Edit eval.yaml (set dataset_path, scorer, workspace_mode, etc.)

# 4. Run
agent-teams-evals run --config eval.yaml
```

CLI overrides are available for quick one-offs without editing the file:

```bash
agent-teams-evals run --config eval.yaml --limit 5 --concurrency 2
```

## Workspace modes

### git mode (default)

Clones the repo locally on the host, registers the clone directory as a temporary agent-teams workspace, then deletes both after the run.

```yaml
workspace_mode: git
evals_workdir: .agent_teams/evals/workspaces
git_clone_timeout_seconds: 120
```

### docker mode

Runs each eval item inside a dedicated SWE-bench Docker container. The agent-teams server starts inside the container alongside the repo. This is the recommended mode for SWE-bench.

```yaml
workspace_mode: docker

docker:
  # SWE-bench image prefix; full image = {image_prefix}.{instance_id}:latest
  image_prefix: "swebench/sweb.eval.x86_64"

  # Runtime base image -- build once before running evals:
  #   docker build -f Dockerfile.agent-runtime -t agent-teams-runtime:latest .
  agent_runtime_image: "agent-teams-runtime:latest"

  # Path to the agent-teams binary inside agent_runtime_image.
  agent_runtime_bin: "/opt/agent-runtime/venv/bin/agent-teams"

  # Port the agent-teams server listens on inside each container.
  container_server_port: 8000

  # Path inside each eval container where the repo is checked out.
  container_repo_path: "/testbed"

  container_startup_timeout_seconds: 60

  # Host env vars forwarded into every container.
  forward_env_vars:
    - ANTHROPIC_API_KEY
    - HTTP_PROXY
    - HTTPS_PROXY
    - NO_PROXY

  # Verbatim env vars injected into containers (no host-env lookup).
  # Use for values that differ from the host, e.g. proxy via host.docker.internal:
  # extra_env:
  #   HTTP_PROXY: "http://host.docker.internal:7897"
  #   HTTPS_PROXY: "http://host.docker.internal:7897"

  # Auto-build missing SWE-bench instance images (requires docker + datasets packages).
  build_instance_images: false
```

The runtime image is a data container -- it is created once (`docker create`) and mounted into every eval container via `--volumes-from`. It provides Python 3.12 and the agent-teams venv at `/opt/agent-runtime/`.

## Config file reference

All settings live in a single YAML file. Use `init-config` to generate a commented template.

```yaml
# --- Dataset ---
dataset: jsonl                          # jsonl | swebench
dataset_path: .agent_teams/evals/datasets/custom.jsonl

# --- Scorer ---
scorer: keyword                         # keyword | regex | event_status | swebench | swebench_docker
swebench_pass_threshold: 0.8            # used by swebench scorer (Jaccard threshold)

# --- Backend ---
backend: agent_teams
agent_teams:
  base_url: "http://127.0.0.1:8000"    # used in git mode; docker mode uses per-container port
  execution_mode: ai
  approval_mode: yolo
  timeout_seconds: 600
  config_dir: null                      # path mounted as ~/.config/agent-teams in containers
                                        # controls model, role, system prompt
                                        # null = use whatever config is in the container

# --- Workspace ---
workspace_mode: git                     # git | docker
evals_workdir: .agent_teams/evals/workspaces
git_clone_timeout_seconds: 120

docker:
  image_prefix: "swebench/sweb.eval.x86_64"
  agent_runtime_image: "agent-teams-runtime:latest"
  container_startup_timeout_seconds: 60
  forward_env_vars:
    - ANTHROPIC_API_KEY
    - HTTP_PROXY
    - HTTPS_PROXY
    - NO_PROXY

# --- Filtering ---
limit: null                             # max items to run, null = all
item_ids: []                            # run only these item IDs, [] = all

# --- Execution ---
concurrency: 1
keep_workspaces: false

# --- Output ---
output_dir: .agent_teams/evals/results
report_format: json                     # json | html | both

# --- Cost estimation (USD per 1M tokens) ---
cost_per_million_input_tokens: 3.0
cost_per_million_output_tokens: 15.0
```

## Datasets

Place dataset files under `.agent_teams/evals/datasets/` (git-ignored).

### Custom JSONL

Each record is a JSON object (pretty-printed multi-line JSON is also supported). Required field: `intent`. Optional fields:

| Field | Type | Used by |
|---|---|---|
| `item_id` | str | identifier (auto-generated if absent) |
| `expected_keywords` | list[str] | keyword scorer |
| `expected_patterns` | list[str] | regex scorer |
| `repo_url` | str | git/docker workspace setup |
| `base_commit` | str | git/docker workspace setup |
| `reference_patch` | str | swebench scorer |
| `fail_to_pass` | list[str] | swebench_docker scorer |
| `pass_to_pass` | list[str] | swebench_docker scorer |

Example:

```json
{"item_id": "hello-world", "intent": "Say hello", "expected_keywords": ["hello"]}
```

### SWE-bench

Download from [SWE-bench/SWE-bench_Verified](https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified) and save the file under `.agent_teams/evals/datasets/`. Set `dataset: swebench` in config -- the loader maps SWE-bench fields automatically.

## Scorers

| Scorer | Passes when | Requires |
|---|---|---|
| `keyword` | all `expected_keywords` appear in agent output | -- |
| `regex` | all `expected_patterns` match agent output | -- |
| `event_status` | run outcome is `completed` (baseline) | -- |
| `swebench` | Jaccard similarity of generated vs reference patch >= threshold | git diff, `reference_patch` |
| `swebench_docker` | `fail_to_pass` tests pass and `pass_to_pass` tests do not regress | docker mode, `fail_to_pass`/`pass_to_pass` |

`swebench_docker` runs `pytest` directly inside the eval container via `docker exec` -- no patch extraction needed.

## How workspace isolation works

### git mode

1. Repo is cloned to `.agent_teams/evals/workspaces/{item_id}/{run_hash}/repo/`
2. That directory is registered as a temporary workspace via `POST /api/workspaces`
3. The session is created inside that workspace -- the agent's file tools are scoped to the repo
4. After the run, the workspace is deleted and the clone is removed (unless `keep_workspaces: true`)

### docker mode

1. A stopped runtime data container is created once from `agent_runtime_image`
2. For each item, a SWE-bench image is launched with `docker run -d`, mounting the runtime via `--volumes-from`
3. The agent-teams server starts inside the container; the runner waits for it to become ready
4. A temporary workspace is registered pointing to `container_repo_path` inside the container
5. After the run, the workspace is deleted and the container is removed (unless `keep_workspaces: true`)
6. The runtime data container is removed after all items finish

## Output

Results land in `output_dir` (default `.agent_teams/evals/results/`):

- `report.json` -- full structured report (all item results + summary stats)
- `report.html` -- self-contained HTML report with per-item table

Summary printed to stdout after each run:

```
Dataset : swebench
Scorer  : swebench_docker
Results : 3/10 passed (30.0%)
Outcomes: completed=8  failed=1  timed_out=1  stopped=0
Tokens  : in=524,000  out=31,000  est_cost=$1.6370
Duration: mean=187.3s  p50=165.2s  p95=310.8s
```

## Re-rendering a report

```bash
agent-teams-evals report \
    --results-file .agent_teams/evals/results/report.json \
    --format html
```

## Module layout

```
src/agent_teams_evals/
    run.py                  CLI entry point (typer)
    run_config.py           RunConfig model + YAML loader + sample template
    models.py               EvalItem, EvalResult, EvalReport, RunOutcome, TokenUsage
    runner.py               EvalRunner -- drives one item end-to-end
    reporter.py             ASCII table + JSON + HTML output, build_report()
    conftest.py             pytest fixture: backend_url
    backends/
        base.py             AgentBackend ABC
        agent_teams.py      AgentTeamsBackend + AgentTeamsConfig
    loaders/
        base.py             DatasetLoader ABC
        jsonl_loader.py     generic JSONL (multi-line JSON supported)
        swebench_loader.py  SWE-bench field mapping
    scorers/
        base.py             Scorer ABC
        keyword_scorer.py
        regex_scorer.py
        event_status_scorer.py
        swebench_scorer.py      Jaccard patch similarity
        swebench_docker_scorer.py  pytest inside container via docker exec
    workspace/
        base.py             PreparedWorkspace model + WorkspaceSetup ABC
        git_setup.py        git clone + checkout per item
        docker_setup.py     DockerConfig + DockerWorkspaceSetup
        patch_extractor.py  git diff extraction (local or via docker exec)
    jsonl/
        eval_custom.py      pytest parametrize scenario for custom JSONL
    swebench_evals/
        eval_lite.py        pytest parametrize scenario for SWE-bench
```
