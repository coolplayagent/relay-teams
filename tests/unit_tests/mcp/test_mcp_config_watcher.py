# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from relay_teams.mcp.mcp_config_watcher import McpConfigFileWatcher


@pytest.mark.asyncio
async def test_mcp_config_file_watcher_reloads_on_file_change(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "mcp.json"
    config_path.write_text('{"mcpServers": {}}', encoding="utf-8")
    changed = asyncio.Event()
    loop = asyncio.get_running_loop()

    def on_changed() -> None:
        loop.call_soon_threadsafe(changed.set)

    watcher = McpConfigFileWatcher(
        config_path=config_path,
        on_changed=on_changed,
        poll_interval_seconds=0.01,
        debounce_seconds=0.0,
    )

    watcher.start()
    try:
        config_path.write_text(
            '{"mcpServers": {"docs": {"command": "npx"}}}',
            encoding="utf-8",
        )
        await asyncio.wait_for(changed.wait(), timeout=1)
    finally:
        await watcher.stop()


@pytest.mark.asyncio
async def test_mcp_config_file_watcher_ignores_invalid_json(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "mcp.json"
    config_path.write_text('{"mcpServers": {}}', encoding="utf-8")
    changed = asyncio.Event()
    loop = asyncio.get_running_loop()

    def on_changed() -> None:
        loop.call_soon_threadsafe(changed.set)

    watcher = McpConfigFileWatcher(
        config_path=config_path,
        on_changed=on_changed,
        poll_interval_seconds=0.01,
        debounce_seconds=0.0,
    )

    watcher.start()
    try:
        config_path.write_text('{"mcpServers": ', encoding="utf-8")
        await asyncio.sleep(0.08)
        assert not changed.is_set()
    finally:
        await watcher.stop()


@pytest.mark.asyncio
async def test_mcp_config_file_watcher_start_and_stop_are_idempotent(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "mcp.json"
    config_path.write_text('{"mcpServers": {}}', encoding="utf-8")
    changed = asyncio.Event()

    def on_changed() -> None:
        changed.set()

    watcher = McpConfigFileWatcher(
        config_path=config_path,
        on_changed=on_changed,
        poll_interval_seconds=0.01,
        debounce_seconds=0.0,
    )

    await watcher.stop()
    watcher.start()
    watcher.start()
    await watcher.stop()

    assert not changed.is_set()


@pytest.mark.asyncio
async def test_mcp_config_file_watcher_tolerates_reload_callback_failure(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "mcp.json"
    config_path.write_text('{"mcpServers": {}}', encoding="utf-8")
    attempted = asyncio.Event()
    loop = asyncio.get_running_loop()

    def on_changed() -> None:
        loop.call_soon_threadsafe(attempted.set)
        raise RuntimeError("reload failed")

    watcher = McpConfigFileWatcher(
        config_path=config_path,
        on_changed=on_changed,
        poll_interval_seconds=0.01,
        debounce_seconds=0.0,
    )

    watcher.start()
    try:
        config_path.write_text(
            '{"mcpServers": {"docs": {"command": "npx"}}}',
            encoding="utf-8",
        )
        await asyncio.wait_for(attempted.wait(), timeout=1)
    finally:
        await watcher.stop()


def test_mcp_config_file_watcher_treats_missing_file_as_valid(
    tmp_path: Path,
) -> None:
    watcher = McpConfigFileWatcher(
        config_path=tmp_path / "mcp.json",
        on_changed=lambda: None,
    )

    assert watcher._file_is_valid_json() is True
