# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from agent_teams.paths import get_app_config_dir
from agent_teams.workspace import WorkspaceHandle


def stage_docs_dir(
    workspace: WorkspaceHandle | None = None,
    run_id: str = "",
    workspace_root: Path | None = None,
) -> Path:
    if workspace is not None:
        return workspace.artifacts.stage_docs_dir(run_id)
    if workspace_root is None:
        raise ValueError("workspace or workspace_root is required")
    return get_app_config_dir() / "stage_docs" / run_id


def current_stage_doc_path(
    *,
    workspace: WorkspaceHandle | None = None,
    run_id: str,
    role_id: str,
    workspace_root: Path | None = None,
) -> Path:
    if workspace is not None:
        return workspace.artifacts.current_stage_doc_path(
            run_id=run_id, role_id=role_id
        )
    file_name = {
        "spec_spec": "spec.md",
        "spec_design": "design.md",
        "spec_verify": "verify.md",
    }.get(role_id)
    if file_name is None:
        raise ValueError(f"Role does not have stage doc: {role_id}")
    return stage_docs_dir(workspace_root=workspace_root, run_id=run_id) / file_name


def previous_stage_doc_path(
    *,
    workspace: WorkspaceHandle | None = None,
    run_id: str,
    role_id: str,
    workspace_root: Path | None = None,
) -> Path:
    if workspace is not None:
        return workspace.artifacts.previous_stage_doc_path(
            run_id=run_id, role_id=role_id
        )
    file_name = {
        "spec_design": "spec.md",
        "spec_coder": "design.md",
        "spec_verify": "design.md",
    }.get(role_id)
    if file_name is None:
        raise ValueError(f"Role does not have previous stage doc: {role_id}")
    return stage_docs_dir(workspace_root=workspace_root, run_id=run_id) / file_name


def write_stage_doc_once(
    *,
    workspace: WorkspaceHandle | None = None,
    path: Path,
    content: str,
    workspace_root: Path | None = None,
) -> None:
    if workspace is not None:
        workspace.artifacts.write_stage_doc_once(path=path, content=content)
        return
    if path.exists():
        raise ValueError(f"stage document already written: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
