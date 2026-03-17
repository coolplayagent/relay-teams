from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import typer
from pydantic import BaseModel, ConfigDict

from agent_teams_evals.models import EvalItem
from agent_teams_evals.workspace.base import PreparedWorkspace, WorkspaceSetup

# Environment variables forwarded from the host into each container by default.
_DEFAULT_FORWARD_ENV = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)

# Startup script injected into the container via `bash -c`.
# If /agent-teams is volume-mounted (source tree), uv runs in that project dir
# so Python 3.13 is guaranteed by pyproject.toml's requires-python.
# The container's own Python (e.g. 3.9) remains on PATH and is used by shell
# commands the agent spawns — correct for SWE-bench project tests.
_STARTUP_SCRIPT = """\
set -e
export PATH="/root/.cargo/bin:/root/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
if [ -d /agent-teams ]; then
    cd /agent-teams
    uv run agent-teams server start --host 0.0.0.0 --port 8000
else
    uv tool run --python 3.13 agent-teams server start --host 0.0.0.0 --port 8000
fi
"""


def _log(item_id: str, msg: str) -> None:
    typer.echo(f"  [{item_id}] {msg}")


def _find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _docker_run(
    image: str,
    port: int,
    volumes: dict[str, str],
    env: dict[str, str],
) -> str:
    cmd = ["docker", "run", "-d", "-p", f"{port}:8000"]
    for host_path, container_path in volumes.items():
        cmd.extend(["-v", f"{host_path}:{container_path}"])
    for key, val in env.items():
        cmd.extend(["-e", f"{key}={val}"])
    cmd.extend([image, "bash", "-c", _STARTUP_SCRIPT])
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _wait_for_server(base_url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"{base_url}/api/workspaces", timeout=3)
            return
        except Exception:
            time.sleep(2)
    raise TimeoutError(f"agent-teams server at {base_url} not ready within {timeout}s")


class DockerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Image name prefix; full image = {image_prefix}.{instance_id}:latest
    image_prefix: str = "swebench/sweb.eval.x86_64"
    # Path to local agent-teams source tree to volume-mount as /agent-teams.
    # None = agent-teams must already be installed in the image.
    agent_teams_source: Path | None = None
    container_startup_timeout_seconds: float = 120.0
    # Host environment variable names to forward into the container.
    forward_env_vars: tuple[str, ...] = _DEFAULT_FORWARD_ENV


class DockerWorkspaceSetup(WorkspaceSetup):
    def __init__(
        self,
        docker_cfg: DockerConfig,
        config_dir: Path | None,
    ) -> None:
        self._docker_cfg = docker_cfg
        self._config_dir = config_dir

    def prepare(self, item: EvalItem) -> PreparedWorkspace:
        if item.base_commit is None:
            raise ValueError(f"Item {item.item_id} has no base_commit")

        image = f"{self._docker_cfg.image_prefix}.{item.item_id}:latest"
        port = _find_free_port()

        volumes: dict[str, str] = {}
        if self._docker_cfg.agent_teams_source is not None:
            volumes[str(self._docker_cfg.agent_teams_source.resolve())] = "/agent-teams"
        if self._config_dir is not None:
            volumes[str(self._config_dir.resolve())] = "/root/.config/agent-teams"

        env: dict[str, str] = {}
        for var in self._docker_cfg.forward_env_vars:
            val = os.environ.get(var)
            if val:
                env[var] = val

        _log(item.item_id, f"starting container {image} on port {port} ...")
        container_id = _docker_run(image, port, volumes, env)
        _log(item.item_id, f"container: {container_id[:12]}")

        agent_base_url = f"http://localhost:{port}"
        _log(item.item_id, f"waiting for server at {agent_base_url} ...")
        _wait_for_server(
            agent_base_url, self._docker_cfg.container_startup_timeout_seconds
        )
        _log(item.item_id, "server ready")

        return PreparedWorkspace(
            item_id=item.item_id,
            repo_path=Path("/testbed"),  # standard SWE-bench path inside the container
            base_commit=item.base_commit,
            container_id=container_id,
            agent_base_url=agent_base_url,
        )

    def cleanup(self, workspace: PreparedWorkspace) -> None:
        if workspace.container_id:
            subprocess.run(
                ["docker", "rm", "-f", workspace.container_id],
                capture_output=True,
            )
