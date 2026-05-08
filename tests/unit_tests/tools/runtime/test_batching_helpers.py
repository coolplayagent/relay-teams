# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import BaseModel

from relay_teams.tools.runtime.json_helpers import (
    normalize_json_object,
    normalize_json_value,
    safe_json,
)
from relay_teams.tools.runtime.tool_result_batching import (
    ToolResultCommitBuffer,
    ToolResultCommitItem,
    current_tool_result_commit_buffer,
    suspended_tool_result_batching,
    tool_result_batch_scope,
)
from relay_teams.tools.runtime.context import ToolContext
from relay_teams.tools.runtime.persisted_state import ToolExecutionStatus


class _JsonPayload(BaseModel):
    value: int


def test_json_helpers_normalize_models_collections_and_fallbacks() -> None:
    assert normalize_json_object("not-a-dict") == {}
    assert normalize_json_object({1: _JsonPayload(value=3)}) == {"1": {"value": 3}}
    normalized = normalize_json_value((_JsonPayload(value=1), object()))
    assert isinstance(normalized, list)
    assert normalized[0] == {"value": 1}
    assert safe_json(object())
    assert safe_json("x" * 600).endswith("...(truncated)")


@pytest.mark.asyncio
async def test_tool_result_commit_buffer_pop_and_singleflight() -> None:
    buffer = ToolResultCommitBuffer()
    item = ToolResultCommitItem(
        ctx=cast(ToolContext, SimpleNamespace()),
        tool_call_id="call-1",
        tool_name="read",
        args_summary={},
        visible_envelope={"ok": True},
        internal_data=None,
        runtime_meta={},
        execution_status=ToolExecutionStatus.COMPLETED,
        tool_content_parts=(),
        duration_ms=1,
        success=True,
    )
    await buffer.add_async(cast(ToolResultCommitItem, item))
    assert await buffer.pop_items_async() == (item,)
    assert await buffer.pop_items_async() == ()

    calls = 0

    async def factory() -> object:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return "value"

    first, second = await asyncio.gather(
        buffer.invoke_action_singleflight_async(key="shared", factory=factory),
        buffer.invoke_action_singleflight_async(key="shared", factory=factory),
    )

    assert calls == 1
    assert first.value == "value"
    assert second.value == "value"
    assert {first.shared, second.shared} == {False, True}


@pytest.mark.asyncio
async def test_allowed_tools_singleflight_and_batch_contexts() -> None:
    calls = 0
    with tool_result_batch_scope() as buffer:
        assert current_tool_result_commit_buffer() is buffer

        async def factory() -> tuple[str, ...] | None:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return ("read", "grep")

        first, second = await asyncio.gather(
            buffer.allowed_tools_for_policy_async(key="policy", factory=factory),
            buffer.allowed_tools_for_policy_async(key="policy", factory=factory),
        )
        assert first == ("read", "grep")
        assert second == ("read", "grep")
        assert calls == 1
        with suspended_tool_result_batching():
            assert current_tool_result_commit_buffer() is None
        assert current_tool_result_commit_buffer() is buffer
    assert current_tool_result_commit_buffer() is None
