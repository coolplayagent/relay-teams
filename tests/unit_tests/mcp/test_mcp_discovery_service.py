# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio

import pytest

from relay_teams.mcp.mcp_discovery_service import McpDiscoveryService
from relay_teams.mcp.mcp_models import (
    McpConfigScope,
    McpDiscoveryStatus,
    McpServerSpec,
    McpToolInfo,
)
from relay_teams.mcp.mcp_registry import McpRegistry


def _spec(name: str, *, enabled: bool = True, command: str = "npx") -> McpServerSpec:
    return McpServerSpec(
        name=name,
        config={"mcpServers": {name: {"command": command}}},
        server_config={"command": command},
        source=McpConfigScope.APP,
        enabled=enabled,
    )


@pytest.mark.asyncio
async def test_start_warmup_does_not_block(monkeypatch) -> None:
    registry = McpRegistry((_spec("filesystem"),))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def fake_list_tools(name: str) -> tuple[McpToolInfo, ...]:
        assert name == "filesystem"
        entered.set()
        await release.wait()
        return (McpToolInfo(name="filesystem_read_file", description="Read"),)

    monkeypatch.setattr(registry, "list_tools_for_discovery", fake_list_tools)
    service = McpDiscoveryService(registry)

    service.start_warmup(registry)

    summary = service.get_tools_summary("filesystem")
    assert summary.status == McpDiscoveryStatus.LOADING
    await asyncio.wait_for(entered.wait(), timeout=1)
    release.set()
    await asyncio.sleep(0)
    loaded = service.get_tools_summary("filesystem")
    assert loaded.status == McpDiscoveryStatus.READY
    assert [tool.name for tool in loaded.tools] == ["filesystem_read_file"]


@pytest.mark.asyncio
async def test_discovery_failure_is_cached(monkeypatch) -> None:
    registry = McpRegistry((_spec("broken"),))

    async def fake_list_tools(name: str) -> tuple[McpToolInfo, ...]:
        assert name == "broken"
        raise RuntimeError("connection failed")

    monkeypatch.setattr(registry, "list_tools_for_discovery", fake_list_tools)
    service = McpDiscoveryService(registry)

    service.start_warmup(registry)
    await asyncio.sleep(0)

    summary = service.get_tools_summary("broken")
    assert summary.status == McpDiscoveryStatus.FAILED
    assert summary.tools == ()
    assert summary.error == "RuntimeError: connection failed"
    assert registry.is_server_runtime_failed("broken") is False


@pytest.mark.asyncio
async def test_disabled_server_is_not_enqueued(monkeypatch) -> None:
    registry = McpRegistry((_spec("disabled-docs", enabled=False),))

    async def fake_list_tools(name: str) -> tuple[McpToolInfo, ...]:
        _ = name
        raise AssertionError("Disabled MCP servers should not be discovered")

    monkeypatch.setattr(registry, "list_tools_for_discovery", fake_list_tools)
    service = McpDiscoveryService(registry)

    service.start_warmup(registry)
    await asyncio.sleep(0)

    summary = service.get_tools_summary("disabled-docs")
    assert summary.status == McpDiscoveryStatus.DISABLED


@pytest.mark.asyncio
async def test_replaced_registry_ignores_old_task_result(monkeypatch) -> None:
    old_registry = McpRegistry((_spec("docs"),))
    new_registry = McpRegistry((_spec("docs", command="uvx"),))
    release_old = asyncio.Event()

    async def old_list_tools(name: str) -> tuple[McpToolInfo, ...]:
        assert name == "docs"
        await release_old.wait()
        return (McpToolInfo(name="old_tool", description="Old"),)

    async def new_list_tools(name: str) -> tuple[McpToolInfo, ...]:
        assert name == "docs"
        return (McpToolInfo(name="new_tool", description="New"),)

    monkeypatch.setattr(old_registry, "list_tools_for_discovery", old_list_tools)
    monkeypatch.setattr(new_registry, "list_tools_for_discovery", new_list_tools)
    service = McpDiscoveryService(old_registry)

    service.start_warmup(old_registry)
    service.replace_registry(new_registry)
    release_old.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    summary = service.get_tools_summary("docs")
    assert summary.status == McpDiscoveryStatus.READY
    assert [tool.name for tool in summary.tools] == ["new_tool"]


