# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


def test_windows_setup_installs_project_entry_points() -> None:
    script = Path("setup.bat").read_text(encoding="utf-8")

    assert 'set "PYTHON_CMD=py -3"' in script
    assert 'if "%PYTHON_CMD%"=="" python --version >nul 2>&1' in script
    assert (
        'if %errorlevel% equ 0 if "%PYTHON_CMD%"=="" set "PYTHON_CMD=python"' in script
    )
    assert 'set "UV_CMD="' in script
    assert 'if "%UV_CMD%"=="" uv --version >nul 2>&1' in script
    assert 'if %errorlevel% equ 0 if "%UV_CMD%"=="" set "UV_CMD=uv"' in script
    assert "%PYTHON_CMD% -m pip install uv" in script
    assert "%UV_CMD% sync --all-extras --index-strategy unsafe-best-match" in script
    assert "%UV_CMD% pip install -e ." in script


def test_posix_setup_installs_project_entry_points() -> None:
    script = Path("setup.sh").read_text(encoding="utf-8")

    assert 'PYTHON_BIN="python3"' in script
    assert 'UV_MODE=""' in script
    assert "elif command -v uv >/dev/null 2>&1; then" in script
    assert "run_uv() {" in script
    assert '"$PYTHON_BIN" -m pip install uv' in script
    assert "run_uv sync --all-extras --index-strategy unsafe-best-match" in script
    assert "run_uv pip install -e ." in script
