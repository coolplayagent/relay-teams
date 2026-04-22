# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic_ai import Agent

from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.agents.instances.models import (
    RuntimeToolSnapshotEntry,
    RuntimeToolsSnapshot,
)
from relay_teams.tools.discovery_tools import activate_tools as activate_tools_module
from relay_teams.tools.discovery_tools import register_activate_tools
from relay_teams.tools.runtime import ToolDeps


class _FakeAgent:
    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., object]] = {}
        self.tool_descriptions: dict[str, str] = {}

    def tool(
        self, *, description: str
    ) -> Callable[[Callable[..., object]], Callable[..., object]]:
        def decorator(func: Callable[..., object]) -> Callable[..., object]:
            self.tools[func.__name__] = func
            self.tool_descriptions[func.__name__] = description
            return func

        return decorator


def _invoke_tool_action(
    action: Callable[..., object],
    raw_args: dict[str, object] | None = None,
) -> object:
    if raw_args is None:
        return action()
    signature = inspect.signature(action)
    bound_args = {
        name: raw_args[name]
        for name in signature.parameters
        if name in raw_args and name != "ctx"
    }
    return action(**bound_args)


def _seed_runtime_snapshot(
    *,
    agent_repo: AgentInstanceRepository,
    instance_id: str,
    runtime_tools: RuntimeToolsSnapshot,
    runtime_active_tools_json: str = "",
) -> None:
    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="trace-1",
        session_id="session-1",
        instance_id=instance_id,
        role_id="reader",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        status=InstanceStatus.IDLE,
    )
    agent_repo.update_runtime_snapshot(
        instance_id,
        runtime_system_prompt="prompt",
        runtime_tools_json=json.dumps(runtime_tools.model_dump(mode="json")),
        runtime_active_tools_json=runtime_active_tools_json,
    )


@pytest.mark.asyncio
async def test_activate_tools_persists_new_local_tool_activation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_activate_tools(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["activate_tools"],
    )
    agent_repo = AgentInstanceRepository(tmp_path / "instances.db")
    _seed_runtime_snapshot(
        agent_repo=agent_repo,
        instance_id="instance-1",
        runtime_tools=RuntimeToolsSnapshot(
            local_tools=(
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="activate_tools",
                    description="Activate tools.",
                ),
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="tool_search",
                    description="Discover tools.",
                ),
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="read",
                    description="Read files.",
                ),
            ),
        ),
        runtime_active_tools_json='["tool_search","activate_tools"]',
    )
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            agent_repo=agent_repo,
            instance_id="instance-1",
        )
    )

    async def _fake_execute_tool_call(ctx, **kwargs: object) -> dict[str, object]:
        del ctx
        return cast(
            dict[str, object],
            _invoke_tool_action(
                cast(Callable[..., object], kwargs["action"]),
                cast(dict[str, object], kwargs.get("raw_args")),
            ),
        )

    monkeypatch.setattr(
        activate_tools_module,
        "execute_tool_call",
        _fake_execute_tool_call,
    )

    result = await tool(ctx, tool_names=["read"])

    assert result["activated"] == ["read"]
    assert result["already_active"] == []
    assert result["unknown_or_unauthorized"] == []
    assert result["active_tools"] == ["tool_search", "activate_tools", "read"]
    persisted = agent_repo.get_instance("instance-1")
    assert json.loads(persisted.runtime_active_tools_json) == [
        "tool_search",
        "activate_tools",
        "read",
    ]


@pytest.mark.asyncio
async def test_activate_tools_reports_unknown_requested_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_activate_tools(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["activate_tools"],
    )
    agent_repo = AgentInstanceRepository(tmp_path / "instances.db")
    _seed_runtime_snapshot(
        agent_repo=agent_repo,
        instance_id="instance-1",
        runtime_tools=RuntimeToolsSnapshot(
            local_tools=(
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="activate_tools",
                    description="Activate tools.",
                ),
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="tool_search",
                    description="Discover tools.",
                ),
            ),
        ),
        runtime_active_tools_json='["tool_search","activate_tools"]',
    )
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            agent_repo=agent_repo,
            instance_id="instance-1",
        )
    )

    async def _fake_execute_tool_call(ctx, **kwargs: object) -> dict[str, object]:
        del ctx
        return cast(
            dict[str, object],
            _invoke_tool_action(
                cast(Callable[..., object], kwargs["action"]),
                cast(dict[str, object], kwargs.get("raw_args")),
            ),
        )

    monkeypatch.setattr(
        activate_tools_module,
        "execute_tool_call",
        _fake_execute_tool_call,
    )

    result = await tool(ctx, tool_names=["read", "missing_tool"])

    assert result["activated"] == []
    assert result["unknown_or_unauthorized"] == ["read", "missing_tool"]
    assert "not authorized local tools" in str(result["warning"])


@pytest.mark.asyncio
async def test_activate_tools_accepts_single_string_tool_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_activate_tools(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["activate_tools"],
    )
    agent_repo = AgentInstanceRepository(tmp_path / "instances.db")
    _seed_runtime_snapshot(
        agent_repo=agent_repo,
        instance_id="instance-1",
        runtime_tools=RuntimeToolsSnapshot(
            local_tools=(
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="activate_tools",
                    description="Activate tools.",
                ),
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="tool_search",
                    description="Discover tools.",
                ),
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="read",
                    description="Read files.",
                ),
            ),
        ),
        runtime_active_tools_json='["tool_search","activate_tools"]',
    )
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            agent_repo=agent_repo,
            instance_id="instance-1",
        )
    )

    async def _fake_execute_tool_call(ctx, **kwargs: object) -> dict[str, object]:
        del ctx
        return cast(
            dict[str, object],
            _invoke_tool_action(
                cast(Callable[..., object], kwargs["action"]),
                cast(dict[str, object], kwargs.get("raw_args")),
            ),
        )

    monkeypatch.setattr(
        activate_tools_module,
        "execute_tool_call",
        _fake_execute_tool_call,
    )

    result = await tool(ctx, tool_names="read")

    assert result["activated"] == ["read"]
    assert result["unknown_or_unauthorized"] == []
    assert result["active_tools"] == ["tool_search", "activate_tools", "read"]
