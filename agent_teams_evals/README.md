# agent_teams_evals

Benchmark evaluation framework for agent-teams. Drives the agent system entirely through its HTTP SDK — no internal imports from `src/`.

## Quick start

```bash
# 1. Start the agent-teams backend
agent-teams server start

# 2. Generate a config file
python agent_teams_evals/run.py init-config --output eval.yaml

# 3. Edit eval.yaml (set dataset_path, scorer, etc.)

# 4. Run
python agent_teams_evals/run.py run --config eval.yaml
```

## Config file

All settings live in a single YAML file. Use `init-config` to generate a commented template.

```yaml
# Backend
base_url: "http://127.0.0.1:8000"
execution_mode: ai

# Dataset
dataset: jsonl                   # jsonl | swebench
dataset_path: .agent_teams/evals/datasets/custom.jsonl

# Scorer
scorer: keyword                  # keyword | regex | event_status | swebench

# Execution
run_timeout_seconds: 300
concurrency: 4
keep_workspaces: false

# Output
output_dir: .agent_teams/evals/results
report_format: both              # json | html | both

# Cost estimation (USD per 1M tokens, Claude Sonnet defaults)
cost_per_million_input_tokens: 3.0
cost_per_million_output_tokens: 15.0
```

CLI overrides are available for quick one-offs without editing the file:

```bash
python agent_teams_evals/run.py run --config eval.yaml --limit 5 --concurrency 2
```

## Datasets

Place dataset files under `.agent_teams/evals/datasets/` (git-ignored).

### Custom JSONL

Each line is a JSON object. Required field: `intent`. Optional fields:

| Field | Type | Used by |
|---|---|---|
| `item_id` | str | identifier (auto-generated if absent) |
| `expected_keywords` | list[str] | keyword scorer |
| `expected_patterns` | list[str] | regex scorer |
| `repo_url` | str | swebench workspace setup |
| `base_commit` | str | swebench workspace setup |
| `reference_patch` | str | swebench scorer |

Example:

```json
{"item_id": "hello-world", "intent": "Say hello", "expected_keywords": ["hello"]}
```

### SWE-bench

Download from [SWE-bench/SWE-bench_Verified](https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified) and save as JSONL. Set `dataset: swebench` in config — the loader maps SWE-bench fields automatically.

## Scorers

| Scorer | Passes when |
|---|---|
| `keyword` | all `expected_keywords` appear in agent output |
| `regex` | all `expected_patterns` match agent output |
| `event_status` | run outcome is `completed` (baseline) |
| `swebench` | Jaccard similarity of generated vs reference patch >= threshold |

## How workspace isolation works

For SWE-bench items, each eval item gets its own agent-teams workspace:

1. Repo is cloned to `.agent_teams/evals/workspaces/{item_id}/{run_hash}/repo/`
2. That directory is registered as a temporary workspace via `POST /api/workspaces`
3. The session is created inside that workspace — the agent's file tools are scoped to the repo
4. After the run, the workspace is deleted and the clone is removed (unless `keep_workspaces: true`)

This matches the SWE-agent / OpenHands container isolation pattern.

## Output

Results land in `output_dir` (default `.agent_teams/evals/results/`):

- `report.json` — full structured report (all item results + summary stats)
- `report.html` — self-contained HTML report with per-item table

Summary printed to stdout after each run:

```
Dataset : swebench
Scorer  : swebench
Results : 3/10 passed (30.0%)
Outcomes: completed=8  failed=1  timed_out=1  stopped=0
Tokens  : in=524,000  out=31,000  est_cost=$1.6370
Duration: mean=187.3s  p50=165.2s  p95=310.8s
```

## Re-rendering a report

```bash
python agent_teams_evals/run.py report \
    --results-file .agent_teams/evals/results/report.json \
    --format html
```

## Module layout

```
agent_teams_evals/
    run.py              CLI entry point (typer)
    run_config.py       RunConfig model + YAML loader + sample template
    config.py           EvalConfig (base Pydantic model)
    models.py           EvalItem, EvalResult, EvalReport, RunOutcome, TokenUsage
    runner.py           EvalRunner — drives one item end-to-end
    reporter.py         ASCII table + JSON + HTML output, build_report()
    conftest.py         pytest fixture: backend_url
    loaders/
        jsonl_loader.py
        swebench_loader.py
    scorers/
        keyword_scorer.py
        regex_scorer.py
        event_status_scorer.py
        swebench_scorer.py
    workspace/
        git_setup.py        git clone + checkout per item
        patch_extractor.py  git diff extraction
    jsonl/
        eval_custom.py  pytest parametrize scenario for custom JSONL
    swebench/
        eval_lite.py    pytest parametrize scenario for SWE-bench Lite
```
