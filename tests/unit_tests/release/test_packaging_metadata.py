# -*- coding: utf-8 -*-
from __future__ import annotations

import tomllib
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_pyproject_uses_relay_teams_distribution_name_and_scripts() -> None:
    pyproject_path = _project_root() / "pyproject.toml"
    with pyproject_path.open("rb") as handle:
        pyproject = tomllib.load(handle)

    assert pyproject["project"]["name"] == "relay-teams"
    assert (
        pyproject["project"]["scripts"]["relay-teams"]
        == "agent_teams.interfaces.cli.app:main"
    )
    assert (
        pyproject["project"]["scripts"]["relay-teams-evals"]
        == "agent_teams_evals.run:app"
    )
    assert "agent-teams" not in pyproject["project"]["scripts"]
    assert "agent-teams-evals" not in pyproject["project"]["scripts"]


def test_release_workflow_and_runtime_wrapper_reference_relay_teams() -> None:
    project_root = _project_root()
    release_workflow = (
        project_root / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")
    runtime_dockerfile = (
        project_root / "docker" / "Dockerfile.agent-runtime"
    ).read_text(encoding="utf-8")
    runtime_pyproject_script = (
        project_root / "docker" / "prepare_runtime_pyproject.py"
    ).read_text(encoding="utf-8")

    assert "https://pypi.org/project/relay-teams/" in release_workflow
    assert '--find-links "$RUNTIME_ROOT/wheels" relay-teams' in runtime_dockerfile
    assert 'exec "$VENV_PATH/bin/relay-teams" "$@"' in runtime_dockerfile
    assert "/opt/agent-runtime/bin/relay-teams server start ..." in runtime_dockerfile
    assert 'relay-teams-evals = "agent_teams_evals.run:app"' in runtime_pyproject_script
