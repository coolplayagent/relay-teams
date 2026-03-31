# -*- coding: utf-8 -*-
from __future__ import annotations

import builtins
import runpy
from pathlib import Path
from typing import Callable, cast

import pytest


def test_root_package_defers_missing_external_sdk_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def fake_import(
        name: str,
        globals_dict: dict[str, object] | None = None,
        locals_dict: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "agent_teams.interfaces.sdk.client":
            raise ModuleNotFoundError("No module named 'pydantic'", name="pydantic")
        return original_import(name, globals_dict, locals_dict, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    namespace = runpy.run_path(
        str(Path("src") / "agent_teams" / "__init__.py"),
        init_globals={"__name__": "agent_teams"},
    )
    package_getattr = cast(Callable[[str], object], namespace["__getattr__"])

    with pytest.raises(ModuleNotFoundError, match="SDK dependencies"):
        _ = package_getattr("AgentTeamsClient")


def test_root_package_no_longer_exports_legacy_agent_teams_app() -> None:
    namespace = runpy.run_path(
        str(Path("src") / "agent_teams" / "__init__.py"),
        init_globals={"__name__": "agent_teams"},
    )

    assert "AgentTeamsApp" not in cast(list[str], namespace["__all__"])
