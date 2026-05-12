# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from pathlib import Path
import threading

import pytest

from relay_teams.env import runtime_env
from relay_teams.env.app_env_watcher import AppEnvFileStamp, AppEnvFileWatcher


def _reset_runtime_env_sync(
    monkeypatch: pytest.MonkeyPatch,
    keys: tuple[str, ...],
) -> None:
    runtime_env.PROCESS_ENV_BASELINE.clear()
    runtime_env.SYNCED_APP_ENV_KEYS.clear()
    for key in keys:
        monkeypatch.delenv(key, raising=False)


@pytest.mark.asyncio
async def test_app_env_file_watcher_reloads_on_file_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    key = "APP_ENV_WATCHED_VALUE"
    _reset_runtime_env_sync(monkeypatch, (key,))
    env_file = tmp_path / ".env"
    env_file.write_text(f"{key}=before\n", encoding="utf-8")
    changed = asyncio.Event()
    changed_events: list[frozenset[str]] = []
    loop = asyncio.get_running_loop()
    loop_thread_id = threading.get_ident()
    callback_thread_ids: list[int] = []

    def on_changed(changed_keys: frozenset[str]) -> None:
        changed_events.append(changed_keys)
        callback_thread_ids.append(threading.get_ident())
        loop.call_soon_threadsafe(changed.set)

    watcher = AppEnvFileWatcher(
        env_file_path=env_file,
        on_changed=on_changed,
        poll_interval_seconds=0.01,
        debounce_seconds=0.0,
    )

    watcher.start()
    try:
        env_file.write_text(f"{key}=after\n", encoding="utf-8")
        await asyncio.wait_for(changed.wait(), timeout=1)
    finally:
        await watcher.stop()

    assert changed_events == [frozenset((key,))]
    assert callback_thread_ids == [loop_thread_id]
    assert runtime_env.os.environ[key] == "after"


@pytest.mark.asyncio
async def test_app_env_file_watcher_reloads_on_file_creation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    key = "APP_ENV_WATCHED_CREATED"
    _reset_runtime_env_sync(monkeypatch, (key,))
    env_file = tmp_path / ".env"
    changed = asyncio.Event()
    loop = asyncio.get_running_loop()

    def on_changed(_changed_keys: frozenset[str]) -> None:
        loop.call_soon_threadsafe(changed.set)

    watcher = AppEnvFileWatcher(
        env_file_path=env_file,
        on_changed=on_changed,
        poll_interval_seconds=0.01,
        debounce_seconds=0.0,
    )

    watcher.start()
    try:
        env_file.write_text(f"{key}=created\n", encoding="utf-8")
        await asyncio.wait_for(changed.wait(), timeout=1)
    finally:
        await watcher.stop()

    assert runtime_env.os.environ[key] == "created"


@pytest.mark.asyncio
async def test_app_env_file_watcher_reloads_on_file_deletion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    key = "APP_ENV_WATCHED_DELETED"
    _reset_runtime_env_sync(monkeypatch, (key,))
    env_file = tmp_path / ".env"
    env_file.write_text(f"{key}=configured\n", encoding="utf-8")
    runtime_env.sync_app_env_to_process_env(env_file)
    changed = asyncio.Event()
    changed_events: list[frozenset[str]] = []
    loop = asyncio.get_running_loop()

    def on_changed(changed_keys: frozenset[str]) -> None:
        changed_events.append(changed_keys)
        loop.call_soon_threadsafe(changed.set)

    watcher = AppEnvFileWatcher(
        env_file_path=env_file,
        on_changed=on_changed,
        poll_interval_seconds=0.01,
        debounce_seconds=0.0,
    )

    watcher.start()
    try:
        env_file.unlink()
        await asyncio.wait_for(changed.wait(), timeout=1)
    finally:
        await watcher.stop()

    assert changed_events == [frozenset((key,))]
    assert key not in runtime_env.os.environ


@pytest.mark.asyncio
async def test_app_env_file_watcher_start_and_stop_are_idempotent(
    tmp_path: Path,
) -> None:
    changed = asyncio.Event()
    watcher = AppEnvFileWatcher(
        env_file_path=tmp_path / ".env",
        on_changed=lambda _changed_keys: changed.set(),
        poll_interval_seconds=0.01,
        debounce_seconds=0.0,
    )

    await watcher.stop()
    watcher.start()
    watcher.start()
    await watcher.stop()

    assert not changed.is_set()


@pytest.mark.asyncio
async def test_app_env_file_watcher_tolerates_reload_callback_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    key = "APP_ENV_WATCHED_CALLBACK_FAILURE"
    _reset_runtime_env_sync(monkeypatch, (key,))
    env_file = tmp_path / ".env"
    env_file.write_text(f"{key}=before\n", encoding="utf-8")
    attempted = asyncio.Event()
    succeeded = asyncio.Event()
    loop = asyncio.get_running_loop()
    attempts = 0

    def on_changed(_changed_keys: frozenset[str]) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            loop.call_soon_threadsafe(attempted.set)
            raise RuntimeError("reload failed")
        loop.call_soon_threadsafe(succeeded.set)

    watcher = AppEnvFileWatcher(
        env_file_path=env_file,
        on_changed=on_changed,
        poll_interval_seconds=0.01,
        debounce_seconds=0.0,
    )

    watcher.start()
    try:
        env_file.write_text(f"{key}=after-failure\n", encoding="utf-8")
        await asyncio.wait_for(attempted.wait(), timeout=1)
        env_file.write_text(f"{key}=after-success\n", encoding="utf-8")
        await asyncio.wait_for(succeeded.wait(), timeout=1)
    finally:
        await watcher.stop()

    assert attempts == 2
    assert runtime_env.os.environ[key] == "after-success"


@pytest.mark.asyncio
async def test_app_env_file_watcher_stop_waits_for_inflight_reload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    key = "APP_ENV_WATCHED_STOP_WAIT"
    _reset_runtime_env_sync(monkeypatch, (key,))
    env_file = tmp_path / ".env"
    env_file.write_text(f"{key}=before\n", encoding="utf-8")
    entered = threading.Event()
    release = threading.Event()

    def on_changed(_changed_keys: frozenset[str]) -> None:
        return None

    watcher = AppEnvFileWatcher(
        env_file_path=env_file,
        on_changed=on_changed,
        poll_interval_seconds=0.01,
        debounce_seconds=0.0,
    )
    original_sync_stable_change = watcher._sync_stable_change

    def sync_stable_change(stable_stamp: AppEnvFileStamp | None) -> frozenset[str]:
        entered.set()
        release.wait(timeout=2.0)
        return original_sync_stable_change(stable_stamp)

    monkeypatch.setattr(watcher, "_sync_stable_change", sync_stable_change)

    watcher.start()
    try:
        env_file.write_text(f"{key}=after\n", encoding="utf-8")
        assert await asyncio.to_thread(entered.wait, 1.0) is True
        stop_task = asyncio.create_task(watcher.stop())
        await asyncio.sleep(0.05)

        assert not stop_task.done()

        release.set()
        await asyncio.wait_for(stop_task, timeout=1)
    finally:
        release.set()
        await watcher.stop()
