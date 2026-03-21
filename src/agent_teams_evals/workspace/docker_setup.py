from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from importlib import import_module
import os
import socket
import subprocess
import sys
import time
import types
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Protocol, cast

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

type DatasetRow = Mapping[str, object]


class _DockerClient(Protocol): ...


class _DockerModule(Protocol):
    def from_env(self) -> _DockerClient: ...


class _DatasetsModule(Protocol):
    def load_dataset(
        self,
        path: str,
        *,
        split: str,
        streaming: bool = False,
    ) -> Iterable[DatasetRow]: ...


class _SWEConstantsModule(Protocol):
    BASE_IMAGE_BUILD_DIR: Path
    ENV_IMAGE_BUILD_DIR: Path
    INSTANCE_IMAGE_BUILD_DIR: Path


class _SWEDockerBuildModule(Protocol):
    def build_instance_images(
        self,
        *,
        client: _DockerClient,
        dataset: Sequence[DatasetRow],
        force_rebuild: bool,
        max_workers: int,
        tag: str,
        env_image_tag: str,
    ) -> None: ...


def _log(item_id: str, msg: str) -> None:
    typer.echo(f"  [{item_id}] {msg}")


class _ResourceModule(types.ModuleType):
    RLIMIT_NOFILE: int

    def getrlimit(self, resource: int) -> tuple[int, int]:
        _ = resource
        return (0, 0)

    def setrlimit(self, resource: int, limits: tuple[int, int]) -> None:
        _ = (resource, limits)


def _install_resource_stub() -> None:
    if "resource" in sys.modules or sys.platform != "win32":
        return
    resource_stub = _ResourceModule("resource")
    resource_stub.RLIMIT_NOFILE = 0
    sys.modules["resource"] = resource_stub


def _load_docker_module() -> _DockerModule:
    return cast(_DockerModule, import_module("docker"))


def _load_dataset(
    path: str,
    *,
    split: str,
    streaming: bool = False,
) -> Iterable[DatasetRow]:
    datasets_module = cast(_DatasetsModule, import_module("datasets"))
    return datasets_module.load_dataset(path, split=split, streaming=streaming)


def _load_swebench_constants_module() -> _SWEConstantsModule:
    return cast(_SWEConstantsModule, import_module("swebench.harness.constants"))


def _load_swebench_docker_build_module() -> _SWEDockerBuildModule:
    return cast(
        _SWEDockerBuildModule,
        import_module("swebench.harness.docker_build"),
    )


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
        encoding="utf-8",
        errors="replace",
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
    # Base image that provides /opt/agent-runtime/ via --volumes-from.
    # Build once with: docker build -f docker/Dockerfile.agent-runtime -t agent-teams-runtime:latest .
    agent_runtime_image: str = "agent-teams-runtime:latest"
    # Path to the wrapper inside agent_runtime_image that creates a local
    # container venv and starts agent-teams from there.
    agent_runtime_bin: str = "/opt/agent-runtime/bin/agent-teams"
    # Port the agent-teams server listens on inside the container.
    container_server_port: int = 8000
    # Path inside each eval container where the repo is checked out.
    container_repo_path: str = "/testbed"
    container_startup_timeout_seconds: float = 60.0
    # Host environment variable names to forward into each container.
    forward_env_vars: tuple[str, ...] = _DEFAULT_FORWARD_ENV
    # Extra environment variables injected verbatim into each container.
    # Use this for container-specific values that differ from the host, e.g.
    # proxy addresses using host.docker.internal instead of 127.0.0.1.
    extra_env: dict[str, str] = {}
    # When true, build agent_runtime_image from runtime_dockerfile before running.
    # The build context is the current working directory.
    build_runtime_image: bool = False
    # Dockerfile used when build_runtime_image is true.
    runtime_dockerfile: str = "docker/Dockerfile.agent-runtime"
    # When true, automatically build missing SWE-bench instance images before
    # each eval item runs. Requires the swebench and docker Python packages.
    build_instance_images: bool = False
    # HuggingFace dataset used to look up instance metadata when building images.
    swebench_dataset: str = "SWE-bench/SWE-bench_Verified"


def _image_exists(image: str) -> bool:
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True,
    )
    return result.returncode == 0


