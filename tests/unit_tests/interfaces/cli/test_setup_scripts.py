# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


def test_windows_setup_installs_project_entry_points() -> None:
    script = Path("setup.bat").read_text(encoding="utf-8")

    assert 'set "PYTHON_CMD=py -3"' in script
    assert "%PYTHON_CMD% -m pip install uv" in script
    assert (
        "%PYTHON_CMD% -m uv sync --all-extras --index-strategy unsafe-best-match"
        in script
    )
    assert "%PYTHON_CMD% -m uv pip install -e ." in script


def test_posix_setup_installs_project_entry_points() -> None:
    script = Path("setup.sh").read_text(encoding="utf-8")

    assert 'PYTHON_BIN="python3"' in script
    assert '"$PYTHON_BIN" -m pip install uv' in script
    assert (
        '"$PYTHON_BIN" -m uv sync --all-extras --index-strategy unsafe-best-match'
        in script
    )
    assert '"$PYTHON_BIN" -m uv pip install -e .' in script
