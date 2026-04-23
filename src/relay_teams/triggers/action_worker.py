# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
import threading

from relay_teams.logger import get_logger, log_event
from relay_teams.triggers.service import GitHubTriggerService

LOGGER = get_logger(__name__)


class GitHubTriggerActionWorker:
    def __init__(
        self,
        *,
        trigger_service: GitHubTriggerService,
        poll_interval_seconds: float = 1.0,
        stop_timeout_seconds: float = 10.0,
    ) -> None:
        self._trigger_service = trigger_service
        self._poll_interval_seconds = poll_interval_seconds
        self._stop_timeout_seconds = stop_timeout_seconds
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None

    async def start(self) -> None:
        thread = self._thread
        if thread is not None and thread.is_alive():
            return
        self._stop_event.clear()
        self._wake_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="github-trigger-action-worker",
            daemon=True,
        )
        self._thread.start()

    async def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread is None:
            return
        try:
            await asyncio.to_thread(thread.join, self._stop_timeout_seconds)
        except asyncio.CancelledError:
            if not thread.is_alive():
                self._thread = None
            return
        if thread.is_alive():
            log_event(
                LOGGER,
                logging.WARNING,
                event="github.trigger.action_worker_stop_timeout",
                message="Timed out waiting for GitHub trigger action worker to stop",
                payload={"timeout_seconds": self._stop_timeout_seconds},
            )
            return
        self._thread = None

    def wake(self) -> None:
        self._wake_event.set()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                progress = self._trigger_service.process_pending_actions()
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
            self._wake_event.wait(timeout=self._poll_interval_seconds)
            self._wake_event.clear()


__all__ = ["GitHubTriggerActionWorker"]
