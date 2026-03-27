# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from setuptools import setup
from setuptools.command.build_py import build_py


PROJECT_ROOT = Path(__file__).resolve().parent


def _load_frontend_packaging_module() -> ModuleType:
    module_path = (
        PROJECT_ROOT / "src" / "agent_teams" / "release" / "frontend_packaging.py"
    )
    spec = importlib.util.spec_from_file_location(
        "agent_teams_release_frontend_packaging",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Unable to load frontend packaging helper from {module_path}"
        )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_frontend_packaging = _load_frontend_packaging_module()


class BuildPyWithFrontend(build_py):
    def run(self) -> None:
        super().run()
        target_dir = _frontend_packaging.copy_frontend_dist(
            project_root=PROJECT_ROOT,
            build_lib=Path(self.build_lib).resolve(),
        )
        self.announce(
            f"copied frontend build artifacts to {target_dir}",
            level=2,
        )


setup(cmdclass={"build_py": BuildPyWithFrontend})
