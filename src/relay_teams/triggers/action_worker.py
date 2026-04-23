# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from relay_teams.logger import get_logger, log_event
from relay_teams.triggers.service import GitHubTriggerService

LOGGER = get_logger(__name__)


class GitHubTriggerActionWorker:
    def __init__(
        self,
        *,
        trigger_service: GitHubTriggerService,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self._trigger_service = trigger_service
        self._poll_interval_seconds = poll_interval_seconds
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._wake_event.clear()
        self._task = asyncio.create_task(
            self._run_loop(),
            name="github-trigger-action-worker",
        )

    async def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        task = self._task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._task = None

    def wake(self) -> None:
        self._wake_event.set()

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                progress = await asyncio.to_thread(
                    self._trigger_service.process_pending_actions
                )
                if progress:
                    continue
            except Exception as exc:
                log_event(
                    LOGGER,
                    logging.ERROR,
                    event="github.trigger.action_worker_failed",
                    message="GitHub trigger action worker failed",
                    payload={"error": str(exc)},
                    exc_info=exc,
                )
            try:
                await asyncio.wait_for(
                    self._wake_event.wait(),
                    timeout=self._poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass
            self._wake_event.clear()


__all__ = ["GitHubTriggerActionWorker"]
