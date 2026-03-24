# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
import logging

from agent_teams.automation.automation_service import AutomationService
from agent_teams.logger import get_logger, log_event

logger = get_logger(__name__)


class AutomationSchedulerService:
    def __init__(
        self,
        *,
        automation_service: AutomationService,
        poll_interval_seconds: float = 30.0,
    ) -> None:
        self._automation_service = automation_service
        self._poll_interval_seconds = poll_interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop_event.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._task = None

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            processed = self._automation_service.process_due_projects(
                now=datetime.now(tz=UTC)
            )
            if processed:
                log_event(
                    logger,
                    logging.INFO,
                    event="automation.scheduler.tick",
                    message="Processed scheduled automation projects",
                    payload={"count": len(processed)},
                )
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                continue


__all__ = ["AutomationSchedulerService"]
