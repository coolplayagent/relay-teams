# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_teams.tools.stage_tools import stage_documents as stage_docs_module
from agent_teams.tools.stage_tools.stage_documents import (
    current_stage_doc_path,
    stage_docs_dir,
    previous_stage_doc_path,
    write_stage_doc_once,
)


def test_stage_doc_paths(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    session_id = "session-1"
    role_id = "spec_design"

    previous_dir = (
        stage_docs_dir(
            workspace_root=root,
            session_id=session_id,
            role_id=role_id,
        )
        / "spec"
    )
    previous_dir.mkdir(parents=True, exist_ok=True)
    spec_path = previous_dir / "20260314T000000000000Z.md"
    spec_path.write_text("spec content", encoding="utf-8")

    design_path = write_stage_doc_once(
        workspace_root=root,
        session_id=session_id,
        role_id=role_id,
        content="design content",
    )

    assert spec_path.parent == (
        stage_docs_dir(
            workspace_root=root,
            session_id=session_id,
            role_id=role_id,
        )
        / "spec"
    )
    assert (
        current_stage_doc_path(
            workspace_root=root,
            session_id=session_id,
            role_id=role_id,
        )
        == design_path
    )
    assert (
        previous_stage_doc_path(
            workspace_root=root,
            session_id=session_id,
            role_id=role_id,
        )
        == spec_path
    )


def test_write_stage_doc_once_keeps_history(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    fixed_times = iter(
        (
            datetime(2026, 3, 14, 0, 0, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 14, 0, 0, 0, 1, tzinfo=timezone.utc),
        )
    )

    class _FakeDateTime:
        @staticmethod
        def now(*, tz: timezone | None = None) -> datetime:
            value = next(fixed_times)
            return value if tz is None else value.astimezone(tz)

    original_datetime = stage_docs_module.datetime
    stage_docs_module.datetime = _FakeDateTime
    try:
        first = write_stage_doc_once(
            workspace_root=root,
            session_id="session-1",
            role_id="spec_spec",
            content="v1",
        )
        second = write_stage_doc_once(
            workspace_root=root,
            session_id="session-1",
            role_id="spec_spec",
            content="v2",
        )
    finally:
        stage_docs_module.datetime = original_datetime

    assert first != second
    assert first.read_text(encoding="utf-8") == "v1"
    assert second.read_text(encoding="utf-8") == "v2"
    assert len(list(first.parent.glob("*.md"))) == 2
