# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from agent_teams.tools.workspace_tools.path_utils import resolve_workspace_path


def test_resolve_workspace_path_returns_path_within_workspace(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "src" / "app.py"
    nested.parent.mkdir(parents=True)
    nested.write_text("print('ok')", encoding="utf-8")

    resolved = resolve_workspace_path(tmp_path, "src/app.py")

    assert resolved == nested.resolve()


def test_resolve_workspace_path_allows_workspace_root(tmp_path: Path) -> None:
    resolved = resolve_workspace_path(tmp_path, ".")

    assert resolved == tmp_path.resolve()


def test_resolve_workspace_path_rejects_escape_outside_workspace(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="Path is outside workspace"):
        resolve_workspace_path(tmp_path, "../outside.txt")
