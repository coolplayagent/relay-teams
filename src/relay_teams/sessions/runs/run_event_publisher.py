# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable

from relay_teams.logger import get_logger, log_event
from relay_teams.notifications import (
    NotificationContext,
    NotificationService,
    NotificationType,
)
from relay_teams.sessions.runs.event_stream import RunEventHub, publish_run_event_async
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimeRecord,
    RunRuntimeRepository,
)
from relay_teams.trace import bind_trace_context

logger = get_logger(__name__)


class RunEventPublisher:
    def __init__(
        self,
        *,
        run_event_hub: RunEventHub,
        get_runtime: Callable[[str], RunRuntimeRecord | None],
        get_run_runtime_repo: Callable[[], RunRuntimeRepository | None],
        get_notification_service: Callable[[], NotificationService | None],
    ) -> None:
        self._run_event_hub = run_event_hub
        self._get_runtime = get_runtime
        self._get_run_runtime_repo = get_run_runtime_repo
        self._get_notification_service = get_notification_service

    def emit_notification(
        self,
        *,
        notification_type: NotificationType,
        session_id: str,
        run_id: str,
        trace_id: str,
        title: str,
        body: str,
        session_mode: str = "normal",
        run_kind: str = "conversation",
    ) -> None:
        notification_service = self._get_notification_service()
        if notification_service is None:
            return
        try:
            _ = notification_service.emit(
                notification_type=notification_type,
                title=title,
                body=body,
                context=NotificationContext(
                    session_id=session_id,
                    run_id=run_id,
                    trace_id=trace_id,
                    session_mode=session_mode,
                    run_kind=run_kind,
                ),
            )
        except Exception as exc:
            with bind_trace_context(
                trace_id=trace_id,
                run_id=run_id,
                session_id=session_id,
            ):
                log_event(
                    logger,
                    logging.ERROR,
                    event="run.notification.failed",
                    message="Run notification failed",
                    payload={"notification_type": notification_type.value},
                    exc_info=exc,
                )

    async def emit_notification_async(
        self,
        *,
        notification_type: NotificationType,
        session_id: str,
        run_id: str,
        trace_id: str,
        title: str,
        body: str,
        session_mode: str = "normal",
        run_kind: str = "conversation",
    ) -> None:
        notification_service = self._get_notification_service()
        if notification_service is None:
            return
        try:
            _ = await notification_service.emit_async(
                notification_type=notification_type,
                title=title,
                body=body,
                context=NotificationContext(
                    session_id=session_id,
                    run_id=run_id,
                    trace_id=trace_id,
                    session_mode=session_mode,
                    run_kind=run_kind,
                ),
            )
        except Exception as exc:
            with bind_trace_context(
                trace_id=trace_id,
                run_id=run_id,
                session_id=session_id,
            ):
                log_event(
                    logger,
                    logging.ERROR,
                    event="run.notification.failed",
                    message="Run notification failed",
                    payload={"notification_type": notification_type.value},
                    exc_info=exc,
                )

    def safe_runtime_update(self, run_id: str, **changes: object) -> None:
        run_runtime_repo = self._get_run_runtime_repo()
        if run_runtime_repo is None:
            return
        try:
            run_runtime_repo.update(run_id, **changes)
        except Exception as exc:
            try:
                runtime = self._get_runtime(run_id)
            except (KeyError, sqlite3.Error):
                runtime = None
            session_id = runtime.session_id if runtime is not None else ""
            with bind_trace_context(
                trace_id=run_id,
                run_id=run_id,
                session_id=session_id,
            ):
                log_event(
                    logger,
                    logging.ERROR,
                    event="run.runtime.update_failed",
                    message="Run runtime update failed",
                    payload={
                        "change_count": len(changes),
                        "change_keys": ",".join(sorted(changes.keys())),
                    },
                    exc_info=exc,
                )

    async def safe_runtime_update_async(self, run_id: str, **changes: object) -> None:
        run_runtime_repo = self._get_run_runtime_repo()
        if run_runtime_repo is None:
            return
        try:
            await run_runtime_repo.update_async(run_id, **changes)
        except Exception as exc:
            with bind_trace_context(
                trace_id=run_id,
                run_id=run_id,
                session_id="",
            ):
                log_event(
                    logger,
                    logging.ERROR,
                    event="run.runtime.update_failed",
                    message="Run runtime update failed",
                    payload={
                        "change_count": len(changes),
                        "change_keys": ",".join(sorted(changes.keys())),
                    },
                    exc_info=exc,
                )

    def safe_publish_run_event(
        self,
        event: RunEvent,
        *,
        failure_event: str,
    ) -> None:
        try:
            self._run_event_hub.publish(event)
        except Exception as exc:
            with bind_trace_context(
                trace_id=event.trace_id,
                run_id=event.run_id,
                session_id=event.session_id,
            ):
                log_event(
                    logger,
                    logging.ERROR,
                    event=failure_event,
                    message="Run event publish failed",
                    payload={"event_type": event.event_type.value},
                    exc_info=exc,
                )

    async def safe_publish_run_event_async(
        self,
        event: RunEvent,
        *,
        failure_event: str,
    ) -> None:
        try:
            await publish_run_event_async(self._run_event_hub, event)
        except Exception as exc:
            with bind_trace_context(
                trace_id=event.trace_id,
                run_id=event.run_id,
                session_id=event.session_id,
            ):
                log_event(
                    logger,
                    logging.ERROR,
                    event=failure_event,
                    message="Run event publish failed",
                    payload={"event_type": event.event_type.value},
                    exc_info=exc,
                )
