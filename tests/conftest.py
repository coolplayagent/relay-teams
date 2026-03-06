# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import inspect

import pytest


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
