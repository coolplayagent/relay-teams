import pytest
from pathlib import Path

from agent_teams.tools.stage_tools.docs import (
    current_stage_doc_path,
    previous_stage_doc_path,
)
from agent_teams.tools.stage_tools.docs import write_stage_doc_once


def test_stage_doc_paths() -> None:
    root = Path("D:/workspace/agent_teams")
    run_id = "run123"

    assert current_stage_doc_path(
        workspace_root=root, run_id=run_id, role_id="spec_spec"
    ) == (root / ".agent_teams" / "stage_docs" / run_id / "spec.md")
    assert current_stage_doc_path(
        workspace_root=root, run_id=run_id, role_id="spec_design"
    ) == (root / ".agent_teams" / "stage_docs" / run_id / "design.md")
    assert current_stage_doc_path(
        workspace_root=root, run_id=run_id, role_id="spec_verify"
    ) == (root / ".agent_teams" / "stage_docs" / run_id / "verify.md")

    assert previous_stage_doc_path(
        workspace_root=root, run_id=run_id, role_id="spec_design"
    ) == (root / ".agent_teams" / "stage_docs" / run_id / "spec.md")
    assert previous_stage_doc_path(
        workspace_root=root, run_id=run_id, role_id="spec_coder"
    ) == (root / ".agent_teams" / "stage_docs" / run_id / "design.md")
    assert previous_stage_doc_path(
        workspace_root=root, run_id=run_id, role_id="spec_verify"
    ) == (root / ".agent_teams" / "stage_docs" / run_id / "design.md")


def test_write_stage_doc_once_rejects_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = Path("smoke_stage_doc_once.md")
    written = {"exists": False}

    def _exists(_: Path) -> bool:
        return written["exists"]

    def _mkdir(_: Path, parents: bool, exist_ok: bool) -> None:
        return None

    def _write_text(_: Path, content: str, encoding: str) -> int:
        written["exists"] = True
        return len(content)

    monkeypatch.setattr(Path, "exists", _exists)
    monkeypatch.setattr(Path, "write_text", _write_text)
    monkeypatch.setattr(Path, "mkdir", _mkdir)

    write_stage_doc_once(path=path, content="v1")
    with pytest.raises(ValueError):
        write_stage_doc_once(path=path, content="v2")
