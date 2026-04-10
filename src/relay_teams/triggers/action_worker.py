# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from threading import Event, Thread

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
        self._stop_event = Event()
        self._wake_event = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._wake_event.clear()
        self._thread = Thread(
            target=self._run_loop,
            name="github-trigger-action-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread is None:
            return
        thread.join(timeout=10.0)
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
