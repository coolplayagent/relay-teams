from __future__ import annotations

from agent_teams_evals.workspace.docker_setup import DockerConfig


def test_docker_config_uses_docker_subdir_runtime_dockerfile_by_default() -> None:
    config = DockerConfig()

    assert config.runtime_dockerfile == "docker/Dockerfile.agent-runtime"