@pytest.mark.asyncio
async def test_unchanged_ready_server_is_not_discovered_again(monkeypatch) -> None:
    registry = McpRegistry((_spec("docs"),))
    calls = 0

    async def fake_list_tools(name: str) -> tuple[McpToolInfo, ...]:
        nonlocal calls
        assert name == "docs"
        calls += 1
        return (McpToolInfo(name="docs_search", description="Search"),)

    monkeypatch.setattr(registry, "list_tools_for_discovery", fake_list_tools)
    service = McpDiscoveryService(registry)

    service.start_warmup(registry)
    await asyncio.sleep(0)
    assert service.get_tools_summary("docs").status == McpDiscoveryStatus.READY

    service.replace_registry(McpRegistry((_spec("docs"),)))
    await asyncio.sleep(0)

    summary = service.get_tools_summary("docs")
    assert summary.status == McpDiscoveryStatus.READY
    assert [tool.name for tool in summary.tools] == ["docs_search"]
    assert calls == 1


@pytest.mark.asyncio
async def test_env_fingerprint_change_rediscovers_ready_server(monkeypatch) -> None:
    first_registry = McpRegistry(
        (_spec("docs"),),
        discovery_env_fingerprint="env-v1",
    )
    second_registry = McpRegistry(
        (_spec("docs"),),
        discovery_env_fingerprint="env-v2",
    )
    calls: list[str] = []

    async def first_list_tools(name: str) -> tuple[McpToolInfo, ...]:
        assert name == "docs"
        calls.append("first")
        return (McpToolInfo(name="old_tool", description="Old"),)

    async def second_list_tools(name: str) -> tuple[McpToolInfo, ...]:
        assert name == "docs"
        calls.append("second")
        return (McpToolInfo(name="new_tool", description="New"),)

    monkeypatch.setattr(first_registry, "list_tools_for_discovery", first_list_tools)
    monkeypatch.setattr(second_registry, "list_tools_for_discovery", second_list_tools)
    service = McpDiscoveryService(first_registry)

    service.start_warmup(first_registry)
    await asyncio.sleep(0)
    assert service.get_tools_summary("docs").status == McpDiscoveryStatus.READY

    service.replace_registry(second_registry)
    await asyncio.sleep(0)

    summary = service.get_tools_summary("docs")
    assert summary.status == McpDiscoveryStatus.READY
    assert [tool.name for tool in summary.tools] == ["new_tool"]
    assert calls == ["first", "second"]


@pytest.mark.asyncio
async def test_unchanged_failed_server_is_not_retried_on_reload(monkeypatch) -> None:
    registry = McpRegistry((_spec("broken"),))
    calls = 0

    async def fake_list_tools(name: str) -> tuple[McpToolInfo, ...]:
        nonlocal calls
        assert name == "broken"
        calls += 1
        raise RuntimeError("offline")

    monkeypatch.setattr(registry, "list_tools_for_discovery", fake_list_tools)
    service = McpDiscoveryService(registry)

    service.start_warmup(registry)
    await asyncio.sleep(0)
    assert service.get_tools_summary("broken").status == McpDiscoveryStatus.FAILED

    service.replace_registry(McpRegistry((_spec("broken"),)))
    await asyncio.sleep(0)

    summary = service.get_tools_summary("broken")
    assert summary.status == McpDiscoveryStatus.FAILED
    assert summary.error == "RuntimeError: offline"
    assert calls == 1


