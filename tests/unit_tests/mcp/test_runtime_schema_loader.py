# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio

import pytest

from relay_teams.mcp.mcp_models import McpToolSchema
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.mcp.runtime_schema_loader import (
    RuntimeMcpSchemaLoader,
    RuntimeMcpSchemaStatus,
)


class _ConfigurableSchemaRegistry(McpRegistry):
    def __init__(self, *, tool_suffix: str = "search") -> None:
        super().__init__(())
        self.calls: dict[str, int] = {}
        self.release: asyncio.Event | None = None
        self.fail_names: set[str] = set()
        self.client_error_names: set[str] = set()
        self.sleep_seconds = 0.0
        self.tool_suffix = tool_suffix

    async def list_tool_schemas(self, name: str) -> tuple[McpToolSchema, ...]:
        self.calls[name] = self.calls.get(name, 0) + 1
        if self.release is not None:
            await self.release.wait()
        if self.sleep_seconds > 0:
            await asyncio.sleep(self.sleep_seconds)
        if name in self.client_error_names:
            raise _ClientTransportError(f"{name} transport unavailable")
        if name in self.fail_names:
            raise RuntimeError(f"{name} offline")
        return (
            McpToolSchema(
                name=f"{name}_{self.tool_suffix}",
                description="Search",
                input_schema={"type": "object"},
            ),
        )


class _ClientTransportError(Exception):
    pass


@pytest.mark.asyncio
async def test_cache_hit_does_not_call_registry_again() -> None:
    registry = _ConfigurableSchemaRegistry()
    loader = RuntimeMcpSchemaLoader(registry, cache_ttl_seconds=60.0)

    first = await loader.load_server("docs")
    second = await loader.load_server("docs")

    assert first.status == RuntimeMcpSchemaStatus.LOADED
    assert second.status == RuntimeMcpSchemaStatus.CACHE_HIT
    assert registry.calls == {"docs": 1}


@pytest.mark.asyncio
async def test_concurrent_same_server_load_shares_in_flight_probe() -> None:
    registry = _ConfigurableSchemaRegistry()
    registry.release = asyncio.Event()
    loader = RuntimeMcpSchemaLoader(registry, cache_ttl_seconds=60.0)

    first_task = asyncio.create_task(loader.load_server("docs"))
    second_task = asyncio.create_task(loader.load_server("docs"))
    await asyncio.sleep(0)
    registry.release.set()

    first, second = await asyncio.gather(first_task, second_task)

    assert first.ok is True
    assert second.ok is True
    assert registry.calls == {"docs": 1}


@pytest.mark.asyncio
async def test_timeout_enters_server_failure_ttl_and_fast_fails() -> None:
    registry = _ConfigurableSchemaRegistry()
    registry.sleep_seconds = 1.0
    loader = RuntimeMcpSchemaLoader(
        registry,
        failure_ttl_seconds=60.0,
        server_timeout_seconds=0.01,
    )

    first = await loader.load_server("slow")
    registry.sleep_seconds = 0.0
    second = await loader.load_server("slow")

    assert first.status == RuntimeMcpSchemaStatus.FAILED
    assert second.status == RuntimeMcpSchemaStatus.SERVER_COOLDOWN
    assert "slow" in (second.error or "")
    assert "cooldown" in (second.error or "")
    assert registry.calls == {"slow": 1}


@pytest.mark.asyncio
async def test_consecutive_failures_trigger_global_cooldown() -> None:
    registry = _ConfigurableSchemaRegistry()
    registry.fail_names.update({"one", "two"})
    loader = RuntimeMcpSchemaLoader(
        registry,
        global_failure_threshold=2,
        global_cooldown_seconds=60.0,
    )

    _ = await loader.load_many(("one", "two"))
    skipped = await loader.load_server("three")

    assert skipped.status == RuntimeMcpSchemaStatus.GLOBAL_COOLDOWN
    assert "three" in (skipped.error or "")
    assert "global cooldown" in (skipped.error or "")
    assert registry.calls == {"one": 1, "two": 1}


