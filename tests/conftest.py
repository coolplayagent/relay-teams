# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import inspect
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

import pytest


def _ensure_installed_mcp_package() -> None:
    existing = sys.modules.get("mcp")
    if existing is not None and "site-packages" in str(
        getattr(existing, "__file__", "")
    ):
        return

    for loaded_name, loaded_module in tuple(sys.modules.items()):
        if loaded_name == "mcp" or loaded_name.startswith("mcp."):
            if "site-packages" not in str(getattr(loaded_module, "__file__", "")):
                del sys.modules[loaded_name]

    package_init = Path(
        str(importlib.metadata.distribution("mcp").locate_file("mcp/__init__.py"))
    )
    spec = spec_from_file_location(
        "mcp",
        package_init,
        submodule_search_locations=[str(package_init.parent)],
    )
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError("mcp")
    module = module_from_spec(spec)
    sys.modules["mcp"] = module
    spec.loader.exec_module(module)
    importlib.import_module("pydantic_ai.mcp")


_ensure_installed_mcp_package()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "asyncio: mark a test function to run in an asyncio event loop",
    )


def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    marker = pyfuncitem.get_closest_marker("asyncio")
    if marker is None:
        return None

    test_function = pyfuncitem.obj
    if not inspect.iscoroutinefunction(test_function):
        return None

    funcargs = pyfuncitem.funcargs
    test_args = {name: funcargs[name] for name in pyfuncitem._fixtureinfo.argnames}
    asyncio.run(test_function(**test_args))
    return True
