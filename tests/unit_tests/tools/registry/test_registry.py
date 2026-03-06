# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from agent_teams.tools.registry import ToolRegistry


def _register_alpha(_: object) -> None:
    return None


def _register_beta(_: object) -> None:
    return None


def test_registry_require_deduplicates_and_preserves_first_seen_order() -> None:
    registry = ToolRegistry(
        {
            "alpha": _register_alpha,
            "beta": _register_beta,
        }
    )

    resolved = registry.require(("beta", "alpha", "beta"))

    assert resolved == (_register_beta, _register_alpha)


def test_registry_require_raises_for_unknown_tool() -> None:
    registry = ToolRegistry({"alpha": _register_alpha})

    with pytest.raises(ValueError, match="Unknown tools"):
        registry.require(("alpha", "missing"))
