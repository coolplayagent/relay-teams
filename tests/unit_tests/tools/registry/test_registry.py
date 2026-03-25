# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from agent_teams.tools.registry import ToolRegistry, ToolResolutionContext


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


def test_registry_list_configurable_names_omits_hidden_tools() -> None:
    registry = ToolRegistry(
        {
            "alpha": _register_alpha,
            "beta": _register_beta,
        },
        hidden_from_config=("beta",),
    )

    assert registry.list_names() == ("alpha", "beta")
    assert registry.list_configurable_names() == ("alpha",)


class _ImplicitResolver:
    def resolve_implicit_tools(
        self,
        context: ToolResolutionContext,
    ) -> tuple[str, ...]:
        if context.session_id == "session-1":
            return ("alpha", "beta")
        return ()


def test_registry_require_appends_implicit_tools_from_context() -> None:
    registry = ToolRegistry(
        {
            "alpha": _register_alpha,
            "beta": _register_beta,
        }
    )
    registry.register_implicit_resolver(_ImplicitResolver())

    resolved = registry.require(
        ("beta",),
        context=ToolResolutionContext(session_id="session-1"),
    )

    assert resolved == (_register_beta, _register_alpha)