def _ensure_instance_image(item_id: str, image: str, dataset_name: str) -> None:
    """Build a SWE-bench instance image if it does not already exist."""
    if _image_exists(image):
        return
    typer.echo(f"  [{item_id}] image {image!r} not found, building ...")
    try:
        # swebench.harness imports `prepare_images` which imports the Unix-only
        # `resource` module.  Stub it on Windows before the first import so that
        # the package loads; the stub is never actually called during image builds.
        _install_resource_stub()
    except ImportError as exc:
        raise RuntimeError(
            f"swebench and docker packages are required to auto-build instance images: {exc}"
        ) from exc

    docker_sdk = _load_docker_module()
    swe_constants = _load_swebench_constants_module()
    swe_docker_build = _load_swebench_docker_build_module()

    # swebench hardcodes relative Path("logs/build_images/...") constants, which
    # creates a logs/ directory in CWD (the project root).  Redirect them to the
    # project's configured log directory so build artifacts land alongside other logs.
    from agent_teams.paths import get_project_log_dir

    _log_dir = get_project_log_dir() / "build_images"
    swe_constants.BASE_IMAGE_BUILD_DIR = _log_dir / "base"
    swe_constants.ENV_IMAGE_BUILD_DIR = _log_dir / "env"
    swe_constants.INSTANCE_IMAGE_BUILD_DIR = _log_dir / "instances"

    ds = _load_dataset(dataset_name, split="test")
    instances = [
        row for item in ds if (row := dict(item)).get("instance_id") == item_id
    ]
    if not instances:
        raise RuntimeError(
            f"Instance {item_id!r} not found in dataset {dataset_name!r}"
        )

    client = docker_sdk.from_env()
    # build_instance_images chains build_env_images -> build_base_images internally.
    # Tag parameters must be passed explicitly because swebench 4.x defaults them to
    # None which triggers an assertion inside make_test_spec.
    swe_docker_build.build_instance_images(
        client=client,
        dataset=instances,
        force_rebuild=False,
        max_workers=1,
        tag="latest",
        env_image_tag="latest",
    )
    typer.echo(f"  [{item_id}] instance image ready.")


def _build_runtime_image(dockerfile: str, image: str) -> None:
    typer.echo(f"Building runtime image {image!r} from {dockerfile!r} ...")
    subprocess.run(
        ["docker", "build", "-f", dockerfile, "-t", image, "."],
        check=True,
    )
    typer.echo(f"Runtime image {image!r} built.")


class DockerWorkspaceSetup(WorkspaceSetup):
    def __init__(
        self,
        docker_cfg: DockerConfig,
        config_dir: Path | None,
    ) -> None:
        self._docker_cfg = docker_cfg
        self._config_dir = config_dir
        if docker_cfg.build_runtime_image:
            _build_runtime_image(
                docker_cfg.runtime_dockerfile, docker_cfg.agent_runtime_image
            )
        # Create a stopped data container that holds /opt/agent-runtime/.
        # All eval containers mount its volumes via --volumes-from.
        self._runtime_container = _create_runtime_container(
            docker_cfg.agent_runtime_image
        )

    def prepare(self, item: EvalItem) -> PreparedWorkspace:
        return self._prepare_agent_workspace(item)

    def _prepare_agent_workspace(self, item: EvalItem) -> PreparedWorkspace:
        if item.base_commit is None:
            raise ValueError(f"Item {item.item_id} has no base_commit")

        image = f"{self._docker_cfg.image_prefix}.{item.item_id}:latest"

        if self._docker_cfg.build_instance_images:
            _ensure_instance_image(
                item.item_id, image, self._docker_cfg.swebench_dataset
            )

        port = _find_free_port()
        container_id = self._run_container(
            item=item,
            image=image,
            port=port,
            command=[
                "/opt/agent-runtime/eval-entrypoint.sh",
                self._docker_cfg.agent_runtime_bin,
                "server",
                "start",
                "--host",
                "0.0.0.0",
                "--port",
                str(self._docker_cfg.container_server_port),
            ],
            with_runtime=True,
            log_msg=f"starting container {image} on port {port} ...",
        )
        _log(item.item_id, f"container: {container_id[:12]}")

        agent_base_url = f"http://localhost:{port}"
        _log(item.item_id, f"waiting for server at {agent_base_url} ...")
        _wait_for_server(
            agent_base_url, self._docker_cfg.container_startup_timeout_seconds
        )
        _log(item.item_id, "server ready")

        return PreparedWorkspace(
            item_id=item.item_id,
            repo_path=Path("."),  # placeholder; container_repo_path is used instead
            base_commit=item.base_commit,
            container_id=container_id,
            agent_base_url=agent_base_url,
            container_repo_path=self._docker_cfg.container_repo_path,
        )

    def _run_container(
        self,
        *,
        item: EvalItem,
        image: str,
        port: int | None,
        command: list[str],
        with_runtime: bool,
        log_msg: str,
    ) -> str:
        cmd = ["docker", "run", "-d"]
        if port is not None:
            cmd += [
                "-p",
                f"{port}:{self._docker_cfg.container_server_port}",
            ]
        if with_runtime:
            # Mount /opt/agent-runtime/ from the runtime data container.
            # The mounted runtime contains uv, a managed Python 3.12, and an
            # offline wheelhouse. The wrapper creates a local venv per container.
            cmd += ["--volumes-from", self._runtime_container]

            # Mount host config read-only to a staging path. The eval entrypoint
            # copies only a small whitelist of runtime config files/directories
            # into place so host-local logs and other incidental files are not
            # carried into eval containers.
            if self._config_dir is not None:
                host_cfg = self._config_dir.expanduser().resolve()
                cmd += ["-v", f"{host_cfg}:/tmp/agent-config-host:ro"]

        for var in self._docker_cfg.forward_env_vars:
            val = os.environ.get(var)
            if val:
                cmd += ["-e", f"{var}={val}"]

        for key, val in self._docker_cfg.extra_env.items():
            cmd += ["-e", f"{key}={val}"]

        cmd += [image, *command]

        _log(item.item_id, log_msg)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        return result.stdout.strip()

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
