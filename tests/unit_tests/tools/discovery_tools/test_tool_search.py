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
from relay_teams.tools.discovery_tools import register_tool_search
from relay_teams.tools.runtime import ToolDeps
from relay_teams.tools.discovery_tools import tool_search as tool_search_module


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
    )


@pytest.mark.asyncio
async def test_tool_search_keyword_search_returns_compact_matches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_tool_search(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["tool_search"],
    )
    agent_repo = AgentInstanceRepository(tmp_path / "instances.db")
    _seed_runtime_snapshot(
        agent_repo=agent_repo,
        instance_id="instance-1",
        runtime_tools=RuntimeToolsSnapshot(
            local_tools=(
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="tool_search",
                    description="Discover tools.",
                ),
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="read",
                    description="Read a file or directory from disk.",
                ),
            ),
            mcp_tools=(
                RuntimeToolSnapshotEntry(
                    source="mcp",
                    name="docs_search",
                    description="Search developer documentation.",
                    server_name="docs",
                ),
            ),
        ),
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
        tool_search_module,
        "execute_tool_call",
        _fake_execute_tool_call,
    )

    result = await tool(ctx, query="docs search")

    assert result["mode"] == "keyword"
    assert result["total_authorized_tools"] == 3
    matches = cast(list[dict[str, object]], result["matches"])
    assert matches[0]["name"] == "docs_search"
    assert matches[0]["server_name"] == "docs"
    assert "parameters_json_schema" not in matches[0]


@pytest.mark.asyncio
async def test_tool_search_select_includes_schema(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_tool_search(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["tool_search"],
    )
    agent_repo = AgentInstanceRepository(tmp_path / "instances.db")
    _seed_runtime_snapshot(
        agent_repo=agent_repo,
        instance_id="instance-1",
        runtime_tools=RuntimeToolsSnapshot(
            local_tools=(
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="read",
                    description="Read a file or directory from disk.",
                    parameters_json_schema={
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                        },
                    },
                ),
            ),
        ),
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
        tool_search_module,
        "execute_tool_call",
        _fake_execute_tool_call,
    )

    result = await tool(ctx, query="select:read")

    assert result["mode"] == "select"
    matches = cast(list[dict[str, object]], result["matches"])
    assert matches == [
        {
            "name": "read",
            "source": "local",
            "description": "Read a file or directory from disk.",
            "kind": "function",
            "sequential": False,
            "parameters_json_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
            },
        }
    ]


@pytest.mark.asyncio
async def test_tool_search_select_respects_max_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_tool_search(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["tool_search"],
    )
    agent_repo = AgentInstanceRepository(tmp_path / "instances.db")
    _seed_runtime_snapshot(
        agent_repo=agent_repo,
        instance_id="instance-1",
        runtime_tools=RuntimeToolsSnapshot(
            local_tools=(
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="read",
                    description="Read a file or directory from disk.",
                    parameters_json_schema={"type": "object"},
                ),
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="write",
                    description="Write full file contents.",
                    parameters_json_schema={"type": "object"},
                ),
            ),
        ),
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
        tool_search_module,
        "execute_tool_call",
        _fake_execute_tool_call,
    )

    result = await tool(ctx, query="select:read,write", max_results=1)

    assert result["mode"] == "select"
    assert (
        result["warning"]
        == "Only the first 1 matched tools were returned due to max_results."
    )
    matches = cast(list[dict[str, object]], result["matches"])
    assert [match["name"] for match in matches] == ["read"]


@pytest.mark.asyncio
async def test_tool_search_exact_name_returns_schema_without_select_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_tool_search(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["tool_search"],
    )
    agent_repo = AgentInstanceRepository(tmp_path / "instances.db")
    _seed_runtime_snapshot(
        agent_repo=agent_repo,
        instance_id="instance-1",
        runtime_tools=RuntimeToolsSnapshot(
            local_tools=(
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="tool_search",
                    description="Discover tools.",
                ),
            ),
        ),
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
        tool_search_module,
        "execute_tool_call",
        _fake_execute_tool_call,
    )

    result = await tool(ctx, query="tool_search")

    assert result["mode"] == "exact"
    matches = cast(list[dict[str, object]], result["matches"])
    assert len(matches) == 1
    assert matches[0]["name"] == "tool_search"
    assert "parameters_json_schema" in matches[0]


