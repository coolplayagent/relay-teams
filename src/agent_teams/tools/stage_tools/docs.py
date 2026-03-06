from __future__ import annotations

from pathlib import Path

STAGE_ROLE_TO_FILE = {
    "spec_spec": "spec.md",
    "spec_design": "design.md",
    "spec_verify": "verify.md",
}

PREVIOUS_STAGE_FILE = {
    "spec_design": "spec.md",
    "spec_coder": "design.md",
    "spec_verify": "design.md",
}


def stage_docs_dir(workspace_root: Path, run_id: str) -> Path:
    return workspace_root / ".agent_teams" / "stage_docs" / run_id


def current_stage_doc_path(*, workspace_root: Path, run_id: str, role_id: str) -> Path:
    file_name = STAGE_ROLE_TO_FILE.get(role_id)
    if file_name is None:
        raise ValueError(f"Role does not have stage doc: {role_id}")
    return stage_docs_dir(workspace_root, run_id) / file_name


def previous_stage_doc_path(*, workspace_root: Path, run_id: str, role_id: str) -> Path:
    file_name = PREVIOUS_STAGE_FILE.get(role_id)
    if file_name is None:
        raise ValueError(f"Role does not have previous stage doc: {role_id}")
    return stage_docs_dir(workspace_root, run_id) / file_name


def write_stage_doc_once(*, path: Path, content: str) -> None:
    if path.exists():
        raise ValueError(f"stage document already written: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
