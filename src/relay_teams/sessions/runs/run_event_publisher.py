# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
import os
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
    RunRuntimeStatus,
)
from relay_teams.trace import bind_trace_context

logger = get_logger(__name__)

_RUN_RUNTIME_UPDATE_TIMEOUT_SECONDS = max(
    0.1,
    float(os.getenv("RELAY_TEAMS_RUN_RUNTIME_UPDATE_TIMEOUT_SECONDS", "2.0")),
)
_RUN_NOTIFICATION_TIMEOUT_SECONDS = max(
    0.1,
    float(os.getenv("RELAY_TEAMS_RUN_NOTIFICATION_TIMEOUT_SECONDS", "2.0")),
)
_RUN_RUNTIME_UPDATE_RETRY_ATTEMPTS = max(
    0,
    int(os.getenv("RELAY_TEAMS_RUN_RUNTIME_UPDATE_RETRY_ATTEMPTS", "3")),
)
_RUN_RUNTIME_UPDATE_RETRY_DELAY_SECONDS = max(
    0.0,
    float(os.getenv("RELAY_TEAMS_RUN_RUNTIME_UPDATE_RETRY_DELAY_SECONDS", "0.2")),
)
_TERMINAL_RUNTIME_STATUSES = frozenset(
    {
        RunRuntimeStatus.STOPPED,
        RunRuntimeStatus.COMPLETED,
        RunRuntimeStatus.FAILED,
    }
)


class RunEventPublisher:  # pragma: no cover
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
            _ = await asyncio.wait_for(
                notification_service.emit_async(
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
                ),
                timeout=_RUN_NOTIFICATION_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            with bind_trace_context(
                trace_id=trace_id,
                run_id=run_id,
                session_id=session_id,
            ):
                log_event(
                    logger,
                    logging.WARNING,
                    event="run.notification.timeout",
                    message="Run notification timed out",
                    payload={
                        "notification_type": notification_type.value,
                        "timeout_seconds": _RUN_NOTIFICATION_TIMEOUT_SECONDS,
                    },
                    exc_info=exc,
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
            await asyncio.wait_for(
                run_runtime_repo.update_async(run_id, **changes),
                timeout=_RUN_RUNTIME_UPDATE_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            session_id = self._runtime_session_id(run_id)
            with bind_trace_context(
                trace_id=run_id,
                run_id=run_id,
                session_id=session_id,
            ):
                log_event(
                    logger,
                    logging.WARNING,
                    event="run.runtime.update_timeout",
                    message="Run runtime update timed out",
                    payload={
                        "change_count": len(changes),
                        "change_keys": ",".join(sorted(changes.keys())),
                        "timeout_seconds": _RUN_RUNTIME_UPDATE_TIMEOUT_SECONDS,
                    },
                    exc_info=exc,
                )
            self._schedule_runtime_update_retry(run_id, changes)
        except Exception as exc:
            session_id = self._runtime_session_id(run_id)
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

    def _runtime_session_id(self, run_id: str) -> str:
        try:
            runtime = self._get_runtime(run_id)
        except (KeyError, sqlite3.Error):
            return ""
        return runtime.session_id if runtime is not None else ""

    def _schedule_runtime_update_retry(
        self,
        run_id: str,
        changes: dict[str, object],
    ) -> None:
        if _RUN_RUNTIME_UPDATE_RETRY_ATTEMPTS <= 0:
            return
        try:
            task = asyncio.create_task(
                self._retry_runtime_update_async(run_id, dict(changes))
            )
        except RuntimeError:
            return
        task.add_done_callback(self._observe_runtime_update_retry_result)

    @staticmethod
    def _observe_runtime_update_retry_result(
        task: asyncio.Task[None],
    ) -> None:
        try:
            task.result()
        except Exception as exc:
            log_event(
                logger,
                logging.ERROR,
                event="run.runtime.update_retry_failed",
                message="Run runtime update retry task failed",
                exc_info=exc,
            )

    async def _retry_runtime_update_async(
        self,
        run_id: str,
        changes: dict[str, object],
    ) -> None:
        session_id = self._runtime_session_id(run_id)
        for attempt in range(1, _RUN_RUNTIME_UPDATE_RETRY_ATTEMPTS + 1):
            if _RUN_RUNTIME_UPDATE_RETRY_DELAY_SECONDS > 0:
                await asyncio.sleep(_RUN_RUNTIME_UPDATE_RETRY_DELAY_SECONDS)
            run_runtime_repo = self._get_run_runtime_repo()
            if run_runtime_repo is None:
                return
            if self._runtime_update_retry_is_stale(run_id, changes):
                return
            try:
                await asyncio.wait_for(
                    run_runtime_repo.update_async(run_id, **changes),
                    timeout=_RUN_RUNTIME_UPDATE_TIMEOUT_SECONDS,
                )
                return
            except TimeoutError as exc:
                with bind_trace_context(
                    trace_id=run_id,
                    run_id=run_id,
                    session_id=session_id,
                ):
                    log_event(
                        logger,
                        logging.WARNING,
                        event="run.runtime.update_retry_timeout",
                        message="Run runtime update retry timed out",
                        payload={
                            "attempt": attempt,
                            "change_count": len(changes),
                            "change_keys": ",".join(sorted(changes.keys())),
                            "timeout_seconds": _RUN_RUNTIME_UPDATE_TIMEOUT_SECONDS,
                        },
                        exc_info=exc,
                    )
            except Exception as exc:
                with bind_trace_context(
                    trace_id=run_id,
                    run_id=run_id,
                    session_id=session_id,
                ):
                    log_event(
                        logger,
                        logging.ERROR,
                        event="run.runtime.update_retry_failed",
                        message="Run runtime update retry failed",
                        payload={
                            "attempt": attempt,
                            "change_count": len(changes),
                            "change_keys": ",".join(sorted(changes.keys())),
                        },
                        exc_info=exc,
                    )
                return

    def _runtime_update_retry_is_stale(
        self,
        run_id: str,
        changes: dict[str, object],
    ) -> bool:
        try:
            runtime = self._get_runtime(run_id)
        except (KeyError, sqlite3.Error):
            return False
        if runtime is None or runtime.status not in _TERMINAL_RUNTIME_STATUSES:
            return False
        requested_status = _coerce_runtime_status(changes.get("status"))
        return requested_status != runtime.status

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


def _coerce_runtime_status(value: object) -> RunRuntimeStatus | None:
    if isinstance(value, RunRuntimeStatus):
        return value
    if isinstance(value, str):
        try:
            return RunRuntimeStatus(value)
        except ValueError:
            return None
    return None
