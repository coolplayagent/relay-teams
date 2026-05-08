from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import typer
import yaml
from pydantic import BaseModel, ConfigDict

from relay_teams_evals.models import EvalItem
from relay_teams_evals.workspace.base import (
    PreparedWorkspace,
    WorkspaceSetup,
    WorkspaceSetupError,
)
from relay_teams_evals.workspace.docker_setup import (
    _DEFAULT_FORWARD_ENV,
    _build_runtime_image,
    _create_runtime_container,
    _find_free_port,
    _wait_for_server,
)


def _log(item_id: str, msg: str) -> None:
    typer.echo(f"  [{item_id}] {msg}")


class TerminalBenchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_download_dataset: bool = True
    dataset_name: str = "terminal-bench-core"
    dataset_version: str = "head"
    registry_url: str | None = None
    local_registry_path: Path | None = None
    overwrite_dataset: bool = False
    agent_runtime_image: str = "agent-teams-runtime:latest"
    agent_runtime_bin: str = "/opt/agent-runtime/bin/relay-teams"
    runtime_dockerfile: str = "docker/Dockerfile.agent-runtime"
    build_runtime_image: bool = False
    container_server_port: int = 8000
    container_startup_timeout_seconds: float = 60.0
    no_rebuild: bool = False
    cleanup: bool = False
    forward_env_vars: tuple[str, ...] = _DEFAULT_FORWARD_ENV
    extra_env: dict[str, str] = {}


def _safe_slug(value: str) -> str:
    chars = [ch.lower() if ch.isalnum() else "-" for ch in value]
    return "".join(chars).strip("-")[:40] or "task"


def _task_path(item: EvalItem) -> Path:
    raw_path = item.extra_fields.get("terminalbench_task_path")
    if not raw_path:
        raise ValueError(f"Item {item.item_id} has no terminalbench_task_path")
    return Path(raw_path)


def _compose_vars(
    *,
    container_name: str,
    image_name: str,
    docker_name_prefix: str,
    logs_path: Path,
    agent_logs_path: Path,
) -> dict[str, str]:
    return {
        "T_BENCH_TASK_DOCKER_CLIENT_CONTAINER_NAME": container_name,
        "T_BENCH_TASK_DOCKER_CLIENT_IMAGE_NAME": image_name,
        "T_BENCH_TASK_DOCKER_NAME_PREFIX": docker_name_prefix,
        "T_BENCH_CONTAINER_LOGS_PATH": "/logs",
        "T_BENCH_CONTAINER_AGENT_LOGS_PATH": "/agent-logs",
        "T_BENCH_TEST_DIR": "/tests",
        "T_BENCH_TASK_LOGS_PATH": str(logs_path.resolve()),
        "T_BENCH_TASK_AGENT_LOGS_PATH": str(agent_logs_path.resolve()),
    }


def _normalize_environment(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, dict):
        return [f"{key}={val}" for key, val in value.items()]
    return []


def _patch_compose_file(
    *,
    compose_path: Path,
    compose_vars: dict[str, str],
    runtime_container: str,
    host_port: int,
    server_port: int,
    config_dir: Path | None,
    forwarded_env: dict[str, str],
) -> None:
    text = compose_path.read_text(encoding="utf-8")
    for key, val in compose_vars.items():
        text = text.replace("${" + key + "}", val)
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid docker-compose YAML: {compose_path}")

    services = data.setdefault("services", {})
    if not isinstance(services, dict) or "client" not in services:
        raise ValueError("Terminal-Bench compose file must define services.client")
    client = services["client"]
    if not isinstance(client, dict):
        raise ValueError("Terminal-Bench services.client must be a mapping")

    volumes_from = client.setdefault("volumes_from", [])
    if isinstance(volumes_from, list) and runtime_container not in volumes_from:
        volumes_from.append(runtime_container)

    ports = client.setdefault("ports", [])
    port_mapping = f"{host_port}:{server_port}"
    if isinstance(ports, list) and port_mapping not in ports:
        ports.append(port_mapping)

    env_entries = _normalize_environment(client.get("environment"))
    existing_keys = {entry.split("=", 1)[0] for entry in env_entries}
    for key, val in forwarded_env.items():
        if key not in existing_keys:
            env_entries.append(f"{key}={val}")
    client["environment"] = env_entries

    if config_dir is not None:
        volumes = client.setdefault("volumes", [])
        config_mount = f"{config_dir.resolve()}:/tmp/agent-config-host:ro"
        if isinstance(volumes, list) and config_mount not in volumes:
            volumes.append(config_mount)

    compose_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _docker_compose(
    *,
    project_name: str,
    compose_path: Path,
    args: list[str],
    env: dict[str, str],
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            project_name,
            "-f",
            str(compose_path.resolve()),
            *args,
        ],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def _container_workdir(container_id: str) -> str:
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.Config.WorkingDir}}", container_id],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    workdir = result.stdout.strip()
    return workdir or "/"


