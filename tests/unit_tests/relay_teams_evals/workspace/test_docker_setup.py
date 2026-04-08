from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams_evals.workspace.base import WorkspaceSetupError
from relay_teams_evals.workspace.docker_setup import (
    DockerConfig,
    _ensure_instance_image,
)


class _FakeConstantsModule:
    def __init__(self, tmp_path: Path) -> None:
        self.BASE_IMAGE_BUILD_DIR = tmp_path / "base"
        self.ENV_IMAGE_BUILD_DIR = tmp_path / "env"
        self.INSTANCE_IMAGE_BUILD_DIR = tmp_path / "instances"


class _FakeTestSpec:
    def __init__(self) -> None:
        self.instance_id = "demo"
        self.env_image_key = "sweb.env.py.x86_64.fake:latest"
        self.instance_image_key = "sweb.eval.x86_64.demo:latest"
        self.install_repo_script = "\n".join(
            [
                "#!/bin/bash",
                "set -euxo pipefail",
                "source /opt/miniconda3/bin/activate",
                "conda activate testbed",
                'echo "Current environment: $CONDA_DEFAULT_ENV"',
                "python -m pip install -e .[test] --verbose",
            ]
        )
        self.instance_dockerfile = "FROM fake\n"
        self.platform = "linux/x86_64"


class _FakeTestSpecModule:
    def __init__(self, spec: _FakeTestSpec) -> None:
        self._spec = spec

    def make_test_spec(
        self,
        instance,
        namespace: str | None = None,
        base_image_tag: str = "latest",
        env_image_tag: str = "latest",
        instance_image_tag: str = "latest",
        arch: str = "x86_64",
    ) -> _FakeTestSpec:
        _ = (
            instance,
            namespace,
            base_image_tag,
            env_image_tag,
            instance_image_tag,
            arch,
        )
        return self._spec


class _FakeDockerModule:
    def from_env(self) -> object:
        return object()


class _FakeDockerBuildModule:
    def __init__(self) -> None:
        self.setup_repo_script = ""

    def build_env_images(
        self,
        *,
        client: object,
        dataset: list[object],
        force_rebuild: bool,
        max_workers: int,
        namespace: str | None = None,
        instance_image_tag: str | None = None,
        env_image_tag: str = "latest",
    ) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
        _ = (
            client,
            dataset,
            force_rebuild,
            max_workers,
            namespace,
            instance_image_tag,
            env_image_tag,
        )
        return ([], [])

    def build_image(
        self,
        image_name: str,
        setup_scripts: dict[str, str],
        dockerfile: str,
        platform: str,
        client: object,
        build_dir: Path,
        nocache: bool = False,
    ) -> None:
        _ = (image_name, dockerfile, platform, client, nocache)
        build_dir.mkdir(parents=True, exist_ok=True)
        self.setup_repo_script = setup_scripts["setup_repo.sh"]


def test_docker_config_uses_docker_subdir_runtime_dockerfile_by_default() -> None:
    config = DockerConfig()

    assert config.runtime_dockerfile == "docker/Dockerfile.agent-runtime"


def test_ensure_instance_image_passes_through_original_setup_repo_script(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spec = _FakeTestSpec()
    build_module = _FakeDockerBuildModule()
    constants = _FakeConstantsModule(tmp_path)
    state_counts: dict[str, int] = {}

    def _image_exists(image: str) -> bool:
        count = state_counts.get(image, 0)
        state_counts[image] = count + 1
        if image == spec.instance_image_key:
            return count >= 1
        if image == spec.env_image_key:
            return True
        return False

    monkeypatch.setattr(
        "relay_teams_evals.workspace.docker_setup._install_resource_stub",
        lambda: None,
    )
    monkeypatch.setattr(
        "relay_teams_evals.workspace.docker_setup._load_docker_module",
        lambda: _FakeDockerModule(),
    )
    monkeypatch.setattr(
        "relay_teams_evals.workspace.docker_setup._configure_swebench_log_dirs",
        lambda: constants,
    )
    monkeypatch.setattr(
        "relay_teams_evals.workspace.docker_setup._load_swebench_docker_build_module",
        lambda: build_module,
    )
    monkeypatch.setattr(
        "relay_teams_evals.workspace.docker_setup._load_swebench_test_spec_module",
        lambda: _FakeTestSpecModule(spec),
    )
    monkeypatch.setattr(
        "relay_teams_evals.workspace.docker_setup._load_dataset",
        lambda path, *, split, streaming=False: [{"instance_id": "demo"}],
    )
    monkeypatch.setattr(
        "relay_teams_evals.workspace.docker_setup._image_exists",
        _image_exists,
    )

    _ensure_instance_image("demo", spec.instance_image_key, "dataset")

    assert build_module.setup_repo_script == spec.install_repo_script


def test_ensure_instance_image_fails_when_built_image_is_still_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spec = _FakeTestSpec()
    build_module = _FakeDockerBuildModule()
    constants = _FakeConstantsModule(tmp_path)
    build_log_path = (
        constants.INSTANCE_IMAGE_BUILD_DIR
        / spec.instance_image_key.replace(":", "__")
        / "build_image.log"
    )

    def _build_image(
        image_name: str,
        setup_scripts: dict[str, str],
        dockerfile: str,
        platform: str,
        client: object,
        build_dir: Path,
        nocache: bool = False,
    ) -> None:
        _ = (image_name, setup_scripts, dockerfile, platform, client, nocache)
        build_dir.mkdir(parents=True, exist_ok=True)
        (build_dir / "build_image.log").write_text(
            "ModuleNotFoundError: No module named 'pkg_resources'\n",
            encoding="utf-8",
        )

    build_module.build_image = _build_image

    monkeypatch.setattr(
        "relay_teams_evals.workspace.docker_setup._install_resource_stub",
        lambda: None,
    )
    monkeypatch.setattr(
        "relay_teams_evals.workspace.docker_setup._load_docker_module",
        lambda: _FakeDockerModule(),
    )
    monkeypatch.setattr(
        "relay_teams_evals.workspace.docker_setup._configure_swebench_log_dirs",
        lambda: constants,
    )
    monkeypatch.setattr(
        "relay_teams_evals.workspace.docker_setup._load_swebench_docker_build_module",
        lambda: build_module,
    )
    monkeypatch.setattr(
        "relay_teams_evals.workspace.docker_setup._load_swebench_test_spec_module",
        lambda: _FakeTestSpecModule(spec),
    )
    monkeypatch.setattr(
        "relay_teams_evals.workspace.docker_setup._load_dataset",
        lambda path, *, split, streaming=False: [{"instance_id": "demo"}],
    )
    monkeypatch.setattr(
        "relay_teams_evals.workspace.docker_setup._image_exists",
        lambda image: image == spec.env_image_key,
    )

    with pytest.raises(WorkspaceSetupError) as exc_info:
        _ensure_instance_image("demo", spec.instance_image_key, "dataset")

    exc = exc_info.value
    assert exc.retryable is False
    assert exc.build_log_path == str(build_log_path)
    assert exc.build_error_summary is not None
    assert "pkg_resources" in exc.build_error_summary
    assert "failed to build" in str(exc)
