from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import ConfigDict

from agent_teams_evals.config import EvalConfig

_SAMPLE_YAML = """\
# Agent Teams Eval Config
# Generate this file: python agent_teams_evals/run.py init-config
# Run with:           python agent_teams_evals/run.py run --config eval.yaml

# --- Backend ---
base_url: "http://127.0.0.1:8000"
workspace_id: default
execution_mode: ai

# --- Dataset ---
dataset: jsonl                          # jsonl | swebench
dataset_path: .agent_teams/evals/datasets/custom.jsonl

# --- Scorer ---
scorer: keyword                         # keyword | regex | event_status | swebench

# --- Filtering ---
limit: null                             # max items to run, null = all
item_ids: []                            # run only these item IDs, [] = all

# --- Execution ---
run_timeout_seconds: 300
concurrency: 1
keep_workspaces: false
git_clone_timeout_seconds: 120

# --- Scoring ---
swebench_pass_threshold: 0.8            # Jaccard threshold for swebench scorer

# --- Cost estimation (USD per 1M tokens) ---
cost_per_million_input_tokens: 3.0      # Claude Sonnet input price
cost_per_million_output_tokens: 15.0    # Claude Sonnet output price

# --- Output ---
output_dir: .agent_teams/evals/results
report_format: json                     # json | html | both
"""


class RunConfig(EvalConfig):
    """Full run configuration: EvalConfig fields plus dataset/scorer/report selection."""

    model_config = ConfigDict(extra="forbid")

    dataset: str = "jsonl"
    scorer: str = "keyword"
    report_format: Literal["json", "html", "both"] = "json"


def load_run_config(path: Path) -> RunConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Config file must be a YAML mapping: {path}")
    return RunConfig.model_validate(raw)


def sample_yaml() -> str:
    return _SAMPLE_YAML
