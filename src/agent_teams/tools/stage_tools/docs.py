# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

from agent_teams.workspace import WorkspaceHandle

STAGE_ROLE_TO_DIR = {
    "spec_spec": "spec",
    "spec_design": "design",
    "spec_verify": "verify",
}

PREVIOUS_STAGE_DIR = {
    "spec_design": "spec",
    "spec_coder": "design",
    "spec_verify": "design",
}


def stage_docs_dir(
    workspace: WorkspaceHandle | None = None,
    session_id: str = "",
    role_id: str = "",
    workspace_root: Path | None = None,
) -> Path:
    if workspace is not None:
        if not session_id or not role_id:
            raise ValueError("session_id and role_id are required")
        return (
            workspace.root_path
            / ".agent_teams"
            / "sessions"
            / session_id
            / "roles"
            / role_id
            / "stage"
        )
    if workspace_root is None:
        raise ValueError("workspace or workspace_root is required")
    if not session_id or not role_id:
        raise ValueError("session_id and role_id are required")
    return (
        workspace_root
        / ".agent_teams"
        / "sessions"
        / session_id
        / "roles"
        / role_id
        / "stage"
    )


def current_stage_doc_path(
    *,
    workspace: WorkspaceHandle | None = None,
    session_id: str,
    role_id: str,
    workspace_root: Path | None = None,
) -> Path:
    if workspace is not None:
        stage_name = STAGE_ROLE_TO_DIR.get(role_id)
        if stage_name is None:
            raise ValueError(f"Role does not have stage doc: {role_id}")
        return _latest_stage_doc(
            stage_docs_dir(
                workspace=workspace,
                session_id=session_id,
                role_id=role_id,
            )
            / stage_name
        )
    stage_name = STAGE_ROLE_TO_DIR.get(role_id)
    if stage_name is None:
        raise ValueError(f"Role does not have stage doc: {role_id}")
    return _latest_stage_doc(
        stage_docs_dir(
            workspace_root=workspace_root,
            session_id=session_id,
            role_id=role_id,
        )
        / stage_name
    )


def previous_stage_doc_path(
    *,
    workspace: WorkspaceHandle | None = None,
    session_id: str,
    role_id: str,
    workspace_root: Path | None = None,
) -> Path:
    if workspace is not None:
        stage_name = PREVIOUS_STAGE_DIR.get(role_id)
        if stage_name is None:
            raise ValueError(f"Role does not have previous stage doc: {role_id}")
        return _latest_stage_doc(
            stage_docs_dir(
                workspace=workspace,
                session_id=session_id,
                role_id=role_id,
            )
            / stage_name
        )
    stage_name = PREVIOUS_STAGE_DIR.get(role_id)
    if stage_name is None:
        raise ValueError(f"Role does not have previous stage doc: {role_id}")
    return _latest_stage_doc(
        stage_docs_dir(
            workspace_root=workspace_root,
            session_id=session_id,
            role_id=role_id,
        )
        / stage_name
    )


def write_stage_doc_once(
    *,
    workspace: WorkspaceHandle | None = None,
    session_id: str,
    role_id: str,
    content: str,
    workspace_root: Path | None = None,
) -> Path:
    stage_name = STAGE_ROLE_TO_DIR.get(role_id)
    if stage_name is None:
        raise ValueError(f"Role does not have stage doc: {role_id}")
    target_dir = (
        stage_docs_dir(
            workspace=workspace,
            session_id=session_id,
            role_id=role_id,
            workspace_root=workspace_root,
        )
        / stage_name
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = target_dir / f"{timestamp}.md"
    if workspace is not None:
        path.write_text(content, encoding="utf-8")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _latest_stage_doc(stage_dir: Path) -> Path:
    candidates = sorted(stage_dir.glob("*.md")) if stage_dir.exists() else []
    if not candidates:
        return stage_dir / "missing.md"
    return candidates[-1]
