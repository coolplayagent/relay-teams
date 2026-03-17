from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
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

# Path inside the runtime base image where agent-teams is installed.
_AGENT_RUNTIME_BIN = "/opt/agent-runtime/venv/bin/agent-teams"


def _log(item_id: str, msg: str) -> None:
    typer.echo(f"  [{item_id}] {msg}")


def _find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _create_runtime_container(image: str) -> str:
    """Create a stopped data container whose volumes will be shared into eval containers."""
    name = f"agent-runtime-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        ["docker", "create", "--name", name, image],
        capture_output=True,
        text=True,
        check=True,
    )
    return name


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

    # SWE-bench image prefix; full image = {image_prefix}.{instance_id}:latest
    image_prefix: str = "swebench/sweb.eval.x86_64"
    # Base image that provides /opt/agent-runtime/venv/ via --volumes-from.
    # Build once with: docker build -f Dockerfile.agent-runtime -t agent-teams-runtime:latest .
    agent_runtime_image: str = "agent-teams-runtime:latest"
    container_startup_timeout_seconds: float = 60.0
    # Host environment variable names to forward into each container.
    forward_env_vars: tuple[str, ...] = _DEFAULT_FORWARD_ENV


class DockerWorkspaceSetup(WorkspaceSetup):
    def __init__(
        self,
        docker_cfg: DockerConfig,
        config_dir: Path | None,
    ) -> None:
        self._docker_cfg = docker_cfg
        self._config_dir = config_dir
        # Create a stopped data container that holds /opt/agent-runtime/.
        # All eval containers mount its volumes via --volumes-from.
        self._runtime_container = _create_runtime_container(
            docker_cfg.agent_runtime_image
        )

    def prepare(self, item: EvalItem) -> PreparedWorkspace:
        if item.base_commit is None:
            raise ValueError(f"Item {item.item_id} has no base_commit")

        image = f"{self._docker_cfg.image_prefix}.{item.item_id}:latest"
        port = _find_free_port()

        cmd = [
            "docker",
            "run",
            "-d",
            "-p",
            f"{port}:8000",
            # Mount /opt/agent-runtime/ from the runtime data container.
            # Python 3.13 + agent-teams live here, NOT on system PATH.
            "--volumes-from",
            self._runtime_container,
        ]

        # Mount agent-teams config dir (model.json, roles/) if provided.
        if self._config_dir is not None:
            cmd += ["-v", f"{self._config_dir.resolve()}:/root/.config/agent-teams"]

        # Forward selected host environment variables.
        for var in self._docker_cfg.forward_env_vars:
            val = os.environ.get(var)
            if val:
                cmd += ["-e", f"{var}={val}"]

        # Start agent-teams directly via the venv binary — no PATH conflict.
        cmd += [
            image,
            _AGENT_RUNTIME_BIN,
            "server",
            "start",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ]

        _log(item.item_id, f"starting container {image} on port {port} ...")
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        container_id = result.stdout.strip()
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

    def teardown(self) -> None:
        """Remove the runtime data container after all items have finished."""
        subprocess.run(
            ["docker", "rm", "-f", self._runtime_container],
            capture_output=True,
        )
