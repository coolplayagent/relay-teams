from __future__ import annotations

from pathlib import Path


def test_runtime_dockerfile_wheelhouse_contains_project_runtime_dependencies() -> None:
    dockerfile = (
        Path(__file__).resolve().parents[4] / "docker" / "Dockerfile.agent-runtime"
    ).read_text(encoding="utf-8")

    assert '"lark-oapi==1.5.3"' in dockerfile
    assert '"markitdown[pdf,docx,pptx,xlsx]>=0.1.5,<0.2"' in dockerfile


def test_runtime_dockerfile_allows_dev_project_version_offline_install() -> None:
    dockerfile = (
        Path(__file__).resolve().parents[4] / "docker" / "Dockerfile.agent-runtime"
    ).read_text(encoding="utf-8")

    assert "--prerelease=allow" in dockerfile