class TerminalBenchWorkspaceSetup(WorkspaceSetup):
    def __init__(
        self,
        *,
        workdir: Path,
        config: TerminalBenchConfig,
        agent_config_dir: Path | None,
    ) -> None:
        self._workdir = workdir
        self._cfg = config
        self._agent_config_dir = (
            agent_config_dir.expanduser() if agent_config_dir else None
        )
        self._prepared: dict[str, PreparedWorkspace] = {}

        if config.build_runtime_image:
            _build_runtime_image(config.runtime_dockerfile, config.agent_runtime_image)
        self._runtime_container = _create_runtime_container(config.agent_runtime_image)

    def prepare(self, item: EvalItem) -> PreparedWorkspace:
        source_task_path = _task_path(item)
        if not (source_task_path / "docker-compose.yaml").exists():
            raise ValueError(
                f"Terminal-Bench task has no docker-compose.yaml: {source_task_path}"
            )

        run_slug = uuid.uuid4().hex[:8]
        item_slug = _safe_slug(item.item_id)
        task_run_dir = self._workdir / "terminalbench" / item_slug / run_slug
        task_path = task_run_dir / "task"
        logs_path = task_run_dir / "logs"
        agent_logs_path = task_run_dir / "agent-logs"
        logs_path.mkdir(parents=True, exist_ok=True)
        agent_logs_path.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_task_path, task_path)

        port = _find_free_port()
        project_name = f"rt-tb-{item_slug}-{run_slug}"
        image_name = f"{project_name}__client"
        compose_path = task_path / "docker-compose.yaml"
        forwarded = {
            key: val
            for key in self._cfg.forward_env_vars
            if (val := os.environ.get(key))
        }
        forwarded.update(self._cfg.extra_env)
        compose_vars = _compose_vars(
            container_name=project_name,
            image_name=image_name,
            docker_name_prefix=f"tb__{item_slug}",
            logs_path=logs_path,
            agent_logs_path=agent_logs_path,
        )
        _patch_compose_file(
            compose_path=compose_path,
            compose_vars=compose_vars,
            runtime_container=self._runtime_container,
            host_port=port,
            server_port=self._cfg.container_server_port,
            config_dir=self._agent_config_dir,
            forwarded_env=forwarded,
        )
        env = os.environ.copy()
        env.update(compose_vars)

        container_id = ""
        try:
            if not self._cfg.no_rebuild:
                _log(item.item_id, "building Terminal-Bench task container ...")
                _docker_compose(
                    project_name=project_name,
                    compose_path=compose_path,
                    args=["build"],
                    env=env,
                )

            _log(item.item_id, "starting Terminal-Bench task container ...")
            _docker_compose(
                project_name=project_name,
                compose_path=compose_path,
                args=["up", "-d"],
                env=env,
            )

            result = subprocess.run(
                ["docker", "inspect", "--format", "{{.Id}}", project_name],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
            )
            container_id = result.stdout.strip()
            container_repo_path = _container_workdir(container_id)
            _log(item.item_id, f"container: {container_id[:12]}")

            subprocess.run(
                [
                    "docker",
                    "exec",
                    "-d",
                    container_id,
                    "/opt/agent-runtime/eval-entrypoint.sh",
                    self._cfg.agent_runtime_bin,
                    "server",
                    "start",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    str(self._cfg.container_server_port),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
            )
            agent_base_url = f"http://localhost:{port}"
            _log(item.item_id, f"waiting for server at {agent_base_url} ...")
            _wait_for_server(
                agent_base_url, self._cfg.container_startup_timeout_seconds
            )
            _log(item.item_id, "server ready")

        except (OSError, subprocess.CalledProcessError, TimeoutError) as exc:
            if project_name:
                _docker_compose(
                    project_name=project_name,
                    compose_path=compose_path,
                    args=["down"],
                    env=env,
                    check=False,
                )
            raise WorkspaceSetupError(str(exc), retryable=True) from exc

        prepared = PreparedWorkspace(
            item_id=item.item_id,
            repo_path=task_path,
            base_commit="terminalbench",
            container_id=container_id,
            agent_base_url=agent_base_url,
            container_repo_path=container_repo_path,
            compose_project_name=project_name,
            compose_file_path=compose_path,
            terminalbench_task_path=task_path,
        )
        self._prepared[item.item_id] = prepared
        return prepared

    def cleanup(self, workspace: PreparedWorkspace) -> None:
        if workspace.compose_project_name and workspace.compose_file_path:
            env = os.environ.copy()
            _docker_compose(
                project_name=workspace.compose_project_name,
                compose_path=workspace.compose_file_path,
                args=["down"],
                env=env,
                check=False,
            )
            if self._cfg.cleanup:
                _docker_compose(
                    project_name=workspace.compose_project_name,
                    compose_path=workspace.compose_file_path,
                    args=["down", "--rmi", "all", "--volumes"],
                    env=env,
                    check=False,
                )
        if not workspace.repo_path.exists():
            return
        shutil.rmtree(workspace.repo_path.parent, ignore_errors=True)

    def teardown(self) -> None:
        subprocess.run(
            ["docker", "rm", "-f", self._runtime_container],
            capture_output=True,
        )
