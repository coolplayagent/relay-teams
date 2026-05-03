# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from relay_teams.tools.auto_harness_tools.upgrade_tool import (
    _build_upgrade_approval_request,
    _coerce_test_cases,
    _resolve_current_role,
)


def test_build_upgrade_approval_request() -> None:
    result = _build_upgrade_approval_request(
        tool_name="my_tool", target_role_id="role-1"
    )
    assert "my_tool" in result.target_summary
    assert "role-1" in result.cache_key


def test_build_upgrade_approval_request_strips_target_role() -> None:
    result = _build_upgrade_approval_request(tool_name="t", target_role_id="  ")
    assert result.cache_key.endswith("upgrade:t:")


def test_coerce_test_cases_passes_models_through() -> None:
    from relay_teams.tools.generated_tools.models import GeneratedToolTestCase

    case = GeneratedToolTestCase(input={"k": "v"}, expected="y")
    result = _coerce_test_cases([case])
    assert result == (case,)


def test_coerce_test_cases_converts_dicts() -> None:
    result = _coerce_test_cases([{"input": {"a": 1}, "expected": "b"}])
    assert len(result) == 1
    assert result[0].input == {"a": 1}


@pytest.mark.asyncio
async def test_resolve_current_role_falls_back_to_registry() -> None:
    ctx = MagicMock()
    ctx.deps.runtime_role_resolver = None
    ctx.deps.run_id = "r1"
    ctx.deps.role_id = "role-1"
    fake_role = MagicMock()
    ctx.deps.role_registry.get = MagicMock(return_value=fake_role)
    result = await _resolve_current_role(ctx)
    assert result is fake_role


@pytest.mark.asyncio
async def test_resolve_current_role_uses_resolver() -> None:
    ctx = MagicMock()
    fake_role = MagicMock()
    resolver = MagicMock()
    resolver.get_effective_role_async = AsyncMock(return_value=fake_role)
    ctx.deps.runtime_role_resolver = resolver
    ctx.deps.run_id = "r1"
    ctx.deps.role_id = "role-1"
    result = await _resolve_current_role(ctx)
    assert result is fake_role
