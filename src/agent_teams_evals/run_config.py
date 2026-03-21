from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from agent_teams_evals.backends.agent_teams_config import AgentTeamsConfig
from agent_teams_evals.workspace.docker_setup import DockerConfig

_SAMPLE_YAML = """\
# Agent Teams Eval Config
# Generate this file: agent-teams-evals init-config
# Run with:           agent-teams-evals run --config eval.yaml
# Resume is automatic when output_dir already has checkpoint files.
# Force a fresh run with:
#   agent-teams-evals run --config eval.yaml --restart

# --- Dataset ---
dataset: jsonl                          # jsonl | swebench
dataset_path: .agent_teams/evals/datasets/custom.jsonl

# --- Scorer ---
scorer: keyword                         # keyword | regex | event_status | swebench | swebench_docker
swebench_pass_threshold: 0.8            # patch Jaccard threshold (primary for swebench, auxiliary for swebench_docker)

# --- Backend ---
backend: agent_teams
agent_teams:
  base_url: "http://127.0.0.1:8000"    # used in git mode; docker mode uses per-container port
  execution_mode: ai
  session_mode: normal                  # normal | orchestration
  orchestration_preset_id: null         # null = use server default when session_mode=orchestration
  yolo: true
  timeout_seconds: 600
  config_dir: null                      # docker mode: stage this config dir into the container
                                        # whitelist: model.json, notifications.json,
                                        # orchestration.json, .env, mcp.json, logger.ini,
                                        # roles/, skills/
                                        # e.g. ./eval_configs/claude-sonnet
                                        # null = use whatever config is in the container

# --- Workspace ---
workspace_mode: git                     # git | docker
evals_workdir: .agent_teams/evals/workspaces
git_clone_timeout_seconds: 120

docker:
  image_prefix: "swebench/sweb.eval.x86_64"
  # Runtime base image: provides uv, a managed Python 3.12, and an offline
  # wheelhouse via /opt/agent-runtime/ through --volumes-from.
  # Build once: docker build -f docker/Dockerfile.agent-runtime -t agent-teams-runtime:latest .
  agent_runtime_image: "agent-teams-runtime:latest"
  agent_runtime_bin: "/opt/agent-runtime/bin/agent-teams"
  container_startup_timeout_seconds: 60
  forward_env_vars:
    - ANTHROPIC_API_KEY
    - HTTP_PROXY
    - HTTPS_PROXY
    - NO_PROXY
  # extra_env: verbatim env vars injected into containers.
  # Use when container-side values differ from host, e.g. proxy with
  # host.docker.internal instead of 127.0.0.1:
  # extra_env:
  #   HTTP_PROXY: "http://host.docker.internal:7897"
  #   HTTPS_PROXY: "http://host.docker.internal:7897"

# --- Filtering ---
limit: null                             # max items to run, null = all
item_ids: []                            # run only these item IDs, [] = all

# --- Execution ---
concurrency: 1
keep_workspaces: false
save_artifacts: true                    # persist replay data (patch, output, db, logs)
infra_retry_attempts: 2                 # retry infra-only failures before recording a final failure
infra_retry_backoff_seconds: 5.0        # fixed backoff between infra retry attempts

# --- Output ---
output_dir: .agent_teams/evals/results
report_format: json                     # json | html | both
                                        # report.json is refreshed during the run

# --- Cost estimation (USD per 1M tokens) ---
cost_per_million_input_tokens: 3.0      # Claude Sonnet input price
cost_per_million_cached_input_tokens: 0.3  # cache-read price when reported
cost_per_million_output_tokens: 15.0    # Claude Sonnet output price
cost_per_million_reasoning_output_tokens: 15.0  # reasoning output price when reported
"""


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Dataset
    dataset: str = "jsonl"
    dataset_path: Path | None = None

    # Scorer
    scorer: str = "keyword"
    swebench_pass_threshold: float = 0.8

    # Backend
    backend: str = "agent_teams"
    agent_teams: AgentTeamsConfig = Field(default_factory=AgentTeamsConfig)

    # Workspace
    workspace_mode: str = "git"
    evals_workdir: Path = Path(".agent_teams/evals/workspaces")
    git_clone_timeout_seconds: float = 120.0
    docker: DockerConfig = Field(default_factory=DockerConfig)

    # Filtering
    limit: int | None = None
    item_ids: tuple[str, ...] = ()

    # Execution
    concurrency: int = 1
    keep_workspaces: bool = False
    save_artifacts: bool = True
    infra_retry_attempts: int = 2
    infra_retry_backoff_seconds: float = 5.0

    # Output
    output_dir: Path = Path(".agent_teams/evals/results")
    report_format: Literal["json", "html", "both"] = "json"
    cost_per_million_input_tokens: float = 3.0
    cost_per_million_cached_input_tokens: float = 0.3
    cost_per_million_output_tokens: float = 15.0
    cost_per_million_reasoning_output_tokens: float = 15.0


def load_run_config(path: Path) -> RunConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Config file must be a YAML mapping: {path}")
    return RunConfig.model_validate(raw)


def sample_yaml() -> str:
    return _SAMPLE_YAML
