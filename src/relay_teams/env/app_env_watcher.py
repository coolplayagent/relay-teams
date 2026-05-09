# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.env.runtime_env import (
    load_env_file,
    load_secret_env_vars,
    sync_app_env_to_process_env,
)
from relay_teams.logger import get_logger, log_event

LOGGER = get_logger(__name__)
_DEFAULT_POLL_INTERVAL_SECONDS = 2.0
_DEFAULT_DEBOUNCE_SECONDS = 0.5


class AppEnvFileStamp(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mtime_ns: int = Field(ge=0)
    size: int = Field(ge=0)


class AppEnvFileWatcher:
    def __init__(
        self,
        *,
        env_file_path: Path,
        on_changed: Callable[[frozenset[str]], None],
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
        debounce_seconds: float = _DEFAULT_DEBOUNCE_SECONDS,
    ) -> None:
        self._env_file_path = env_file_path
        self._on_changed = on_changed
        self._poll_interval_seconds = max(0.1, poll_interval_seconds)
        self._debounce_seconds = max(0.0, debounce_seconds)
        self._last_stamp: AppEnvFileStamp | None = None
        self._last_env: dict[str, str] = {}
        self._task: asyncio.Task[None] | None = None
        self._reload_task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self.refresh_snapshot()
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._watch_loop())

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        reload_task = self._reload_task
        if reload_task is not None and not reload_task.done():
            await asyncio.gather(reload_task, return_exceptions=True)
        self._reload_task = None
        self._task = None

    def refresh_snapshot(self) -> None:
        self._last_stamp = self._read_stamp()
        self._last_env = self._read_app_env()

    def sync_current_env_for_handled_change(self) -> frozenset[str]:
        previous_env = self._last_env
        current_env = sync_app_env_to_process_env(self._env_file_path)
        self._last_env = current_env
        return frozenset(_changed_env_keys(previous_env, current_env))

    async def _watch_loop(self) -> None:
        while True:
            await asyncio.sleep(self._poll_interval_seconds)
            current_stamp = self._read_stamp()
            if current_stamp == self._last_stamp:
                continue
            await asyncio.sleep(self._debounce_seconds)
            stable_stamp = self._read_stamp()
            if stable_stamp == self._last_stamp:
                continue
            reload_task = asyncio.create_task(self._run_reload(stable_stamp))
            self._reload_task = reload_task
            try:
                await asyncio.shield(reload_task)
            finally:
                if self._reload_task is reload_task and reload_task.done():
                    self._reload_task = None

    async def _run_reload(self, stable_stamp: AppEnvFileStamp | None) -> None:
        try:
            changed_keys = await asyncio.to_thread(
                self._sync_stable_change,
                stable_stamp,
            )
            if changed_keys:
                self._on_changed(changed_keys)
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="env.app.file_watch_reload_failed",
                message="Failed to reload app environment after file change",
                payload={"env_file_path": str(self._env_file_path)},
                exc_info=exc,
            )

    def _sync_stable_change(
        self,
        stable_stamp: AppEnvFileStamp | None,
    ) -> frozenset[str]:
        previous_env = self._last_env
        current_env = sync_app_env_to_process_env(self._env_file_path)
        self._last_stamp = stable_stamp
        self._last_env = current_env
        changed_keys = _changed_env_keys(previous_env, current_env)
        return frozenset(changed_keys)

    def _read_stamp(self) -> AppEnvFileStamp | None:
        try:
            stat_result = self._env_file_path.stat()
        except FileNotFoundError:
            return None
        return AppEnvFileStamp(
            mtime_ns=stat_result.st_mtime_ns,
            size=stat_result.st_size,
        )

    def _read_app_env(self) -> dict[str, str]:
        values = load_env_file(self._env_file_path)
        values.update(load_secret_env_vars(self._env_file_path.parent))
        return values


def _changed_env_keys(
    previous_env: dict[str, str],
    current_env: dict[str, str],
) -> set[str]:
    changed_keys: set[str] = set()
    for key in previous_env.keys() | current_env.keys():
        if previous_env.get(key) != current_env.get(key):
            changed_keys.add(key)
    return changed_keys