@pytest.mark.asyncio
async def test_queued_loads_honor_global_cooldown_after_semaphore_wait() -> None:
    registry = _ConfigurableSchemaRegistry()
    registry.fail_names.update({"one", "two"})
    loader = RuntimeMcpSchemaLoader(
        registry,
        max_concurrency=1,
        global_failure_threshold=1,
        global_cooldown_seconds=60.0,
    )

    result = await loader.load_many(("one", "two"))

    statuses = {entry.server_name: entry.status for entry in result.results}
    assert statuses == {
        "one": RuntimeMcpSchemaStatus.FAILED,
        "two": RuntimeMcpSchemaStatus.GLOBAL_COOLDOWN,
    }
    assert registry.calls == {"one": 1}


@pytest.mark.asyncio
async def test_server_recovers_after_failure_ttl() -> None:
    registry = _ConfigurableSchemaRegistry()
    registry.fail_names.add("docs")
    loader = RuntimeMcpSchemaLoader(registry, failure_ttl_seconds=0.01)

    first = await loader.load_server("docs")
    await asyncio.sleep(0.02)
    registry.fail_names.clear()
    second = await loader.load_server("docs")

    assert first.status == RuntimeMcpSchemaStatus.FAILED
    assert second.status == RuntimeMcpSchemaStatus.LOADED
    assert [schema.name for schema in second.schemas] == ["docs_search"]
    assert registry.calls == {"docs": 2}


@pytest.mark.asyncio
async def test_replace_registry_clears_failure_state() -> None:
    broken_registry = _ConfigurableSchemaRegistry()
    broken_registry.fail_names.add("docs")
    loader = RuntimeMcpSchemaLoader(broken_registry, failure_ttl_seconds=60.0)
    first = await loader.load_server("docs")

    recovered_registry = _ConfigurableSchemaRegistry()
    loader.replace_registry(recovered_registry)
    second = await loader.load_server("docs")

    assert first.status == RuntimeMcpSchemaStatus.FAILED
    assert second.status == RuntimeMcpSchemaStatus.LOADED
    assert broken_registry.calls == {"docs": 1}
    assert recovered_registry.calls == {"docs": 1}


@pytest.mark.asyncio
async def test_client_transport_error_returns_failed_result() -> None:
    registry = _ConfigurableSchemaRegistry()
    registry.client_error_names.add("remote")
    loader = RuntimeMcpSchemaLoader(registry)

    result = await loader.load_many(("remote",))

    assert result.skipped_count == 1
    assert result.results[0].status == RuntimeMcpSchemaStatus.FAILED
    assert "remote transport unavailable" in (result.results[0].error or "")
    assert registry.calls == {"remote": 1}


@pytest.mark.asyncio
async def test_replace_registry_discards_stale_in_flight_result() -> None:
    stale_registry = _ConfigurableSchemaRegistry(tool_suffix="stale")
    stale_registry.release = asyncio.Event()
    loader = RuntimeMcpSchemaLoader(stale_registry, cache_ttl_seconds=60.0)

    stale_task = asyncio.create_task(loader.load_server("docs"))
    await asyncio.sleep(0)
    fresh_registry = _ConfigurableSchemaRegistry(tool_suffix="fresh")
    loader.replace_registry(fresh_registry)
    stale_registry.release.set()

    stale_result = await stale_task
    fresh_result = await loader.load_server("docs")
    cached_result = await loader.load_server("docs")

    assert stale_result.status == RuntimeMcpSchemaStatus.FAILED
    assert "registry was replaced" in (stale_result.error or "")
    assert [schema.name for schema in fresh_result.schemas] == ["docs_fresh"]
    assert [schema.name for schema in cached_result.schemas] == ["docs_fresh"]
    assert stale_registry.calls == {"docs": 1}
    assert fresh_registry.calls == {"docs": 1}
