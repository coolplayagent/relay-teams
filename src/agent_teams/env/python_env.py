# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import shutil
import sys

AGENT_TEAMS_PYTHON_EXECUTABLE_ENV_KEY = "AGENT_TEAMS_PYTHON_EXECUTABLE"


def bind_subprocess_python_env(env: Mapping[str, str]) -> dict[str, str]:
    bound_env = dict(env)
    python_executable = resolve_subprocess_python_executable(bound_env)
    bound_env[AGENT_TEAMS_PYTHON_EXECUTABLE_ENV_KEY] = str(python_executable)
    bound_env["PATH"] = _prepend_to_path(
        bound_env.get("PATH"), python_executable.parent
    )
    return bound_env


def resolve_subprocess_python_executable(env: Mapping[str, str]) -> Path:
    search_path = env.get("PATH")
    resolved_python = (
        None if search_path is None else shutil.which("python", path=search_path)
    )
    if resolved_python:
        return Path(resolved_python).expanduser().resolve()
    return Path(sys.executable).expanduser().resolve()


def _prepend_to_path(existing_path: str | None, directory: Path) -> str:
    directory_str = str(directory)
    if existing_path:
        path_parts = existing_path.split(os.pathsep)
        if path_parts and _normalize_path_entry(path_parts[0]) == _normalize_path_entry(
            directory_str
        ):
            return existing_path
        return os.pathsep.join((directory_str, existing_path))
    return directory_str


def _normalize_path_entry(path_entry: str) -> str:
    normalized = path_entry.strip().strip('"')
    if not normalized:
        return ""
    return os.path.normcase(os.path.normpath(str(Path(normalized).expanduser())))