@pytest.mark.asyncio
async def test_successful_discovery_clears_runtime_failed_state(monkeypatch) -> None:
    registry = McpRegistry((_spec("docs"),))
    registry.mark_server_runtime_failed("docs")

    async def fake_list_tools(name: str) -> tuple[McpToolInfo, ...]:
        assert name == "docs"
        return (McpToolInfo(name="docs_search", description="Search"),)

    monkeypatch.setattr(registry, "list_tools_for_discovery", fake_list_tools)
    service = McpDiscoveryService(registry)

    service.start_warmup(registry)
    await asyncio.sleep(0)

    summary = service.get_tools_summary("docs")
    assert summary.status == McpDiscoveryStatus.READY
    assert registry.is_server_runtime_failed("docs") is False


@pytest.mark.asyncio
async def test_mark_ready_cancels_stale_discovery_result(monkeypatch) -> None:
    registry = McpRegistry((_spec("docs"),))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def fake_list_tools(name: str) -> tuple[McpToolInfo, ...]:
        assert name == "docs"
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        raise RuntimeError("late failure")

    monkeypatch.setattr(registry, "list_tools_for_discovery", fake_list_tools)
    service = McpDiscoveryService(registry)

    service.start_warmup(registry)
    await asyncio.wait_for(entered.wait(), timeout=1)
    service.mark_ready(
        "docs",
        (McpToolInfo(name="docs_search", description="Search"),),
    )
    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    summary = service.get_tools_summary("docs")
    assert summary.status == McpDiscoveryStatus.READY
    assert summary.error is None
    assert [tool.name for tool in summary.tools] == ["docs_search"]


def test_malformed_server_config_is_reported_as_unknown_transport() -> None:
    registry = McpRegistry(
        (
            McpServerSpec(
                name="dirty",
                config={"mcpServers": {"dirty": {}}},
                server_config={},
                source=McpConfigScope.APP,
            ),
        )
    )

    service = McpDiscoveryService(registry)

    summary = service.get_tools_summary("dirty")
    assert summary.transport == "unknown"
    assert summary.status == McpDiscoveryStatus.PENDING


@pytest.mark.asyncio
async def test_changed_server_is_discovered_again(monkeypatch) -> None:
    first_registry = McpRegistry((_spec("docs"),))
    second_registry = McpRegistry((_spec("docs", command="uvx"),))

    async def first_list_tools(name: str) -> tuple[McpToolInfo, ...]:
        assert name == "docs"
        return (McpToolInfo(name="old_tool", description="Old"),)

    async def second_list_tools(name: str) -> tuple[McpToolInfo, ...]:
        assert name == "docs"
        return (McpToolInfo(name="new_tool", description="New"),)

    monkeypatch.setattr(first_registry, "list_tools_for_discovery", first_list_tools)
    monkeypatch.setattr(second_registry, "list_tools_for_discovery", second_list_tools)
    service = McpDiscoveryService(first_registry)

    service.start_warmup(first_registry)
    await asyncio.sleep(0)
    assert [tool.name for tool in service.get_tools_summary("docs").tools] == [
        "old_tool"
    ]

    service.replace_registry(second_registry)
    await asyncio.sleep(0)

    summary = service.get_tools_summary("docs")
    assert summary.status == McpDiscoveryStatus.READY
    assert [tool.name for tool in summary.tools] == ["new_tool"]


@pytest.mark.asyncio
async def test_replace_registry_from_worker_thread_enqueues_on_bound_loop(
    monkeypatch,
) -> None:
    first_registry = McpRegistry(())
    second_registry = McpRegistry((_spec("docs"),))
    discovered = asyncio.Event()

    async def second_list_tools(name: str) -> tuple[McpToolInfo, ...]:
        assert name == "docs"
        discovered.set()
        return (McpToolInfo(name="docs_search", description="Search"),)

    monkeypatch.setattr(second_registry, "list_tools_for_discovery", second_list_tools)
    service = McpDiscoveryService(first_registry)
    service.start_warmup(first_registry)

    await asyncio.to_thread(service.replace_registry, second_registry)
    await asyncio.wait_for(discovered.wait(), timeout=1)

    summary = service.get_tools_summary("docs")
    assert summary.status == McpDiscoveryStatus.READY
    assert [tool.name for tool in summary.tools] == ["docs_search"]
