# -*- coding: utf-8 -*-
from __future__ import annotations

import json
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
        == "relay_teams.interfaces.cli.app:main"
    )
    assert (
        pyproject["project"]["scripts"]["relay-teams-evals"]
        == "relay_teams_evals.run:app"
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
    assert 'relay-teams-evals = "relay_teams_evals.run:app"' in runtime_pyproject_script


def test_pr_checks_gate_changed_line_unit_coverage() -> None:
    project_root = _project_root()
    pr_workflow = (project_root / ".github" / "workflows" / "pr-checks.yml").read_text(
        encoding="utf-8"
    )
    pyproject_path = project_root / "pyproject.toml"
    with pyproject_path.open("rb") as handle:
        pyproject = tomllib.load(handle)

    dev_dependencies = pyproject["project"]["optional-dependencies"]["dev"]
    coverage_run = pyproject["tool"]["coverage"]["run"]
    diff_cover = pyproject["tool"]["diff_cover"]

    assert "diff-cover>=9.0.0" in dev_dependencies
    assert "bandit>=1.8.0" in dev_dependencies
    assert "xenon>=0.9.3" in dev_dependencies
    assert coverage_run["source"] == ["src/relay_teams", "src/relay_teams_evals"]
    assert diff_cover["compare_branch"] == "origin/main"
    assert diff_cover["fail_under"] == 90
    assert diff_cover["include"] == [
        "src/relay_teams/**/*.py",
        "src/relay_teams_evals/**/*.py",
    ]
    assert "fetch-depth: 0" in pr_workflow
    assert "ruff check --no-cache --force-exclude ." in pr_workflow
    assert "ruff format --check --no-cache --force-exclude ." in pr_workflow
    assert "bandit -r src" in pr_workflow
    assert "--severity-level high" in pr_workflow
    assert "xenon" in pr_workflow
    assert "--max-modules C" in pr_workflow
    assert "--cov=src/relay_teams" in pr_workflow
    assert "--cov=src/relay_teams_evals" in pr_workflow
    assert "--cov-report=xml:coverage.xml" in pr_workflow
    assert "diff-cover coverage.xml --config-file pyproject.toml" in pr_workflow


def test_qodana_code_quality_workflow_uses_cloud_scan() -> None:
    project_root = _project_root()
    qodana_workflow = (
        project_root / ".github" / "workflows" / "code_quality.yml"
    ).read_text(encoding="utf-8")
    qodana_config = (project_root / "qodana.yaml").read_text(encoding="utf-8")

    assert "name: Qodana" in qodana_workflow
    assert "JetBrains/qodana-action@v2025.3.2" in qodana_workflow
    assert "pr-mode: false" in qodana_workflow
    assert "QODANA_TOKEN" in qodana_workflow
    assert 'QODANA_ENDPOINT: "https://qodana.cloud"' in qodana_workflow
    assert "fetch-depth: 0" in qodana_workflow
    assert "linter: qodana-python-community" in qodana_config
    assert "failureConditions" not in qodana_config


def test_pptx_craft_package_metadata_preserves_esm_runtime_contract() -> None:
    package_json_path = (
        _project_root()
        / "src"
        / "relay_teams"
        / "builtin"
        / "skills"
        / "pptx-craft"
        / "package.json"
    )

    package_json = json.loads(package_json_path.read_text(encoding="utf-8"))

    assert package_json["type"] == "module"
    assert package_json["engines"]["node"] == ">=18.0.0"
