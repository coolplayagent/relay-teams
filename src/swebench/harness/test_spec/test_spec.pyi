from __future__ import annotations

from collections.abc import Mapping


class TestSpec:
    ...


def make_test_spec(instance: Mapping[str, str]) -> TestSpec: ...
