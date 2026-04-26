# -*- coding: utf-8 -*-
from __future__ import annotations

import builtins
from collections.abc import Callable
from typing import cast

from pydantic_ai import Agent
from pytest import MonkeyPatch

from relay_teams.tools.computer_tools import register_computer_tools
from relay_teams.tools.runtime.context import ToolDeps


class _RecordingAgent:
    def __init__(self) -> None:
        self.tool_names: list[str] = []

    def tool(
        self,
        *,
        description: str | None = None,
    ) -> Callable[[Callable[..., object]], Callable[..., object]]:
        _ = description

        def _decorator(func: Callable[..., object]) -> Callable[..., object]:
            self.tool_names.append(func.__name__)
            return func

        return _decorator


def test_computer_tool_registration_is_deduplicated_per_agent(
    monkeypatch: MonkeyPatch,
) -> None:
    first_agent = _RecordingAgent()
    second_agent = _RecordingAgent()
    original_id = builtins.id
    target_ids = {original_id(first_agent), original_id(second_agent)}

    def reused_id(value: object) -> int:
        if original_id(value) in target_ids:
            return 1
        return original_id(value)

    monkeypatch.setattr(builtins, "id", reused_id)

    register_computer_tools(cast(Agent[ToolDeps, str], cast(object, first_agent)))
    register_computer_tools(cast(Agent[ToolDeps, str], cast(object, first_agent)))
    register_computer_tools(cast(Agent[ToolDeps, str], cast(object, second_agent)))

    assert first_agent.tool_names == second_agent.tool_names
    assert first_agent.tool_names.count("capture_screen") == 1
    assert first_agent.tool_names.count("click_at") == 1
    assert second_agent.tool_names.count("capture_screen") == 1
    assert second_agent.tool_names.count("click_at") == 1
