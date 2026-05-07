# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections.abc import Callable
from json import loads
import logging
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.logger import get_logger, log_event

LOGGER = get_logger(__name__)
_DEFAULT_POLL_INTERVAL_SECONDS = 2.0
_DEFAULT_DEBOUNCE_SECONDS = 0.5


class McpConfigFileStamp(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mtime_ns: int = Field(ge=0)
    size: int = Field(ge=0)


class McpConfigFileWatcher:
    def __init__(
        self,
        *,
        config_path: Path,
        on_changed: Callable[[], None],
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
        debounce_seconds: float = _DEFAULT_DEBOUNCE_SECONDS,
    ) -> None:
        self._config_path = config_path
        self._on_changed = on_changed
        self._poll_interval_seconds = max(0.1, poll_interval_seconds)
        self._debounce_seconds = max(0.0, debounce_seconds)
        self._last_stamp: McpConfigFileStamp | None = None
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._last_stamp = self._read_stamp()
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._watch_loop())

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._task = None

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
            self._last_stamp = stable_stamp
            if not self._file_is_valid_json():
                continue
            try:
                await asyncio.to_thread(self._on_changed)
            except Exception as exc:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="mcp.config.file_watch_reload_failed",
                    message="Failed to reload MCP config after file change",
                    payload={"config_path": str(self._config_path)},
                    exc_info=exc,
                )

    def _read_stamp(self) -> McpConfigFileStamp | None:
        try:
            stat_result = self._config_path.stat()
        except FileNotFoundError:
            return None
        return McpConfigFileStamp(
            mtime_ns=stat_result.st_mtime_ns,
            size=stat_result.st_size,
        )

    def _file_is_valid_json(self) -> bool:
        if not self._config_path.exists():
            return True
        try:
            cast(object, loads(self._config_path.read_text(encoding="utf-8-sig")))
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="mcp.config.file_watch_invalid_json",
                message="Ignoring MCP config file change because JSON is invalid",
                payload={"config_path": str(self._config_path)},
                exc_info=exc,
            )
            return False
        return True