@pytest.mark.asyncio
async def test_tool_search_exact_name_prioritizes_exact_match_and_includes_related_matches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_tool_search(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["tool_search"],
    )
    agent_repo = AgentInstanceRepository(tmp_path / "instances.db")
    _seed_runtime_snapshot(
        agent_repo=agent_repo,
        instance_id="instance-1",
        runtime_tools=RuntimeToolsSnapshot(
            local_tools=(
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="edit",
                    description="Edit a file in the workspace.",
                    parameters_json_schema={"type": "object"},
                ),
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="notebook_edit",
                    description="Edit a notebook cell by index.",
                    parameters_json_schema={"type": "object"},
                ),
            ),
        ),
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
        tool_search_module,
        "execute_tool_call",
        _fake_execute_tool_call,
    )

    result = await tool(ctx, query="edit")

    assert result["mode"] == "exact"
    matches = cast(list[dict[str, object]], result["matches"])
    assert [match["name"] for match in matches] == ["edit", "notebook_edit"]
    assert all("parameters_json_schema" in match for match in matches)


@pytest.mark.asyncio
async def test_tool_search_keyword_search_filters_low_signal_false_positives(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_tool_search(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["tool_search"],
    )
    agent_repo = AgentInstanceRepository(tmp_path / "instances.db")
    _seed_runtime_snapshot(
        agent_repo=agent_repo,
        instance_id="instance-1",
        runtime_tools=RuntimeToolsSnapshot(
            local_tools=(
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="tool_search",
                    description="Discover authorized runtime tools.",
                ),
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="notebook_edit",
                    description="Edit a notebook cell by index.",
                ),
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="spawn_subagent",
                    description="Delegate work to another agent.",
                ),
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="webfetch",
                    description="Fetch a web page.",
                ),
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="edit",
                    description="Edit a file in the workspace.",
                ),
            ),
        ),
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
        tool_search_module,
        "execute_tool_call",
        _fake_execute_tool_call,
    )

    result = await tool(ctx, query="definitely_not_a_real_tool")

    assert result["mode"] == "keyword"
    matches = cast(list[dict[str, object]], result["matches"])
    assert matches == []


@pytest.mark.asyncio
async def test_tool_search_keyword_search_matches_compound_tool_names(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_tool_search(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["tool_search"],
    )
    agent_repo = AgentInstanceRepository(tmp_path / "instances.db")
    _seed_runtime_snapshot(
        agent_repo=agent_repo,
        instance_id="instance-1",
        runtime_tools=RuntimeToolsSnapshot(
            local_tools=(
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="webfetch",
                    description="Fetch a web page.",
                ),
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="websearch",
                    description="Search the web.",
                ),
            ),
        ),
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
        tool_search_module,
        "execute_tool_call",
        _fake_execute_tool_call,
    )

    result = await tool(ctx, query="web fetch")

    assert result["mode"] == "keyword"
    matches = cast(list[dict[str, object]], result["matches"])
    assert [match["name"] for match in matches] == ["webfetch"]


@pytest.mark.asyncio
async def test_tool_search_empty_query_reports_actual_authorized_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_agent = _FakeAgent()
    register_tool_search(cast(Agent[ToolDeps, str], fake_agent))
    tool = cast(
        Callable[..., Awaitable[dict[str, object]]],
        fake_agent.tools["tool_search"],
    )
    agent_repo = AgentInstanceRepository(tmp_path / "instances.db")
    _seed_runtime_snapshot(
        agent_repo=agent_repo,
        instance_id="instance-1",
        runtime_tools=RuntimeToolsSnapshot(
            local_tools=(
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="read",
                    description="Read a file or directory from disk.",
                ),
                RuntimeToolSnapshotEntry(
                    source="local",
                    name="tool_search",
                    description="Discover tools.",
                ),
            ),
            mcp_tools=(
                RuntimeToolSnapshotEntry(
                    source="mcp",
                    name="docs_search",
                    description="Search developer documentation.",
                    server_name="docs",
                ),
            ),
        ),
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
        tool_search_module,
        "execute_tool_call",
        _fake_execute_tool_call,
    )

    result = await tool(ctx, query="   ")

    assert result["mode"] == "keyword"
    assert result["warning"] == "Query must not be empty."
    assert result["total_authorized_tools"] == 3
    matches = cast(list[dict[str, object]], result["matches"])
    assert matches == []
